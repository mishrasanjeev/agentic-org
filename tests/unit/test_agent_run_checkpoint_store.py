# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""POST /agents/{id}/run when the configured checkpoint store is unavailable (PRD F-2)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from core.langgraph.checkpointer import CheckpointerUnavailableError
from tests.regression.test_ownership_agents_20260914 import _admin, _agent, _patch_session, _Session


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


def test_run_is_refused_with_503_and_reason_when_the_store_is_unavailable(app) -> None:
    agent = _agent("finance", agent_type="custom_agent")
    refusal = CheckpointerUnavailableError("checkpoint_store_unreachable", "PoolTimeout")
    with (
        _admin(app) as client,
        _patch_session(_Session(agent)),
        patch("api.v1.agents._resolve_connector_configs", AsyncMock(return_value=({}, []))),
        patch("core.langgraph.runner.run_agent", AsyncMock(side_effect=refusal)) as runner,
    ):
        resp = client.post(f"/api/v1/agents/{agent.id}/run", json={"inputs": {"query": "reconcile"}})

    runner.assert_awaited_once()
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == {
        "error": "agent_checkpoint_store_unavailable",
        "reason": "checkpoint_store_unreachable",
        "message": "The agent run store is unavailable. Retry later.",
    }


def _hitl_rows(session: _Session) -> list:
    from core.models.hitl import HITLQueue

    return [row for row in session.added if isinstance(row, HITLQueue)]


def _paused(**overrides):
    async def _run(**kwargs):
        return {
            "status": "hitl_triggered",
            "output": {"total": 750000},
            "confidence": 0.95,
            "reasoning_trace": [],
            "tool_calls_log": [],
            "hitl_trigger": "total > 500000",
            "error": "",
            "thread_id": kwargs["thread_id"],
            "performance": {},
            **overrides,
        }

    return AsyncMock(side_effect=_run)


def test_paused_run_stores_the_server_generated_tenant_thread_on_its_approval(app) -> None:
    from core.langgraph.thread_ids import thread_belongs_to_tenant
    from tests.regression.test_ownership_agents_20260914 import TENANT

    agent = _agent("finance", agent_type="custom_agent")
    session = _Session(agent)
    runner = _paused()
    recent_context = {
        "thread_id": "x",
        "conversation": [{"role": "user", "text": "मेरी काल्पनिक दुकान का रंग बैंगनी है।"}],
    }
    with (
        _admin(app) as client,
        _patch_session(session),
        patch("api.v1.agents._resolve_connector_configs", AsyncMock(return_value=({}, []))),
        patch("core.push.sender.notify_approval_created", AsyncMock()),
        patch("core.langgraph.runner.run_agent", runner),
    ):
        # A client-supplied thread id is ignored: it is not part of the contract.
        resp = client.post(
            f"/api/v1/agents/{agent.id}/run",
            json={"inputs": {"query": "reconcile"}, "thread_id": "tenant:guess", "context": recent_context},
        )

    assert resp.status_code == 200, resp.text
    passed_thread = runner.await_args.kwargs["thread_id"]
    assert thread_belongs_to_tenant(passed_thread, TENANT)
    assert runner.await_args.kwargs["task_input"]["context"] == recent_context
    (row,) = _hitl_rows(session)
    assert row.checkpoint_thread_id == passed_thread
    assert passed_thread not in resp.text


def test_approval_without_a_paused_checkpoint_stores_no_thread(app) -> None:
    agent = _agent("finance", agent_type="custom_agent")
    session = _Session(agent)
    with (
        _admin(app) as client,
        _patch_session(session),
        patch("api.v1.agents._resolve_connector_configs", AsyncMock(return_value=({}, []))),
        patch("core.push.sender.notify_approval_created", AsyncMock()),
        patch("core.langgraph.runner.run_agent", _paused(thread_id=None)),
    ):
        resp = client.post(f"/api/v1/agents/{agent.id}/run", json={"inputs": {"query": "reconcile"}})

    assert resp.status_code == 200, resp.text
    (row,) = _hitl_rows(session)
    assert row.checkpoint_thread_id is None


@pytest.mark.parametrize("change", ["matching", "agent", "company", "tools", "traffic_split", "redirected"])
def test_channel_context_scope_is_rechecked_before_model_dispatch(app, change, monkeypatch) -> None:
    from api.v1 import agents

    agent = _agent("finance", agent_type="custom_agent")
    scope = {"agent_id": str(agent.id), "company_id": None}
    if change == "agent":
        scope["agent_id"] = "00000000-0000-0000-0000-000000000002"
    elif change == "company":
        scope["company_id"] = "00000000-0000-0000-0000-000000000002"
    elif change == "tools":
        agent.authorized_tools = ["synthetic.read"]
    elif change == "traffic_split":
        agent.config = {"traffic_split": {"to_agent_id": "00000000-0000-0000-0000-000000000002", "percent": 100}}
    alternate = _agent("finance", agent_type="custom_agent", status="active")
    if change == "redirected":
        agent.status = "active"
        agent.config = {"traffic_split": {"to_agent_id": str(alternate.id), "percent": 100}}

    def internal_context(request: Request):
        request.state.channel_answer_scope = scope
        return ["finance"]

    runner = _paused()
    with (
        _admin(app) as client,
        _patch_session(_Session(agent)),
        patch("api.v1.agents._resolve_connector_configs", AsyncMock(return_value=({}, []))),
        patch("core.push.sender.notify_approval_created", AsyncMock()),
        patch("core.langgraph.runner.run_agent", runner),
        patch("api.v1.agents.agent_traffic.enabled", return_value=change == "redirected"),
        patch("api.v1.agents.agent_traffic.choose", return_value=(alternate, "synthetic_split")),
    ):
        # The app is shared by other route suites; restore its domain dependency.
        monkeypatch.setitem(app.dependency_overrides, agents.get_user_domains, internal_context)
        response = client.post(f"/api/v1/agents/{agent.id}/run", json={"inputs": {"task": "synthetic question"}})

    if change == "matching":
        assert response.status_code == 200, response.text
        runner.assert_awaited_once()
    else:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"] == "channel_context_scope_changed"
        runner.assert_not_awaited()
