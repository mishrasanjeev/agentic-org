// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Date-range presets for the Insights filter bar, computed in the tenant's
 * reporting timezone so "today" matches the tenant-local days the rollups use.
 */

export type RangePreset = "7d" | "30d" | "mtd" | "qtd" | "custom";
export const RANGE_PRESETS: readonly RangePreset[] = ["7d", "30d", "mtd", "qtd", "custom"];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function isIsoDate(value: string | null | undefined): value is string {
  if (!value || !ISO_DATE.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

/** Today's date (YYYY-MM-DD) in the given IANA timezone; falls back to UTC for an unknown zone. */
export function todayIn(timezone: string | null | undefined, now: Date = new Date()): string {
  try {
    // en-CA formats as YYYY-MM-DD.
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone || "UTC",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

function parts(iso: string): { y: number; m: number; d: number } {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m, d };
}

function fmt(y: number, m: number, d: number): string {
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  const { y, m, d } = parts(iso);
  return fmt(y, m, d + days);
}

/** First day of the fiscal quarter containing `iso`, for a fiscal year starting in `fyStartMonth` (1-12). */
export function fiscalQuarterStart(iso: string, fyStartMonth = 1): string {
  const { y, m } = parts(iso);
  const start = Number.isInteger(fyStartMonth) && fyStartMonth >= 1 && fyStartMonth <= 12 ? fyStartMonth : 1;
  const offset = (m - start + 12) % 12; // months since the fiscal year began
  const quarterFirstMonth = m - (offset % 3);
  return fmt(y, quarterFirstMonth, 1);
}

export interface DateRange {
  from: string;
  to: string;
}

/** Resolve a preset to inclusive from/to dates. Custom ranges come from the URL instead. */
export function presetRange(
  preset: Exclude<RangePreset, "custom">,
  today: string,
  fyStartMonth = 1,
): DateRange {
  switch (preset) {
    case "7d":
      return { from: addDays(today, -6), to: today };
    case "mtd": {
      const { y, m } = parts(today);
      return { from: fmt(y, m, 1), to: today };
    }
    case "qtd":
      return { from: fiscalQuarterStart(today, fyStartMonth), to: today };
    case "30d":
    default:
      return { from: addDays(today, -29), to: today };
  }
}
