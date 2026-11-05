# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights cost metrics: spend, spend per run, by agent and model, the month's forecast, budgets and pricing quality.

**Cost source, per day.** Each day's spend comes from one source:

1. ``spend_usage_rollups`` when AI spend intelligence is on
   (``AGENTICORG_SPEND_INTELLIGENCE_ENABLED``) and the tenant has rollup rows
   for that day: priced usage in INR (``amount_inr``);
2. otherwise ``agent_cost_ledger``: each agent's runtime-reported cost in USD
   for that UTC day.

So when metering is turned on mid-period, the earlier days come from the
ledger and the later ones from the rollups, and the total is their sum. A
response's ``source`` names what was read (``spend_usage_rollups``,
``agent_cost_ledger`` or both joined by ``+``) and its ``data_quality`` is
``mixed`` when both were.

Amounts are shown in the tenant's reporting currency. When a source's
currency differs, it is converted with the tenant's latest USD rate in
``spend_fx_rates`` and the response carries ``conversion.kind =
fx_converted``; with no rate the amounts stay in the source's currency and the
``hint`` says so. Without a rate, INR rollup days cannot be added to USD
ledger days, so every day is then read from the ledger (the hint says that
too). Cost metrics are tenant-wide for the roles that may read them (no
domain filter). A company filter keeps the spend of that company's agents.
Budgets compare ``budget_alerts`` thresholds (USD) with the ledger, exactly as
the budget evaluator does.

