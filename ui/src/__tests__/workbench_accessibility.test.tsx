// SPDX-License-Identifier: Apache-2.0
/**
 * The accessibility suite for the workbench pages: every shell page and panel renders with no axe
 * violation (WCAG 2.x rules as axe-core ships them; colour contrast and page regions are judged by
 * the browser suite, since a component renders without the layout).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: (...args: unknown[]) => mockPut(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import BusinessConsole from "@/components/BusinessConsole";
import ReviewQueue from "@/components/ReviewQueue";
import WorkbenchSearch from "@/components/WorkbenchSearch";
import Workbench from "@/pages/Workbench";

const TABS = [
  { key: "queue", title: "Review queue", path: "/dashboard/workbench/review_officer/queue", source: "queue", sensitive: false, actions: ["decide", "edit"] },
  { key: "approvals", title: "Approvals", path: "/dashboard/approvals", source: "approvals", sensitive: false, actions: ["decide"] },
  { key: "search", title: "Search", path: "/dashboard/workbench/review_officer/search", source: "search", sensitive: false, actions: [] },
];
const BENCH = { name: "review_officer", title: "Review officer", description: "Items that wait for a decision.", held_by: "role", tabs: TABS };
const SUMMARY = { ...BENCH, counts: { queue: 3, approvals: 3, search: null }, waiting: 3 };
const DRAFT_ITEM = { kind: "draft", id: "d1", title: "Welcome letter", summary: "drafting letter", priority: "normal", status: "pending_approval", requested_by: "u1", created_at: null, due_at: null, age_seconds: 60, path: "/dashboard/workbench/review_officer/drafts", actions: ["approve", "reject", "edit"] };
const SETTING = { key: "documents.type_confidence_floor", title: "Document type confidence floor", description: "Below this a document goes to review.", group: "documents", kind: "number", default: 0.6, applies: "x", minimum: 0, maximum: 1, unit: "", options: [], value: 0.6, source: "default", updated_by: null, updated_at: null, previous: null };
const RULES = { ...SETTING, key: "queue.priority_rules", title: "Review queue priority rules", kind: "rules", default: [], value: [], minimum: null, maximum: null };
const KINDS = { ...SETTING, key: "content.approval_kinds", title: "Draft kinds", kind: "list", default: ["notice"], options: ["notice", "letter"], value: ["notice"], minimum: null, maximum: null };

function answer(url: string) {
  if (url === "/workbench") return Promise.resolve({ data: { enabled: true, role: "cfo", workbenches: [BENCH] } });
  if (url === "/workbench/review_officer/summary") return Promise.resolve({ data: SUMMARY });
  if (url === "/workbench/queue") return Promise.resolve({ data: { items: [DRAFT_ITEM], allowed_kinds: ["approval", "document", "draft", "case"], counts: { draft: 1 } } });
  if (url === "/workbench/queue/draft/d1") return Promise.resolve({ data: { kind: "draft", item: { title: "Welcome letter" }, editable: [{ name: "title", value: "Welcome letter" }, { name: "body", value: "x".repeat(200) }], decidable: true } });
  if (url === "/workbench/console") return Promise.resolve({ data: { groups: [{ key: "documents", title: "Document processing", settings: [SETTING, KINDS, RULES] }], total: 3 } });
  if (url.startsWith("/workbench/search")) {
    return Promise.resolve({
      data: {
        query: { must: ["ravi"], must_not: [] },
        kinds: ["case"],
        hits: [{ kind: "case", id: "c1", title: "Case KYB-1", subtitle: "onboarding", snippet: "Ravi", path: "/dashboard/approvals/cases/KYB-1", facets: { state: "awaiting_decision" }, updated_at: null }],
        counts: { case: 1 },
        facets: { case: { state: { awaiting_decision: 1 } } },
        total: 1,
        allowed_kinds: ["case", "document", "customer", "account"],
        filters: {},
      },
    });
  }
  return Promise.reject(new Error(`unexpected ${url}`));
}

async function violations(container: HTMLElement): Promise<string[]> {
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}

function at(path: string) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/dashboard/workbench" element={<Workbench />} />
          <Route path="/dashboard/workbench/:name" element={<Workbench />} />
          <Route path="/dashboard/workbench/:name/:tab" element={<Workbench />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

describe("workbench accessibility", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockImplementation((url: string) => answer(url));
  });

  it("the workbench index has no violations", async () => {
    const { container } = at("/dashboard/workbench");
    await screen.findByTestId("workbench-card");
    expect(await violations(container)).toEqual([]);
  });

  it("a workbench with its tabs has no violations", async () => {
    const { container } = at("/dashboard/workbench/review_officer/approvals");
    await screen.findByTestId("workbench-open");
    expect(await violations(container)).toEqual([]);
  });

  it("the review queue with an item open has no violations", async () => {
    const { container } = render(
      <MemoryRouter>
        <ReviewQueue />
      </MemoryRouter>,
    );
    const rows = await screen.findAllByTestId("queue-item");
    rows[0].click();
    await screen.findByTestId("queue-editable");
    expect(await violations(container)).toEqual([]);
  });

  it("the business console has no violations", async () => {
    const { container } = render(<BusinessConsole />);
    await screen.findByTestId("console-documents.type_confidence_floor");
    expect(await violations(container)).toEqual([]);
  });

  it("the search with results has no violations", async () => {
    const { container } = render(
      <MemoryRouter>
        <WorkbenchSearch />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText("Search cases, documents, customers and accounts"), { target: { value: "ravi" } });
    fireEvent.click(screen.getByText("Search"));
    await screen.findAllByTestId("search-hit");
    expect(await violations(container)).toEqual([]);
  });
});
