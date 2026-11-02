# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Money saved: an estimate from measured task counts and tenant-owned baselines and rates.

The unit is a completed task of an agent type. Counts come from the runs the
rollup reads (``insights_daily``, and ``run_facts`` for days not rolled up),
measured runs only: demo-seeded runs never produce savings. Every agent run
that completed is one task, workflow steps included; an approval resuming a
paused run is not a second task (it is the same run), and a failed run is
charged once in the error cost. For agent type *t*::

    hours_saved_t = completed_t × baseline_minutes_t / 60
    gross         = Σ hours_saved_t × rate(role_t)
    error_cost    = Σ failed_t × baseline_minutes_t / 60 × rate(role_t)
    review_cost   = Σ min(decision_at − first_viewed_at, 30 min) × rate(reviewer)   (5 min when never opened)
    net           = gross − spend − review_cost − error_cost
    ROI           = net / spend, only when spend reaches the floor

A baseline names minutes per task and the role that would do it by hand
(``value_baselines``; ``task_type`` ``*`` covers every task of the type, a
specific one wins); a rate is that role's loaded hourly cost
(``value_rates``). Both start as seeded defaults with their source, and every
figure stays hidden until a tenant administrator has confirmed each baseline
and rate the period uses (``confirmed_at``); until then the response carries
the counts and the hint to confirm. Agent types with no baseline are listed as
unpriced and never valued. Spend is the period's spend from its one cost
source (as ``cost.spend``), in the reporting currency; a rate in the other
currency is converted with the tenant's latest USD rate (``fx_estimated``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from core.insights.metrics import AGENT_TASK_GROUP, MetricContext, Period, day_bounds, money, run_data, totals

# The review time of one decided approval, capped; and assumed when it was never opened.
REVIEW_CAP = timedelta(minutes=30)
REVIEW_UNOPENED = timedelta(minutes=5)
REVIEWER_ROLE = "reviewer"
ANY_TASK = "*"
# ROI is shown only at or above this period spend (or the tenant's own floor).
ROI_FLOOR: dict[str, Decimal] = {"INR": Decimal("5000"), "USD": Decimal("60")}
ROI_FLOOR_KEY = "insights_value_roi_floor"
VALUE_LINK = "/dashboard/insights/value"
CONFIRM_HINT = (
    "Savings are an estimate from baseline minutes per task and hourly rates. A tenant administrator confirms "
    "them once on the Money saved page; until then the figures stay hidden."
)

# Minutes of human work one completed task replaces, and who would do it (owner decision 2026-11-02).
SEED_BASELINES: dict[str, tuple[int, str]] = {
    "ap_processor": (12, "finance_associate"),
    "ar_collections": (10, "finance_associate"),
    "recon_agent": (45, "accountant"),
    "tax_compliance": (45, "accountant"),
    "close_agent": (60, "accountant"),
    "fpa_agent": (60, "financial_analyst"),
    "compliance_guard": (30, "compliance_associate"),
    "contract_intelligence": (45, "legal_associate"),
    "legal_ops": (30, "legal_associate"),
    "risk_sentinel": (20, "compliance_associate"),
    "vendor_manager": (20, "operations_associate"),
    "facilities_agent": (10, "operations_associate"),
    "it_operations": (15, "it_engineer"),
    "support_triage": (6, "support_associate"),
    "support_deflector": (8, "support_associate"),
    "onboarding_agent": (45, "hr_associate"),
    "offboarding_agent": (40, "hr_associate"),
    "payroll_engine": (30, "hr_associate"),
    "talent_acquisition": (20, "recruiter"),
    "performance_coach": (20, "hr_associate"),
    "ld_coordinator": (15, "hr_associate"),
    "content_factory": (60, "marketing_associate"),
    "seo_strategist": (30, "marketing_associate"),
    "social_media": (15, "marketing_associate"),
    "campaign_pilot": (30, "marketing_associate"),
    "abm": (20, "marketing_associate"),
    "crm_intelligence": (15, "sales_associate"),
    "brand_monitor": (15, "marketing_associate"),
    "competitive_intel": (30, "marketing_analyst"),
}


@dataclass(frozen=True)
class SeedRate:
    hourly_cost: Decimal
    citation: str


# Loaded hourly cost per role by reporting currency, each with its source (core/insights/value_rates.py).
def seed_rates(currency: str) -> dict[str, SeedRate]:
    from core.insights.value_rates import IN_RATES, US_RATES

    return IN_RATES if currency == "INR" else US_RATES


# ------------------------------------------------------------------ settings