Cost per run divides spend by runs over the same days only: the days with run
data (from the tenant's first run on, less days left out of the run read). A
shorter coverage than the period is computed over the covered days and says
so (``mixed`` plus a hint naming them).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from core.config import settings
from core.insights.metrics import (
    TOP_ROWS,
    UNATTRIBUTED,
    MetricContext,
    Period,
    agent_names,
    bucket_start,
    bucket_starts,
    coverage,
    money,
    no_data,
    pct,
    run_data,
    totals,
)

ROLLUPS = "spend_usage_rollups"
LEDGER = "agent_cost_ledger"
TOKEN_USAGE_TYPES: tuple[str, ...] = ("llm_tokens", "embedding_tokens")
COST_GROUPS: tuple[str, ...] = ("agent",)
NATIVE: dict[str, str] = {ROLLUPS: "INR", LEDGER: "USD"}
# The forecast fits complete days only, and needs this many days with spend.
FORECAST_MIN_DAYS = 7
# While the month has fewer, the trend is fitted over this many trailing complete days.
FORECAST_TRAILING_DAYS = 14

NO_SPEND_HINT = (
    "No spend is recorded for this period. Agent runs record their cost in the cost ledger; "
    "turn on AI spend metering for priced usage by model."
)
METERING_HINT = (
    "This needs AI spend metering. Turn it on (AGENTICORG_SPEND_INTELLIGENCE_ENABLED) to price "
    "every model call and see this breakdown."
)
MIXED_SOURCE_HINT = (
    "Days with metered usage are read from spend_usage_rollups and the other days from the agent cost ledger."
)
FORECAST_DAYS_HINT = (
    "A forecast needs 7 days of spend: 7 complete days this month, or in the last 14 complete days while "
    "the month is younger."
)


def _join(*hints: str | None) -> str | None:
    parts = [hint for hint in hints if hint]
    return " ".join(parts) if parts else None


def _month_end(day: date) -> date:
    following = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


@dataclass(frozen=True)
class CostBasis:
    """The request's display currency, and whether metered rollup days may be read at all.

    ``rate`` is the tenant's USD to INR rate when one is needed and recorded.
    """

    currency: str
    rollups_on: bool
    rate: Decimal | None = None
    conversion: dict[str, Any] | None = None
    hint: str | None = None

    def shown(self, amount: Decimal, native: str) -> Decimal:
        """``amount`` in ``native`` currency, in the display currency."""
        if native == self.currency or self.rate is None:
            return amount
        return amount * self.rate if native == "USD" else amount / self.rate

    def envelope(self, source: str) -> dict[str, Any]:
        out: dict[str, Any] = {"source": source, "currency": self.currency, "unit": self.currency.lower()}
        if self.conversion:
            out["conversion"] = self.conversion
        return out


async def _rollup_days(ctx: MetricContext, start: date, end: date) -> frozenset[date]:
    """The days in ``start``..``end`` with metered rollup rows (tenant-wide); empty while metering is off."""
    from sqlalchemy import select

    from core.models.spend_usage import SpendUsageRollup as R

    if not settings.spend_intelligence_enabled:
        return frozenset()
    key = ("rollup_days", start, end)
    if key not in ctx.cache:
        rows = await ctx.session.execute(
            select(R.day).where(R.tenant_id == ctx.tenant_id, R.day >= start, R.day <= end).distinct()
        )
        ctx.cache[key] = frozenset(day for (day,) in rows)
    return ctx.cache[key]


def _ledger_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """The ledger stores naive UTC midnights, one row per agent and UTC day."""
    lo = datetime(start.year, start.month, start.day, tzinfo=UTC).replace(tzinfo=None)
    hi = datetime(end.year, end.month, end.day, tzinfo=UTC).replace(tzinfo=None) + timedelta(days=1)
    return lo, hi


async def _ledger_days(ctx: MetricContext, start: date, end: date) -> frozenset[date]:
    from sqlalchemy import Date, cast, select

    from core.models.agent import AgentCostLedger as L

    lo, hi = _ledger_bounds(start, end)
    rows = await ctx.session.execute(
        select(cast(L.period_date, Date))
        .where(L.tenant_id == ctx.tenant_id, L.period_date >= lo, L.period_date < hi)
        .distinct()
    )
    return frozenset(day for (day,) in rows)


async def _usd_rate(ctx: MetricContext) -> tuple[Decimal, date] | None:
    from sqlalchemy import select

    from core.models.spend import SpendFxRate as X

    row = (
        await ctx.session.execute(
            select(X.rate_to_inr, X.rate_date)
            .where(X.tenant_id == ctx.tenant_id, X.currency == "USD")
            .order_by(X.rate_date.desc())
            .limit(1)
        )
    ).first()
    if row is None or not row[0]:
        return None
    return Decimal(row[0]), row[1]


def _request_window(ctx: MetricContext) -> tuple[date, date]:
    """Every day a cost metric of this request may read: the period, the comparison and the forecast's window."""
    today = ctx.today
    starts = [ctx.period.start, today.replace(day=1), today - timedelta(days=FORECAST_TRAILING_DAYS)]
    if ctx.compare is not None:
        starts.append(ctx.compare.start)
    return min(starts), max(ctx.period.end, today)


async def cost_basis(ctx: MetricContext) -> CostBasis:
    """The request's display currency, decided once per request over every day it may read."""
    if "cost_basis" in ctx.cache:
        return ctx.cache["cost_basis"]
    start, end = _request_window(ctx)
    rollup_days = await _rollup_days(ctx, start, end)
    natives: set[str] = set()
    if rollup_days:
        natives.add("INR")
        if await _ledger_days(ctx, start, end) - rollup_days:
            natives.add("USD")
    else:
        natives.add("USD")
    wanted = ctx.reporting.currency
    rate = await _usd_rate(ctx) if natives - {wanted} else None
    rollups_on = bool(rollup_days)
    hint: str | None = None
    if len(natives) == 2 and rate is None:
        # INR rollup days cannot be added to USD ledger days without a rate: read the ledger for every day.
        rollups_on = False
        natives = {"USD"}
        hint = (
            "No USD to INR rate is recorded, so metered days (INR) cannot be added to cost-ledger days (USD); "
            "every day is read from the agent cost ledger."
        )
    if natives <= {wanted}:
        basis = CostBasis(currency=wanted, rollups_on=rollups_on, hint=hint)
    elif rate is not None:
        value, rate_date = rate
        basis = CostBasis(
            currency=wanted,
            rollups_on=rollups_on,
            rate=value,
            conversion={"kind": "fx_converted", "rate_to_inr": format(value, "f"), "rate_date": rate_date.isoformat()},
            hint=hint,
        )
    else:
        (native,) = natives
        basis = CostBasis(
            currency=native,
            rollups_on=rollups_on,
            hint=_join(
                hint,
                f"No USD to INR rate is recorded, so amounts are shown in {native}. "
                f"Add a USD rate under AI spend FX rates to see them in {wanted}.",
            ),
        )
    ctx.cache["cost_basis"] = basis
    return basis


def _company_agents(ctx: MetricContext) -> Any:
    from sqlalchemy import select

    from core.models.agent import Agent

    return select(Agent.id).where(Agent.tenant_id == ctx.tenant_id, Agent.company_id == ctx.company_id)


@dataclass
class Spend:
    """Spend per (day, group) in the display currency, and the sources it was read from."""

    amounts: dict[tuple[date, str], Decimal]
    sources: set[str] = field(default_factory=set)
    rollup_days: frozenset[date] = frozenset()

    @property
    def source(self) -> str:
        return "+".join(name for name in (ROLLUPS, LEDGER) if name in self.sources) or LEDGER

    @property
    def mixed(self) -> bool:
        return len(self.sources) > 1

    @property
    def quality(self) -> str:
        return "mixed" if self.mixed else "measured"

    def total(self, days: set[date] | None = None) -> Decimal:
        return sum(
            (amount for (day, _grp), amount in self.amounts.items() if days is None or day in days), Decimal("0")
        )


async def daily_spend(ctx: MetricContext, start: date, end: date, *, group: str | None = None) -> Spend:
    """Spend in the display currency per (day, group) for ``start`` to ``end``, each day from its own source."""
    from sqlalchemy import Date, String, cast, func, literal, select

    basis = await cost_basis(ctx)
    key = ("spend", start, end, group)
    if key in ctx.cache:
        return ctx.cache[key]
    rollup_days = await _rollup_days(ctx, start, end) if basis.rollups_on else frozenset()
    amounts: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    sources: set[str] = set()
    if rollup_days:
        from core.models.spend_usage import SpendUsageRollup as R

        grp = cast(R.agent_id, String) if group == "agent" else literal("")
        query = (
            select(R.day, grp.label("grp"), func.coalesce(func.sum(R.amount_inr), 0))
            .where(R.tenant_id == ctx.tenant_id, R.day >= start, R.day <= end)
            .group_by(R.day, grp)
        )
        if ctx.company_id is not None:
            query = query.where(R.agent_id.in_(_company_agents(ctx)))
        for day_value, grp_value, amount in await ctx.session.execute(query):
            amounts[(day_value, (grp_value or UNATTRIBUTED) if group else "")] += basis.shown(
                Decimal(amount or 0), NATIVE[ROLLUPS]
            )
        # A metered day is read from the rollups even when this company had no rows on it.
        sources.add(ROLLUPS)
    from core.models.agent import AgentCostLedger as L

    day = cast(L.period_date, Date)
    grp = cast(L.agent_id, String) if group == "agent" else literal("")
    lo, hi = _ledger_bounds(start, end)
    query = (
        select(day, grp.label("grp"), func.coalesce(func.sum(L.cost_usd), 0))
        .where(L.tenant_id == ctx.tenant_id, L.period_date >= lo, L.period_date < hi)
        .group_by(day, grp)
    )
    if ctx.company_id is not None:
        query = query.where(L.agent_id.in_(_company_agents(ctx)))
    for day_value, grp_value, amount in await ctx.session.execute(query):
        if day_value in rollup_days:
            continue  # this day is read from the rollups
        amounts[(day_value, (grp_value or UNATTRIBUTED) if group else "")] += basis.shown(
            Decimal(amount or 0), NATIVE[LEDGER]
        )
        sources.add(LEDGER)
    spend = Spend(amounts=dict(amounts), sources=sources, rollup_days=rollup_days)
    ctx.cache[key] = spend
    return spend


def _none(basis: CostBasis, source: str, hint: str, **extra: Any) -> dict[str, Any]:
    """``no_data`` with the request's cost source and currency."""
    return {**no_data(source, hint), **basis.envelope(source), **extra}


def _sum(values: Any) -> Decimal:
    return sum(values, Decimal("0"))


async def _labels(ctx: MetricContext, groups: Any) -> dict[str, str]:
    names = await agent_names(ctx, groups)
    return {g: names.get(g, "Unattributed" if g == UNATTRIBUTED else g) for g in groups}


def _source_hint(spend: Spend) -> str | None:
    return MIXED_SOURCE_HINT if spend.mixed else None


async def resolve_cost_spend(ctx: MetricContext) -> dict[str, Any]:
    basis = await cost_basis(ctx)
    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end, group=ctx.group_by)
    if not spend.amounts:
        return _none(basis, spend.source, basis.hint or NO_SPEND_HINT)
    out: dict[str, Any] = {
        **basis.envelope(spend.source),
        "value": money(spend.total()),
        "data_quality": spend.quality,
    }
    if ctx.compare is not None:
        previous = await daily_spend(ctx, ctx.compare.start, ctx.compare.end, group=ctx.group_by)
        out["compare_value"] = money(previous.total()) if previous.amounts else None
    buckets: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    for (day, grp), amount in spend.amounts.items():
        buckets[(bucket_start(day, ctx.period.granularity), grp)] += amount
    if ctx.group_by is None:
        out["series"] = [
            {"t": start.isoformat(), "value": money(buckets.get((start, ""), Decimal("0")))}
            for start in bucket_starts(ctx.period)
        ]
    else:
        labels = await _labels(ctx, {grp for _, grp in buckets})
        out["series"] = [
            {"t": start.isoformat(), "value": money(amount), "group": labels[grp]}
            for (start, grp), amount in sorted(buckets.items())
        ]
        per_group: dict[str, Decimal] = defaultdict(Decimal)
        for (_day, grp), amount in spend.amounts.items():
            per_group[grp] += amount
        out["rows"] = sorted(
            ({"group": labels[g], "value": money(a)} for g, a in per_group.items()),
            key=lambda r: (-(r["value"] or 0), r["group"]),
        )
    hint = _join(basis.hint, _source_hint(spend))
    if hint:
        out["hint"] = hint
    return out


