// SPDX-License-Identifier: Apache-2.0
/**
 * Workbench shell: the index of held workbenches, a workbench's tabs with counts, the drafts panel.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Workbench, { countLabel, shellPanelOf } from "@/pages/Workbench";

const REVIEW = {
  name: "review_officer",
  title: "Review officer",
  description: "Items that wait for a decision.",
  held_by: "role",
  tabs: [
    { key: "approvals", title: "Approvals", path: "/dashboard/approvals", source: "approvals", sensitive: false, actions: ["decide", "edit"] },
    { key: "drafts", title: "Content drafts", path: "/dashboard/workbench/review_officer/drafts", source: "drafts", sensitive: false, actions: ["decide"] },
  ],
};
const SUMMARY = { ...REVIEW, counts: { approvals: 3, drafts: 1 }, waiting: 4 };
const DRAFT = { id: "d1", service: "drafting", kind: "letter", status: "pending_approval", title: "Welcome letter", created_by: "u1", created_at: "2026-10-07T10:00:00+00:00" };

function renderAt(path: string) {
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

function answer(url: string, params?: Record<string, string>) {
  if (url === "/workbench") return Promise.resolve({ data: { enabled: true, role: "cfo", workbenches: [REVIEW] } });
  if (url === "/workbench/review_officer/summary") return Promise.resolve({ data: SUMMARY });
  if (url === "/content/drafts") return Promise.resolve({ data: { drafts: params?.status === "pending_approval" ? [DRAFT] : [], total: 1 } });
  return Promise.reject(new Error(`unexpected ${url}`));
}

describe("Workbench", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation((url: string, config?: { params?: Record<string, string> }) => answer(url, config?.params));
  });

  it("names the shell tabs and formats counts", () => {
    expect(shellPanelOf(REVIEW.tabs[0])).toBeNull();
    expect(shellPanelOf(REVIEW.tabs[1])).toBe("drafts");
    expect(countLabel(null)).toBe("—");
    expect(countLabel(0)).toBe("0");
    expect(countLabel(1200)).toBe("999+");
  });

  it("says when workbenches are off", async () => {
    mockGet.mockResolvedValueOnce({ data: { enabled: false, workbenches: [] } });
    renderAt("/dashboard/workbench");
    expect(await screen.findByTestId("workbench-off")).toBeTruthy();
  });

  it("lists the held workbenches with a link to each", async () => {
    renderAt("/dashboard/workbench");
    const card = await screen.findByTestId("workbench-card");
    expect(card.getAttribute("href")).toBe("/dashboard/workbench/review_officer");
    expect(card.textContent).toContain("Review officer");
    expect(card.textContent).toContain("by role");
  });

  it("shows a workbench's tabs with counts and opens the page behind a tab", async () => {
    renderAt("/dashboard/workbench/review_officer");
    expect(await screen.findByTestId("workbench-count-approvals")).toHaveTextContent("3");
    const open = await screen.findByTestId("workbench-open");
    expect(open.textContent).toContain("3 waiting in Approvals");
    expect(open.textContent).toContain("decide, edit");
    expect(screen.getByText("Open Approvals").getAttribute("href")).toBe("/dashboard/approvals");
    fireEvent.click(screen.getByTestId("workbench-refresh"));
    await waitFor(() => expect(mockGet.mock.calls.filter((c) => c[0] === "/workbench/review_officer/summary").length).toBe(2));
  });

  it("renders the drafts panel in the shell and records a decision", async () => {
    mockPost.mockResolvedValue({ data: { id: "d1", status: "approved" } });
    renderAt("/dashboard/workbench/review_officer/drafts");
    const row = await screen.findByTestId("workbench-draft");
    expect(row.textContent).toContain("Welcome letter");
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/content/drafts/d1/decide", { decision: "approve", notes: "" }));
    await waitFor(() => expect(mockGet.mock.calls.filter((c) => c[0] === "/content/drafts").length).toBe(2));
  });

  it("reports a workbench that cannot be loaded", async () => {
    mockGet.mockImplementation((url: string) => (url === "/workbench" ? answer(url) : Promise.reject(new Error("404"))));
    renderAt("/dashboard/workbench/nothing");
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load the workbench.");
  });
});
