// SPDX-License-Identifier: Apache-2.0
/**
 * Workbench search: the query, the kinds, the facets as filters, the results with links.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import WorkbenchSearch, { buildParams, toggle } from "@/components/WorkbenchSearch";

const RESPONSE = {
  query: { must: ["ravi"], must_not: [] },
  kinds: ["case", "document", "customer", "account"],
  hits: [
    { kind: "case", id: "c1", title: "Case KYB-1", subtitle: "onboarding · registry · awaiting_decision", snippet: "Ravi Kumar", path: "/dashboard/approvals/cases/KYB-1", facets: { state: "awaiting_decision", purpose: "onboarding", provider: "registry" }, updated_at: null },
    { kind: "account", id: "d1:0:account_number", title: "account number 1234", subtitle: "Ravi · bank_statement · s.pdf", snippet: "account_number 1234 Ravi", path: "/dashboard/documents", facets: { document_type: "bank_statement" }, updated_at: null },
  ],
  counts: { case: 1, document: 0, customer: 0, account: 1 },
  facets: { case: { state: { awaiting_decision: 1 }, purpose: { onboarding: 1 }, provider: { registry: 1 } }, account: { document_type: { bank_statement: 1 } } },
  total: 2,
  allowed_kinds: ["case", "document", "customer", "account"],
  filters: {},
};

function renderSearch() {
  return render(
    <MemoryRouter>
      <WorkbenchSearch />
    </MemoryRouter>,
  );
}

describe("WorkbenchSearch", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({ data: RESPONSE });
  });

  it("builds query parameters and toggles values", () => {
    const params = buildParams(" ravi ", ["case"], { state: ["awaiting_decision", "decided"] });
    expect(params.getAll("state")).toEqual(["awaiting_decision", "decided"]);
    expect(params.get("q")).toBe("ravi");
    expect(params.getAll("kind")).toEqual(["case"]);
    expect(toggle(["a"], "a")).toEqual([]);
    expect(toggle(["a"], "b")).toEqual(["a", "b"]);
  });

  it("searches on submit and lists hits with their links and facets", async () => {
    renderSearch();
    expect(screen.getByTestId("search-count").textContent).toContain("Enter a query");
    fireEvent.change(screen.getByLabelText("Search cases, documents, customers and accounts"), { target: { value: "ravi" } });
    fireEvent.click(screen.getByText("Search"));
    const hits = await screen.findAllByTestId("search-hit");
    expect(hits).toHaveLength(2);
    expect(mockGet.mock.calls[0][0]).toBe("/workbench/search?q=ravi&limit=50");
    expect(screen.getByText("Case KYB-1").getAttribute("href")).toBe("/dashboard/approvals/cases/KYB-1");
    expect(screen.getByTestId("search-count").textContent).toBe("2 results");
    expect(screen.getByTestId("search-facet-state")).toBeTruthy();
  });

  it("narrows by a facet value and by kind", async () => {
    renderSearch();
    fireEvent.change(screen.getByLabelText("Search cases, documents, customers and accounts"), { target: { value: "ravi" } });
    fireEvent.click(screen.getByText("Search"));
    await screen.findAllByTestId("search-hit");
    fireEvent.click(screen.getByLabelText(/awaiting_decision/));
    await waitFor(() => expect(mockGet.mock.calls[mockGet.mock.calls.length - 1][0]).toBe("/workbench/search?q=ravi&state=awaiting_decision&limit=50"));
    fireEvent.click(screen.getByTestId("search-kind-customer"));
    await waitFor(() => expect(mockGet.mock.calls[mockGet.mock.calls.length - 1][0]).toBe("/workbench/search?q=ravi&kind=case&kind=document&kind=account&state=awaiting_decision&limit=50"));
  });

  it("reports a failed search", async () => {
    mockGet.mockRejectedValue(new Error("500"));
    renderSearch();
    fireEvent.change(screen.getByLabelText("Search cases, documents, customers and accounts"), { target: { value: "ravi" } });
    fireEvent.click(screen.getByText("Search"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The search failed.");
  });
});
