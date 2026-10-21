# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The bank-operated customer agent: sessions, the autonomy ladder, explanations, turns and the routes."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1 import customer_agent as api
from core.config import settings
from core.conversation import dialogue as engine
from core.conversation import runtime, supervisor
from core.conversation.dialogue import Dialogue
from core.customer_agent import actions, autonomy, explanations, permissions, sessions, turns

TENANT = uuid.uuid4()
ISSUER = "https://id.example-bank.test"
AUDIENCE = "agenticorg-customer-agent"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()


def sign(claims: dict, *, alg: str = "RS256", key=PRIVATE_KEY) -> str:
    now = int(time.time())
    body = {"iss": ISSUER, "aud": AUDIENCE, "sub": "cust_7f3a", "iat": now, "exp": now + 120, "jti": uuid.uuid4().hex}
    body.update(claims)
    return jwt.encode(body, key, algorithm=alg)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "customer_agent_enabled", True)
    monkeypatch.setattr(settings, "customer_agent_tenant_id", str(TENANT))
    monkeypatch.setattr(settings, "customer_agent_company_id", "")
    monkeypatch.setattr(settings, "customer_agent_agent_id", "")
    monkeypatch.setattr(settings, "customer_agent_assertion_issuer", ISSUER)
    monkeypatch.setattr(settings, "customer_agent_assertion_audience", AUDIENCE)
    monkeypatch.setattr(settings, "customer_agent_assertion_jwks_uri", "https://id.example-bank.test/jwks.json")
    monkeypatch.setattr(settings, "customer_agent_max_autonomy", 4)
    monkeypatch.setattr(sessions, "_signing_key", lambda token: PUBLIC_KEY)
    from core.governance import operator_override

    monkeypatch.setattr(operator_override, "check", AsyncMock(return_value=operator_override.ALLOWED))


def session_row(**overrides):
    now = datetime.now(UTC)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        company_id=None,
        customer_ref="cust_7f3a",
        principal_id="customer:cust_7f3a",
        channel="web",
        token_hash="x",
        assertion_jti="jti-1",
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
        last_seen_at=None,
        disclosure_acknowledged_at=now,
        revoked_at=None,
        created_at=now,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def mandate(**overrides):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "customer_ref": "cust_7f3a",
        "task_class": "fund_transfer",
        "autonomy_level": 4,
        "per_action_cap": 10000.0,
        "period_cap": 50000.0,
        "period": "month",
        "currency": "INR",
        "counterparties": [],
        "categories": [],
        "window": {},
        "status": "active",
        "expires_at": None,
        "reconfirm_at": None,
        "created_by": "customer:cust_7f3a",
        "created_at": datetime.now(UTC),
        "updated_at": None,
        "revoked_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Sessions ──────────────────────────────────────────────────────────────────


class TestAssertions:
    def test_a_bank_signed_assertion_names_the_customer_and_channel(self):
        identity = sessions.verify_assertion(sign({"channel": "app"}))
        assert identity.customer_ref == "cust_7f3a" and identity.channel == "app"

    def test_an_unknown_channel_falls_back_to_web(self):
        assert sessions.verify_assertion(sign({"channel": "pigeon"})).channel == "web"

    @pytest.mark.parametrize(
        "claims",
        [{"iss": "https://other.test"}, {"aud": "someone-else"}, {"sub": ""}, {"exp": int(time.time()) - 600}],
    )
    def test_wrong_issuer_audience_subject_or_expiry_is_refused(self, claims):
        with pytest.raises(sessions.CustomerAssertionError):
            sessions.verify_assertion(sign(claims))

    def test_a_token_signed_with_another_key_is_refused(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(sessions.CustomerAssertionError, match="verification failed"):
            sessions.verify_assertion(sign({}, key=other))

    def test_symmetric_and_unsigned_algorithms_are_refused(self):
        now = int(time.time())
        hs = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "c", "iat": now, "exp": now + 60}, "s", algorithm="HS256"
        )
        with pytest.raises(sessions.CustomerAssertionError, match="unsupported"):
            sessions.verify_assertion(hs)
        none = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "c", "iat": now, "exp": now + 60}, None, algorithm="none"
        )
        with pytest.raises(sessions.CustomerAssertionError, match="unsupported"):
            sessions.verify_assertion(none)

    def test_unconfigured_issuer_refuses_every_assertion(self, monkeypatch):
        monkeypatch.setattr(settings, "customer_agent_assertion_issuer", "")
        with pytest.raises(sessions.CustomerAssertionError, match="not configured"):
            sessions.verify_assertion(sign({}))

    def test_an_unreachable_jwks_fails_closed(self, monkeypatch):
        def boom(token):
            raise ConnectionError("jwks down")

        monkeypatch.setattr(sessions, "_signing_key", boom)
        with pytest.raises(sessions.CustomerAssertionError, match="signing keys"):
            sessions.verify_assertion(sign({}))

    def test_tokens_are_prefixed_random_and_only_hashed(self):
        token = sessions.new_token()
        assert sessions.is_session_token(token) and token != sessions.new_token()
        assert len(sessions.token_hash(token)) == 64 and token not in sessions.token_hash(token)
        assert not sessions.is_session_token("cas_short") and not sessions.is_session_token("ao_sk_" + "x" * 40)

    def test_session_view_carries_the_disclosure_and_never_the_hash(self):
        view = sessions.session_view(session_row(disclosure_acknowledged_at=None), token="cas_abc")
        assert view["disclosure"] and view["disclosure_acknowledged"] is False
        assert view["session_token"] == "cas_abc" and "token_hash" not in view
        assert "session_token" not in sessions.session_view(session_row())


