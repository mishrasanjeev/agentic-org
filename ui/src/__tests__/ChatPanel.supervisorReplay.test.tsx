// SPDX-License-Identifier: Apache-2.0
/**
 * The chat shows a supervisor's replies from the caller's own session: on reopening (replies sent while the
 * panel was closed) and when the live feed says one arrived (the feed never carries the text).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const listeners: Array<(event: Record<string, unknown>) => void> = [];

vi.mock("../lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("@/lib/websocket", () => ({
  AgenticOrgWS: class {
    subscribe(fn: (event: Record<string, unknown>) => void) {
      listeners.push(fn);
      return () => {
        const index = listeners.indexOf(fn);
        if (index >= 0) listeners.splice(index, 1);
      };
    }
    connect() {
      return undefined;
    }
    disconnect() {
      return undefined;
    }
  },
}));

vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { tenant_id: "tenant-a", user_id: "user-1", role: "analyst" } }),
}));

import ChatPanel, { mergeSupervisorTurns, type Message } from "@/components/ChatPanel";

const KEY = "web:c1:a1:u:user-1";
let replayed: Array<{ role: string; text: string; at: string }> = [];

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  listeners.splice(0, listeners.length);
  replayed = [
    { role: "system", text: "A supervisor has joined the conversation.", at: "2026-10-07T10:00:00+00:00" },
    { role: "supervisor", text: "I can help with the transfer.", at: "2026-10-07T10:01:00+00:00" },
  ];
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/chat/history")) {
      return Promise.resolve({
        data: [{ id: "h1", role: "user", text: "transfer 500 to Ravi", timestamp: "2026-10-07T09:59:00+00:00" }],
      });
    }
    if (url.startsWith("/conversation/session")) {
      return Promise.resolve({ data: { session_key: KEY, dialogue: {}, messages: replayed } });
    }
    return Promise.reject(new Error("unexpected"));
  });
});

describe("mergeSupervisorTurns", () => {
  it("adds supervisor replies and notices once each, in time order, and ignores other roles", () => {
    const prev: Message[] = [{ id: "u", role: "user", text: "hi", timestamp: new Date("2026-10-07T10:00:30+00:00") }];
    const merged = mergeSupervisorTurns(prev, [
      ...replayed,
      { role: "user", text: "not replayed", at: "2026-10-07T10:02:00+00:00" },
    ]);
    expect(merged.map((m) => m.text)).toEqual([
      "A supervisor has joined the conversation.",
      "hi",
      "I can help with the transfer.",
    ]);
    expect(merged[2].agent).toBe("Supervisor");
    expect(mergeSupervisorTurns(merged, replayed)).toBe(merged);
  });
});

describe("ChatPanel supervisor replies", () => {
  it("replays replies sent while the chat was closed when it opens", async () => {
    render(<ChatPanel open onClose={() => undefined} agentId="a1" />);
    expect(await screen.findByText("I can help with the transfer.")).toBeInTheDocument();
    expect(screen.getByText("transfer 500 to Ravi")).toBeInTheDocument();
    const sessionCall = mockGet.mock.calls.find(([url]) => String(url).startsWith("/conversation/session"));
    expect(String(sessionCall?.[0])).toContain("agent_id=a1");
  });

  it("reads a new reply from the session when the feed says one arrived, without text on the feed", async () => {
    render(<ChatPanel open onClose={() => undefined} agentId="a1" />);
    await screen.findByText("I can help with the transfer.");
    await waitFor(() => expect(listeners.length).toBeGreaterThan(0));
    replayed = [...replayed, { role: "supervisor", text: "Done, anything else?", at: "2026-10-07T10:05:00+00:00" }];
    await act(async () => {
      for (const listener of [...listeners]) listener({ type: "conversation.message", session_key: KEY, role: "supervisor" });
    });
    expect(await screen.findByText("Done, anything else?")).toBeInTheDocument();
    expect(screen.getAllByText("I can help with the transfer.")).toHaveLength(1);
  });
});
