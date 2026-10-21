# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The customer agent's storage seams and the agent's execution context, exercised against a fake tenant session."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.database as database
from core.config import external_keys, settings
from core.conversation import runtime
from core.customer_agent import actions, autonomy, permissions, sessions, turns

TENANT = uuid.uuid4()


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return list(self.rows)

    def scalar(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    """Answers each ``execute`` with the next configured row set and records what was added."""

    def __init__(self, results):
        self.results = [list(r) for r in results]
        self.added: list = []
        self.executed = 0

    async def execute(self, query):
        self.executed += 1
        return FakeResult(self.results.pop(0) if self.results else [])

    def add(self, row):
        self.added.append(row)


def fake_db(monkeypatch, *results) -> FakeSession:
    session = FakeSession(results)
    calls: list[tuple] = []

    @asynccontextmanager
    async def get_tenant_session(tenant_id, company_id=None):
        calls.append((tenant_id, company_id))
        yield session

    monkeypatch.setattr(database, "get_tenant_session", get_tenant_session)
    session.calls = calls  # type: ignore[attr-defined]
    return session


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "customer_agent_enabled", True)
    monkeypatch.setattr(settings, "customer_agent_tenant_id", str(TENANT))
    monkeypatch.setattr(settings, "customer_agent_company_id", "")
    monkeypatch.setattr(settings, "customer_agent_agent_id", "")
    monkeypatch.setattr(settings, "customer_agent_max_autonomy", 4)
    monkeypatch.setattr(settings, "customer_agent_outside_agents", False)


def row(**overrides):
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "customer_ref": "cust_1",
        "principal_id": "customer:cust_1",
        "channel": "web",
        "token_hash": "h",
        "assertion_jti": "j",
        "issued_at": now,
        "expires_at": now + timedelta(minutes=30),
        "last_seen_at": None,
        "disclosure_acknowledged_at": None,
        "revoked_at": None,
        "created_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def action(**overrides):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "customer_ref": "cust_1",
        "session_id": None,
        "task_class": "fund_transfer",
        "intent": "fund_transfer",
        "parameters": {"amount": 10},
        "amount": 10.0,
        "status": "prepared",
        "level_applied": 2,
        "requires": "confirm",
        "approval": None,
        "explanation": {},
        "execution": None,
        "channel": "web",
        "created_at": datetime.now(UTC),
        "decided_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def mandate(**overrides):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "customer_ref": "cust_1",
        "task_class": "fund_transfer",
        "autonomy_level": 3,
        "per_action_cap": None,
        "period_cap": None,
        "period": "month",
        "currency": "INR",
        "counterparties": [],
        "window": {},
        "status": "active",
        "expires_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": None,
        "revoked_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── sessions ──────────────────────────────────────────────────────────────────


