// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Shape metric responses into chart data and plain-text summaries.
 * Values that are missing or non-finite are dropped, never drawn as zero.
 */
import type { MetricPoint, MetricResponse, MetricRow } from "./api";
import { toFiniteNumber } from "./format";

export interface LinePoint {
  t: string;
  value: number;
}

/** One quantity over time, sorted by time, missing values dropped. */
export function lineData(series: MetricPoint[] | null | undefined): LinePoint[] {
  const out: LinePoint[] = [];
  for (const p of series ?? []) {
    const v = toFiniteNumber(p?.value);
    if (p && typeof p.t === "string" && v !== null) out.push({ t: p.t, value: v });
  }
  return out.sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
}

export function hasGroups(series: MetricPoint[] | null | undefined): boolean {
  return (series ?? []).some((p) => typeof p?.group === "string" && p.group !== "");
}

export interface StackedData {
  groups: string[];
  rows: Array<Record<string, number | string>>;
}

/** Parts over time: one row per time bucket, one column per group. */
export function stackedData(series: MetricPoint[] | null | undefined): StackedData {
  const groups: string[] = [];
  const byT = new Map<string, Record<string, number | string>>();
  for (const p of series ?? []) {
    const v = toFiniteNumber(p?.value);
    if (!p || typeof p.t !== "string" || v === null) continue;
    const g = typeof p.group === "string" && p.group !== "" ? p.group : "value";
    if (!groups.includes(g)) groups.push(g);
    const row = byT.get(p.t) ?? { t: p.t };
    row[g] = (toFiniteNumber(row[g]) ?? 0) + v;
    byT.set(p.t, row);
  }
  const rows = Array.from(byT.values()).sort((a, b) => String(a.t).localeCompare(String(b.t)));
  return { groups, rows };
}

const LABEL_FIELDS = ["label", "name", "title", "group", "agent_name", "agent", "agent_ref", "model", "status", "unit", "period", "agent_id", "key"];
const VALUE_FIELDS = ["value", "cost", "cost_usd", "amount", "amount_inr", "count", "runs", "tokens", "total", "actual"];

/** Pick the label column of a row: a known label field, else the first string field. */
export function rowLabel(row: MetricRow): string {
  for (const f of LABEL_FIELDS) {
    const v = row[f];
    if (typeof v === "string" && v !== "") return v;
  }
  for (const v of Object.values(row)) {
    if (typeof v === "string" && v !== "") return v;
  }
  return "";
}

