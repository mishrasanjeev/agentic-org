# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights cost metrics: spend, spend per run, by agent and model, the month's forecast, budgets and pricing quality.

**Cost source.** One per request, named in every cost response's ``source``:

1. ``spend_usage_rollups`` when AI spend intelligence is on
   (``AGENTICORG_SPEND_INTELLIGENCE_ENABLED``) and the tenant has rollup rows
   in the period: priced usage in INR (``amount_inr``);
2. otherwise ``agent_cost_ledger``: each agent's runtime-reported cost in USD
   per UTC day.

Amounts are shown in the tenant's reporting currency. When it differs from
the source's, they are converted with the tenant's latest USD rate in
``spend_fx_rates`` and the response carries ``conversion.kind =
fx_converted``; with no rate the amounts stay in the source's currency and
the ``hint`` says so. Cost metrics are tenant-wide for the roles that may
read them (no domain filter). A company filter keeps the spend of that
company's agents. Budgets compare ``budget_alerts`` thresholds (USD) with the
ledger, exactly as the budget evaluator does.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from core.config import settings
from core.insights.metrics import (
    COSTS_LINK,
    TOP_ROWS,
    UNATTRIBUTED,
    MetricContext,
    Period,
    agent_names,
    bucket_start,
    bucket_starts,
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

NO_SPEND_HINT = (
    "No spend is recorded for this period. Agent runs record their cost in the cost ledger; "
    "turn on AI spend metering for priced usage by model."
)
METERING_HINT = (
    "This needs AI spend metering. Turn it on (AGENTICORG_SPEND_INTELLIGENCE_ENABLED) to price "
    "every model call and see this breakdown."
)


@dataclass(frozen=True)
class CostBasis:
    """Where the request's cost figures come from and how they are shown."""

    source: str
    native: str  # INR for the rollups, USD for the ledger
    currency: str  # what the response shows
    # shown amount = native amount x multiply / divide (the USD rate on one side)
    multiply: Decimal = Decimal("1")
    divide: Decimal = Decimal("1")
    conversion: dict[str, Any] | None = None
    hint: str | None = None

    def shown(self, amount: Decimal) -> Decimal:
        return amount * self.multiply / self.divide

    def envelope(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "currency": self.currency, "unit": self.currency.lower()}
        if self.conversion:
            out["conversion"] = self.conversion
        return out


async def _has_rollups(ctx: MetricContext, start: date, end: date) -> bool:
    from sqlalchemy import exists, select

    from core.models.spend_usage import SpendUsageRollup as R

    query = select(exists().where(R.tenant_id == ctx.tenant_id, R.day >= start, R.day <= end))
    return bool((await ctx.session.execute(query)).scalar())


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


async def cost_basis(ctx: MetricContext) -> CostBasis:
    """The request's cost source and currency, decided once per request."""
    if "cost_basis" in ctx.cache:
        return ctx.cache["cost_basis"]
    start = ctx.period.start if ctx.compare is None else min(ctx.period.start, ctx.compare.start)
    use_rollups = bool(settings.spend_intelligence_enabled) and await _has_rollups(ctx, start, ctx.period.end)
    source, native = (ROLLUPS, "INR") if use_rollups else (LEDGER, "USD")
    wanted = ctx.reporting.currency
    basis = CostBasis(source=source, native=native, currency=native)
    if wanted != native:
        rate = await _usd_rate(ctx)
        if rate is None:
            basis = CostBasis(
                source=source,
                native=native,
                currency=native,
                hint=(
                    f"No USD to INR rate is recorded, so amounts are shown in {native}. "
                    "Add a USD rate under AI spend FX rates to see them in "
                    f"{wanted}."
                ),
            )
        else:
            value, rate_date = rate
            basis = CostBasis(
                source=source,
                native=native,
                currency=wanted,
                multiply=value if native == "USD" else Decimal("1"),
                divide=value if native == "INR" else Decimal("1"),
                conversion={
                    "kind": "fx_converted",
                    "rate_to_inr": format(value, "f"),
                    "rate_date": rate_date.isoformat(),
                },
            )
    ctx.cache["cost_basis"] = basis
    return basis


def _company_agents(ctx: MetricContext) -> Any:
    from sqlalchemy import select

    from core.models.agent import Agent

    return select(Agent.id).where(Agent.tenant_id == ctx.tenant_id, Agent.company_id == ctx.company_id)


async def daily_spend(
    ctx: MetricContext, start: date, end: date, *, group: str | None = None
) -> dict[tuple[date, str], Decimal]:
    """Spend in the source's own currency per (day, group) for ``start`` to ``end``."""
    from sqlalchemy import Date, String, cast, func, literal, select

    basis = await cost_basis(ctx)
    key = ("spend", basis.source, start, end, group)
    if key in ctx.cache:
        return ctx.cache[key]
    if basis.source == ROLLUPS:
        from core.models.spend_usage import SpendUsageRollup as R

        grp = cast(R.agent_id, String) if group == "agent" else literal("")
        query = (
            select(R.day, grp.label("grp"), func.coalesce(func.sum(R.amount_inr), 0))
            .where(R.tenant_id == ctx.tenant_id, R.day >= start, R.day <= end)
            .group_by(R.day, grp)
        )
        if ctx.company_id is not None:
            query = query.where(R.agent_id.in_(_company_agents(ctx)))
    else:
        from core.models.agent import AgentCostLedger as L

        day = cast(L.period_date, Date)
        grp = cast(L.agent_id, String) if group == "agent" else literal("")
        # The ledger stores naive UTC midnights, one row per agent and UTC day.
        lo = datetime(start.year, start.month, start.day, tzinfo=UTC).replace(tzinfo=None)
        hi = datetime(end.year, end.month, end.day, tzinfo=UTC).replace(tzinfo=None) + timedelta(days=1)
        query = (
            select(day, grp.label("grp"), func.coalesce(func.sum(L.cost_usd), 0))
            .where(L.tenant_id == ctx.tenant_id, L.period_date >= lo, L.period_date < hi)
            .group_by(day, grp)
        )
        if ctx.company_id is not None:
            query = query.where(L.agent_id.in_(_company_agents(ctx)))
    spend: dict[tuple[date, str], Decimal] = {}
    for day_value, grp_value, amount in await ctx.session.execute(query):
        spend[(day_value, (grp_value or UNATTRIBUTED) if group else "")] = Decimal(amount or 0)
    ctx.cache[key] = spend
    return spend


def _none(basis: CostBasis, hint: str, **extra: Any) -> dict[str, Any]:
    """``no_data`` with the request's cost source and currency."""
    return {**no_data(basis.source, hint), **basis.envelope(), **extra}


def _sum(values: Any) -> Decimal:
    return sum(values, Decimal("0"))


async def _labels(ctx: MetricContext, groups: Any) -> dict[str, str]:
    names = await agent_names(ctx, groups)
    return {g: names.get(g, "Unattributed" if g == UNATTRIBUTED else g) for g in groups}


async def resolve_cost_spend(ctx: MetricContext) -> dict[str, Any]:
    basis = await cost_basis(ctx)
    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end, group=ctx.group_by)
    if not spend:
        return _none(basis, basis.hint or NO_SPEND_HINT)
    out: dict[str, Any] = {
        **basis.envelope(),
        "value": money(basis.shown(_sum(spend.values()))),
        "data_quality": "measured",
    }
    if ctx.compare is not None:
        previous = await daily_spend(ctx, ctx.compare.start, ctx.compare.end, group=ctx.group_by)
        out["compare_value"] = money(basis.shown(_sum(previous.values()))) if previous else None
    buckets: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    for (day, grp), amount in spend.items():
        buckets[(bucket_start(day, ctx.period.granularity), grp)] += amount
    if ctx.group_by is None:
        out["series"] = [
            {"t": start.isoformat(), "value": money(basis.shown(buckets.get((start, ""), Decimal("0"))))}
            for start in bucket_starts(ctx.period)
        ]
    else:
        labels = await _labels(ctx, {grp for _, grp in buckets})
        out["series"] = [
            {"t": start.isoformat(), "value": money(basis.shown(amount)), "group": labels[grp]}
            for (start, grp), amount in sorted(buckets.items())
        ]
        per_group: dict[str, Decimal] = defaultdict(Decimal)
        for (_day, grp), amount in spend.items():
            per_group[grp] += amount
        out["rows"] = sorted(
            ({"group": labels[g], "value": money(basis.shown(a))} for g, a in per_group.items()),
            key=lambda r: (-(r["value"] or 0), r["group"]),
        )
    if basis.hint:
        out["hint"] = basis.hint
    return out


