# SPDX-License-Identifier: Apache-2.0
"""A direct Run Agent result must be explainable without another model call."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from core.ownership import Caller


@pytest.fixture
def explanation_case(monkeypatch):
    import api.v1.agents as routes
    from core.config import settings

    tenant, owner, agent_id = uuid4(), uuid4(), uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        tenant_id=tenant,
        domain="finance",
        visibility="personal",
        owner_user_id=owner,
    )
    now = datetime.now(UTC)
    audit = SimpleNamespace(
        id=uuid4(),
        created_at=now,
        resource_id="msg_synthetic",
        outcome="completed",
        details={"confidence": 1.0, "reasoning_trace": ["Calling LLM (synthetic-model)"]},
    )
    case = SimpleNamespace(agent=agent, audit=audit, result=None, statements=[], tenant_calls=[])

    class Session:
        async def execute(self, statement):
            case.statements.append(str(statement))
            result = MagicMock()
            table = statement.column_descriptions[0]["entity"].__tablename__
            result.scalar_one_or_none.return_value = {
                "agents": case.agent,
                "agent_task_results": case.result,
                "audit_log": case.audit,
            }[table]
            return result

    @asynccontextmanager
    async def session_for(value):
        case.tenant_calls.append(value)
        yield Session()

    monkeypatch.setattr(routes, "get_tenant_session", session_for)
    monkeypatch.setattr(settings, "agent_explanation_audit_enabled", True, raising=False)
    case.call = lambda caller=None: routes.get_latest_explanation(
        agent_id,
        tenant_id=str(tenant),
        user_domains=None,
        caller=caller or Caller(owner, "developer", ["finance"], False, False),
    )
    case.tenant, case.now = tenant, now
    return case


@pytest.mark.asyncio
async def test_direct_run_audit_is_not_reported_as_never_run(explanation_case):
    case = explanation_case
    response = await case.call()
    assert response["has_run"] is True
    assert response["run_id"] == "msg_synthetic"
    assert response["status"] == "completed"
    assert response["confidence"] == 1.0
    assert response["record_source"] == "direct_run_audit"
    assert response["tool_usage_recorded"] is False
    assert "Tool-call details were not captured in this run record." in response["bullets"]
    assert response["tools_cited"] == []
    assert case.tenant_calls == [case.tenant]
    query = next(s for s in case.statements if "FROM audit_log" in s)
    assert "audit_log.tenant_id" in query and "audit_log.agent_id" in query
    assert "audit_log.event_type" in query and "audit_log.resource_type" in query


@pytest.mark.asyncio
async def test_fallback_is_default_off(explanation_case, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "agent_explanation_audit_enabled", False, raising=False)
    response = await explanation_case.call()
    assert response["has_run"] is False
    assert not any("FROM audit_log" in s for s in explanation_case.statements)


@pytest.mark.asyncio
async def test_private_agent_stops_before_any_history_query(explanation_case):
    with pytest.raises(HTTPException) as refused:
        await explanation_case.call(Caller(uuid4(), "developer", ["finance"], False, False))
    assert refused.value.status_code == 404
    assert len(explanation_case.statements) == 1


@pytest.mark.asyncio
async def test_foreign_or_missing_agent_stops_before_any_history_query(explanation_case):
    explanation_case.agent = None
    with pytest.raises(HTTPException) as refused:
        await explanation_case.call()
    assert refused.value.status_code == 404
    assert len(explanation_case.statements) == 1


@pytest.mark.asyncio
async def test_newer_durable_result_wins_over_old_audit(explanation_case):
    case = explanation_case
    case.result = SimpleNamespace(
        id=uuid4(),
        created_at=case.now + timedelta(seconds=1),
        status="completed",
        confidence=0.95,
        tool_calls=[],
        hitl_required=False,
        hitl_decision=None,
        error_message=None,
        duration_ms=12,
    )
    response = await case.call()
    assert response["run_id"] == str(case.result.id)
    assert response["confidence"] == 0.95


@pytest.mark.asyncio
async def test_newer_direct_run_wins_over_old_durable_result(explanation_case):
    case = explanation_case
    case.result = SimpleNamespace(created_at=case.now - timedelta(seconds=1))
    response = await case.call()
    assert response["run_id"] == "msg_synthetic"


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [None, float("nan"), float("inf"), "unavailable", -1, 2, True, 10**400])
async def test_legacy_bad_confidence_is_not_fabricated(explanation_case, confidence):
    explanation_case.audit.details["confidence"] = confidence
    response = await explanation_case.call()
    assert response["confidence"] is None


@pytest.mark.asyncio
async def test_hitl_is_reported_without_inventing_a_decision(explanation_case):
    explanation_case.audit.outcome = "hitl_triggered"
    response = await explanation_case.call()
    assert response["hitl_required"] is True
    assert response["hitl_decision"] is None
    assert "Review outcome is available in Approvals; no decision is inferred here." in response["bullets"]


@pytest.mark.asyncio
async def test_no_history_keeps_truthful_empty_state(explanation_case):
    explanation_case.audit = None
    assert (await explanation_case.call())["has_run"] is False


def test_explanation_audit_setting_defaults_off():
    from core.config import Settings

    assert Settings.model_fields["agent_explanation_audit_enabled"].default is False
