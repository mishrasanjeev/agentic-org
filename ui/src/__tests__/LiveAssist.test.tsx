// SPDX-License-Identifier: Apache-2.0
/**
 * Live agent assist: opening a call, a turn with the checklist, mood, intent, knowledge and flags, closing.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import LiveAssist, { flagLabel } from "@/components/LiveAssist";

const CHECKLIST = [
  { key: "recorded_line", title: "Recorded line", script: "This call is being recorded.", state: "pending", at: null },
  { key: "identity_verification", title: "Identity verified", script: "I need to verify your identity.", state: "pending", at: null },
];
const OPENED = { id: "s1", status: "open", required: ["recorded_line", "identity_verification"], checklist: CHECKLIST };
const TURN = {
  id: "s1",
  status: "open",
  turn_count: 1,
  flags: [],
  checklist: [{ ...CHECKLIST[0], state: "late", at: 70 }, CHECKLIST[1]],
  compliant_so_far: false,
  mood: { score: -0.5, label: "negative" },
  sentiment: { overall: -0.5, negative_streak: 1 },
  intent: { name: "fund_transfer", title: "Fund transfer", confidence: 0.8 },
  next_step: { slot: "amount", question: "How much should I transfer?" },
  new_flags: [{ kind: "disclosure_overdue", key: "recorded_line", title: "Recorded line", at: 70 }],
  suggestions: [{ document: "Transfers FAQ", text: "Transfers above 2 lakh need a second factor.", score: 0.8 }],
};

describe("LiveAssist", () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it("labels flags", () => {
    expect(flagLabel({ kind: "disclosure_overdue", key: "recorded_line", title: "Recorded line", at: 70 })).toBe("Recorded line not said in time");
    expect(flagLabel({ kind: "negative_streak", key: null, title: "Two negative turns in a row", at: 80 })).toBe("Two negative turns in a row");
  });

  it("opens a call, takes a turn and shows what the agent should see, then closes", async () => {
    mockPost.mockImplementation((url: string) => {
      if (url === "/speech/live/sessions") return Promise.resolve({ data: OPENED });
      if (url === "/speech/live/sessions/s1/turns") return Promise.resolve({ data: TURN });
      if (url === "/speech/live/sessions/s1/close") return Promise.resolve({ data: { ...OPENED, status: "closed", report: { compliant: false, missing: [{ key: "identity_verification", title: "Identity verified" }], late: [{ key: "recorded_line" }] } } });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    render(<LiveAssist />);
    fireEvent.change(screen.getByTestId("live-ref"), { target: { value: "C-1" } });
    fireEvent.change(screen.getByTestId("live-type"), { target: { value: "loan" } });
    fireEvent.click(screen.getByTestId("live-start"));
    await screen.findByTestId("live-checklist");
    expect(mockPost).toHaveBeenCalledWith("/speech/live/sessions", { call_ref: "C-1", call_type: "loan" });
    expect(screen.getByTestId("live-checklist").textContent).toContain("Recorded line");
    fireEvent.change(screen.getByTestId("live-text"), { target: { value: "I want to transfer money" } });
    fireEvent.change(screen.getByTestId("live-seconds"), { target: { value: "70" } });
    fireEvent.click(screen.getByTestId("live-send"));
    await screen.findByTestId("live-view");
    expect(mockPost).toHaveBeenCalledWith("/speech/live/sessions/s1/turns", { speaker: "customer", text: "I want to transfer money", at: 70 });
    expect(screen.getByTestId("live-view").textContent).toContain("Fund transfer");
    expect(screen.getByTestId("live-view").textContent).toContain("How much should I transfer?");
    expect(screen.getByTestId("live-suggestions").textContent).toContain("Transfers FAQ");
    expect(screen.getByTestId("live-flags").textContent).toContain("Recorded line not said in time");
    expect(screen.getByTestId("live-checklist").textContent).toContain("late");
    fireEvent.click(screen.getByTestId("live-close"));
    expect(await screen.findByTestId("live-report")).toHaveTextContent("1 missing, 1 late");
    await waitFor(() => expect(screen.queryByTestId("live-send")).toBeNull());
    fireEvent.click(screen.getByTestId("live-new"));
    expect(await screen.findByTestId("live-start")).toBeTruthy();
  });

  it("reports a session that cannot be opened", async () => {
    mockPost.mockRejectedValue(new Error("500"));
    render(<LiveAssist />);
    fireEvent.click(screen.getByTestId("live-start"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The live session could not be opened.");
  });
});