# ── The autonomy ladder ───────────────────────────────────────────────────────


class TestAutonomy:
    def test_task_classes(self):
        assert autonomy.task_class("balance_enquiry") == "read"
        assert autonomy.task_class("fund_transfer") == "fund_transfer"
        assert autonomy.task_class("greeting") == "conversation"
        assert autonomy.task_class(None) == "conversation"

    def test_reads_need_no_mandate(self):
        decision = autonomy.decide("balance_enquiry", {}, None)
        assert decision.allowed and decision.requires == "none" and decision.level == 0

    def test_a_transaction_without_a_mandate_prepares_and_waits_for_one_tap(self):
        decision = autonomy.decide("fund_transfer", {"amount": 5000}, None)
        assert decision.allowed and decision.requires == "confirm" and decision.level == 2

    def test_level_one_refuses_to_prepare(self):
        decision = autonomy.decide("fund_transfer", {"amount": 5000}, mandate(autonomy_level=1))
        assert not decision.allowed and "level 2" in decision.reason

    def test_level_three_needs_a_step_up(self):
        decision = autonomy.decide("fund_transfer", {"amount": 5000}, mandate(autonomy_level=3))
        assert decision.allowed and decision.requires == "step_up"

    def test_level_four_runs_within_the_mandate_and_steps_up_outside_it(self):
        inside = autonomy.decide("fund_transfer", {"amount": 5000, "payee": "Ravi"}, mandate())
        assert inside.requires == "none" and inside.level == 4 and inside.cap_detail == "within_mandate"
        over = autonomy.decide("fund_transfer", {"amount": 50000}, mandate())
        assert over.requires == "step_up" and over.cap_detail == "cap_exceeded:per_action"
        period = autonomy.decide("fund_transfer", {"amount": 5000}, mandate(), spent_in_period=48000)
        assert period.requires == "step_up" and period.cap_detail == "cap_exceeded:period"
        payee = autonomy.decide("fund_transfer", {"amount": 500, "payee": "Stranger"}, mandate(counterparties=["Ravi"]))
        assert payee.requires == "step_up" and payee.cap_detail == "purpose_not_allowed:counterparty"

    def test_expired_or_out_of_window_mandates_fall_back_to_the_default(self):
        past = datetime.now(UTC) - timedelta(days=1)
        assert autonomy.decide("fund_transfer", {"amount": 5}, mandate(expires_at=past)).requires == "confirm"
        night = datetime(2026, 10, 11, 23, 0, tzinfo=UTC)
        windowed = mandate(window={"start_hour": 9, "end_hour": 18})
        assert autonomy.decide("fund_transfer", {"amount": 5}, windowed, now=night).requires == "confirm"
        noon = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        assert autonomy.decide("fund_transfer", {"amount": 5}, windowed, now=noon).requires == "none"

    def test_the_deployment_ceiling_bounds_every_level(self, monkeypatch):
        monkeypatch.setattr(settings, "customer_agent_max_autonomy", 2)
        decision = autonomy.decide("fund_transfer", {"amount": 5}, mandate(autonomy_level=4))
        assert decision.level == 2 and decision.requires == "confirm"
        with pytest.raises(ValueError, match="ceiling"):
            autonomy.validate_mandate({"task_class": "fund_transfer", "autonomy_level": 3})

    def test_validate_mandate(self):
        cleaned = autonomy.validate_mandate(
            {
                "task_class": "bill_payment",
                "autonomy_level": 4,
                "per_action_cap": "2500",
                "period_cap": 10000,
                "period": "week",
                "counterparties": ["BESCOM ", ""],
                "window": {"start_hour": 8, "end_hour": 20, "junk": 1},
            }
        )
        assert cleaned == {
            "task_class": "bill_payment",
            "autonomy_level": 4,
            "per_action_cap": 2500.0,
            "period_cap": 10000.0,
            "period": "week",
            "counterparties": ["BESCOM"],
            "window": {"start_hour": 8, "end_hour": 20},
        }
        for bad in (
            {"task_class": "balance_enquiry"},
            {"task_class": "fund_transfer", "autonomy_level": 7},
            {"task_class": "fund_transfer", "autonomy_level": 4},
            {"task_class": "fund_transfer", "per_action_cap": -1},
            {"task_class": "fund_transfer", "period": "year"},
            {"task_class": "fund_transfer", "expires_at": "tomorrow"},
        ):
            with pytest.raises(ValueError):
                autonomy.validate_mandate(bad)


