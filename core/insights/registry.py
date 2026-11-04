# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Insights metric registry: one definition per metric key, the views that show them, and who may read them.

Each :class:`Metric` says what it measures (title, definition, unit), where
it reads from (``source`` tables), which views show it, how its data quality
is decided, its watch and act thresholds, the dimensions it may be grouped
by, and the resolver that computes it. :func:`resolve` wraps a resolver's
result in the response every metric shares::

    {key, title, definition, unit, currency?, source, data_quality, computed_at,
     period {from, to, granularity}, compare {from, to, basis, ...}?, value?, compare_value?,
     state?, series?, rows?, hint?, lower_bound?}

Comparisons are like with like. ``compare`` is the same number of days
immediately before the period. When the period includes the tenant-local
today, which is still running, the change is computed over complete days
only: ``compare.basis`` is ``complete_days``, ``compare.current_value`` is the
metric over the period's days before today and ``compare_value`` over as many
days from the start of the comparison window (``compare.complete_days`` of
them). Otherwise ``compare.basis`` is ``full_period`` and ``compare_value``
covers the whole comparison window. The tile ``value`` always includes today.

Views: ``executive`` (administrators), ``finance`` (and the CFO),
``operations`` (COO), ``hr`` (CHRO), ``marketing`` (CMO), ``commerce``
(merchant), ``compliance`` (auditor) and ``engineering`` (developer). A
metric may be read by a role that may read any view showing it.
``ui/src/lib/insights/metricKeys.generated.json`` lists the keys
(``scripts/generate_insights_metric_keys.py`` writes it).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from core.insights import metrics as runs
from core.insights import metrics_cost as cost
from core.insights import metrics_domain as domain
from core.insights import metrics_governance as governance
from core.insights import metrics_partner as partner
from core.insights import metrics_runtime as runtime
from core.insights import value
from core.insights.metrics import RUN_GROUPS, MetricContext, Period

Unit = Literal["count", "percent", "duration_ms", "usd", "inr", "tokens", "hours"]
UNITS: tuple[str, ...] = ("count", "percent", "duration_ms", "usd", "inr", "tokens", "hours")
DATA_QUALITIES: tuple[str, ...] = ("measured", "estimated", "seeded", "no_data", "mixed")
STATES: tuple[str, ...] = ("ok", "watch", "act")
Resolver = Callable[[MetricContext], Awaitable[dict[str, Any]]]

EXECUTIVE = "executive"
FINANCE = "finance"
OPERATIONS = "operations"
HR = "hr"
MARKETING = "marketing"
COMMERCE = "commerce"
COMPLIANCE = "compliance"
ENGINEERING = "engineering"
BACK_OFFICE = "back_office"
DOMAIN = "domain"
PARTNER = "partner"

# Which roles may read each view. Administrators hold every view; the check is
# made server side in every handler (403 insights_view_forbidden).
VIEW_ROLES: dict[str, frozenset[str]] = {
    EXECUTIVE: frozenset({"admin"}),
    FINANCE: frozenset({"admin", "cfo"}),
    OPERATIONS: frozenset({"admin", "coo"}),
    HR: frozenset({"admin", "chro"}),
    MARKETING: frozenset({"admin", "cmo"}),
    COMMERCE: frozenset({"admin", "merchant"}),
    COMPLIANCE: frozenset({"admin", "auditor"}),
    ENGINEERING: frozenset({"admin", "developer"}),
    # There is no back-office role (no cbo): administrators read it.
    BACK_OFFICE: frozenset({"admin"}),
    # Domain leads and analysts: everything in their assigned domain.
    DOMAIN: frozenset({"admin", "domain_lead", "analyst"}),
    # A CA firm's view of its client companies: the roles the legacy partner dashboard admitted.
    PARTNER: frozenset({"admin", "cfo", "coo", "auditor"}),
}

# A role view shows the run and approval metrics of its domains only (and of the caller's domains within
# them); the Executive and Finance views keep the caller's domains as they are.
VIEW_DOMAINS: dict[str, tuple[str, ...] | None] = {
    EXECUTIVE: None,
    FINANCE: None,
    OPERATIONS: ("ops", "operations"),
    HR: ("hr",),
    MARKETING: ("marketing",),
    COMMERCE: ("commerce",),
    COMPLIANCE: None,
    ENGINEERING: None,
    BACK_OFFICE: ("backoffice", "legal", "risk", "corporate", "comms"),
    # The caller's own domains (an analyst's assigned domain; every domain for an administrator).
    DOMAIN: None,
    PARTNER: None,
}

