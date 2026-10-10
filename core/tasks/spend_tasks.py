# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence tasks (``core/spend/``): spilled usage, maintenance jobs and the daily sweeps.

``persist_usage`` writes usage events the in-process writer could not (a
busy rollup day, a database error, shutdown); it is idempotent by the
events' keys and retried with backoff on transient errors (a lock or
statement timeout and a deadlock included), each retry carrying only the
tenants not yet written. Events it finally cannot write are counted
(``spill_failed``) and recorded as gaps per tenant. ``run_job`` runs one
maintenance job. The beat tasks check the partition horizon, settle FX for
tenants with pending conversions, queue commitment recomputes, sweep the
jobs (a lost worker's job queued again, queued jobs resent), sample every
tenant's storage once a day and spread closed in-house GPU pool hours.

Every task answers ``{"skipped": "spend_intelligence_disabled"}`` while the
feature is off; the beat tasks also skip while ``spend_sweeps_enabled`` is off.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

SETTLE_LOOKBACK_DAYS = 7
PERSIST_MAX_RETRIES = 8
PERSIST_BACKOFF_MAX_S = 600
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


async def _persist(payload: list[dict[str, Any]], written: set[str] | None = None) -> dict[str, Any]:
    """Write each tenant's events in its own transaction; ``written`` collects the tenants done."""
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
        if written is not None:
            written.add(tenant_id)
        totals["written"] += result.written
        totals["duplicates"] += result.duplicates
        totals["refused"] += result.refused
        totals["skipped"] += result.skipped
    return totals


async def _record_lost(payload: list[dict[str, Any]]) -> None:
    """Count events that will not be written (``spill_failed``) and record them as gaps per tenant.

    The gaps are written at once when the database answers; otherwise they
    wait in this process's aggregator, which its writer flushes.
    """
    from core.database import get_tenant_session
    from core.spend import clock, meter, writer

    gaps: dict[str, dict[Any, int]] = {}
    for item in payload:
        try:
            event = meter.UsageEvent.from_wire(item)
        # enterprise-gate: broad-except-ok reason=an-unreadable-spilled-event-is-logged-and-counted
        except Exception as exc:
            logger.warning("spend_spilled_event_unreadable", error_type=type(exc).__name__)
            writer._count("llm_tokens", "spill_failed")
            continue
        writer._count(event.usage_type, "spill_failed")
        key = (clock.event_date_of(event.event_time), event.usage_type, "spill_failed", "")
        tenant_gaps = gaps.setdefault(event.tenant_id, {})
        tenant_gaps[key] = tenant_gaps.get(key, 0) + 1
    for tenant_id, tenant_gaps in gaps.items():
        try:
            async with get_tenant_session(uuid.UUID(tenant_id)) as session:
                await meter.upsert_gaps(session, uuid.UUID(tenant_id), tenant_gaps)
        # enterprise-gate: broad-except-ok reason=a-gap-write-failure-keeps-the-counts-for-the-writer-and-logs
        except Exception as exc:
            logger.warning("spend_spill_gap_write_failed", error_type=type(exc).__name__)
            for (day, usage_type, reason, detail), count in tenant_gaps.items():
                writer.add_gap(tenant_id, day, usage_type, reason, detail, count)
            writer.start_for_gaps()


def _backoff(retries: int) -> int:
    return int(min(PERSIST_BACKOFF_MAX_S, 2 ** max(0, retries)))


@app.task(name="core.tasks.spend_tasks.persist_usage", bind=True, max_retries=PERSIST_MAX_RETRIES)
def persist_usage(self: Any, payload: list[dict[str, Any]]) -> dict[str, Any]:
    """Write spilled usage events (idempotent by their keys).

    A transient database failure retries with backoff, carrying only the
    tenants not yet written. Events that cannot be written (retries spent, or
    any other failure) are counted and recorded as gaps before the task fails.
    """
    from core.spend.errors import retryable

    off = _off()
    if off is not None:
        return off
    items = list(payload or [])
    written: set[str] = set()
    try:
        return run_async(_persist(items, written))
    # enterprise-gate: broad-except-ok reason=a-spill-write-failure-retries-or-records-the-lost-events-as-gaps
    except Exception as exc:
        left = [item for item in items if str(item.get("tenant_id")) not in written]
        if retryable(exc) and self.request.retries < self.max_retries:
            logger.warning("spend_spill_write_retried", error_type=type(exc).__name__, events=len(left))
            raise self.retry(args=[left], exc=exc, countdown=_backoff(self.request.retries)) from exc
        logger.warning("spend_spill_write_failed", error_type=type(exc).__name__, events=len(left))
        run_async(_record_lost(left))
        raise


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
        queued += 1 if out and not out.get("merged") else 0
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
        queued += 1 if out and not out.get("merged") else 0
    return {"queued": queued}


@app.task(name="core.tasks.spend_tasks.recompute_commitments")
def recompute_commitments() -> dict[str, Any]:
    """Every 15 minutes: queue a commitment recompute for each tenant with an active commitment."""
    off = _sweeps_off()
    if off is not None:
        return off
    return run_async(_recompute_commitments())


async def _sweep_jobs() -> dict[str, Any]:
    from core.spend import jobs, tenants

    totals = {"requeued": 0, "sent": 0}
    for tenant_id in await tenants.active_tenant_ids():
        try:
            found = await jobs.sweep(tenant_id)
        # enterprise-gate: broad-except-ok reason=one-tenants-job-sweep-failure-is-logged-the-sweep-continues
        except Exception as exc:
            logger.warning("spend_job_sweep_failed", error_type=type(exc).__name__)
            continue
        totals["requeued"] += found["requeued"]
        totals["sent"] += found["sent"]
    return totals


@app.task(name="core.tasks.spend_tasks.sweep_jobs")
def sweep_jobs() -> dict[str, Any]:
    """Every 15 minutes: queue again the jobs a lost worker left running and resend queued jobs."""
    off = _sweeps_off()
    if off is not None:
        return off
    return run_async(_sweep_jobs())


@app.task(name="core.tasks.spend_tasks.sample_storage")
def sample_storage() -> dict[str, Any]:
    """Daily (23:30 IST): every active tenant's storage GB-days for the intended day, missed days filled."""
    off = _sweeps_off()
    if off is not None:
        return off
    # Imported past the guard, so a skipped run loads neither the storage module nor what it imports.
    from core.spend import storage

    return run_async(storage.sample_all_tenants())


async def _allocate_gpu_hours() -> dict[str, Any]:
    from core.spend import clock, gpu

    return await gpu.allocate_pending(now=clock.now_utc())


@app.task(name="core.tasks.spend_tasks.allocate_gpu_hours")
def allocate_gpu_hours() -> dict[str, Any]:
    """Hourly: spread each closed in-house GPU pool hour across the tenants' calls it served."""
    off = _sweeps_off()
    if off is not None:
        return off
    return run_async(_allocate_gpu_hours())