async def ensure_seeded(session: Any, tenant_id: uuid.UUID, currency: str) -> None:
    """Write the seeded baselines and rates a tenant does not have yet (never overwrites a row)."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from core.models.insights import ValueBaseline, ValueRate

    have_types = {
        row[0]
        for row in await session.execute(
            select(ValueBaseline.agent_type).where(
                ValueBaseline.tenant_id == tenant_id, ValueBaseline.task_type == ANY_TASK
            )
        )
    }
    missing = [(t, m, r) for t, (m, r) in SEED_BASELINES.items() if t not in have_types]
    if missing:
        await session.execute(
            insert(ValueBaseline)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "agent_type": agent_type,
                        "task_type": ANY_TASK,
                        "baseline_minutes": Decimal(minutes),
                        "role": role,
                        "source": "seeded",
                    }
                    for agent_type, minutes, role in missing
                ]
            )
            .on_conflict_do_nothing(constraint="uq_value_baselines_tenant_type_task")
        )
    have_roles = {
        row[0] for row in await session.execute(select(ValueRate.role).where(ValueRate.tenant_id == tenant_id))
    }
    rates = seed_rates(currency)
    new_rates = [(role, seed) for role, seed in rates.items() if role not in have_roles]
    if new_rates:
        await session.execute(
            insert(ValueRate)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "role": role,
                        "hourly_cost": seed.hourly_cost,
                        "currency": currency if currency in ("INR", "USD") else "USD",
                        "source": "seeded",
                        "citation": seed.citation,
                    }
                    for role, seed in new_rates
                ]
            )
            .on_conflict_do_nothing(constraint="uq_value_rates_tenant_role")
        )


@dataclass
class Assumptions:
    baselines: list[Any]
    rates: dict[str, Any]
    roi_floor: Decimal
    roi_floor_custom: bool

    def baseline_for(self, agent_type: str, task_type: str | None) -> Any | None:
        specific = None
        general = None
        for b in self.baselines:
            if b.agent_type != agent_type:
                continue
            if task_type and b.task_type == task_type:
                specific = b
            elif b.task_type == ANY_TASK:
                general = b
        return specific or general


async def load_assumptions(session: Any, tenant_id: uuid.UUID, currency: str) -> Assumptions:
    """The tenant's baselines and rates (seeding the defaults it lacks) and its ROI floor."""
    from sqlalchemy import select

    from core.models.insights import ValueBaseline, ValueRate
    from core.models.tenant import Tenant

    await ensure_seeded(session, tenant_id, currency)
    baselines = list(
        (
            await session.execute(
                select(ValueBaseline)
                .where(ValueBaseline.tenant_id == tenant_id)
                .order_by(ValueBaseline.agent_type, ValueBaseline.task_type)
            )
        ).scalars()
    )
    rates = {
        r.role: r
        for r in (
            await session.execute(select(ValueRate).where(ValueRate.tenant_id == tenant_id).order_by(ValueRate.role))
        ).scalars()
    }
    settings = (await session.execute(select(Tenant.settings).where(Tenant.id == tenant_id))).scalar_one_or_none()
    floor = ROI_FLOOR.get(currency, ROI_FLOOR["USD"])
    custom = False
    raw = (settings or {}).get(ROI_FLOOR_KEY) if isinstance(settings, dict) else None
    try:
        if raw is not None and Decimal(str(raw)) >= 0:
            floor, custom = Decimal(str(raw)), True
    except ArithmeticError:
        pass
    return Assumptions(baselines=baselines, rates=rates, roi_floor=floor, roi_floor_custom=custom)


# ------------------------------------------------------------------ the estimate


@dataclass
class ValueLine:
    agent_type: str
    task_type: str
    completed: int
    failed: int
    baseline_id: str | None = None
    baseline_minutes: Decimal | None = None
    role: str | None = None
    rate: Decimal | None = None
    rate_id: str | None = None
    baseline_confirmed: bool = False
    rate_confirmed: bool = False
    baseline_source: str | None = None
    hours_saved: Decimal = Decimal("0")
    gross: Decimal | None = None
    error_cost: Decimal | None = None

    @property
    def priced(self) -> bool:
        return self.baseline_minutes is not None and self.rate is not None


