"""Regression coverage for the shadow-mode HITL feedback loop."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.feedback.shadow_learning import capture_hitl_feedback


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


def _hitl_item(*, status: str = "shadow") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        workflow_run_id=None,
        trigger_type="confidence_below_floor",
        context={"agent_status": status, "run_id": "run-123"},
        decision_options={"context": {"invoice_total": 100}},
    )


def _agent(*, status: str = "shadow") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        shadow_sample_count=4,
        shadow_accuracy_current=Decimal("0.700"),
        shadow_model_confidence_current=Decimal("0.700"),
        shadow_feedback_count=0,
        shadow_human_confidence_current=None,
    )


@pytest.mark.asyncio
async def test_shadow_approval_is_captured_and_raises_calibrated_confidence() -> None:
    item = _hitl_item()
    agent = _agent()
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_ScalarResult(None), _ScalarResult(agent)])
    captured = []

    def add(row):
        row.id = uuid.uuid4()
        captured.append(row)

    session.add.side_effect = add
    session.flush = AsyncMock()

    result = await capture_hitl_feedback(
        session,
        item=item,
        decision="approve",
        notes="Correct result",
        actor_id="reviewer-1",
        actor_role="admin",
        actor_name="Reviewer",
        policy_action="advance",
        policy_state=None,
        delegated_from=None,
    )

    assert result["feedback_type"] == "hitl_approve"
    assert result["ran_in_shadow"] is True
    assert result["confidence_after"] > result["confidence_before"]
    assert agent.shadow_feedback_count == 1
    assert agent.shadow_human_confidence_current == Decimal("1.000")
    assert captured[0].source == "hitl"
    assert captured[0].source_event_id.startswith(f"hitl:{item.id}:")


@pytest.mark.asyncio
async def test_nonterminal_vote_is_captured_without_changing_confidence() -> None:
    item = _hitl_item()
    agent = _agent()
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_ScalarResult(None), _ScalarResult(agent)])
    captured = []

    def add(row):
        row.id = uuid.uuid4()
        captured.append(row)

    session.add.side_effect = add
    session.flush = AsyncMock()

    result = await capture_hitl_feedback(
        session,
        item=item,
        decision="approve",
        notes="First quorum vote",
        actor_id="reviewer-1",
        actor_role="manager",
        actor_name="Reviewer",
        policy_action="collect",
        policy_state={"approvals": [{"user_id": "reviewer-1"}]},
        delegated_from=None,
    )

    assert result["feedback_type"] == "hitl_vote"
    assert result["confidence_after"] == result["confidence_before"]
    assert agent.shadow_feedback_count == 0
    assert captured[0].learning_weight == Decimal("0")


def test_collector_uses_the_deployed_feedback_schema() -> None:
    source = (
        __import__("pathlib").Path(__file__).parents[2]
        / "core"
        / "feedback"
        / "collector.py"
    ).read_text(encoding="utf-8")
    assert "feedback_text" in source
    assert "CAST(:corrected_output AS JSONB)" in source
    assert "(id, agent_id, run_id, feedback_type, text," not in source
