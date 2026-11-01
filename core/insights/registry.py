# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Insights metric registry: one definition per metric key, the views that show them, and who may read them.

Each :class:`Metric` says what it measures (title, definition, unit), where
it reads from (``source`` tables), which views show it, how its data quality
is decided, its watch and act thresholds, the dimensions it may be grouped
by, and the resolver that computes it. :func:`resolve` wraps a resolver's
result in the response every metric shares::

    {key, title, definition, unit, currency?, source, data_quality, computed_at,
     period {from, to, granularity}, compare {from, to}?, value?, compare_value?,
     state?, series?, rows?, hint?}

Views: ``executive`` (administrators) and ``finance`` (administrators and the
CFO). A metric may be read by a role that may read any view showing it.
``ui/src/lib/insights/metricKeys.generated.json`` lists the keys
(``scripts/generate_insights_metric_keys.py`` writes it).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from core.insights import metrics as runs
from core.insights import metrics_cost as cost
from core.insights.metrics import RUN_GROUPS, MetricContext

Unit = Literal["count", "percent", "duration_ms", "usd", "inr", "tokens"]
UNITS: tuple[str, ...] = ("count", "percent", "duration_ms", "usd", "inr", "tokens")
DATA_QUALITIES: tuple[str, ...] = ("measured", "estimated", "seeded", "no_data", "mixed")
STATES: tuple[str, ...] = ("ok", "watch", "act")
Resolver = Callable[[MetricContext], Awaitable[dict[str, Any]]]

EXECUTIVE = "executive"
FINANCE = "finance"

# Which roles may read each view. Administrators hold every view; the check is
# made server side in every handler (403 insights_view_forbidden).
VIEW_ROLES: dict[str, frozenset[str]] = {
    EXECUTIVE: frozenset({"admin"}),
    FINANCE: frozenset({"admin", "cfo"}),
}

RUN_QUALITY_RULE = (
    "measured when every run read was recorded live; seeded when every run came from demo seeding; "
    "mixed when both; no_data when no run is recorded in the period."
)
COST_QUALITY_RULE = "measured from the cost source; no_data when the source has no spend in the period."
SNAPSHOT_QUALITY_RULE = "measured from the current rows; no_data when there is nothing to count."


@dataclass(frozen=True)
class Thresholds:
    """``watch`` and ``act`` levels; ``above`` means a higher value is worse."""

    watch: float
    act: float
    direction: Literal["above", "below"]

    def state(self, value: float | None) -> str | None:
        if value is None:
            return None
        v = float(value)
        if self.direction == "above":
            return "act" if v >= self.act else ("watch" if v >= self.watch else "ok")
        return "act" if v <= self.act else ("watch" if v <= self.watch else "ok")

    def as_dict(self) -> dict[str, Any]:
        return {"watch": self.watch, "act": self.act, "direction": self.direction}


@dataclass(frozen=True)
class Metric:
    key: str
    title: str
    definition: str
    unit: Unit
    source: tuple[str, ...]
    views: tuple[str, ...]
    data_quality_rule: str
    resolver: Resolver
    thresholds: Thresholds | None = None
    group_by: tuple[str, ...] = ()
    # True when the metric covers the requested period and may be compared with the previous one.
    comparable: bool = True
    kind: Literal["run", "cost", "snapshot"] = "run"

    def describe(self, allowed_views: frozenset[str] | set[str] | None = None) -> dict[str, Any]:
        views = [v for v in self.views if allowed_views is None or v in allowed_views]
        return {
            "key": self.key,
            "title": self.title,
            "definition": self.definition,
            "unit": self.unit,
            "source": list(self.source),
            "views": views,
            "data_quality_rule": self.data_quality_rule,
            "thresholds": self.thresholds.as_dict() if self.thresholds else None,
            "group_by": list(self.group_by),
            "comparable": self.comparable,
        }


RUN_SOURCE = ("insights_daily", "run_facts")
COST_SOURCE = ("spend_usage_rollups", "agent_cost_ledger")
RATE_GROUPS = tuple(g for g in RUN_GROUPS if g != "status")