async def resolve_cost_per_run(ctx: MetricContext) -> dict[str, Any]:
    basis = await cost_basis(ctx)
    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end)
    runs = await run_data(ctx, ctx.period, domain_filter=False)
    run_count = totals(runs.rows)["runs"]
    if not spend or not run_count:
        hint = NO_SPEND_HINT if not spend else "No agent runs are recorded for this period, so no per-run cost."
        envelope = {k: v for k, v in basis.envelope().items() if k != "source"}
        return no_data(f"{basis.source}+{runs.source}", basis.hint or hint, **envelope)

    def per_run(amount: Decimal, count: int) -> float | None:
        return money(basis.shown(amount) / count, 6) if count else None

    out: dict[str, Any] = {
        **basis.envelope(),
        "source": f"{basis.source}+{runs.source}",
        "value": per_run(_sum(spend.values()), run_count),
        "data_quality": "measured",
    }
    if ctx.compare is not None:
        previous_spend = await daily_spend(ctx, ctx.compare.start, ctx.compare.end)
        previous_runs = totals((await run_data(ctx, ctx.compare, domain_filter=False)).rows)["runs"]
        out["compare_value"] = per_run(_sum(previous_spend.values()), previous_runs) if previous_spend else None
    spend_by_bucket: dict[date, Decimal] = defaultdict(Decimal)
    for (day, _grp), amount in spend.items():
        spend_by_bucket[bucket_start(day, ctx.period.granularity)] += amount
    runs_by_bucket: dict[date, int] = defaultdict(int)
    for row in runs.rows:
        runs_by_bucket[bucket_start(row["day"], ctx.period.granularity)] += row["runs"]
    series = []
    for start in bucket_starts(ctx.period):
        value = per_run(spend_by_bucket.get(start, Decimal("0")), runs_by_bucket.get(start, 0))
        if value is not None:
            series.append({"t": start.isoformat(), "value": value})
    out["series"] = series
    if basis.hint:
        out["hint"] = basis.hint
    return out


