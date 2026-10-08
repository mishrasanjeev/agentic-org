# SPDX-License-Identifier: Apache-2.0
"""Ratings, reliability metrics and certification status for an agent's card.

**Reliability** is read from the agent's stored task results over a window
(30 days by default): runs by status, the share that needed a human, the
average and 95th-percentile duration, tokens and cost per run, and the
feedback recorded against the agent by type. Nothing is computed from a
run's content; the figures are counts and sums over rows the agent already
leaves. Shadow accuracy is the agent's own.

**Ratings** are one score from 1 to 5 per user and agent, with a short
comment; a new rating by the same user replaces the old. The card carries
the average and the count, never who rated.

**Certification** is what the registry and the attestations can say: the
registry state (approved or published means a second person approved it),
the evaluation gate verdict, and the tenant's attestation for the agent's
model provider (region, no training on tenant data, valid or expired).
Grantex trust-registry attestations and passports are not attached here;
the card says so.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, cast, func, select

from core.models.agent_rating import AgentRating
from core.models.agent_task_result import AgentTaskResult
from core.models.feedback import AgentFeedback

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365
MAX_COMMENT = 500


class RatingError(ValueError):
    pass


def validate_window(days: Any) -> int:
    if days is None:
        return DEFAULT_WINDOW_DAYS
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_WINDOW_DAYS:
        raise RatingError(f"days is a whole number between 1 and {MAX_WINDOW_DAYS}")
    return days


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


async def metrics(session: Any, tenant_id: uuid.UUID, agent: Any, *, days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """Counts and sums over the agent's task results and feedback in the window."""
    since = datetime.now(UTC) - timedelta(days=days)
    by_status = (
        await session.execute(
            select(AgentTaskResult.status, func.count(), func.sum(cast(AgentTaskResult.hitl_required, Integer)))
            .where(
                AgentTaskResult.agent_id == agent.id,
                AgentTaskResult.tenant_id == tenant_id,
                AgentTaskResult.created_at >= since,
            )
            .group_by(AgentTaskResult.status)
        )
    ).all()
    statuses = {str(status): int(count or 0) for status, count, _hitl in by_status}
    runs = sum(statuses.values())
    hitl = sum(int(hitl or 0) for _status, _count, hitl in by_status)
    totals = (
        await session.execute(
            select(
                func.avg(AgentTaskResult.duration_ms),
                func.percentile_cont(0.95).within_group(AgentTaskResult.duration_ms),
                func.sum(AgentTaskResult.tokens_used),
                func.sum(AgentTaskResult.cost_usd),
                func.avg(AgentTaskResult.confidence),
            ).where(
                AgentTaskResult.agent_id == agent.id,
                AgentTaskResult.tenant_id == tenant_id,
                AgentTaskResult.created_at >= since,
            )
        )
    ).one()
    avg_ms, p95_ms, tokens, cost, confidence = totals
    feedback_rows = (
        await session.execute(
            select(AgentFeedback.feedback_type, func.count())
            .where(
                AgentFeedback.agent_id == agent.id,
                AgentFeedback.tenant_id == tenant_id,
                AgentFeedback.created_at >= since,
            )
            .group_by(AgentFeedback.feedback_type)
        )
    ).all()
    feedback = {str(kind): int(count or 0) for kind, count in feedback_rows}
    completed = statuses.get("completed", 0)
    failed = statuses.get("failed", 0)
    shadow = getattr(agent, "shadow_accuracy_current", None)
    return {
        "window_days": days,
        "runs": runs,
        "by_status": statuses,
        "completion_rate": _rate(completed, runs),
        "failure_rate": _rate(failed, runs),
        "human_review_rate": _rate(hitl, runs),
        "avg_duration_ms": int(avg_ms) if avg_ms is not None else None,
        "p95_duration_ms": int(p95_ms) if p95_ms is not None else None,
        "tokens_per_run": round(int(tokens or 0) / runs, 1) if runs else None,
        "cost_per_run_usd": round(float(cost or 0.0) / runs, 6) if runs else None,
        "avg_confidence": round(float(confidence), 4) if confidence is not None else None,
        "feedback": feedback,
        "shadow_accuracy": float(shadow) if shadow is not None else None,
    }


async def rating_summary(session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> dict[str, Any]:
    count, average = (
        await session.execute(
            select(func.count(), func.avg(AgentRating.score)).where(
                AgentRating.agent_id == agent_id, AgentRating.tenant_id == tenant_id
            )
        )
    ).one()
    return {"count": int(count or 0), "average": round(float(average), 2) if average is not None else None}


async def rate(
    session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID, user_id: uuid.UUID, score: Any, comment: Any = None
) -> AgentRating:
    """Record or replace the user's rating of the agent."""
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise RatingError("score is a whole number between 1 and 5")
    if comment is not None and (not isinstance(comment, str) or len(comment) > MAX_COMMENT):
        raise RatingError(f"comment is text of at most {MAX_COMMENT} characters")
    clean = comment.strip() if isinstance(comment, str) and comment.strip() else None
    existing = (
        await session.execute(
            select(AgentRating)
            .where(AgentRating.agent_id == agent_id, AgentRating.tenant_id == tenant_id, AgentRating.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is not None:
        existing.score = score
        existing.comment = clean
        existing.updated_at = now
        return existing
    rating = AgentRating(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        score=score,
        comment=clean,
        created_at=now,
        updated_at=now,
    )
    session.add(rating)
    return rating


async def certification(
    session: Any, tenant_id: uuid.UUID, agent: Any, *, registry_state: str, gate_verdict: dict
) -> dict[str, Any]:
    """What the registry and the attestations can say about the agent being fit for production."""
    from core.models.provider_attestation import ProviderAttestation

    provider = str(getattr(agent, "llm_provider", None) or "")
    attestation = None
    if provider:
        row = (
            await session.execute(
                select(ProviderAttestation)
                .where(ProviderAttestation.tenant_id == tenant_id, ProviderAttestation.provider == provider)
                .order_by(ProviderAttestation.attested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            now = datetime.now(UTC)
            valid = row.revoked_at is None and (row.expires_at is None or row.expires_at > now)
            attestation = {
                "provider": row.provider,
                "data_region": row.data_region,
                "in_region": bool(row.in_region),
                "no_training": bool(row.no_training),
                "valid": valid,
                "attested_at": row.attested_at.isoformat() if row.attested_at else None,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
    return {
        "registry_approved": registry_state in ("approved", "published"),
        "registry_state": registry_state,
        "evaluation_gate": gate_verdict,
        "provider_attestation": attestation,
        # Honest about what is not here: the card does not carry a trust-registry attestation or a passport.
        "trust_registry": {
            "attached": False,
            "note": "Grantex trust-registry attestations and passports are not attached",
        },
    }
