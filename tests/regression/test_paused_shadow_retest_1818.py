# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Issue #1818: replay pause -> blocked resume -> retest -> shadow resume over HTTP.

The real router and ownership checks run against a tenant-filtering session
double. This is not PostgreSQL/RLS or production acceptance evidence.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from api.v1 import agents
from core.models.agent import Agent, AgentLifecycleEvent
from core.ownership import Caller


@pytest.fixture
def lifecycle(monkeypatch):
    tenant_id, owner_id = uuid4(), uuid4()
    agent = Agent(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Shadow recovery",
        domain="finance",
        status="shadow",
        visibility="personal",
        owner_user_id=owner_id,
        shadow_sample_count=16,
        shadow_scored_sample_count=16,
        shadow_accuracy_current=Decimal("0.483"),
        shadow_accuracy_floor=Decimal("0.800"),
        shadow_min_samples=10,
        shadow_model_confidence_current=Decimal("0.700"),
        shadow_human_confidence_current=Decimal("0.400"),
        shadow_feedback_count=3,
    )
    events, statements = [], []
    caller = Caller(owner_id, "cfo", ["finance"], False, False)
    session = MagicMock()

    async def execute(statement):
        statements.append(statement)
        params = statement.compile(dialect=postgresql.dialect()).params
        entity = statement.column_descriptions[0]["entity"]
        row = None
        if entity is Agent:
            if params.get("id_1") == agent.id and params.get("tenant_id_1") == agent.tenant_id:
                row = agent
        elif entity is AgentLifecycleEvent:
            row = next((event for event in reversed(events) if event.to_status == "paused"), None)
        else:
            raise AssertionError(f"Unexpected query: {statement}")
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        return result

    def add(event):
        assert isinstance(event, AgentLifecycleEvent)
        event.created_at = datetime.now(UTC)
        events.append(event)

    session.execute = AsyncMock(side_effect=execute)
    session.add.side_effect = add

    @asynccontextmanager
    async def tenant_session(tid, *_args):
        assert tid == tenant_id
        yield session

    monkeypatch.setattr(agents, "get_tenant_session", tenant_session)
    monkeypatch.setattr(
        agents,
        "revoke_agent_authority",
        AsyncMock(
            return_value=MagicMock(
                token_revoked=True,
                revoked=0,
                failed=0,
            )
        ),
    )
    app = FastAPI()
    app.include_router(agents.router, prefix="/api/v1")
    app.dependency_overrides[agents.get_current_tenant] = lambda: str(tenant_id)
    app.dependency_overrides[agents.caller_from_request] = lambda: caller
    with TestClient(app) as client:
        yield client, agent, events, statements, app


def test_pause_retest_resume_recovery_preserves_promotion_gate(lifecycle):
    client, agent, events, statements, _ = lifecycle
    path = f"/api/v1/agents/{agent.id}"
    assert client.post(f"{path}/pause").json()["status"] == "paused"
    blocked = client.post(f"{path}/resume")
    assert blocked.status_code == 409
    assert "retest" in blocked.json()["detail"]

    for _ in range(2):
        retest = client.post(f"{path}/retest")
        assert retest.status_code == 200, retest.text
        assert retest.json()["retest"] is True
        assert agent.status == "paused"
        assert agent.shadow_sample_count == agent.shadow_scored_sample_count == 0
        assert agent.shadow_feedback_count == 0
        assert agent.shadow_accuracy_current is None
        assert agent.shadow_model_confidence_current is None
        assert agent.shadow_human_confidence_current is None
        assert (events[-1].from_status, events[-1].to_status) == ("shadow", "paused")
    assert retest.json()["previous_sample_count"] == 0
    assert events[1].shadow_samples == 16
    assert events[1].shadow_accuracy == pytest.approx(0.483)

    resumed = client.post(f"{path}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == agent.status == "shadow"
    assert (events[-1].from_status, events[-1].to_status) == ("paused", "shadow")
    promotion = client.post(f"{path}/promote")
    assert promotion.status_code == 409
    assert "0/10 scored samples" in promotion.json()["detail"]
    assert agent.status == "shadow"

    pause_queries = [s for s in statements if s.column_descriptions[0]["entity"] is AgentLifecycleEvent]
    for statement in pause_queries:
        compiled = statement.compile(dialect=postgresql.dialect())
        assert compiled.params["tenant_id_1"] == agent.tenant_id
        assert compiled.params["agent_id_1"] == agent.id
        assert compiled.params["to_status_1"] == "paused"
        assert "created_at DESC" in str(compiled)
        assert compiled.params["param_1"] == 1


def test_resume_ignores_accuracy_without_samples(lifecycle):
    client, agent, _, _, _ = lifecycle
    agent.shadow_sample_count = agent.shadow_scored_sample_count = 0
    agent.shadow_accuracy_current = Decimal("0.000")
    path = f"/api/v1/agents/{agent.id}"
    assert client.post(f"{path}/pause").status_code == 200
    response = client.post(f"{path}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == agent.status == "shadow"


@pytest.mark.parametrize("previous", ["active", "inactive", "retired", "paused", None])
def test_retest_rejects_paused_non_shadow_origins(lifecycle, previous):
    client, agent, events, _, _ = lifecycle
    # An older shadow pause must not override the most recent pause's origin.
    events.append(AgentLifecycleEvent(from_status="shadow", to_status="paused"))
    if previous is not None:
        events.append(AgentLifecycleEvent(from_status=previous, to_status="paused"))
    else:
        events.clear()
    agent.status = "paused"
    response = client.post(f"/api/v1/agents/{agent.id}/retest")
    assert response.status_code == 409
    assert agent.status == "paused"
    assert agent.shadow_sample_count == 16
    assert agent.shadow_accuracy_current == Decimal("0.483")


@pytest.mark.parametrize("status", ["active", "inactive", "retired"])
def test_retest_rejects_other_current_states(lifecycle, status):
    client, agent, _, _, _ = lifecycle
    agent.status = status
    assert client.post(f"/api/v1/agents/{agent.id}/retest").status_code == 409
    assert agent.shadow_sample_count == 16


def test_retest_shadow_still_resets_without_changing_status(lifecycle):
    client, agent, events, _, _ = lifecycle
    assert client.post(f"/api/v1/agents/{agent.id}/retest").status_code == 200
    assert agent.status == "shadow"
    assert (events[-1].from_status, events[-1].to_status) == ("shadow", "shadow")


def test_retest_cannot_cross_tenant_or_owner_boundary(lifecycle):
    client, agent, _, _, app = lifecycle
    path = f"/api/v1/agents/{agent.id}/retest"
    app.dependency_overrides[agents.caller_from_request] = lambda: Caller(
        uuid4(),
        "cfo",
        ["finance"],
        False,
        False,
    )
    assert client.post(path).status_code == 404
    agent.tenant_id = uuid4()
    assert client.post(path).status_code == 404
    assert agent.shadow_sample_count == 16
