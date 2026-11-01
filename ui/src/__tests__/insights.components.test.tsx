// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights components: decision tile, chart card (view-data toggle, export
 * menu), data table sorting, empty state, data-quality badge, needs-you list
 * and the filter bar.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import "@/i18n";
import ChartCard from "@/components/insights/ChartCard";
import DataQualityBadge from "@/components/insights/DataQualityBadge";
import DataTable from "@/components/insights/DataTable";
import DecisionTile from "@/components/insights/DecisionTile";
import EmptyState from "@/components/insights/EmptyState";
import FilterBar from "@/components/insights/FilterBar";
import NeedsYouList from "@/components/insights/NeedsYouList";
import type { MetricResponse } from "@/lib/insights/api";

function metric(partial: Partial<MetricResponse>): MetricResponse {
  return {
    key: "cost.spend",
    title: "Spend",
    definition: "Total spend in the period.",
    unit: "inr",
    currency: "INR",
    source: "spend_usage_rollups",
    data_quality: "measured",
    computed_at: "2026-11-01T04:00:00Z",
    period: { from: "2026-10-03", to: "2026-11-01" },
    compare: { from: "2026-09-03", to: "2026-10-02" },
    ...partial,
  };
}

const withRouter = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

function expectNoBadText(container: HTMLElement) {
  expect(container.textContent).not.toMatch(/NaN|undefined|Infinity|\[object Object\]/);
}

describe("DecisionTile", () => {
  it("shows value, delta, state, sparkline, quality and one action", () => {
    const { container } = withRouter(
      <DecisionTile
        metric={metric({
          value: 1234567,
          compare_value: 1000000,
          state: "watch",
          series: [
            { t: "2026-10-30", value: 10 },
            { t: "2026-10-31", value: 30 },
            { t: "2026-11-01", value: 20 },
          ],
        })}
        action={{ to: "/dashboard/costs", label: "Open cost details" }}
      />,
    );
    expect(screen.getByTestId("tile-value")).toHaveTextContent("₹12,34,567");
    expect(screen.getByTestId("tile-delta")).toHaveTextContent("+23.5%");
    expect(screen.getByTestId("tile-delta")).toHaveTextContent("vs previous period");
    expect(screen.getByTestId("state-chip")).toHaveAttribute("data-state", "watch");
    expect(screen.getByTestId("state-chip")).toHaveTextContent("Watch");
    expect(screen.getByTestId("sparkline")).toBeInTheDocument();
    expect(screen.getByTestId("data-quality-badge")).toHaveTextContent("Measured");
    expect(screen.getByRole("link", { name: "Open cost details" })).toHaveAttribute("href", "/dashboard/costs");
    expectNoBadText(container);
  });

  it("shows the API hint instead of a number when there is no data", () => {
    const { container } = withRouter(
      <DecisionTile
        metric={metric({ value: null, data_quality: "no_data", hint: "Turn on spend metering to see cost." })}
        action={null}
      />,
    );
    expect(screen.queryByTestId("tile-value")).toBeNull();
    expect(screen.getByTestId("insights-empty")).toHaveTextContent("Turn on spend metering to see cost.");
    expect(screen.getByTestId("data-quality-badge")).toHaveTextContent("No data");
    expectNoBadText(container);
  });

  it("says there is no comparison when the compare value is missing or zero", () => {
    withRouter(<DecisionTile metric={metric({ value: 5, compare_value: 0, unit: "count" })} />);
    expect(screen.getByTestId("tile-delta")).toHaveTextContent("No comparison period");
  });
});

describe("DataQualityBadge and EmptyState", () => {
  it("labels each quality and falls back to mixed for unknown values", () => {
    const { rerender } = render(<DataQualityBadge quality="seeded" />);
    expect(screen.getByTestId("data-quality-badge")).toHaveTextContent("Sample data");
    rerender(<DataQualityBadge quality="estimated" />);
    expect(screen.getByTestId("data-quality-badge")).toHaveTextContent("Estimated");
    rerender(<DataQualityBadge quality="something-new" />);
    expect(screen.getByTestId("data-quality-badge")).toHaveAttribute("data-quality", "mixed");
  });

  it("shows the hint, or a default sentence without one", () => {
    const { rerender } = render(<EmptyState hint="Run an agent to see run metrics." />);
    expect(screen.getByRole("status")).toHaveTextContent("Run an agent to see run metrics.");
    rerender(<EmptyState hint="  " />);
    expect(screen.getByRole("status")).toHaveTextContent("Nothing was recorded for this period.");
  });
});

