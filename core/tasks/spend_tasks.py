# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence tasks (``core/spend/``): spilled usage, maintenance jobs and the daily sweeps.

``persist_usage`` writes usage events the in-process writer could not (a
busy rollup day, a database error, shutdown); it is idempotent by the
events' keys and retried with backoff on transient errors. ``run_job`` runs
one maintenance job. The beat tasks check the partition horizon, settle FX
for tenants with pending conversions, and queue commitment recomputes.

Every task answers ``{"skipped": "spend_intelligence_disabled"}`` while the
feature is off; the beat tasks also skip while ``spend_sweeps_enabled`` is off.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy.exc import InterfaceError, OperationalError

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

SETTLE_LOOKBACK_DAYS = 7
_OFF = {"skipped": "spend_intelligence_disabled"}
_SWEEPS_OFF = {"skipped": "spend_sweeps_disabled"}


def _off() -> dict[str, Any] | None:
    from core import spend

    return None if spend.enabled() else dict(_OFF)


def _sweeps_off() -> dict[str, Any] | None:
    from core.config import settings

    off = _off()
    if off is not None:
        return off
    return None if getattr(settings, "spend_sweeps_enabled", True) else dict(_SWEEPS_OFF)


async def _persist(payload: list[dict[str, Any]]) -> dict[str, Any]:
    from core.database import get_tenant_session
    from core.spend import meter

    events = [meter.UsageEvent.from_wire(item) for item in payload]
    by_tenant: dict[str, list[meter.UsageEvent]] = {}
    for event in events:
        by_tenant.setdefault(event.tenant_id, []).append(event)
    totals = {"written": 0, "duplicates": 0, "refused": 0, "skipped": 0}
    for tenant_id, tenant_events in by_tenant.items():
        tenant_uuid = uuid.UUID(tenant_id)
        async with get_tenant_session(tenant_uuid) as session:
            result = await meter.write_events(session, tenant_uuid, tenant_events, lock="wait")
        totals["written"] += result.written
        totals["duplicates"] += result.duplicates
        totals["refused"] += result.refused
        totals["skipped"] += result.skipped
    return totals


@app.task(
    name="core.tasks.spend_tasks.persist_usage",
    autoretry_for=(OperationalError, InterfaceError, OSError, TimeoutError),
    retry_backoff=True,
    max_retries=8,
)
def persist_usage(payload: list[dict[str, Any]]) -> dict[str, Any]:
    """Write spilled usage events (idempotent by their keys)."""
    off = _off()
    if off is not None:
        return off
    return run_async(_persist(list(payload or [])))


@app.task(name="core.tasks.spend_tasks.run_job")
def run_job(tenant_id: str, job_id: str) -> dict[str, Any]:
    """Run one spend maintenance job."""
    from core.spend import jobs

    off = _off()
    if off is not None:
        return off
    return run_async(jobs.run(uuid.UUID(str(tenant_id)), uuid.UUID(str(job_id))))


async def _check_partitions() -> dict[str, Any]:
    from core.spend import clock, partitions

    found = await partitions.horizon(clock.now_utc())
    if found["low"]:
        logger.warning(
            "spend_usage_partitions_horizon_low", last_month=found["last_month"], months_ahead=found["months_ahead"]
        )
    return found


@app.task(name="core.tasks.spend_tasks.check_partitions")
def check_partitions() -> dict[str, Any]:
    """Daily: warn when fewer than six named months of usage partitions remain."""
    off = _sweeps_off()
    if off is not None:
        return off
    return run_async(_check_partitions())


async def _tenants_with_pending_fx(start: Any, end: Any) -> list[uuid.UUID]:
    from sqlalchemy import or_, select

    from core.database import get_tenant_session
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import tenants

    out = []
    for tenant_id in await tenants.active_tenant_ids():
        try:
            async with get_tenant_session(tenant_id) as session:
                found = (
                    await session.execute(
                        select(R.id)
                        .where(
                            R.tenant_id == tenant_id,
                            R.event_time >= start,
                            R.event_time < end,
                            or_(R.fx_estimated, R.unconverted),
                        )
                        .limit(1)
                    )
                ).all()
        # enterprise-gate: broad-except-ok reason=one-tenants-settlement-check-failure-is-logged-the-sweep-continues
        except Exception as exc:
            logger.warning("spend_settle_check_failed", error_type=type(exc).__name__)
            continue
        if found:
            out.append(tenant_id)
    return out


async def _settle_fx_daily() -> dict[str, Any]:
    from core.spend import clock, jobs

    now = clock.now_utc()
    today = clock.today_in(clock.reporting_zone(), now)
    first = today - timedelta(days=SETTLE_LOOKBACK_DAYS)
    start = clock.day_bounds(first, clock.reporting_zone())[0]
    end = clock.day_bounds(today, clock.reporting_zone())[1]
    queued = 0
    for tenant_id in await _tenants_with_pending_fx(start, end):
        out = await jobs.enqueue_followup(
            tenant_id,
            kind="settle_fx",
            params={"start": first.isoformat(), "end": today.isoformat(), "force_dates": []},
            actor=jobs.SYSTEM_ACTOR,
        )
        queued += 1 if out and not out.get("coalesced") else 0
    return {"queued": queued}


@app.task(name="core.tasks.spend_tasks.settle_fx_daily")
def settle_fx_daily() -> dict[str, Any]:
    """Daily: settle the last seven days' pending FX conversions of every tenant that has any."""
    off = _sweeps_off()
    if off is not None:
        return off
    return run_async(_settle_fx_daily())


async def _recompute_commitments() -> dict[str, Any]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.spend import SpendCommitment as C
    from core.spend import jobs, tenants

    queued = 0
    for tenant_id in await tenants.active_tenant_ids():
        try:
            async with get_tenant_session(tenant_id) as session:
                found = (
                    await session.execute(select(C.id).where(C.tenant_id == tenant_id, C.status == "active").limit(1))
                ).all()
        # enterprise-gate: broad-except-ok reason=one-tenants-commitment-check-failure-is-logged-the-sweep-continues
        except Exception as exc:
            logger.warning("spend_recompute_check_failed", error_type=type(exc).__name__)
            continue
        if not found:
            continue
        out = await jobs.enqueue_followup(tenant_id, kind="recompute_commitments", params={}, actor=jobs.SYSTEM_ACTOR)
        queued += 1 if out and not out.get("coalesced") else 0
    return {"queued": queued}


@app.task(name="core.tasks.spend_tasks.recompute_commitments")
def recompute_commitments() -> dict[str, Any]:
    """Every 15 minutes: queue a commitment recompute for each tenant with an active commitment."""
    off = _sweeps_off()
    if off is not None:
        return off
    return run_async(_recompute_commitments())
