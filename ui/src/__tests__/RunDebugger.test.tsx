// SPDX-License-Identifier: Apache-2.0
/**
 * The debugging console for one run: steps, the selected state, inspection by path, and step/continue while paused.
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
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import RunDebugger, { formatValue, stepLabel } from "@/components/RunDebugger";

const AGENT = "11111111-1111-4111-8111-111111111111";
const THREAD = "tenant:22222222-2222-4222-8222-222222222222:run:abc";

const THREAD_OUT = {
  thread_id: THREAD,
  total: 3,
  paused: true,
  next: ["execute_tools"],
  pseudonymised: false,
  steps: [
    { index: 0, checkpoint_id: "c0", step: -1, source: "input", node: "", next: [], changed: [], state: {} },
    {
      index: 1,
      checkpoint_id: "c1",
      step: 0,
      source: "loop",
      node: "__start__",
      next: ["reason"],
      changed: ["status", "messages"],
      state: { status: "running", messages: [{ type: "human", content: "hi" }] },
    },
    {
      index: 2,
      checkpoint_id: "c2",
      step: 1,
      source: "loop",
      node: "reason",
      next: ["execute_tools"],
      changed: ["messages"],
      state: { status: "running", messages: [{ type: "human", content: "hi" }, { type: "ai", content: "", tool_calls: ["lookup"] }] },
    },
  ],
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/steps/")) return Promise.resolve({ data: { path: "status", value: "running", truncated: false, bytes: 9 } });
    return Promise.resolve({ data: THREAD_OUT });
  });
});

describe("RunDebugger", () => {
  it("lists the steps, selects the latest one and shows its state", async () => {
    render(<RunDebugger agentId={AGENT} threadId={THREAD} />);
    await screen.findByTestId("debugger-steps");
    expect(mockGet).toHaveBeenCalledWith(`/agents/${AGENT}/debug/threads/${encodeURIComponent(THREAD)}`);
    expect(screen.getByTestId("debugger-step-0").textContent).toContain("input");
    expect(screen.getByTestId("debugger-step-2").textContent).toContain("reason");
    expect(screen.getByTestId("debugger-changed-1").textContent).toContain("status, messages");
    expect(screen.getByTestId("debugger-state").textContent).toContain("lookup");
    expect(screen.getByTestId("debugger-paused").textContent).toContain("paused before execute_tools");
  });

  it("shows an earlier step when it is chosen and inspects a value by its path", async () => {
    render(<RunDebugger agentId={AGENT} threadId={THREAD} />);
    fireEvent.click(await screen.findByTestId("debugger-step-1"));
    expect(screen.getByTestId("debugger-state").textContent).not.toContain("lookup");
    fireEvent.change(screen.getByTestId("debugger-path"), { target: { value: "status" } });
    fireEvent.click(screen.getByTestId("debugger-inspect"));
    await waitFor(() => expect(screen.getByTestId("debugger-inspected").textContent).toContain("running"));
    expect(mockGet).toHaveBeenCalledWith(`/agents/${AGENT}/debug/threads/${encodeURIComponent(THREAD)}/steps/c1`, {
      params: { path: "status" },
    });
  });

  it("steps a paused run and reloads the steps", async () => {
    mockPost.mockResolvedValue({ data: { status: "paused", paused_before: ["evaluate"] } });
    render(<RunDebugger agentId={AGENT} threadId={THREAD} />);
    fireEvent.click(await screen.findByTestId("debugger-step"));
    await waitFor(() => expect(screen.getByTestId("debugger-notice").textContent).toContain("Paused before evaluate"));
    expect(mockPost).toHaveBeenCalledWith(`/agents/${AGENT}/debug/threads/${encodeURIComponent(THREAD)}/step`);
    expect(mockGet.mock.calls.filter((call) => !String(call[0]).includes("/steps/")).length).toBe(2);
  });

  it("says when a continued run finished", async () => {
    mockPost.mockResolvedValue({ data: { status: "completed", paused_before: [] } });
    render(<RunDebugger agentId={AGENT} threadId={THREAD} />);
    fireEvent.click(await screen.findByTestId("debugger-continue"));
    await waitFor(() => expect(screen.getByTestId("debugger-notice").textContent).toContain("finished with status completed"));
    expect(mockPost).toHaveBeenCalledWith(`/agents/${AGENT}/debug/threads/${encodeURIComponent(THREAD)}/continue`);
  });

  it("reports a failed load", async () => {
    mockGet.mockRejectedValue(new Error("nope"));
    render(<RunDebugger agentId={AGENT} threadId={THREAD} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Failed to load the run's steps.");
  });

  it("labels steps and formats values", () => {
    expect(stepLabel({ ...THREAD_OUT.steps[0] })).toBe("input");
    expect(stepLabel({ ...THREAD_OUT.steps[2] })).toBe("reason");
    expect(stepLabel({ ...THREAD_OUT.steps[2], node: "" })).toBe("state");
    expect(formatValue("plain")).toBe("plain");
    expect(formatValue({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
});
