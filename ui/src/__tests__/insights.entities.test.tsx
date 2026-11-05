// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights Level 3 entity pages, breadcrumbs and the Ctrl/Cmd-K search palette: the request each sends, what the
 * page draws (facts, scoped cards, timeline, links), the 404 and 403 states, and where a search result opens.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import i18n from "@/i18n";
import Breadcrumbs from "@/components/insights/Breadcrumbs";
import SearchPalette from "@/components/insights/SearchPalette";
import InsightsEntity from "@/pages/InsightsEntity";
import { entityPath, kindFromSegment, resultPath } from "@/lib/insights/entities";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderAt(initial: string) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/dashboard/insights/:segment/:id" element={<InsightsEntity />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

const AGENT_PAGE = {
  kind: "agent",
  id: "a1",
  title: "Invoice matcher",
  subtitle: "ap_processor · finance",
  period: { from: "2026-10-05", to: "2026-11-03" },
  facts: [
    { key: "status", value: "active" },
    { key: "domain", value: "finance" },
    { key: "spend_usd", value: 2.5 },
  ],
  cards: [
    {
      key: "runs.volume",
      title: "Agent runs",
      definition: "Runs",
      unit: "count",
      source: "run_facts",
      data_quality: "measured",
      computed_at: "2026-11-03T10:00:00Z",
      period: { from: "2026-10-05", to: "2026-11-03", granularity: "day" },
      value: 12,
    },
  ],
  timeline: [
    { at: "2026-11-02T09:00:00Z", event: "agent.run", action: "run", outcome: "failure", actor_type: "agent", actor: "a1" },
  ],
  links: { runs: "/dashboard/insights/explore/runs?filter%5Bagent_id%5D=a1", manage: "/dashboard/agents/a1" },
};

function httpError(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: {} } });
}

describe("entity routes", () => {
  it("maps kinds to URL segments and back", () => {
    expect(entityPath("cost_centre", "c 1")).toBe("/dashboard/insights/cost-centres/c%201");
    expect(kindFromSegment("companies")).toBe("company");
    expect(kindFromSegment("planets")).toBeNull();
    expect(resultPath({ kind: "run", id: "r1", title: "ap_processor", at: "2026-11-01T03:00:00+00:00" })).toBe(
      "/dashboard/insights/runs/r1?at=2026-11-01T03%3A00%3A00%2B00%3A00",
    );
    expect(resultPath({ kind: "user", id: "u1", title: "Asha" })).toBe("/dashboard/insights/users/u1");
  });
});

describe("InsightsEntity", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    await i18n.changeLanguage("en");
  });

  it("asks for the kind named by the URL and draws facts, cards, timeline and links", async () => {
    mockGet.mockResolvedValue({ data: AGENT_PAGE });
    renderAt("/dashboard/insights/agents/a1");
    expect(await screen.findByTestId("entity-title")).toHaveTextContent("Invoice matcher");
    expect(mockGet.mock.calls[0][0]).toBe("/insights/entities/agent/a1");
    expect(screen.getByText("finance")).toBeInTheDocument();
    expect(screen.getByText("Spend in the period (USD)")).toBeInTheDocument();
    expect(screen.getByText("Agent runs")).toBeInTheDocument();
    expect(screen.getByText("agent.run")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage" })).toHaveAttribute("href", "/dashboard/agents/a1");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute(
      "href",
      "/dashboard/insights/explore/runs?filter%5Bagent_id%5D=a1",
    );
    const crumbs = screen.getByRole("navigation", { name: "Where you are in Insights" });
    expect(crumbs).toHaveTextContent("Insights");
    expect(crumbs).toHaveTextContent("Agent");
  });

  it("says not found for a 404 and not available for a 403", async () => {
    mockGet.mockRejectedValueOnce(httpError(404));
    const first = renderAt("/dashboard/insights/agents/missing");
    expect(await screen.findByText("Not found")).toBeInTheDocument();
    first.unmount();
    mockGet.mockRejectedValueOnce(httpError(403));
    renderAt("/dashboard/insights/workflows/w1");
    expect(await screen.findByText("Not available to your role")).toBeInTheDocument();
  });
});

describe("Breadcrumbs", () => {
  it("links every crumb but the current page", () => {
    render(
      <MemoryRouter>
        <Breadcrumbs items={[{ label: "Explore", to: "/dashboard/insights/explore" }, { label: "Runs" }]} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Insights" })).toHaveAttribute("href", "/dashboard/insights");
    expect(screen.getByRole("link", { name: "Explore" })).toHaveAttribute("href", "/dashboard/insights/explore");
    expect(screen.getByText("Runs")).toHaveAttribute("aria-current", "page");
  });
});

describe("SearchPalette", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    await i18n.changeLanguage("en");
  });

  function renderPalette() {
    const chatBar = vi.fn();
    window.addEventListener("keydown", chatBar);
    render(
      <MemoryRouter initialEntries={["/dashboard/insights"]}>
        <SearchPalette />
        <LocationProbe />
      </MemoryRouter>,
    );
    return chatBar;
  }

  it("opens on Ctrl-K before the chat bar sees it, searches, and opens the chosen result", async () => {
    mockGet.mockResolvedValue({
      data: {
        results: [
          { kind: "agent", id: "a1", title: "Invoice matcher", detail: "finance" },
          { kind: "user", id: "u1", title: "Asha Rao", detail: "cfo" },
        ],
      },
    });
    const chatBar = renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(chatBar).not.toHaveBeenCalled();
    const input = await screen.findByRole("searchbox");
    fireEvent.change(input, { target: { value: "in" } });
    expect(await screen.findByText("Invoice matcher")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/insights/search", { params: { q: "in" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/dashboard/insights/users/u1"));
    window.removeEventListener("keydown", chatBar);
  });

  it("waits for two characters and says when nothing matches", async () => {
    mockGet.mockResolvedValue({ data: { results: [] } });
    renderPalette();
    fireEvent.click(screen.getByTestId("insights-search-open"));
    const input = await screen.findByRole("searchbox");
    fireEvent.change(input, { target: { value: "a" } });
    expect(screen.getByText("Type at least two characters.")).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "zz" } });
    expect(await screen.findByText("Nothing you can open matches.")).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });
});
