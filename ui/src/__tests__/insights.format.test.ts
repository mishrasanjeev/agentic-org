// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { describe, expect, it } from "vitest";
import {
  computeDelta,
  formatAge,
  formatCount,
  formatDuration,
  formatInr,
  formatLowerBound,
  formatMetricValue,
  formatMoney,
  formatPercent,
  formatTokens,
  formatUsd,
  humaniseKey,
  MISSING,
  toFiniteNumber,
} from "@/lib/insights/format";

const BAD_VALUES: unknown[] = [null, undefined, NaN, Infinity, -Infinity, "", "abc", {}, []];

describe("insights formatters", () => {
  it("formats INR with Indian digit grouping", () => {
    expect(formatInr(1234567)).toBe("₹12,34,567");
    expect(formatInr(100000)).toBe("₹1,00,000");
    expect(formatInr(42.5)).toBe("₹42.50");
    expect(formatInr("2500.75")).toBe("₹2,501");
  });

  it("formats USD with cents, and sub-dollar amounts with up to four decimals", () => {
    expect(formatUsd(1234.5)).toBe("$1,234.50");
    expect(formatUsd(0.0123)).toBe("$0.0123");
    expect(formatUsd(0)).toBe("$0.00");
  });

  it("chooses the currency by code", () => {
    expect(formatMoney(150000, "INR")).toBe("₹1,50,000");
    expect(formatMoney(150000, "USD")).toBe("$150,000.00");
    expect(formatMoney(5, null)).toBe("$5.00");
  });

  it("formats percent to one decimal place", () => {
    expect(formatPercent(87.345)).toBe("87.3%");
    expect(formatPercent(100)).toBe("100.0%");
    expect(formatPercent(0)).toBe("0.0%");
  });

  it("humanises durations", () => {
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(12_345)).toBe("12.3 s");
    expect(formatDuration(252_000)).toBe("4m 12s");
    expect(formatDuration(120_000)).toBe("2m");
    expect(formatDuration(7_500_000)).toBe("2h 5m");
    expect(formatDuration(3 * 86_400_000 + 4 * 3_600_000)).toBe("3d 4h");
  });

  it("formats tokens compactly", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(12_300)).toBe("12.3K");
    expect(formatTokens(4_500_000)).toBe("4.5M");
  });

  it("groups counts in the Indian style for INR tenants", () => {
    expect(formatCount(1234567, "INR")).toBe("12,34,567");
    expect(formatCount(1234567, "USD")).toBe("1,234,567");
  });

  it("dispatches by unit", () => {
    expect(formatMetricValue(12.34, "usd")).toBe("$12.34");
    expect(formatMetricValue(1200, "inr")).toBe("₹1,200");
    expect(formatMetricValue(55.55, "percent")).toBe("55.5%");
    expect(formatMetricValue(1500, "duration_ms")).toBe("1.5 s");
    expect(formatMetricValue(2_000_000, "tokens")).toBe("2M");
    expect(formatMetricValue(3, "count")).toBe("3");
  });

  it("never renders NaN, undefined or Infinity", () => {
    const fns = [formatInr, formatUsd, formatPercent, formatDuration, formatTokens, formatCount, formatAge];
    for (const fn of fns) {
      for (const v of BAD_VALUES) {
        const out = fn(v as never);
        expect(out).toBe(MISSING);
      }
    }
    for (const unit of ["usd", "inr", "percent", "duration_ms", "tokens", "count", "mystery"]) {
      for (const v of BAD_VALUES) {
        const out = formatMetricValue(v, unit, "INR");
        expect(out).not.toMatch(/NaN|undefined|Infinity|null/);
      }
    }
  });

  it("marks a floor value as more than it", () => {
    expect(formatLowerBound(formatDuration(600000))).toBe("> 10m");
    expect(formatLowerBound(MISSING)).toBe(MISSING);
  });

  it("computes the delta against the comparison value", () => {
    expect(computeDelta(110, 100)).toEqual({ pct: 10, direction: "up", text: "+10.0%" });
    expect(computeDelta(90, 100)).toEqual({ pct: -10, direction: "down", text: "−10.0%" });
    expect(computeDelta(100, 100).direction).toBe("flat");
    expect(computeDelta(5, 0)).toEqual({ pct: null, direction: "none", text: MISSING });
    expect(computeDelta(5, null).direction).toBe("none");
    expect(computeDelta(undefined, 3).direction).toBe("none");
  });

  it("formats ages and keys", () => {
    expect(formatAge(30)).toBe("30s");
    expect(formatAge(600)).toBe("10m");
    expect(formatAge(7200)).toBe("2h");
    expect(formatAge(3 * 86400)).toBe("3d");
    expect(humaniseKey("agent_ref")).toBe("Agent ref");
    expect(toFiniteNumber("12.5")).toBe(12.5);
    expect(toFiniteNumber("  ")).toBeNull();
  });
});
