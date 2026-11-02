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
 */
export type ChartKind = "line" | "stacked" | "hbar" | "bullet" | "forecast";

export interface MetricPresentation {
  chart: ChartKind;
  /** Where the tile's one action goes. Every target is a route the view's roles can open. */
  action: { to: string; labelKey: string };
}

export const METRIC_PRESENTATION: Record<string, MetricPresentation> = {
  "agents.by_status": { chart: "hbar", action: { to: "/dashboard/agents", labelKey: "insights.actions.openAgents" } },
  "runs.volume": { chart: "stacked", action: { to: "/dashboard/agents", labelKey: "insights.actions.openAgents" } },
  "runs.success_rate": { chart: "line", action: { to: "/dashboard/agents", labelKey: "insights.actions.openAgents" } },
  "runs.straight_through_rate": {
    chart: "line",
    action: { to: "/dashboard/approvals", labelKey: "insights.actions.openApprovals" },
  },
  "runs.latency": { chart: "line", action: { to: "/dashboard/agents", labelKey: "insights.actions.openAgents" } },
  "approvals.pending": { chart: "line", action: { to: "/dashboard/approvals", labelKey: "insights.actions.openApprovals" } },
  "approvals.cycle_time": {
    chart: "line",
    action: { to: "/dashboard/approvals", labelKey: "insights.actions.openApprovals" },
  },
  "cost.spend": { chart: "line", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
  "cost.per_run": { chart: "line", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
  "cost.by_agent": { chart: "hbar", action: { to: "/dashboard/agents", labelKey: "insights.actions.openAgents" } },
  "cost.by_model": { chart: "hbar", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
  "cost.forecast": { chart: "forecast", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
  "cost.budgets": { chart: "bullet", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
  "tokens.by_unit": { chart: "stacked", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
  "cost.data_quality": { chart: "hbar", action: { to: "/dashboard/costs", labelKey: "insights.actions.openCosts" } },
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
  ],
};

/** Views each role may open; mirrors the server-side check (admin both, cfo finance). */
export const ROLE_VIEWS: Record<string, readonly InsightsViewKey[]> = {
  admin: ["executive", "finance"],
  cfo: ["finance"],
};

/** Insights is offered to these roles; the route guard and the nav entry use the same list. */
export const INSIGHTS_ROLES: readonly string[] = ["admin", "cfo"];

export function defaultViewForRole(role: string | null | undefined): InsightsViewKey {
  return role === "cfo" ? "finance" : "executive";
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
