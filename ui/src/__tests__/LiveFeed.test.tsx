import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const wsMock = vi.hoisted(() => ({
  subscribers: [] as Array<(data: Record<string, unknown>) => void>,
  statusSubscribers: [] as Array<(status: string) => void>,
}));

vi.mock("@/lib/websocket", () => ({
  AgenticOrgWS: class {
    connect = vi.fn();
    disconnect = vi.fn();
    subscribe = vi.fn((fn: (data: Record<string, unknown>) => void) => {
      wsMock.subscribers.push(fn);
      return () => undefined;
    });
    subscribeStatus = vi.fn((fn: (status: string) => void) => {
      wsMock.statusSubscribers.push(fn);
      fn("connecting");
      return () => undefined;
    });
  },
}));

import LiveFeed from "@/components/LiveFeed";

describe("LiveFeed", () => {
  beforeEach(() => {
    wsMock.subscribers = [];
    wsMock.statusSubscribers = [];
  });

  it("does not render heartbeat messages as activity", () => {
    render(<LiveFeed tenantId="tenant-a" />);
    expect(screen.getByText("No live events yet.")).toBeInTheDocument();

    act(() => {
      wsMock.subscribers[0]({ type: "heartbeat", sequence: null });
    });

    expect(screen.getByText("No live events yet.")).toBeInTheDocument();

    act(() => {
      wsMock.subscribers[0]({
        type: "approval.created",
        sequence: 7,
        payload: { title: "Approval needed" },
      });
    });

    expect(screen.getByText("approval created")).toBeInTheDocument();
    expect(screen.queryByText(/Approval needed/)).not.toBeInTheDocument();
  });

  it("shows connection and catch-up state without leaking event payloads", () => {
    render(<LiveFeed tenantId="tenant-a" />);
    act(() => wsMock.statusSubscribers[0]("delayed"));
    expect(screen.getByText("Updates delayed")).toBeInTheDocument();
    expect(screen.getByText(/Catch-up is retrying/)).toBeInTheDocument();
    act(() => wsMock.subscribers[0]({ type: "task.finished", sequence: 1, payload: { token: "private-value" } }));
    expect(screen.getByText("task finished")).toBeInTheDocument();
    expect(screen.queryByText(/private-value/)).not.toBeInTheDocument();
    act(() => wsMock.statusSubscribers[0]("sign_in_required"));
    expect(screen.getByText("Session ended")).toBeInTheDocument();
    expect(screen.getByText(/Sign in again/)).toBeInTheDocument();
  });
});
