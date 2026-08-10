"""Close the loop between shadow-mode HITL decisions and agent confidence."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.agent import Agent
from core.models.feedback import AgentFeedback
from core.models.hitl import HITLQueue

HUMAN_EVIDENCE_WEIGHT = Decimal("3.00")
MIN_HUMAN_REVIEWS_FOR_LEARNED_AUTONOMY = 3
LEARNED_ROUTINE_CONFIDENCE_FLOOR = Decimal("0.600")
_MILLI = Decimal("0.001")
_CONFIDENCE_ONLY_CONDITION = re.compile(
    r"^\s*confidence\s*(?:<|<=)\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\s*$",
    re.IGNORECASE,
)


def _q(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value)).quantize(
        _MILLI, rounding=ROUND_HALF_UP
    )


def _decision_signal(decision: str, terminal: bool) -> tuple[str, Decimal | None]:
    normalized = decision.strip().lower()
    if not terminal:
        return "hitl_vote", None
    if normalized in {"approve", "approved", "accept", "accepted"}:
        return "hitl_approve", Decimal("1")
    if normalized in {"reject", "rejected", "deny", "denied"}:
        return "hitl_reject", Decimal("0")
    if normalized in {"override", "correct", "correction", "edit"}:
        return "hitl_override", Decimal("0.25")
    return "hitl_vote", None


def _integer_metric(agent: Any, name: str, fallback: int = 0) -> int:
    value = getattr(agent, name, None)
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)
    return max(int(fallback or 0), 0)


def _decimal_metric(
    agent: Any,
    name: str,
    fallback: Decimal | None = None,
) -> Decimal | None:
    value = getattr(agent, name, None)
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        return fallback
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback
    if not parsed.is_finite():
        return fallback
    return _q(parsed)


def scored_shadow_sample_count(agent: Any) -> int:
    """Return count of samples that actually contributed model confidence.

    The fallback preserves compatibility with rows/mocks created before the
    dedicated counter existed. Migrated databases backfill the same value.
    """
    return _integer_metric(
        agent,
        "shadow_scored_sample_count",
        _integer_metric(agent, "shadow_sample_count"),
    )


def is_confidence_only_condition(condition: str | None) -> bool:
    """Whether a HITL expression merely duplicates the confidence gate."""
    return bool(_CONFIDENCE_ONLY_CONDITION.fullmatch(str(condition or "")))


def learned_review_policy(agent: Any) -> dict[str, Any]:
    """Compute the evidence-gated confidence floor for routine review.

    Learning may relax only the generic confidence gate. Explicit amount,
    policy, workflow, and action-risk approvals remain untouched. A hard 0.60
    per-run floor also remains in place so an anomalously uncertain run still
    reaches a human even after the agent has earned routine autonomy.
    """
    configured_floor = _decimal_metric(
        agent,
        "confidence_floor",
        Decimal("0"),
    ) or Decimal("0")
    accuracy_floor = max(
        _decimal_metric(
            agent,
            "shadow_accuracy_floor",
            Decimal("0"),
        )
        or Decimal("0"),
        LEARNED_ROUTINE_CONFIDENCE_FLOOR,
    )
    required_samples = _integer_metric(agent, "shadow_min_samples")
    scored_samples = scored_shadow_sample_count(agent)
    human_reviews = _integer_metric(agent, "shadow_feedback_count")
    combined = _decimal_metric(agent, "shadow_accuracy_current")
    human = _decimal_metric(agent, "shadow_human_confidence_current")
    status = str(getattr(agent, "status", "") or "")

    reason = "learned_routine_autonomy_enabled"
    eligible = True
    if status not in {"shadow", "active"}:
        eligible, reason = False, "agent_status_not_learning"
    elif required_samples <= 0:
        eligible, reason = False, "shadow_learning_disabled"
    elif scored_samples < required_samples:
        eligible, reason = False, "insufficient_scored_samples"
    elif human_reviews < MIN_HUMAN_REVIEWS_FOR_LEARNED_AUTONOMY:
        eligible, reason = False, "insufficient_human_reviews"
    elif combined is None or combined < accuracy_floor:
        eligible, reason = False, "combined_confidence_below_floor"
    elif human is None or human < accuracy_floor:
        eligible, reason = False, "human_confidence_below_floor"

    effective_floor = (
        LEARNED_ROUTINE_CONFIDENCE_FLOOR if eligible else configured_floor
    )
    confidence_condition_suppressed = eligible and is_confidence_only_condition(
        getattr(agent, "hitl_condition", "")
    )
    return {
        "autonomy_eligible": eligible,
        "reason": reason,
        "configured_confidence_floor": float(configured_floor),
        "effective_confidence_floor": float(effective_floor),
        "required_scored_samples": required_samples,
        "scored_samples": scored_samples,
        "required_human_reviews": MIN_HUMAN_REVIEWS_FOR_LEARNED_AUTONOMY,
        "human_reviews": human_reviews,
        "combined_confidence": float(combined) if combined is not None else None,
        "human_confidence": float(human) if human is not None else None,
        "confidence_condition_suppressed": confidence_condition_suppressed,
    }


async def capture_hitl_feedback(
    session: AsyncSession,
    *,
    item: HITLQueue,
    decision: str,
    notes: str,
    actor_id: str,
    actor_role: str,
    actor_name: str,
    policy_action: str,
    policy_state: dict[str, Any] | None,
    delegated_from: str | None,
) -> dict[str, Any]:
    """Persist one human action and calibrate shadow confidence atomically.

    The approval mutation and feedback insert share the caller's transaction.
    A row lock serializes concurrent decisions, while ``source_event_id`` makes
    retries idempotent. Human evidence is deliberately stronger than the
    model's self-reported confidence, but it never erases the model signal.
    """
    action_number = len((policy_state or {}).get("approvals") or []) or 1
    source_event_id = f"hitl:{item.id}:action:{action_number}"
    existing = await session.execute(
        select(AgentFeedback.id).where(
            AgentFeedback.tenant_id == item.tenant_id,
            AgentFeedback.source_event_id == source_event_id,
        )
    )
    existing_id = existing.scalar_one_or_none()
    if existing_id is not None:
        return {"feedback_id": str(existing_id), "deduplicated": True}

    agent_result = await session.execute(
        select(Agent)
        .where(Agent.id == item.agent_id, Agent.tenant_id == item.tenant_id)
        .with_for_update()
    )
    agent = agent_result.scalar_one()
    terminal = policy_action in {"advance", "reject"}
    feedback_type, human_signal = _decision_signal(decision, terminal)
    context = dict(item.context or {})
    ran_in_shadow = context.get("agent_status") == "shadow" or agent.status == "shadow"
    before = Decimal(str(agent.shadow_accuracy_current)) if agent.shadow_accuracy_current is not None else None
    after = before

    if ran_in_shadow and human_signal is not None:
        model_count = scored_shadow_sample_count(agent)
        model_average = agent.shadow_model_confidence_current
        if model_average is None:
            model_average = agent.shadow_accuracy_current
        model_average_d = Decimal(str(model_average or 0))

        human_count = int(agent.shadow_feedback_count or 0)
        human_average_d = Decimal(str(agent.shadow_human_confidence_current or 0))
        new_human_count = human_count + 1
        new_human_average = _q(
            ((human_average_d * human_count) + human_signal) / new_human_count
        )

        model_mass = model_average_d * model_count
        human_mass = new_human_average * new_human_count * HUMAN_EVIDENCE_WEIGHT
        denominator = Decimal(model_count) + Decimal(new_human_count) * HUMAN_EVIDENCE_WEIGHT
        after = _q(
            (model_mass + human_mass) / denominator if denominator else human_signal
        )

        agent.shadow_feedback_count = new_human_count
        agent.shadow_human_confidence_current = new_human_average
        agent.shadow_accuracy_current = after

    run_id = str(
        item.workflow_run_id
        or context.get("run_id")
        or context.get("correlation_id")
        or item.id
    )
    original_output = item.decision_options.get("context") if item.decision_options else None
    feedback = AgentFeedback(
        tenant_id=item.tenant_id,
        agent_id=item.agent_id,
        run_id=run_id,
        feedback_type=feedback_type,
        feedback_text=notes or f"Human decision: {decision}",
        original_output=original_output if isinstance(original_output, dict) else None,
        corrected_output=None,
        source="hitl",
        source_event_id=source_event_id,
        actor_id=actor_id,
        decision=decision,
        context={
            "hitl_id": str(item.id),
            "trigger_type": item.trigger_type,
            "ran_in_shadow": ran_in_shadow,
            "policy_action": policy_action,
            "policy_state": policy_state,
            "actor_role": actor_role,
            "actor_name": actor_name,
            "delegated_from": delegated_from,
        },
        confidence_before=before,
        confidence_after=after,
        learning_weight=HUMAN_EVIDENCE_WEIGHT if human_signal is not None else Decimal("0"),
    )
    session.add(feedback)
    await session.flush()
    return {
        "feedback_id": str(feedback.id),
        "feedback_type": feedback_type,
        "ran_in_shadow": ran_in_shadow,
        "confidence_before": float(before) if before is not None else None,
        "confidence_after": float(after) if after is not None else None,
        "deduplicated": False,
    }