@dataclass
class _PerRun:
    """Spend and runs over the same days: the covered days of a period."""

    amount: Decimal | None  # spend over the covered days; None when there is no spend or no run
    runs: int
    spend: Spend
    run_source: str
    covered: list[date]
    hint: str | None


async def _per_run(ctx: MetricContext, period: Period) -> _PerRun:
    runs = await run_data(ctx, period, domain_filter=False)
    covered, hint = coverage(ctx, period, runs)
    spend = await daily_spend(ctx, period.start, period.end)
    count = int(totals(runs.rows)["volume"])
    amount = spend.total(set(covered)) if spend.amounts and count and covered else None
    return _PerRun(amount, count, spend, runs.source, covered, hint)


async def resolve_cost_per_run(ctx: MetricContext) -> dict[str, Any]:
    basis = await cost_basis(ctx)
    current = await _per_run(ctx, ctx.period)
    amount, run_count, spend, covered, coverage_hint = (
        current.amount,
        current.runs,
        current.spend,
        current.covered,
        current.hint,
    )
    source = f"{spend.source}+{current.run_source}"
    if amount is None:
        hint = NO_SPEND_HINT if not spend.amounts else "No agent runs are recorded for this period, so no per-run cost."
        envelope = {k: v for k, v in basis.envelope(source).items() if k != "source"}
        return no_data(source, basis.hint or hint, **envelope)

    def per_run(total: Decimal, count: int) -> float | None:
        return money(total / count, 6) if count else None

    out: dict[str, Any] = {
        **basis.envelope(source),
        "value": per_run(amount, run_count),
        # A ratio over fewer days than the period, or over two cost sources, is not plainly measured.
        "data_quality": "mixed" if coverage_hint or spend.mixed else "measured",
    }
    if ctx.compare is not None:
        previous = await _per_run(ctx, ctx.compare)
        out["compare_value"] = per_run(previous.amount, previous.runs) if previous.amount is not None else None
    covered_days = set(covered)
    spend_by_bucket: dict[date, Decimal] = defaultdict(Decimal)
    for (day, _grp), value in spend.amounts.items():
        if day in covered_days:
            spend_by_bucket[bucket_start(day, ctx.period.granularity)] += value
    runs = await run_data(ctx, ctx.period, domain_filter=False)
    runs_by_bucket: dict[date, int] = defaultdict(int)
    for start, rows in _rows_by_bucket(runs.rows, ctx.period.granularity).items():
        runs_by_bucket[start] = int(totals(rows)["volume"])
    series = []
    for start in bucket_starts(ctx.period):
        value = per_run(spend_by_bucket.get(start, Decimal("0")), runs_by_bucket.get(start, 0))
        if value is not None:
            series.append({"t": start.isoformat(), "value": value})
    out["series"] = series
    hint = _join(coverage_hint, basis.hint, _source_hint(spend))
    if hint:
        out["hint"] = hint
    return out