@dataclass
class ValueEstimate:
    currency: str
    lines: list[ValueLine]
    spend: Decimal | None
    spend_source: str
    review_hours: Decimal
    review_items: int
    reviewer_rate: Decimal | None
    reviewer_confirmed: bool
    roi_floor: Decimal
    reviewer_rate_id: str | None = None
    fx_estimated: bool = False
    hints: list[str] = field(default_factory=list)

    @property
    def priced(self) -> list[ValueLine]:
        return [line for line in self.lines if line.priced and line.completed + line.failed > 0]

    @property
    def unpriced(self) -> list[ValueLine]:
        return [line for line in self.lines if not line.priced and line.completed + line.failed > 0]

    @property
    def hours_saved(self) -> Decimal:
        return sum((line.hours_saved for line in self.priced), Decimal("0"))

    @property
    def gross(self) -> Decimal:
        return sum((line.gross or Decimal("0") for line in self.priced), Decimal("0"))

    @property
    def error_cost(self) -> Decimal:
        return sum((line.error_cost or Decimal("0") for line in self.priced), Decimal("0"))

    @property
    def review_cost(self) -> Decimal:
        return self.review_hours * (self.reviewer_rate or Decimal("0"))

    @property
    def net(self) -> Decimal:
        return self.gross - (self.spend or Decimal("0")) - self.review_cost - self.error_cost

    @property
    def confirmed(self) -> bool:
        """Every baseline and rate the period uses is confirmed (and the reviewer rate, when there was review time)."""
        used = self.priced
        if not used:
            return False
        if not all(line.baseline_confirmed and line.rate_confirmed for line in used):
            return False
        return not (self.review_hours > 0 and not self.reviewer_confirmed)

    def unconfirmed_ids(self) -> tuple[list[str], list[str]]:
        """The baselines and rates the period uses that are not confirmed yet (and the reviewer rate after review)."""
        baselines = sorted(
            {line.baseline_id for line in self.priced if line.baseline_id and not line.baseline_confirmed}
        )
        rates = {line.rate_id for line in self.priced if line.rate_id and not line.rate_confirmed}
        if self.review_hours > 0 and self.reviewer_rate_id and not self.reviewer_confirmed:
            rates.add(self.reviewer_rate_id)
        return baselines, sorted(rates)

    @property
    def confidence(self) -> str:
        """High when 80% or more of the hours come from confirmed baselines, medium from 50%, else low."""
        hours = self.hours_saved
        if hours <= 0:
            return "low"
        confirmed = sum((line.hours_saved for line in self.priced if line.baseline_confirmed), Decimal("0"))
        share = confirmed / hours
        return "high" if share >= Decimal("0.8") else ("medium" if share >= Decimal("0.5") else "low")

    @property
    def roi(self) -> Decimal | None:
        if self.spend is None or self.spend <= 0 or self.spend < self.roi_floor:
            return None
        return self.net / self.spend


async def _convert(ctx: MetricContext, amount: Decimal, source: str, target: str) -> tuple[Decimal | None, bool]:
    """``amount`` from ``source`` to ``target`` currency with the tenant's latest USD rate; None without one."""
    if source == target:
        return amount, False
    from core.insights.metrics_cost import _usd_rate

    if "usd_rate" not in ctx.cache:
        ctx.cache["usd_rate"] = await _usd_rate(ctx)
    rate = ctx.cache["usd_rate"]
    if rate is None:
        return None, False
    value, _day = rate
    return (amount * value if source == "USD" else amount / value), True


async def task_counts(ctx: MetricContext, period: Period) -> dict[tuple[str, str], tuple[int, int]]:
    """Completed and failed measured runs per (agent type, task type) in the period, tenant-wide."""
    data = await run_data(ctx, period, group=AGENT_TASK_GROUP, domain_filter=False)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data.rows:
        if row["data_quality"] == "measured":
            grouped[row["grp"]].append(row)
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for grp, rows in grouped.items():
        agent_type, _, task_type = grp.partition("|")
        t = totals(rows)
        out[(agent_type, task_type)] = (int(t["completed"]), int(t["failed"]))
    return out


async def review_time(ctx: MetricContext, period: Period) -> tuple[Decimal, int]:
    """Hours people spent deciding approvals decided in the period (each capped), and how many there were."""
    from sqlalchemy import func, select

    from core.models.hitl import HITLQueue as H

    lo = day_bounds(period.start, ctx.zone)[0]
    hi = day_bounds(period.end, ctx.zone)[1]
    cap = REVIEW_CAP.total_seconds()
    seconds = func.least(
        func.extract("epoch", H.decision_at - func.coalesce(H.first_viewed_at, H.decision_at - REVIEW_UNOPENED)),
        cap,
    )
    row = (
        await ctx.session.execute(
            select(func.count(), func.coalesce(func.sum(func.greatest(seconds, 0)), 0)).where(
                H.tenant_id == ctx.tenant_id, H.decision_at.is_not(None), H.decision_at >= lo, H.decision_at < hi
            )
        )
    ).one()
    return Decimal(str(row[1] or 0)) / Decimal(3600), int(row[0] or 0)