async def resolve_cost_by_agent(ctx: MetricContext) -> dict[str, Any]:
    basis = await cost_basis(ctx)
    spend = await daily_spend(ctx, ctx.period.start, ctx.period.end, group="agent")
    if not spend:
        return _none(basis, basis.hint or NO_SPEND_HINT)
    per_agent: dict[str, Decimal] = defaultdict(Decimal)
    for (_day, grp), amount in spend.items():
        per_agent[grp] += amount
    labels = await _labels(ctx, per_agent.keys())
    ranked = sorted(per_agent.items(), key=lambda item: (-item[1], labels[item[0]]))
    rows = [
        {
            "agent_id": None if grp == UNATTRIBUTED else grp,
            "agent_name": labels[grp],
            "value": money(basis.shown(amount)),
        }
        for grp, amount in ranked[:TOP_ROWS]
    ]
    out: dict[str, Any] = {
        **basis.envelope(),
        "value": money(basis.shown(_sum(per_agent.values()))),
        "rows": rows,
        "data_quality": "measured",
    }
    if len(ranked) > TOP_ROWS:
        out["hint"] = f"The {TOP_ROWS} agents with the highest spend are listed; the total covers all {len(ranked)}."
    elif basis.hint:
        out["hint"] = basis.hint
    return out


