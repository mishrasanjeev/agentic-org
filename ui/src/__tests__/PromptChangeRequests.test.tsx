// SPDX-License-Identifier: Apache-2.0
/**
 * Prompt change requests panel: hidden while maker-checker is off and nothing
 * is pending, the pending list, the side-by-side review, and the decisions.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import PromptChangeRequests from "@/components/prompts/PromptChangeRequests";

const CHANGE = {
  id: "c1",
  template_id: "t1",
  kind: "update",
  domain: "ops",
  proposed: { template_text: "You are the claims agent. Be brief." },
  reason: "tighten the wording",
  status: "pending",
  requested_by: "user:maker",
  requested_at: "2026-10-04T09:00:00+00:00",
};

const DETAIL = { ...CHANGE, current: { name: "claims agent", template_text: "You are the claims agent." } };

function route(makerChecker: boolean, changes = [CHANGE]) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/prompt-templates/changes") return Promise.resolve({ data: { maker_checker: makerChecker, changes } });
    if (url === "/prompt-templates/changes/c1") return Promise.resolve({ data: DETAIL });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

describe("PromptChangeRequests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPost.mockResolvedValue({ data: { status: "approved" } });
  });

  it("shows nothing while maker-checker is off and nothing is pending, or when it cannot load", async () => {
    route(false, []);
    const { container, unmount } = render(<PromptChangeRequests />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    mockGet.mockRejectedValue(new Error("forbidden"));
    const second = render(<PromptChangeRequests />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });

  it("lists what is waiting and says maker-checker is on", async () => {
    route(true);
    render(<PromptChangeRequests />);
    const row = await screen.findByTestId("prompt-change-c1");
    expect(row).toHaveTextContent("Change");
    expect(row).toHaveTextContent("user:maker");
    expect(screen.getByTestId("prompt-changes")).toHaveTextContent("only after a second person approves");
  });

  it("shows the template as it is beside what is proposed, and approves", async () => {
    route(true);
    const onDecided = vi.fn();
    render(<PromptChangeRequests onDecided={onDecided} />);
    fireEvent.click(await screen.findByTestId("prompt-change-review-c1"));
    expect(await screen.findByTestId("prompt-change-current")).toHaveTextContent("You are the claims agent.");
    expect(screen.getByTestId("prompt-change-proposed")).toHaveTextContent("Be brief.");
    fireEvent.click(screen.getByTestId("prompt-change-approve"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/prompt-templates/changes/c1/approve", { note: null }));
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
  });

  it("needs a note to reject and can withdraw", async () => {
    route(true);
    render(<PromptChangeRequests />);
    fireEvent.click(await screen.findByTestId("prompt-change-review-c1"));
    const reject = await screen.findByTestId("prompt-change-reject");
    expect(reject).toBeDisabled();
    fireEvent.change(screen.getByTestId("prompt-change-note"), { target: { value: "wrong tone" } });
    fireEvent.click(reject);
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/prompt-templates/changes/c1/reject", { note: "wrong tone" }),
    );
    fireEvent.click(await screen.findByTestId("prompt-change-review-c1"));
    fireEvent.click(await screen.findByTestId("prompt-change-withdraw"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/prompt-templates/changes/c1/withdraw"));
  });

  it("says when a decision was refused", async () => {
    route(true);
    mockPost.mockRejectedValue(new Error("403"));
    render(<PromptChangeRequests />);
    fireEvent.click(await screen.findByTestId("prompt-change-review-c1"));
    fireEvent.click(await screen.findByTestId("prompt-change-approve"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The decision was not recorded.");
  });
});