async def estimate(ctx: MetricContext, period: Period, assumptions: Assumptions) -> ValueEstimate:
    from core.insights.metrics_cost import cost_basis, daily_spend

    basis = await cost_basis(ctx)
    currency = basis.currency
    spend = await daily_spend(ctx, period.start, period.end)
    counts = await task_counts(ctx, period)
    fx_used = False
    hints: list[str] = []
    rates: dict[str, tuple[Decimal | None, Any]] = {}
    for role, rate in assumptions.rates.items():
        converted, used = await _convert(ctx, Decimal(rate.hourly_cost), rate.currency, currency)
        fx_used = fx_used or used
        rates[role] = (converted, rate)
    lines: list[ValueLine] = []
    for (agent_type, task_type), (completed, failed) in sorted(counts.items()):
        line = ValueLine(agent_type=agent_type, task_type=task_type or "", completed=completed, failed=failed)
        baseline = assumptions.baseline_for(agent_type, task_type or None)
        if baseline is not None:
            line.baseline_id = str(baseline.id)
            line.baseline_minutes = Decimal(baseline.baseline_minutes)
            line.role = baseline.role
            line.baseline_confirmed = baseline.confirmed_at is not None
            line.baseline_source = baseline.source
            converted, rate = rates.get(baseline.role, (None, None))
            if rate is not None:
                line.rate_id = str(rate.id)
                line.rate_confirmed = rate.confirmed_at is not None
                line.rate = converted
        if line.baseline_minutes is not None:
            line.hours_saved = Decimal(completed) * line.baseline_minutes / 60
        if line.priced and line.rate is not None and line.baseline_minutes is not None:
            line.gross = line.hours_saved * line.rate
            line.error_cost = Decimal(failed) * line.baseline_minutes / 60 * line.rate
        lines.append(line)
    review_hours, review_items = await review_time(ctx, period)
    reviewer_rate, reviewer = rates.get(REVIEWER_ROLE, (None, None))
    if any(converted is None for converted, _ in rates.values()):
        hints.append(
            "Some hourly rates are in another currency and no USD to INR rate is recorded, so they are left out. "
            "Add a USD rate under AI spend FX rates."
        )
    if fx_used:
        hints.append("Some hourly rates were converted to the reporting currency with the latest USD rate.")
    if any(line.baseline_minutes is not None and line.rate is None for line in lines if line.completed + line.failed):
        hints.append("Some baselines name a role with no hourly rate; add the rate on the Money saved page.")
    return ValueEstimate(
        currency=currency,
        lines=lines,
        spend=spend.total() if spend.amounts else None,
        spend_source=spend.source,
        review_hours=review_hours,
        review_items=review_items,
        reviewer_rate=reviewer_rate,
        reviewer_confirmed=bool(reviewer is not None and reviewer.confirmed_at is not None),
        roi_floor=assumptions.roi_floor,
        reviewer_rate_id=str(reviewer.id) if reviewer is not None else None,
        fx_estimated=fx_used,
        hints=hints,
    )


async def estimate_for(ctx: MetricContext, period: Period) -> ValueEstimate:
    """The estimate for ``period`` (cached per request; seeds the tenant's defaults on first use)."""
    key = ("value", period.start, period.end)
    if key in ctx.cache:
        return ctx.cache[key]
    from core.insights.metrics_cost import cost_basis

    basis = await cost_basis(ctx)
    if "value_assumptions" not in ctx.cache:
        ctx.cache["value_assumptions"] = await load_assumptions(ctx.session, ctx.tenant_id, basis.currency)
    result = await estimate(ctx, period, ctx.cache["value_assumptions"])
    ctx.cache[key] = result
    return result


# ------------------------------------------------------------------ metric resolvers

SOURCE = "run_facts+value_baselines+value_rates+hitl_queue"
NO_TASKS_HINT = (
    "No completed or failed agent runs with a baseline are recorded for this period. Savings count completed "
    "tasks of agent types that have baseline minutes per task."
)


def _envelope(est: ValueEstimate) -> dict[str, Any]:
    return {"source": SOURCE, "currency": est.currency, "unit": est.currency.lower()}


def _hint(est: ValueEstimate) -> str | None:
    parts = list(est.hints)
    if est.unpriced:
        names = ", ".join(sorted({line.agent_type for line in est.unpriced}))
        parts.append(f"Agent types with no baseline are not valued: {names}.")
    return " ".join(parts) or None


def _confirmation(est: ValueEstimate) -> dict[str, Any]:
    return {
        "confirmed": est.confirmed,
        "confidence": est.confidence,
        "unpriced_task_types": len({line.agent_type for line in est.unpriced}),
        "link": VALUE_LINK,
    }


