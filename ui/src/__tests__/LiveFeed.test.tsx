import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const wsMock = vi.hoisted(() => ({
  subscribers: [] as Array<(data: Record<string, unknown>) => void>,
}));

vi.mock("@/lib/websocket", () => ({
  AgenticOrgWS: class {
    connect = vi.fn();
    disconnect = vi.fn();
    subscribe = vi.fn((fn: (data: Record<string, unknown>) => void) => {
      wsMock.subscribers.push(fn);
      return () => undefined;
    });
  },
}));

import LiveFeed from "@/components/LiveFeed";

describe("LiveFeed", () => {
  beforeEach(() => {
    wsMock.subscribers = [];
  });

  it("does not render heartbeat messages as activity", () => {
    render(<LiveFeed tenantId="tenant-a" />);
    expect(screen.getByText("Waiting for events...")).toBeInTheDocument();

    act(() => {
      wsMock.subscribers[0]({ type: "heartbeat", sequence: null });
    });

    expect(screen.getByText("Waiting for events...")).toBeInTheDocument();

    act(() => {
      wsMock.subscribers[0]({
        type: "approval.created",
        sequence: 7,
        payload: { title: "Approval needed" },
      });
    });

    expect(screen.getByText(/approval.created:/)).toBeInTheDocument();
    expect(screen.getByText(/Approval needed/)).toBeInTheDocument();
  });
});
