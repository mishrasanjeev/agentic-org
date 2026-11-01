// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Number formatting for the Insights views.
 *
 * Every formatter returns a display string and never "NaN", "undefined" or
 * "Infinity": a missing or non-finite value renders as an em dash, so a
 * metric without data can never look like a number.
 */
import type { MetricUnit, ReportingCurrency } from "./api";

export const MISSING = "—";

/** True for a finite number (numeric strings from NUMERIC columns count). */
export function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

const INR_WHOLE = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const INR_SMALL = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const USD_CENTS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const USD_MICRO = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const COUNT_IN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const COUNT_US = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const DECIMAL_IN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const DECIMAL_US = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** Rupees with Indian digit grouping (1,23,45,678). Under 100 keeps paise. */
export function formatInr(value: unknown): string {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING;
  return Math.abs(n) < 100 ? INR_SMALL.format(n) : INR_WHOLE.format(n);
}

/** US dollars; amounts under one dollar keep up to four decimals so per-run costs stay readable. */
export function formatUsd(value: unknown): string {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING;
  return Math.abs(n) > 0 && Math.abs(n) < 1 ? USD_MICRO.format(n) : USD_CENTS.format(n);
}

export function formatMoney(value: unknown, currency: ReportingCurrency | string | null | undefined): string {
  return String(currency || "").toUpperCase() === "INR" ? formatInr(value) : formatUsd(value);
}

/** A percentage given on the 0-100 scale, one decimal place. */
export function formatPercent(value: unknown): string {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING;
  return `${n.toFixed(1)}%`;
}

/** Milliseconds as a short human duration: 850 ms, 12.3 s, 4m 12s, 2h 5m, 3d 4h. */
export function formatDuration(value: unknown): string {
  const ms = toFiniteNumber(value);
  if (ms === null) return MISSING;
  const sign = ms < 0 ? "-" : "";
  const abs = Math.abs(ms);
  if (abs < 1000) return `${sign}${Math.round(abs)} ms`;
  const seconds = abs / 1000;
  if (seconds < 60) return `${sign}${seconds.toFixed(1)} s`;
  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) {
    const s = totalSeconds % 60;
    return s ? `${sign}${minutes}m ${s}s` : `${sign}${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const m = minutes % 60;
    return m ? `${sign}${hours}h ${m}m` : `${sign}${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const h = hours % 24;
  return h ? `${sign}${days}d ${h}h` : `${sign}${days}d`;
}

/** Token counts in compact form: 950, 12.3K, 4.5M, 1.2B. */
export function formatTokens(value: unknown): string {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING;
  return Math.abs(n) < 1000 ? COUNT_US.format(n) : COMPACT.format(n);
}

/** Whole counts; Indian grouping for INR tenants. */
export function formatCount(value: unknown, currency?: ReportingCurrency | string | null): string {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING;
  return String(currency || "").toUpperCase() === "INR" ? COUNT_IN.format(n) : COUNT_US.format(n);
}

/** A plain decimal for table cells whose unit is unknown. */
export function formatDecimal(value: unknown, currency?: ReportingCurrency | string | null): string {
  const n = toFiniteNumber(value);
  if (n === null) return MISSING;
  return String(currency || "").toUpperCase() === "INR" ? DECIMAL_IN.format(n) : DECIMAL_US.format(n);
}

/** Format a metric value by its registry unit. */
export function formatMetricValue(
  value: unknown,
  unit: MetricUnit | string | null | undefined,
  currency?: ReportingCurrency | string | null,
): string {
  switch (unit) {
    case "usd":
      return formatUsd(value);
    case "inr":
      return formatInr(value);
    case "percent":
      return formatPercent(value);
    case "duration_ms":
      return formatDuration(value);
    case "tokens":
      return formatTokens(value);
    case "count":
      return formatCount(value, currency);
    default:
      // Money metrics may declare the currency on the response instead of the unit.
      if (currency && (unit === "currency" || unit === "money")) return formatMoney(value, currency);
      return formatDecimal(value, currency);
  }
}

export interface Delta {
  /** Relative change in percent, or null when there is nothing to compare against. */
  pct: number | null;
  direction: "up" | "down" | "flat" | "none";
  text: string;
}

/** Change of value against the comparison value, as a signed percentage with one decimal. */
export function computeDelta(value: unknown, compareValue: unknown): Delta {
  const v = toFiniteNumber(value);
  const c = toFiniteNumber(compareValue);
  if (v === null || c === null || c === 0) return { pct: null, direction: "none", text: MISSING };
  const pct = ((v - c) / Math.abs(c)) * 100;
  if (!Number.isFinite(pct)) return { pct: null, direction: "none", text: MISSING };
  const rounded = Math.round(pct * 10) / 10;
  if (rounded === 0) return { pct: 0, direction: "flat", text: "0.0%" };
  return {
    pct: rounded,
    direction: rounded > 0 ? "up" : "down",
    text: `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(1)}%`,
  };
}

/** Age in seconds as a short human string: 45s, 12m, 5h, 3d. */
export function formatAge(seconds: unknown): string {
  const s = toFiniteNumber(seconds);
  if (s === null || s < 0) return MISSING;
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/** "agent_ref" -> "Agent ref". */
export function humaniseKey(key: string): string {
  const text = key.replace(/[_.]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : key;
}