/** Pick the value of a row: a known value field, else the first numeric field. */
export function rowValue(row: MetricRow): number | null {
  for (const f of VALUE_FIELDS) {
    const v = toFiniteNumber(row[f]);
    if (v !== null) return v;
  }
  for (const v of Object.values(row)) {
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
}

export interface RankedItem {
  label: string;
  value: number;
}

/**
 * Items ranked by value, largest first. Reads rows when present, else sums
 * series values by group (so a grouped series also ranks).
 */
export function rankedData(metric: Pick<MetricResponse, "rows" | "series">, limit = 10): RankedItem[] {
  const items = new Map<string, number>();
  const rows = metric.rows ?? [];
  if (rows.length > 0) {
    for (const r of rows) {
      if (!r || typeof r !== "object") continue;
      const label = rowLabel(r);
      const value = rowValue(r);
      if (label && value !== null) items.set(label, (items.get(label) ?? 0) + value);
    }
  } else {
    for (const p of metric.series ?? []) {
      const v = toFiniteNumber(p?.value);
      const g = typeof p?.group === "string" && p.group ? p.group : null;
      if (g && v !== null) items.set(g, (items.get(g) ?? 0) + v);
    }
  }
  return Array.from(items, ([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, limit);
}

export interface BulletItem {
  label: string;
  actual: number;
  threshold: number | null;
  state: string | null;
  /** Budgets carry their own currency (the budget's value may be a count of flagged budgets). */
  currency: string | null;
}

/** Budget rows: actual against threshold, with the row's own state when it has one. */
export function bulletData(rows: MetricRow[] | null | undefined): BulletItem[] {
  const out: BulletItem[] = [];
  for (const r of rows ?? []) {
    if (!r || typeof r !== "object") continue;
    const actual = toFiniteNumber(r.actual ?? r.spent ?? r.value ?? r.amount);
    if (actual === null) continue;
    const threshold = toFiniteNumber(r.threshold ?? r.budget ?? r.limit ?? r.target);
    out.push({
      label: rowLabel(r) || String(out.length + 1),
      actual,
      threshold,
      state: typeof r.state === "string" ? r.state : null,
      currency: typeof r.currency === "string" && r.currency ? r.currency : null,
    });
  }
  return out;
}

export interface ForecastData {
  point: number;
  low: number | null;
  high: number | null;
  actual: number | null;
}

/** Forecast point with its band, from the first row or the top-level value. */
export function forecastData(metric: Pick<MetricResponse, "rows" | "value">): ForecastData | null {
  const row = (metric.rows ?? []).find((r) => r && typeof r === "object") ?? {};
  const point = toFiniteNumber(row.point ?? row.forecast ?? row.value ?? metric.value);
  if (point === null) return null;
  return {
    point,
    low: toFiniteNumber(row.low ?? row.lower),
    high: toFiniteNumber(row.high ?? row.upper),
    actual: toFiniteNumber(row.actual ?? row.month_to_date ?? row.mtd),
  };
}

/** True when the metric has nothing to draw or show. */
export function isEmptyMetric(metric: MetricResponse): boolean {
  if (metric.data_quality === "no_data") return true;
  const hasValue = toFiniteNumber(metric.value) !== null;
  const hasSeries = lineData(metric.series).length > 0;
  const hasRows = (metric.rows ?? []).length > 0;
  return !hasValue && !hasSeries && !hasRows;
}

/** The data shape the chart form is chosen from. */
export function dataShape(metric: Pick<MetricResponse, "rows" | "series">) {
  return {
    hasSeries: lineData(metric.series).length > 0,
    hasGroups: hasGroups(metric.series),
    hasRows: (metric.rows ?? []).some((r) => r && typeof r === "object"),
  };
}

/**
 * Table columns for the "View data" toggle: the series (t, group, value)
 * when `fromSeries` and a series exists, otherwise the rows' keys.
 */
export function tableColumns(metric: MetricResponse, fromSeries = false): string[] {
  const rows = metric.rows ?? [];
  const useSeries = (fromSeries && (metric.series ?? []).length > 0) || rows.length === 0;
  if (!useSeries) {
    const cols: string[] = [];
    for (const r of rows) {
      if (!r || typeof r !== "object") continue;
      for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
    }
    return cols;
  }
  return hasGroups(metric.series) ? ["t", "group", "value"] : ["t", "value"];
}

export function tableRows(metric: MetricResponse, fromSeries = false): MetricRow[] {
  const rows = metric.rows ?? [];
  const useSeries = (fromSeries && (metric.series ?? []).length > 0) || rows.length === 0;
  if (!useSeries) return rows.filter((r) => r && typeof r === "object");
  return (metric.series ?? []).filter((p) => p && typeof p.t === "string").map((p) => ({ ...p }));
}

export interface WaterfallStep {
  label: string;
  /** Invisible offset under the bar, so each step starts where the previous one ended. */
  base: number;
  /** The bar's height (always positive). */
  size: number;
  /** The signed amount the step adds or takes away (a total for the first and last steps). */
  value: number;
  kind: "total" | "decrease" | "increase";
}

/**
 * Gross-to-net steps from rows of `{label, value}`: the first and last rows are
 * totals drawn from zero, the rows between are signed changes drawn from the
 * running total. Rows without a number are skipped.
 */
export function waterfallData(rows: MetricRow[] | null | undefined): WaterfallStep[] {
  const items = (rows ?? [])
    .map((r) => ({ label: typeof r?.label === "string" ? r.label : "", value: toFiniteNumber(r?.value) }))
    .filter((r): r is { label: string; value: number } => r.label !== "" && r.value !== null);
  const out: WaterfallStep[] = [];
  let running = 0;
  items.forEach((item, i) => {
    const total = i === 0 || i === items.length - 1;
    if (total) {
      out.push({ label: item.label, base: Math.min(0, item.value), size: Math.abs(item.value), value: item.value, kind: "total" });
      running = item.value;
      return;
    }
    const end = running + item.value;
    out.push({
      label: item.label,
      base: Math.min(running, end),
      size: Math.abs(item.value),
      value: item.value,
      kind: item.value < 0 ? "decrease" : "increase",
    });
    running = end;
  });
  return out;
}
