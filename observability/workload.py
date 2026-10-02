# SPDX-License-Identifier: Apache-2.0
"""The live workload of one tenant: review deadlines and the last hour's outcomes, each read on its own.

A part that cannot be read says so (``error``) instead of failing the whole
answer, as the compliance evidence package does, so the console always shows
what it can. Reads only, and every part is the tenant's own: the task queues
are shared by every tenant, so their depths are not a tenant's workload and
are not answered here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from observability import timeline, tracing

logger = structlog.get_logger()

WINDOW_HOURS = 1


async def review_deadlines(tenant_id: uuid.UUID) -> dict[str, Any]:
    """Pending reviews, how many are past their deadline, and the soonest deadline still ahead."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.hitl import HITLQueue

    now = datetime.now(UTC)
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    select(HITLQueue.expires_at).where(HITLQueue.tenant_id == tenant_id, HITLQueue.status == "pending")
                )
            ).all()
    # enterprise-gate: broad-except-ok reason=an-unreadable-review-queue-is-reported-as-unknown-not-raised
    except Exception as exc:
        logger.warning("workload_reviews_unavailable", error_type=type(exc).__name__)
        return {
            "pending": None,
            "overdue": None,
            "soonest_due_at": None,
            "soonest_seconds_left": None,
            "error": type(exc).__name__,
        }
    deadlines = [row[0] for row in rows if row[0] is not None]
    ahead = sorted(deadline for deadline in deadlines if deadline > now)
    soonest = ahead[0] if ahead else None
    return {
        "pending": len(rows),
        "overdue": sum(1 for deadline in deadlines if deadline <= now),
        "soonest_due_at": soonest.isoformat() if soonest is not None else None,
        "soonest_seconds_left": int((soonest - now).total_seconds()) if soonest is not None else None,
        "error": None,
    }


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return int(ordered[len(ordered) // 2])


async def run_outcomes(tenant_id: uuid.UUID, *, hours: int = WINDOW_HOURS) -> dict[str, Any]:
    """The stored runs of the window by their outcome, with the median duration."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.run_span import RunSpan

    since = datetime.now(UTC) - timedelta(hours=hours)
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    select(RunSpan.attributes, RunSpan.duration_ms).where(
                        RunSpan.tenant_id == tenant_id,
                        RunSpan.name.in_(sorted(timeline.ROOT_SPANS)),
                        RunSpan.started_at >= since,
                    )
                )
            ).all()
    # enterprise-gate: broad-except-ok reason=unreadable-run-spans-are-reported-as-unknown-not-raised
    except Exception as exc:
        logger.warning("workload_runs_unavailable", error_type=type(exc).__name__)
        return {
            "window_hours": hours,
            "runs": None,
            "by_status": None,
            "p50_duration_ms": None,
            "error": type(exc).__name__,
        }
    by_status: dict[str, int] = {}
    for attributes, _duration in rows:
        status = str((attributes or {}).get("agent.run.status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "window_hours": hours,
        "runs": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "p50_duration_ms": _median([int(duration or 0) for _attributes, duration in rows]),
        "error": None,
    }


async def model_call_outcomes(tenant_id: uuid.UUID, *, hours: int = WINDOW_HOURS) -> dict[str, Any]:
    """The routed model calls of the window: how many, how many failed, the median latency."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.model_gateway_record import ModelGatewayRecord

    since = datetime.now(UTC) - timedelta(hours=hours)
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    select(ModelGatewayRecord.outcome, ModelGatewayRecord.latency_ms).where(
                        ModelGatewayRecord.tenant_id == tenant_id, ModelGatewayRecord.created_at >= since
                    )
                )
            ).all()
    # enterprise-gate: broad-except-ok reason=unreadable-routing-records-are-reported-as-unknown-not-raised
    except Exception as exc:
        logger.warning("workload_model_calls_unavailable", error_type=type(exc).__name__)
        return {
            "window_hours": hours,
            "calls": None,
            "failed": None,
            "p50_latency_ms": None,
            "error": type(exc).__name__,
        }
    return {
        "window_hours": hours,
        "calls": len(rows),
        "failed": sum(1 for outcome, _latency in rows if outcome == "failed"),
        "p50_latency_ms": _median([int(latency or 0) for _outcome, latency in rows]),
        "error": None,
    }


async def guardrail_outcomes(tenant_id: uuid.UUID, *, hours: int = WINDOW_HOURS) -> dict[str, Any]:
    """Guardrail blocks and transforms of the window, from the signed audit rows."""
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.models.audit import AuditLog

    since = datetime.now(UTC) - timedelta(hours=hours)
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    select(AuditLog.outcome, func.count())
                    .where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.event_type == "guardrail.outcome",
                        AuditLog.created_at >= since,
                    )
                    .group_by(AuditLog.outcome)
                )
            ).all()
    # enterprise-gate: broad-except-ok reason=unreadable-audit-rows-are-reported-as-unknown-not-raised
    except Exception as exc:
        logger.warning("workload_guardrails_unavailable", error_type=type(exc).__name__)
        return {"window_hours": hours, "blocked": None, "transformed": None, "error": type(exc).__name__}
    counts = {str(outcome): int(count) for outcome, count in rows}
    return {
        "window_hours": hours,
        "blocked": counts.get("blocked", 0),
        "transformed": counts.get("transformed", 0),
        "error": None,
    }


async def workload(tenant_id: uuid.UUID) -> dict[str, Any]:
    """The whole picture for one tenant, part by part."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "tracing_enabled": tracing.enabled(),
        "timeline_enabled": timeline.enabled(),
        "reviews": await review_deadlines(tenant_id),
        "runs": await run_outcomes(tenant_id),
        "model_calls": await model_call_outcomes(tenant_id),
        "guardrails": await guardrail_outcomes(tenant_id),
    }
