// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for the Insights platform view (api/v1/insights_platform.py)
 * and the shaping of its figures into the v1 tile and chart inputs.
 *
 * The backend is the authorization boundary: anyone but the platform
 * operator gets 404 from every route. Figures are cross-tenant aggregates;
 * this module never computes a number the API did not send.
 */
import api from "@/lib/api";
import type { MetricResponse } from "./api";
import { toFiniteNumber } from "./format";

export type PlatformRangePreset = "7d" | "30d" | "90d";
export const PLATFORM_RANGE_PRESETS: readonly PlatformRangePreset[] = ["7d", "30d", "90d"];
const PRESET_DAYS: Record<PlatformRangePreset, number> = { "7d": 7, "30d": 30, "90d": 90 };
/** The reconciled-nights gate when the API does not say (core/insights/platform.py). */
export const DEFAULT_GATE_NIGHTS = 14;

export interface PlatformPeriod {
  from: string;
  to: string;
}

export interface PlatformDay {
  day: string;
  tenants_active: number;
  runs: number;
  failed: number;
  tokens: number;
  cost_usd: number;
}

export interface PlatformTotals {
  tenants_active: number;
  tenants_total: number;
  runs: number;
  failed: number;
  /** Failed runs over runs, 0-1; null without runs. */
  failure_rate: number | null;
  tokens: number;
  cost_usd: number;
}

export interface PlatformSummary {
  period: PlatformPeriod;
  totals: PlatformTotals;
  series: PlatformDay[];
  reconciled_gate_nights: number;
}

export interface PlatformTenantRow {
  tenant_id: string;
  tenant_name: string;
  plan: string;
  runs: number;
  failed: number;
  failure_rate: number | null;
  cost_usd: number;
  active_agents: number;
  last_active_day: string | null;
  reconciled_nights: number;
  gate_met: boolean;
  last_rollup_status: string | null;
}

export interface PlatformTenants {
  period: PlatformPeriod;
  reconciled_gate_nights: number;
  tenants: PlatformTenantRow[];
  truncated: boolean;
}

export interface PostureCheck {
  key: "schema" | "rls" | "role" | "health" | string;
  ok: boolean;
}

