# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights rollup sweeps (``core/insights/rollup.py``), each over every tenant in its own local day.

``rollup_today`` (every 15 minutes) rebuilds each tenant's local today.
``rollup_closed_days`` (hourly at :05) rolls, for each tenant, every one of
its last ``LATE_FACT_DAYS`` local days (yesterday from 02:00 local) whose
rollup is missing, was made before the day had closed (plus the settle time
the metrics wait for), or is older than the newest fact *created* for that
day (a late fact); and it catches up every day of the last
``CATCH_UP_DAYS`` with no rollup at all (after a reporting time zone change
cleared them, or a gap in the sweep). ``reconcile_yesterday`` (hourly at :35)
reconciles each tenant's last ``RECONCILE_DAYS`` closed local days (yesterday
from 02:30 local) that are not reconciled yet.

Every task answers ``{"skipped": "insights_disabled"}`` while
``insights_enabled`` is off, and skips a tenant that turned Insights off. One
tenant's failure is logged (without the tenant id) and the sweep goes on.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from core.insights.metrics import closed_at
from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

_OFF = {"skipped": "insights_disabled"}
# A closed day is rolled from this local time on, and reconciled from RECONCILE_AFTER.
CLOSED_DAY_AFTER = time(2, 0)
RECONCILE_AFTER = time(2, 30)
# Recent days rolled again when their rollup is older than their newest fact (or made before they closed).
LATE_FACT_DAYS = 7
# Days rolled when they have no rollup state at all, this far back (bounded).
CATCH_UP_DAYS = 31
# Closed days reconciled until they are.
RECONCILE_DAYS = 3


@dataclass(frozen=True)
class TenantClock:
    """A tenant to sweep and the zone its days are counted in."""

    tenant_id: uuid.UUID
    zone: ZoneInfo


def _off() -> dict[str, Any] | None:
    from core.config import settings

    return None if getattr(settings, "insights_enabled", False) else dict(_OFF)


def _now() -> datetime:
    """The current instant, in UTC (the one clock the tests patch)."""
    return datetime.now(UTC)


async def _tenants() -> list[TenantClock]:
    """Every tenant not deleted, with its reporting zone, read from the tenants catalogue (no RLS)."""
    from sqlalchemy import select

    from core.database import get_platform_session
    from core.insights.rollup import tenant_zone
    from core.models.tenant import Tenant

    async with get_platform_session() as session:
        rows = (
            await session.execute(
                select(Tenant.id, Tenant.data_region, Tenant.settings)
                .where(Tenant.deleted_at.is_(None))
                .order_by(Tenant.id)
            )
        ).all()
    return [TenantClock(row[0], tenant_zone(row[2] or {}, row[1])) for row in rows]


async def _enabled_for(tenant_id: uuid.UUID) -> bool:
    from core.insights.flags import insights_enabled_for

    found = insights_enabled_for(tenant_id)
    if inspect.isawaitable(found):
        found = await found
    return bool(found)


async def _sweep(task: str, per_tenant: Callable[[TenantClock, datetime], Awaitable[int]]) -> dict[str, Any]:
    """Run ``per_tenant`` for every enabled tenant; it answers how many days it acted on."""
    now = _now()
    totals = {"tenants": 0, "days": 0, "skipped": 0, "failed": 0}
    for tenant in await _tenants():
        try:
            if not await _enabled_for(tenant.tenant_id):
                totals["skipped"] += 1
                continue
            totals["days"] += await per_tenant(tenant, now)
            totals["tenants"] += 1
        # enterprise-gate: broad-except-ok reason=one-tenants-insights-rollup-failure-is-logged-the-sweep-continues
        except Exception as exc:
            totals["failed"] += 1
            logger.warning("insights_sweep_tenant_failed", task=task, error_type=type(exc).__name__)
    return totals