class TestSessionStorage:
    def test_configuration_readers(self, monkeypatch):
        monkeypatch.setattr(settings, "customer_agent_tenant_id", "not-a-uuid")
        assert sessions.tenant_id() is None
        monkeypatch.setattr(settings, "customer_agent_tenant_id", "")
        assert sessions.tenant_id() is None
        monkeypatch.setattr(settings, "customer_agent_company_id", " c-1 ")
        monkeypatch.setattr(settings, "customer_agent_agent_id", " a-1 ")
        assert sessions.company_id() == "c-1" and sessions.agent_id() == "a-1"
        monkeypatch.setattr(settings, "customer_agent_disclosure", " Custom text ")
        assert sessions.disclosure_text() == "Custom text"

    def test_open_session_refuses_when_off_or_without_a_tenant(self, monkeypatch):
        monkeypatch.setattr(settings, "customer_agent_enabled", False)
        with pytest.raises(sessions.CustomerAgentDisabledError):
            asyncio.run(sessions.open_session("a.b.c"))
        monkeypatch.setattr(settings, "customer_agent_enabled", True)
        monkeypatch.setattr(settings, "customer_agent_tenant_id", "")
        with pytest.raises(sessions.CustomerAssertionError, match="no tenant"):
            asyncio.run(sessions.open_session("a.b.c"))

    def test_open_session_stores_the_hash_and_the_company(self, monkeypatch):
        company = uuid.uuid4()
        monkeypatch.setattr(settings, "customer_agent_company_id", str(company))
        monkeypatch.setattr(
            sessions,
            "verify_assertion",
            lambda assertion, channel="web": sessions.CustomerIdentity("cust_1", "app", {}, "jti-9"),
        )
        db = fake_db(monkeypatch)
        stored, token = asyncio.run(sessions.open_session("a.b.c", channel="app"))
        assert db.added == [stored] and stored.company_id == company and stored.channel == "app"
        assert stored.token_hash == sessions.token_hash(token) and token not in stored.token_hash
        assert stored.assertion_jti == "jti-9" and stored.expires_at > stored.issued_at

    def test_authenticate(self, monkeypatch):
        assert asyncio.run(sessions.authenticate("nope")) is None
        token = sessions.new_token()
        monkeypatch.setattr(settings, "customer_agent_tenant_id", "")
        assert asyncio.run(sessions.authenticate(token)) is None
        monkeypatch.setattr(settings, "customer_agent_tenant_id", str(TENANT))
        fake_db(monkeypatch, [])
        assert asyncio.run(sessions.authenticate(token)) is None
        fake_db(monkeypatch, [row(revoked_at=datetime.now(UTC))])
        assert asyncio.run(sessions.authenticate(token)) is None
        fake_db(monkeypatch, [row(expires_at=datetime.now(UTC) - timedelta(seconds=1))])
        assert asyncio.run(sessions.authenticate(token)) is None
        live = row()
        fake_db(monkeypatch, [live])
        assert asyncio.run(sessions.authenticate(token)) is live and live.last_seen_at is not None
        monkeypatch.setattr(settings, "customer_agent_enabled", False)
        assert asyncio.run(sessions.authenticate(token)) is None

    def test_acknowledge_and_revoke(self, monkeypatch):
        current = row()
        fake_db(monkeypatch, [])
        assert asyncio.run(sessions.acknowledge_disclosure(current)) is current
        stored = row(id=current.id)
        fake_db(monkeypatch, [stored])
        asyncio.run(sessions.acknowledge_disclosure(current))
        first = stored.disclosure_acknowledged_at
        assert first is not None and current.disclosure_acknowledged_at == first
        fake_db(monkeypatch, [stored])
        asyncio.run(sessions.acknowledge_disclosure(current))
        assert stored.disclosure_acknowledged_at == first
        fake_db(monkeypatch, [stored])
        asyncio.run(sessions.revoke_session(current))
        assert stored.revoked_at is not None
        when = stored.revoked_at
        fake_db(monkeypatch, [stored])
        asyncio.run(sessions.revoke_session(current))
        assert stored.revoked_at == when
        fake_db(monkeypatch, [])
        asyncio.run(sessions.revoke_session(current))


# ── actions ───────────────────────────────────────────────────────────────────


class TestActionStorage:
    def test_list_get_and_view(self, monkeypatch):
        done = action(
            status="executed", execution={"status": "executed"}, decided_at=datetime.now(UTC), approval={"m": 1}
        )
        fake_db(monkeypatch, [done, action()])
        rows = asyncio.run(actions.list_actions(TENANT, "cust_1", limit=500, status="executed"))
        assert len(rows) == 2
        view = actions.action_view(done)
        assert view["approved"] is True and view["execution"] == {"status": "executed"} and view["decided_at"]
        assert actions.action_view(action())["execution"] is None
        fake_db(monkeypatch, [done])
        assert asyncio.run(actions.get_action(TENANT, "cust_1", done.id)) is done
        fake_db(monkeypatch, [])
        assert asyncio.run(actions.get_action(TENANT, "cust_1", uuid.uuid4())) is None


# ── permissions ───────────────────────────────────────────────────────────────


