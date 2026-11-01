# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights metric resolvers: runs, agents and approvals (cost lives in ``core/insights/metrics_cost.py``).

Every resolver takes a :class:`MetricContext` (the tenant session, the
tenant's reporting settings, the period in tenant-local days, the optional
company and the caller's domains) and returns the parts of a metric response
it knows: ``value``, ``compare_value``, ``series``, ``rows``, ``source``,
``data_quality`` and, when there is nothing to show, a ``hint`` saying how to
get data. ``core/insights/registry.py`` wraps them in the response envelope.
Nothing here invents a number: a period with no recorded data answers
``data_quality: no_data``, never a placeholder.

Run metrics read ``insights_daily`` for the closed days the rollup has
written and ``run_facts`` for the rest of the period (today, and any recent
day not rolled yet), at most ``MAX_RAW_DAYS`` days of raw facts per read.
They are limited to the caller's domains (``None`` = every domain). Every
query runs in the caller's tenant session, under forced row-level security.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from core.insights.buckets import BUCKET_COUNT, FINITE_BOUNDS_MS, percentile_from_buckets, sum_buckets
from core.insights.reporting_settings import ReportingSettings

MAX_RANGE_DAYS = 366
MAX_RAW_DAYS = 31
STATEMENT_TIMEOUT = "5s"
GRANULARITIES: tuple[str, ...] = ("day", "week", "month")
COMPARE_MODES: tuple[str, ...] = ("previous", "none")
DEFAULT_PERIOD_DAYS = 30
TOP_ROWS = 50
NEEDS_YOU_APPROVALS = 5
PAUSED_AGENT_DAYS = 7
APPROVALS_LINK = "/dashboard/approvals"
AGENTS_LINK = "/dashboard/agents"
COSTS_LINK = "/dashboard/costs"
UNATTRIBUTED = "unattributed"

# Run statuses that are not an outcome yet: a person still has to act.
WAITING_STATUSES = frozenset({"hitl_triggered", "paused"})

# Dimensions a run metric may be grouped by (``group_by``); ``agent`` is the
# agent id, or the raw agent reference when the caller's id was not a UUID.
RUN_GROUPS: tuple[str, ...] = ("agent", "agent_type", "domain", "entrypoint", "task_type", "environment", "status")

NO_RUNS_HINT = (
    "No agent runs are recorded for this period. Runs are recorded as agents run; "
    "widen the date range or run an agent."
)


# ------------------------------------------------------------------ periods


@dataclass(frozen=True)
class Period:
    """Tenant-local days ``start`` to ``end``, both included."""

    start: date
    end: date
    granularity: str = "day"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict[str, str]:
        return {"from": self.start.isoformat(), "to": self.end.isoformat(), "granularity": self.granularity}

    def range_dict(self) -> dict[str, str]:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}

    def each_day(self) -> list[date]:
        return [self.start + timedelta(days=offset) for offset in range(self.days)]


class PeriodError(ValueError):
    """A requested period or granularity the API refuses (422)."""