_METRICS: tuple[Metric, ...] = (
    Metric(
        key="agents.by_status",
        title="Agents by status",
        definition="The number of agents in each lifecycle status now (shadow, active, paused, retired).",
        unit="count",
        source=("agents",),
        views=(EXECUTIVE,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=runs.resolve_agents_by_status,
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="runs.volume",
        title="Agent runs",
        definition="The number of agent runs that started in the period, across every entry point.",
        unit="count",
        source=RUN_SOURCE,
        views=(EXECUTIVE, FINANCE),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_volume,
        group_by=RUN_GROUPS,
    ),
    Metric(
        key="runs.success_rate",
        title="Run success rate",
        definition=(
            "Completed runs as a share of finished runs. Runs still waiting on a person "
            "(sent for approval or paused) are left out until they finish."
        ),
        unit="percent",
        source=RUN_SOURCE,
        views=(EXECUTIVE,),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_success_rate,
        thresholds=Thresholds(watch=95.0, act=90.0, direction="below"),
        group_by=RATE_GROUPS,
    ),
    Metric(
        key="runs.straight_through_rate",
        title="Straight-through rate",
        definition="Runs that completed with no person involved, as a share of all runs.",
        unit="percent",
        source=RUN_SOURCE,
        views=(EXECUTIVE,),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_straight_through_rate,
        thresholds=Thresholds(watch=70.0, act=50.0, direction="below"),
        group_by=RATE_GROUPS,
    ),
    Metric(
        key="runs.latency",
        title="Run latency (p95)",
        definition=(
            "The 95th percentile of run duration, estimated from latency buckets as the upper bound "
            "of the bucket holding it, so it never understates. Rows give p50, p95 and the mean."
        ),
        unit="duration_ms",
        source=RUN_SOURCE,
        views=(EXECUTIVE,),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_latency,
        thresholds=Thresholds(watch=30_000.0, act=60_000.0, direction="above"),
        group_by=RATE_GROUPS,
    ),
    Metric(
        key="approvals.pending",
        title="Pending approvals",
        definition="Approvals waiting on a person now; rows list the oldest five.",
        unit="count",
        source=("hitl_queue",),
        views=(EXECUTIVE,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=runs.resolve_approvals_pending,
        thresholds=Thresholds(watch=5.0, act=20.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="approvals.cycle_time",
        title="Approval cycle time (median)",
        definition="The median time from an approval request to its decision, for decisions made in the period.",
        unit="duration_ms",
        source=("hitl_queue",),
        views=(EXECUTIVE,),
        data_quality_rule="measured from decided approvals; no_data when none was decided in the period.",
        resolver=runs.resolve_approvals_cycle_time,
        thresholds=Thresholds(watch=4 * 3_600_000.0, act=24 * 3_600_000.0, direction="above"),
    ),
    Metric(
        key="cost.spend",
        title="AI spend",
        definition=(
            "Spend in the period from the cost source (metered usage when AI spend metering is on, "
            "otherwise the agent cost ledger), in the tenant's reporting currency."
        ),
        unit="usd",
        source=COST_SOURCE,
        views=(EXECUTIVE, FINANCE),
        data_quality_rule=COST_QUALITY_RULE,
        resolver=cost.resolve_cost_spend,
        group_by=cost.COST_GROUPS,
        kind="cost",
    ),
    Metric(
        key="cost.per_run",
        title="Cost per run",
        definition="Spend in the period divided by the agent runs that started in it, tenant-wide.",
        unit="usd",
        source=(*COST_SOURCE, *RUN_SOURCE),
        views=(FINANCE,),
        data_quality_rule="measured when both spend and runs are recorded; no_data otherwise.",
        resolver=cost.resolve_cost_per_run,
        kind="cost",
    ),
    Metric(
        key="cost.by_agent",
        title="Spend by agent",
        definition="Spend in the period per agent, highest first (the top 50 are listed).",
        unit="usd",
        source=COST_SOURCE,
        views=(FINANCE,),
        data_quality_rule=COST_QUALITY_RULE,
        resolver=cost.resolve_cost_by_agent,
        comparable=False,
        kind="cost",
    ),
    Metric(
        key="cost.by_model",
        title="Spend by model",
        definition="Metered spend in the period per provider and model, highest first. Needs AI spend metering.",
        unit="usd",
        source=("spend_usage_rollups",),
        views=(FINANCE,),
        data_quality_rule="measured from metered usage; no_data when metering is off or nothing was metered.",
        resolver=cost.resolve_cost_by_model,
        comparable=False,
        kind="cost",
    ),
    Metric(
        key="cost.forecast",
        title="Month-end spend forecast",
        definition=(
            "This month's spend to date plus the daily trend carried to month end, with a 95% band "
            "from the day-to-day scatter. Always an estimate; it covers the current month whatever "
            "period is selected."
        ),
        unit="usd",
        source=COST_SOURCE,
        views=(FINANCE,),
        data_quality_rule="estimated whenever there is spend this month; no_data otherwise.",
        resolver=cost.resolve_cost_forecast,
        comparable=False,
        kind="cost",
    ),
    Metric(
        key="cost.budgets",
        title="Budgets",
        definition=(
            "Each budget alert's threshold against the cost ledger's spend for its current day, week or "
            "month (UTC), as the budget evaluator sums it. The value is the number of budgets at or past "
            "their warning level."
        ),
        unit="count",
        source=("budget_alerts", "agent_cost_ledger"),
        views=(FINANCE,),
        data_quality_rule="measured; no_data when no budget alert is set.",
        resolver=cost.resolve_cost_budgets,
        comparable=False,
        kind="cost",
    ),
    Metric(
        key="tokens.by_unit",
        title="Tokens by unit",
        definition=(
            "Tokens used in the period by unit: metered token units when AI spend metering is on, "
            "otherwise the input, output and cached tokens agent runs report."
        ),
        unit="tokens",
        source=("spend_usage_rollups", *RUN_SOURCE),
        views=(FINANCE,),
        data_quality_rule="measured from the source read; no_data when it has no tokens in the period.",
        resolver=cost.resolve_tokens_by_unit,
        kind="cost",
    ),
    Metric(
        key="cost.data_quality",
        title="Pricing coverage",
        definition=(
            "The share of metered usage records that were priced, with counts of unpriced, unconverted, "
            "FX-estimated, estimated and allocated records. Needs AI spend metering."
        ),
        unit="percent",
        source=("spend_usage_rollups",),
        views=(FINANCE,),
        data_quality_rule="measured, or mixed when some records were estimated; no_data when metering is off.",
        resolver=cost.resolve_cost_data_quality,
        thresholds=Thresholds(watch=99.0, act=95.0, direction="below"),
        comparable=False,
        kind="cost",
    ),
)

METRICS: dict[str, Metric] = {metric.key: metric for metric in _METRICS}


@dataclass(frozen=True)
class View:
    key: str
    title: str
    tiles: tuple[str, ...]
    charts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def keys(self) -> tuple[str, ...]:
        seen: list[str] = []
        for key in (*self.tiles, *self.charts):
            if key not in seen:
                seen.append(key)
        return tuple(seen)


VIEWS: dict[str, View] = {
    EXECUTIVE: View(
        key=EXECUTIVE,
        title="Executive",
        tiles=(
            "runs.volume",
            "runs.success_rate",
            "runs.straight_through_rate",
            "runs.latency",
            "approvals.pending",
            "approvals.cycle_time",
            "cost.spend",
        ),
        charts=("runs.volume", "agents.by_status", "runs.success_rate", "cost.spend"),
    ),
    FINANCE: View(
        key=FINANCE,
        title="Finance",
        tiles=("cost.spend", "cost.per_run", "cost.forecast", "runs.volume", "cost.data_quality"),
        charts=("cost.spend", "cost.by_agent", "cost.by_model", "cost.forecast", "cost.budgets", "tokens.by_unit"),
    ),
}


def metric_keys() -> list[str]:
    return sorted(METRICS)


def allowed_views(role: str, *, is_admin: bool) -> list[str]:
    """The views a caller may read, in display order.

    Administrators are known by the admin scope (``is_admin``), never by a
    role claim alone: a token that says ``admin`` without the scope reads
    nothing.
    """
    effective = (role or "").lower()
    if is_admin:
        effective = "admin"
    elif effective == "admin":
        return []
    return [view for view in VIEWS if effective in VIEW_ROLES[view]]


def readable_metrics(views: list[str] | set[str]) -> list[Metric]:
    allowed = set(views)
    return [metric for metric in _METRICS if allowed.intersection(metric.views)]


async def resolve(metric: Metric, ctx: MetricContext) -> dict[str, Any]:
    """The metric's response for ``ctx``."""
    part = await metric.resolver(ctx)
    period = part.pop("period", None) or ctx.period.as_dict()
    value = part.get("value")
    state = part.pop("state", None)
    if state is None and metric.thresholds is not None:
        state = metric.thresholds.state(value)
    response: dict[str, Any] = {
        "key": metric.key,
        "title": metric.title,
        "definition": metric.definition,
        "unit": part.pop("unit", None) or metric.unit,
        "source": part.pop("source", None) or "+".join(metric.source),
        "data_quality": part.pop("data_quality", "no_data"),
        "computed_at": datetime.now(UTC).isoformat(),
        "period": period,
        "value": value,
    }
    currency = part.pop("currency", None)
    if currency:
        response["currency"] = currency
    if metric.comparable and ctx.compare is not None:
        response["compare"] = ctx.compare.range_dict()
        response["compare_value"] = part.pop("compare_value", None)
    else:
        part.pop("compare_value", None)
    if state is not None:
        response["state"] = state
    for name in ("series", "rows", "hint", "conversion"):
        if part.get(name) is not None:
            response[name] = part[name]
    if response["data_quality"] not in DATA_QUALITIES:
        raise ValueError(f"{metric.key}: data_quality {response['data_quality']!r} is not one of {DATA_QUALITIES}")
    return response
