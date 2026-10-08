// SPDX-License-Identifier: Apache-2.0
/**
 * The unified review queue: the list by kind, an item's editable fields, a decision with edits.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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

import ReviewQueue, { ageLabel, editKey } from "@/components/ReviewQueue";

const DRAFT = {
  kind: "draft",
  id: "d1",
  title: "Welcome letter",
  summary: "drafting letter",
  priority: "normal",
  status: "pending_approval",
  requested_by: "u1",
  created_at: "2026-10-07T10:00:00+00:00",
  due_at: null,
  age_seconds: 5400,
  path: "/dashboard/workbench/review_officer/drafts",
  actions: ["approve", "reject", "edit"],
};
const CASE = { ...DRAFT, kind: "case", id: "c1", title: "Case KYB-1", summary: "onboarding via x; 1 decision request(s)", priority: "high", status: "awaiting_decision", path: "/dashboard/approvals/cases/KYB-1", actions: ["open"] };

function renderQueue() {
  return render(
    <MemoryRouter>
      <ReviewQueue />
    </MemoryRouter>,
  );
}

describe("ReviewQueue", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation((url: string) => {
      if (url === "/workbench/queue") return Promise.resolve({ data: { items: [CASE, DRAFT], allowed_kinds: ["approval", "document", "draft", "case"], counts: { draft: 1, case: 1 } } });
      if (url === "/workbench/queue/draft/d1") {
        return Promise.resolve({ data: { kind: "draft", item: { title: "Welcome letter", output: { body: "Dear customer" } }, editable: [{ name: "title", value: "Welcome letter" }, { name: "body", value: "Dear customer" }], decidable: true } });
      }
      if (url === "/workbench/queue/case/c1") return Promise.resolve({ data: { kind: "case", item: { title: "Case KYB-1" }, editable: [], decidable: true } });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
  });

  it("formats ages and edit keys", () => {
    expect(ageLabel(null)).toBe("");
    expect(ageLabel(120)).toBe("2 min");
    expect(ageLabel(5400)).toBe("2 h");
    expect(ageLabel(200000)).toBe("2 d");
    expect(editKey({ name: "net_pay", value: "1", document_index: 2 })).toBe("2:net_pay");
    expect(editKey({ name: "title", value: "" })).toBe("0:title");
  });

  it("lists the items with kind filters and counts", async () => {
    renderQueue();
    const rows = await screen.findAllByTestId("queue-item");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Case KYB-1");
    expect(screen.getByTestId("queue-kind-draft").textContent).toContain("1");
    fireEvent.click(screen.getByTestId("queue-kind-draft"));
    await waitFor(() => expect(mockGet).toHaveBeenLastCalledWith("/workbench/queue", { params: { limit: "100", kind: "draft" } }));
  });

  it("edits a draft's fields and decides with the edits", async () => {
    mockPost.mockResolvedValue({ data: { kind: "draft", decision: "approve" } });
    renderQueue();
    const rows = await screen.findAllByTestId("queue-item");
    fireEvent.click(rows[1]);
    const title = await screen.findByTestId("queue-edit-title");
    fireEvent.change(title, { target: { value: "Welcome aboard" } });
    fireEvent.change(screen.getByTestId("queue-notes"), { target: { value: "ok" } });
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/workbench/queue/draft/d1/decide", {
        decision: "approve",
        notes: "ok",
        edits: [{ name: "title", value: "Welcome aboard", document_index: 0 }],
      }),
    );
    expect(await screen.findByTestId("queue-notice")).toHaveTextContent("Draft approved with 1 edit(s).");
  });

  it("sends a case to its own page instead of deciding it", async () => {
    renderQueue();
    const rows = await screen.findAllByTestId("queue-item");
    fireEvent.click(rows[0]);
    expect(await screen.findByTestId("queue-case-note")).toBeTruthy();
    expect(screen.queryByText("Approve")).toBeNull();
    expect(screen.getByText("Open in Case page").getAttribute("href")).toBe("/dashboard/approvals/cases/KYB-1");
  });

  it("reports a decision the backend refused", async () => {
    mockPost.mockRejectedValue(new Error("409"));
    renderQueue();
    const rows = await screen.findAllByTestId("queue-item");
    fireEvent.click(rows[1]);
    await screen.findByTestId("queue-editable");
    fireEvent.click(screen.getByText("Reject"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The decision was not recorded.");
  });
});
