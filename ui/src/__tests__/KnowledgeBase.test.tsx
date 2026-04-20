/**
 * KnowledgeBase component — regression tests for the 20-Apr QA bugs:
 *   TC_005  Deleting one file no longer wipes the whole list.
 *   TC_006  "ready" is normalised (server-side) and shows a green badge.
 *   TC_010  Uploaded column renders a real timestamp, not "-".
 *   TC_011  Duplicate filename upload triggers a confirm dialog and,
 *           when the user cancels, does not call POST.
 */
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

import KnowledgeBase from "@/pages/KnowledgeBase";

const THREE_DOCS = [
  {
    document_id: "doc-a",
    filename: "alpha.pdf",
    status: "indexed",
    size_bytes: 12345,
    created_at: "2026-04-20T09:15:00Z",
    uploaded_at: "2026-04-20T09:15:00Z",
  },
  {
    document_id: "doc-b",
    filename: "beta.pdf",
    status: "indexed",
    size_bytes: 56789,
    created_at: "2026-04-20T09:20:00Z",
    uploaded_at: "2026-04-20T09:20:00Z",
  },
  {
    document_id: "doc-c",
    filename: "gamma.pdf",
    status: "processing",
    size_bytes: 9999,
    created_at: "2026-04-20T09:25:00Z",
    uploaded_at: "2026-04-20T09:25:00Z",
  },
];

const STATS = {
  total_documents: 3,
  total_chunks: 42,
  index_size_mb: 1.2,
};

function armFetches(docs = THREE_DOCS) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/knowledge/documents") {
      return Promise.resolve({ data: { items: docs, total: docs.length } });
    }
    if (url === "/knowledge/stats") {
      return Promise.resolve({ data: STATS });
    }
    return Promise.resolve({ data: null });
  });
  mockPost.mockResolvedValue({ data: { document_id: "new" } });
  mockDelete.mockResolvedValue({ data: { ok: true } });
}

describe("KnowledgeBase", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the uploaded date from uploaded_at (TC_010)", async () => {
    armFetches();
    render(<KnowledgeBase />);
    await waitFor(() => {
      expect(screen.getByText("alpha.pdf")).toBeInTheDocument();
    });
    // The dash is what we used to render. A real timestamp should
    // contain digits (localised; don't assert exact format).
    const row = screen.getByText("alpha.pdf").closest("tr")!;
    const cells = row.querySelectorAll("td");
    const uploadedCell = cells[3]; // Filename, Status, Size, Uploaded, Actions
    expect(uploadedCell.textContent).not.toBe("-");
    expect(uploadedCell.textContent).toMatch(/\d/);
  });

  it("deletes only the targeted row and does NOT wipe the list (TC_005)", async () => {
    armFetches();
    render(<KnowledgeBase />);
    await waitFor(() => {
      expect(screen.getByText("alpha.pdf")).toBeInTheDocument();
      expect(screen.getByText("beta.pdf")).toBeInTheDocument();
      expect(screen.getByText("gamma.pdf")).toBeInTheDocument();
    });

    // Clicking Delete on alpha should DELETE /knowledge/documents/doc-a
    // and then refetch. Arm the next GET to return only beta + gamma.
    mockGet.mockImplementation((url: string) => {
      if (url === "/knowledge/documents") {
        return Promise.resolve({
          data: { items: THREE_DOCS.slice(1), total: 2 },
        });
      }
      if (url === "/knowledge/stats") return Promise.resolve({ data: STATS });
      return Promise.resolve({ data: null });
    });

    const alphaRow = screen.getByText("alpha.pdf").closest("tr")!;
    const deleteBtn = alphaRow.querySelector("button")!;
    await act(async () => {
      fireEvent.click(deleteBtn);
    });

    await waitFor(() => {
      expect(mockDelete).toHaveBeenCalledWith(
        "/knowledge/documents/doc-a",
      );
    });
    // After refetch, beta and gamma still present; alpha gone.
    await waitFor(() => {
      expect(screen.queryByText("alpha.pdf")).not.toBeInTheDocument();
      expect(screen.getByText("beta.pdf")).toBeInTheDocument();
      expect(screen.getByText("gamma.pdf")).toBeInTheDocument();
    });
  });

  it("maps legacy status='ready' from older server builds to a known badge (TC_006)", async () => {
    // A server that hasn't rolled out the _normalize_status fix may still
    // send 'ready'. The frontend normaliser in fetchData must not leave
    // the row in a broken badge state.
    const legacy = [
      { ...THREE_DOCS[0], status: "ready" },
    ];
    armFetches(legacy);
    render(<KnowledgeBase />);
    await waitFor(() => {
      expect(screen.getByText("alpha.pdf")).toBeInTheDocument();
    });
    // Non-canonical statuses are coerced to 'indexed' (green/success badge),
    // not rendered raw.
    expect(screen.queryByText("ready")).not.toBeInTheDocument();
    expect(screen.getByText("indexed")).toBeInTheDocument();
  });

  it("warns on duplicate filename upload and skips POST when user cancels (TC_011)", async () => {
    armFetches();
    render(<KnowledgeBase />);
    await waitFor(() => {
      expect(screen.getByText("alpha.pdf")).toBeInTheDocument();
    });

    // Simulate uploading a new file with the same filename as an existing doc.
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const dupFile = new File(["..."], "alpha.pdf", { type: "application/pdf" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: dupFile, length: 1, item: (i: number) => [dupFile][i] },
    });

    await act(async () => {
      fireEvent.change(fileInput);
    });

    // User clicked Cancel on the confirm dialog (beforeEach returns false)
    expect(window.confirm).toHaveBeenCalled();
    // No POST should be made because the user cancelled.
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("sends allow_duplicate=true when user confirms the overwrite (TC_011)", async () => {
    armFetches();
    vi.spyOn(window, "confirm").mockReturnValue(true); // user clicks OK
    render(<KnowledgeBase />);
    await waitFor(() => {
      expect(screen.getByText("alpha.pdf")).toBeInTheDocument();
    });

    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const dupFile = new File(["..."], "alpha.pdf", { type: "application/pdf" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: dupFile, length: 1, item: (i: number) => [dupFile][i] },
    });

    await act(async () => {
      fireEvent.change(fileInput);
    });

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/knowledge/upload?allow_duplicate=true",
        expect.anything(),
      );
    });
  });
});