def _rows_by_bucket(rows: list[dict[str, Any]], granularity: str) -> dict[date, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[bucket_start(row["day"], granularity)].append(row)
    return grouped


async def resolve_cost_by_agent(ctx: MetricContext) -> dict[str, Any]:
    basis = await cost_basis(ctx)
    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end, group="agent")
    if not spend.amounts:
        return _none(basis, spend.source, basis.hint or NO_SPEND_HINT)
    per_agent: dict[str, Decimal] = defaultdict(Decimal)
    for (_day, grp), amount in spend.amounts.items():
        per_agent[grp] += amount
    labels = await _labels(ctx, per_agent.keys())
    ranked = sorted(per_agent.items(), key=lambda item: (-item[1], labels[item[0]]))
    rows = [
        {
            "agent_id": None if grp == UNATTRIBUTED else grp,
            "agent_name": labels[grp],
            "value": money(amount),
        }
        for grp, amount in ranked[:TOP_ROWS]
    ]
    out: dict[str, Any] = {
        **basis.envelope(spend.source),
        "value": money(_sum(per_agent.values())),
        "rows": rows,
        "data_quality": spend.quality,
    }
    top = f"The {TOP_ROWS} agents with the highest spend are listed; the total covers all {len(ranked)}."
    hint = _join(top if len(ranked) > TOP_ROWS else None, basis.hint, _source_hint(spend))
    if hint:
        out["hint"] = hint
    return out


