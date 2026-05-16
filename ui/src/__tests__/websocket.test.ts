import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgenticOrgWS } from "@/lib/websocket";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1000 } as CloseEvent);
  });

  emitOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: string) {
    this.onmessage?.({ data } as MessageEvent<string>);
  }

  emitClose(code: number) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }
}

describe("AgenticOrgWS", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("does not reconnect after explicit disconnect", () => {
    const client = new AgenticOrgWS({ baseDelayMs: 10, jitterRatio: 0, maxRetries: 3 });
    client.connect("tenant-a");
    const socket = MockWebSocket.instances[0];
    socket.emitOpen();

    client.disconnect();
    vi.advanceTimersByTime(1000);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("treats auth failure as terminal", () => {
    const client = new AgenticOrgWS({ baseDelayMs: 10, jitterRatio: 0, maxRetries: 3 });
    client.connect("tenant-a");
    MockWebSocket.instances[0].emitClose(1008);

    vi.advanceTimersByTime(1000);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("isolates bad JSON socket messages", () => {
    const listener = vi.fn();
    const client = new AgenticOrgWS({ baseDelayMs: 10, jitterRatio: 0 });
    client.subscribe(listener);
    client.connect("tenant-a");
    const socket = MockWebSocket.instances[0];

    socket.emitMessage("{bad-json");
    socket.emitMessage(JSON.stringify({ type: "activity", sequence: 1 }));

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith({ type: "activity", sequence: 1 });
  });

  it("fetches missed events after reconnect using the last seen sequence", async () => {
    const listener = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [{ type: "missed", sequence: 2 }] }),
    } as Response);
    const client = new AgenticOrgWS({
      baseDelayMs: 10,
      jitterRatio: 0,
      fetchImpl,
    });
    client.subscribe(listener);
    client.connect("tenant-a");
    const firstSocket = MockWebSocket.instances[0];
    firstSocket.emitMessage(JSON.stringify({ type: "seen", sequence: 1 }));
    firstSocket.emitClose(1006);

    vi.advanceTimersByTime(10);
    const reconnectSocket = MockWebSocket.instances[1];
    reconnectSocket.emitOpen();
    await vi.runAllTicks();

    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/feed/events?after=1&limit=100", {
      credentials: "include",
    });
    await vi.waitFor(() => {
      expect(listener).toHaveBeenCalledWith({ type: "missed", sequence: 2 });
    });
  });
});
