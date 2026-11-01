# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights rollup sweeps (``core/insights/rollup.py``), each over every tenant in its own local day.

``rollup_today`` (every 15 minutes) rebuilds each tenant's local today.
``rollup_closed_days`` (hourly at :05) rolls a tenant's local yesterday once
it is 02:00 there and the day has no rollup yet or facts newer than its
rollup, and catches up the two days before it when they were never rolled.
``reconcile_yesterday`` (hourly at :35) reconciles each tenant's local
yesterday once it is 02:30 there, until the day is reconciled.

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

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

_OFF = {"skipped": "insights_disabled"}
# A closed day is rolled from this local time on, and reconciled from RECONCILE_AFTER.
CLOSED_DAY_AFTER = time(2, 0)
RECONCILE_AFTER = time(2, 30)
# Days before yesterday that are rolled when they have no rollup state yet.
CATCH_UP_DAYS = 3


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
    local_now: datetime, rolled_at: Mapping[date, datetime | None], last_fact_created: datetime | None
) -> list[date]:
    """The closed local days to roll for a tenant, oldest first.

    ``rolled_at`` maps each day that has a rollup state to when it was last
    rolled (``None`` if never). Yesterday is rolled from 02:00 local when it
    has no rollup or a fact was created after its rollup
    (``last_fact_created`` is the newest ``created_at`` among yesterday's
    facts). The days before yesterday, back to the third day ago, are rolled
    when they have no state at all.
    """
    today = local_now.date()
    yesterday = today - timedelta(days=1)
    out: list[date] = []
    for back in range(CATCH_UP_DAYS, 1, -1):
        day = today - timedelta(days=back)
        if day not in rolled_at:
            out.append(day)
    if local_now.time() >= CLOSED_DAY_AFTER:
        when = rolled_at.get(yesterday)
        if when is None or (last_fact_created is not None and when < last_fact_created):
            out.append(yesterday)
    return out


def should_reconcile(local_now: datetime, status: str | None) -> bool:
    """Whether to reconcile the tenant's local yesterday now (from 02:30 local, until reconciled)."""
    return local_now.time() >= RECONCILE_AFTER and status != "reconciled"


async def _closed_day_inputs(tenant: TenantClock, today: date) -> tuple[dict[date, datetime | None], datetime | None]:
    """The rollup states of the last ``CATCH_UP_DAYS`` local days and the newest fact of yesterday."""
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.insights.rollup import day_bounds
    from core.models.insights import InsightsRollupState as S
    from core.models.insights import RunFact as F

    yesterday = today - timedelta(days=1)
    start, end = day_bounds(yesterday, tenant.zone)
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
        last_created = (
            await session.execute(
                select(func.max(F.created_at)).where(
                    F.tenant_id == tenant.tenant_id, F.started_at >= start, F.started_at < end
                )
            )
        ).scalar_one_or_none()
    return {row[0]: row[1] for row in states}, last_created


async def _state_status(tenant: TenantClock, day: date) -> str | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.insights import InsightsRollupState as S

    async with get_tenant_session(tenant.tenant_id) as session:
        return (
            await session.execute(select(S.status).where(S.tenant_id == tenant.tenant_id, S.day == day))
        ).scalar_one_or_none()


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
    yesterday = local_now.date() - timedelta(days=1)
    if local_now.time() < RECONCILE_AFTER:
        return 0
    if not should_reconcile(local_now, await _state_status(tenant, yesterday)):
        return 0
    await reconcile_day(tenant.tenant_id, yesterday, zone=tenant.zone)
    return 1


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
    """Hourly at :35: reconcile each enabled tenant's local yesterday once it is 02:30 there."""
    off = _off()
    if off is not None:
        return off
    return run_async(_sweep("reconcile_yesterday", _reconcile))