async def resolve_cost_by_model(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    basis = await cost_basis(ctx)
    rollup_days = await _rollup_days(ctx, ctx.period.start, ctx.period.end) if basis.rollups_on else frozenset()
    if not rollup_days:
        return _none(basis, ROLLUPS, _join(basis.hint, METERING_HINT) or METERING_HINT)
    from core.models.spend_usage import SpendUsageRollup as R

    query = (
        select(R.provider, R.model, func.coalesce(func.sum(R.amount_inr), 0))
        .where(R.tenant_id == ctx.tenant_id, R.day >= ctx.period.start, R.day <= ctx.period.end)
        .group_by(R.provider, R.model)
    )
    if ctx.company_id is not None:
        query = query.where(R.agent_id.in_(_company_agents(ctx)))
    found = [(provider, model, Decimal(amount or 0)) for provider, model, amount in await ctx.session.execute(query)]
    if not found:
        return _none(basis, ROLLUPS, basis.hint or NO_SPEND_HINT)
    found.sort(key=lambda item: (-item[2], item[0], item[1]))
    native = NATIVE[ROLLUPS]
    out: dict[str, Any] = {
        **basis.envelope(ROLLUPS),
        "value": money(basis.shown(_sum(amount for *_, amount in found), native)),
        "rows": [
            {"provider": provider, "model": model or provider, "value": money(basis.shown(amount, native))}
            for provider, model, amount in found[:TOP_ROWS]
        ],
        "data_quality": "measured",
    }
    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end)
    partial = (
        f"Only metered days ({min(rollup_days).isoformat()} on) are broken down by model; "
        "earlier days come from the agent cost ledger, which has no model."
        if spend.mixed
        else None
    )
    hint = _join(partial, basis.hint)
    if hint:
        out["hint"] = hint
    return out