describe("DataTable", () => {
  it("sorts by a column from the keyboard and marks aria-sort", async () => {
    const user = userEvent.setup();
    render(
      <DataTable
        caption="Spend data"
        columns={["t", "value"]}
        unit="usd"
        rows={[
          { t: "2026-10-02", value: 5 },
          { t: "2026-10-01", value: 20 },
          { t: "2026-10-03", value: 1 },
        ]}
      />,
    );
    const table = screen.getByTestId("insights-data-table");
    const valueHeader = screen.getByRole("button", { name: "Sort by Value" });
    valueHeader.focus();
    await user.keyboard("{Enter}");
    let cells = within(table).getAllByRole("row").slice(1).map((r) => r.lastElementChild?.textContent);
    expect(cells).toEqual(["$1.00", "$5.00", "$20.00"]);
    expect(valueHeader.closest("th")).toHaveAttribute("aria-sort", "ascending");
    await user.keyboard(" ");
    cells = within(table).getAllByRole("row").slice(1).map((r) => r.lastElementChild?.textContent);
    expect(cells).toEqual(["$20.00", "$5.00", "$1.00"]);
    expect(valueHeader.closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("region", { name: "Spend data" })).toHaveAttribute("tabindex", "0");
  });

  it("renders missing cells as a dash", () => {
    const { container } = render(<DataTable caption="x" columns={["a", "b"]} rows={[{ a: null, b: { n: 1 } }]} />);
    expect(container.textContent).toContain("—");
    expectNoBadText(container);
  });
});

