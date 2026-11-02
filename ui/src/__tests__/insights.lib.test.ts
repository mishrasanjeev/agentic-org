// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { describe, expect, it } from "vitest";
import en from "../locales/en.json";
import hi from "../locales/hi.json";
import metricKeys from "@/lib/insights/metricKeys.generated.json";
import {
  exportFilename,
  exportPath,
  exportUrl,
  filterParams,
  readErrorDetail,
  type MetricResponse,
} from "@/lib/insights/api";
import {
  bulletData,
  dataShape,
  forecastData,
  isEmptyMetric,
  lineData,
  rankedData,
  stackedData,
  tableColumns,
  tableRows,
} from "@/lib/insights/chartData";
import { addDays, fiscalQuarterStart, isIsoDate, presetRange, todayIn } from "@/lib/insights/dates";
import { chartKindFor, METRIC_PRESENTATION, ROLE_VIEWS, tableFromSeries, VIEW_METRICS } from "@/lib/insights/metrics";
import { chartColor, normaliseState } from "@/lib/insights/tokens";

const pageSource = (
  import.meta.glob("../pages/InsightsDashboard.tsx", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >
)["../pages/InsightsDashboard.tsx"];

const componentSources = import.meta.glob("../components/insights/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function metric(partial: Partial<MetricResponse>): MetricResponse {
  return {
    key: "runs.volume",
    title: "Runs",
    definition: "",
    unit: "count",
    source: "insights_daily",
    data_quality: "measured",
    computed_at: "2026-11-01T00:00:00Z",
    period: { from: "2026-10-01", to: "2026-10-30" },
    ...partial,
  };
}

function leafKeys(obj: unknown, prefix = ""): string[] {
  if (!obj || typeof obj !== "object") return [prefix];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) => leafKeys(v, prefix ? `${prefix}.${k}` : k));
}