async def resolve_cost_forecast(ctx: MetricContext) -> dict[str, Any]:
    """This month's spend: the complete days so far plus the trend carried to month end, with a band.

    Always ``estimated``. The trend is fitted over complete days only (today
    is still running): this month's complete days once 7 of them have spend,
    else the last 14 complete days, which must also hold 7 days of spend;
    otherwise there is no forecast (``no_data``).
    """
    from core.finops.forecast import project

    basis = await cost_basis(ctx)
    today = ctx.today
    yesterday = today - timedelta(days=1)
    month_start, month_end = today.replace(day=1), _month_end(today)
    period = Period(month_start, month_end, "day").as_dict()
    month = await daily_spend(ctx, month_start, today)
    daily: dict[date, Decimal] = defaultdict(Decimal)
    for (day, _grp), amount in month.amounts.items():
        daily[day] += amount
    complete_month = {day: amount for day, amount in daily.items() if day < today}
    if sum(1 for amount in complete_month.values() if amount > 0) >= FORECAST_MIN_DAYS:
        fit_start, fit, fit_label = month_start, complete_month, "month_to_date"
    else:
        fit_start = today - timedelta(days=FORECAST_TRAILING_DAYS)
        trailing = await daily_spend(ctx, fit_start, yesterday)
        fit = defaultdict(Decimal)
        for (day, _grp), amount in trailing.amounts.items():
            fit[day] += amount
        fit_label = "trailing_14_days"
        if sum(1 for amount in fit.values() if amount > 0) < FORECAST_MIN_DAYS:
            source = month.source if month.amounts else trailing.source
            return _none(basis, source, _join(FORECAST_DAYS_HINT, basis.hint) or FORECAST_DAYS_HINT, period=period)
    history_days = (yesterday - fit_start).days + 1
    # The rest of the month from today (still running) to its last day.
    horizon_days = (month_end - today).days + 1
    points = [(day, 0, float(amount)) for day, amount in fit.items() if fit_start <= day <= yesterday]
    projection = project(points, history_days=history_days, horizon_days=horizon_days, end=yesterday)
    complete_actual = float(_sum(complete_month.values()))
    today_actual = float(daily.get(today, Decimal("0")))
    # The rest of the month is at least what today has already spent.
    remaining = (
        max(projection["projected_cost_usd"], today_actual),
        max(projection["projected_cost_low_usd"], today_actual),
        max(projection["projected_cost_high_usd"], today_actual),
    )
    point, low, high = (round(complete_actual + part, 2) for part in remaining)
    mtd = complete_actual + today_actual
    out: dict[str, Any] = {
        **basis.envelope(month.source),
        "period": period,
        "value": point,
        "rows": [
            {
                "label": "forecast",
                "point": point,
                "low": low,
                "high": high,
                "actual": round(mtd, 2),
                "fit": fit_label,
                "fit_days": history_days,
            },
        ],
        "series": [
            {"t": day.isoformat(), "value": money(daily.get(day, Decimal("0")))}
            for day in (month_start + timedelta(days=offset) for offset in range((today - month_start).days + 1))
        ],
        "data_quality": "estimated",
    }
    hint = _join(basis.hint, _source_hint(month))
    if hint:
        out["hint"] = hint
    return out


def _budget_state(used_pct: float | None, warn_at: int) -> str:
    if used_pct is None:
        return "ok"
    if used_pct >= 100:
        return "breached"
    if used_pct >= warn_at:
        return "warn"
    return "ok"


async def budget_rows(ctx: MetricContext) -> list[dict[str, Any]]:
    """Each budget alert's threshold against the ledger's spend for its current period (as the evaluator sums it)."""
    from sqlalchemy import select

    from core.billing.budget_evaluator import _period_start
    from core.insights.budgets import spend_of
    from core.models.budget_alert import BudgetAlert

    if "budgets" in ctx.cache:
        return ctx.cache["budgets"]
    query = select(BudgetAlert).where(BudgetAlert.tenant_id == ctx.tenant_id).order_by(BudgetAlert.name)
    if ctx.company_id is not None:
        query = query.where(BudgetAlert.company_id == ctx.company_id)
    alerts = list((await ctx.session.execute(query)).scalars())
    rows: list[dict[str, Any]] = []
    for alert in alerts:
        try:
            _period_start(alert.period, ctx.now)
        except ValueError:
            continue
        actual = await spend_of(ctx.session, ctx.tenant_id, alert, ctx.now)
        threshold = Decimal(alert.threshold_usd or 0)
        used = pct(float(actual), float(threshold))
        rows.append(
            {
                "id": str(alert.id),
                "name": alert.name,
                "period": alert.period,
                "threshold": money(threshold, 2),
                "actual": money(actual, 2),
                "used_pct": used,
                "warn_at_percent": int(alert.warn_at_percent),
                "state": _budget_state(used, int(alert.warn_at_percent)),
                "currency": "USD",
                "tenant_wide": alert.company_id is None and alert.cost_center_id is None,
            }
        )
    ctx.cache["budgets"] = rows
    return rows