def waterfall(est: ValueEstimate) -> list[dict[str, Any]]:
    return [
        {"label": "gross_saved", "value": money(est.gross)},
        {"label": "spend", "value": money(-(est.spend or Decimal("0")))},
        {"label": "review_cost", "value": money(-est.review_cost)},
        {"label": "error_cost", "value": money(-est.error_cost)},
        {"label": "net_saved", "value": money(est.net)},
    ]


async def resolve_money_saved(ctx: MetricContext) -> dict[str, Any]:
    est = await estimate_for(ctx, ctx.period)
    if not est.priced:
        return {**_envelope(est), "value": None, "data_quality": "no_data", "hint": _hint(est) or NO_TASKS_HINT}
    out: dict[str, Any] = {**_envelope(est), "data_quality": "estimated", "confirmation": _confirmation(est)}
    if not est.confirmed:
        out.update(value=None, hint=CONFIRM_HINT)
        return out
    out.update(value=money(est.net), rows=waterfall(est))
    if ctx.compare is not None:
        previous = await estimate_for(ctx, ctx.compare)
        out["compare_value"] = money(previous.net) if previous.priced and previous.confirmed else None
    hint = _hint(est)
    if hint:
        out["hint"] = hint
    return out


async def resolve_hours_saved(ctx: MetricContext) -> dict[str, Any]:
    est = await estimate_for(ctx, ctx.period)
    if not est.priced:
        return {"source": SOURCE, "value": None, "data_quality": "no_data", "hint": _hint(est) or NO_TASKS_HINT}
    out: dict[str, Any] = {"source": SOURCE, "data_quality": "estimated", "confirmation": _confirmation(est)}
    if not est.confirmed:
        out.update(value=None, hint=CONFIRM_HINT)
        return out
    out["value"] = round(float(est.hours_saved), 1)
    out["rows"] = sorted(
        (
            {"group": line.agent_type, "value": round(float(line.hours_saved), 1)}
            for line in est.priced
            if line.hours_saved > 0
        ),
        key=lambda r: -r["value"],
    )
    if ctx.compare is not None:
        previous = await estimate_for(ctx, ctx.compare)
        out["compare_value"] = round(float(previous.hours_saved), 1) if previous.priced and previous.confirmed else None
    return out


async def resolve_roi(ctx: MetricContext) -> dict[str, Any]:
    est = await estimate_for(ctx, ctx.period)
    if not est.priced:
        return {"source": SOURCE, "value": None, "data_quality": "no_data", "hint": _hint(est) or NO_TASKS_HINT}
    out: dict[str, Any] = {"source": SOURCE, "data_quality": "estimated", "confirmation": _confirmation(est)}
    if not est.confirmed:
        out.update(value=None, hint=CONFIRM_HINT)
        return out
    roi = est.roi
    if roi is None:
        floor = money(est.roi_floor, 0)
        out.update(
            value=None,
            hint=(
                f"ROI is shown once the period's spend reaches {floor} {est.currency}: net saved over a smaller "
                "spend says little. Net saved is on the Money saved tile."
            ),
        )
        return out
    out["value"] = round(float(roi) * 100, 1)
    if ctx.compare is not None:
        previous = await estimate_for(ctx, ctx.compare)
        prev_roi = previous.roi if previous.priced and previous.confirmed else None
        out["compare_value"] = round(float(prev_roi) * 100, 1) if prev_roi is not None else None
    return out


def lines_as_rows(est: ValueEstimate) -> list[dict[str, Any]]:
    """The Value and savings explorer rows: one per agent type and task type with runs in the period."""
    rows = []
    for line in est.lines:
        if line.completed + line.failed == 0:
            continue
        rows.append(
            {
                "agent_type": line.agent_type,
                "task_type": line.task_type or None,
                "completed": line.completed,
                "failed": line.failed,
                "baseline_minutes": float(line.baseline_minutes) if line.baseline_minutes is not None else None,
                "role": line.role,
                "hourly_rate": money(line.rate) if line.rate is not None else None,
                "hours_saved": round(float(line.hours_saved), 1) if line.baseline_minutes is not None else None,
                "gross_saved": money(line.gross) if line.gross is not None else None,
                "error_cost": money(line.error_cost) if line.error_cost is not None else None,
                "baseline_source": line.baseline_source,
                "confirmed": line.baseline_confirmed and line.rate_confirmed if line.priced else False,
                "priced": line.priced,
                "links": {"value": VALUE_LINK},
            }
        )
    return rows
