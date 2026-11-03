# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Insights explorers: one declaration per Level 2 table (columns, filters, readers, resolver).

``ui/src/lib/insights/explorerKeys.generated.json`` lists the keys
(``scripts/generate_insights_metric_keys.py`` writes it with the metric keys).
"""

from __future__ import annotations

from core.insights import explore_sources as src
from core.insights.explorers import DOMAIN_HEADS, Column, Explorer
from core.insights.run_facts import ENTRYPOINTS, STATUSES

# Developers read their domain's agents, runs and quality; auditors read every run, approval and the audit log.
ENGINEERS: frozenset[str] = frozenset({"developer"})
AUDITORS: frozenset[str] = frozenset({"auditor"})
# Domain leads and analysts read their assigned domain's agents, runs, approvals, cost and quality.
ANALYSTS: frozenset[str] = frozenset({"domain_lead", "analyst"})

RUN_STATUSES = tuple(sorted(STATUSES))
RUN_ENTRYPOINTS = tuple(sorted(e for e in ENTRYPOINTS if e != "debug"))
AGENT_STATUSES = ("shadow", "active", "paused", "retired")
APPROVAL_STATUSES = ("pending", "decided", "rejected", "expired")
DRIVERS = ("volume", "price", "mix", "none")

_EXPLORERS: tuple[Explorer, ...] = (
    Explorer(
        key="agents",
        title="Agents",
        definition=(
            "Every agent you can see, with its runs, success rate, p95 latency and spend in the period, its last "
            "run, how long it has been idle, its shadow promotion checks and its learned rules."
        ),
        source=("agents", "insights_daily", "run_facts", "spend_usage_rollups", "agent_cost_ledger"),
        readers=DOMAIN_HEADS | ENGINEERS | ANALYSTS,
        default_sort="spend",
        resolver=src.explore_agents,
        columns=(
            Column("agent", "Agent", "text", filter="text"),
            Column("agent_id", "Agent id", "id", sortable=False, filter="enum"),
            Column("agent_type", "Type", "text", filter="enum"),
            Column("domain", "Domain", "text", filter="enum"),
            Column("status", "Status", "status", filter="enum", options=AGENT_STATUSES),
            Column("runs", "Runs", "count", filter="min"),
            Column("success_rate", "Success rate", "percent", "Completed over completed plus failed.", filter="min"),
            Column("p95_ms", "p95 latency", "duration_ms"),
            Column(
                "spend", "Spend", "money", "In the reporting currency, from the period's cost source.", filter="min"
            ),
            Column("last_run_at", "Last run", "datetime"),
            Column(
                "idle_days",
                "Idle days",
                "count",
                "Days since the last run (or since creation when it never ran in the last 90 days).",
                filter="min",
            ),
            Column("shadow_samples", "Scored samples", "count"),
            Column("shadow_required", "Samples required", "count"),
            Column("shadow_accuracy", "Shadow accuracy", "percent"),
            Column("accuracy_floor", "Accuracy floor", "percent"),
            Column("promotion_eligible", "Eligible to promote", "bool", filter="bool"),
            Column("promotion_reason", "Promotion check", "text", sortable=False),
            Column("learned_rules", "Learned rules", "count", filter="min"),
        ),
    ),
    Explorer(
        key="runs",
        title="Runs",
        definition=(
            "One row per agent run that started in the period (at most its last 31 days), newest first. An "
            "approval resuming a paused run is its own row (entry point hitl_resume). Debugging-console runs "
            "are not shown. Cost is what the runtime reported, in USD."
        ),
        source=("run_facts",),
        readers=DOMAIN_HEADS | ENGINEERS | AUDITORS | ANALYSTS,
        default_sort="started_at",
        resolver=src.explore_runs,
        columns=(
            Column("started_at", "Started", "datetime"),
            Column("agent", "Agent", "text", sortable=False),
            Column("agent_id", "Agent id", "id", sortable=False, filter="enum"),
            Column("agent_type", "Type", "text", filter="enum"),
            Column("domain", "Domain", "text", filter="enum"),
            Column("task_type", "Task", "text", filter="text"),
            Column("entrypoint", "Entry point", "text", filter="enum", options=RUN_ENTRYPOINTS),
            Column("status", "Status", "status", filter="enum", options=RUN_STATUSES),
            Column("duration_ms", "Duration", "duration_ms", filter="min"),
            Column("tokens_total", "Tokens", "tokens", filter="min"),
            Column("cost_usd", "Cost (USD)", "money", filter="min"),
            Column("hitl_required", "Needed approval", "bool", filter="bool"),
            Column("error_class", "Error", "text", filter="text"),
            Column("data_quality", "Data quality", "text", filter="enum", options=("measured", "seeded")),
        ),
    ),
    Explorer(
        key="workflows",
        title="Workflows",
        definition="One row per workflow run that started in the period, with its status, duration and steps.",
        source=("workflow_runs", "workflow_definitions"),
        readers=frozenset({"coo"}),
        default_sort="started_at",
        resolver=src.explore_workflows,
        columns=(
            Column("started_at", "Started", "datetime"),
            Column("workflow", "Workflow", "text", filter="text"),
            Column("workflow_id", "Workflow id", "id", sortable=False, filter="enum"),
            Column("domain", "Domain", "text", filter="enum"),
            Column("status", "Status", "status", filter="enum"),
            Column("duration_ms", "Duration", "duration_ms", filter="min"),
            Column("steps_completed", "Steps done", "count"),
            Column("steps_total", "Steps", "count"),
            Column(
                "past_timeout", "Past its timeout", "bool", "Timed out, or finished after its timeout.", filter="bool"
            ),
        ),
    ),
    Explorer(
        key="approvals",
        title="Approvals",
        definition=(
            "One row per approval requested in the period: who it waited on, what was decided, by whom and how "
            "long it took. A pending approval past its expiry shows as expired."
        ),
        source=("hitl_queue",),
        readers=DOMAIN_HEADS | AUDITORS | ANALYSTS,
        default_sort="created_at",
        resolver=src.explore_approvals,
        columns=(
            Column("created_at", "Requested", "datetime"),
            Column("title", "Approval", "text", filter="text"),
            Column("agent", "Agent", "text", sortable=False),
            Column("agent_id", "Agent id", "id", sortable=False, filter="enum"),
            Column("trigger_type", "Trigger", "text", filter="enum"),
            Column("priority", "Priority", "text", filter="enum"),
            Column("assignee_role", "Waits on", "text", filter="enum"),
            Column("status", "Status", "status", filter="enum", options=APPROVAL_STATUSES),
            Column("decision", "Decision", "text", filter="enum"),
            Column("decided_by", "Decided by", "text", sortable=False),
            Column("decision_at", "Decided", "datetime"),
            Column("cycle_time_ms", "Time to decide", "duration_ms", filter="min"),
            Column("expires_at", "Expires", "datetime"),
        ),
    ),
    Explorer(
        key="cost",
        title="Cost and drivers",
        definition=(
            "Spend per agent in the period against the same number of days before it, with the change split "
            "into volume, price and mix and the driver named. Administrators and the CFO see every agent; "
            "other roles see their domains' agents."
        ),
        source=("spend_usage_rollups", "agent_cost_ledger", "insights_daily", "run_facts"),
        readers=DOMAIN_HEADS | ANALYSTS,
        default_sort="spend",
        resolver=src.explore_cost,
        columns=(
            Column("agent", "Agent", "text", filter="text"),
            Column("agent_id", "Agent id", "id", sortable=False, filter="enum"),
            Column("domain", "Domain", "text", filter="enum"),
            Column("spend", "Spend", "money", filter="min"),
            Column("prior_spend", "Previous spend", "money"),
            Column("change", "Change", "money"),
            Column("change_pct", "Change %", "percent"),
            Column("volume_effect", "Volume effect", "money", "Change in quantity at the earlier unit price."),
            Column("price_effect", "Price effect", "money", "Change in unit price on the earlier quantity."),
            Column("mix_effect", "Mix effect", "money", "New or dropped models and usage types, and the rest."),
            Column("driver", "Driver", "text", filter="enum", options=DRIVERS),
            Column("basis", "Quantity basis", "text", filter="enum", options=("metered", "runs")),
            Column("runs", "Runs", "count"),
            Column("cost_per_run", "Cost per run", "money"),
        ),
    ),
    Explorer(
        key="value",
        title="Value and savings",
        definition=(
            "The money-saved estimate line by line: completed and failed tasks of each agent type in the period, "
            "the baseline minutes per task, the role and its hourly rate, hours saved, gross saved and error cost, "
            "and whether a tenant administrator has confirmed the baseline and rate. Every figure is an estimate."
        ),
        source=("run_facts", "insights_daily", "value_baselines", "value_rates"),
        readers=frozenset({"cfo"}),
        default_sort="gross_saved",
        resolver=src.explore_value,
        columns=(
            Column("agent_type", "Agent type", "text", filter="text"),
            Column("task_type", "Task", "text", filter="text"),
            Column("completed", "Completed", "count", filter="min"),
            Column("failed", "Failed", "count"),
            Column("baseline_minutes", "Minutes per task", "number"),
            Column("role", "Done by hand by", "text", filter="enum"),
            Column("hourly_rate", "Hourly rate", "money"),
            Column("hours_saved", "Hours saved", "number", filter="min"),
            Column("gross_saved", "Gross saved", "money"),
            Column("error_cost", "Error cost", "money"),
            Column("baseline_source", "Baseline source", "text", filter="enum", options=("seeded", "tenant")),
            Column("confirmed", "Confirmed", "bool", filter="bool"),
            Column("priced", "Priced", "bool", filter="bool"),
        ),
    ),
    Explorer(
        key="quality",
        title="Quality and learning",
        definition=(
            "One row per learned rule (a prompt amendment applied from feedback), with the feedback its agent "
            "received in the period. Revoking a rule removes it from the agent's prompt."
        ),
        source=("agents", "agent_feedback"),
        readers=DOMAIN_HEADS | ENGINEERS | ANALYSTS,
        default_sort="negative_feedback",
        resolver=src.explore_quality,
        columns=(
            Column("agent", "Agent", "text", filter="text"),
            Column("agent_id", "Agent id", "id", sortable=False, filter="enum"),
            Column("domain", "Domain", "text", filter="enum"),
            Column("status", "Agent status", "status", filter="enum", options=AGENT_STATUSES),
            Column("rule_number", "Rule #", "count"),
            Column("rule", "Learned rule", "text", sortable=False, filter="text"),
            Column("rules_on_agent", "Rules on agent", "count"),
            Column("feedback", "Feedback", "count"),
            Column("negative_feedback", "Negative feedback", "count", "Thumbs down and corrections.", filter="min"),
            Column("unapplied_feedback", "Not applied", "count", filter="min"),
        ),
    ),
    Explorer(
        key="connectors",
        title="Connectors",
        definition=(
            "Every connector with its status, the worst health across the companies it is configured for, its "
            "last health check and the agents that use it. A snapshot of now."
        ),
        source=("connectors", "connector_configs", "agents"),
        readers=frozenset({"coo"}),
        default_sort="health",
        default_order="asc",
        uses_period=False,
        resolver=src.explore_connectors,
        columns=(
            Column("connector", "Connector", "text", filter="text"),
            Column("category", "Category", "text", filter="enum"),
            Column("status", "Status", "status", filter="enum"),
            Column(
                "health",
                "Health",
                "status",
                filter="enum",
                options=("healthy", "degraded", "unhealthy", "unknown", "not_configured"),
            ),
            Column("configured_companies", "Companies", "count"),
            Column("sync_failing", "Sync failing", "bool", filter="bool"),
            Column("last_health_check", "Last health check", "datetime"),
            Column("dependent_agents", "Agents using it", "count", filter="min"),
        ),
    ),
    Explorer(
        key="audit",
        title="Security and audit",
        definition=(
            "The audit log for the period: what was done, by whom, to what, and whether it succeeded, newest "
            "first. Details are not shown here; open the audit log for a row's full record."
        ),
        source=("audit_log",),
        readers=AUDITORS,
        default_sort="created_at",
        resolver=src.explore_audit,
        columns=(
            Column("created_at", "When", "datetime"),
            Column("category", "Category", "text", filter="enum"),
            Column("event_type", "Event", "text", filter="text"),
            Column("actor_type", "Actor type", "text", filter="enum"),
            Column("actor", "Actor", "text", sortable=False),
            Column("resource_type", "Resource", "text", filter="enum"),
            Column("resource_id", "Resource id", "id", sortable=False),
            Column("action", "Action", "text", filter="text"),
            Column("outcome", "Outcome", "status", filter="enum"),
            Column("signed", "Signed", "bool", sortable=False),
        ),
    ),
)

EXPLORERS: dict[str, Explorer] = {explorer.key: explorer for explorer in _EXPLORERS}


def explorer_keys() -> list[str]:
    return sorted(EXPLORERS)


def readable_explorers(role: str, *, is_admin: bool) -> list[Explorer]:
    """The explorers a caller may open, in display order."""
    return [explorer for explorer in _EXPLORERS if explorer.readable_by(role, is_admin=is_admin)]
