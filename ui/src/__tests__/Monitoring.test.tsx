// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Monitoring page (administrators): the live workload and the synthetic checks. Run timelines and the run debugger
 * moved to Insights (the runs explorer and each run's page); the page links there.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Monitoring from "@/pages/Monitoring";

const WORKLOAD = {
  generated_at: "2026-10-02T10:05:00+00:00",
  tracing_enabled: true,
  timeline_enabled: false,
  reviews: { pending: 4, overdue: 1, soonest_due_at: "2026-10-02T10:07:05+00:00", soonest_seconds_left: 125, error: null },
  runs: { window_hours: 1, runs: 7, by_status: { completed: 6, guardrail_blocked: 1 }, p50_duration_ms: 1800, error: null },
  model_calls: { window_hours: 1, calls: 12, failed: 1, p50_latency_ms: 900, error: null },
  guardrails: { window_hours: 1, blocked: null, transformed: null, error: "ProgrammingError" },
};

function renderPage() {
  return render(
    <HelmetProvider>
      <MemoryRouter>
        <Monitoring />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url === "/observability/workload") return Promise.resolve({ data: WORKLOAD });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
});

describe("Monitoring page", () => {
  it("opens on the workload with the review countdown and a part that could not be read", async () => {
    renderPage();
    await screen.findByTestId("workload-reviews");
    expect(screen.queryByTestId("tab-traces")).toBeNull();
    expect(screen.getByTestId("reviews-pending").textContent).toBe("4");
    expect(screen.getByTestId("reviews-overdue").textContent).toBe("1");
    expect(screen.getByTestId("reviews-countdown").textContent).toBe("2m 05s");
    expect(screen.getByTestId("runs-total").textContent).toBe("7");
    expect(screen.getByTestId("model-calls-total").textContent).toBe("12");
    expect(screen.getByTestId("workload-guardrails").textContent).toContain("Unavailable (ProgrammingError)");
  });

  it("sends run timelines to Insights", async () => {
    renderPage();
    expect(screen.getByRole("link", { name: "Insights run page" })).toHaveAttribute(
      "href",
      "/dashboard/insights/explore/runs",
    );
  });

  it("reports a failed load instead of an empty page", async () => {
    mockGet.mockRejectedValue(new Error("boom"));
    renderPage();
    expect((await screen.findByRole("alert")).textContent).toContain("Failed to load the workload.");
  });
});

describe("Monitoring page: the Checks tab", () => {
  it("is absent while synthetic checks are off", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/observability/workload") return Promise.resolve({ data: WORKLOAD });
      if (url === "/observability/checks") {
        return Promise.resolve({ data: { enabled: false, kinds: [], limit: 20, checks: [] } });
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderPage();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/observability/checks"));
    expect(screen.queryByTestId("tab-checks")).not.toBeInTheDocument();
  });

  it("appears where synthetic checks are switched on", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/observability/workload") return Promise.resolve({ data: WORKLOAD });
      if (url === "/observability/checks") {
        return Promise.resolve({ data: { enabled: true, kinds: ["model"], limit: 20, checks: [] } });
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderPage();
    fireEvent.click(await screen.findByTestId("tab-checks"));
    expect(await screen.findByTestId("checks-panel")).toBeInTheDocument();
  });
});