RUN_QUALITY_RULE = (
    "measured when every run read was recorded live; seeded when every run came from demo seeding; "
    "mixed when both; no_data when no run is recorded in the period."
)
COST_QUALITY_RULE = (
    "measured from one cost source; mixed when some days come from metered usage and others from the "
    "agent cost ledger; no_data when there is no spend in the period."
)
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
    # Run dimensions the metric may be filtered by (``filter[dim]``); only run metrics take filters, and
    # they may also be grouped by two of their ``group_by`` dimensions at once.
    filters: tuple[str, ...] = ()
    # True when the metric covers the requested period and may be compared with the previous one.
    comparable: bool = True
    kind: Literal["run", "cost", "snapshot", "value"] = "run"

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
            "filters": list(self.filters),
            "comparable": self.comparable,
        }


MAX_GROUP_DIMENSIONS = 2
MAX_FILTER_VALUES = 50
MAX_FILTER_VALUE_LENGTH = 128


class MetricQueryError(ValueError):
    """A metric request asked for a grouping or filter the metric does not offer."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


def parse_grouping(metric: Metric, values: Sequence[str | None]) -> tuple[str | None, str | None]:
    """``group_by`` values (``group_by=a`` or ``group_by[]=a&group_by[]=b``) as at most two allowed dimensions."""
    dims = list(dict.fromkeys(v.strip() for v in values if v and v.strip()))
    if not dims:
        return None, None
    unknown = [d for d in dims if d not in metric.group_by]
    if unknown:
        allowed = ", ".join(metric.group_by) or "none"
        raise MetricQueryError("group_by_not_allowed", f"{metric.key} may be grouped by: {allowed}")
    if len(dims) > 1 and not metric.filters:
        raise MetricQueryError("group_by_not_allowed", f"{metric.key} may be grouped by one dimension at a time")
    if len(dims) > MAX_GROUP_DIMENSIONS:
        raise MetricQueryError(
            "group_by_not_allowed", f"{metric.key} may be grouped by at most {MAX_GROUP_DIMENSIONS} dimensions"
        )
    return dims[0], dims[1] if len(dims) > 1 else None


def parse_filters(metric: Metric, raw: Mapping[str, str | Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """``filter[dim]=a,b`` parameters (``dim`` without the brackets) as allowed values per dimension."""
    out: dict[str, tuple[str, ...]] = {}
    for dim, given in raw.items():
        if dim not in metric.filters:
            allowed = ", ".join(metric.filters) or "none"
            raise MetricQueryError("filter_not_allowed", f"{metric.key} may be filtered by: {allowed}")
        parts = [given] if isinstance(given, str) else list(given)
        values = tuple(dict.fromkeys(v.strip() for part in parts for v in str(part).split(",") if v.strip()))
        if not values:
            raise MetricQueryError("invalid_filter", f"filter[{dim}] needs at least one value")
        if len(values) > MAX_FILTER_VALUES:
            raise MetricQueryError("invalid_filter", f"filter[{dim}] takes at most {MAX_FILTER_VALUES} values")
        if any(len(v) > MAX_FILTER_VALUE_LENGTH for v in values):
            raise MetricQueryError(
                "invalid_filter", f"filter[{dim}] values are at most {MAX_FILTER_VALUE_LENGTH} characters"
            )
        out[dim] = values
    return out


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
        views=(EXECUTIVE, FINANCE, OPERATIONS, HR, DOMAIN),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=runs.resolve_agents_by_status,
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="runs.volume",
        title="Agent runs",
        definition=(
            "The number of agent runs that started in the period, across every entry point. An approval "
            "resuming a paused run is part of that run, not a new one; debugging-console runs are not counted."
        ),
        unit="count",
        source=RUN_SOURCE,
        views=(EXECUTIVE, FINANCE, OPERATIONS, HR, MARKETING, ENGINEERING, DOMAIN, BACK_OFFICE),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_volume,
        group_by=RUN_GROUPS,
        filters=RUN_GROUPS,
    ),
    Metric(
        key="runs.success_rate",
        title="Run success rate",
        definition=(
            "Completed runs as a share of runs that completed or failed. A run paused for approval counts "
            "by its outcome once it is resumed. Runs still waiting on a person, refused by the platform "
            "(operator halt, billing limit, guardrail or gateway block) or rejected are neither successes "
            "nor failures and are left out; rows break the runs down by status. Debugging-console runs are "
            "not counted."
        ),
        unit="percent",
        source=RUN_SOURCE,
        views=(EXECUTIVE, OPERATIONS, HR, ENGINEERING, FINANCE, DOMAIN, BACK_OFFICE),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_success_rate,
        thresholds=Thresholds(watch=95.0, act=90.0, direction="below"),
        group_by=RATE_GROUPS,
        filters=RUN_GROUPS,
    ),
    Metric(
        key="runs.straight_through_rate",
        title="Straight-through rate",
        definition=(
            "Completed runs that no person was involved in (no approval), as a share of all completed runs. "
            "A run approved and resumed counts as completed with a person involved."
        ),
        unit="percent",
        source=RUN_SOURCE,
        views=(EXECUTIVE, FINANCE, OPERATIONS, HR, DOMAIN),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_straight_through_rate,
        thresholds=Thresholds(watch=70.0, act=50.0, direction="below"),
        group_by=RATE_GROUPS,
        filters=RUN_GROUPS,
    ),
    Metric(
        key="runs.latency",
        title="Run latency (p95)",
        definition=(
            "The 95th percentile of run duration, estimated from latency buckets as the upper bound "
            "of the bucket holding it. When it lands in the last, open-ended bucket (over 10 minutes) "
            "the value is 10 minutes with lower_bound true: the runs took longer than that. Rows give "
            "p50, p95 and the mean."
        ),
        unit="duration_ms",
        source=RUN_SOURCE,
        views=(EXECUTIVE, OPERATIONS, ENGINEERING, DOMAIN),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_latency,
        thresholds=Thresholds(watch=30_000.0, act=60_000.0, direction="above"),
        group_by=RATE_GROUPS,
        filters=RUN_GROUPS,
    ),
    Metric(
        key="runs.confidence",
        title="Run confidence",
        definition=(
            "The mean confidence the agents reported for their runs in the period (0 to 100). Rows give the runs "
            "below their agent's confidence floor (sent to a person) and the runs that reported a score; runs "
            "without a score are left out. Debugging-console runs are not counted."
        ),
        unit="percent",
        source=RUN_SOURCE,
        views=(EXECUTIVE, ENGINEERING, DOMAIN),
        data_quality_rule=RUN_QUALITY_RULE,
        resolver=runs.resolve_runs_confidence,
        thresholds=Thresholds(watch=80.0, act=70.0, direction="below"),
        group_by=RATE_GROUPS,
        filters=RUN_GROUPS,
    ),
    Metric(
        key="quality.feedback",
        title="Feedback on runs",
        definition=(
            "Feedback reviewers recorded on agent runs in the period (thumbs up, thumbs down, corrections); rows "
            "give the count of each type. Limited to the agents of the caller's domains."
        ),
        unit="count",
        source=("agent_feedback",),
        views=(DOMAIN, ENGINEERING),
        data_quality_rule="measured from the feedback rows; no_data when none was recorded in the period.",
        resolver=domain.resolve_quality_feedback,
    ),
    Metric(
        key="approvals.pending",
        title="Pending approvals",
        definition="Approvals waiting on a person now; rows list the oldest five.",
        unit="count",
        source=("hitl_queue",),
        views=(EXECUTIVE, OPERATIONS, HR, MARKETING, DOMAIN, BACK_OFFICE),
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
        views=(EXECUTIVE, HR, DOMAIN, BACK_OFFICE),
        data_quality_rule="measured from decided approvals; no_data when none was decided in the period.",
        resolver=runs.resolve_approvals_cycle_time,
        thresholds=Thresholds(watch=4 * 3_600_000.0, act=24 * 3_600_000.0, direction="above"),
    ),
    Metric(
        key="cost.spend",
        title="AI spend",
        definition=(
            "Spend in the period, in the tenant's reporting currency. Each day comes from metered usage "
            "when AI spend metering has rows for it, otherwise from the agent cost ledger; the source "
            "names what was read."
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
        definition=(
            "Spend divided by the agent runs that started on the same days, tenant-wide. Only days with run "
            "data count on both sides (from the tenant's first recorded run, less days not yet rolled up); "
            "when that is fewer days than the period, the hint names the days covered."
        ),
        unit="usd",
        source=(*COST_SOURCE, *RUN_SOURCE),
        views=(FINANCE,),
        data_quality_rule=(
            "measured when spend and runs cover the whole period from one cost source; mixed when they "
            "cover fewer days than the period or both cost sources; no_data when either is missing."
        ),
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
            "This month's spend on its complete days plus the daily trend carried from today to month end, "
            "with a 95% band from the day-to-day scatter. The trend is fitted on complete days only: this "
            "month's once 7 of them have spend, else the last 14 complete days. Always an estimate; it "
            "covers the current month whatever period is selected."
        ),
        unit="usd",
        source=COST_SOURCE,
        views=(FINANCE,),
        data_quality_rule="estimated when there are 7 days of spend to fit; no_data otherwise.",
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
            "Tokens used in the period by unit: metered token units when every spend day of the period is "
            "metered, otherwise the input, output and cached tokens agent runs report. Run figures leave "
            "out usage after an approval resumes a paused run (a resume reports the whole thread's usage, "
            "so it is recorded as 0); spend metering is the cost truth."
        ),
        unit="tokens",
        source=("spend_usage_rollups", *RUN_SOURCE),
        views=(FINANCE,),
        data_quality_rule=(
            "measured from the source read; mixed when run figures cover fewer days than the period; "
            "no_data when it has no tokens in the period."
        ),
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
    Metric(
        key="value.money_saved",
        title="Money saved (estimated)",
        definition=(
            "Net money saved in the period: completed tasks times baseline minutes per task, valued at the "
            "role's loaded hourly rate, less AI spend, the time people spent deciding approvals and the manual "
            "redo of failed tasks. An estimate from tenant-confirmed baselines and rates; hidden until a tenant "
            "administrator has confirmed every baseline and rate the period uses. Rows give the waterfall."
        ),
        unit="inr",
        source=("run_facts", "insights_daily", "value_baselines", "value_rates", "hitl_queue", *COST_SOURCE),
        views=(EXECUTIVE, FINANCE),
        data_quality_rule="estimated, always; no_data when no completed task of a priced agent type is recorded.",
        resolver=value.resolve_money_saved,
        kind="value",
    ),
    Metric(
        key="value.hours_saved",
        title="Hours saved (estimated)",
        definition=(
            "Completed tasks in the period times baseline minutes per task, in hours. Hidden until a tenant "
            "administrator has confirmed the baselines and rates the period uses. Rows give hours per agent type."
        ),
        unit="hours",
        source=("run_facts", "insights_daily", "value_baselines"),
        views=(EXECUTIVE,),
        data_quality_rule="estimated, always; no_data when no completed task of a priced agent type is recorded.",
        resolver=value.resolve_hours_saved,
        kind="value",
    ),
    Metric(
        key="value.roi",
        title="ROI (estimated)",
        definition=(
            "Net money saved over AI spend in the period, as a percentage. Shown once the period's spend reaches "
            "the floor (INR 5,000 or USD 60 unless the tenant set its own) and the assumptions are confirmed."
        ),
        unit="percent",
        source=("run_facts", "insights_daily", "value_baselines", "value_rates", "hitl_queue", *COST_SOURCE),
        views=(FINANCE,),
        data_quality_rule="estimated, always; no_data when no completed task of a priced agent type is recorded.",
        resolver=value.resolve_roi,
        thresholds=Thresholds(watch=50.0, act=0.0, direction="below"),
        kind="value",
    ),
    Metric(
        key="workflows.runs",
        title="Workflow runs",
        definition="Workflow runs that started in the period; rows break them down by their status now.",
        unit="count",
        source=("workflow_runs",),
        views=(OPERATIONS,),
        data_quality_rule="measured; no_data when no workflow run started in the period.",
        resolver=domain.resolve_workflow_runs,
    ),
    Metric(
        key="workflows.stalled",
        title="Stalled workflow runs",
        definition="Workflow runs still open past their timeout, now; rows list the five oldest.",
        unit="count",
        source=("workflow_runs", "workflow_definitions"),
        views=(OPERATIONS,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=domain.resolve_workflows_stalled,
        thresholds=Thresholds(watch=1.0, act=5.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="connectors.unhealthy",
        title="Unhealthy connectors",
        definition=(
            "Connector configurations whose last health check failed, now; rows count configurations by health."
        ),
        unit="count",
        source=("connector_configs",),
        views=(OPERATIONS,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=domain.resolve_connectors_unhealthy,
        thresholds=Thresholds(watch=1.0, act=3.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="hr.pt_returns",
        title="Professional tax returns filed",
        definition=(
            "Professional tax returns submitted in the period; rows add the returns prepared but not submitted yet."
        ),
        unit="count",
        source=("professional_tax_returns",),
        views=(HR,),
        data_quality_rule="measured; no_data when no return was prepared or filed.",
        resolver=domain.resolve_pt_returns,
    ),
    Metric(
        key="marketing.leads",
        title="New leads",
        definition="Leads added to the pipeline in the period; rows break them down by their stage now.",
        unit="count",
        source=("lead_pipeline",),
        views=(MARKETING,),
        data_quality_rule="measured; no_data when no lead was added in the period.",
        resolver=domain.resolve_leads,
    ),
    Metric(
        key="marketing.content_drafts",
        title="Content drafts",
        definition=(
            "Content drafts created in the period; rows break them down by status (draft, pending, approved, "
            "rejected)."
        ),
        unit="count",
        source=("content_drafts",),
        views=(MARKETING,),
        data_quality_rule="measured; no_data when no draft was created in the period.",
        resolver=domain.resolve_content_drafts,
    ),
    Metric(
        key="marketing.draft_cycle_time",
        title="Draft approval time (median)",
        definition=(
            "The median time from a content draft's creation to its approval or rejection, for drafts decided in "
            "the period."
        ),
        unit="duration_ms",
        source=("content_drafts",),
        views=(MARKETING,),
        data_quality_rule="measured; no_data when no draft was decided in the period.",
        resolver=domain.resolve_draft_cycle_time,
        thresholds=Thresholds(watch=24 * 3_600_000.0, act=72 * 3_600_000.0, direction="above"),
    ),
    Metric(
        key="commerce.funnel",
        title="Shopping funnel",
        definition=(
            "Carts, checkout sessions, completed checkouts, orders and paid orders created in the period, in order. "
            "An order counts as paid when a payment capture for it succeeded. The value is the orders."
        ),
        unit="count",
        source=("commerce_carts", "commerce_checkout_sessions", "commerce_orders", "commerce_payment_operations"),
        views=(COMMERCE,),
        data_quality_rule="measured; no_data when there was no shopping activity in the period.",
        resolver=domain.resolve_commerce_funnel,
        comparable=False,
    ),
    Metric(
        key="commerce.orders",
        title="Orders",
        definition="Orders placed in the period.",
        unit="count",
        source=("commerce_orders",),
        views=(COMMERCE,),
        data_quality_rule="measured; no_data when no order was placed in the period.",
        resolver=domain.resolve_commerce_orders,
    ),
    Metric(
        key="commerce.webhook_failures",
        title="Order webhooks given up",
        definition=(
            "Order webhooks created in the period that were given up after their retries (dead lettered); rows add "
            "the delivered and still pending ones."
        ),
        unit="count",
        source=("commerce_order_webhook_deliveries",),
        views=(COMMERCE,),
        data_quality_rule="measured; no_data when no order webhook was sent in the period.",
        resolver=domain.resolve_webhook_failures,
        thresholds=Thresholds(watch=1.0, act=10.0, direction="above"),
        comparable=False,
    ),
    Metric(
        key="compliance.audit_integrity",
        title="Audit rows verified",
        definition=(
            "The newest 1,000 links of the audit chain checked now: rows whose signature and chain link verify. "
            "The tile is in act when any link is broken; rows add unsigned and not yet sealed rows and the first break."
        ),
        unit="count",
        source=("audit_log", "audit_chain_anchors"),
        views=(COMPLIANCE,),
        data_quality_rule="measured from the chain now; no_data when sealing is off or nothing is sealed yet.",
        resolver=governance.resolve_audit_integrity,
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="compliance.approvals_decided",
        title="Approvals decided",
        definition=(
            "Approvals decided in the period; rows split them by decided directly or under a delegation "
            "(another user acting for the approver)."
        ),
        unit="count",
        source=("hitl_queue", "audit_log"),
        views=(COMPLIANCE,),
        data_quality_rule="measured; no_data when no approval was decided in the period.",
        resolver=governance.resolve_approvals_decided,
    ),
    Metric(
        key="compliance.rubber_stamps",
        title="Approvals decided in under 20 seconds",
        definition=(
            "The share of approvals decided in the period less than 20 seconds after the reviewer opened them "
            "(or after they were raised, when the reviewer never opened them)."
        ),
        unit="percent",
        source=("hitl_queue",),
        views=(COMPLIANCE,),
        data_quality_rule="measured; no_data when no approval was decided in the period.",
        resolver=governance.resolve_rubber_stamps,
        thresholds=Thresholds(watch=25.0, act=50.0, direction="above"),
    ),
    Metric(
        key="compliance.dsar_open",
        title="Open data subject requests",
        definition=(
            "Data subject requests (access, erase, export) received or in progress now; rows count every request by "
            "status and give the oldest open one's age in days. No subject is shown."
        ),
        unit="count",
        source=("dsar_requests",),
        views=(COMPLIANCE,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=governance.resolve_dsar_open,
        thresholds=Thresholds(watch=1.0, act=5.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="compliance.dsar_completion_time",
        title="Data subject request completion (median)",
        definition=(
            "The median time from a data subject request to its completion, for requests completed in the period."
        ),
        unit="duration_ms",
        source=("dsar_requests",),
        views=(COMPLIANCE,),
        data_quality_rule="measured; no_data when no request was completed in the period.",
        resolver=governance.resolve_dsar_completion_time,
        thresholds=Thresholds(watch=7 * 86_400_000.0, act=25 * 86_400_000.0, direction="above"),
    ),
    Metric(
        key="compliance.attestations_expiring",
        title="Residency attestations expiring",
        definition=(
            "Provider residency attestations in force that expire within 30 days or have expired, now; rows list them."
        ),
        unit="count",
        source=("provider_residency_attestations",),
        views=(COMPLIANCE,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=governance.resolve_attestations_expiring,
        thresholds=Thresholds(watch=1.0, act=3.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="compliance.change_log",
        title="Changes made",
        definition=(
            "Changes made in the period, by kind: agents created, deleted or re-scoped; prompt edits; governance "
            "(policies, residency, operator overrides, model gateway policies and limits); spend controls; settings; "
            "access (BI logins, delegations); industry packs."
        ),
        unit="count",
        source=("audit_log", "prompt_edit_history", "prompt_template_edit_history", "user_delegations"),
        views=(COMPLIANCE,),
        data_quality_rule="measured; no_data when nothing was changed in the period.",
        resolver=governance.resolve_change_log,
    ),
    Metric(
        key="engineering.gateway_issues",
        title="Model calls failed or fell back",
        definition=(
            "Model calls in the period that failed or were served by a fallback model; rows add every call and the "
            "calls restricted by policy."
        ),
        unit="count",
        source=("model_gateway_records",),
        views=(ENGINEERING,),
        data_quality_rule="measured; no_data when no model call was recorded in the period.",
        resolver=governance.resolve_gateway_issues,
        thresholds=Thresholds(watch=1.0, act=25.0, direction="above"),
    ),
    Metric(
        key="engineering.slow_spans",
        title="Slowest step (p95)",
        definition=(
            "Trace spans in the period grouped by name (agent runs, reasoning, tool calls, knowledge searches), "
            "ordered by total time, with calls, total time, p95 and errors; the value is the highest p95."
        ),
        unit="duration_ms",
        source=("run_spans",),
        views=(ENGINEERING,),
        data_quality_rule="measured; no_data when no span was stored in the period.",
        resolver=governance.resolve_slow_spans,
        thresholds=Thresholds(watch=30_000.0, act=60_000.0, direction="above"),
        comparable=False,
    ),
    Metric(
        key="engineering.eval_pass_rate",
        title="Eval pass rate (latest)",
        definition="The pass rate of the newest eval run in the period; rows give the newest run of each dataset.",
        unit="percent",
        source=("eval_runs",),
        views=(ENGINEERING,),
        data_quality_rule="measured; no_data when no eval run finished in the period.",
        resolver=governance.resolve_eval_pass_rate,
        thresholds=Thresholds(watch=90.0, act=80.0, direction="below"),
        comparable=False,
    ),
    Metric(
        key="engineering.prompt_edits",
        title="Prompt edits",
        definition="Agent prompt and prompt template edits made in the period.",
        unit="count",
        source=("prompt_edit_history", "prompt_template_edit_history"),
        views=(ENGINEERING,),
        data_quality_rule="measured; no_data when no prompt was edited in the period.",
        resolver=governance.resolve_prompt_edits,
    ),
    Metric(
        key="engineering.tool_usage",
        title="Tool calls",
        definition=(
            "Tool calls agents made through the tool gateway in the period; rows give each tool's calls, failures, "
            "refusals (policy, scope or limits), idempotent replays, success rate and p95 latency."
        ),
        unit="count",
        source=("tool_calls",),
        views=(ENGINEERING,),
        data_quality_rule="measured from the gateway's tool-call records; no_data when none was recorded.",
        resolver=runtime.resolve_tool_usage,
    ),
    Metric(
        key="compliance.guardrail_events",
        title="Guardrail outcomes",
        definition=(
            "Guardrail outcomes in the period (a rule found something), by stage, detector, action and whether it "
            "was enforced or only flagged. Dry runs are not counted. Counted per UTC day."
        ),
        unit="count",
        source=("guardrail_events",),
        views=(COMPLIANCE,),
        data_quality_rule="measured from the daily counters; no_data when no outcome was counted.",
        resolver=runtime.resolve_guardrail_events,
    ),
    Metric(
        key="compliance.security_events",
        title="Security events",
        definition=(
            "Failed sign-ins, rate-limit and scope denials, session revocations, API-key use and webhook "
            "signature failures in the period, by kind. A kind spikes when its last day is more than three "
            "standard deviations above its 30 days before (and at least 5). Counted per UTC day."
        ),
        unit="count",
        source=("security_events_daily",),
        views=(COMPLIANCE,),
        data_quality_rule="measured from the daily counters; no_data when no event was counted.",
        resolver=runtime.resolve_security_events,
    ),
    Metric(
        key="backoffice.deadlines",
        title="Compliance deadlines",
        definition=(
            "Statutory deadlines not yet filed that are overdue today; rows give, per deadline type, the overdue "
            "ones and those due in the next 30 days."
        ),
        unit="count",
        source=("compliance_deadlines",),
        views=(BACK_OFFICE, PARTNER),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=domain.resolve_compliance_deadlines,
        thresholds=Thresholds(watch=1.0, act=3.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="backoffice.txn_findings",
        title="Transaction findings",
        definition=(
            "Findings transaction monitoring detected in the period; rows give each severity's findings and how "
            "many are still open."
        ),
        unit="count",
        source=("txn_findings",),
        views=(BACK_OFFICE,),
        data_quality_rule="measured from the findings rows; no_data when none was detected in the period.",
        resolver=domain.resolve_txn_findings,
    ),
    Metric(
        key="partner.clients",
        title="Active client companies",
        definition=(
            "Client companies marked active; rows give every client's health (its recorded score less 25 per "
            "overdue deadline and 5 per filing waiting for approval), its runs and run success in the period, "
            "overdue deadlines, filings waiting and the balance outstanding on its invoices."
        ),
        unit="count",
        source=("companies", "run_facts", "compliance_deadlines", "filing_approvals", "ca_client_invoices"),
        views=(PARTNER,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=partner.resolve_partner_clients,
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="partner.filings",
        title="Filings waiting for approval",
        definition="Filings prepared for the firm's clients that wait for an approval now, by filing type.",
        unit="count",
        source=("filing_approvals",),
        views=(PARTNER,),
        data_quality_rule=SNAPSHOT_QUALITY_RULE,
        resolver=partner.resolve_partner_filings,
        thresholds=Thresholds(watch=5.0, act=15.0, direction="above"),
        comparable=False,
        kind="snapshot",
    ),
    Metric(
        key="partner.billing",
        title="Invoiced to clients",
        definition=(
            "What the firm invoiced its clients in the period (issued invoices, not drafts or voided ones); rows "
            "give, per currency, the amount invoiced, the payments received in the period and the balance "
            "outstanding now."
        ),
        unit="inr",
        source=("ca_client_invoices", "ca_client_payments"),
        views=(PARTNER,),
        data_quality_rule="measured from the firm's invoices and payments; no_data when no invoice is recorded.",
        resolver=partner.resolve_partner_billing,
        comparable=False,
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
            "value.money_saved",
            "value.hours_saved",
        ),
        charts=("runs.volume", "agents.by_status", "runs.success_rate", "runs.confidence", "cost.spend",
                "value.money_saved"),
    ),
    FINANCE: View(
        key=FINANCE,
        title="Finance",
        tiles=("cost.spend", "cost.per_run", "cost.forecast", "runs.volume", "runs.success_rate",
               "runs.straight_through_rate", "cost.data_quality", "value.money_saved", "value.roi"),
        charts=("cost.spend", "cost.by_agent", "cost.by_model", "cost.forecast", "cost.budgets", "tokens.by_unit",
                "value.money_saved", "agents.by_status"),
    ),
    OPERATIONS: View(
        key=OPERATIONS,
        title="Operations",
        tiles=(
            "workflows.runs",
            "workflows.stalled",
            "runs.success_rate",
            "runs.straight_through_rate",
            "runs.latency",
            "approvals.pending",
            "connectors.unhealthy",
        ),
        charts=("workflows.runs", "runs.volume", "connectors.unhealthy", "agents.by_status"),
    ),
    HR: View(
        key=HR,
        title="HR",
        tiles=("runs.volume", "runs.success_rate", "runs.straight_through_rate", "approvals.pending",
               "approvals.cycle_time", "hr.pt_returns"),
        charts=("runs.volume", "hr.pt_returns", "agents.by_status"),
    ),
    MARKETING: View(
        key=MARKETING,
        title="Marketing",
        tiles=("marketing.leads", "marketing.content_drafts", "marketing.draft_cycle_time", "runs.volume",
               "approvals.pending"),
        charts=("marketing.leads", "marketing.content_drafts"),
    ),
    COMMERCE: View(
        key=COMMERCE,
        title="Commerce",
        tiles=("commerce.orders", "commerce.webhook_failures"),
        charts=("commerce.funnel", "commerce.orders"),
    ),
    COMPLIANCE: View(
        key=COMPLIANCE,
        title="Compliance",
        tiles=(
            "compliance.audit_integrity",
            "compliance.approvals_decided",
            "compliance.rubber_stamps",
            "compliance.dsar_open",
            "compliance.dsar_completion_time",
            "compliance.attestations_expiring",
            "compliance.change_log",
            "compliance.security_events",
            "compliance.guardrail_events",
        ),
        charts=("compliance.approvals_decided", "compliance.change_log", "compliance.security_events"),
    ),
    ENGINEERING: View(
        key=ENGINEERING,
        title="Engineering",
        tiles=(
            "runs.success_rate",
            "runs.latency",
            "engineering.gateway_issues",
            "engineering.slow_spans",
            "engineering.eval_pass_rate",
            "engineering.prompt_edits",
            "engineering.tool_usage",
            "runs.confidence",
            "quality.feedback",
        ),
        charts=("engineering.slow_spans", "engineering.tool_usage", "runs.volume", "runs.confidence",
                "quality.feedback"),
    ),
    BACK_OFFICE: View(
        key=BACK_OFFICE,
        title="Back office",
        tiles=(
            "backoffice.deadlines",
            "backoffice.txn_findings",
            "approvals.pending",
            "approvals.cycle_time",
            "runs.volume",
            "runs.success_rate",
        ),
        charts=("backoffice.deadlines", "backoffice.txn_findings", "runs.volume"),
    ),
    DOMAIN: View(
        key=DOMAIN,
        title="My domain",
        tiles=(
            "runs.volume",
            "runs.success_rate",
            "runs.straight_through_rate",
            "runs.latency",
            "runs.confidence",
            "approvals.pending",
            "approvals.cycle_time",
            "quality.feedback",
        ),
        charts=("runs.volume", "runs.success_rate", "runs.confidence", "quality.feedback", "agents.by_status"),
    ),
    PARTNER: View(
        key=PARTNER,
        title="Partner",
        tiles=("partner.clients", "backoffice.deadlines", "partner.filings", "partner.billing"),
        charts=("partner.clients", "backoffice.deadlines", "partner.filings", "partner.billing"),
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


async def _complete_days_compare(metric: Metric, ctx: MetricContext, days: int) -> tuple[Any, Any]:
    """The metric over the period's first ``days`` complete days, and over as many days of the comparison window."""
    if days <= 0 or ctx.compare is None:
        return None, None
    current = Period(ctx.period.start, ctx.period.start + timedelta(days=days - 1), ctx.period.granularity)
    previous = Period(ctx.compare.start, ctx.compare.start + timedelta(days=days - 1), ctx.compare.granularity)
    part = await metric.resolver(replace(ctx, period=current, compare=previous))
    current_value = part.get("value") if part.get("data_quality") != "no_data" else None
    return current_value, part.get("compare_value")


async def resolve(metric: Metric, ctx: MetricContext) -> dict[str, Any]:
    """The metric's response for ``ctx``."""
    from core.insights.telemetry import metric_seconds, timed

    with timed(metric_seconds, metric.key):
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
        compare: dict[str, Any] = ctx.compare.range_dict()
        today = ctx.today
        if ctx.period.start <= today <= ctx.period.end:
            # Today is still running: compare complete days with as many days of the comparison window.
            days = (today - ctx.period.start).days
            current_value, compare_value = await _complete_days_compare(metric, ctx, days)
            compare.update(basis="complete_days", complete_days=days, current_value=current_value)
            response["compare_value"] = compare_value
        else:
            compare["basis"] = "full_period"
            response["compare_value"] = part.pop("compare_value", None)
        response["compare"] = compare
    part.pop("compare_value", None)
    if state is not None:
        response["state"] = state
    for name in ("series", "rows", "hint", "conversion", "lower_bound", "confirmation"):
        if part.get(name) is not None:
            response[name] = part[name]
    if response["data_quality"] not in DATA_QUALITIES:
        raise ValueError(f"{metric.key}: data_quality {response['data_quality']!r} is not one of {DATA_QUALITIES}")
    return response