class TestExplanations:
    def test_why_this_names_data_rule_alternatives_earnings_and_challenge(self):
        decision = autonomy.decide("fund_transfer", {"amount": 5000, "payee": "Ravi"}, mandate())
        panel = explanations.why_this(
            "fund_transfer", {"amount": 5000, "payee": "Ravi", "note": ""}, decision, mandate()
        )
        assert panel["data_used"] == {"amount": 5000, "payee": "Ravi"}
        assert "level 4" in panel["rule"] and panel["mandate"]["per_action_cap"] == 10000.0
        assert panel["alternatives"] and panel["bank_earns"] and "person" in panel["challenge"]
        assert "AI assistant" in panel["ai_disclosure"]

    def test_card_shape(self):
        action = SimpleNamespace(
            id=uuid.uuid4(), intent="fund_transfer", parameters={"amount": 1}, amount=1.0, status="prepared"
        )
        decision = autonomy.decide("fund_transfer", {"amount": 1}, None)
        card = explanations.card_for(action, decision, "Rs 1 to Ravi")
        assert card["type"] == "prepared_action" and card["requires"] == "confirm" and card["title"] == "Fund transfer"


# ── Turns under the ladder ────────────────────────────────────────────────────


class Store:
    """In-memory stand-ins for the dialogue session and the customer-agent tables."""

    def __init__(self, monkeypatch, *, mandate_row=None, execution=None):
        self.dialogue = Dialogue()
        self.actions: dict[uuid.UUID, SimpleNamespace] = {}
        self.mandate = mandate_row
        self.spent = 0.0
        self.execution = execution or {"status": "executed", "result": {"reference": "TXN1"}}
        self.executed: list[str] = []

        async def load_dialogue(tid, key):
            return Dialogue.from_dict(self.dialogue.to_dict())

        async def save_dialogue(tid, key, dialogue, **kw):
            self.dialogue = Dialogue.from_dict(dialogue.to_dict())

        async def claim_dialogue(tid, key, expected, dialogue, **kw):
            self.dialogue = Dialogue.from_dict(dialogue.to_dict())
            return None

        async def execute(outcome, context, claim=None):
            if claim is not None:
                await claim()
            self.executed.append(outcome.intent)
            return dict(self.execution, intent=outcome.intent)

        async def business_rules(tid):
            return SimpleNamespace(retries=2, amount_limits={}, negative_turns=2)

        async def insert_action(row):
            self.actions[row.id] = row
            return row

        async def settle_action(tid, action_id, **fields):
            row = self.actions.get(action_id)
            if row is None:
                return None
            for name, value in fields.items():
                setattr(row, name, value)
            row.decided_at = datetime.now(UTC)
            return row

        async def supersede_prepared(tid, ref, intent, keep_id):
            count = 0
            for r in self.actions.values():
                if r.intent == intent and r.status == "prepared" and r.id != keep_id:
                    r.status = "superseded"
                    count += 1
            return count

        async def latest_prepared(tid, ref, intent):
            rows = [r for r in self.actions.values() if r.intent == intent and r.status == "prepared"]
            return rows[-1] if rows else None

        async def load_mandate(tid, ref, klass):
            return self.mandate if self.mandate is not None and self.mandate.task_class == klass else None

        async def spent_in_period(tid, ref, klass, since):
            return self.spent

        async def execution_context(tid):
            return runtime.ExecutionContext(tenant_id=str(tid), agent_id="agent", authorized_tools=["banking.transfer"])

        monkeypatch.setattr(runtime, "load_dialogue", load_dialogue)
        monkeypatch.setattr(runtime, "save_dialogue", save_dialogue)
        monkeypatch.setattr(runtime, "claim_dialogue", claim_dialogue)
        monkeypatch.setattr(runtime, "execute", execute)
        monkeypatch.setattr(runtime, "business_rules", business_rules)
        monkeypatch.setattr(runtime, "held_turn", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())
        monkeypatch.setattr(turns, "insert_action", insert_action)
        monkeypatch.setattr(turns, "settle_action", settle_action)
        monkeypatch.setattr(turns, "latest_prepared", latest_prepared)
        monkeypatch.setattr(turns, "supersede_prepared", supersede_prepared)
        monkeypatch.setattr(turns, "load_mandate", load_mandate)
        monkeypatch.setattr(turns, "spent_in_period", spent_in_period)
        monkeypatch.setattr(turns, "execution_context", execution_context)

    def only_action(self):
        assert len(self.actions) == 1, self.actions
        return next(iter(self.actions.values()))


