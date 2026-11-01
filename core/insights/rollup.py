# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Daily rollups of ``run_facts`` into ``insights_daily``, and their reconciliation.

A tenant's day is its **local** day, in the zone of its reporting settings
(``tenants.settings["reporting"]["timezone"]``; ``Asia/Kolkata`` for an IN
tenant and UTC otherwise when unset). ``rollup_day`` rebuilds one tenant-day:
under a transaction advisory lock on (tenant, day) it deletes the day's
``insights_daily`` rows and re-aggregates the facts whose ``started_at`` falls
in the day's UTC bounds (a range on the partition key, so only the month
partitions the day touches are read), one row per dimension combination, and
marks the day ``rolled`` in ``insights_rollup_state``. Running it again gives
the same rows.

``reconcile_day`` compares the run, token and cost totals of the raw facts
with the rolled rows in one snapshot. Counts must match exactly and cost
within 0.01%; otherwise the day is rolled once more and compared again, and a
day still off is marked ``mismatch`` and logged. Logs and the mismatch
counter never carry a tenant id.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from prometheus_client import Counter

from core.insights.buckets import BUCKET_COUNT, FINITE_BOUNDS_MS

logger = structlog.get_logger()

# The dimension columns of an insights_daily row, in hashing order.
DIMENSIONS: tuple[str, ...] = (
    "company_id",
    "agent_id",
    "agent_ref",
    "agent_type",
    "domain",
    "entrypoint",
    "task_type",
    "status",
    "environment",
    "data_quality",
)
DEFAULT_ZONE_IN = "Asia/Kolkata"
DEFAULT_ZONE = "UTC"
# Cost may differ by at most this fraction between the facts and the rollup.
COST_TOLERANCE = Decimal("0.0001")
DETAIL_MAX = 200
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"

reconcile_mismatch_total = Counter(
    "agenticorg_insights_reconcile_mismatch_total",
    "Tenant-days whose insights rollup still disagreed with the run facts after a re-roll",
)