def closed_days_to_roll(
    local_now: datetime,
    rolled_at: Mapping[date, datetime | None],
    last_fact_created: Mapping[date, datetime],
) -> list[date]:
    """The closed local days to roll for a tenant, oldest first.

    ``local_now`` is the tenant's local time (aware, in its zone);
    ``rolled_at`` maps each day that has a rollup state to when it was last
    rolled (``None`` if never); ``last_fact_created`` maps a day to the newest
    ``created_at`` among its facts (by their local start day). A day of the
    last ``LATE_FACT_DAYS`` is rolled when it has no rollup, was never rolled,
    was rolled before it closed (``closed_at``: what the metrics wait for
    before reading the rollup), or a fact was created after its rollup. Older
    days of the last ``CATCH_UP_DAYS`` are rolled when they have no state.
    Yesterday waits for 02:00 local.
    """
    zone = local_now.tzinfo if isinstance(local_now.tzinfo, ZoneInfo) else ZoneInfo("UTC")
    today = local_now.date()
    yesterday = today - timedelta(days=1)
    out: list[date] = []
    for back in range(CATCH_UP_DAYS, 0, -1):
        day = today - timedelta(days=back)
        if day == yesterday and local_now.time() < CLOSED_DAY_AFTER:
            continue
        if day not in rolled_at:
            out.append(day)
            continue
        if back > LATE_FACT_DAYS:
            continue
        when = rolled_at[day]
        newest = last_fact_created.get(day)
        if when is None or when < closed_at(day, zone) or (newest is not None and when < newest):
            out.append(day)
    return out


def days_to_reconcile(local_now: datetime, statuses: Mapping[date, str | None]) -> list[date]:
    """The last ``RECONCILE_DAYS`` closed local days not reconciled yet, oldest first (yesterday from 02:30 local)."""
    today = local_now.date()
    out = []
    for back in range(RECONCILE_DAYS, 0, -1):
        day = today - timedelta(days=back)
        if back == 1 and local_now.time() < RECONCILE_AFTER:
            continue
        if statuses.get(day) != "reconciled":
            out.append(day)
    return out


async def _closed_day_inputs(
    tenant: TenantClock, today: date
) -> tuple[dict[date, datetime | None], dict[date, datetime]]:
    """The rollup states of the last ``CATCH_UP_DAYS`` local days and the newest fact created per recent day."""
    from sqlalchemy import Date, cast, func, select

    from core.database import get_tenant_session
    from core.insights.rollup import day_bounds
    from core.models.insights import InsightsRollupState as S
    from core.models.insights import RunFact as F

    yesterday = today - timedelta(days=1)
    start = day_bounds(today - timedelta(days=LATE_FACT_DAYS), tenant.zone)[0]
    end = day_bounds(yesterday, tenant.zone)[1]
    local_day = cast(func.timezone(tenant.zone.key, F.started_at), Date)
    async with get_tenant_session(tenant.tenant_id) as session:
        states = (
            await session.execute(
                select(S.day, S.rolled_at).where(
                    S.tenant_id == tenant.tenant_id,
                    S.day >= today - timedelta(days=CATCH_UP_DAYS),
                    S.day <= yesterday,
                )
            )
        ).all()
        # created_at, not started_at: a fact written late for an earlier day is what makes a rollup stale.
        newest = (
            await session.execute(
                select(local_day, func.max(F.created_at))
                .where(F.tenant_id == tenant.tenant_id, F.started_at >= start, F.started_at < end)
                .group_by(local_day)
            )
        ).all()
    return {row[0]: row[1] for row in states}, {row[0]: row[1] for row in newest if row[1] is not None}


async def _state_statuses(tenant: TenantClock, first: date, last: date) -> dict[date, str | None]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.insights import InsightsRollupState as S

    async with get_tenant_session(tenant.tenant_id) as session:
        rows = (
            await session.execute(
                select(S.day, S.status).where(S.tenant_id == tenant.tenant_id, S.day >= first, S.day <= last)
            )
        ).all()
    return {row[0]: row[1] for row in rows}


async def _roll_today(tenant: TenantClock, now: datetime) -> int:
    from core.insights.rollup import local_day, rollup_day

    await rollup_day(tenant.tenant_id, local_day(now, tenant.zone), zone=tenant.zone)
    return 1


async def _roll_closed(tenant: TenantClock, now: datetime) -> int:
    from core.insights.rollup import rollup_day

    local_now = now.astimezone(tenant.zone)
    rolled_at, last_created = await _closed_day_inputs(tenant, local_now.date())
    days = closed_days_to_roll(local_now, rolled_at, last_created)
    for day in days:
        await rollup_day(tenant.tenant_id, day, zone=tenant.zone)
    return len(days)