@pytest.mark.asyncio
class TestTurns:
    async def test_a_read_runs_at_level_zero_with_an_explanation(self, monkeypatch):
        store = Store(monkeypatch)
        turn = await turns.run_customer_turn(session_row(), "what is my balance?")
        assert store.executed == ["balance_enquiry"] and turn.decision.level == 0
        assert turn.explanation["title"].startswith("Why this") and turn.card is None
        assert turn.to_dict()["autonomy"]["task_class"] == "read" and not store.actions

    async def test_level_two_prepares_a_card_then_one_tap_runs_it(self, monkeypatch):
        store = Store(monkeypatch)
        row = session_row()
        turn = await turns.run_customer_turn(row, "transfer 5000 to Ravi")
        assert turn.card and turn.card["requires"] == "confirm" and turn.card["status"] == "prepared"
        assert turn.decision.level == 2 and not store.executed
        prepared = store.only_action()
        assert prepared.amount == 5000.0 and prepared.explanation["rule"].startswith("Autonomy level 2")
        done = await turns.run_customer_turn(row, "yes")
        assert store.executed == ["fund_transfer"]
        assert prepared.status == "executed" and prepared.execution["reference"] == "TXN1"
        assert done.card["status"] == "executed" and done.payload["outcome"]["kind"] == "execute"

    async def test_no_cancels_the_card(self, monkeypatch):
        store = Store(monkeypatch)
        row = session_row()
        await turns.run_customer_turn(row, "transfer 5000 to Ravi")
        turn = await turns.run_customer_turn(row, "no")
        assert turn.payload["outcome"]["kind"] == "cancelled" and not store.executed
        assert store.only_action().status == "cancelled" and turn.card["status"] == "cancelled"

    async def test_level_one_refuses_with_the_reason_and_records_the_refusal(self, monkeypatch):
        store = Store(monkeypatch, mandate_row=mandate(autonomy_level=1))
        turn = await turns.run_customer_turn(session_row(), "transfer 5000 to Ravi")
        assert turn.refusal and "permissions" in turn.payload["answer"] and not store.executed
        assert store.only_action().status == "refused" and store.dialogue.stage == engine.STAGE_IDLE

    async def test_level_four_within_the_mandate_runs_in_the_same_turn(self, monkeypatch):
        store = Store(monkeypatch, mandate_row=mandate())
        turn = await turns.run_customer_turn(session_row(), "transfer 5000 to Ravi")
        assert store.executed == ["fund_transfer"] and turn.decision.requires == "none"
        action = store.only_action()
        assert action.status == "executed" and action.level_applied == 4 and turn.card["status"] == "executed"

    async def test_level_four_above_the_cap_needs_the_banks_approval_bound_to_the_action(self, monkeypatch):
        store = Store(monkeypatch, mandate_row=mandate())
        row = session_row()
        turn = await turns.run_customer_turn(row, "transfer 50000 to Ravi")
        assert turn.approval_required and turn.card["requires"] == "step_up" and not store.executed
        action = store.only_action()
        # A plain yes does not run it.
        refused = await turns.run_customer_turn(row, "yes")
        assert refused.approval_required and not store.executed and action.status == "prepared"
        # An approval for another action does not run it.
        wrong = {"method": "passkey", "assertion": sign({"action_id": str(uuid.uuid4()), "amount": 50000})}
        still = await turns.run_customer_turn(row, "yes", approval=wrong)
        assert still.approval_required and not store.executed and "different action" in still.payload["answer"]
        # A wrong amount does not run it.
        tampered = {"method": "passkey", "assertion": sign({"action_id": str(action.id), "amount": 5})}
        still = await turns.run_customer_turn(row, "yes", approval=tampered)
        assert still.approval_required and not store.executed
        # The bank's approval of this action does.
        good = {"method": "passkey", "assertion": sign({"action_id": str(action.id), "amount": 50000})}
        done = await turns.run_customer_turn(row, "yes", approval=good)
        assert store.executed == ["fund_transfer"] and not done.approval_required
        assert action.status == "executed" and action.approval["method"] == "passkey"

    async def test_an_approval_with_an_unknown_method_or_no_assertion_is_refused(self):
        row = session_row()
        action = SimpleNamespace(id=uuid.uuid4(), amount=10.0)
        for approval in (None, {}, {"method": "wave", "assertion": "x"}, {"method": "otp"}):
            with pytest.raises(turns.ApprovalError):
                turns.verify_approval(approval, session_row=row, action=action)
        other_customer = {"method": "otp", "assertion": sign({"sub": "someone", "action_id": str(action.id)})}
        with pytest.raises(turns.ApprovalError, match="different customer"):
            turns.verify_approval(other_customer, session_row=row, action=action)

    async def test_without_a_connected_agent_the_action_fails_loud_and_is_recorded_as_failed(self, monkeypatch):
        store = Store(monkeypatch)
        monkeypatch.setattr(turns, "execution_context", AsyncMock(return_value=None))
        row = session_row()
        await turns.run_customer_turn(row, "transfer 5000 to Ravi")
        done = await turns.run_customer_turn(row, "yes")
        assert done.payload["outcome"]["execution"]["status"] == "unbound"
        assert store.only_action().status == "failed" and store.only_action().execution["status"] == "unbound"
        assert "Nothing has been done" in done.payload["answer"]

    async def test_a_held_session_goes_to_the_supervisor(self, monkeypatch):
        Store(monkeypatch)
        monkeypatch.setattr(runtime, "held_turn", AsyncMock(return_value={"answer": "held", "outcome": {}}))
        turn = await turns.run_customer_turn(session_row(), "transfer 5000 to Ravi")
        assert turn.payload["answer"] == "held" and turn.card is None

    async def test_the_session_key_names_the_customer_not_the_channel(self):
        row = session_row()
        assert turns.session_key_for(row) == turns.session_key_for(session_row(id=row.id, channel="app"))
        assert "cust_7f3a" in turns.user_id_for(row)


