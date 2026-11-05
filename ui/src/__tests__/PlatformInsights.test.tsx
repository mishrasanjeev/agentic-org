// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Platform view: tiles, runs chart without an export menu, the tenant table
 * with its reconciled-nights gate, the posture checklist, the range in the
 * URL and the request, the not-available state on 404, and no NaN or
 * undefined text.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import i18n from "@/i18n";
import PlatformInsights from "@/pages/PlatformInsights";
import { normaliseSummary, normaliseTenants, platformRange } from "@/lib/insights/platform";

const SUMMARY = {
  period: { from: "2026-10-03", to: "2026-11-01" },
  totals: { tenants_active: 1, tenants_total: 2, runs: 14, failed: 1, failure_rate: 0.0714, tokens: 1000, cost_usd: 1.75 },
  series: [
    { day: "2026-10-30", tenants_active: 2, runs: 10, failed: 1, tokens: 900, cost_usd: 1.5 },
    { day: "2026-10-31", tenants_active: 1, runs: 4, failed: 0, tokens: 100, cost_usd: 0.25 },
  ],
  operations: {
    exports: { queued: 1, running: 0, ready: 6, failed: 2, expired: 0 },
    export_failure_rate: 0.25,
    oldest_open_minutes: 12,
    tenants_late: 1,
    max_lag_hours: 3.5,
    late_after_hours: 2,
  },
  reconciled_gate_nights: 14,
};
const TENANTS = {
  period: SUMMARY.period,
  reconciled_gate_nights: 14,
  truncated: false,
  tenants: [
    {
      tenant_id: "00000000-0000-4000-8000-0000000000a3",
      tenant_name: "Synthetic Traders",
      plan: "enterprise",
      runs: 14,
      failed: 1,
      failure_rate: 0.0714,
      cost_usd: 1.75,
      active_agents: 3,
      last_active_day: "2026-10-31",
      reconciled_nights: 15,
      gate_met: true,
      last_rollup_status: "reconciled",
    },
    {
      tenant_id: "00000000-0000-4000-8000-0000000000a2",
      tenant_name: "Synthetic Idle Clinic",
      plan: "free",
      runs: 0,
      failed: 0,
      failure_rate: null,
      cost_usd: 0,
      active_agents: 0,
      last_active_day: null,
      reconciled_nights: 3,
      gate_met: false,
      last_rollup_status: "mismatch",
    },
  ],
};
const POSTURE = {
  computed_at: "2026-11-01T06:00:00+00:00",
  ok: false,
  checks: [
    { key: "schema", ok: true },
    { key: "rls", ok: false },
    { key: "role", ok: true },
    { key: "health", ok: true },
  ],
  schema: { ok: true, database_heads: ["v6z107_insights_platform"], code_heads: ["v6z107_insights_platform"] },
  rls: { ok: false, tenant_tables: 120, forced: 119, unforced: ["legacy_table"] },
  role: { ok: true, role: "agenticorg_app", superuser: false, bypass_rls: false },
  health: {
    ok: true,
    window_hours: 24,
    snapshots: 288,
    counts: { healthy: 286, unhealthy: 2, other: 0 },
    latest_status: "healthy",
    latest_at: "2026-11-01T05:55:00+00:00",
    latest_version: "9.9.9",
    latest_commit: "abc1234",
  },
};

function answer(overrides: Record<string, unknown> = {}) {
  const data: Record<string, unknown> = {
    "/platform/insights/summary": SUMMARY,
    "/platform/insights/tenants": TENANTS,
    "/platform/insights/posture": POSTURE,
    ...overrides,
  };
  mockGet.mockImplementation((url: string) => {
    if (!(url in data)) return Promise.reject(new Error(`unexpected ${url}`));
    const v = data[url];
    if (v && typeof v === "object" && "response" in (v as object)) return Promise.reject(v);
    return Promise.resolve({ data: v });
  });
}

let lastSearch = "";
function LocationProbe() {
  lastSearch = useLocation().search;
  return null;
}