describe("metric keys used by the Insights page", () => {
  const registry = new Set(metricKeys as string[]);

  it("the generated key list is a sorted, non-empty list of unique strings", () => {
    const keys = metricKeys as string[];
    expect(keys.length).toBeGreaterThan(0);
    expect([...keys].sort()).toEqual(keys);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("every key the page presents exists in the registry", () => {
    for (const key of Object.keys(METRIC_PRESENTATION)) expect(registry.has(key), key).toBe(true);
    for (const keys of Object.values(VIEW_METRICS)) for (const key of keys) expect(registry.has(key), key).toBe(true);
  });

  it("every registry key has a chart form and an action", () => {
    for (const key of registry) expect(METRIC_PRESENTATION[key], key).toBeDefined();
  });

  it("metric keys written literally in the page and components exist in the registry", () => {
    const sources = [pageSource, ...Object.values(componentSources)];
    const pattern = /["']((?:agents|runs|approvals|cost|tokens)\.[a-z_]+)["']/g;
    for (const src of sources) {
      for (const m of src.matchAll(pattern)) expect(registry.has(m[1]), m[1]).toBe(true);
    }
  });

  it("role views mirror the server rule: admin both, cfo finance only", () => {
    expect(ROLE_VIEWS.admin).toEqual(["executive", "finance"]);
    expect(ROLE_VIEWS.cfo).toEqual(["finance"]);
    expect(ROLE_VIEWS.analyst).toBeUndefined();
  });

  it("every finance tile drills to a page or explorer the CFO can open", () => {
    // The CFO's explorers (core/insights/explore_registry.py): agents, runs, approvals, cost, quality.
    const cfoExplorers = ["agents", "runs", "approvals", "cost", "quality"];
    for (const key of VIEW_METRICS.finance) {
      const target = METRIC_PRESENTATION[key].action.to;
      const explorer = /^\/dashboard\/insights\/explore\/([a-z]+)/.exec(target)?.[1];
      if (explorer) expect(cfoExplorers, key).toContain(explorer);
      else expect(target, key).toBe("/dashboard/costs");
    }
  });
});

describe("insights locale keys", () => {
  it("English and Hindi carry the same insights keys", () => {
    expect(leafKeys(hi.insights).sort()).toEqual(leafKeys(en.insights).sort());
    expect(en.nav.insights).toBe("Insights");
    expect(hi.nav.insights).toBeTruthy();
  });

  it("Hindi strings are translated, not copied English", () => {
    const enLeaves = leafKeys(en.insights);
    const get = (obj: unknown, path: string) =>
      path.split(".").reduce<unknown>((acc, k) => (acc as Record<string, unknown>)?.[k], obj);
    const formatNames = new Set(["export.csv", "export.json", "export.xlsx"]);
    for (const key of enLeaves) {
      if (formatNames.has(key)) continue;
      expect(get(hi.insights, key), key).not.toBe(get(en.insights, key));
    }
  });

  it("every insights.* key referenced in the page and components exists in both locales", () => {
    const sources = [pageSource, ...Object.values(componentSources)];
    const get = (obj: unknown, path: string) =>
      path.split(".").reduce<unknown>((acc, k) => (acc as Record<string, unknown>)?.[k], obj);
    let count = 0;
    for (const src of sources) {
      for (const m of src.matchAll(/\bt\(\s*["'](insights\.[A-Za-z0-9_.]+)["']/g)) {
        count += 1;
        expect(get(en, m[1]), m[1]).toBeDefined();
        expect(get(hi, m[1]), m[1]).toBeDefined();
      }
      // Template keys: every value the template can take must exist.
      for (const m of src.matchAll(/\bt\(\s*`(insights\.[A-Za-z.]+)\.\$\{/g)) {
        expect(get(en, m[1]), m[1]).toBeTypeOf("object");
        expect(get(hi, m[1]), m[1]).toBeTypeOf("object");
      }
    }
    expect(count).toBeGreaterThan(30);
  });
});

describe("insights dates", () => {
  it("validates ISO dates", () => {
    expect(isIsoDate("2026-02-28")).toBe(true);
    expect(isIsoDate("2026-02-30")).toBe(false);
    expect(isIsoDate("01-02-2026")).toBe(false);
    expect(isIsoDate(null)).toBe(false);
  });

  it("computes today in the tenant timezone", () => {
    const now = new Date("2026-10-31T20:00:00Z"); // 01:30 on 1 Nov in India
    expect(todayIn("Asia/Kolkata", now)).toBe("2026-11-01");
    expect(todayIn("UTC", now)).toBe("2026-10-31");
    expect(todayIn("Not/AZone", now)).toBe("2026-10-31");
  });

  it("resolves presets to inclusive ranges", () => {
    expect(presetRange("7d", "2026-11-01")).toEqual({ from: "2026-10-26", to: "2026-11-01" });
    expect(presetRange("30d", "2026-11-01")).toEqual({ from: "2026-10-03", to: "2026-11-01" });
    expect(presetRange("mtd", "2026-11-15")).toEqual({ from: "2026-11-01", to: "2026-11-15" });
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("uses the fiscal year start for quarter-to-date", () => {
    // April fiscal year (India): quarters start Apr, Jul, Oct, Jan.
    expect(fiscalQuarterStart("2026-11-20", 4)).toBe("2026-10-01");
    expect(fiscalQuarterStart("2027-02-03", 4)).toBe("2027-01-01");
    // February fiscal year: quarters start Feb, May, Aug, Nov; January belongs to the Nov quarter.
    expect(fiscalQuarterStart("2027-01-10", 2)).toBe("2026-11-01");
    expect(presetRange("qtd", "2026-08-09", 1)).toEqual({ from: "2026-07-01", to: "2026-08-09" });
    expect(fiscalQuarterStart("2026-08-09", 99)).toBe("2026-07-01");
  });
});

describe("insights chart data", () => {
  it("drops missing points and sorts a line series", () => {
    expect(
      lineData([
        { t: "2026-10-02", value: 5 },
        { t: "2026-10-01", value: "3" },
        { t: "2026-10-03", value: null },
      ]),
    ).toEqual([
      { t: "2026-10-01", value: 3 },
      { t: "2026-10-02", value: 5 },
    ]);
  });

  it("pivots a grouped series into stacked rows", () => {
    const out = stackedData([
      { t: "2026-10-01", value: 2, group: "completed" },
      { t: "2026-10-01", value: 1, group: "failed" },
      { t: "2026-10-02", value: 4, group: "completed" },
    ]);
    expect(out.groups).toEqual(["completed", "failed"]);
    expect(out.rows).toEqual([
      { t: "2026-10-01", completed: 2, failed: 1 },
      { t: "2026-10-02", completed: 4 },
    ]);
  });

  it("ranks rows, or a grouped series, largest first", () => {
    const fromRows = rankedData({ rows: [{ model: "a", cost_usd: 2 }, { model: "b", cost_usd: 9 }] });
    expect(fromRows).toEqual([
      { label: "b", value: 9 },
      { label: "a", value: 2 },
    ]);
    const fromSeries = rankedData({
      series: [
        { t: "d1", value: 1, group: "x" },
        { t: "d2", value: 5, group: "y" },
        { t: "d3", value: 2, group: "x" },
      ],
    });
    expect(fromSeries).toEqual([
      { label: "y", value: 5 },
      { label: "x", value: 3 },
    ]);
  });

  it("reads budgets and forecasts", () => {
    expect(bulletData([{ period: "monthly", actual: 80, threshold: 100, state: "warn" }, { period: "x" }])).toEqual([
      { label: "monthly", actual: 80, threshold: 100, state: "warn", currency: null },
    ]);
    expect(forecastData({ rows: [{ point: 120, low: 100, high: 150 }] })).toEqual({
      point: 120,
      low: 100,
      high: 150,
      actual: null,
    });
    expect(forecastData({ value: 50 })).toEqual({ point: 50, low: null, high: null, actual: null });
    expect(forecastData({})).toBeNull();
  });

  it("treats no_data or nothing to show as empty", () => {
    expect(isEmptyMetric(metric({ data_quality: "no_data", value: 5 }))).toBe(true);
    expect(isEmptyMetric(metric({ value: null, series: [], rows: [] }))).toBe(true);
    expect(isEmptyMetric(metric({ value: 0 }))).toBe(false);
  });

  it("derives table columns", () => {
    expect(tableColumns(metric({ series: [{ t: "d", value: 1 }] }))).toEqual(["t", "value"]);
    expect(tableColumns(metric({ series: [{ t: "d", value: 1, group: "g" }] }))).toEqual(["t", "group", "value"]);
    expect(tableColumns(metric({ rows: [{ a: 1 }, { b: 2 }] }))).toEqual(["a", "b"]);
  });

  it("chooses a chart form from the key, adjusted to the data the API sent", () => {
    const shape = (hasSeries: boolean, hasGroups: boolean, hasRows: boolean) => ({ hasSeries, hasGroups, hasRows });
    expect(chartKindFor("cost.budgets", shape(false, false, true))).toBe("bullet");
    expect(chartKindFor("cost.forecast", shape(true, false, true))).toBe("forecast");
    expect(chartKindFor("runs.volume", shape(true, true, true))).toBe("stacked");
    // "parts over time" with one part is one quantity over time.
    expect(chartKindFor("runs.volume", shape(true, false, false))).toBe("line");
    // tokens.by_unit sends unit totals as rows and no series: rank them.
    expect(chartKindFor("tokens.by_unit", shape(false, false, true))).toBe("hbar");
    expect(chartKindFor("future.metric", shape(true, true, false))).toBe("stacked");
    expect(chartKindFor("future.metric", shape(false, false, true))).toBe("hbar");
    expect(chartKindFor("future.metric", shape(false, false, false))).toBe("line");
    expect(tableFromSeries("line")).toBe(true);
    expect(tableFromSeries("forecast")).toBe(false);
  });

  it("tabulates the series for time charts and the rows otherwise", () => {
    const latency = metric({
      unit: "duration_ms",
      series: [{ t: "2026-10-01", value: 1200 }],
      rows: [{ label: "measured_runs", value: 40 }],
    });
    expect(tableColumns(latency, true)).toEqual(["t", "value"]);
    expect(tableRows(latency, true)).toEqual([{ t: "2026-10-01", value: 1200 }]);
    expect(tableColumns(latency, false)).toEqual(["label", "value"]);
    expect(dataShape(latency)).toEqual({ hasSeries: true, hasGroups: false, hasRows: true });
  });

  it("keeps a budget row's own currency", () => {
    expect(bulletData([{ name: "Ops", actual: 5, threshold: 10, state: "ok", currency: "USD" }])[0].currency).toBe("USD");
  });
});

describe("insights tokens", () => {
  it("cycles through the eight chart tokens and never emits a literal colour", () => {
    expect(chartColor(0)).toBe("hsl(var(--chart-1))");
    expect(chartColor(8)).toBe("hsl(var(--chart-1))");
    expect(chartColor(-1)).toBe("hsl(var(--chart-8))");
  });

  it("maps budget states onto metric states", () => {
    expect(normaliseState("warn")).toBe("watch");
    expect(normaliseState("breached")).toBe("act");
    expect(normaliseState("ok")).toBe("ok");
    expect(normaliseState("weird")).toBeNull();
  });

  it("new insights components use colour tokens, never hex literals", () => {
    for (const [path, src] of Object.entries(componentSources)) {
      expect(src, path).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    }
    expect(pageSource).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe("insights api helpers", () => {
  it("drops empty filters", () => {
    expect(filterParams({ from: "2026-10-01", to: "", compare: "previous", company_id: undefined })).toEqual({
      from: "2026-10-01",
      compare: "previous",
    });
  });

  it("builds an URL-encoded export URL and filename", () => {
    expect(exportPath("cost.spend")).toBe("/insights/metrics/cost.spend/export");
    const url = exportUrl("cost.spend", "xlsx", { from: "2026-10-01", to: "2026-10-30", company_id: "a b&c" });
    expect(url).toBe("/insights/metrics/cost.spend/export?format=xlsx&from=2026-10-01&to=2026-10-30&company_id=a+b%26c");
    expect(exportFilename("cost.spend", "csv", { from: "2026-10-01", to: "2026-10-30" })).toBe(
      "insights-cost.spend-2026-10-01-2026-10-30.csv",
    );
  });

  it("reads the API detail from JSON and Blob error bodies", async () => {
    expect(await readErrorDetail({ response: { data: { detail: "insights_disabled" } } })).toBe("insights_disabled");
    const blob = new Blob([JSON.stringify({ detail: { hint: "Narrow the date range." } })], { type: "application/json" });
    expect(await readErrorDetail({ response: { data: blob } })).toBe("Narrow the date range.");
    expect(await readErrorDetail(new Error("x"))).toBeNull();
  });
});