class TestPermissionStorage:
    def test_list_set_and_revoke(self, monkeypatch):
        fake_db(monkeypatch, [mandate()])
        assert len(asyncio.run(permissions.list_mandates(TENANT, "cust_1"))) == 1
        old = mandate()
        db = fake_db(monkeypatch, [old])
        cleaned = autonomy.validate_mandate(
            {"task_class": "fund_transfer", "autonomy_level": 4, "per_action_cap": 500, "period": "week"}
        )
        created = asyncio.run(permissions.set_mandate(TENANT, "cust_1", cleaned, created_by="customer:cust_1"))
        assert old.status == "revoked" and old.revoked_at is not None
        assert db.added == [created] and created.autonomy_level == 4 and created.period == "week"
        assert permissions.mandate_view(created)["level_name"] == "execute_within_mandate"
        fake_db(monkeypatch, [])
        assert asyncio.run(permissions.revoke_mandate(TENANT, "cust_1", uuid.uuid4())) is None
        active = mandate()
        fake_db(monkeypatch, [active])
        assert asyncio.run(permissions.revoke_mandate(TENANT, "cust_1", active.id)).status == "revoked"
        fake_db(monkeypatch, [active])
        again = asyncio.run(permissions.revoke_mandate(TENANT, "cust_1", active.id))
        assert again.status == "revoked"

    def test_outside_agents_states(self, monkeypatch):
        monkeypatch.setattr(external_keys, "grantex_api_key", "gx_test_key")
        monkeypatch.setattr(settings, "customer_agent_outside_agents", True)
        monkeypatch.setattr(
            permissions,
            "_principal_dashboard",
            lambda principal_id: {
                "status": "available",
                "dashboard_url": "https://authority.example/d",
                "expires_at": "x",
            },
        )
        page = asyncio.run(permissions.outside_agents("customer:cust_1"))
        assert page["status"] == "available" and page["dashboard_url"].startswith("https://")

        def boom(principal_id):
            raise ConnectionError("down")

        monkeypatch.setattr(permissions, "_principal_dashboard", boom)
        assert asyncio.run(permissions.outside_agents("customer:cust_1"))["status"] == "unavailable"
        monkeypatch.setattr(external_keys, "grantex_api_key", "")
        assert asyncio.run(permissions.outside_agents("customer:cust_1"))["status"] == "not_connected"

    def test_principal_dashboard_uses_the_authority_client(self, monkeypatch):
        import grantex

        created = {}

        class FakeClient:
            def __init__(self, **kwargs):
                created.update(kwargs)
                self.principal_sessions = SimpleNamespace(
                    create=lambda params: SimpleNamespace(
                        dashboard_url="https://authority.example/p", expires_at="soon"
                    )
                )

            def close(self):
                raise RuntimeError("close failed")

        monkeypatch.setattr(grantex, "Grantex", FakeClient)
        monkeypatch.setattr(external_keys, "grantex_api_key", "gx_test_key")
        out = permissions._principal_dashboard("customer:cust_1")
        assert out == {"status": "available", "dashboard_url": "https://authority.example/p", "expires_at": "soon"}
        assert created["api_key"] == "gx_test_key"

    def test_permissions_page(self, monkeypatch):
        fake_db(monkeypatch, [mandate()])
        page = asyncio.run(permissions.permissions_page(row()))
        assert page["mandates"][0]["task_class"] == "fund_transfer" and page["levels"]["4"] == "execute_within_mandate"
        assert page["outside_agents"]["status"] == "not_connected"


# ── turns: storage seams and the execution context ────────────────────────────