function renderPage(initial = "/dashboard/platform") {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route
            path="/dashboard/platform"
            element={
              <>
                <PlatformInsights />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

function expectNoBrokenText(container: HTMLElement) {
  const text = container.textContent || "";
  expect(text).not.toMatch(/NaN|undefined|Infinity|\[object Object\]/);
}

describe("PlatformInsights", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    lastSearch = "";
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-01T06:00:00Z"));
    await i18n.changeLanguage("en");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows skeletons while loading", () => {
    mockGet.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId("platform-loading")).toBeInTheDocument();
  });

  it("renders tiles, the runs chart, the tenant table and the posture from the API", async () => {
    answer();
    const { container } = renderPage();
    const tiles = await screen.findByTestId("platform-tiles");
    const values = within(tiles).getAllByTestId("tile-value").map((n) => n.textContent);
    expect(values).toEqual(["1", "14", "7.1%", "$1.75"]);
    expect(
      within(tiles)
        .getAllByTestId("decision-tile")
        .map((n) => n.getAttribute("data-metric-key")),
    ).toEqual(["platform.tenants_active", "platform.runs", "platform.failure_rate", "platform.spend"]);

    // The 30-day UTC range is sent to both data routes.
    for (const path of ["/platform/insights/summary", "/platform/insights/tenants"]) {
      expect(mockGet).toHaveBeenCalledWith(path, { params: { from: "2026-10-03", to: "2026-11-01" } });
    }
    expect(mockGet).toHaveBeenCalledWith("/platform/insights/posture");

    const chart = screen.getByTestId("chart-card");
    expect(within(chart).getByText("Runs per day")).toBeInTheDocument();
    // The platform figures export through their own endpoint: CSV, XLSX and JSON, never a PDF.
    fireEvent.click(within(chart).getByRole("button", { name: "Export Runs per day" }));
    const formats = within(chart)
      .getAllByRole("menuitem")
      .map((item) => item.getAttribute("data-format"));
    expect(formats).toEqual(["csv", "xlsx", "json", "parquet", "svg", "png"]);
    fireEvent.keyDown(within(chart).getByRole("menu"), { key: "Escape" });
    fireEvent.click(within(chart).getByRole("button", { name: /view data/i }));
    expect(within(chart).getByTestId("insights-data-table")).toBeInTheDocument();

    const rows = screen.getAllByTestId("platform-tenant-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Synthetic Traders")).toBeInTheDocument();
    expect(within(rows[0]).getByText("7.1%")).toBeInTheDocument();
    expect(within(rows[0]).getByText("$1.75")).toBeInTheDocument();
    // No runs: the failure rate and last active day are shown as missing, never as 0.
    expect(within(rows[1]).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    const bullets = screen.getAllByTestId("gate-bullet");
    expect(bullets.map((b) => b.getAttribute("data-met"))).toEqual(["true", "false"]);
    expect(bullets[0]).toHaveAttribute("aria-valuenow", "14");
    expect(bullets[0]).toHaveAttribute("aria-valuemax", "14");
    expect(bullets[1]).toHaveAttribute("aria-valuetext", "3 of 14 reconciled nights");

    const checks = screen.getAllByTestId("posture-check");
    expect(checks.map((c) => c.getAttribute("data-ok"))).toEqual(["true", "false", "true", "true"]);
    expect(screen.getByText(/Not forced: legacy_table/)).toBeInTheDocument();
    expect(screen.getByText(/288 snapshots in the last 24 hours, 2 unhealthy/)).toBeInTheDocument();
    expectNoBrokenText(container);
  });

  it("shows the dashboard's own export and rollup figures", async () => {
    answer();
    renderPage();
    const panel = await screen.findByTestId("platform-operations");
    expect(panel).toHaveTextContent("6 ready, 2 failed, 0 expired, 1 still open.");
    expect(panel).toHaveTextContent("25.0%");
    expect(panel).toHaveTextContent("12 min");
    expect(panel).toHaveTextContent("Tenants late: 1. The furthest behind by 3.5 hours.");
  });

  it("sorts the tenant table from its column headers", async () => {
    answer();
    renderPage();
    await screen.findAllByTestId("platform-tenant-row");
    const header = screen.getByRole("button", { name: /sort by runs/i });
    fireEvent.click(header);
    let rows = screen.getAllByTestId("platform-tenant-row");
    expect(within(rows[0]).getByText("Synthetic Idle Clinic")).toBeInTheDocument();
    expect(header.closest("th")).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(header);
    rows = screen.getAllByTestId("platform-tenant-row");
    expect(within(rows[0]).getByText("Synthetic Traders")).toBeInTheDocument();
    expect(header.closest("th")).toHaveAttribute("aria-sort", "descending");
  });

  it("says the view is not available on 404 and shows nothing else", async () => {
    answer({ "/platform/insights/summary": { response: { status: 404, data: { detail: "Not Found" } } } });
    const { container } = renderPage();
    expect(await screen.findByText("The platform view is not available")).toBeInTheDocument();
    expect(screen.queryByTestId("platform-tiles")).toBeNull();
    expect(screen.queryByTestId("platform-tenants-table")).toBeNull();
    expect(screen.queryByRole("group", { name: "Period" })).toBeNull();
    expectNoBrokenText(container);
  });

  it("shows the error with a retry for other failures", async () => {
    answer({ "/platform/insights/posture": { response: { status: 500, data: { detail: "posture unavailable" } } } });
    renderPage();
    expect(await screen.findByText("The platform view could not be loaded")).toBeInTheDocument();
    expect(screen.getByText("posture unavailable")).toBeInTheDocument();
    answer();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("platform-tiles")).toBeInTheDocument();
  });

  it("shows the hint instead of zeros when no tenant ran anything", async () => {
    answer({
      "/platform/insights/summary": {
        ...SUMMARY,
        totals: { tenants_active: 0, tenants_total: 2, runs: 0, failed: 0, failure_rate: null, tokens: 0, cost_usd: 0 },
        series: [],
      },
    });
    const { container } = renderPage();
    const tiles = await screen.findByTestId("platform-tiles");
    expect(within(tiles).queryAllByTestId("tile-value")).toHaveLength(0);
    expect(within(tiles).getAllByText("No tenant recorded a run in this period.")).toHaveLength(4);
    expectNoBrokenText(container);
  });

  it("keeps the range in the URL and requests it", async () => {
    answer();
    renderPage();
    await screen.findByTestId("platform-tiles");
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith("/platform/insights/summary", { params: { from: "2026-10-26", to: "2026-11-01" } }),
    );
    expect(lastSearch).toBe("?range=7d");
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("reads an unknown range as the 30-day default", async () => {
    answer();
    renderPage("/dashboard/platform?range=365d");
    await screen.findByTestId("platform-tiles");
    expect(mockGet).toHaveBeenCalledWith("/platform/insights/tenants", { params: { from: "2026-10-03", to: "2026-11-01" } });
  });

  it("renders in Hindi", async () => {
    await i18n.changeLanguage("hi");
    answer();
    const { container } = renderPage();
    await screen.findByTestId("platform-tiles");
    expect(screen.getAllByText("प्लेटफ़ॉर्म").length).toBeGreaterThan(0);
    expect(screen.getByText("सक्रिय टेनेंट")).toBeInTheDocument();
    expectNoBrokenText(container);
  });
});