# ── Routes ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    row = session_row()

    async def authenticate(token):
        return row if token == "cas_" + "k" * 40 else None

    monkeypatch.setattr(sessions, "authenticate", authenticate)
    return TestClient(app), row


AUTH = {"Authorization": "Bearer cas_" + "k" * 40}


class TestRoutes:
    def test_everything_is_404_while_the_agent_is_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "customer_agent_enabled", False)
        http, _ = client
        assert http.post("/api/v1/customer-agent/sessions", json={"assertion": "x" * 30}).status_code == 404
        assert http.get("/api/v1/customer-agent/session", headers=AUTH).status_code == 404

    def test_the_prefix_is_exempt_from_the_operator_middleware(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        assert "/api/v1/customer-agent/" in GrantexAuthMiddleware.EXEMPT_PREFIXES

    def test_a_missing_or_unknown_token_is_401(self, client):
        http, _ = client
        assert http.get("/api/v1/customer-agent/session").status_code == 401
        bad = http.get("/api/v1/customer-agent/session", headers={"Authorization": "Bearer cas_nope"})
        assert bad.status_code == 401 and bad.json()["detail"]["error"] == "customer_session_invalid"

    def test_opening_a_session_returns_the_token_once(self, client, monkeypatch):
        http, row = client
        row.disclosure_acknowledged_at = None
        monkeypatch.setattr(sessions, "open_session", AsyncMock(return_value=(row, "cas_fresh")))
        response = http.post("/api/v1/customer-agent/sessions", json={"assertion": sign({}), "channel": "web"})
        assert response.status_code == 200
        body = response.json()
        assert body["session_token"] == "cas_fresh" and body["disclosure_acknowledged"] is False and body["disclosure"]
        assert http.get("/api/v1/customer-agent/session", headers=AUTH).json().get("session_token") is None

    def test_a_rejected_assertion_is_401(self, client, monkeypatch):
        http, _ = client
        monkeypatch.setattr(sessions, "open_session", AsyncMock(side_effect=sessions.CustomerAssertionError("bad")))
        response = http.post("/api/v1/customer-agent/sessions", json={"assertion": "x" * 30})
        assert response.status_code == 401 and response.json()["detail"]["error"] == "assertion_rejected"

    def test_turns_need_the_disclosure_acknowledged(self, client, monkeypatch):
        http, row = client
        row.disclosure_acknowledged_at = None
        response = http.post("/api/v1/customer-agent/turns", json={"text": "hi"}, headers=AUTH)
        assert response.status_code == 409 and response.json()["detail"]["error"] == "disclosure_required"

        async def acknowledge(r):
            r.disclosure_acknowledged_at = datetime.now(UTC)
            return r

        monkeypatch.setattr(sessions, "acknowledge_disclosure", acknowledge)
        assert http.post("/api/v1/customer-agent/session/acknowledge", headers=AUTH).json()["disclosure_acknowledged"]
        turn = turns.Turn(payload={"answer": "Hello"}, decision=autonomy.decide("greeting", {}, None))
        monkeypatch.setattr(turns, "run_customer_turn", AsyncMock(return_value=turn))
        response = http.post("/api/v1/customer-agent/turns", json={"text": "hi"}, headers=AUTH)
        assert response.status_code == 200 and response.json()["answer"] == "Hello"
        assert response.json()["autonomy"]["task_class"] == "conversation"

    def test_intents_list_task_classes_and_default_levels(self, client):
        http, _ = client
        body = http.get("/api/v1/customer-agent/intents", headers=AUTH).json()
        names = {item["name"]: item for item in body["intents"]}
        assert "greeting" not in names
        assert names["fund_transfer"]["default_level"] == 2 and names["balance_enquiry"]["task_class"] == "read"

    def test_confirm_carries_the_tap_into_the_dialogue_and_refuses_a_stale_card(self, client, monkeypatch):
        http, row = client
        action = SimpleNamespace(
            id=uuid.uuid4(), intent="fund_transfer", status="prepared", parameters={"amount": 5000}
        )
        monkeypatch.setattr(actions, "get_action", AsyncMock(return_value=action))
        confirming = Dialogue(stage=engine.STAGE_CONFIRMING, intent="fund_transfer", slots={"amount": 5000})
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=confirming))
        run = AsyncMock(return_value=turns.Turn(payload={"answer": "Done"}))
        monkeypatch.setattr(turns, "run_customer_turn", run)
        settle = AsyncMock(return_value=action)
        monkeypatch.setattr(turns, "settle_action", settle)
        response = http.post(
            f"/api/v1/customer-agent/actions/{action.id}/confirm",
            json={"approval": {"method": "otp", "assertion": "a.b.c"}},
            headers=AUTH,
        )
        assert response.status_code == 200 and response.json()["answer"] == "Done"
        assert run.await_args.args[1] == "yes" and run.await_args.kwargs["approval"]["method"] == "otp"
        # The amount changed at the confirmation: the old card is superseded and never run.
        edited = Dialogue(stage=engine.STAGE_CONFIRMING, intent="fund_transfer", slots={"amount": 50000})
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=edited))
        response = http.post(f"/api/v1/customer-agent/actions/{action.id}/confirm", headers=AUTH)
        assert response.status_code == 409 and response.json()["detail"]["error"] == "action_stale"
        # The dialogue moved on: the card is superseded and never run.
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        response = http.post(f"/api/v1/customer-agent/actions/{action.id}/confirm", headers=AUTH)
        assert response.status_code == 409 and response.json()["detail"]["error"] == "action_stale"
        assert settle.await_args.kwargs["status"] == "superseded" and run.await_count == 1
        # A settled action is refused.
        action.status = "executed"
        response = http.post(f"/api/v1/customer-agent/actions/{action.id}/confirm", headers=AUTH)
        assert response.status_code == 409 and response.json()["detail"]["error"] == "action_settled"
        assert http.post("/api/v1/customer-agent/actions/not-a-uuid/confirm", headers=AUTH).status_code == 404

    def test_cancel_settles_the_card(self, client, monkeypatch):
        http, row = client
        action = SimpleNamespace(id=uuid.uuid4(), intent="fund_transfer", status="prepared", parameters={})
        monkeypatch.setattr(actions, "get_action", AsyncMock(return_value=action))
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        settle = AsyncMock(return_value=action)
        monkeypatch.setattr(turns, "settle_action", settle)
        response = http.post(f"/api/v1/customer-agent/actions/{action.id}/cancel", headers=AUTH)
        assert response.status_code == 200 and response.json()["card"]["status"] == "cancelled"
        assert settle.await_args.kwargs["status"] == "cancelled"

    def test_permissions_page_and_mandates(self, client, monkeypatch):
        http, row = client
        rows = [mandate()]
        monkeypatch.setattr(permissions, "list_mandates", AsyncMock(return_value=rows))
        monkeypatch.setattr(permissions, "outside_agents", AsyncMock(return_value={"status": "not_connected"}))
        body = http.get("/api/v1/customer-agent/permissions", headers=AUTH).json()
        assert body["mandates"][0]["level_name"] == "execute_within_mandate" and body["autonomy_ceiling"] == 4
        assert body["outside_agents"]["status"] == "not_connected"
        created = mandate(autonomy_level=3, per_action_cap=None, period_cap=None)
        set_mandate = AsyncMock(return_value=created)
        monkeypatch.setattr(permissions, "set_mandate", set_mandate)
        response = http.put(
            "/api/v1/customer-agent/permissions/mandates",
            json={"task_class": "fund_transfer", "autonomy_level": 3},
            headers=AUTH,
        )
        assert response.status_code == 200 and response.json()["autonomy_level"] == 3
        assert set_mandate.await_args.kwargs["created_by"] == "customer:cust_7f3a"
        invalid = http.put(
            "/api/v1/customer-agent/permissions/mandates",
            json={"task_class": "balance_enquiry", "autonomy_level": 3},
            headers=AUTH,
        )
        assert invalid.status_code == 422 and invalid.json()["detail"]["error"] == "mandate_invalid"
        monkeypatch.setattr(permissions, "revoke_mandate", AsyncMock(return_value=None))
        assert (
            http.delete(f"/api/v1/customer-agent/permissions/mandates/{uuid.uuid4()}", headers=AUTH).status_code == 404
        )

    def test_outside_agents_without_the_authority_service_is_reported_not_raised(self):
        import asyncio

        assert asyncio.run(permissions.outside_agents(None))["status"] == "unavailable"
        assert asyncio.run(permissions.outside_agents("customer:x"))["status"] == "not_connected"


