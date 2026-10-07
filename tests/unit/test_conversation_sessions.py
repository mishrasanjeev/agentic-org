# SPDX-License-Identifier: Apache-2.0
"""Conversational services: the session store, the agent context a turn runs with, and the session routes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.conversation import dialogue as engine
from core.conversation import runtime, supervisor
from core.conversation.dialogue import Dialogue, Outcome

TENANT = uuid.uuid4()


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_args, **_kw):
        return _Result(self.row)

    def add(self, row):
        self.added.append(row)


def _row(**overrides):
    base = {
        "session_key": "web:c:a:u:u1",
        "user_id": "u1",
        "agent_id": None,
        "status": "active",
        "intent": "fund_transfer",
        "state": Dialogue(stage=engine.STAGE_CONFIRMING, intent="fund_transfer", slots={"amount": 5.0}).to_dict(),
        "turns": 2,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSessionStore:
    def test_a_session_idle_for_too_long_starts_over(self):
        assert runtime._expired(None) is True
        assert runtime._expired(datetime.now(UTC) - timedelta(seconds=runtime.IDLE_SECONDS + 1)) is True
        assert runtime._expired(datetime.now(UTC) - timedelta(seconds=10)) is False
        assert runtime._expired(datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10)) is False

    @pytest.mark.asyncio
    async def test_load_returns_the_stored_dialogue_or_a_fresh_one(self, monkeypatch):
        import core.database as database

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        assert (await runtime.load_dialogue(TENANT, "k")).stage == engine.STAGE_IDLE
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(_row()))
        restored = await runtime.load_dialogue(TENANT, "k")
        assert restored.stage == engine.STAGE_CONFIRMING and restored.slots == {"amount": 5.0}
        stale = _row(updated_at=datetime.now(UTC) - timedelta(hours=2))
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(stale))
        assert (await runtime.load_dialogue(TENANT, "k")).stage == engine.STAGE_IDLE

    @pytest.mark.asyncio
    async def test_save_adds_a_row_or_updates_it_and_reset_clears_it(self, monkeypatch):
        import core.database as database

        fresh = _Session(None)
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: fresh)
        dialogue = Dialogue(stage=engine.STAGE_COLLECTING, intent="card_block", turns=1)
        await runtime.save_dialogue(TENANT, "k", dialogue, user_id="u" * 200, agent_id="not-a-uuid", channel="web")
        assert len(fresh.added) == 1
        row = fresh.added[0]
        assert (
            row.status == "active" and row.intent == "card_block" and row.agent_id is None and len(row.user_id) == 128
        )

        existing = _row()
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(existing))
        agent = uuid.uuid4()
        await runtime.save_dialogue(TENANT, "k", Dialogue(), user_id="u1", agent_id=str(agent), channel="web")
        assert (
            existing.status == "idle" and existing.intent is None and existing.agent_id == agent and existing.turns == 0
        )

        assert await runtime.reset_dialogue(TENANT, "k") is True and existing.state["stage"] == engine.STAGE_IDLE
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        assert await runtime.reset_dialogue(TENANT, "k") is False

    @pytest.mark.asyncio
    async def test_agent_bindings_come_from_the_agents_config_or_are_empty(self, monkeypatch):
        import core.database as database

        assert await runtime.agent_bindings(str(TENANT), "") == {}
        assert await runtime.agent_bindings(str(TENANT), "nope") == {}
        agent = SimpleNamespace(config={"conversation": {"bindings": {"fund_transfer": "bank:transfer_funds"}}})
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(agent))
        assert await runtime.agent_bindings(str(TENANT), str(uuid.uuid4())) == {"fund_transfer": "bank:transfer_funds"}
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        assert await runtime.agent_bindings(str(TENANT), str(uuid.uuid4())) == {}

        class _Broken:
            async def __aenter__(self):
                raise RuntimeError("down")

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Broken())
        assert await runtime.agent_bindings(str(TENANT), str(uuid.uuid4())) == {}

    def test_answers_for_executed_read_intents_and_bounded_results(self):
        balance = Outcome(kind="execute", text="t", intent="balance_enquiry", summary="Balance enquiry.")
        execution = {"status": "executed", "result": {"balance": 12345.5, "account": "XXXX1234"}}
        assert runtime.answer_for(balance, execution) == "The balance of the account ending 1234 is ₹12,345.50."
        status = Outcome(kind="execute", text="t", intent="application_status", summary="s")
        assert runtime.answer_for(
            status, {"status": "executed", "result": {"status": "approved", "detail": "Funds soon."}}
        ) == ("The application is approved. Funds soon.")
        loan = Outcome(kind="execute", text="t", intent="loan_enquiry", summary="s")
        assert runtime.answer_for(loan, {"status": "executed", "result": {"rate": 8.5}}).startswith(
            "Here is what I found:"
        )
        transfer = Outcome(kind="execute", text="t", intent="fund_transfer", summary="Transfer ₹5 to R.")
        assert (
            runtime.answer_for(transfer, {"status": "executed", "result": {"reference": "TXN-1"}})
            == "Transfer ₹5 to R. Done. Reference TXN-1."
        )
        assert runtime.answer_for(Outcome(kind="ask", text="How much?"), None) == "How much?"
        big = runtime._bounded({"blob": "x" * 5000})
        assert isinstance(big, str) and big.endswith("…")
        assert runtime.dialogue_view(Dialogue(intent="fund_transfer", slots={"amount": 1.0}))["missing"] == ["payee"]


class _LockedStore:
    """One stored session row behind a row lock held for the whole transaction, as ``FOR UPDATE`` holds it."""

    def __init__(self, row=None):
        self.row = row
        self.lock = asyncio.Lock()
        self.added: list = []

    def session(self, *_a, **_k):
        store = self

        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                if store.lock.locked():
                    store.lock.release()
                return False

            async def execute(self, *_args, **_kw):
                await store.lock.acquire()
                await asyncio.sleep(0)
                return _Result(store.row)

            def add(self, row):
                store.added.append(row)
                store.row = row

        return _Tx()


def _wire_transfer_tool(monkeypatch, calls: list[dict]):
    from core.governance import operator_override
    from core.langgraph import tool_adapter

    class _Tool:
        async def ainvoke(self, params):
            await asyncio.sleep(0)
            calls.append(params)
            return {"status": "ok", "reference": "TXN-1"}

    monkeypatch.setattr(tool_adapter, "_build_tool_index", lambda *_a, **_k: {"transfer_funds": ("core_bank", "d")})
    monkeypatch.setattr(tool_adapter, "build_tools_for_agent", lambda *_a, **_k: [_Tool()])
    import auth.run_grants as run_grants

    monkeypatch.setattr(run_grants, "direct_tool_call_permitted", AsyncMock(return_value=True))
    monkeypatch.setattr(operator_override, "check", AsyncMock(return_value=operator_override.ALLOWED))
    return runtime.ExecutionContext(
        tenant_id=str(TENANT), agent_id="a1", authorized_tools=["transfer_funds"], run_grant=object()
    )


def _confirming_row():
    dialogue = Dialogue()
    engine.advance(dialogue, "transfer 500 to Ravi")
    assert dialogue.stage == engine.STAGE_CONFIRMING
    return _row(state=dialogue.to_dict(), turns=dialogue.turns)


class TestConfirmedExecutionIsClaimedOnce:
    @pytest.mark.asyncio
    async def test_the_claim_needs_the_state_the_turn_started_from(self, monkeypatch):
        import core.database as database

        row = _confirming_row()
        store = _LockedStore(row)
        monkeypatch.setattr(database, "get_tenant_session", store.session)
        expected = Dialogue.from_dict(row.state).to_dict()
        advanced = Dialogue.from_dict(row.state)
        engine.advance(advanced, "yes")

        key = await runtime.claim_dialogue(TENANT, "k", expected, advanced, user_id="u1", agent_id=None, channel="web")

        assert key and row.state["execution_key"] == key and row.state["stage"] == engine.STAGE_IDLE
        assert row.status == "idle"
        # The same confirmation again no longer matches what is stored.
        again = Dialogue.from_dict(expected)
        engine.advance(again, "yes")
        assert (
            await runtime.claim_dialogue(TENANT, "k", expected, again, user_id="u1", agent_id=None, channel="web")
            is None
        )
        assert row.state["execution_key"] == key

    @pytest.mark.asyncio
    async def test_a_missing_or_idle_expired_session_claims_as_a_fresh_one(self, monkeypatch):
        import core.database as database

        store = _LockedStore(None)
        monkeypatch.setattr(database, "get_tenant_session", store.session)
        fresh = Dialogue()
        expected = fresh.to_dict()
        engine.advance(fresh, "what is the status of application APP123456")
        assert await runtime.claim_dialogue(TENANT, "k", expected, fresh, user_id="u1", agent_id=None, channel="web")
        assert len(store.added) == 1

        stale = _row(updated_at=datetime.now(UTC) - timedelta(hours=2))
        store = _LockedStore(stale)
        monkeypatch.setattr(database, "get_tenant_session", store.session)
        assert await runtime.claim_dialogue(
            TENANT, "k", Dialogue().to_dict(), Dialogue(), user_id="u1", agent_id=None, channel="web"
        )

    @pytest.mark.asyncio
    async def test_two_overlapping_confirmations_run_the_transfer_once(self, monkeypatch):
        import core.database as database

        calls: list[dict] = []
        context = _wire_transfer_tool(monkeypatch, calls)
        row = _confirming_row()
        store = _LockedStore(row)
        monkeypatch.setattr(database, "get_tenant_session", store.session)
        save = AsyncMock()
        monkeypatch.setattr(runtime, "save_dialogue", save)

        async def confirm():
            return await runtime.run_turn(
                TENANT,
                "k",
                Dialogue.from_dict(row.state),
                "yes",
                context,
                user_id="u1",
                agent_id="a1",
                channel="web",
                no_agent_message="-",
            )

        first, second = await asyncio.gather(confirm(), confirm())

        statuses = sorted(execution["status"] for _, execution in (first, second))
        assert statuses == ["executed", "superseded"] and len(calls) == 1
        assert calls[0]["idempotency_key"] == row.state["execution_key"]
        # A claimed turn has already stored the session; it is not written again.
        assert save.await_count == 0

    @pytest.mark.asyncio
    async def test_a_failure_after_the_claim_leaves_nothing_to_confirm_again(self, monkeypatch):
        import core.database as database

        calls: list[dict] = []
        context = _wire_transfer_tool(monkeypatch, calls)
        row = _confirming_row()
        monkeypatch.setattr(database, "get_tenant_session", _LockedStore(row).session)
        broken_save = AsyncMock(side_effect=RuntimeError("database went away"))
        monkeypatch.setattr(runtime, "save_dialogue", broken_save)
        common = {"user_id": "u1", "agent_id": "a1", "channel": "web", "no_agent_message": "-"}

        outcome, execution = await runtime.run_turn(
            TENANT, "k", Dialogue.from_dict(row.state), "yes", context, **common
        )
        assert outcome.kind == "execute" and execution["status"] == "executed" and broken_save.await_count == 0

        # A retried "yes" reads the stored session, which confirms nothing any more.
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        retry, _ = await runtime.run_turn(TENANT, "k", Dialogue.from_dict(row.state), "yes", context, **common)
        assert retry.kind != "execute" and len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_turn_refused_before_the_claim_is_saved_as_before(self, monkeypatch):
        calls: list[dict] = []
        context = _wire_transfer_tool(monkeypatch, calls)
        context.authorized_tools = []
        claim = AsyncMock()
        monkeypatch.setattr(runtime, "claim_dialogue", claim)
        save = AsyncMock()
        monkeypatch.setattr(runtime, "save_dialogue", save)
        dialogue = Dialogue.from_dict(_confirming_row().state)

        _, execution = await runtime.run_turn(
            TENANT, "k", dialogue, "yes", context, user_id="u1", agent_id="a1", channel="web", no_agent_message="-"
        )

        assert execution["status"] == "unbound" and claim.await_count == 0 and save.await_count == 1


class _HandoffSession(_Session):
    def __init__(self, row=None, *, fail: bool = False):
        super().__init__(row)
        self.fail = fail

    async def flush(self):
        if self.fail:
            raise RuntimeError("write refused")


class TestHandoff:
    """One hand-off path (``escalation.handoff``): the review item with the context, its notification, and honesty."""

    @staticmethod
    def _escalation():
        dialogue = Dialogue()
        engine.advance(dialogue, "send money to Ravi")
        return dialogue, engine.advance(dialogue, "connect me with a human agent")

    @staticmethod
    def _quiet_supervisor(monkeypatch):
        monkeypatch.setattr(supervisor, "mark_escalated", AsyncMock())
        monkeypatch.setattr(supervisor, "announce", AsyncMock())
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())

    @pytest.mark.asyncio
    async def test_asking_for_a_person_queues_a_handoff_with_the_context_and_notifies(self, monkeypatch):
        import core.database as database
        import core.push.sender as sender
        from core.conversation import escalation

        agent = SimpleNamespace(name="Branch assistant", visibility="tenant", owner_user_id=None)
        session = _HandoffSession(agent)
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: session)
        notify = AsyncMock(return_value={})
        monkeypatch.setattr(sender, "notify_approval_created", notify)
        self._quiet_supervisor(monkeypatch)
        user = uuid.uuid4()
        context = runtime.ExecutionContext(tenant_id=str(TENANT), agent_id=str(uuid.uuid4()), domain="ops")
        dialogue, outcome = self._escalation()

        answer = await runtime.finish_turn(
            TENANT,
            "k",
            dialogue,
            outcome,
            None,
            text="connect me with a human agent",
            user_id=str(user),
            agent_id=context.agent_id,
            channel="web",
            context=context,
        )

        assert len(session.added) == 1
        item = session.added[0]
        assert item.trigger_type == escalation.TRIGGER and item.assignee_role == "ops"
        assert item.requested_by_user_id == user and str(item.agent_id) == context.agent_id
        assert item.context["handoff"]["intent"] == "fund_transfer"
        assert item.context["handoff"]["slots"] == {"payee": "Ravi"}
        handoff = answer["outcome"]["handoff"]
        assert notify.call_args.kwargs["item_id"] == str(item.id) == handoff["hitl_id"]
        assert str(item.id)[:8].upper() in answer["answer"] and "queue" in answer["answer"]
        # The response never carries the dialogue's hand-off notes (slots, recent turns).
        assert set(handoff) == {"reason", "intent", "hitl_id", "ticket"} and "execution" not in answer["outcome"]
        assert "handoff" not in runtime.outcome_payload(outcome, None)

    @pytest.mark.asyncio
    async def test_without_an_agent_or_a_written_item_nothing_is_promised(self, monkeypatch):
        import core.database as database
        from core.conversation import escalation

        self._quiet_supervisor(monkeypatch)
        dialogue, outcome = self._escalation()

        async def _handoff(agent_id: str) -> dict:
            return await escalation.handoff(
                TENANT,
                session_key="k",
                dialogue=dialogue,
                user_id="u1",
                agent_id=agent_id,
                channel="web",
                reason=escalation.REASON_REQUESTED,
                notes=outcome.handoff,
            )

        none = await _handoff("")
        assert none["hitl_id"] is None and not escalation.handed_over(none)
        answer = runtime.answer_for(outcome, None, none)
        assert "nothing has been handed over" in answer and "connect you" not in answer
        assert "nothing has been handed over" in runtime.answer_for(outcome, None)

        failing = _HandoffSession(SimpleNamespace(name="x"), fail=True)
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: failing)
        assert not escalation.handed_over(await _handoff(str(uuid.uuid4())))
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _HandoffSession(None))
        assert not escalation.handed_over(await _handoff(str(uuid.uuid4())))

    @pytest.mark.asyncio
    async def test_the_turn_route_raises_the_handoff_for_an_escalation(self, monkeypatch):
        from api.v1 import conversation as api
        from core.conversation import escalation

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())
        raised = AsyncMock(
            return_value={"reason": "requested", "intent": "talk_to_agent", "hitl_id": "ab12cd34-0000", "ticket": None}
        )
        monkeypatch.setattr(escalation, "handoff", raised)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))

        answer = await api.post_turn(api.TurnIn(text="I want to talk to a human"), request, tenant_id=str(TENANT))

        assert raised.await_count == 1 and answer["outcome"]["kind"] == "escalate"
        assert "AB12CD34" in answer["answer"] and answer["outcome"]["handoff"]["hitl_id"] == "ab12cd34-0000"


class TestAgentContext:
    @staticmethod
    def _request(claims=None):
        return SimpleNamespace(
            state=SimpleNamespace(claims=claims or {"agenticorg:user_id": "u1"}, grant_token=None, agent_id="")
        )

    @pytest.mark.asyncio
    async def test_no_agent_means_no_context_and_a_bad_id_is_not_found(self):
        from api.v1 import conversation as api

        assert await api._execution_context(self._request(), str(TENANT), "c1", "") is None
        with pytest.raises(HTTPException) as info:
            await api._execution_context(self._request(), str(TENANT), "c1", "not-a-uuid")
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_context_carries_the_agents_tools_connectors_bindings_and_grant(self, monkeypatch):
        import api.v1.agents as agents_api
        import auth.run_grants as run_grants
        from api.v1 import conversation as api

        company = uuid.uuid4()
        agent = SimpleNamespace(
            id=uuid.uuid4(),
            status="active",
            connector_ids=["conn-1"],
            agent_type="support",
            domain="ops",
            authorized_tools=["bank:transfer_funds"],
            config={"conversation": {"bindings": {"fund_transfer": "bank:transfer_funds"}}},
            owner_user_id=None,
            visibility="tenant",
        )
        monkeypatch.setattr(agents_api, "_require_company_for_tenant", AsyncMock(return_value=company))
        ready = AsyncMock()
        monkeypatch.setattr(agents_api, "_assert_connectors_ready_for_dispatch", ready)
        monkeypatch.setattr(
            agents_api, "_resolve_connector_configs", AsyncMock(return_value=({"bank": {"k": "v"}}, ["bank"]))
        )
        grant = object()
        resolved = AsyncMock(return_value=grant)
        monkeypatch.setattr(run_grants, "resolve_run_grant", resolved)
        monkeypatch.setattr(api, "get_tenant_session", lambda *_a, **_k: _Session(agent))
        monkeypatch.setattr(api, "require_agent_visible", lambda *_a, **_k: None)
        monkeypatch.setattr(api, "agent_ownership_fields", lambda *_a, **_k: {"visibility": "tenant"})

        context = await api._execution_context(self._request(), str(TENANT), str(company), str(agent.id))

        assert context is not None and context.authorized_tools == ["bank:transfer_funds"]
        assert context.connector_config == {"bank": {"k": "v"}} and context.connector_names == ["bank"]
        assert context.bindings == {"fund_transfer": "bank:transfer_funds"} and context.run_grant is grant
        assert ready.await_count == 1 and resolved.call_args.kwargs["runtime"] == "conversation"

    @pytest.mark.asyncio
    async def test_a_missing_or_refused_agent_is_refused(self, monkeypatch):
        import api.v1.agents as agents_api
        from api.v1 import conversation as api

        monkeypatch.setattr(agents_api, "_require_company_for_tenant", AsyncMock(return_value=uuid.uuid4()))
        monkeypatch.setattr(api, "get_tenant_session", lambda *_a, **_k: _Session(None))
        with pytest.raises(HTTPException) as info:
            await api._execution_context(self._request(), str(TENANT), "c", str(uuid.uuid4()))
        assert info.value.status_code == 404
        retired = SimpleNamespace(id=uuid.uuid4(), status="retired", connector_ids=[], config={})
        monkeypatch.setattr(api, "get_tenant_session", lambda *_a, **_k: _Session(retired))
        monkeypatch.setattr(api, "require_agent_visible", lambda *_a, **_k: None)
        monkeypatch.setattr(api, "agent_status_refusal", lambda *_a, **_k: "Agent is retired")
        with pytest.raises(HTTPException) as info:
            await api._execution_context(self._request(), str(TENANT), "c", str(retired.id))
        assert info.value.status_code == 409


class TestSessionRoutes:
    @pytest.mark.asyncio
    async def test_the_session_is_read_and_reset_for_the_caller_and_channels_are_checked(self, monkeypatch):
        from api.v1 import conversation as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        dialogue = Dialogue(
            stage=engine.STAGE_COLLECTING, intent="fund_transfer", pending="amount", slots={"payee": "Ravi"}
        )
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=dialogue))
        monkeypatch.setattr(supervisor, "replay", AsyncMock(return_value=[]))
        reset = AsyncMock(return_value=True)
        monkeypatch.setattr(runtime, "reset_dialogue", reset)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))

        shown = await api.get_session(request, company_id="c1", agent_id="a1", channel="web", tenant_id=str(TENANT))
        assert shown["session_key"] == "web:c1:a1:u:u1" and shown["dialogue"]["pending"] == "amount"
        assert shown["dialogue"]["missing"] == ["amount"] and shown["dialogue"]["slots"] == {"payee": "Ravi"}

        cleared = await api.reset_session(
            request, company_id="c1", agent_id="a1", channel="voice", tenant_id=str(TENANT)
        )
        assert (
            cleared == {"session_key": "voice:c1:a1:u:u1", "reset": True}
            and reset.call_args.args[1] == "voice:c1:a1:u:u1"
        )

        with pytest.raises(HTTPException) as info:
            await api.get_session(request, company_id="", agent_id="", channel="pigeon", tenant_id=str(TENANT))
        assert info.value.status_code == 422

    def test_the_turn_input_rejects_unknown_fields_and_empty_text(self):
        from pydantic import ValidationError

        from api.v1 import conversation as api

        with pytest.raises(ValidationError):
            api.TurnIn(text="", company_id="c")
        with pytest.raises(ValidationError):
            api.TurnIn(text="hi", extra="x")
