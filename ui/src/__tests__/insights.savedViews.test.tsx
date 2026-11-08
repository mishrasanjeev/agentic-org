// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Saved views (open one, save the current filters, delete one's own) and the "what changed since you last looked"
 * strip (shown when something changed, then the visit is recorded).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockDelete = vi.fn();
vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import i18n from "@/i18n";
import SavedViews from "@/components/insights/SavedViews";
import WhatChanged from "@/components/insights/WhatChanged";
import { filtersFromSearch, searchFromFilters } from "@/lib/insights/savedViews";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

const VIEWS = [
  { id: "v1", view_key: "finance", name: "Q3 close", filters: { range: "custom", from: "2026-07-01", to: "2026-09-30" },
    shared: true, mine: false },
  { id: "v2", view_key: "finance", name: "My month", filters: { range: "mtd" }, shared: false, mine: true },
];

describe("saved view filters", () => {
  it("keep the URL query except the company, and rebuild it with repeated keys", () => {
    const params = new URLSearchParams("view=finance&range=7d&company_id=c1&filter%5Bstatus%5D=failed&x=1&x=2");
    expect(filtersFromSearch(params)).toEqual({ view: "finance", range: "7d", "filter[status]": "failed", x: ["1", "2"] });
    expect(searchFromFilters({ range: "7d", x: ["1", "2"] }).toString()).toBe("range=7d&x=1&x=2");
  });
});

describe("SavedViews", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockDelete.mockReset();
    await i18n.changeLanguage("en");
  });

  function renderAt(initial: string) {
    return render(
      <MemoryRouter initialEntries={[initial]}>
        <SavedViews viewKey="finance" keep={["view"]} />
        <LocationProbe />
      </MemoryRouter>,
    );
  }

  it("opens a saved view, keeping the view tab", async () => {
    mockGet.mockResolvedValue({ data: { saved_views: VIEWS } });
    renderAt("/dashboard/insights?view=finance&range=7d");
    expect(mockGet).toHaveBeenCalledWith("/insights/saved-views", { params: { view_key: "finance" } });
    const select = await screen.findByRole("combobox", { name: "Saved views" });
    expect(select).toHaveClass("insights-target");
    await screen.findByRole("option", { name: "Q3 close · shared" });
    fireEvent.change(select, { target: { value: "v1" } });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("?range=custom&from=2026-07-01&to=2026-09-30&view=finance"),
    );
  });

  it("saves the current filters under a name, shared when asked", async () => {
    mockGet.mockResolvedValue({ data: { saved_views: [] } });
    mockPost.mockResolvedValue({ data: { saved_view: VIEWS[1] } });
    renderAt("/dashboard/insights?view=finance&range=mtd&company_id=c1");
    fireEvent.click(await screen.findByTestId("saved-views-save"));
    expect(screen.getByPlaceholderText("Name this view")).toHaveClass("insights-target");
    fireEvent.change(screen.getByPlaceholderText("Name this view"), { target: { value: "My month" } });
    fireEvent.click(screen.getByLabelText("Share with everyone who can open it"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved.");
    expect(mockPost).toHaveBeenCalledWith("/insights/saved-views", {
      view_key: "finance",
      name: "My month",
      filters: { view: "finance", range: "mtd" },
      shared: true,
    });
  });

  it("deletes one's own saved view only", async () => {
    mockGet.mockResolvedValue({ data: { saved_views: VIEWS } });
    mockDelete.mockResolvedValue({ data: { deleted: "v2" } });
    renderAt("/dashboard/insights?view=finance");
    await screen.findByRole("option", { name: "My month" });
    expect(screen.queryByRole("button", { name: "Delete the saved view Q3 close" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete the saved view My month" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/insights/saved-views/v2"));
  });
});

describe("WhatChanged", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockPost.mockReset();
    await i18n.changeLanguage("en");
  });

  it("lists the changes with links, then records the visit", async () => {
    mockGet.mockResolvedValue({
      data: {
        since: "2026-11-01T10:00:00Z",
        first_visit: false,
        items: [
          { key: "runs_failed", count: 3, link: "/dashboard/insights/explore/runs?filter%5Bstatus%5D=failed" },
          { key: "agents_created", count: 1, link: "/dashboard/insights/explore/agents" },
        ],
      },
    });
    mockPost.mockResolvedValue({ data: {} });
    render(
      <MemoryRouter>
        <WhatChanged />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: "3 failed runs" })).toHaveAttribute(
      "href",
      "/dashboard/insights/explore/runs?filter%5Bstatus%5D=failed",
    );
    expect(screen.getByRole("link", { name: "1 agent added" })).toBeInTheDocument();
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/insights/changes/seen"));
  });

  it("shows nothing when nothing changed", async () => {
    mockGet.mockResolvedValue({ data: { since: "2026-11-01T10:00:00Z", first_visit: false, items: [] } });
    mockPost.mockResolvedValue({ data: {} });
    const { container } = render(
      <MemoryRouter>
        <WhatChanged />
      </MemoryRouter>,
    );
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