async def _reconcile(tenant: TenantClock, now: datetime) -> int:
    from core.insights.rollup import reconcile_day

    local_now = now.astimezone(tenant.zone)
    today = local_now.date()
    statuses = await _state_statuses(tenant, today - timedelta(days=RECONCILE_DAYS), today - timedelta(days=1))
    days = days_to_reconcile(local_now, statuses)
    for day in days:
        await reconcile_day(tenant.tenant_id, day, zone=tenant.zone)
    return len(days)


@app.task(name="core.tasks.insights_tasks.rollup_today")
def rollup_today() -> dict[str, Any]:
    """Every 15 minutes: rebuild each enabled tenant's local today."""
    off = _off()
    if off is not None:
        return off
    return run_async(_sweep("rollup_today", _roll_today))


@app.task(name="core.tasks.insights_tasks.rollup_closed_days")
def rollup_closed_days() -> dict[str, Any]:
    """Hourly at :05: roll each enabled tenant's closed local days that need it."""
    off = _off()
    if off is not None:
        return off
    return run_async(_sweep("rollup_closed_days", _roll_closed))


@app.task(name="core.tasks.insights_tasks.reconcile_yesterday")
def reconcile_yesterday() -> dict[str, Any]:
    """Hourly at :35: reconcile each enabled tenant's last closed local days that are not reconciled yet."""
    off = _off()
    if off is not None:
        return off
    return run_async(_sweep("reconcile_yesterday", _reconcile))


async def _evaluate_alerts(tenant: TenantClock, now: datetime) -> int:
    """Open, refresh and resolve the tenant's alerts; email new ``act`` alerts once. Answers alerts opened."""
    from core.database import get_tenant_session
    from core.insights import alerts
    from core.insights.metrics import MetricContext, apply_statement_timeout, local_today
    from core.insights.reporting_settings import load_reporting_settings

    async with get_tenant_session(tenant.tenant_id) as session:
        await apply_statement_timeout(session)
        reporting = await load_reporting_settings(session, tenant.tenant_id)
        current, previous = alerts.window(local_today(reporting.zone(), now))
        ctx = MetricContext(
            session=session,
            tenant_id=tenant.tenant_id,
            reporting=reporting,
            period=current,
            compare=previous,
            now=now,
        )
        candidates = await alerts.evaluate(ctx)
        applied = await alerts.apply(session, tenant.tenant_id, candidates, now)
    if applied.to_notify:
        await alerts.notify(tenant.tenant_id, applied.to_notify, now)
    return applied.opened


@app.task(name="core.tasks.insights_tasks.evaluate_alerts")
def evaluate_alerts() -> dict[str, Any]:
    """Every 15 minutes: each enabled tenant's Insights alerts (``days`` counts alerts opened)."""
    off = _off()
    if off is not None:
        return off
    return run_async(_sweep("evaluate_alerts", _evaluate_alerts))


@app.task(name="core.tasks.insights_tasks.build_export", acks_late=True)
def build_export(tenant_id: str, export_id: str) -> dict[str, Any]:
    """One background export (core/insights/export_jobs.py); the job records why when it cannot be made."""
    from core.insights import export_jobs

    status = run_async(export_jobs.build(uuid.UUID(tenant_id), uuid.UUID(export_id)))
    return {"export_id": export_id, "status": status}


async def _expire_exports() -> dict[str, Any]:
    from core.insights import export_jobs

    now = _now()
    totals = {"tenants": 0, "expired": 0, "failed": 0}
    # Every tenant, Insights on or off: a file is deleted on time even after Insights was turned off.
    for tenant in await _tenants():
        try:
            totals["expired"] += await export_jobs.expire(tenant.tenant_id, now)
            totals["tenants"] += 1
        # enterprise-gate: broad-except-ok reason=one-tenants-export-cleanup-failure-is-logged-the-sweep-continues
        except Exception as exc:
            totals["failed"] += 1
            logger.warning("insights_export_expiry_failed", error_type=type(exc).__name__)
    return totals


@app.task(name="core.tasks.insights_tasks.expire_exports")
def expire_exports() -> dict[str, Any]:
    """Daily: delete Insights export files past their seven days and report lost builds as failed."""
    return run_async(_expire_exports())
