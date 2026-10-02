# SPDX-License-Identifier: Apache-2.0
"""A paused agent is refused by the run endpoint, chat and workflow steps when the setting is on (A-110)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.governance import agent_status

ROOT = Path(__file__).resolve().parents[3]


def test_default_off_keeps_the_legacy_statuses(monkeypatch):
    monkeypatch.setattr(agent_status.settings, "paused_agents_refused", False)
    assert agent_status.inactive_agent_statuses() == {"deleted", "retired"}
    assert agent_status.refusal_for("paused") is None
    assert agent_status.refusal_for("retired") == "Cannot run a retired agent"
    assert agent_status.refusal_for("active") is None


def test_on_refuses_paused(monkeypatch):
    monkeypatch.setattr(agent_status.settings, "paused_agents_refused", True)
    assert "paused" in agent_status.inactive_agent_statuses()
    assert agent_status.refusal_for("paused") == "Agent is paused"
    assert agent_status.refusal_for("shadow") is None


class _Row(SimpleNamespace):
    def __getattr__(self, name):  # attributes the routes read but these tests do not set
        return None


class _PassedStatusCheckError(Exception):
    """Raised by the first call after the status check, to prove the check let the request through."""


def _paused_agent():
    return _Row(
        id=uuid.uuid4(), domain="finance", name="probe", agent_type="finance", status="paused",
        authorized_tools=[], connector_ids=[],
    )


def _session_returning(agent):
    result = MagicMock()
    result.scalar_one_or_none.return_value = agent

    @contextlib.asynccontextmanager
    async def _session(*_args, **_kwargs):
        yield SimpleNamespace(execute=AsyncMock(return_value=result))

    return _session


def _run_endpoint(agent):
    """POST /agents/{id}/run, replayed against a session that returns ``agent``."""
    from fastapi import HTTPException

    from api.v1 import agents

    with (
        patch.object(agents, "get_tenant_session", _session_returning(agent)),
        patch.object(agents, "require_agent_visible", lambda *_: None),
        patch.object(agents, "_active_agent_below_production_floor", side_effect=_PassedStatusCheckError),
    ):
        try:
            payload = {"inputs": {"task": "Summarise the open invoices for this quarter"}}
            asyncio.run(agents.run_agent(agent.id, MagicMock(), payload=payload, tenant_id=str(uuid.uuid4())))
        except HTTPException as exc:
            return exc
        except _PassedStatusCheckError:
            return None
    raise AssertionError("the run endpoint neither refused nor reached the next check")


def _chat_endpoint(agent):
    """POST /chat/query naming the agent, replayed against a session that returns ``agent``."""
    from fastapi import HTTPException

    from api.v1 import chat

    body = chat.ChatQueryRequest(query="hello", agent_id=str(agent.id))
    with (
        patch("api.v1.agents._require_company_for_tenant", AsyncMock(return_value=uuid.uuid4())),
        patch.object(chat, "caller_from_request", return_value=SimpleNamespace(user_id=None, is_admin=True)),
        patch.object(chat, "get_tenant_session", _session_returning(agent)),
        patch.object(chat, "require_agent_visible", lambda *_: None),
        patch.object(chat, "_pinned_llm_provider", side_effect=_PassedStatusCheckError),
    ):
        try:
            asyncio.run(chat.chat_query(body, MagicMock(), tenant_id=str(uuid.uuid4()), user_domains=None))
        except HTTPException as exc:
            return exc
        except _PassedStatusCheckError:
            return None
    raise AssertionError("the chat endpoint neither refused nor reached the next step")


@pytest.mark.parametrize("endpoint", [_run_endpoint, _chat_endpoint], ids=["run", "chat"])
def test_a_paused_agent_is_refused_with_409_when_on_and_runs_when_off(monkeypatch, endpoint):
    monkeypatch.setattr(agent_status.settings, "paused_agents_refused", True)
    refused = endpoint(_paused_agent())
    assert refused is not None and refused.status_code == 409 and refused.detail == "Agent is paused"

    monkeypatch.setattr(agent_status.settings, "paused_agents_refused", False)
    assert endpoint(_paused_agent()) is None


@pytest.mark.parametrize("endpoint", [_run_endpoint, _chat_endpoint], ids=["run", "chat"])
def test_a_retired_agent_stays_refused_with_the_setting_off(monkeypatch, endpoint):
    monkeypatch.setattr(agent_status.settings, "paused_agents_refused", False)
    agent = _paused_agent()
    agent.status = "retired"
    refused = endpoint(agent)
    assert refused is not None and refused.status_code == 409 and "retired" in refused.detail


@pytest.mark.parametrize(("on", "expect_config"), [(False, True), (True, False)])
def test_workflow_agent_step_treats_a_paused_agent_as_inactive_when_on(monkeypatch, on, expect_config):
    from workflows import step_types

    monkeypatch.setattr(agent_status.settings, "paused_agents_refused", on)
    agent_id, tenant_id = uuid.uuid4(), uuid.uuid4()

    class _Row(SimpleNamespace):
        def __getattr__(self, name):  # attributes the config builder reads but this test does not set
            return None

    row = _Row(
        id=agent_id,
        tenant_id=tenant_id,
        company_id=None,
        domain="finance",
        agent_type="finance",
        status="paused",
        name="probe",
        employee_name=None,
        system_prompt="x",
        authorized_tools=[],
        llm_model=None,
        llm_provider=None,
        hitl_condition="",
        confidence_floor=0.88,
        output_schema=None,
        prompt_variables={},
        cost_controls=None,
    )
    session = MagicMock()

    async def _execute(_query):
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        return result

    session.execute = _execute

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield session

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    config = asyncio.run(step_types._load_workflow_agent_config(str(agent_id), str(tenant_id)))
    assert bool(config) is expect_config
