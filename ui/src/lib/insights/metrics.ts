// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * How the Insights page presents each registry metric key: the chart form
 * and the one action a tile offers. Keys must exist in the backend registry
 * (core/insights/registry.py); a unit test checks this map against
 * metricKeys.generated.json, which is generated from that registry.
 */
import type { InsightsViewKey } from "./api";

/**
 * Chart forms, chosen by the shape of the data:
 * - line: one quantity over time
 * - stacked: parts over time (series carrying `group`)
 * - hbar: items ranked by value, sorted descending
 * - bullet: actual against a threshold (budgets)
 * - forecast: point estimate with a low/high whisker
 * - waterfall: gross to net, each step from the running total
 * - funnel: stages in their order, with the share kept from one stage to the next
 */
export type ChartKind = "line" | "stacked" | "hbar" | "bullet" | "forecast" | "waterfall" | "funnel";

export interface MetricPresentation {
  chart: ChartKind;
  /** Where the tile's one action goes. Every target is a route the view's roles can open. */
  action: { to: string; labelKey: string };
}

const RUNS = "/dashboard/insights/explore/runs";
const APPROVALS = "/dashboard/insights/explore/approvals";
const AGENTS = "/dashboard/insights/explore/agents";
const COST = "/dashboard/insights/explore/cost";
const VALUE_PAGE = "/dashboard/insights/value";
const WORKFLOWS = "/dashboard/insights/explore/workflows";
const CONNECTORS = "/dashboard/insights/explore/connectors";
const ORDERS_PAGE = "/dashboard/orders";
const AUDIT = "/dashboard/insights/explore/audit";
const QUALITY = "/dashboard/insights/explore/quality";
const COMPANIES_PAGE = "/dashboard/companies";
const TRANSACTIONS_PAGE = "/dashboard/transactions";

