"""Aishwarya 13 May 2026 reopen regressions.

These tests pin the bugs to completed outcomes, not just route selection.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session_ctx(mock_session: AsyncMock) -> AsyncMock:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def test_oauth_connector_callback_is_pre_session_auth_exempt() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware
    from auth.middleware import AuthMiddleware

    assert "/api/v1/oauth/callback" in GrantexAuthMiddleware.EXEMPT_PATHS
    assert "/api/v1/oauth/callback" in AuthMiddleware.EXEMPT_PATHS


@pytest.mark.asyncio
async def test_active_agent_rollback_returns_to_shadow_without_snapshot() -> None:
    from api.v1.agents import rollback_agent
    from tests.unit.test_agents_and_sales import make_mock_agent

    tenant_id = "00000000-0000-0000-0000-000000000001"
    agent_id = uuid.uuid4()
    agent = make_mock_agent(
        id=agent_id,
        status="active",
        version="1.0.1",
        shadow_sample_count=12,
        shadow_accuracy_current=Decimal("0.910"),
    )

    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent
    version_result = MagicMock()
    version_result.scalar_one_or_none.return_value = None
    transition_result = MagicMock()
    transition_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[agent_result, version_result, transition_result])
    session.add = MagicMock()

    with patch("api.v1.agents.get_tenant_session") as get_tenant_session:
        get_tenant_session.return_value = _make_session_ctx(session)
        result = await rollback_agent(agent_id=agent_id, tenant_id=tenant_id)

    assert result["rolled_back"] is True
    assert result["from_status"] == "active"
    assert result["to_status"] == "shadow"
    assert result["checkpoint_available"] is False
    assert agent.status == "shadow"
    event = session.add.call_args.args[0]
    assert event.from_status == "active"
    assert event.to_status == "shadow"


def test_agent_detail_refreshes_shadow_metrics_after_sample_generation() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "ui" / "src" / "pages" / "AgentDetail.tsx").read_text(
        encoding="utf-8"
    )
    assert "<ShadowTab agent={agent} onUpdated={() => fetchAgent(true)} />" in src
    assert "await onUpdated();" in src
    assert "Refresh to see updated count and accuracy" not in src
    assert "Refresh to see updated results" not in src
