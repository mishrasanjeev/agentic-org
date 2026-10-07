# SPDX-License-Identifier: Apache-2.0
"""Conversational services: the session store, the agent context a turn runs with, and the session routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.conversation import dialogue as engine
from core.conversation import runtime
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
