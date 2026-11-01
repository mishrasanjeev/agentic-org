// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights page: role tabs (admin both, cfo finance only), filters in the
 * URL and the request, disabled / forbidden / empty states, no NaN or
 * undefined text, and the export menu URL.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

let mockRole = "admin";
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { user_id: "u1", email: "x@example.com", name: "X", role: mockRole, domain: "all", tenant_id: "t1" },
    isAuthenticated: true,
    isHydrating: false,
  }),
}));

import i18n from "@/i18n";
import InsightsDashboard from "@/pages/InsightsDashboard";
import { setSelectedCompanyId } from "@/lib/companyScope";
import type { InsightsViewResponse, MetricResponse } from "@/lib/insights/api";

const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };

function m(key: string, partial: Partial<MetricResponse> = {}): MetricResponse {
  return {
    key,
    title: key,
    definition: `Definition of ${key}`,
    unit: "count",
    currency: "INR",
    source: "insights_daily",
    data_quality: "measured",
    computed_at: "2026-11-01T04:00:00Z",
    period: { from: "2026-10-03", to: "2026-11-01", granularity: "day" },
    compare: { from: "2026-09-03", to: "2026-10-02" },
    ...partial,
  };
}

function executiveView(): InsightsViewResponse {
  return {
    view: "executive",
    period: { from: "2026-10-03", to: "2026-11-01" },
    tiles: [
      m("runs.volume", { title: "Runs", value: 1520, compare_value: 1400, state: "ok" }),
      m("runs.success_rate", { title: "Success rate", unit: "percent", value: 96.25, compare_value: 95, state: "ok" }),
      m("runs.latency", { title: "p95 latency", unit: "duration_ms", value: 12500, compare_value: null }),
      m("approvals.pending", {
        title: "Pending approvals",
        value: null,
        data_quality: "no_data",
        hint: "No approvals were requested in this period.",
      }),
      m("cost.spend", { title: "Spend", unit: "inr", value: 245000, compare_value: 200000, state: "watch" }),
    ],
    charts: [
      m("runs.volume", {
        title: "Runs by status",
        series: [
          { t: "2026-10-30", value: 40, group: "completed" },
          { t: "2026-10-30", value: 3, group: "failed" },
          { t: "2026-10-31", value: 52, group: "completed" },
        ],
      }),
      m("cost.spend", {
        title: "Daily spend",
        unit: "inr",
        data_quality: "no_data",
        hint: "Turn on spend metering to see cost.",
      }),
    ],
    needs_you: [{ kind: "approval", title: "Vendor payment approval", age_seconds: 90000, link: "/dashboard/approvals" }],
  };
}

// Shapes as core/insights/metrics_cost.py returns them.
function financeView(): InsightsViewResponse {
  return {
    view: "finance",
    period: { from: "2026-10-03", to: "2026-11-01" },
    tiles: [
      m("cost.spend", { title: "Spend", unit: "inr", value: 245000, compare_value: 200000 }),
      m("cost.per_run", { title: "Cost per run", unit: "inr", value: 161.18, compare_value: 150 }),
      m("cost.forecast", { title: "Forecast", unit: "inr", value: 300000, data_quality: "estimated" }),
      m("cost.data_quality", {
        title: "Priced usage",
        unit: "percent",
        value: null,
        data_quality: "no_data",
        hint: "Turn on AI spend metering to see how much usage is priced.",
      }),
    ],
    charts: [
      m("cost.by_agent", {
        title: "Spend by agent",
        unit: "inr",
        rows: [
          { agent_id: "a1", agent_name: "AP clerk", value: 120000 },
          { agent_id: null, agent_name: "Unattributed", value: 5000 },
        ],
      }),
      m("cost.forecast", {
        title: "Month-end forecast",
        unit: "inr",
        data_quality: "estimated",
        value: 300000,
        rows: [{ label: "forecast", point: 300000, low: 260000, high: 340000, actual: 20000 }],
        series: [{ t: "2026-11-01", value: 20000 }],
      }),
      m("cost.budgets", {
        title: "Budgets",
        unit: "count",
        currency: "USD",
        value: 1,
        state: "act",
        rows: [
          { id: "b1", name: "Ops monthly", period: "monthly", threshold: 100, actual: 125.5, used_pct: 125.5, state: "breached", currency: "USD" },
          { id: "b2", name: "Daily cap", period: "daily", threshold: 10, actual: 2, used_pct: 20, state: "ok", currency: "USD" },
        ],
      }),
      m("tokens.by_unit", {
        title: "Tokens by unit",
        unit: "tokens",
        value: 1500000,
        rows: [
          { unit: "input", value: 1200000 },
          { unit: "output", value: 300000 },
        ],
      }),
    ],
    needs_you: [
      { kind: "budget", title: "Ops monthly", link: "/dashboard/costs", state: "act", detail: "125.5% of the monthly budget used" },
    ],
  };
}

