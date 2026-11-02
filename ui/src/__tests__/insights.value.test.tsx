// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Money saved and alerts: the waterfall steps, hours formatting, acknowledging
 * an alert from the needs-you list, the Money saved page before and after the
 * assumptions are confirmed (administrator controls, CFO read-only), and the
 * approvals page recording what a person has seen.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockPut = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    put: (...args: unknown[]) => mockPut(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import i18n from "@/i18n";
import en from "../locales/en.json";
import hi from "../locales/hi.json";
import NeedsYouList from "@/components/insights/NeedsYouList";
import { waterfallData } from "@/lib/insights/chartData";
import { formatMetricValue } from "@/lib/insights/format";
import type { ValueResponse } from "@/lib/insights/value";
import Approvals from "@/pages/Approvals";
import InsightsValue from "@/pages/InsightsValue";

const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };

function valueResponse(partial: Partial<ValueResponse> = {}, confirmed = false): ValueResponse {
  return {
    period: { from: "2026-10-03", to: "2026-11-01" },
    currency: "INR",
    baselines: [
      {
        id: "b1",
        agent_type: "ap_processor",
        task_type: "*",
        baseline_minutes: 12,
        role: "finance_associate",
        setup_cost: null,
        source: "seeded",
        confirmed,
        confirmed_at: null,
        updated_at: null,
      },
    ],
    rates: [
      {
        id: "r1",
        role: "finance_associate",
        hourly_cost: 136.63,
        currency: "INR",
        source: "seeded",
        citation: "MoSPI PLFS Annual Report 2025 Table 51",
        confirmed,
        confirmed_at: null,
        updated_at: null,
      },
    ],
    roi_floor: 5000,
    roi_floor_custom: false,
    summary: {
      currency: "INR",
      confirmed,
      confidence: confirmed ? "high" : "low",
      completed_tasks: 600,
      failed_tasks: 10,
      unpriced_task_types: ["custom_bot"],
      hours_saved: confirmed ? 120 : null,
      waterfall: confirmed
        ? [
            { label: "gross_saved", value: 18000 },
            { label: "spend", value: -6000 },
            { label: "review_cost", value: -1000 },
            { label: "error_cost", value: -300 },
            { label: "net_saved", value: 10700 },
          ]
        : null,
      roi_percent: confirmed ? 178.3 : null,
      review_items: 4,
      spend: 6000,
      hints: [],
      unconfirmed: confirmed ? { baseline_ids: [], rate_ids: [] } : { baseline_ids: ["b1"], rate_ids: ["r1"] },
    },
    can_edit: true,
    lines: [],
    ...partial,
  };
}

function renderValue() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/insights/value"]}>
        <Routes>
          <Route path="/dashboard/insights/value" element={<InsightsValue />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

describe("money saved helpers", () => {
  it("draws gross to net from the running total", () => {
    const steps = waterfallData([
      { label: "gross_saved", value: 18000 },
      { label: "spend", value: -6000 },
      { label: "review_cost", value: -1000 },
      { label: "error_cost", value: -300 },
      { label: "net_saved", value: 10700 },
      { label: "junk", value: "x" },
    ]);
    expect(steps.map((s) => [s.label, s.base, s.size, s.kind])).toEqual([
      ["gross_saved", 0, 18000, "total"],
      ["spend", 12000, 6000, "decrease"],
      ["review_cost", 11000, 1000, "decrease"],
      ["error_cost", 10700, 300, "decrease"],
      ["net_saved", 0, 10700, "total"],
    ]);
    // A loss: the net bar hangs below zero.
    const loss = waterfallData([
      { label: "gross_saved", value: 100 },
      { label: "spend", value: -300 },
      { label: "net_saved", value: -200 },
    ]);
    expect(loss[1]).toMatchObject({ base: -200, size: 300 });
    expect(loss[2]).toMatchObject({ base: -200, size: 200 });
  });

  it("formats hours and keeps the strings in both languages", () => {
    expect(formatMetricValue(1234.56, "hours")).toBe("1,234.6 h");
    expect(formatMetricValue(null, "hours")).toBe("—");
    const leaves = (obj: unknown, prefix = ""): string[] =>
      Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
        v && typeof v === "object" ? leaves(v, `${prefix}${k}.`) : [`${prefix}${k}`],
      );
    expect(leaves(hi.insightsValue).sort()).toEqual(leaves(en.insightsValue).sort());
    expect(hi.insights.waterfall.net_saved).not.toBe(en.insights.waterfall.net_saved);
  });
});

