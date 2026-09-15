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