def _zone_or_none(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def default_zone_name(data_region: str | None) -> str:
    """``Asia/Kolkata`` for an IN tenant, UTC for every other region."""
    return DEFAULT_ZONE_IN if str(data_region or "").strip().upper() == "IN" else DEFAULT_ZONE


def tenant_zone(tenant_settings: Any, data_region: str | None = None) -> ZoneInfo:
    """The zone a tenant's days are counted in.

    ``tenant_settings`` is the tenant's ``settings`` JSON (its
    ``["reporting"]["timezone"]`` is read) or a ``ReportingSettings`` (its
    ``timezone``). An unset or unknown zone falls back to the region default.
    """
    name: Any = None
    if isinstance(tenant_settings, Mapping):
        reporting = tenant_settings.get("reporting")
        if isinstance(reporting, Mapping):
            name = reporting.get("timezone")
    elif tenant_settings is not None:
        name = getattr(tenant_settings, "timezone", None)
    zone = _zone_or_none(name)
    if zone is None and name:
        logger.warning("insights_reporting_timezone_invalid")
    return zone or ZoneInfo(default_zone_name(data_region))


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """``[start, end)`` of the local ``day`` in ``zone``, as UTC instants (23 or 25 hours on a DST change)."""
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_day(moment: datetime, zone: ZoneInfo) -> date:
    """The local date of ``moment`` in ``zone`` (a naive moment is read as UTC)."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(zone).date()


def _hash_value(value: Any) -> str | None:
    return None if value is None else str(value)


def dims_hash(dims: Mapping[str, Any] | Sequence[Any]) -> str:
    """sha256 hex of the dimension tuple, in ``DIMENSIONS`` order; ``None`` and ``"None"`` hash apart."""
    if isinstance(dims, Mapping):
        values = [_hash_value(dims.get(name)) for name in DIMENSIONS]
    else:
        if len(dims) != len(DIMENSIONS):
            raise ValueError(f"a dimension tuple has {len(DIMENSIONS)} values, not {len(dims)}")
        values = [_hash_value(value) for value in dims]
    encoded = json.dumps(values, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _zone_for(session: Any, tenant_id: uuid.UUID) -> ZoneInfo:
    from core.insights.reporting_settings import load_reporting_settings

    found = load_reporting_settings(session, tenant_id)
    if inspect.isawaitable(found):
        found = await found
    return tenant_zone(found)


async def _lock(session: Any, tenant_id: uuid.UUID, day: date) -> None:
    """Serialise rollups and reconciliations of one tenant-day; held until the transaction ends."""
    from sqlalchemy import text

    await session.execute(text(_LOCK), {"key": f"insights_rollup:{tenant_id}:{day.isoformat()}"})


def _aggregate_query(tenant_id: uuid.UUID, start: datetime, end: datetime) -> Any:
    from sqlalchemy import and_, func, select

    from core.models.insights import RunFact as F

    dims = [getattr(F, name) for name in DIMENSIONS]
    buckets = []
    previous: int | None = None
    for bound in FINITE_BOUNDS_MS:
        # Inclusive upper bounds; a negative duration lands in the first bucket.
        cond = F.duration_ms <= bound if previous is None else and_(F.duration_ms > previous, F.duration_ms <= bound)
        buckets.append(func.count().filter(cond).label(f"b{len(buckets)}"))
        previous = bound
    # The open-ended last bucket.
    buckets.append(func.count().filter(F.duration_ms > previous).label(f"b{len(buckets)}"))
    return (
        select(
            *dims,
            func.count().label("runs"),
            func.count().filter(F.hitl_required.is_(True)).label("hitl_required"),
            func.count().filter(F.outcome_validated.is_(True)).label("outcome_validated"),
            func.coalesce(func.sum(F.tokens_in), 0).label("tokens_in"),
            func.coalesce(func.sum(F.tokens_out), 0).label("tokens_out"),
            func.coalesce(func.sum(F.tokens_cached), 0).label("tokens_cached"),
            func.coalesce(func.sum(F.tokens_total), 0).label("tokens_total"),
            func.coalesce(func.sum(F.cost_usd), 0).label("cost_usd"),
            func.coalesce(func.sum(F.duration_ms), 0).label("duration_ms_sum"),
            func.count(F.duration_ms).label("duration_n"),
            func.coalesce(func.sum(F.confidence), 0.0).label("confidence_sum"),
            func.count(F.confidence).label("confidence_n"),
            func.count().filter(F.confidence < F.confidence_floor).label("below_floor"),
            *buckets,
        )
        .where(F.tenant_id == tenant_id, F.started_at >= start, F.started_at < end)
        .group_by(*dims)
    )


def _daily_row(tenant_id: uuid.UUID, day: date, row: Any) -> dict[str, Any]:
    data = row._mapping
    dims = {name: data[name] for name in DIMENSIONS}
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "day": day,
        "dims_hash": dims_hash(dims),
        **dims,
        "runs": int(data["runs"]),
        "hitl_required": int(data["hitl_required"]),
        "outcome_validated": int(data["outcome_validated"]),
        "tokens_in": int(data["tokens_in"]),
        "tokens_out": int(data["tokens_out"]),
        "tokens_cached": int(data["tokens_cached"]),
        "tokens_total": int(data["tokens_total"]),
        "cost_usd": Decimal(data["cost_usd"]),
        "duration_ms_sum": int(data["duration_ms_sum"]),
        "duration_n": int(data["duration_n"]),
        "latency_buckets": [int(data[f"b{index}"]) for index in range(BUCKET_COUNT)],
        "confidence_sum": float(data["confidence_sum"]),
        "confidence_n": int(data["confidence_n"]),
        "below_floor": int(data["below_floor"]),
    }


async def _upsert_state(session: Any, tenant_id: uuid.UUID, day: date, values: dict[str, Any]) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.models.insights import InsightsRollupState as S

    stmt = pg_insert(S).values(id=uuid.uuid4(), tenant_id=tenant_id, day=day, **values)
    stmt = stmt.on_conflict_do_update(index_elements=[S.tenant_id, S.day], set_=values)
    await session.execute(stmt)


async def rollup_day(tenant_id: uuid.UUID, day: date, *, zone: ZoneInfo | None = None) -> dict[str, Any]:
    """Rebuild the tenant's ``insights_daily`` rows for its local ``day``; idempotent.

    ``zone`` is the tenant's reporting zone when the caller already resolved
    it (a sweep that chose ``day`` in it); otherwise it is read from the
    tenant's reporting settings.
    """
    from sqlalchemy import delete, func, insert

    from core.database import get_tenant_session
    from core.models.insights import InsightsDaily as D

    tenant_id = uuid.UUID(str(tenant_id))
    async with get_tenant_session(tenant_id) as session:
        if zone is None:
            zone = await _zone_for(session, tenant_id)
        start, end = day_bounds(day, zone)
        await _lock(session, tenant_id, day)
        await session.execute(delete(D).where(D.tenant_id == tenant_id, D.day == day))
        found = await session.execute(_aggregate_query(tenant_id, start, end))
        rows = [_daily_row(tenant_id, day, row) for row in found]
        if rows:
            await session.execute(insert(D), rows)
        runs = sum(row["runs"] for row in rows)
        tokens = sum(row["tokens_total"] for row in rows)
        cost = sum((row["cost_usd"] for row in rows), Decimal("0"))
        # rolled_at is the transaction's start, before the facts were read: a
        # fact created after it makes the closed-day sweep roll the day again.
        await _upsert_state(
            session,
            tenant_id,
            day,
            {
                "status": "rolled",
                "rolled_at": func.now(),
                "reconciled_at": None,
                "source_runs": runs,
                "rolled_runs": runs,
                "source_tokens": tokens,
                "rolled_tokens": tokens,
                "source_cost_usd": cost,
                "rolled_cost_usd": cost,
                "detail": None,
            },
        )
    return {"day": day.isoformat(), "rows": len(rows), "runs": runs, "tokens_total": tokens, "cost_usd": str(cost)}


@dataclass(frozen=True)
class Totals:
    """Run, token and cost totals of one tenant-day, from the raw facts and from the rollup."""

    source_runs: int
    source_tokens: int
    source_cost_usd: Decimal
    rolled_runs: int
    rolled_tokens: int
    rolled_cost_usd: Decimal

    def differences(self) -> list[str]:
        """What disagrees, as short text; empty when the totals reconcile."""
        out = []
        if self.source_runs != self.rolled_runs:
            out.append(f"runs {self.source_runs}!={self.rolled_runs}")
        if self.source_tokens != self.rolled_tokens:
            out.append(f"tokens {self.source_tokens}!={self.rolled_tokens}")
        if not cost_within_tolerance(self.source_cost_usd, self.rolled_cost_usd):
            out.append(f"cost_usd {self.source_cost_usd}!={self.rolled_cost_usd}")
        return out


def cost_within_tolerance(source: Decimal, rolled: Decimal) -> bool:
    """Whether two cost totals agree within 0.01% of the larger."""
    source, rolled = Decimal(source), Decimal(rolled)
    return abs(source - rolled) <= max(abs(source), abs(rolled)) * COST_TOLERANCE


async def _totals(session: Any, tenant_id: uuid.UUID, day: date, start: datetime, end: datetime) -> Totals:
    """Both sides' totals in one statement, so one snapshot answers both."""
    from sqlalchemy import func, select, true

    from core.models.insights import InsightsDaily as D
    from core.models.insights import RunFact as F

    raw = (
        select(
            func.count().label("runs"),
            func.coalesce(func.sum(F.tokens_total), 0).label("tokens"),
            func.coalesce(func.sum(F.cost_usd), 0).label("cost"),
        )
        .where(F.tenant_id == tenant_id, F.started_at >= start, F.started_at < end)
        .subquery()
    )
    rolled = (
        select(
            func.coalesce(func.sum(D.runs), 0).label("runs"),
            func.coalesce(func.sum(D.tokens_total), 0).label("tokens"),
            func.coalesce(func.sum(D.cost_usd), 0).label("cost"),
        )
        .where(D.tenant_id == tenant_id, D.day == day)
        .subquery()
    )
    both = select(raw.c.runs, raw.c.tokens, raw.c.cost, rolled.c.runs, rolled.c.tokens, rolled.c.cost).select_from(
        raw.join(rolled, true())
    )
    row = (await session.execute(both)).one()
    return Totals(
        source_runs=int(row[0]),
        source_tokens=int(row[1]),
        source_cost_usd=Decimal(row[2]),
        rolled_runs=int(row[3]),
        rolled_tokens=int(row[4]),
        rolled_cost_usd=Decimal(row[5]),
    )


async def _compare(tenant_id: uuid.UUID, day: date, zone: ZoneInfo | None, *, final: bool) -> tuple[Totals, ZoneInfo]:
    """Compare under the tenant-day lock; mark ``reconciled`` when it agrees (or ``mismatch`` when ``final``)."""
    from sqlalchemy import func

    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        if zone is None:
            zone = await _zone_for(session, tenant_id)
        start, end = day_bounds(day, zone)
        await _lock(session, tenant_id, day)
        totals = await _totals(session, tenant_id, day, start, end)
        differences = totals.differences()
        if differences and not final:
            return totals, zone
        values: dict[str, Any] = {
            "source_runs": totals.source_runs,
            "rolled_runs": totals.rolled_runs,
            "source_tokens": totals.source_tokens,
            "rolled_tokens": totals.rolled_tokens,
            "source_cost_usd": totals.source_cost_usd,
            "rolled_cost_usd": totals.rolled_cost_usd,
        }
        if differences:
            values.update(status="mismatch", reconciled_at=None, detail="; ".join(differences)[:DETAIL_MAX])
        else:
            values.update(status="reconciled", reconciled_at=func.now(), detail=None)
        await _upsert_state(session, tenant_id, day, values)
    return totals, zone


async def reconcile_day(tenant_id: uuid.UUID, day: date, *, zone: ZoneInfo | None = None) -> dict[str, Any]:
    """Check the tenant-day's rollup against its facts; re-roll once if it is off.

    Answers ``{"day", "status": "reconciled" | "mismatch", "rerolled"}`` (and
    ``"detail"`` on a mismatch).
    """
    tenant_id = uuid.UUID(str(tenant_id))
    totals, zone = await _compare(tenant_id, day, zone, final=False)
    if not totals.differences():
        return {"day": day.isoformat(), "status": "reconciled", "rerolled": False}
    await rollup_day(tenant_id, day, zone=zone)
    totals, zone = await _compare(tenant_id, day, zone, final=True)
    differences = totals.differences()
    if not differences:
        return {"day": day.isoformat(), "status": "reconciled", "rerolled": True}
    detail = "; ".join(differences)[:DETAIL_MAX]
    reconcile_mismatch_total.inc()
    logger.warning("insights_reconcile_mismatch", day=day.isoformat(), detail=detail)
    return {"day": day.isoformat(), "status": "mismatch", "rerolled": True, "detail": detail}
