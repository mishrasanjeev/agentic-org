// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights explorers and run evidence: the request the page sends (period,
 * filter[column], sort, page), the table it draws (cells by type, links,
 * sort state), access and error states, the explorer export, revoking a
 * learned rule only when the rule is still the one shown, and the run page.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import i18n from "@/i18n";
import en from "../locales/en.json";
import hi from "../locales/hi.json";
import InsightsExplorer from "@/pages/InsightsExplorer";
import InsightsRun from "@/pages/InsightsRun";
import {
  explorerParams,
  explorerRoute,
  filtersFromSearch,
  type ExplorerColumn,
  type ExplorerDescription,
  type ExplorerResponse,
} from "@/lib/insights/explore";
import { formatCell, linkColumn, primaryLink } from "@/lib/insights/explorerCells";

const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };

const RUN_COLUMNS: ExplorerColumn[] = [
  { key: "started_at", title: "Started", type: "datetime", sortable: true, filter: null },
  { key: "agent", title: "Agent", type: "text", sortable: false, filter: null },
  { key: "agent_id", title: "Agent id", type: "id", sortable: false, filter: "enum" },
  {
    key: "status",
    title: "Status",
    type: "status",
    sortable: true,
    filter: "enum",
    options: ["completed", "failed"],
  },
  { key: "duration_ms", title: "Duration", type: "duration_ms", sortable: true, filter: "min" },
  { key: "cost_usd", title: "Cost (USD)", type: "money", sortable: true, filter: "min" },
  { key: "hitl_required", title: "Needed approval", type: "bool", sortable: true, filter: "bool" },
];

const QUALITY_COLUMNS: ExplorerColumn[] = [
  { key: "agent", title: "Agent", type: "text", sortable: true, filter: "text" },
  { key: "rule_number", title: "Rule #", type: "count", sortable: true, filter: null },
  { key: "rule", title: "Learned rule", type: "text", sortable: false, filter: "text" },
];

function describeExplorer(key: string, columns: ExplorerColumn[], extra: Partial<ExplorerDescription> = {}) {
  return {
    key,
    title: key === "runs" ? "Runs" : "Quality and learning",
    definition: `Definition of ${key}`,
    source: ["run_facts"],
    columns,
    default_sort: columns[0].key,
    default_order: "desc" as const,
    uses_period: true,
    ...extra,
  };
}

function runsPage(partial: Partial<ExplorerResponse> = {}): ExplorerResponse {
  return {
    key: "runs",
    title: "Runs",
    definition: "Definition of runs",
    source: "run_facts",
    data_quality: "measured",
    computed_at: "2026-11-01T04:00:00Z",
    period: { from: "2026-10-03", to: "2026-11-01", granularity: "day" },
    columns: RUN_COLUMNS,
    rows: [
      {
        started_at: "2026-11-01T03:00:00+00:00",
        agent: "Invoice matcher",
        agent_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status: "failed",
        duration_ms: 9000,
        cost_usd: 0.012,
        hitl_required: false,
        links: { run: "/dashboard/insights/runs/r1?at=2026-11-01T03%3A00%3A00%2B00%3A00", agent: "/dashboard/agents/a1" },
      },
    ],
    total: 120,
    page: 1,
    page_size: 50,
    sort: "started_at",
    order: "desc",
    filters: {},
    hint: "Runs are read one by one.",
    summary: null,
    ...partial,
  };
}

let lastSearch = "";
function LocationProbe() {
  const location = useLocation();
  lastSearch = location.pathname + location.search;
  return null;
}

