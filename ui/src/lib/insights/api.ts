// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for the Insights API (api/v1/insights.py).
 *
 * The backend is the authorization boundary: it decides which views a role
 * may read (403 insights_view_forbidden), whether Insights is on for the
 * tenant (404 insights_disabled) and whether a company id belongs to the
 * tenant (422). This module only shapes requests and responses.
 */
import api from "@/lib/api";

export type InsightsViewKey = "executive" | "finance";
export type DataQuality = "measured" | "estimated" | "seeded" | "no_data" | "mixed";
export type MetricUnit = "count" | "percent" | "duration_ms" | "usd" | "inr" | "tokens";
export type MetricState = "ok" | "watch" | "act";
export type ReportingCurrency = "INR" | "USD";
export type CompareMode = "previous" | "none";
export type ExportFormat = "csv" | "xlsx" | "json";

export const VIEW_KEYS: readonly InsightsViewKey[] = ["executive", "finance"];
export const EXPORT_FORMATS: readonly ExportFormat[] = ["csv", "xlsx", "json"];

export interface ReportingSettings {
  timezone: string;
  fiscal_year_start_month: number;
  currency: ReportingCurrency;
}

export interface InsightsStatus {
  enabled: boolean;
  views: InsightsViewKey[];
  platform_operator: boolean;
  settings?: ReportingSettings | null;
}

export interface MetricPeriod {
  from: string;
  to: string;
  granularity?: string;
}

export interface MetricPoint {
  t: string;
  value: number | string | null;
  group?: string | null;
}

export type MetricRow = Record<string, unknown>;

export interface MetricResponse {
  key: string;
  title: string;
  definition: string;
  unit: MetricUnit | string;
  currency?: ReportingCurrency | string | null;
  source: string;
  data_quality: DataQuality | string;
  computed_at: string;
  period: MetricPeriod;
  compare?: { from: string; to: string } | null;
  value?: number | string | null;
  compare_value?: number | string | null;
  state?: MetricState | null;
  series?: MetricPoint[] | null;
  rows?: MetricRow[] | null;
  hint?: string | null;
}

export interface NeedsYouItem {
  kind?: string;
  title: string;
  /** Age of the item; the API may send seconds, days or a preformatted string. */
  age_seconds?: number | null;
  age_days?: number | null;
  age?: string | number | null;
  link?: string | null;
  /** Metric state, or a budget state (ok / warn / breached). */
  state?: MetricState | "warn" | "breached" | string | null;
  detail?: string | null;
}

export interface InsightsViewResponse {
  view: InsightsViewKey;
  period: MetricPeriod;
  tiles: MetricResponse[];
  charts: MetricResponse[];
  needs_you: NeedsYouItem[];
}

/** Query filters shared by views, metrics and exports. */
export interface InsightsFilters {
  from?: string;
  to?: string;
  compare?: CompareMode;
  company_id?: string;
}

/** Drop empty filter values so the query string only carries what was chosen. */
export function filterParams(filters: InsightsFilters): Record<string, string> {
  const params: Record<string, string> = {};
  for (const [k, v] of Object.entries(filters)) {
    if (typeof v === "string" && v.trim() !== "") params[k] = v;
  }
  return params;
}

export function exportPath(metricKey: string): string {
  return `/insights/metrics/${encodeURIComponent(metricKey)}/export`;
}

/** The export URL, relative to the API base, with URL-encoded filters. */
export function exportUrl(metricKey: string, format: ExportFormat, filters: InsightsFilters): string {
  const qs = new URLSearchParams({ format, ...filterParams(filters) });
  return `${exportPath(metricKey)}?${qs.toString()}`;
}

export function exportFilename(metricKey: string, format: ExportFormat, filters: InsightsFilters): string {
  const from = filters.from || "start";
  const to = filters.to || "today";
  return `insights-${metricKey}-${from}-${to}.${format}`;
}

function normaliseStatus(data: unknown): InsightsStatus {
  const raw = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const views = Array.isArray(raw.views)
    ? (raw.views.filter((v) => VIEW_KEYS.includes(v as InsightsViewKey)) as InsightsViewKey[])
    : [];
  const settings = raw.settings && typeof raw.settings === "object" ? (raw.settings as ReportingSettings) : null;
  return {
    enabled: raw.enabled === true,
    views,
    platform_operator: raw.platform_operator === true,
    settings,
  };
}

function normaliseView(data: unknown, view: InsightsViewKey): InsightsViewResponse {
  const raw = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const list = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
  return {
    view: (raw.view as InsightsViewKey) || view,
    period: (raw.period as MetricPeriod) || { from: "", to: "" },
    tiles: list<MetricResponse>(raw.tiles).filter((m) => m && typeof m.key === "string"),
    charts: list<MetricResponse>(raw.charts).filter((m) => m && typeof m.key === "string"),
    needs_you: list<NeedsYouItem>(raw.needs_you).filter((n) => n && typeof n.title === "string"),
  };
}

export const insightsApi = {
  async status(): Promise<InsightsStatus> {
    const { data } = await api.get("/insights/status");
    return normaliseStatus(data);
  },
  async view(view: InsightsViewKey, filters: InsightsFilters): Promise<InsightsViewResponse> {
    const { data } = await api.get(`/insights/views/${encodeURIComponent(view)}`, { params: filterParams(filters) });
    return normaliseView(data, view);
  },
  async metric(
    key: string,
    filters: InsightsFilters & { granularity?: "day" | "week" | "month"; group_by?: string },
  ): Promise<MetricResponse> {
    const { data } = await api.get(`/insights/metrics/${encodeURIComponent(key)}`, {
      params: filterParams(filters),
    });
    return data as MetricResponse;
  },
  /** Fetch an export as a Blob through the authenticated client (cookies and the 413 hint included). */
  async exportBlob(key: string, format: ExportFormat, filters: InsightsFilters): Promise<Blob> {
    const { data } = await api.get(exportPath(key), {
      params: { format, ...filterParams(filters) },
      responseType: "blob",
    });
    return data as Blob;
  },
};

/** Pull the API's `detail` out of an error whose body may be a Blob (export requests). */
export async function readErrorDetail(error: unknown): Promise<string | null> {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  let body: unknown = data;
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      body = JSON.parse(await data.text());
    } catch {
      return null;
    }
  }
  const top = (body as { hint?: unknown } | null)?.hint;
  if (typeof top === "string") return top;
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { hint?: unknown; message?: unknown; detail?: unknown };
    if (typeof d.hint === "string") return d.hint;
    if (typeof d.message === "string") return d.message;
    if (typeof d.detail === "string") return d.detail;
  }
  return null;
}

export function errorStatus(error: unknown): number | null {
  const status = (error as { response?: { status?: unknown } })?.response?.status;
  return typeof status === "number" ? status : null;
}