describe("platform client helpers", () => {
  it("computes UTC ranges with today included", () => {
    const now = new Date("2026-11-01T23:30:00Z");
    expect(platformRange("7d", now)).toEqual({ from: "2026-10-26", to: "2026-11-01" });
    expect(platformRange("90d", now)).toEqual({ from: "2026-08-04", to: "2026-11-01" });
  });

  it("normalises missing or malformed fields without inventing numbers", () => {
    const summary = normaliseSummary({ totals: { runs: "12", failure_rate: "x" }, series: [{ day: 3 }, { day: "2026-10-01" }] });
    expect(summary.totals.runs).toBe(12);
    expect(summary.totals.failure_rate).toBeNull();
    expect(summary.series).toHaveLength(1);
    expect(summary.reconciled_gate_nights).toBe(14);
    expect(summary.operations.exports.ready).toBe(0);
    expect(summary.operations.export_failure_rate).toBeNull();
    expect(summary.operations.oldest_open_minutes).toBeNull();
    expect(summary.operations.late_after_hours).toBe(2);
    const tenants = normaliseTenants({ tenants: [{ tenant_id: "t", failure_rate: null }, { name: "no id" }] });
    expect(tenants.tenants).toHaveLength(1);
    expect(tenants.tenants[0].failure_rate).toBeNull();
    expect(tenants.tenants[0].last_active_day).toBeNull();
    expect(normaliseTenants(null).tenants).toEqual([]);
  });
});
