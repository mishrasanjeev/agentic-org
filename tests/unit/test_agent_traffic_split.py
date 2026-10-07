# SPDX-License-Identifier: Apache-2.0
"""The traffic split endpoints and the run path that applies a split."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import agents as agents_api
from core.agent_registry import approval, traffic
from core.config import settings
from core.schemas.api import AgentTrafficSplitIn

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: value)


def _agent(status="active", **over):
    base = {"id": uuid.uuid4(), "status": status, "config": {"temperature": 0.1}}
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def store(monkeypatch):
    holder: dict[str, _Session] = {}

    @asynccontextmanager
    async def _session(_tenant):
        yield holder["session"]

    monkeypatch.setattr(agents_api, "get_tenant_session", _session)
    monkeypatch.setattr(agents_api, "require_agent_mutable", lambda _agent, _caller: None)
    monkeypatch.setattr(agents_api, "require_agent_visible", lambda _agent, _caller: None)

    def install(*answers):
        holder["session"] = _Session(*answers)
        return holder["session"]

    return install


class TestEndpoints:
    def test_a_split_is_stored_under_a_row_lock_when_the_target_is_active(self, store):
        agent = _agent()
        target = _agent()
        session = store(agent, target)
        result = asyncio.run(
            agents_api.set_agent_traffic_split(
                agent.id,
                AgentTrafficSplitIn(split={"to_agent_id": str(target.id), "percent": 10}),
                tenant_id=str(TENANT),
                user_domains=None,
                caller=None,
            )
        )
        assert agent.config == {"temperature": 0.1, "traffic_split": {"to_agent_id": str(target.id), "percent": 10}}
        assert result["split"]["percent"] == 10 and result["enforced"] is False
        assert "FOR UPDATE" in session.statements[0] and "agents.tenant_id" in session.statements[1]

    def test_refusals_leave_the_agent_unchanged(self, store):
        agent = _agent()
        before = dict(agent.config)
        cases = [
            (AgentTrafficSplitIn(split={"to_agent_id": "x", "percent": 10}), (), 422),
            (AgentTrafficSplitIn(split={"to_agent_id": str(agent.id), "percent": 10}), (), 422),
            (AgentTrafficSplitIn(split={"to_agent_id": str(uuid.uuid4()), "percent": 10}), (agent, None), 404),
            (
                AgentTrafficSplitIn(split={"to_agent_id": str(uuid.uuid4()), "percent": 10}),
                (agent, _agent(status="shadow")),
                409,
            ),
        ]
        for body, answers, status in cases:
            store(*answers)
            with pytest.raises(HTTPException) as refused:
                asyncio.run(
                    agents_api.set_agent_traffic_split(
                        agent.id, body, tenant_id=str(TENANT), user_domains=None, caller=None
                    )
                )
            assert refused.value.status_code == status
        assert agent.config == before
        store(None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(
                agents_api.set_agent_traffic_split(
                    uuid.uuid4(), AgentTrafficSplitIn(split=None), tenant_id=str(TENANT), user_domains=None, caller=None
                )
            )
        assert missing.value.status_code == 404

    def test_null_removes_the_split_and_the_split_is_read_back(self, store):
        agent = _agent(config={"temperature": 0.1, "traffic_split": {"to_agent_id": str(uuid.uuid4()), "percent": 5}})
        store(agent)
        result = asyncio.run(
            agents_api.set_agent_traffic_split(
                agent.id, AgentTrafficSplitIn(split=None), tenant_id=str(TENANT), user_domains=None, caller=None
            )
        )
        assert agent.config == {"temperature": 0.1} and result["split"] is None
        split = {"to_agent_id": str(uuid.uuid4()), "percent": 25}
        store(_agent(config={"traffic_split": split}))
        read = asyncio.run(
            agents_api.get_agent_traffic_split(uuid.uuid4(), tenant_id=str(TENANT), user_domains=None, caller=None)
        )
        assert read["split"] == split and read["enforced"] is False


class TestRunPath:
    def test_the_run_checks_the_agent_asked_for_then_redirects_on_one_draw_and_says_so(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        run = src[src.index('@router.post("/agents/{agent_id}/run")') :]
        run = run[: run.index('"runtime": "langgraph",', run.index("response = {"))]
        assert run.index("require_agent_visible(agent_row, effective_caller)") < run.index(
            "_refuse_unrunnable_agent(agent_row, tenant_id)"
        )
        # The agent asked for passes its own status, floor and override checks before any redirection.
        assert run.index("_refuse_unrunnable_agent(agent_row, tenant_id)") < run.index(
            "agent_traffic.declared(agent_row)"
        )
        # One draw per run, shared by the decision to load the target and the choice.
        assert run.count("agent_traffic.bucket(split_cid)") == 1
        assert "agent_traffic.chooses_target(split, draw=split_draw)" in run
        assert "lambda _id: target_row, draw=split_draw" in run
        # The target is loaded tenant-scoped, must be visible to the caller and is held to the same checks.
        between = run[run.index("agent_traffic.chooses_target(") : run.index("agent_traffic.choose(")]
        assert "Agent.tenant_id == tid" in between
        assert "can_view_agent(target_row, effective_caller)" in between
        assert "_refuse_unrunnable_agent(target_row, tenant_id)" in between and "target_row = None" in between
        helper_start = src.index("async def _refuse_unrunnable_agent(")
        helper = src[helper_start : src.index("@router.", helper_start)]
        for control in (
            "agent_status_refusal(agent_row.status)",
            "_active_agent_below_production_floor(agent_row)",
            "check_operator_override(tenant_id, agent_id=str(agent_row.id))",
        ):
            assert control in helper
        assert '"requested_agent_id": str(requested_agent_id),' in run and '"served_by": served_by_split,' in run
        # The served agent is the one the rest of the run uses.
        assert "agent_id = chosen.id" in run

    def test_a_refused_promotion_names_the_registry_state(self):
        refused = agents_api._approval_refused(approval.ApprovalError("not_approved", "not yet", "review"))
        assert refused.status_code == 409
        assert refused.detail == {
            "error": "agent_registry",
            "code": "not_approved",
            "message": "not yet",
            "state": "review",
        }

    def test_off_by_default_no_run_is_redirected(self):
        assert settings.agent_traffic_split_enabled is False
        assert traffic.enabled() is False