function renderAt(initial: string) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route
            path="/dashboard/insights/explore/:explorer?"
            element={
              <>
                <InsightsExplorer />
                <LocationProbe />
              </>
            }
          />
          <Route path="/dashboard/insights/runs/:runId" element={<InsightsRun />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

function route(handlers: Record<string, (params: unknown) => unknown>) {
  mockGet.mockImplementation((url: string, config?: { params?: unknown }) => {
    if (url === "/insights/status")
      return Promise.resolve({ data: { enabled: true, views: ["executive"], platform_operator: false, settings: SETTINGS } });
    for (const [prefix, handler] of Object.entries(handlers)) {
      if (url === prefix || url.startsWith(prefix)) {
        try {
          const data = handler(config?.params);
          if (data instanceof Promise) return data;
          return Promise.resolve({ data });
        } catch (e) {
          return Promise.reject(e);
        }
      }
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

function lastPageCall(key = "runs") {
  const calls = pageCalls(key);
  return calls[calls.length - 1]?.[1];
}

function pageCalls(key = "runs") {
  return mockGet.mock.calls.filter((c) => c[0] === `/insights/explore/${key}`);
}

describe("explorer helpers", () => {
  it("builds filter[column] parameters and drops empty ones", () => {
    expect(
      explorerParams({
        from: "2026-10-01",
        to: "2026-10-31",
        compare: "previous",
        company_id: "",
        filters: { status: "failed", agent: " ", idle_days: "30 " },
        sort: "duration_ms",
        order: "asc",
        page: 1,
        page_size: 50,
      }),
    ).toEqual({
      from: "2026-10-01",
      to: "2026-10-31",
      "filter[status]": "failed",
      "filter[idle_days]": "30",
      sort: "duration_ms",
      order: "asc",
      page_size: "50",
    });
    expect(explorerParams({ page: 3 })).toEqual({ page: "3" });
  });

  it("reads filters from a URL and builds explorer routes", () => {
    const search = new URLSearchParams("filter[status]=failed&filter[x-y]=no&sort=a&filter[agent_id]=");
    expect(filtersFromSearch(search)).toEqual({ status: "failed" });
    expect(explorerRoute("runs", { agent_id: "a1" }, { sort: "duration_ms" })).toBe(
      "/dashboard/insights/explore/runs?filter%5Bagent_id%5D=a1&sort=duration_ms",
    );
    expect(explorerRoute("cost")).toBe("/dashboard/insights/explore/cost");
  });

  it("formats cells by their declared type and never shows NaN or null", () => {
    const opts = { currency: "INR", timezone: "Asia/Kolkata", yes: "Yes", no: "No" };
    const c = (key: string, type: string): ExplorerColumn => ({ key, title: key, type, sortable: true, filter: null });
    expect(formatCell(c("spend", "money"), 1234.5, opts)).toContain("₹");
    expect(formatCell(c("cost_usd", "money"), 1.5, opts)).toContain("$");
    expect(formatCell(c("hitl_required", "bool"), true, opts)).toBe("Yes");
    expect(formatCell(c("hitl_required", "bool"), false, opts)).toBe("No");
    expect(formatCell(c("runs", "count"), "abc", opts)).toBe("—");
    expect(formatCell(c("runs", "count"), null, opts)).toBe("—");
    expect(formatCell(c("started_at", "datetime"), "not a date", opts)).toBe("—");
    expect(formatCell(c("agent_id", "id"), "aaaaaaaa-bbbb-cccc", opts)).toBe("aaaaaaaa…");
    expect(formatCell(c("status", "status"), "hitl_triggered", opts)).toBe("hitl triggered");
    expect(primaryLink({ links: { agent: "/a", run: "/r" } })).toBe("/r");
    expect(primaryLink({ links: { agent: "https://evil.example" } })).toBeNull();
    expect(linkColumn(RUN_COLUMNS)).toBe("agent");
  });

  it("explorer strings exist in English and Hindi, translated", () => {
    const leaves = (obj: unknown, prefix = ""): string[] =>
      Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
        v && typeof v === "object" ? leaves(v, `${prefix}${k}.`) : [`${prefix}${k}`],
      );
    for (const ns of ["insightsExplore", "insightsRun"] as const) {
      expect(leaves(hi[ns]).sort(), ns).toEqual(leaves(en[ns]).sort());
    }
    expect(hi.insightsExplore.title).not.toBe(en.insightsExplore.title);
  });
});

describe("InsightsExplorer", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockDelete.mockReset();
    lastSearch = "";
    localStorage.clear();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-01T04:00:00Z"));
    await i18n.changeLanguage("en");
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends the period, the URL's filters, sort and page, and draws the rows with their links", async () => {
    route({
      "/insights/explorers": () => ({
        explorers: [describeExplorer("runs", RUN_COLUMNS), describeExplorer("quality", QUALITY_COLUMNS)],
      }),
      "/insights/explore/runs": () => runsPage(),
    });
    renderAt("/dashboard/insights/explore/runs?range=7d&filter[status]=failed&sort=duration_ms&order=desc");
    const table = await screen.findByTestId("insights-explorer-table");
    expect(pageCalls()[0][1]).toEqual({
      params: {
        from: "2026-10-26",
        to: "2026-11-01",
        "filter[status]": "failed",
        sort: "duration_ms",
        order: "desc",
        page_size: "50",
      },
    });
    const row = within(table).getAllByRole("row")[1];
    expect(within(row).getByRole("link", { name: "Invoice matcher" })).toHaveAttribute(
      "href",
      "/dashboard/insights/runs/r1?at=2026-11-01T03%3A00%3A00%2B00%3A00",
    );
    expect(row.textContent).toContain("$0.01");
    expect(row.textContent).toContain("No");
    expect(screen.getByText("Rows 1–50 of 120")).toBeInTheDocument();
    expect(screen.getByTestId("insights-explorer-hint")).toHaveTextContent("Runs are read one by one.");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("aria-current", "page");
    expect(document.body.textContent).not.toMatch(/NaN|undefined|\[object Object\]/);
  });

  it("sorting, filtering and paging go through the URL and the request", async () => {
    route({
      "/insights/explorers": () => ({ explorers: [describeExplorer("runs", RUN_COLUMNS)] }),
      "/insights/explore/runs": () => runsPage(),
    });
    renderAt("/dashboard/insights/explore/runs");
    await screen.findByTestId("insights-explorer-table");
    fireEvent.click(screen.getByRole("button", { name: /Duration/ }));
    await waitFor(() => expect(lastSearch).toContain("sort=duration_ms"));
    await waitFor(() => expect(lastPageCall()).toMatchObject({ params: { sort: "duration_ms", order: "desc" } }));
    expect(screen.getByRole("columnheader", { name: /Duration/ })).toHaveAttribute("aria-sort", "descending");

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    await waitFor(() => expect(lastPageCall()).toMatchObject({ params: { "filter[status]": "failed" } }));
    fireEvent.change(screen.getByLabelText("Needed approval"), { target: { value: "true" } });
    await waitFor(() =>
      expect(lastPageCall()).toMatchObject({ params: { "filter[hitl_required]": "true" } }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastPageCall()).toMatchObject({ params: { page: "2" } }));
    fireEvent.click(screen.getByRole("button", { name: "Clear filters (2)" }));
    await waitFor(() => expect(lastSearch).not.toContain("filter"));
  });

  it("shows an id filter from a drill as a removable chip", async () => {
    route({
      "/insights/explorers": () => ({ explorers: [describeExplorer("runs", RUN_COLUMNS)] }),
      "/insights/explore/runs": () => runsPage(),
    });
    renderAt("/dashboard/insights/explore/runs?filter[agent_id]=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    await screen.findByTestId("insights-explorer-table");
    expect(pageCalls()[0][1]).toMatchObject({
      params: { "filter[agent_id]": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Remove the Agent id filter" }));
    await waitFor(() => expect(lastSearch).not.toContain("agent_id"));
  });

  it("lands on the first explorer the role may open, and says when a table is refused", async () => {
    route({
      "/insights/explorers": () => ({ explorers: [describeExplorer("runs", RUN_COLUMNS)] }),
      "/insights/explore/runs": () => {
        throw { response: { status: 403, data: { detail: "insights_explorer_forbidden" } } };
      },
    });
    renderAt("/dashboard/insights/explore/audit");
    await waitFor(() => expect(lastSearch).toBe("/dashboard/insights/explore/runs"));
    expect(await screen.findByText("This table is not available to your role")).toBeInTheDocument();
  });

  it("exports every filtered row through the explorer export", async () => {
    route({
      "/insights/explorers": () => ({ explorers: [describeExplorer("runs", RUN_COLUMNS)] }),
      "/insights/explore/runs/export": () => new Blob(["a,b"]),
      "/insights/explore/runs": () => runsPage(),
    });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    renderAt("/dashboard/insights/explore/runs?filter[status]=failed&page=2");
    await screen.findByTestId("insights-explorer-table");
    fireEvent.click(screen.getByRole("button", { name: /Export/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /XLSX/ }));
    await waitFor(() =>
      expect(mockGet.mock.calls.some((c) => c[0] === "/insights/explore/runs/export")).toBe(true),
    );
    const call = mockGet.mock.calls.find((c) => c[0] === "/insights/explore/runs/export")!;
    expect(call[1].params).toMatchObject({ format: "xlsx", "filter[status]": "failed" });
    expect(call[1].params.page).toBeUndefined();
    expect(call[1].params.page_size).toBeUndefined();
  });

  it("revokes a learned rule only when the rule is still the one shown", async () => {
    const rows = [
      {
        agent: "Invoice matcher",
        rule_number: 1,
        rule: "Always quote the PO number",
        actions: { revoke: { agent_id: "a1", index: 0 } },
      },
    ];
    let amendments = ["Always quote the PO number", "Use INR"];
    route({
      "/insights/explorers": () => ({ explorers: [describeExplorer("quality", QUALITY_COLUMNS)] }),
      "/insights/explore/quality": () => ({ ...runsPage(), key: "quality", columns: QUALITY_COLUMNS, rows, total: 1 }),
      "/agents/a1/amendments": () => ({ amendments }),
    });
    mockDelete.mockResolvedValue({ data: { removed: "Always quote the PO number" } });
    renderAt("/dashboard/insights/explore/quality");
    fireEvent.click(await screen.findByRole("button", { name: "Revoke learned rule 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, revoke" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/agents/a1/amendments/0"));

    // The list changed meanwhile: nothing is deleted and the page says so.
    mockDelete.mockClear();
    amendments = ["Use INR"];
    fireEvent.click(await screen.findByRole("button", { name: "Revoke learned rule 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, revoke" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rules changed");
    expect(mockDelete).not.toHaveBeenCalled();
  });
});

describe("InsightsRun", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    localStorage.clear();
    await i18n.changeLanguage("en");
  });

  it("shows the run, its model calls, spans, approvals and audit rows", async () => {
    route({
      "/insights/runs/r1": (params) => {
        expect(params).toEqual({ at: "2026-11-01T03:00:00+00:00" });
        return {
          run: {
            id: "r1",
            agent: "Invoice matcher",
            agent_id: "a1",
            status: "completed",
            started_at: "2026-11-01T03:00:00+00:00",
            duration_ms: 1200,
            tokens: { input: 100, output: 50, cached: 0, total: 150 },
            cost_usd: 0.01,
            data_quality: "measured",
          },
          gateway_records: [{ provider: "gemini", model: "gemini-2.5-flash", outcome: "ok", latency_ms: 800 }],
          spans: [{ name: "llm.call", kind: "llm", status: "ok", duration_ms: 800 }],
          approvals: [{ title: "Approve invoice INV-1", status: "decided", link: "/dashboard/approvals?id=h1" }],
          audit: [],
          links: { agent: "/dashboard/agents/a1" },
          limits: { records: 50, spans: 200 },
        };
      },
    });
    renderAt("/dashboard/insights/runs/r1?at=2026-11-01T03%3A00%3A00%2B00%3A00");
    expect(await screen.findByRole("link", { name: "Invoice matcher" })).toHaveAttribute("href", "/dashboard/agents/a1");
    expect(screen.getByText("gemini-2.5-flash")).toBeInTheDocument();
    expect(screen.getByText("llm.call")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Approve invoice INV-1" })).toHaveAttribute("href", "/dashboard/approvals?id=h1");
    expect(screen.getByText("No audit rows for this run.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/NaN|undefined|\[object Object\]/);
  });

  it("says the run was not found for a 404", async () => {
    route({
      "/insights/runs/r2": () => {
        throw { response: { status: 404, data: { detail: { error: "run_not_found" } } } };
      },
    });
    renderAt("/dashboard/insights/runs/r2?at=x");
    expect(await screen.findByText("Run not found")).toBeInTheDocument();
  });
});