async def resolve_cost_budgets(ctx: MetricContext) -> dict[str, Any]:
    rows = await budget_rows(ctx)
    envelope = {"source": "budget_alerts+agent_cost_ledger", "currency": "USD", "unit": "usd"}
    if not rows:
        return no_data(
            envelope["source"],
            "No budget is set for this organisation, so spend is not tracked against a limit. "
            "An administrator or the CFO can set one under Insights, Budgets.",
            **{k: v for k, v in envelope.items() if k != "source"},
        )
    flagged = [row for row in rows if row["state"] != "ok"]
    state = "act" if any(r["state"] == "breached" for r in rows) else ("watch" if flagged else "ok")
    return {
        **envelope,
        "unit": "count",
        "value": len(flagged),
        "rows": rows,
        "state": state,
        "data_quality": "measured",
    }


async def resolve_tokens_by_unit(ctx: MetricContext) -> dict[str, Any]:
    """Metered token units when every spend day of the period is metered; otherwise the tokens runs report."""
    from sqlalchemy import func, select

    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end)
    if spend.sources == {ROLLUPS}:
        from core.models.spend_usage import SpendUsageRollup as R

        query = (
            select(R.unit, func.coalesce(func.sum(R.quantity), 0))
            .where(
                R.tenant_id == ctx.tenant_id,
                R.day >= ctx.period.start,
                R.day <= ctx.period.end,
                R.usage_type.in_(TOKEN_USAGE_TYPES),
            )
            .group_by(R.unit)
        )
        if ctx.company_id is not None:
            query = query.where(R.agent_id.in_(_company_agents(ctx)))
        found = sorted(
            ((unit, int(Decimal(q or 0))) for unit, q in await ctx.session.execute(query)), key=lambda r: -r[1]
        )
        if not found:
            return no_data(ROLLUPS, "No token usage is metered for this period.")
        return {
            "source": ROLLUPS,
            "value": sum(q for _, q in found),
            "rows": [{"unit": unit, "value": q} for unit, q in found],
            "data_quality": "measured",
        }
    runs = await run_data(ctx, ctx.period, domain_filter=False)
    t = totals(runs.rows)
    if not t["runs"]:
        return no_data(runs.source, "No agent runs are recorded for this period, so no tokens are counted.")
    _covered, coverage_hint = coverage(ctx, ctx.period, runs)
    quality = runs.quality()
    out: dict[str, Any] = {
        "source": runs.source,
        "value": int(t["tokens_total"]),
        "rows": [
            {"unit": "input", "value": int(t["tokens_in"])},
            {"unit": "output", "value": int(t["tokens_out"])},
            {"unit": "cached", "value": int(t["tokens_cached"])},
            {"unit": "total", "value": int(t["tokens_total"])},
        ],
        # Fewer days than the period are not plainly measured.
        "data_quality": "mixed" if coverage_hint and quality == "measured" else quality,
    }
    if ctx.compare is not None:
        previous = totals((await run_data(ctx, ctx.compare, domain_filter=False)).rows)
        out["compare_value"] = int(previous["tokens_total"]) if previous["runs"] else None
    split = (
        "Runs reported a total only, without an input and output split."
        if t["tokens_total"] and not (t["tokens_in"] or t["tokens_out"])
        else None
    )
    hint = _join(coverage_hint, split)
    if hint:
        out["hint"] = hint
    return out


async def resolve_cost_data_quality(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    if not settings.spend_intelligence_enabled:
        return no_data(ROLLUPS, METERING_HINT)
    from core.models.spend_usage import SpendUsageRollup as R

    names = (
        "record_count",
        "unpriced_count",
        "unconverted_count",
        "fx_estimated_count",
        "estimated_count",
        "allocated_count",
    )
    row = (
        await ctx.session.execute(
            select(*(func.coalesce(func.sum(getattr(R, name)), 0) for name in names)).where(
                R.tenant_id == ctx.tenant_id, R.day >= ctx.period.start, R.day <= ctx.period.end
            )
        )
    ).one()
    counts = {name: int(value or 0) for name, value in zip(names, row, strict=True)}
    if not counts["record_count"]:
        return no_data(ROLLUPS, "No usage is metered for this period yet.")
    priced = counts["record_count"] - counts["unpriced_count"]
    rows = [{"label": "priced", "value": priced}] + [
        {"label": name.removesuffix("_count"), "value": counts[name]} for name in names
    ]
    return {
        "source": ROLLUPS,
        "value": pct(priced, counts["record_count"]),
        "rows": rows,
        "data_quality": "mixed" if counts["fx_estimated_count"] or counts["estimated_count"] else "measured",
    }