describe("ChartCard", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  const lineMetric = metric({
    series: [
      { t: "2026-10-01", value: 100 },
      { t: "2026-10-02", value: 250 },
      { t: "2026-10-03", value: 175 },
    ],
  });

  it("has a text summary and toggles a data table", async () => {
    const user = userEvent.setup();
    withRouter(<ChartCard metric={lineMetric} filters={{ from: "2026-10-01", to: "2026-10-03" }} />);
    expect(screen.getByTestId("chart-summary")).toHaveTextContent(
      "Spend: 3 points from 2026-10-01 to 2026-10-03. Latest ₹175; highest ₹250 on 2026-10-02.",
    );
    const figure = screen.getByRole("figure");
    expect(figure).toHaveAttribute("aria-describedby", screen.getByTestId("chart-summary").id);
    const toggle = screen.getByRole("button", { name: "View data" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Hide data" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("insights-data-table")).toHaveTextContent("₹250");
    expect(screen.getByText("Source: spend_usage_rollups")).toBeInTheDocument();
  });

  it("export menu requests the export endpoint with the view's filters", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:insights");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    mockGet.mockResolvedValue({ data: new Blob(["a,b"]) });

    withRouter(
      <ChartCard
        metric={lineMetric}
        filters={{ from: "2026-10-01", to: "2026-10-03", compare: "previous", company_id: "c-1" }}
      />,
    );
    const button = screen.getByRole("button", { name: "Export Spend" });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    await user.click(button);
    const menu = screen.getByRole("menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["CSV", "Excel (XLSX)", "JSON"]);
    expect(items[0]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(items[1]).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    expect(mockGet).toHaveBeenCalledWith("/insights/metrics/cost.spend/export", {
      params: { format: "xlsx", from: "2026-10-01", to: "2026-10-03", compare: "previous", company_id: "c-1" },
      responseType: "blob",
    });
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(createObjectURL).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it("shows the server's hint when an export is too large", async () => {
    const user = userEvent.setup();
    const body = new Blob([JSON.stringify({ detail: "Export over 50,000 rows. Narrow the date range." })]);
    mockGet.mockRejectedValue({ response: { status: 413, data: body } });
    withRouter(<ChartCard metric={lineMetric} filters={{ from: "2026-01-01", to: "2026-10-03" }} />);
    await user.click(screen.getByRole("button", { name: "Export Spend" }));
    await user.click(screen.getByRole("menuitem", { name: "CSV" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Export over 50,000 rows. Narrow the date range.");
  });

  it("Escape closes the export menu and returns focus", async () => {
    const user = userEvent.setup();
    withRouter(<ChartCard metric={lineMetric} filters={{}} />);
    const button = screen.getByRole("button", { name: "Export Spend" });
    await user.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("shows the hint and no data toggle for an empty metric", () => {
    const { container } = withRouter(
      <ChartCard
        metric={metric({ key: "cost.by_model", data_quality: "no_data", hint: "Add a rate card to price model usage." })}
        filters={{}}
      />,
    );
    expect(screen.getByTestId("insights-empty")).toHaveTextContent("Add a rate card to price model usage.");
    expect(screen.queryByRole("button", { name: "View data" })).toBeNull();
    expect(screen.queryByRole("figure")).toBeNull();
    expectNoBadText(container);
  });

  it("summarises budgets and forecasts in text", () => {
    withRouter(
      <ChartCard
        metric={metric({
          key: "cost.budgets",
          title: "Budgets",
          rows: [
            { period: "monthly", actual: 120, threshold: 100, state: "breached" },
            { period: "weekly", actual: 80, threshold: 100, state: "warn" },
          ],
        })}
        filters={{}}
      />,
    );
    expect(screen.getByTestId("chart-summary")).toHaveTextContent("Budgets: 2 budgets, 1 breached and 1 at warning.");
    expect(screen.getByTestId("bullet-bars")).toHaveTextContent("monthly");
  });

  it("formats budget amounts in the row's currency and tabulates the rows", async () => {
    const user = userEvent.setup();
    withRouter(
      <ChartCard
        metric={metric({
          key: "cost.budgets",
          title: "Budgets",
          unit: "count",
          value: 1,
          rows: [{ name: "Ops", period: "monthly", actual: 1250.5, threshold: 1000, state: "breached", currency: "USD" }],
        })}
        filters={{}}
      />,
    );
    expect(screen.getByTestId("bullet-bars")).toHaveTextContent("Ops");
    expect(screen.getByTestId("bullet-bars")).toHaveTextContent("$1,250.50 / $1,000.00");
    await user.click(screen.getByRole("button", { name: "View data" }));
    const table = screen.getByTestId("insights-data-table");
    expect(table).toHaveTextContent("$1,250.50");
    expect(table).toHaveTextContent("breached");
  });

  it("shows the forecast band as text", () => {
    withRouter(
      <ChartCard
        metric={metric({
          key: "cost.forecast",
          title: "Month-end forecast",
          data_quality: "estimated",
          rows: [{ point: 150000, low: 120000, high: 180000 }],
        })}
        filters={{}}
      />,
    );
    expect(screen.getByTestId("forecast-text")).toHaveTextContent("₹1,50,000");
    expect(screen.getByTestId("chart-summary")).toHaveTextContent("likely between ₹1,20,000 and ₹1,80,000");
    expect(screen.getByTestId("data-quality-badge")).toHaveTextContent("Estimated");
  });
});

describe("NeedsYouList", () => {
  it("lists items with age and in-app links only", () => {
    withRouter(
      <NeedsYouList
        items={[
          { kind: "approval", title: "Invoice 42 approval", age_seconds: 7200, link: "/dashboard/approvals" },
          { kind: "budget", title: "Monthly budget breached", state: "breached", link: "https://evil.example/x" },
          { kind: "agent", title: "Paused agent", age_days: 9, link: "//evil.example" },
        ]}
      />,
    );
    expect(screen.getByText("Invoice 42 approval")).toBeInTheDocument();
    expect(screen.getByText("waiting 2h")).toBeInTheDocument();
    expect(screen.getByText("waiting 9d")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Open" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/dashboard/approvals");
    expect(screen.getByTestId("state-chip")).toHaveAttribute("data-state", "act");
  });

  it("says when nothing needs attention", () => {
    withRouter(<NeedsYouList items={[]} />);
    expect(screen.getByText("Nothing needs you right now.")).toBeInTheDocument();
  });
});

describe("FilterBar", () => {
  it("changes preset and compare", () => {
    const onChange = vi.fn();
    render(<FilterBar value={{ preset: "30d", from: "2026-10-03", to: "2026-11-01", compare: "previous" }} onChange={onChange} />);
    expect(screen.getByTestId("insights-range-text")).toHaveTextContent("2026-10-03 to 2026-11-01");
    fireEvent.change(screen.getByLabelText("Date range"), { target: { value: "mtd" } });
    expect(onChange).toHaveBeenLastCalledWith({ preset: "mtd" });
    fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "none" } });
    expect(onChange).toHaveBeenLastCalledWith({ compare: "none" });
  });

  it("rejects a custom range that ends before it starts", () => {
    const onChange = vi.fn();
    render(<FilterBar value={{ preset: "custom", from: "2026-10-01", to: "2026-10-10", compare: "previous" }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-20" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose valid dates");
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-05" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onChange).toHaveBeenCalledWith({ preset: "custom", from: "2026-10-05", to: "2026-10-10" });
  });
});
