# SPDX-License-Identifier: Apache-2.0
"""A paused agent is refused by the run endpoint, chat and workflow steps when the setting is on (A-110)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_the_run_endpoint_and_chat_consult_the_status(monkeypatch):
    for path in ("api/v1/agents.py", "api/v1/chat.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "agent_status_refusal(" in src, path
    run_src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    body = run_src[run_src.index("async def run_agent(") :]
    assert body.index("agent_status_refusal(") < body.index("_active_agent_below_production_floor(agent_row)")


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