def local_today(zone: ZoneInfo, now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(zone).date()


def default_granularity(days: int) -> str:
    return "day" if days <= 92 else "week"


def make_period(
    start: date | None, end: date | None, *, today: date, granularity: str | None = None
) -> Period:
    """The requested period (default: the last 30 tenant-local days, today included)."""
    end = end or (today if start is None else min(today, start + timedelta(days=DEFAULT_PERIOD_DAYS - 1)))
    start = start or end - timedelta(days=DEFAULT_PERIOD_DAYS - 1)
    if start > end:
        raise PeriodError("from must be on or before to")
    days = (end - start).days + 1
    if days > MAX_RANGE_DAYS:
        raise PeriodError(f"a period is at most {MAX_RANGE_DAYS} days")
    if granularity is not None and granularity not in GRANULARITIES:
        raise PeriodError(f"granularity is one of {', '.join(GRANULARITIES)}")
    return Period(start, end, granularity or default_granularity(days))


def previous_period(period: Period) -> Period:
    """The same number of days immediately before ``period``."""
    end = period.start - timedelta(days=1)
    return Period(end - timedelta(days=period.days - 1), end, period.granularity)


def bucket_start(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def bucket_starts(period: Period) -> list[date]:
    """Every series bucket of the period, in order."""
    seen: list[date] = []
    for day in period.each_day():
        start = bucket_start(day, period.granularity)
        if not seen or seen[-1] != start:
            seen.append(start)
    return seen


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """``[start, end)`` of the local ``day`` as UTC instants."""
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


# ------------------------------------------------------------------ context


@dataclass
class MetricContext:
    """What every resolver reads: the tenant session and the request's filters."""

    session: Any
    tenant_id: uuid.UUID
    reporting: ReportingSettings
    period: Period
    compare: Period | None = None
    company_id: uuid.UUID | None = None
    # Domains the caller's run metrics are limited to; None = every domain.
    domains: list[str] | None = None
    group_by: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    cache: dict[Any, Any] = field(default_factory=dict)

    @property
    def zone(self) -> ZoneInfo:
        return self.reporting.zone()

    @property
    def today(self) -> date:
        return local_today(self.zone, self.now)


async def apply_statement_timeout(session: Any) -> None:
    """Bound every statement of the current transaction (the Insights reads) to ``STATEMENT_TIMEOUT``."""
    from sqlalchemy import text

    await session.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": STATEMENT_TIMEOUT})


# ------------------------------------------------------------------ helpers


def money(value: Decimal | float | int | None, places: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def pct(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round(100.0 * float(numerator) / float(denominator), 1)


def quality_of(values: Iterable[str]) -> str:
    """``measured``, ``seeded`` or ``mixed`` from the data-quality labels of the rows read."""
    labels = set(values)
    if not labels:
        return "no_data"
    if labels == {"seeded"}:
        return "seeded"
    if "seeded" in labels:
        return "mixed"
    return "measured"


def no_data(source: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"value": None, "source": source, "data_quality": "no_data", "hint": hint, **extra}


async def agent_names(ctx: MetricContext, ids: Iterable[str]) -> dict[str, str]:
    """Agent names by id text, for the ids that are this tenant's agents."""
    from sqlalchemy import select

    from core.models.agent import Agent

    wanted: set[uuid.UUID] = set()
    for raw in ids:
        try:
            wanted.add(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return {}
    key = ("agent_names", frozenset(wanted))
    if key in ctx.cache:
        return ctx.cache[key]
    rows = await ctx.session.execute(
        select(Agent.id, Agent.name).where(Agent.tenant_id == ctx.tenant_id, Agent.id.in_(sorted(wanted)))
    )
    names = {str(agent_id): name for agent_id, name in rows}
    ctx.cache[key] = names
    return names


# ------------------------------------------------------------------ run aggregates

_RUN_SUMS: tuple[str, ...] = (
    "runs",
    "hitl_required",
    "outcome_validated",
    "tokens_in",
    "tokens_out",
    "tokens_cached",
    "tokens_total",
    "cost_usd",
    "duration_ms_sum",
    "duration_n",
    "confidence_sum",
    "confidence_n",
    "below_floor",
)


@dataclass
class RunData:
    """Run sums per (day, group, status, data quality) over a period, and where they came from."""

    rows: list[dict[str, Any]]
    sources: set[str]
    skipped_days: int = 0

    @property
    def source(self) -> str:
        if not self.sources:
            return "run_facts"
        return "+".join(sorted(self.sources))

    def quality(self) -> str:
        return quality_of(row["data_quality"] for row in self.rows if row["runs"])


def _group_expr(table: Any, group: str | None) -> Any:
    from sqlalchemy import String, cast, func, literal

    if group is None:
        return literal("").label("grp")
    if group == "agent":
        return func.coalesce(cast(table.agent_id, String), table.agent_ref, literal(UNATTRIBUTED)).label("grp")
    if group not in RUN_GROUPS:
        raise ValueError(f"unknown run group {group!r}")
    return func.coalesce(getattr(table, group), literal(UNATTRIBUTED)).label("grp")


def _filters(ctx: MetricContext, table: Any, *, domain_filter: bool) -> list[Any]:
    clauses = [table.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None:
        clauses.append(table.company_id == ctx.company_id)
    if domain_filter and ctx.domains is not None:
        clauses.append(table.domain.in_(list(ctx.domains)))
    return clauses


def _daily_query(ctx: MetricContext, days: list[date], group: str | None, domain_filter: bool) -> Any:
    from sqlalchemy import func, select

    from core.models.insights import InsightsDaily as D

    grp = _group_expr(D, group)
    sums = [func.coalesce(func.sum(getattr(D, name)), 0).label(name) for name in _RUN_SUMS]
    # Postgres arrays are 1-based.
    buckets = [func.coalesce(func.sum(D.latency_buckets[i + 1]), 0).label(f"b{i}") for i in range(BUCKET_COUNT)]
    return (
        select(D.day.label("day"), grp, D.status, D.data_quality, *sums, *buckets)
        .where(*_filters(ctx, D, domain_filter=domain_filter), D.day.in_(days))
        .group_by(D.day, grp, D.status, D.data_quality)
    )


def _raw_query(ctx: MetricContext, days: list[date], group: str | None, domain_filter: bool) -> Any:
    from sqlalchemy import Date, and_, cast, func, select

    from core.models.insights import RunFact as F

    zone = ctx.zone
    local_day = cast(func.timezone(ctx.reporting.timezone, F.started_at), Date).label("day")
    grp = _group_expr(F, group)
    lo = day_bounds(days[0], zone)[0]
    hi = day_bounds(days[-1], zone)[1]
    buckets: list[Any] = []
    previous: int | None = None
    for bound in FINITE_BOUNDS_MS:
        cond = F.duration_ms <= bound if previous is None else and_(F.duration_ms > previous, F.duration_ms <= bound)
        buckets.append(func.count().filter(cond).label(f"b{len(buckets)}"))
        previous = bound
    buckets.append(func.count().filter(F.duration_ms > previous).label(f"b{len(buckets)}"))
    return (
        select(
            local_day,
            grp,
            F.status,
            F.data_quality,
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
        .where(
            *_filters(ctx, F, domain_filter=domain_filter),
            # The started_at range lets the planner prune month partitions.
            F.started_at >= lo,
            F.started_at < hi,
            local_day.in_(days),
        )
        .group_by(local_day, grp, F.status, F.data_quality)
    )


def _run_row(row: Any) -> dict[str, Any]:
    data = row._mapping
    out: dict[str, Any] = {
        "day": data["day"],
        "grp": data["grp"] or "",
        "status": data["status"],
        "data_quality": data["data_quality"],
    }
    for name in _RUN_SUMS:
        value = data[name]
        out[name] = Decimal(value) if name == "cost_usd" else (float(value) if name == "confidence_sum" else int(value))
    out["buckets"] = [int(data[f"b{i}"]) for i in range(BUCKET_COUNT)]
    return out


async def run_data(
    ctx: MetricContext, period: Period, *, group: str | None = None, domain_filter: bool = True
) -> RunData:
    """Run sums for ``period``: rolled closed days from ``insights_daily``, the rest from ``run_facts``."""
    from sqlalchemy import select

    from core.models.insights import InsightsRollupState as S

    key = ("runs", period.start, period.end, group, domain_filter)
    if key in ctx.cache:
        return ctx.cache[key]
    today = ctx.today
    last = min(period.end, today)
    if period.start > last or (domain_filter and ctx.domains is not None and not ctx.domains):
        data = RunData(rows=[], sources=set())
        ctx.cache[key] = data
        return data
    rolled = {
        day
        for (day,) in await ctx.session.execute(
            select(S.day).where(S.tenant_id == ctx.tenant_id, S.day >= period.start, S.day <= last, S.day < today)
        )
    }
    days = [period.start + timedelta(days=offset) for offset in range((last - period.start).days + 1)]
    closed = [day for day in days if day in rolled]
    raw = [day for day in days if day not in rolled]
    skipped = 0
    if len(raw) > MAX_RAW_DAYS:
        skipped = len(raw) - MAX_RAW_DAYS
        raw = raw[-MAX_RAW_DAYS:]
    rows: list[dict[str, Any]] = []
    sources: set[str] = set()
    if closed:
        found = [_run_row(r) for r in await ctx.session.execute(_daily_query(ctx, closed, group, domain_filter))]
        rows.extend(found)
        sources.add("insights_daily")
    if raw:
        found = [_run_row(r) for r in await ctx.session.execute(_raw_query(ctx, raw, group, domain_filter))]
        rows.extend(found)
        sources.add("run_facts")
    data = RunData(rows=rows, sources=sources, skipped_days=skipped)
    ctx.cache[key] = data
    return data


def _skipped_hint(data: RunData) -> str | None:
    if not data.skipped_days:
        return None
    return (
        f"{data.skipped_days} older day(s) of this period are not rolled up yet and are left out; "
        "they appear once the daily rollup has run."
    )


def totals(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = dict.fromkeys(_RUN_SUMS, 0)
    out["cost_usd"] = Decimal("0")
    out["confidence_sum"] = 0.0
    out["completed"] = 0
    out["waiting"] = 0
    out["straight_through"] = 0
    bucket_rows = []
    for row in rows:
        for name in _RUN_SUMS:
            out[name] += row[name]
        if row["status"] == "completed":
            out["completed"] += row["runs"]
            out["straight_through"] += row["runs"] - row["hitl_required"]
        if row["status"] in WAITING_STATUSES:
            out["waiting"] += row["runs"]
        bucket_rows.append(row["buckets"])
    out["buckets"] = sum_buckets(bucket_rows)
    return out


def _by_bucket(rows: list[dict[str, Any]], granularity: str) -> dict[tuple[date, str], list[dict[str, Any]]]:
    grouped: dict[tuple[date, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(bucket_start(row["day"], granularity), row["grp"])].append(row)
    return grouped


def _by_group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["grp"]].append(row)
    return grouped


async def _group_labels(ctx: MetricContext, groups: Iterable[str]) -> dict[str, str]:
    groups = list(groups)
    if ctx.group_by != "agent":
        return {g: g for g in groups}
    names = await agent_names(ctx, groups)
    return {g: names.get(g, g) for g in groups}


async def _run_series(
    ctx: MetricContext, data: RunData, measure: Any, *, fill_zero: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """The series of ``measure(totals)`` per bucket (and group), and per-group rows when grouped."""
    grouped = _by_bucket(data.rows, ctx.period.granularity)
    series: list[dict[str, Any]] = []
    if ctx.group_by is None:
        for start in bucket_starts(ctx.period):
            rows = grouped.get((start, ""), [])
            if not rows and not fill_zero:
                continue
            value = measure(totals(rows))
            if value is None:
                continue
            series.append({"t": start.isoformat(), "value": value})
        return series, None
    labels = await _group_labels(ctx, {row["grp"] for row in data.rows})
    for (start, grp), rows in sorted(grouped.items()):
        value = measure(totals(rows))
        if value is None:
            continue
        series.append({"t": start.isoformat(), "value": value, "group": labels.get(grp, grp)})
    group_rows = []
    for grp, rows in _by_group(data.rows).items():
        value = measure(totals(rows))
        group_rows.append({"group": labels.get(grp, grp), "value": value})
    group_rows.sort(key=lambda r: (-(r["value"] or 0), r["group"]))
    return series, group_rows


async def _run_metric(ctx: MetricContext, measure: Any, *, fill_zero: bool, empty: Any = None) -> dict[str, Any]:
    data = await run_data(ctx, ctx.period, group=ctx.group_by)
    if not any(row["runs"] for row in data.rows):
        hint = NO_RUNS_HINT if not data.skipped_days else _skipped_hint(data)
        return no_data(data.source, hint or NO_RUNS_HINT)
    value = measure(totals(data.rows))
    out: dict[str, Any] = {"value": value, "source": data.source, "data_quality": data.quality()}
    if ctx.compare is not None:
        previous = await run_data(ctx, ctx.compare, group=ctx.group_by)
        out["compare_value"] = measure(totals(previous.rows)) if any(r["runs"] for r in previous.rows) else None
    series, group_rows = await _run_series(ctx, data, measure, fill_zero=fill_zero)
    out["series"] = series
    if group_rows is not None:
        out["rows"] = group_rows
    hint = _skipped_hint(data)
    if value is None:
        out["hint"] = empty
    elif hint:
        out["hint"] = hint
    return out


def _volume(t: dict[str, Any]) -> int:
    return int(t["runs"])


def _success_rate(t: dict[str, Any]) -> float | None:
    return pct(t["completed"], t["runs"] - t["waiting"])


def _straight_through(t: dict[str, Any]) -> float | None:
    return pct(t["straight_through"], t["runs"])


def _p95(t: dict[str, Any]) -> float | None:
    return percentile_from_buckets(t["buckets"], 0.95)


async def resolve_runs_volume(ctx: MetricContext) -> dict[str, Any]:
    return await _run_metric(ctx, _volume, fill_zero=True)


async def resolve_runs_success_rate(ctx: MetricContext) -> dict[str, Any]:
    return await _run_metric(
        ctx,
        _success_rate,
        fill_zero=False,
        empty="Every run in this period is still waiting on a person; the rate counts finished runs only.",
    )


async def resolve_runs_straight_through_rate(ctx: MetricContext) -> dict[str, Any]:
    return await _run_metric(ctx, _straight_through, fill_zero=False)


async def resolve_runs_latency(ctx: MetricContext) -> dict[str, Any]:
    out = await _run_metric(
        ctx,
        _p95,
        fill_zero=False,
        empty="No run in this period recorded its duration.",
    )
    if out.get("data_quality") == "no_data":
        return out
    data = await run_data(ctx, ctx.period, group=ctx.group_by)
    t = totals(data.rows)
    if ctx.group_by is None:
        mean = round(t["duration_ms_sum"] / t["duration_n"], 1) if t["duration_n"] else None
        out["rows"] = [
            {"label": "p50", "value": percentile_from_buckets(t["buckets"], 0.5)},
            {"label": "p95", "value": percentile_from_buckets(t["buckets"], 0.95)},
            {"label": "mean", "value": mean},
            {"label": "measured_runs", "value": t["duration_n"]},
        ]
    return out


# ------------------------------------------------------------------ agents and approvals


def _agent_scope(ctx: MetricContext, agent: Any) -> list[Any]:
    clauses = [agent.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None:
        clauses.append(agent.company_id == ctx.company_id)
    if ctx.domains is not None:
        # A caller limited to domains sees the tenant's shared agents in them only.
        clauses.append(agent.domain.in_(list(ctx.domains)))
        clauses.append(agent.visibility == "tenant")
    return clauses


async def resolve_agents_by_status(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.agent import Agent

    if ctx.domains is not None and not ctx.domains:
        return no_data("agents", "No domain is assigned to you, so no agents are counted.")
    rows = (
        await ctx.session.execute(
            select(Agent.status, func.count()).where(*_agent_scope(ctx, Agent)).group_by(Agent.status)
        )
    ).all()
    if not rows:
        return no_data("agents", "No agents yet. Create or install agents from the Agents page.")
    counted = sorted(({"status": status, "value": int(count)} for status, count in rows), key=lambda r: -r["value"])
    return {
        "value": sum(r["value"] for r in counted),
        "rows": counted,
        "source": "agents",
        "data_quality": "measured",
    }


def _approval_scope(ctx: MetricContext) -> tuple[Any, list[Any]]:
    from sqlalchemy import select

    from core.models.agent import Agent
    from core.models.hitl import HITLQueue as H

    clauses = [H.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None or ctx.domains is not None:
        clauses.append(H.agent_id.in_(select(Agent.id).where(*_agent_scope(ctx, Agent))))
    return H, clauses


async def pending_approvals(ctx: MetricContext, limit: int) -> tuple[int, list[dict[str, Any]], float | None]:
    """The number of pending approvals, the oldest ``limit`` of them, and the oldest age in seconds."""
    from sqlalchemy import func, select

    key = ("pending_approvals", limit)
    if key in ctx.cache:
        return ctx.cache[key]
    hitl, clauses = _approval_scope(ctx)
    clauses.append(hitl.status == "pending")
    count = int((await ctx.session.execute(select(func.count()).select_from(hitl).where(*clauses))).scalar_one() or 0)
    items: list[dict[str, Any]] = []
    oldest: float | None = None
    if count:
        rows = (
            await ctx.session.execute(
                select(hitl.id, hitl.title, hitl.created_at)
                .where(*clauses)
                .order_by(hitl.created_at.asc())
                .limit(limit)
            )
        ).all()
        for item_id, title, created_at in rows:
            age = max(0.0, (ctx.now - created_at).total_seconds()) if created_at else None
            if age is not None and (oldest is None or age > oldest):
                oldest = age
            items.append(
                {
                    "id": str(item_id),
                    "title": title,
                    "created_at": created_at.isoformat() if created_at else None,
                    "age_seconds": round(age) if age is not None else None,
                }
            )
    result = (count, items, oldest)
    ctx.cache[key] = result
    return result


async def resolve_approvals_pending(ctx: MetricContext) -> dict[str, Any]:
    if ctx.domains is not None and not ctx.domains:
        return no_data("hitl_queue", "No domain is assigned to you, so no approvals are counted.")
    count, items, _oldest = await pending_approvals(ctx, NEEDS_YOU_APPROVALS)
    return {"value": count, "rows": items, "source": "hitl_queue", "data_quality": "measured"}


async def _cycle_times(ctx: MetricContext, period: Period) -> tuple[int, float | None, float | None]:
    from sqlalchemy import extract, func, select

    hitl, clauses = _approval_scope(ctx)
    lo = day_bounds(period.start, ctx.zone)[0]
    hi = day_bounds(period.end, ctx.zone)[1]
    millis = extract("epoch", hitl.decision_at - hitl.created_at) * 1000
    row = (
        await ctx.session.execute(
            select(
                func.count(),
                func.percentile_cont(0.5).within_group(millis),
                func.percentile_cont(0.9).within_group(millis),
            ).where(*clauses, hitl.decision_at.is_not(None), hitl.decision_at >= lo, hitl.decision_at < hi)
        )
    ).one()
    count = int(row[0] or 0)
    p50 = round(float(row[1]), 1) if row[1] is not None else None
    p90 = round(float(row[2]), 1) if row[2] is not None else None
    return count, p50, p90


async def resolve_approvals_cycle_time(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import Date, cast, extract, func, select

    if ctx.domains is not None and not ctx.domains:
        return no_data("hitl_queue", "No domain is assigned to you, so no approvals are counted.")
    count, p50, p90 = await _cycle_times(ctx, ctx.period)
    if not count:
        return no_data("hitl_queue", "No approval was decided in this period.")
    out: dict[str, Any] = {
        "value": p50,
        "source": "hitl_queue",
        "data_quality": "measured",
        "rows": [
            {"label": "p50", "value": p50},
            {"label": "p90", "value": p90},
            {"label": "decided", "value": count},
        ],
    }
    if ctx.compare is not None:
        previous_count, previous_p50, _ = await _cycle_times(ctx, ctx.compare)
        out["compare_value"] = previous_p50 if previous_count else None
    hitl, clauses = _approval_scope(ctx)
    lo = day_bounds(ctx.period.start, ctx.zone)[0]
    hi = day_bounds(ctx.period.end, ctx.zone)[1]
    local_day = cast(func.timezone(ctx.reporting.timezone, hitl.decision_at), Date)
    millis = extract("epoch", hitl.decision_at - hitl.created_at) * 1000
    rows = (
        await ctx.session.execute(
            select(local_day, func.percentile_cont(0.5).within_group(millis))
            .where(*clauses, hitl.decision_at.is_not(None), hitl.decision_at >= lo, hitl.decision_at < hi)
            .group_by(local_day)
            .order_by(local_day)
        )
    ).all()
    # Each day's median decision time. Medians do not add up, so a week or
    # month granularity still shows the daily points.
    out["series"] = [
        {"t": day.isoformat(), "value": round(float(value), 1)} for day, value in rows if value is not None
    ]
    return out


# ------------------------------------------------------------------ needs you


async def needs_you(ctx: MetricContext) -> list[dict[str, Any]]:
    """What waits on a person: the oldest pending approvals, budgets near or over their limit, long-paused agents."""
    from sqlalchemy import select

    from core.models.agent import Agent

    items: list[dict[str, Any]] = []
    if ctx.domains is None or ctx.domains:
        _count, approvals, _oldest = await pending_approvals(ctx, NEEDS_YOU_APPROVALS)
        for approval in approvals:
            age = approval["age_seconds"] or 0
            items.append(
                {
                    "kind": "approval",
                    "title": approval["title"],
                    "age_seconds": approval["age_seconds"],
                    "link": APPROVALS_LINK,
                    "state": "act" if age >= 24 * 3600 else "watch",
                }
            )
    from core.insights.metrics_cost import budget_rows

    for budget in await budget_rows(ctx):
        if budget["state"] == "ok":
            continue
        items.append(
            {
                "kind": "budget",
                "title": budget["name"],
                "link": COSTS_LINK,
                "state": "act" if budget["state"] == "breached" else "watch",
                "detail": f"{budget['used_pct']}% of the {budget['period']} budget used",
            }
        )
    if ctx.domains is None or ctx.domains:
        cutoff = ctx.now - timedelta(days=PAUSED_AGENT_DAYS)
        paused = (
            await ctx.session.execute(
                select(Agent.id, Agent.name, Agent.updated_at)
                .where(*_agent_scope(ctx, Agent), Agent.status == "paused", Agent.updated_at <= cutoff)
                .order_by(Agent.updated_at.asc())
                .limit(TOP_ROWS)
            )
        ).all()
        for agent_id, name, updated_at in paused:
            age = max(0.0, (ctx.now - updated_at).total_seconds()) if updated_at else None
            items.append(
                {
                    "kind": "agent_paused",
                    "title": name,
                    "age_seconds": round(age) if age is not None else None,
                    "link": f"{AGENTS_LINK}/{agent_id}",
                    "state": "watch",
                }
            )
    return items
