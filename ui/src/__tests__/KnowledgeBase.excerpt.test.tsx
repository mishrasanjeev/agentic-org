// SPDX-License-Identifier: Apache-2.0
/**
 * Citations beside search hits and the excerpt opened from one: the query terms marked, previous and next.
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

import KnowledgeBase, { citationLabel, markedPieces, normalizeSearchResult } from "@/pages/KnowledgeBase";

const CITATION = { document_id: "doc-1", source: "upload://policy.pdf#chunk3-abc", chunk_index: 3, page: 4, paragraph: 12, heading: "Exposure limits" };

describe("citations", () => {
  it("labels a citation and keeps it on a normalised hit", () => {
    expect(citationLabel(CITATION)).toBe("page 4 · paragraph 12 · Exposure limits");
    expect(citationLabel({ document_id: "d", sheet: "Q1", cell_range: "A1:C9" })).toBe("sheet Q1 · A1:C9");
    expect(citationLabel(null)).toBe("");
    const hit = normalizeSearchResult({ chunk_text: "t", score: 0.5, document_name: "Policy", citation: CITATION });
    expect(hit?.citation?.document_id).toBe("doc-1");
    expect(normalizeSearchResult({ chunk_text: "t", citation: { nope: true } })?.citation).toBeUndefined();
  });

  it("marks the highlighted spans and leaves the rest plain", () => {
    const pieces = markedPieces("Exposure is capped.", [[0, 8], [12, 18]]);
    expect(pieces).toEqual([
      { text: "Exposure", marked: true },
      { text: " is ", marked: false },
      { text: "capped", marked: true },
      { text: ".", marked: false },
    ]);
    expect(markedPieces("abc", [[2, 1], [5, 9]])).toEqual([{ text: "abc", marked: false }]);
  });
});

describe("KnowledgeBase excerpt", () => {
  it("shows the citation on a hit and opens the excerpt with the terms marked", async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === "/knowledge/documents") return Promise.resolve({ data: { documents: [], total: 0 } });
      if (path === "/knowledge/documents/doc-1/excerpt")
        return Promise.resolve({
          data: {
            document_id: "doc-1",
            document_name: "Policy",
            content: "Exposure is capped at the limit.",
            citation: CITATION,
            highlights: [[0, 8]],
            previous_id: "doc-0",
            next_id: null,
          },
        });
      if (path === "/knowledge/documents/doc-0/excerpt")
        return Promise.resolve({
          data: { document_id: "doc-0", document_name: "Policy", content: "Earlier text.", citation: null, highlights: [], previous_id: null, next_id: "doc-1" },
        });
      return Promise.resolve({ data: {} });
    });
    mockPost.mockResolvedValue({
      data: { results: [{ chunk_text: "Exposure is capped", score: 0.9, document_name: "Policy", citation: CITATION }] },
    });
    render(<KnowledgeBase />);
    const input = await screen.findByPlaceholderText(/test a query/i);
    fireEvent.change(input, { target: { value: "exposure" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    const hit = await screen.findByTestId("kb-search-result");
    expect(hit).toHaveTextContent("page 4 · paragraph 12 · Exposure limits");
    fireEvent.click(screen.getByTestId("kb-open-excerpt-0"));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/knowledge/documents/doc-1/excerpt", { params: { q: "exposure" } }));
    const excerpt = await screen.findByTestId("kb-excerpt");
    expect(excerpt).toHaveTextContent("Policy · page 4 · paragraph 12 · Exposure limits");
    expect(screen.getByTestId("kb-excerpt-text").querySelector("mark")?.textContent).toBe("Exposure");
    expect(screen.getByTestId("kb-excerpt-next")).toBeDisabled();
    fireEvent.click(screen.getByTestId("kb-excerpt-previous"));
    await waitFor(() => expect(screen.getByTestId("kb-excerpt-text")).toHaveTextContent("Earlier text."));
    fireEvent.click(screen.getByTestId("kb-excerpt-close"));
    expect(screen.queryByTestId("kb-excerpt")).not.toBeInTheDocument();
  });
});