/** Every tile drills to the explorer rows behind it, with the filter or sort that produced the number. */
export const METRIC_PRESENTATION: Record<string, MetricPresentation> = {
  "agents.by_status": { chart: "hbar", action: { to: AGENTS, labelKey: "insights.actions.exploreAgents" } },
  "runs.volume": { chart: "stacked", action: { to: RUNS, labelKey: "insights.actions.exploreRuns" } },
  "runs.success_rate": {
    chart: "line",
    action: { to: `${RUNS}?filter%5Bstatus%5D=failed`, labelKey: "insights.actions.exploreFailedRuns" },
  },
  "runs.straight_through_rate": {
    chart: "line",
    action: { to: `${RUNS}?filter%5Bhitl_required%5D=true`, labelKey: "insights.actions.exploreApprovedRuns" },
  },
  "runs.latency": {
    chart: "line",
    action: { to: `${RUNS}?sort=duration_ms&order=desc`, labelKey: "insights.actions.exploreSlowRuns" },
  },
  "runs.confidence": {
    chart: "line",
    action: { to: `${RUNS}?filter%5Bhitl_required%5D=true`, labelKey: "insights.actions.exploreApprovedRuns" },
  },
  "quality.feedback": { chart: "hbar", action: { to: QUALITY, labelKey: "insights.actions.exploreQuality" } },
  "backoffice.deadlines": {
    chart: "hbar",
    action: { to: COMPANIES_PAGE, labelKey: "insights.actions.openCompanies" },
  },
  "partner.clients": { chart: "hbar", action: { to: COMPANIES_PAGE, labelKey: "insights.actions.openCompanies" } },
  "partner.filings": { chart: "hbar", action: { to: COMPANIES_PAGE, labelKey: "insights.actions.openCompanies" } },
  "partner.billing": { chart: "hbar", action: { to: COMPANIES_PAGE, labelKey: "insights.actions.openCompanies" } },
  "backoffice.txn_findings": {
    chart: "line",
    action: { to: TRANSACTIONS_PAGE, labelKey: "insights.actions.openTransactions" },
  },
  "approvals.pending": {
    chart: "line",
    action: { to: `${APPROVALS}?filter%5Bstatus%5D=pending`, labelKey: "insights.actions.explorePending" },
  },
  "approvals.cycle_time": {
    chart: "line",
    action: { to: `${APPROVALS}?sort=cycle_time_ms&order=desc`, labelKey: "insights.actions.exploreApprovals" },
  },
  "cost.spend": { chart: "line", action: { to: COST, labelKey: "insights.actions.exploreCost" } },
  "cost.per_run": {
    chart: "line",
    action: { to: `${COST}?sort=cost_per_run&order=desc`, labelKey: "insights.actions.exploreCost" },
  },
  "cost.by_agent": { chart: "hbar", action: { to: COST, labelKey: "insights.actions.exploreCost" } },
  "cost.by_model": { chart: "hbar", action: { to: COST, labelKey: "insights.actions.exploreCost" } },
  "cost.forecast": { chart: "forecast", action: { to: COST, labelKey: "insights.actions.exploreCost" } },
  "cost.budgets": { chart: "bullet", action: { to: COST, labelKey: "insights.actions.exploreCost" } },
  "tokens.by_unit": {
    chart: "stacked",
    action: { to: `${RUNS}?sort=tokens_total&order=desc`, labelKey: "insights.actions.exploreRuns" },
  },
  "cost.data_quality": { chart: "hbar", action: { to: COST, labelKey: "insights.actions.exploreCost" } },
  "value.money_saved": { chart: "waterfall", action: { to: VALUE_PAGE, labelKey: "insights.actions.openValue" } },
  "value.hours_saved": { chart: "hbar", action: { to: VALUE_PAGE, labelKey: "insights.actions.openValue" } },
  "value.roi": { chart: "line", action: { to: VALUE_PAGE, labelKey: "insights.actions.openValue" } },
  "workflows.runs": { chart: "line", action: { to: WORKFLOWS, labelKey: "insights.actions.exploreWorkflows" } },
  "workflows.stalled": {
    chart: "hbar",
    action: { to: `${WORKFLOWS}?filter%5Bpast_timeout%5D=true`, labelKey: "insights.actions.exploreStalled" },
  },
  "connectors.unhealthy": {
    chart: "hbar",
    action: { to: `${CONNECTORS}?filter%5Bhealth%5D=unhealthy`, labelKey: "insights.actions.exploreConnectors" },
  },
  "hr.pt_returns": { chart: "line", action: { to: AGENTS, labelKey: "insights.actions.exploreAgents" } },
  "marketing.leads": { chart: "line", action: { to: AGENTS, labelKey: "insights.actions.exploreAgents" } },
  "marketing.content_drafts": { chart: "line", action: { to: AGENTS, labelKey: "insights.actions.exploreAgents" } },
  "marketing.draft_cycle_time": { chart: "line", action: { to: AGENTS, labelKey: "insights.actions.exploreAgents" } },
  "commerce.funnel": { chart: "funnel", action: { to: ORDERS_PAGE, labelKey: "insights.actions.openOrders" } },
  "commerce.orders": { chart: "line", action: { to: ORDERS_PAGE, labelKey: "insights.actions.openOrders" } },
  "commerce.webhook_failures": { chart: "hbar", action: { to: ORDERS_PAGE, labelKey: "insights.actions.openOrders" } },
  "compliance.audit_integrity": { chart: "hbar", action: { to: AUDIT, labelKey: "insights.actions.exploreAudit" } },
  "compliance.approvals_decided": {
    chart: "line",
    action: { to: APPROVALS, labelKey: "insights.actions.exploreApprovals" },
  },
  "compliance.rubber_stamps": {
    chart: "line",
    action: { to: `${APPROVALS}?sort=cycle_time_ms&order=asc`, labelKey: "insights.actions.exploreApprovals" },
  },
  "compliance.dsar_open": {
    chart: "hbar",
    action: { to: `${AUDIT}?filter%5Bcategory%5D=dsar`, labelKey: "insights.actions.exploreAudit" },
  },
  "compliance.dsar_completion_time": {
    chart: "line",
    action: { to: `${AUDIT}?filter%5Bcategory%5D=dsar`, labelKey: "insights.actions.exploreAudit" },
  },
  "compliance.attestations_expiring": {
    chart: "hbar",
    action: { to: `${AUDIT}?filter%5Bcategory%5D=residency_attestation`, labelKey: "insights.actions.exploreAudit" },
  },
  "compliance.change_log": { chart: "hbar", action: { to: AUDIT, labelKey: "insights.actions.exploreAudit" } },
  "engineering.gateway_issues": {
    chart: "hbar",
    action: { to: `${RUNS}?filter%5Bstatus%5D=failed`, labelKey: "insights.actions.exploreRuns" },
  },
  "engineering.slow_spans": {
    chart: "hbar",
    action: { to: `${RUNS}?sort=duration_ms&order=desc`, labelKey: "insights.actions.exploreRuns" },
  },
  "engineering.eval_pass_rate": { chart: "hbar", action: { to: QUALITY, labelKey: "insights.actions.exploreQuality" } },
  "engineering.prompt_edits": { chart: "hbar", action: { to: AGENTS, labelKey: "insights.actions.exploreAgents" } },
  "engineering.tool_usage": {
    chart: "hbar",
    action: { to: `${CONNECTORS}?sort=calls_7d&order=desc`, labelKey: "insights.actions.exploreConnectors" },
  },
  "compliance.guardrail_events": { chart: "hbar", action: { to: AUDIT, labelKey: "insights.actions.exploreAudit" } },
  "compliance.security_events": { chart: "line", action: { to: AUDIT, labelKey: "insights.actions.exploreAudit" } },
};

