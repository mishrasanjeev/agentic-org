// SPDX-License-Identifier: Apache-2.0
/**
 * The retrieval trace under the search results: one line per step, shown only when the response carries one.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
  documentsApi: { list: () => Promise.resolve([]) },
  knowledgeApi: { listDocuments: () => Promise.resolve({ documents: [] }) },
}));

import KnowledgeBase, { traceLine } from "@/pages/KnowledgeBase";

describe("traceLine", () => {
  it("says what each step did", () => {
    expect(traceLine({ stage: "plan", detail: { variants: ["a", "b"], rules: ["versus", "versus"] } })).toBe(
      "plan: 2 variants (versus, versus)",
    );
    expect(traceLine({ stage: "search", detail: { query: "home loan", hits: 2, best: 0.9 } })).toBe(
      'search: "home loan" · 2 hits · best 0.90',
    );
    expect(traceLine({ stage: "decision", detail: { action: "expand", reason: "1 of 3 hits" } })).toBe(
      "decision: expand (1 of 3 hits)",
    );
    expect(traceLine({ stage: "fuse", detail: { lists: 4, candidates: 3, returned: 3 } })).toBe(
      "fuse: 4 lists · 3 candidates · 3 returned",
    );
    expect(traceLine({ stage: "rewrite", detail: { model: "openai/x", added: 0, reason: "model_failed" } })).toBe(
      "rewrite: model openai/x added 0 (model_failed)",
    );
    expect(traceLine({ stage: "graph", detail: { matched: ["form 16"], neighbours: 2, edges: 2, chunks: 3 } })).toBe(
      "graph: form 16 · 2 neighbours · 3 chunks",
    );
    expect(traceLine({ stage: "graph", detail: { error: "RuntimeError" } })).toBe("graph: not consulted (RuntimeError)");
    expect(traceLine({ stage: "other" })).toBe("other");
  });
});

describe("KnowledgeBase trace", () => {
  it("asks for the trace and shows the steps under the results, nothing without one", async () => {
    mockGet.mockResolvedValue({ data: { documents: [], total: 0 } });
    mockPost.mockResolvedValueOnce({
      data: {
        results: [{ chunk_text: "home loan rates", score: 0.9, document_name: "Loans" }],
        trace: {
          steps: [
            { stage: "plan", elapsed_ms: 0, detail: { variants: ["home loan"], rules: ["versus"] } },
            { stage: "search", elapsed_ms: 3, detail: { query: "home loan vs personal loan", hits: 1, best: 0.2 } },
            { stage: "decision", elapsed_ms: 3, detail: { action: "expand", reason: "1 of 3 hits" } },
            { bad: true },
          ],
        },
      },
    });
    render(<KnowledgeBase />);
    const input = await screen.findByPlaceholderText(/test a query/i);
    fireEvent.change(input, { target: { value: "home loan vs personal loan" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/knowledge/search", { query: "home loan vs personal loan", trace: true }),
    );
    const trace = await screen.findByTestId("kb-search-trace");
    expect(trace).toHaveTextContent("How this was retrieved (3 steps)");
    expect(trace).toHaveTextContent("decision: expand (1 of 3 hits)");
    mockPost.mockResolvedValueOnce({
      data: { results: [{ chunk_text: "locker rent", score: 0.5, document_name: "Fees" }] },
    });
    fireEvent.change(input, { target: { value: "locker rent" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    await waitFor(() => expect(screen.getByTestId("kb-search-result")).toHaveTextContent("locker rent"));
    expect(screen.queryByTestId("kb-search-trace")).not.toBeInTheDocument();
  });
});
