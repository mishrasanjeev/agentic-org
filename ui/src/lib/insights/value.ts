// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for money saved (api/v1/insights_value.py) and Insights alerts
 * (api/v1/insights_alerts.py).
 *
 * The backend decides who may read (administrators and the CFO) and who may
 * change the assumptions (an active human administrator); every change is
 * audited there. Savings stay hidden until every baseline and rate the period
 * uses is confirmed, so the page shows what is still unconfirmed.
 */
import api from "@/lib/api";

export interface ValueBaseline {
  id: string;
  agent_type: string;
  task_type: string;
  baseline_minutes: number;
  role: string;
  setup_cost: number | null;
  source: "seeded" | "tenant" | string;
  confirmed: boolean;
  confirmed_at: string | null;
  updated_at: string | null;
}

export interface ValueRate {
  id: string;
  role: string;
  hourly_cost: number;
  currency: "INR" | "USD" | string;
  source: "seeded" | "tenant" | string;
  citation: string | null;
  confirmed: boolean;
  confirmed_at: string | null;
  updated_at: string | null;
}

export interface WaterfallRow {
  label: "gross_saved" | "spend" | "review_cost" | "error_cost" | "net_saved" | string;
  value: number | null;
}

export interface ValueSummary {
  currency: string;
  confirmed: boolean;
  confidence: "high" | "medium" | "low" | string;
  completed_tasks: number;
  failed_tasks: number;
  unpriced_task_types: string[];
  hours_saved: number | null;
  waterfall: WaterfallRow[] | null;
  roi_percent: number | null;
  review_items: number;
  spend: number | null;
  hints: string[];
  unconfirmed: { baseline_ids: string[]; rate_ids: string[] };
}

export interface ValueLine {
  agent_type: string;
  task_type: string | null;
  completed: number;
  failed: number;
  baseline_minutes: number | null;
  role: string | null;
  hourly_rate: number | null;
  hours_saved: number | null;
  gross_saved: number | null;
  error_cost: number | null;
  baseline_source: string | null;
  confirmed: boolean;
  priced: boolean;
}

export interface ValueResponse {
  period: { from: string; to: string; granularity?: string };
  currency: string;
  baselines: ValueBaseline[];
  rates: ValueRate[];
  roi_floor: number;
  roi_floor_custom: boolean;
  summary: ValueSummary;
  can_edit: boolean;
  lines: ValueLine[];
}

export interface InsightsAlert {
  id: string;
  rule_key: string;
  metric_key: string | null;
  severity: "watch" | "act" | string;
  owner_role: string;
  title: string;
  detail: Record<string, unknown>;
  link: string | null;
  state: "open" | "acked" | "resolved" | string;
  fired_at: string | null;
  last_seen_at: string | null;
  acked_at: string | null;
}

export const valueApi = {
  async get(range: { from?: string; to?: string } = {}): Promise<ValueResponse> {
    const params: Record<string, string> = {};
    if (range.from) params.from = range.from;
    if (range.to) params.to = range.to;
    const { data } = await api.get("/insights/value", { params });
    return data as ValueResponse;
  },
  async editBaseline(
    id: string,
    patch: { baseline_minutes?: number; role?: string; setup_cost?: number | null },
  ): Promise<ValueBaseline> {
    const { data } = await api.patch(`/insights/value/baselines/${encodeURIComponent(id)}`, patch);
    return data as ValueBaseline;
  },
  async addBaseline(body: {
    agent_type: string;
    task_type?: string;
    baseline_minutes: number;
    role: string;
  }): Promise<ValueBaseline> {
    const { data } = await api.post("/insights/value/baselines", body);
    return data as ValueBaseline;
  },
  async editRate(id: string, patch: { hourly_cost?: number; currency?: string }): Promise<ValueRate> {
    const { data } = await api.patch(`/insights/value/rates/${encodeURIComponent(id)}`, patch);
    return data as ValueRate;
  },
  async addRate(body: { role: string; hourly_cost: number; currency: string }): Promise<ValueRate> {
    const { data } = await api.post("/insights/value/rates", body);
    return data as ValueRate;
  },
  async confirm(ids: { baseline_ids: string[]; rate_ids: string[] }) {
    const { data } = await api.post("/insights/value/confirm", ids);
    return data as { confirmed_baselines: string[]; confirmed_rates: string[] };
  },
  async setRoiFloor(roi_floor: number | null) {
    const { data } = await api.put("/insights/value/settings", { roi_floor });
    return data as { roi_floor: number | null };
  },
};

export const alertsApi = {
  async list(state: "open" | "acked" = "open"): Promise<InsightsAlert[]> {
    const { data } = await api.get("/insights/alerts", { params: { state } });
    const list = (data as { alerts?: unknown })?.alerts;
    return Array.isArray(list) ? (list as InsightsAlert[]) : [];
  },
  async ack(id: string): Promise<InsightsAlert> {
    const { data } = await api.post(`/insights/alerts/${encodeURIComponent(id)}/ack`);
    return data as InsightsAlert;
  },
};
