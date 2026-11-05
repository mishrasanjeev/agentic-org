// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * The benchmark card: an organisation that has not opted in sees why the card
 * is empty (and an administrator can opt in); an opted-in one sees its figures
 * against each published cohort with its position; a refusal hides the card.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPut = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import i18n from "@/i18n";
import BenchmarkCard from "@/components/insights/BenchmarkCard";
import { formatBenchmark, normaliseCard } from "@/lib/insights/benchmarks";

const WINDOW = { from: "2026-10-05", to: "2026-11-03" };

function httpError(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail: "nope" } } });
}

beforeEach(async () => {
  mockGet.mockReset();
  mockPut.mockReset();
  await i18n.changeLanguage("en");
});

describe("BenchmarkCard", () => {
  it("explains opting in and lets an administrator take part", async () => {
    mockGet.mockResolvedValueOnce({
      data: { opted_in: false, can_change: true, participating: 3, min_tenants: 20, min_runs: 100, window: WINDOW, metrics: [] },
    });
    mockGet.mockResolvedValueOnce({
      data: { opted_in: true, can_change: true, participating: 4, min_tenants: 20, window: WINDOW, metrics: [] },
    });
    mockPut.mockResolvedValue({ data: { opted_in: true, changed: true } });
    render(<BenchmarkCard />);
    expect(await screen.findByTestId("benchmarks-not-opted-in")).toHaveTextContent("at least 20 organisations");
    expect(screen.getByText(/Organisations taking part: 3/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("benchmarks-toggle"));
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith("/insights/benchmarks/opt-in", { opt_in: true }));
    expect(await screen.findByRole("button", { name: "Stop taking part" })).toBeInTheDocument();
  });

  it("places the organisation's figures against each cohort", async () => {
    mockGet.mockResolvedValue({
      data: {
        opted_in: true,
        can_change: false,
        participating: 24,
        min_tenants: 20,
        min_runs: 100,
        window: WINDOW,
        runs: 200,
        hint: null,
        metrics: [
          {
            key: "success_rate",
            title: "Run success rate",
            unit: "percent",
            higher_is_better: true,
            value: 60,
            bands: [
              { cohort: "all", p25: 52, p50: 55, p75: 58, tenants: 24, position: "above_p75" },
              { cohort: "plan:free", p25: 51, p50: 55, p75: 57.5, tenants: 21, position: "above_p75" },
            ],
          },
          { key: "avg_run_ms", title: "Average run time", unit: "duration_ms", value: 900, bands: [] },
        ],
      },
    });
    render(<BenchmarkCard />);
    const row = await screen.findByTestId("benchmark-success_rate");
    expect(within(row).getByText("60.0%")).toBeInTheDocument();
    expect(within(row).getByText(/All taking part \(24\): 25th 52\.0%, median 55\.0%, 75th 58\.0%/)).toHaveTextContent(
      "above the 75th percentile",
    );
    expect(within(row).getByText(/Plan free \(21\)/)).toBeInTheDocument();
    expect(within(screen.getByTestId("benchmark-avg_run_ms")).getByText("No cohort large enough yet")).toBeInTheDocument();
    expect(screen.queryByTestId("benchmarks-toggle")).not.toBeInTheDocument();
  });

  it("hides itself when the API refuses the role", async () => {
    mockGet.mockRejectedValue(httpError(403));
    const { container } = render(<BenchmarkCard />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("renders in Hindi", async () => {
    await i18n.changeLanguage("hi");
    mockGet.mockResolvedValue({ data: { opted_in: false, can_change: false, window: WINDOW, metrics: [] } });
    render(<BenchmarkCard />);
    expect(await screen.findByRole("heading", { name: "बेंचमार्क" })).toBeInTheDocument();
  });
});

describe("benchmarks client", () => {
  it("normalises and formats", () => {
    const card = normaliseCard({ metrics: [{ key: "x", bands: [{ cohort: "all", p50: 1, position: "odd" }, { p50: 2 }] }, {}] });
    expect(card.metrics).toHaveLength(1);
    expect(card.metrics[0].bands).toEqual([{ cohort: "all", p25: 0, p50: 1, p75: 0, tenants: 0, position: null }]);
    expect(card.opted_in).toBe(false);
    expect(card.min_tenants).toBe(20);
    expect(formatBenchmark(null, "percent")).toBe("—");
    expect(formatBenchmark(0.0123, "usd")).toBe("$0.0123");
    expect(formatBenchmark(12.5, "usd")).toBe("$12.50");
    expect(formatBenchmark(1500, "duration_ms")).toBe("1.5 s");
    expect(formatBenchmark(800, "duration_ms")).toBe("800 ms");
    expect(formatBenchmark(5, "count")).toBe("5.0");
  });
});