async def resolve_cost_by_model(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    basis = await cost_basis(ctx)
    if basis.source != ROLLUPS:
        return _none(basis, METERING_HINT)
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
        return _none(basis, basis.hint or NO_SPEND_HINT)
    found.sort(key=lambda item: (-item[2], item[0], item[1]))
    out: dict[str, Any] = {
        **basis.envelope(),
        "value": money(basis.shown(_sum(amount for *_, amount in found))),
        "rows": [
            {"provider": provider, "model": model or provider, "value": money(basis.shown(amount))}
            for provider, model, amount in found[:TOP_ROWS]
        ],
        "data_quality": "measured",
    }
    if basis.hint:
        out["hint"] = basis.hint
    return out


def _month_end(day: date) -> date:
    following = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


async def resolve_cost_forecast(ctx: MetricContext) -> dict[str, Any]:
    """This month's spend: month to date plus the trend carried to month end, with a band. Always ``estimated``."""
    from core.finops.forecast import project

    basis = await cost_basis(ctx)
    today = ctx.today
    month_start, month_end = today.replace(day=1), _month_end(today)
    period = Period(month_start, month_end, "day").as_dict()
    spend = await daily_spend(ctx, month_start, today)
    if not spend:
        return _none(basis, basis.hint or NO_SPEND_HINT, period=period)
    daily: dict[date, Decimal] = defaultdict(Decimal)
    for (day, _grp), amount in spend.items():
        daily[day] += basis.shown(amount)
    history_days = (today - month_start).days + 1
    horizon_days = (month_end - today).days
    month_to_date = _sum(daily.values())
    points = [(day, 0, float(amount)) for day, amount in daily.items()]
    if horizon_days > 0:
        projection = project(points, history_days=history_days, horizon_days=horizon_days, end=today)
        remaining = (
            projection["projected_cost_usd"],
            projection["projected_cost_low_usd"],
            projection["projected_cost_high_usd"],
        )
    else:
        remaining = (0.0, 0.0, 0.0)
    mtd = float(month_to_date)
    point, low, high = (round(mtd + part, 2) for part in remaining)
    out: dict[str, Any] = {
        **basis.envelope(),
        "period": period,
        "value": point,
        "rows": [
            {"label": "forecast", "point": point, "low": low, "high": high, "actual": round(mtd, 2)},
        ],
        "series": [
            {"t": day.isoformat(), "value": money(daily.get(day, Decimal("0")))}
            for day in (month_start + timedelta(days=offset) for offset in range(history_days))
        ],
        "data_quality": "estimated",
    }
    if basis.hint:
        out["hint"] = basis.hint
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
    from sqlalchemy import func, select

    from core.billing.budget_evaluator import _period_start
    from core.models.agent import Agent, AgentCostLedger
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
            since = _period_start(alert.period, ctx.now)
        except ValueError:
            continue
        # The ledger stores naive UTC midnights.
        since_naive = since.replace(tzinfo=None)
        spend = select(func.coalesce(func.sum(AgentCostLedger.cost_usd), 0)).where(
            AgentCostLedger.tenant_id == ctx.tenant_id, AgentCostLedger.period_date >= since_naive
        )
        if alert.company_id is not None or alert.cost_center_id is not None:
            spend = spend.join(Agent, Agent.id == AgentCostLedger.agent_id)
            if alert.company_id is not None:
                spend = spend.where(Agent.company_id == alert.company_id)
            if alert.cost_center_id is not None:
                spend = spend.where(Agent.cost_center_id == alert.cost_center_id)
        actual = Decimal((await ctx.session.execute(spend)).scalar_one() or 0)
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
            f"No budget alert is set. Add one to track spend against a limit ({COSTS_LINK}).",
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
    from sqlalchemy import func, select

    basis = await cost_basis(ctx)
    if basis.source == ROLLUPS:
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
    out: dict[str, Any] = {
        "source": runs.source,
        "value": int(t["tokens_total"]),
        "rows": [
            {"unit": "input", "value": int(t["tokens_in"])},
            {"unit": "output", "value": int(t["tokens_out"])},
            {"unit": "cached", "value": int(t["tokens_cached"])},
            {"unit": "total", "value": int(t["tokens_total"])},
        ],
        "data_quality": runs.quality(),
    }
    if ctx.compare is not None:
        previous = totals((await run_data(ctx, ctx.compare, domain_filter=False)).rows)
        out["compare_value"] = int(previous["tokens_total"]) if previous["runs"] else None
    if t["tokens_total"] and not (t["tokens_in"] or t["tokens_out"]):
        out["hint"] = "Runs reported a total only, without an input and output split."
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