describe("NeedsYouList alerts", () => {
  beforeEach(async () => {
    mockPost.mockReset();
    await i18n.changeLanguage("en");
  });

  it("acknowledges an alert in place and keeps other items", async () => {
    mockPost.mockResolvedValue({ data: { state: "acked" } });
    render(
      <MemoryRouter>
        <NeedsYouList
          items={[
            { kind: "alert", id: "a1", title: "Connector tally is unhealthy", state: "act", link: "/dashboard/insights/explore/connectors" },
            { kind: "approval", title: "Approve INV-1", state: "watch", link: "/dashboard/approvals" },
          ]}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge: Connector tally is unhealthy" }));
    await waitFor(() => expect(screen.queryByText("Connector tally is unhealthy")).not.toBeInTheDocument());
    expect(mockPost).toHaveBeenCalledWith("/insights/alerts/a1/ack");
    expect(screen.getByText("Approve INV-1")).toBeInTheDocument();
  });

  it("says so when acknowledging fails", async () => {
    mockPost.mockRejectedValue({ response: { status: 500 } });
    render(
      <MemoryRouter>
        <NeedsYouList items={[{ kind: "alert", id: "a1", title: "Stalled workflows", state: "act" }]} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge: Stalled workflows" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not acknowledge");
    expect(screen.getByText("Stalled workflows")).toBeInTheDocument();
  });
});

describe("InsightsValue", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
    await i18n.changeLanguage("en");
  });

  function route(response: ValueResponse) {
    mockGet.mockImplementation((url: string) => {
      if (url === "/insights/status")
        return Promise.resolve({ data: { enabled: true, views: ["executive"], platform_operator: false, settings: SETTINGS } });
      if (url === "/insights/value") return Promise.resolve({ data: response });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
  }

  it("hides the figures until confirmed and confirms what the period uses", async () => {
    route(valueResponse());
    mockPost.mockResolvedValue({ data: { confirmed_baselines: ["b1"], confirmed_rates: ["r1"] } });
    renderValue();
    const unconfirmed = await screen.findByTestId("value-unconfirmed");
    expect(unconfirmed).toHaveTextContent("1 baselines and 1 hourly rates");
    expect(screen.queryByTestId("value-net")).not.toBeInTheDocument();
    expect(screen.getByText("Agent types with no baseline, not valued: custom_bot.")).toBeInTheDocument();
    expect(screen.getByText("MoSPI PLFS Annual Report 2025 Table 51")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm all used (2)" }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/insights/value/confirm", { baseline_ids: ["b1"], rate_ids: ["r1"] }),
    );
  });

  it("shows net saved and the waterfall once confirmed, and saves an edited baseline", async () => {
    route(valueResponse({}, true));
    mockPatch.mockResolvedValue({ data: {} });
    renderValue();
    expect(await screen.findByTestId("value-net")).toHaveTextContent("10,700");
    expect(screen.getByText("178.3%")).toBeInTheDocument();
    const row = screen.getByTestId("baseline-ap_processor");
    fireEvent.change(within(row).getByLabelText("Minutes per task for ap_processor"), { target: { value: "20" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith("/insights/value/baselines/b1", { baseline_minutes: 20, role: "finance_associate" }),
    );
    expect(document.body.textContent).not.toMatch(/NaN|undefined|\[object Object\]/);
  });

  it("is read-only for the CFO and says who confirms", async () => {
    route(valueResponse({ can_edit: false }));
    renderValue();
    expect(await screen.findByText("A tenant administrator confirms them.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Minutes per task for ap_processor")).not.toBeInTheDocument();
  });

  it("explains a refusal", async () => {
    mockGet.mockImplementation((url: string) =>
      url === "/insights/value"
        ? Promise.reject({ response: { status: 403, data: { detail: "insights_value_forbidden" } } })
        : Promise.resolve({ data: { enabled: true, views: [], platform_operator: false, settings: SETTINGS } }),
    );
    renderValue();
    expect(await screen.findByText("Money saved is for administrators and the CFO")).toBeInTheDocument();
  });
});

describe("Approvals page", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("records that a person has seen the pending approvals", async () => {
    mockGet.mockResolvedValue({
      data: {
        items: [
          {
            id: "h1",
            title: "Approve INV-1",
            trigger_type: "low_confidence",
            priority: "normal",
            status: "pending",
            assignee_role: "finance",
            decision_options: { options: ["approve", "reject"] },
            context: {},
            created_at: "2026-11-01T04:00:00Z",
            expires_at: "2026-11-05T04:00:00Z",
          },
        ],
        total: 1,
        pages: 1,
      },
    });
    mockPost.mockResolvedValue({ data: { marked: 1 } });
    render(
      <MemoryRouter>
        <Approvals />
      </MemoryRouter>,
    );
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/approvals/viewed", { ids: ["h1"] }));
  });
});
