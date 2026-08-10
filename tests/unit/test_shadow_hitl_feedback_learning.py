"""Regression coverage for the shadow-mode HITL feedback loop."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.feedback.shadow_learning import (
    MIN_HUMAN_REVIEWS_FOR_LEARNED_AUTONOMY,
    capture_hitl_feedback,
    is_confidence_only_condition,
    learned_review_policy,
)


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
        confidence_floor=Decimal("0.880"),
        hitl_condition="confidence < 0.88",
        shadow_min_samples=10,
        shadow_accuracy_floor=Decimal("0.800"),
        shadow_sample_count=4,
        shadow_scored_sample_count=4,
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


@pytest.mark.asyncio
async def test_approved_shadow_feedback_progressively_unlocks_routine_autonomy() -> None:
    agent = _agent()
    agent.shadow_sample_count = 10
    agent.shadow_scored_sample_count = 10
    observed = []

    for review_number in range(1, MIN_HUMAN_REVIEWS_FOR_LEARNED_AUTONOMY + 1):
        item = _hitl_item()
        session = MagicMock()
        session.execute = AsyncMock(side_effect=[_ScalarResult(None), _ScalarResult(agent)])
        session.add.side_effect = lambda row: setattr(row, "id", uuid.uuid4())
        session.flush = AsyncMock()

        result = await capture_hitl_feedback(
            session,
            item=item,
            decision="approve",
            notes=f"Accepted review {review_number}",
            actor_id=f"reviewer-{review_number}",
            actor_role="admin",
            actor_name="Reviewer",
            policy_action="advance",
            policy_state=None,
            delegated_from=None,
        )
        observed.append(result["confidence_after"])

    assert observed == sorted(observed)
    assert observed[0] == pytest.approx(0.769)
    assert observed[-1] == pytest.approx(0.842)

    policy = learned_review_policy(agent)
    assert policy["autonomy_eligible"] is True
    assert policy["effective_confidence_floor"] == 0.6
    assert policy["confidence_condition_suppressed"] is True


def test_learning_never_suppresses_explicit_policy_conditions() -> None:
    agent = _agent()
    agent.shadow_sample_count = 10
    agent.shadow_scored_sample_count = 10
    agent.shadow_feedback_count = 3
    agent.shadow_accuracy_current = Decimal("0.900")
    agent.shadow_human_confidence_current = Decimal("1.000")
    agent.hitl_condition = "amount > 500000"

    policy = learned_review_policy(agent)

    assert policy["autonomy_eligible"] is True
    assert policy["confidence_condition_suppressed"] is False


def test_learned_autonomy_enforces_hard_floor_for_permissive_legacy_config() -> None:
    agent = _agent()
    agent.confidence_floor = Decimal("0.400")
    agent.shadow_sample_count = 10
    agent.shadow_scored_sample_count = 10
    agent.shadow_feedback_count = 3
    agent.shadow_accuracy_current = Decimal("0.900")
    agent.shadow_human_confidence_current = Decimal("1.000")

    policy = learned_review_policy(agent)

    assert policy["autonomy_eligible"] is True
    assert policy["effective_confidence_floor"] == 0.6


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"shadow_scored_sample_count": 9}, "insufficient_scored_samples"),
        ({"shadow_feedback_count": 2}, "insufficient_human_reviews"),
        ({"shadow_accuracy_current": Decimal("0.799")}, "combined_confidence_below_floor"),
        ({"shadow_human_confidence_current": Decimal("0.799")}, "human_confidence_below_floor"),
        ({"status": "paused"}, "agent_status_not_learning"),
    ],
)
def test_learned_autonomy_fails_closed_when_any_evidence_gate_regresses(
    mutation: dict,
    reason: str,
) -> None:
    agent = _agent()
    agent.shadow_sample_count = 10
    agent.shadow_scored_sample_count = 10
    agent.shadow_feedback_count = 3
    agent.shadow_accuracy_current = Decimal("0.900")
    agent.shadow_human_confidence_current = Decimal("1.000")
    for field, value in mutation.items():
        setattr(agent, field, value)

    policy = learned_review_policy(agent)

    assert policy["autonomy_eligible"] is False
    assert policy["reason"] == reason
    assert policy["effective_confidence_floor"] == 0.88


def test_only_simple_confidence_conditions_can_be_suppressed() -> None:
    assert is_confidence_only_condition("confidence < 0.88") is True
    assert is_confidence_only_condition(" confidence <= 1.0 ") is True
    assert is_confidence_only_condition("amount < 100") is False
    assert is_confidence_only_condition("confidence < 0.8 or amount < 100") is False
    assert is_confidence_only_condition("") is False


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


def test_shadow_learning_migration_supports_legacy_orm_bootstrap() -> None:
    migration = (
        __import__("pathlib").Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "v6_z8_shadow_hitl_learning.py"
    ).read_text(encoding="utf-8")

    assert migration.count("ADD COLUMN IF NOT EXISTS") == 11
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