export interface PlatformPosture {
  computed_at: string;
  ok: boolean;
  checks: PostureCheck[];
  schema?: { ok: boolean; database_heads: string[]; code_heads: string[] };
  rls?: { ok: boolean; tenant_tables: number; forced: number; unforced: string[] };
  role?: { ok: boolean; role: string | null; superuser: boolean | null; bypass_rls: boolean | null };
  health?: {
    ok: boolean;
    window_hours: number;
    snapshots: number;
    counts: Record<string, number>;
    latest_status: string | null;
    latest_at: string | null;
    latest_version: string | null;
    latest_commit: string | null;
  };
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** The UTC-day range for a preset, today included (the API reads UTC days). */
export function platformRange(preset: PlatformRangePreset, now: Date = new Date()): PlatformPeriod {
  const to = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const from = new Date(to);
  from.setUTCDate(from.getUTCDate() - (PRESET_DAYS[preset] - 1));
  return { from: isoDay(from), to: isoDay(to) };
}

const num = (v: unknown): number => toFiniteNumber(v) ?? 0;
const numOrNull = (v: unknown): number | null => toFiniteNumber(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function normalisePeriod(v: unknown): PlatformPeriod {
  const p = obj(v);
  return { from: str(p.from), to: str(p.to) };
}

function gate(v: unknown): number {
  const n = toFiniteNumber(v);
  return n !== null && n > 0 ? n : DEFAULT_GATE_NIGHTS;
}

export function normaliseSummary(data: unknown): PlatformSummary {
  const raw = obj(data);
  const t = obj(raw.totals);
  return {
    period: normalisePeriod(raw.period),
    totals: {
      tenants_active: num(t.tenants_active),
      tenants_total: num(t.tenants_total),
      runs: num(t.runs),
      failed: num(t.failed),
      failure_rate: numOrNull(t.failure_rate),
      tokens: num(t.tokens),
      cost_usd: num(t.cost_usd),
    },
    series: list(raw.series)
      .map(obj)
      .filter((d) => typeof d.day === "string")
      .map((d) => ({
        day: str(d.day),
        tenants_active: num(d.tenants_active),
        runs: num(d.runs),
        failed: num(d.failed),
        tokens: num(d.tokens),
        cost_usd: num(d.cost_usd),
      })),
    reconciled_gate_nights: gate(raw.reconciled_gate_nights),
  };
}

export function normaliseTenants(data: unknown): PlatformTenants {
  const raw = obj(data);
  return {
    period: normalisePeriod(raw.period),
    reconciled_gate_nights: gate(raw.reconciled_gate_nights),
    truncated: raw.truncated === true,
    tenants: list(raw.tenants)
      .map(obj)
      .filter((r) => typeof r.tenant_id === "string")
      .map((r) => ({
        tenant_id: str(r.tenant_id),
        tenant_name: str(r.tenant_name),
        plan: str(r.plan),
        runs: num(r.runs),
        failed: num(r.failed),
        failure_rate: numOrNull(r.failure_rate),
        cost_usd: num(r.cost_usd),
        active_agents: num(r.active_agents),
        last_active_day: strOrNull(r.last_active_day),
        reconciled_nights: num(r.reconciled_nights),
        gate_met: r.gate_met === true,
        last_rollup_status: strOrNull(r.last_rollup_status),
      })),
  };
}

export function normalisePosture(data: unknown): PlatformPosture {
  const raw = obj(data);
  return {
    ...(raw as Partial<PlatformPosture>),
    computed_at: str(raw.computed_at),
    ok: raw.ok === true,
    checks: list(raw.checks)
      .map(obj)
      .filter((c) => typeof c.key === "string")
      .map((c) => ({ key: str(c.key), ok: c.ok === true })),
  };
}

export const platformApi = {
  /** The daily figures or the tenant table as a file (CSV, XLSX or JSON); the server audits the export. */
  async exportFile(
    dataset: "daily" | "tenants",
    format: "csv" | "xlsx" | "json",
    range: PlatformPeriod,
  ): Promise<{ blob: Blob; filename: string }> {
    const { data } = await api.get("/platform/insights/export", {
      params: { dataset, format, from: range.from, to: range.to },
      responseType: "blob",
    });
    return { blob: data as Blob, filename: `platform-${dataset}-${range.from}-${range.to}.${format}` };
  },
  async summary(range: PlatformPeriod): Promise<PlatformSummary> {
    const { data } = await api.get("/platform/insights/summary", { params: { from: range.from, to: range.to } });
    return normaliseSummary(data);
  },
  async tenants(range: PlatformPeriod): Promise<PlatformTenants> {
    const { data } = await api.get("/platform/insights/tenants", { params: { from: range.from, to: range.to } });
    return normaliseTenants(data);
  },
  async posture(): Promise<PlatformPosture> {
    const { data } = await api.get("/platform/insights/posture");
    return normalisePosture(data);
  },
};

/** Metric keys the page shows (tile and chart identity; they are not registry keys). */
export const PLATFORM_METRIC_KEYS = {
  tenantsActive: "platform.tenants_active",
  runs: "platform.runs",
  failureRate: "platform.failure_rate",
  spend: "platform.spend",
  runsPerDay: "platform.runs_per_day",
} as const;

export interface PlatformMetricText {
  title: string;
  definition: string;
  source: string;
}

/**
 * Wrap one platform figure in the v1 metric shape so DecisionTile and
 * ChartCard render it unchanged. With no runs in the range the metric is
 * `no_data` and shows the hint instead of a zero.
 */
export function platformMetric(
  key: string,
  text: PlatformMetricText,
  summary: PlatformSummary,
  options: {
    unit: MetricResponse["unit"];
    value: number | null;
    series?: Array<{ t: string; value: number | null }>;
    hint: string;
  },
): MetricResponse {
  const hasData = summary.totals.runs > 0 || summary.series.length > 0;
  return {
    key,
    title: text.title,
    definition: text.definition,
    unit: options.unit,
    currency: "USD",
    source: text.source,
    data_quality: hasData ? "measured" : "no_data",
    computed_at: "",
    period: { from: summary.period.from, to: summary.period.to, granularity: "day" },
    compare: null,
    value: hasData ? options.value : null,
    compare_value: null,
    state: null,
    series: hasData ? (options.series ?? null) : null,
    rows: null,
    hint: options.hint,
  };
}