class TestTurnStorage:
    def test_mandate_spend_insert_latest_settle_and_supersede(self, monkeypatch):
        active = mandate()
        fake_db(monkeypatch, [active])
        assert asyncio.run(turns.load_mandate(TENANT, "cust_1", "fund_transfer")) is active
        fake_db(monkeypatch, [42.5])
        assert asyncio.run(turns.spent_in_period(TENANT, "cust_1", "fund_transfer", datetime.now(UTC))) == 42.5
        fake_db(monkeypatch, [None])
        assert asyncio.run(turns.spent_in_period(TENANT, "cust_1", "fund_transfer", datetime.now(UTC))) == 0.0
        prepared = action()
        db = fake_db(monkeypatch)
        assert asyncio.run(turns.insert_action(prepared)) is prepared and db.added == [prepared]
        fake_db(monkeypatch, [prepared])
        assert asyncio.run(turns.latest_prepared(TENANT, "cust_1", "fund_transfer")) is prepared
        fake_db(monkeypatch, [prepared])
        settled = asyncio.run(
            turns.settle_action(TENANT, prepared.id, status="executed", execution={"status": "executed"})
        )
        assert settled.status == "executed" and settled.decided_at is not None
        fake_db(monkeypatch, [])
        assert asyncio.run(turns.settle_action(TENANT, uuid.uuid4(), status="x")) is None
        older = action()
        fake_db(monkeypatch, [older])
        assert asyncio.run(turns.supersede_prepared(TENANT, "cust_1", "fund_transfer", prepared.id)) == 1
        assert older.status == "superseded" and older.execution["status"] == "superseded"

    def test_summaries_and_statuses(self):
        assert turns._summary(None) is None
        executed = {
            "status": "executed",
            "intent": "fund_transfer",
            "result": {"transaction_id": "T1"},
            "tool_call": {},
        }
        summary = turns._summary(executed)
        assert summary["reference"] == "T1" and "tool_call" not in summary
        assert turns._reference({"result": "not a dict"}) is None
        assert turns._status_of({"status": "executed"}) == "executed"
        assert turns._status_of({"status": "superseded"}) == "superseded"
        assert turns._status_of({"status": "unbound"}) == "failed" and turns._status_of(None) == "failed"
        assert turns._amount_of({"amount": "12"}) == 12.0 and turns._amount_of({"amount": "x"}) is None

    def test_execution_context_without_an_agent_or_with_a_bad_or_inactive_one(self, monkeypatch):
        assert asyncio.run(turns.execution_context(TENANT)) is None
        monkeypatch.setattr(settings, "customer_agent_agent_id", "not-a-uuid")
        assert asyncio.run(turns.execution_context(TENANT)) is None
        aid = uuid.uuid4()
        monkeypatch.setattr(settings, "customer_agent_agent_id", str(aid))
        fake_db(monkeypatch, [])
        assert asyncio.run(turns.execution_context(TENANT)) is None
        fake_db(monkeypatch, [SimpleNamespace(id=aid, status="retired", connector_ids=[], config={})])
        assert asyncio.run(turns.execution_context(TENANT)) is None

    def test_execution_context_resolves_tools_connectors_and_the_grant(self, monkeypatch):
        import api.v1.agents as agents_api
        import auth.run_grants as run_grants

        aid, company = uuid.uuid4(), uuid.uuid4()
        monkeypatch.setattr(settings, "customer_agent_agent_id", str(aid))
        monkeypatch.setattr(settings, "customer_agent_company_id", str(company))
        agent = SimpleNamespace(
            id=aid,
            status="active",
            connector_ids=["conn-1"],
            agent_type="banking",
            domain="retail",
            authorized_tools=["banking.transfer"],
            config={
                "grantex": {"grantex_agent_id": "ag_1"},
                "conversation": {"bindings": {"fund_transfer": "banking.transfer"}},
            },
        )
        db = fake_db(monkeypatch, [agent])
        resolve = AsyncMock(return_value=({"conn-1": {"k": 1}}, ["conn-1"]))
        grant = AsyncMock(return_value="run-grant")
        monkeypatch.setattr(agents_api, "_resolve_connector_configs", resolve)
        monkeypatch.setattr(run_grants, "resolve_run_grant", grant)
        context = asyncio.run(turns.execution_context(TENANT))
        assert isinstance(context, runtime.ExecutionContext)
        assert context.agent_id == str(aid) and context.company_id == str(company)
        assert context.authorized_tools == ["banking.transfer"] and context.connector_names == ["conn-1"]
        assert context.run_grant == "run-grant" and db.calls[0][1] == company
        assert grant.await_args.kwargs["grantex_config"] == {"grantex_agent_id": "ag_1"}
        assert resolve.await_args.kwargs["company_id"] == company

    def test_approval_with_unreachable_keys_fails_closed(self, monkeypatch):
        monkeypatch.setattr(settings, "customer_agent_assertion_issuer", "https://id.example-bank.test")
        monkeypatch.setattr(settings, "customer_agent_assertion_audience", "aud")

        def boom(token):
            raise ConnectionError("jwks down")

        monkeypatch.setattr(sessions, "_signing_key", boom)
        with pytest.raises(turns.ApprovalError, match="signing keys"):
            turns.verify_approval({"method": "otp", "assertion": "a.b.c"}, session_row=row(), action=action())