/** Expected metric keys per view (registry views; the API decides what it returns). */
export const VIEW_METRICS: Record<InsightsViewKey, readonly string[]> = {
  executive: [
    "agents.by_status",
    "runs.volume",
    "runs.success_rate",
    "runs.straight_through_rate",
    "runs.latency",
    "approvals.pending",
    "approvals.cycle_time",
    "cost.spend",
    "value.money_saved",
    "value.hours_saved",
    "runs.confidence",
  ],
  finance: [
    "cost.spend",
    "cost.per_run",
    "cost.by_agent",
    "cost.by_model",
    "cost.forecast",
    "cost.budgets",
    "tokens.by_unit",
    "cost.data_quality",
    "runs.volume",
    "runs.success_rate",
    "runs.straight_through_rate",
    "agents.by_status",
    "value.money_saved",
    "value.roi",
  ],
  operations: ["workflows.runs", "workflows.stalled", "runs.success_rate", "runs.straight_through_rate",
    "runs.latency", "approvals.pending", "connectors.unhealthy", "runs.volume", "agents.by_status"],
  hr: ["runs.volume", "runs.success_rate", "runs.straight_through_rate", "approvals.pending", "approvals.cycle_time",
    "hr.pt_returns", "agents.by_status"],
  marketing: ["marketing.leads", "marketing.content_drafts", "marketing.draft_cycle_time", "runs.volume",
    "approvals.pending"],
  commerce: ["commerce.orders", "commerce.webhook_failures", "commerce.funnel"],
  compliance: [
    "compliance.audit_integrity",
    "compliance.approvals_decided",
    "compliance.rubber_stamps",
    "compliance.dsar_open",
    "compliance.dsar_completion_time",
    "compliance.attestations_expiring",
    "compliance.change_log",
    "compliance.security_events",
    "compliance.guardrail_events",
  ],
  engineering: [
    "runs.success_rate",
    "runs.latency",
    "engineering.gateway_issues",
    "engineering.slow_spans",
    "engineering.eval_pass_rate",
    "engineering.prompt_edits",
    "engineering.tool_usage",
    "runs.volume",
    "runs.confidence",
    "quality.feedback",
  ],
  back_office: [
    "backoffice.deadlines",
    "backoffice.txn_findings",
    "approvals.pending",
    "approvals.cycle_time",
    "runs.volume",
    "runs.success_rate",
  ],
  domain: [
    "runs.volume",
    "runs.success_rate",
    "runs.straight_through_rate",
    "runs.latency",
    "runs.confidence",
    "approvals.pending",
    "approvals.cycle_time",
    "quality.feedback",
    "agents.by_status",
  ],
  partner: ["partner.clients", "backoffice.deadlines", "partner.filings", "partner.billing"],
};

/** Views each role may open; mirrors the server-side check (admin both, cfo finance). */
export const ROLE_VIEWS: Record<string, readonly InsightsViewKey[]> = {
  admin: [
    "executive",
    "finance",
    "operations",
    "hr",
    "marketing",
    "commerce",
    "compliance",
    "engineering",
    "back_office",
    "domain",
    "partner",
  ],
  cfo: ["finance", "partner"],
  coo: ["operations", "partner"],
  chro: ["hr"],
  cmo: ["marketing"],
  merchant: ["commerce"],
  auditor: ["compliance", "partner"],
  developer: ["engineering"],
  domain_lead: ["domain"],
  analyst: ["domain"],
};

/**
 * Insights is offered to these roles; the route guard and the nav entry use the same list. Domain leads and
 * analysts land on the Domain view, limited to their assigned domain.
 */
export const INSIGHTS_ROLES: readonly string[] = [
  "admin",
  "cfo",
  "chro",
  "cmo",
  "coo",
  "merchant",
  "auditor",
  "developer",
  "domain_lead",
  "analyst",
];

const DEFAULT_VIEW: Record<string, InsightsViewKey> = {
  cfo: "finance",
  coo: "operations",
  chro: "hr",
  cmo: "marketing",
  merchant: "commerce",
  auditor: "compliance",
  developer: "engineering",
  domain_lead: "domain",
  analyst: "domain",
};

export function defaultViewForRole(role: string | null | undefined): InsightsViewKey {
  return DEFAULT_VIEW[role ?? ""] ?? "executive";
}

export interface DataShape {
  /** The series has at least one drawable point. */
  hasSeries: boolean;
  /** Series points carry a `group`. */
  hasGroups: boolean;
  /** The response carries rows. */
  hasRows: boolean;
}

/**
 * The chart form for a metric: the key's own form, adjusted to the data the
 * API actually sent (a time chart without a series falls back to ranking its
 * rows; "parts over time" without groups is one quantity, so a line). Keys
 * this build does not know yet get a form from the data shape alone.
 */
export function chartKindFor(key: string, shape: DataShape): ChartKind {
  const known = METRIC_PRESENTATION[key]?.chart;
  if (known === "line" || known === "stacked" || known === undefined) {
    if (!shape.hasSeries && shape.hasRows) return "hbar";
    if (known === undefined) return shape.hasGroups ? "stacked" : "line";
    if (known === "stacked" && !shape.hasGroups) return "line";
    return known;
  }
  return known;
}

/** Time charts tabulate their series; ranked, budget and forecast charts tabulate their rows. */
export function tableFromSeries(kind: ChartKind): boolean {
  return kind === "line" || kind === "stacked";
}
