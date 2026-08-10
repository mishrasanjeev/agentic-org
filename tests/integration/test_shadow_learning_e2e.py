"""Real-Postgres E2E coverage for shadow learning and adaptive HITL."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.feedback import AgentFeedback
from core.models.hitl import HITLQueue


@pytest.mark.asyncio
async def test_shadow_feedback_graduates_routine_review_but_keeps_safety_gates(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_auth_headers,
    tenant_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.deps import get_user_role
    from api.main import app

    monkeypatch.setitem(app.dependency_overrides, get_user_role, lambda: "admin")

    create = await client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={
            "name": f"shadow-learning-e2e-{uuid.uuid4().hex[:8]}",
            "agent_type": "shadow_learning_probe",
            "domain": "platform",
            "system_prompt_text": "Return a deterministic test result.",
            "authorized_tools": [],
            "confidence_floor": 0.88,
            "shadow_min_samples": 10,
            "shadow_accuracy_floor": 0.80,
            "hitl_policy": {
                "condition": "confidence < 0.88",
                "assignee_role": "admin",
                "timeout_hours": 4,
                "on_timeout": "escalate",
            },
        },
    )
    assert create.status_code == 201, create.text
    agent_id = uuid.UUID(create.json()["agent_id"])
    tid = uuid.UUID(tenant_id)

    # Seed ten genuine model-scored shadow observations. Human evidence starts
    # empty so the next routine run must still enter HITL.
    async with get_tenant_session(tid) as session:
        agent = (
            await session.execute(
                select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
            )
        ).scalar_one()
        agent.shadow_sample_count = 10
        agent.shadow_scored_sample_count = 10
        agent.shadow_model_confidence_current = Decimal("0.700")
        agent.shadow_accuracy_current = Decimal("0.700")

    observed_floors: list[float] = []
    observed_conditions: list[str] = []

    async def deterministic_run(**kwargs):
        from core.langgraph.agent_graph import _check_hitl_trigger

        confidence = float(kwargs["task_input"]["inputs"].get("test_confidence", 0.7))
        output = {
            "answer": "deterministic",
            "amount": kwargs["task_input"]["inputs"].get("amount", 0),
        }
        floor = float(kwargs["confidence_floor"])
        condition = str(kwargs["hitl_condition"] or "")
        observed_floors.append(floor)
        observed_conditions.append(condition)
        trigger = _check_hitl_trigger(confidence, floor, condition, output)
        return {
            "status": "hitl_triggered" if trigger else "completed",
            "output": output,
            "confidence": confidence,
            "reasoning_trace": ["deterministic shadow-learning E2E"],
            "tool_calls": [],
            "hitl_trigger": trigger,
            "error": "",
            "performance": {
                "total_latency_ms": 1,
                "llm_tokens_used": 0,
                "llm_cost_usd": 0,
            },
        }

    import core.langgraph.runner as runner

    monkeypatch.setattr(runner, "run_agent", deterministic_run)

    confidences: list[float] = []
    for review_number in range(3):
        run = await client.post(
            f"/api/v1/agents/{agent_id}/run",
            headers=auth_headers,
            json={
                "action": "process",
                "inputs": {
                    "task": f"routine shadow case {review_number}",
                    "test_confidence": 0.70,
                },
            },
        )
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "hitl_triggered"
        assert run.json()["review_learning"]["autonomy_eligible"] is False

        pending = await client.get(
            "/api/v1/approvals",
            headers=auth_headers,
            params={"status": "pending", "per_page": 100},
        )
        assert pending.status_code == 200, pending.text
        matching = [
            item
            for item in pending.json()["items"]
            if item["agent_id"] == str(agent_id)
        ]
        assert len(matching) == 1
        assert matching[0]["trigger_type"] == "confidence_below_floor"

        decision = await client.post(
            f"/api/v1/approvals/{matching[0]['id']}/decide",
            headers=auth_headers,
            json={"decision": "approve", "notes": "Correct routine result"},
        )
        assert decision.status_code == 200, decision.text

        detail = await client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers)
        assert detail.status_code == 200, detail.text
        confidences.append(float(detail.json()["shadow_accuracy_current"]))
        assert detail.json()["review_learning"]["autonomy_eligible"] is (
            review_number == 2
        )

    assert confidences == sorted(confidences)
    assert confidences[-1] >= 0.80

    # The fourth identical routine case is autonomous: learned evidence lowers
    # only the generic confidence gate from 0.88 to the hard 0.60 backstop.
    autonomous = await client.post(
        f"/api/v1/agents/{agent_id}/run",
        headers=auth_headers,
        json={
            "action": "process",
            "inputs": {"task": "routine autonomous case", "test_confidence": 0.70},
        },
    )
    assert autonomous.status_code == 200, autonomous.text
    autonomous_body = autonomous.json()
    assert autonomous_body["status"] == "completed"
    assert autonomous_body["hitl_trigger"] is None
    assert autonomous_body["review_learning"]["autonomy_eligible"] is True
    assert autonomous_body["review_learning"]["effective_confidence_floor"] == 0.6
    assert autonomous_body["review_learning"]["confidence_condition_suppressed"] is True
    assert observed_floors[-1] == 0.6
    assert observed_conditions[-1] == ""

    # An anomalously low-confidence run still gets human review.
    low_confidence = await client.post(
        f"/api/v1/agents/{agent_id}/run",
        headers=auth_headers,
        json={
            "action": "process",
            "inputs": {"task": "uncertain edge case", "test_confidence": 0.40},
        },
    )
    assert low_confidence.status_code == 200, low_confidence.text
    assert low_confidence.json()["status"] == "hitl_triggered"
    assert low_confidence.json()["hitl_trigger"].startswith("confidence ")

    pending = await client.get(
        "/api/v1/approvals",
        headers=auth_headers,
        params={"status": "pending", "per_page": 100},
    )
    low_item = next(
        item
        for item in pending.json()["items"]
        if item["agent_id"] == str(agent_id)
        and item["trigger_type"] == "confidence_below_floor"
    )

    # Tenant scope is enforced before decision mutation or feedback capture.
    cross_tenant = await client.post(
        f"/api/v1/approvals/{low_item['id']}/decide",
        headers=make_auth_headers(tenant_id=str(uuid.uuid4())),
        json={"decision": "approve", "notes": "cross-tenant attempt"},
    )
    assert cross_tenant.status_code == 404

    # Two simultaneous retries serialize on the queue row: one succeeds and
    # one receives an intentional conflict, with exactly one feedback record.
    concurrent = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/approvals/{low_item['id']}/decide",
                headers=auth_headers,
                json={"decision": "approve", "notes": "concurrent retry"},
            )
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in concurrent) == [200, 409]
    async with get_tenant_session(tid) as session:
        feedback_rows = (
            await session.execute(
                select(func.count(AgentFeedback.id)).where(
                    AgentFeedback.tenant_id == tid,
                    AgentFeedback.source_event_id == f"hitl:{low_item['id']}:action:1",
                )
            )
        ).scalar_one()
        assert feedback_rows == 1

    # Explicit business-policy conditions are never suppressed by learning.
    async with get_tenant_session(tid) as session:
        agent = (
            await session.execute(
                select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
            )
        ).scalar_one()
        agent.hitl_condition = "amount > 500000"

    policy_run = await client.post(
        f"/api/v1/agents/{agent_id}/run",
        headers=auth_headers,
        json={
            "action": "process",
            "inputs": {
                "task": "large amount policy case",
                "test_confidence": 0.95,
                "amount": 750000,
            },
        },
    )
    assert policy_run.status_code == 200, policy_run.text
    assert policy_run.json()["status"] == "hitl_triggered"
    assert "condition matched" in policy_run.json()["hitl_trigger"]
    assert observed_conditions[-1] == "amount > 500000"

    async with get_tenant_session(tid) as session:
        policy_item = (
            await session.execute(
                select(HITLQueue)
                .where(
                    HITLQueue.tenant_id == tid,
                    HITLQueue.agent_id == agent_id,
                    HITLQueue.context["trigger"].astext.like("condition matched:%"),
                )
                .order_by(HITLQueue.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert policy_item.trigger_type == "policy_condition"
