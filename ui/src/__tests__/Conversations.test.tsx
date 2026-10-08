// SPDX-License-Identifier: Apache-2.0
/**
 * The supervisor console: live sessions, a transcript, takeover, reply and release.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const listeners: Array<(event: Record<string, unknown>) => void> = [];

vi.mock("@/lib/api", () => ({
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
    subscribeStatus() {
      return () => undefined;
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
  useAuth: () => ({ user: { tenant_id: "tenant-a", user_id: "sup@example.test", role: "admin" } }),
}));

import Conversations, { roleLabel, statusLabel } from "@/pages/Conversations";

const ROW = {
  id: "11111111-1111-4111-8111-111111111111",
  session_key: "web:c1:a1:u:user-1",
  user_id: "user-1",
  agent_id: "a1",
  channel: "web",
  status: "escalated",
  intent: "fund_transfer",
  stage: "confirming",
  turns: 3,
  taken_over_by: null,
  taken_over_at: null,
  escalation: { reason: "requested", intent: "fund_transfer", at: "t", hitl_id: "h1", ticket: { reference: "4711" } },
  updated_at: "2026-10-07T10:00:00+00:00",
};

function transcriptFor(held: string | null) {
  return {
    ...ROW,
    taken_over_by: held,
    history: [
      { role: "user", text: "transfer 500 to Ravi" },
      { role: "assistant", text: "Transfer ₹500 to Ravi. Reply yes to confirm or no to cancel." },
    ],
    slots: { amount: 500, payee: "Ravi" },
    escalation_summary: "Hand-off from chat: fund transfer with amount 500, payee Ravi because the user asked for a person.",
  };
}

let held: string | null = null;

beforeEach(() => {
  held = null;
  listeners.length = 0;
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.endsWith("/sessions")) return Promise.resolve({ data: { sessions: [{ ...ROW, taken_over_by: held }], total: 1 } });
    return Promise.resolve({ data: transcriptFor(held) });
  });
  mockPost.mockImplementation((url: string) => {
    if (url.endsWith("/takeover")) held = "sup@example.test";
    if (url.endsWith("/release")) held = null;
    return Promise.resolve({ data: {} });
  });
});

function renderPage() {
  return render(
    <HelmetProvider>
      <Conversations />
    </HelmetProvider>,
  );
}

describe("Conversations page", () => {
  it("lists live sessions and opens a transcript with its summary, slots and turns", async () => {
    renderPage();
    const row = await screen.findByTestId(`conversation-row-${ROW.id}`);
    expect(row.textContent).toContain("fund_transfer");
    expect(row.textContent).toContain("escalated");
    fireEvent.click(row);
    await screen.findByTestId("conversation-transcript");
    expect(screen.getByTestId("conversation-summary").textContent).toContain("Ticket 4711");
    expect(screen.getByTestId("conversation-slots").textContent).toContain("payee: Ravi");
    expect(screen.getByTestId("conversation-history").textContent).toContain("transfer 500 to Ravi");
    expect(mockGet).toHaveBeenCalledWith(`/conversation/supervisor/sessions/${ROW.id}`);
  });

  it("takes a session over, replies, and releases it", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId(`conversation-row-${ROW.id}`));
    fireEvent.click(await screen.findByTestId("conversation-takeover"));
    await screen.findByTestId("conversation-reply");
    expect(mockPost).toHaveBeenCalledWith(`/conversation/supervisor/sessions/${ROW.id}/takeover`);
    fireEvent.change(screen.getByTestId("conversation-reply"), { target: { value: "Hello, I am here to help." } });
    fireEvent.click(screen.getByTestId("conversation-send"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(`/conversation/supervisor/sessions/${ROW.id}/reply`, { text: "Hello, I am here to help." }));
    fireEvent.click(screen.getByTestId("conversation-release"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(`/conversation/supervisor/sessions/${ROW.id}/release`));
    await screen.findByTestId("conversation-takeover");
  });

  it("refreshes when the live feed announces a turn", async () => {
    renderPage();
    await screen.findByTestId(`conversation-row-${ROW.id}`);
    const calls = mockGet.mock.calls.length;
    listeners.forEach((fn) => fn({ type: "conversation.turn", session_key: ROW.session_key, role: "user", text: "yes" }));
    await waitFor(() => expect(mockGet.mock.calls.length).toBeGreaterThan(calls));
  });

  it("says when nothing is in progress and labels states", async () => {
    mockGet.mockImplementation(() => Promise.resolve({ data: { sessions: [], total: 0 } }));
    renderPage();
    await screen.findByTestId("conversations-empty");
    expect(statusLabel({ ...ROW, status: "active", taken_over_by: null })).toBe("active · confirming");
    expect(statusLabel({ ...ROW, taken_over_by: "sup" })).toBe("held by sup");
    expect(roleLabel("supervisor")).toBe("Supervisor");
  });
});