function routeGet(views: Record<string, unknown>, status: unknown = { enabled: true, views: ["executive", "finance"], platform_operator: false, settings: SETTINGS }) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/insights/status") return Promise.resolve({ data: status });
    const match = url.match(/^\/insights\/views\/(\w+)$/);
    if (match) {
      const v = views[match[1]];
      if (v instanceof Error || (v && typeof v === "object" && "response" in (v as object))) return Promise.reject(v);
      return Promise.resolve({ data: v });
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

let lastSearch = "";
function LocationProbe() {
  const loc = useLocation();
  lastSearch = loc.search;
  return null;
}

function renderPage(initial = "/dashboard/insights") {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route
            path="/dashboard/insights"
            element={
              <>
                <InsightsDashboard />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

function viewCalls() {
  return mockGet.mock.calls.filter((c) => String(c[0]).startsWith("/insights/views/"));
}

function expectNoBadText() {
  expect(document.body.textContent).not.toMatch(/NaN|undefined|Infinity|\[object Object\]/);
}

describe("InsightsDashboard", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockRole = "admin";
    lastSearch = "";
    localStorage.clear();
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
    expect(screen.getByTestId("insights-loading")).toBeInTheDocument();
    expect(screen.getAllByTestId("tile-skeleton").length).toBeGreaterThan(0);
  });

  it("admin sees both tabs, lands on Executive and gets tiles, charts and needs-you", async () => {
    routeGet({ executive: executiveView(), finance: financeView() });
    renderPage();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Executive", "Finance"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    await screen.findByTestId("insights-tiles");

    const tiles = screen.getAllByTestId("decision-tile");
    expect(tiles.map((t) => t.getAttribute("data-metric-key"))).toEqual([
      "runs.volume",
      "runs.success_rate",
      "runs.latency",
      "approvals.pending",
      "cost.spend",
    ]);
    expect(within(tiles[1]).getByTestId("tile-value")).toHaveTextContent("96.3%");
    expect(within(tiles[2]).getByTestId("tile-value")).toHaveTextContent("12.5 s");
    expect(within(tiles[4]).getByTestId("tile-value")).toHaveTextContent("₹2,45,000");
    expect(within(tiles[3]).getByTestId("insights-empty")).toHaveTextContent("No approvals were requested in this period.");
    expect(screen.getByText("Turn on spend metering to see cost.")).toBeInTheDocument();
    expect(screen.getByText("Vendor payment approval")).toBeInTheDocument();
    expect(screen.getByTestId("insights-updated")).toBeInTheDocument();

    // Default request: last 30 tenant-local days (IST), previous-period comparison, no company.
    expect(viewCalls()[0]).toEqual([
      "/insights/views/executive",
      { params: { from: "2026-10-03", to: "2026-11-01", compare: "previous" } },
    ]);
    expectNoBadText();
  });

  it("switching tabs loads the Finance view and writes it to the URL", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    routeGet({ executive: executiveView(), finance: financeView() });
    renderPage();
    await screen.findByTestId("insights-tiles");
    await user.click(screen.getByRole("tab", { name: "Finance" }));
    await waitFor(() => expect(viewCalls().some((c) => c[0] === "/insights/views/finance")).toBe(true));
    expect(lastSearch).toContain("view=finance");
    await screen.findByText("Month-end forecast");
    expect(screen.getByRole("tab", { name: "Finance" })).toHaveAttribute("aria-selected", "true");
    expectNoBadText();
  });

  it("arrow keys move between tabs", async () => {
    routeGet({ executive: executiveView(), finance: financeView() });
    renderPage();
    const exec = await screen.findByRole("tab", { name: "Executive" });
    exec.focus();
    fireEvent.keyDown(exec, { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Finance" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("tab", { name: "Finance" })).toHaveFocus();
  });

  it("cfo sees Finance only, with no tab list and no Executive request", async () => {
    mockRole = "cfo";
    routeGet({ finance: financeView() }, { enabled: true, views: ["finance"], platform_operator: false, settings: SETTINGS });
    renderPage();
    await screen.findByText("Spend by agent");
    expect(screen.queryByRole("tablist")).toBeNull();
    // Budgets: amounts in the row's own currency, not the metric's count unit.
    expect(screen.getByTestId("bullet-bars")).toHaveTextContent("$125.50 / $100.00");
    expect(screen.getByText("125.5% of the monthly budget used")).toBeInTheDocument();
    // Rows-only token totals rank as bars and summarise in text.
    const tokens = screen.getAllByTestId("chart-card").find((c) => c.getAttribute("data-metric-key") === "tokens.by_unit");
    expect(tokens?.querySelector("[data-chart-kind]")).toHaveAttribute("data-chart-kind", "hbar");
    expect(within(tokens as HTMLElement).getByTestId("chart-summary")).toHaveTextContent(
      "Tokens by unit: 2 items. Largest is input with 1.2M.",
    );
    const forecast = screen.getAllByTestId("chart-card").find((c) => c.getAttribute("data-metric-key") === "cost.forecast");
    expect(within(forecast as HTMLElement).getByTestId("forecast-text")).toHaveTextContent("₹3,00,000");
    expect(screen.getByText("Turn on AI spend metering to see how much usage is priced.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Finance" })).toBeInTheDocument();
    expect(viewCalls().map((c) => c[0])).toEqual(["/insights/views/finance"]);
    expectNoBadText();
  });

  it("cfo asking for ?view=executive still gets Finance", async () => {
    mockRole = "cfo";
    routeGet({ finance: financeView() }, { enabled: true, views: ["executive", "finance"], platform_operator: false, settings: SETTINGS });
    renderPage("/dashboard/insights?view=executive");
    await screen.findByText("Spend by agent");
    expect(viewCalls().map((c) => c[0])).toEqual(["/insights/views/finance"]);
  });

  it("a role without an Insights view sees the forbidden state", async () => {
    mockRole = "analyst";
    routeGet({}, { enabled: true, views: [], platform_operator: false, settings: SETTINGS });
    renderPage();
    expect(await screen.findByText("No Insights view for your role")).toBeInTheDocument();
    expect(viewCalls()).toHaveLength(0);
  });

  it("shows the disabled state when status says off", async () => {
    routeGet({}, { enabled: false, views: [], platform_operator: false });
    renderPage();
    expect(await screen.findByText("Insights is turned off")).toBeInTheDocument();
    expect(viewCalls()).toHaveLength(0);
  });

  it("shows the disabled state when the view answers 404", async () => {
    routeGet({ executive: { response: { status: 404, data: { detail: "insights_disabled" } } } });
    renderPage();
    expect(await screen.findByText("Insights is turned off")).toBeInTheDocument();
  });

  it("shows the forbidden state when the view answers 403", async () => {
    routeGet({ executive: { response: { status: 403, data: { detail: "insights_view_forbidden" } } } });
    renderPage();
    expect(await screen.findByText("No Insights view for your role")).toBeInTheDocument();
  });

  it("shows the API error and retries", async () => {
    let calls = 0;
    mockGet.mockImplementation((url: string) => {
      if (url === "/insights/status")
        return Promise.resolve({ data: { enabled: true, views: ["executive"], platform_operator: false, settings: SETTINGS } });
      calls += 1;
      if (calls === 1) return Promise.reject({ response: { status: 422, data: { detail: "company_id is not a company of this tenant" } } });
      return Promise.resolve({ data: executiveView() });
    });
    renderPage();
    expect(await screen.findByText("company_id is not a company of this tenant")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByTestId("insights-tiles");
  });

  it("sends the company from the header switcher and the chosen range and compare", async () => {
    localStorage.setItem("company_id", "11111111-2222-3333-4444-555555555555");
    routeGet({ executive: executiveView() });
    renderPage("/dashboard/insights?range=7d&compare=none");
    await screen.findByTestId("insights-tiles");
    expect(viewCalls()[0][1]).toEqual({
      params: {
        from: "2026-10-26",
        to: "2026-11-01",
        compare: "none",
        company_id: "11111111-2222-3333-4444-555555555555",
      },
    });
    expect(screen.getByTestId("insights-company-scope")).toBeInTheDocument();
  });

  it("quarter-to-date follows the tenant's fiscal year", async () => {
    routeGet({ executive: executiveView() });
    renderPage("/dashboard/insights?range=qtd");
    await screen.findByTestId("insights-tiles");
    expect(viewCalls()[0][1]).toEqual({ params: { from: "2026-10-01", to: "2026-11-01", compare: "previous" } });
  });

  it("refreshes scope when the header selects or clears a company after the page loads", async () => {
    routeGet({ executive: executiveView() });
    renderPage("/dashboard/insights?range=custom&from=2026-10-01&to=2026-10-31");
    await screen.findByTestId("insights-tiles");
    const company = "11111111-2222-3333-4444-555555555555";
    act(() => setSelectedCompanyId(company));
    await waitFor(() => expect(viewCalls().at(-1)?.[1]).toEqual({
      params: { from: "2026-10-01", to: "2026-10-31", compare: "previous", company_id: company },
    }));
    expect(screen.getByTestId("insights-company-scope")).toBeInTheDocument();
    act(() => setSelectedCompanyId(""));
    await waitFor(() => expect(viewCalls().at(-1)?.[1]).toEqual({
      params: { from: "2026-10-01", to: "2026-10-31", compare: "previous" },
    }));
    expect(screen.queryByTestId("insights-company-scope")).not.toBeInTheDocument();
  });

  it("a custom range from the URL is used; an invalid one falls back to 30 days", async () => {
    routeGet({ executive: executiveView() });
    const { unmount } = renderPage("/dashboard/insights?range=custom&from=2026-09-01&to=2026-09-30");
    await screen.findByTestId("insights-tiles");
    expect(viewCalls()[0][1]).toEqual({ params: { from: "2026-09-01", to: "2026-09-30", compare: "previous" } });
    unmount();
    mockGet.mockClear();
    renderPage("/dashboard/insights?range=custom&from=2026-09-30&to=2026-09-01");
    await screen.findByTestId("insights-tiles");
    expect(viewCalls()[0][1]).toEqual({ params: { from: "2026-10-03", to: "2026-11-01", compare: "previous" } });
  });

  it("changing the preset updates the URL and refetches", async () => {
    routeGet({ executive: executiveView() });
    renderPage();
    await screen.findByTestId("insights-tiles");
    fireEvent.change(screen.getByLabelText("Date range"), { target: { value: "mtd" } });
    await waitFor(() => expect(lastSearch).toContain("range=mtd"));
    await waitFor(() =>
      expect(viewCalls()[viewCalls().length - 1]).toEqual([
        "/insights/views/executive",
        { params: { from: "2026-11-01", to: "2026-11-01", compare: "previous" } },
      ]),
    );
  });

  it("chart export menu hits the export endpoint with the page filters", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    routeGet({ executive: executiveView() });
    renderPage("/dashboard/insights?range=7d");
    await screen.findByTestId("insights-tiles");
    mockGet.mockImplementation((url: string) =>
      url.endsWith("/export") ? Promise.resolve({ data: new Blob(["x"]) }) : Promise.reject(new Error(url)),
    );
    await user.click(screen.getByRole("button", { name: "Export Runs by status" }));
    await user.click(screen.getByRole("menuitem", { name: "JSON" }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith("/insights/metrics/runs.volume/export", {
        params: { format: "json", from: "2026-10-26", to: "2026-11-01", compare: "previous" },
        responseType: "blob",
      }),
    );
    clickSpy.mockRestore();
  });

  it("renders in Hindi", async () => {
    routeGet({ executive: executiveView() });
    await act(async () => {
      await i18n.changeLanguage("hi");
    });
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "इनसाइट्स" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "कार्यकारी" })).toBeInTheDocument();
    await screen.findByTestId("insights-tiles");
    expect(screen.getByText("आपकी ज़रूरत है")).toBeInTheDocument();
    expectNoBadText();
  });
});
