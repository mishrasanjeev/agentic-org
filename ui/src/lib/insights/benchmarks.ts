// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for Insights benchmarks (api/v1/insights_benchmarks.py): the
 * organisation's figures against cohorts of at least 20 organisations that
 * opted in, and the administrator's opt-in choice.
 */
import api from "@/lib/api";

export type BenchmarkPosition = "below_p25" | "p25_p50" | "p50_p75" | "above_p75";

export interface BenchmarkBand {
  cohort: string;
  p25: number;
  p50: number;
  p75: number;
  tenants: number;
  position: BenchmarkPosition | null;
}

export interface BenchmarkMetric {
  key: string;
  title: string;
  unit: string;
  higher_is_better: boolean | null;
  value: number | null;
  bands: BenchmarkBand[];
}

export interface BenchmarkCard {
  opted_in: boolean;
  can_change: boolean;
  participating: number;
  min_tenants: number;
  min_runs: number;
  window: { from: string; to: string };
  runs: number | null;
  metrics: BenchmarkMetric[];
  hint: string | null;
}

const POSITIONS: BenchmarkPosition[] = ["below_p25", "p25_p50", "p50_p75", "above_p75"];

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function normaliseCard(data: unknown): BenchmarkCard {
  const raw = obj(data);
  const win = obj(raw.window);
  const metrics = (Array.isArray(raw.metrics) ? raw.metrics : [])
    .map(obj)
    .filter((m) => typeof m.key === "string")
    .map((m) => ({
      key: m.key as string,
      title: typeof m.title === "string" ? m.title : (m.key as string),
      unit: typeof m.unit === "string" ? m.unit : "count",
      higher_is_better: typeof m.higher_is_better === "boolean" ? m.higher_is_better : null,
      value: num(m.value),
      bands: (Array.isArray(m.bands) ? m.bands : [])
        .map(obj)
        .filter((b) => typeof b.cohort === "string" && num(b.p50) !== null)
        .map((b) => ({
          cohort: b.cohort as string,
          p25: num(b.p25) ?? 0,
          p50: num(b.p50) ?? 0,
          p75: num(b.p75) ?? 0,
          tenants: num(b.tenants) ?? 0,
          position: POSITIONS.includes(b.position as BenchmarkPosition) ? (b.position as BenchmarkPosition) : null,
        })),
    }));
  return {
    opted_in: raw.opted_in === true,
    can_change: raw.can_change === true,
    participating: num(raw.participating) ?? 0,
    min_tenants: num(raw.min_tenants) ?? 20,
    min_runs: num(raw.min_runs) ?? 100,
    window: { from: typeof win.from === "string" ? win.from : "", to: typeof win.to === "string" ? win.to : "" },
    runs: num(raw.runs),
    metrics,
    hint: typeof raw.hint === "string" && raw.hint ? raw.hint : null,
  };
}

export const benchmarksApi = {
  async card(): Promise<BenchmarkCard> {
    const { data } = await api.get("/insights/benchmarks");
    return normaliseCard(data);
  },
  async setOptIn(optIn: boolean): Promise<void> {
    await api.put("/insights/benchmarks/opt-in", { opt_in: optIn });
  },
};

/** A figure in its unit, as the cards write it. */
export function formatBenchmark(value: number | null, unit: string): string {
  if (value === null) return "—";
  if (unit === "percent") return `${value.toFixed(1)}%`;
  if (unit === "usd") return `$${value.toFixed(value < 1 ? 4 : 2)}`;
  if (unit === "duration_ms") return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
  return value.toFixed(1);
}
