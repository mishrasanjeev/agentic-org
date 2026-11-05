// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights role views: each role lands on its own view (COO operations, CHRO
 * HR, CMO marketing, merchant commerce) and requests it, an administrator can
 * switch to every view, and the commerce funnel draws its stages in order with
 * the share kept from one stage to the next.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

let mockRole = "coo";
vi.mock("@/contexts/AuthContext", () => ({
  useOptionalRole: () => mockRole,
  useAuth: () => ({
    user: { user_id: "u1", email: "x@example.com", name: "X", role: mockRole, domain: "all", tenant_id: "t1" },
    isAuthenticated: true,
    isHydrating: false,
  }),
}));

import i18n from "@/i18n";
import { funnelData } from "@/lib/insights/chartData";
import type { MetricResponse } from "@/lib/insights/api";
import InsightsDashboard from "@/pages/InsightsDashboard";

const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };

function metric(key: string, partial: Partial<MetricResponse> = {}): MetricResponse {
  return {
    key,
    title: key,
    definition: `Definition of ${key}`,
    unit: "count",
    source: "x",
    data_quality: "measured",
    computed_at: "2026-11-01T04:00:00Z",
    period: { from: "2026-10-03", to: "2026-11-01", granularity: "day" },
    ...partial,
  };
}

function route(views: string[]) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/insights/status")
      return Promise.resolve({ data: { enabled: true, views, platform_operator: false, settings: SETTINGS } });
    if (url === "/insights/explorers") return Promise.resolve({ data: { explorers: [] } });
    const match = url.match(/^\/insights\/views\/(\w+)$/);
    if (match) {
      const view = match[1];
      const charts =
        view === "commerce"
          ? [
              metric("commerce.funnel", {
                title: "Shopping funnel",
                rows: [
                  { label: "carts", value: 200 },
                  { label: "checkouts", value: 120 },
                  { label: "checkouts_completed", value: 60 },
                  { label: "orders", value: 60 },
                  { label: "orders_paid", value: 54 },
                ],
              }),
            ]
          : [];
      return Promise.resolve({
        data: {
          view,
          period: { from: "2026-10-03", to: "2026-11-01" },
          tiles: [metric(`${view}.tile`, { title: `${view} tile`, value: 3 })],
          charts,
          needs_you: [],
        },
      });
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

function renderPage() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/insights"]}>
        <Routes>
          <Route path="/dashboard/insights" element={<InsightsDashboard />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

function viewCalls() {
  return mockGet.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith("/insights/views/"));
}

describe("funnel data", () => {
  it("keeps the API's order and the share kept between stages", () => {
    const stages = funnelData([
      { label: "carts", value: 200 },
      { label: "checkouts", value: 120 },
      { label: "orders", value: 0 },
      { label: "orders_paid", value: 0 },
      { label: "bad", value: "x" },
    ]);
    expect(stages.map((s) => [s.label, s.ofFirst, s.kept])).toEqual([
      ["carts", 100, null],
      ["checkouts", 60, 60],
      ["orders", 0, 0],
      ["orders_paid", 0, null],
    ]);
  });
});

describe("role views", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-01T04:00:00Z"));
    await i18n.changeLanguage("en");
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ["coo", "operations", "Operations"],
    ["chro", "hr", "HR"],
    ["cmo", "marketing", "Marketing"],
    ["merchant", "commerce", "Commerce"],
  ])("%s lands on the %s view", async (role, view, heading) => {
    mockRole = role;
    route([view]);
    renderPage();
    await screen.findByTestId("insights-tiles");
    expect(viewCalls()).toEqual([`/insights/views/${view}`]);
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("gives an administrator a tab for every view", async () => {
    mockRole = "admin";
    route(["executive", "finance", "operations", "hr", "marketing", "commerce"]);
    renderPage();
    await screen.findByTestId("insights-tiles");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Executive",
      "Finance",
      "Operations",
      "HR",
      "Marketing",
      "Commerce",
    ]);
  });

  it("draws the commerce funnel in order with the share kept", async () => {
    mockRole = "merchant";
    route(["commerce"]);
    renderPage();
    const funnel = await screen.findByTestId("funnel");
    const items = Array.from(funnel.querySelectorAll("li")).map((li) => li.textContent);
    expect(items[0]).toContain("Carts");
    expect(items[1]).toContain("Checkouts started");
    expect(items[1]).toContain("60% of the stage before");
    expect(items[4]).toContain("Orders paid");
    await waitFor(() => expect(document.body.textContent).not.toMatch(/NaN|undefined|\[object Object\]/));
  });
});
