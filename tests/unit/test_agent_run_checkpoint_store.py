# SPDX-License-Identifier: Apache-2.0
"""POST /agents/{id}/run when the configured checkpoint store is unavailable (PRD F-2)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

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
            json={"inputs": {"query": "reconcile"}, "thread_id": "tenant:guess", "context": {"thread_id": "x"}},
        )

    assert resp.status_code == 200, resp.text
    passed_thread = runner.await_args.kwargs["thread_id"]
    assert thread_belongs_to_tenant(passed_thread, TENANT)
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