class TestMigration:
    def test_migration_v6z84_tables_rls_and_constraints(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z84_customer_agent.py"
        spec = importlib.util.spec_from_file_location("v6_z84_customer_agent", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.revision == "v6z84_customer_agent" and len(module.revision) <= 32
        assert module.down_revision == "v6z83_channel_bindings"
        statements: list[str] = []
        monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(" ".join(str(sql).split())))
        module.upgrade()
        joined = "\n".join(statements)
        for table in ("customer_sessions", "customer_mandates", "customer_actions"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in joined
            assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in joined
            assert f"CREATE POLICY {table}_tenant_isolation" in joined
        assert "ux_customer_sessions_token_hash" in joined and "UNIQUE INDEX" in joined
        assert "assertion_jti VARCHAR(128) NOT NULL" in joined and "ux_customer_sessions_tenant_assertion" in joined
        assert "ck_customer_mandates_level CHECK (autonomy_level >= 0 AND autonomy_level <= 4)" in joined
        assert "ck_customer_actions_status" in joined
        statements.clear()
        module.downgrade()
        assert len(statements) == 3 and all(s.startswith("DROP TABLE IF EXISTS") for s in statements)


class TestReviewFollowUps:
    def test_an_overlong_customer_reference_is_refused_not_truncated(self):
        with pytest.raises(sessions.CustomerAssertionError, match="longer than"):
            sessions.verify_assertion(sign({"sub": "c" * 101}))
        assert sessions.verify_assertion(sign({"sub": "c" * 100})).customer_ref == "c" * 100

    def test_an_assertion_without_a_unique_id_is_refused(self):
        now = int(time.time())
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "c", "iat": now, "exp": now + 60}, PRIVATE_KEY, algorithm="RS256"
        )
        with pytest.raises(sessions.CustomerAssertionError, match="jti"):
            sessions.verify_assertion(token)
        assert sessions.verify_assertion(sign({"jti": "one-time"})).assertion_id == "one-time"

    def test_a_replayed_assertion_opens_no_second_session(self, monkeypatch):
        import asyncio
        from contextlib import asynccontextmanager

        from sqlalchemy.exc import IntegrityError

        import core.database as database

        seen: set[str] = set()

        @asynccontextmanager
        async def fake_session(tid, company_id=None):
            added = []
            yield SimpleNamespace(add=added.append)
            for row in added:
                if row.assertion_jti in seen:
                    raise IntegrityError("INSERT", {}, Exception("duplicate key ux_customer_sessions_tenant_assertion"))
                seen.add(row.assertion_jti)

        monkeypatch.setattr(database, "get_tenant_session", fake_session)
        assertion = sign({"jti": "once"})
        row, token = asyncio.run(sessions.open_session(assertion))
        assert row.assertion_jti == "once" and row.principal_id == "customer:cust_7f3a" and token.startswith("cas_")
        with pytest.raises(sessions.CustomerAssertionError, match="already used"):
            asyncio.run(sessions.open_session(assertion))

    def test_period_spend_counts_executed_actions_by_their_execution_time(self):
        import inspect

        source = inspect.getsource(turns.spent_in_period)
        assert "decided_at >= since" in source and "created_at >= since" not in source

    def test_same_parameters_compares_the_action_not_the_wording(self):
        assert turns.same_parameters({"amount": 5000, "payee": "Ravi", "note": ""}, {"amount": "5000", "payee": "Ravi"})
        assert not turns.same_parameters({"amount": 5000, "payee": "Ravi"}, {"amount": 50000, "payee": "Ravi"})
        assert not turns.same_parameters({"amount": 5000}, {"amount": 5000, "payee": "Someone"})

    @pytest.mark.asyncio
    async def test_editing_the_amount_at_confirmation_supersedes_the_first_card(self, monkeypatch):
        store = Store(monkeypatch)
        row = session_row()
        first = await turns.run_customer_turn(row, "transfer 5000 to Ravi")
        second = await turns.run_customer_turn(row, "make it 7000")
        assert first.card and second.card and first.card["action_id"] != second.card["action_id"]
        statuses = {str(r.id): r.status for r in store.actions.values()}
        assert statuses[first.card["action_id"]] == "superseded" and statuses[second.card["action_id"]] == "prepared"
        done = await turns.run_customer_turn(row, "yes")
        assert store.executed == ["fund_transfer"] and done.card["action_id"] == second.card["action_id"]

    def test_customer_routes_are_rate_limited_per_session_token(self, monkeypatch):
        import asyncio

        import core.auth_state as auth_state
        from api import route_enforcement

        seen: list[tuple[str, str]] = []

        async def check_window_rate(bucket, principal, limit, window):
            seen.append((bucket, principal))
            return False

        monkeypatch.setattr(auth_state, "check_window_rate", check_window_rate)
        monkeypatch.setattr(route_enforcement, "_client_ip", lambda request: "203.0.113.9")

        def request(token: str | None):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return SimpleNamespace(state=SimpleNamespace(), headers=headers, url=SimpleNamespace(path="/x"))

        meta = {"rate_limit": "customer-agent", "auth_required": False}
        asyncio.run(route_enforcement._check_rate_limit(request("cas_" + "a" * 40), meta))
        asyncio.run(route_enforcement._check_rate_limit(request("cas_" + "b" * 40), meta))
        asyncio.run(route_enforcement._check_rate_limit(request(None), meta))
        asyncio.run(
            route_enforcement._check_rate_limit(
                request("cas_" + "a" * 40), {"rate_limit": "customer-agent-public", "auth_required": False}
            )
        )
        principals = [p for _, p in seen]
        assert principals[0].startswith("cas:") and principals[1].startswith("cas:") and principals[0] != principals[1]
        assert principals[2] == "ip:203.0.113.9" and principals[3] == "ip:203.0.113.9"
        assert "a" * 40 not in principals[0]

    def test_the_tables_declare_the_migration_constraints(self):
        from core.models.customer_agent import CustomerAction, CustomerMandate, CustomerSession

        names = {c.name for c in CustomerMandate.__table__.constraints} | {
            c.name for c in CustomerAction.__table__.constraints
        }
        assert {
            "ck_customer_mandates_level",
            "ck_customer_mandates_period",
            "ck_customer_mandates_status",
            "ck_customer_actions_status",
        } <= names
        assert {i.name for i in CustomerSession.__table__.indexes} >= {
            "ux_customer_sessions_token_hash",
            "ux_customer_sessions_tenant_assertion",
        }
