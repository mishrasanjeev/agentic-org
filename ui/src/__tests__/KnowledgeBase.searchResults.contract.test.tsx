/**
 * UI contract audit 2026-09-13 (P): POST /knowledge/search returns
 * {results: [{chunk_text, score, document_name}]}. The page used to type
 * the rows as string[] and render `<p>{r}</p>`, which throws
 * "Objects are not valid as a React child" and blanks the page.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import KnowledgeBase from "@/pages/KnowledgeBase";

describe("KnowledgeBase search results contract", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation((url: string) => {
      if (url === "/knowledge/documents") return Promise.resolve({ data: [] });
      if (url === "/knowledge/stats") return Promise.resolve({ data: {} });
      return Promise.resolve({ data: {} });
    });
  });

  it("renders object rows from /knowledge/search without crashing", async () => {
    mockPost.mockResolvedValue({
      data: {
        query: "gst rate",
        results: [
          { chunk_text: "The applicable GST rate is 18%.", score: 0.9123, document_name: "gst-guide.pdf" },
          { chunk_text: "Composition scheme threshold is 1.5 crore.", score: 0.71, document_name: "gst-guide.pdf" },
        ],
      },
    });

    render(<KnowledgeBase />);
    const input = await screen.findByPlaceholderText("Test a query against the knowledge base...");
    fireEvent.change(input, { target: { value: "gst rate" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/knowledge/search", { query: "gst rate" }));
    expect(await screen.findByText("The applicable GST rate is 18%.")).toBeTruthy();
    expect(screen.getByText("Composition scheme threshold is 1.5 crore.")).toBeTruthy();
    expect(screen.getAllByTestId("kb-search-result")).toHaveLength(2);
    // Provenance line: document name + score.
    expect(screen.getAllByText(/gst-guide\.pdf/).length).toBeGreaterThan(0);
    expect(screen.getByText(/score 0\.91/)).toBeTruthy();
  });
});
