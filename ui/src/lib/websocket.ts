export interface FeedMessage {
  type?: string;
  sequence?: number | null;
  [key: string]: unknown;
}

type Listener = (data: FeedMessage) => void;
export type FeedConnectionStatus = "connecting" | "live" | "reconnecting" | "delayed" | "sign_in_required" | "offline";
type StatusListener = (status: FeedConnectionStatus) => void;

interface AgenticOrgWSOptions {
  maxRetries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  jitterRatio?: number;
  catchUpLimit?: number;
  fetchImpl?: typeof fetch;
}

const TERMINAL_CLOSE_CODES = new Set([1008, 4401, 4403]);

export class AgenticOrgWS {
  private ws: WebSocket | null = null;
  private readonly listeners = new Set<Listener>();
  private readonly statusListeners = new Set<StatusListener>();
  private status: FeedConnectionStatus = "offline";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private catchUpTimer: ReturnType<typeof setTimeout> | null = null;
  private catchUpRetryCount = 0;
  private tenantId: string | null = null;
  private intentionalClose = false;
  private terminal = false;
  private retryCount = 0;
  private lastSequence = 0;
  private readonly pending = new Map<number, FeedMessage>();
  private generation = 0;
  private catchUpGeneration = -1;
  private readonly maxRetries: number;
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly jitterRatio: number;
  private readonly catchUpLimit: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: AgenticOrgWSOptions = {}) {
    this.maxRetries = options.maxRetries ?? 8;
    this.baseDelayMs = options.baseDelayMs ?? 1000;
    this.maxDelayMs = options.maxDelayMs ?? 30000;
    this.jitterRatio = options.jitterRatio ?? 0.25;
    this.catchUpLimit = options.catchUpLimit ?? 100;
    this.fetchImpl = options.fetchImpl ?? fetch.bind(window);
  }

  connect(tenantId: string) {
    if (this.isSocketActive() && this.tenantId === tenantId) {
      return;
    }
    this.clearReconnectTimer();
    this.clearCatchUpTimer();
    this.catchUpRetryCount = 0;
    if (this.ws && this.tenantId !== tenantId) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    if (this.tenantId !== tenantId) {
      this.generation += 1;
      this.lastSequence = 0;
      this.pending.clear();
    }
    this.tenantId = tenantId;
    this.intentionalClose = false;
    this.terminal = false;
    this.setStatus("connecting");
    this.openSocket();
  }

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  subscribeStatus(fn: StatusListener) {
    this.statusListeners.add(fn);
    fn(this.status);
    return () => this.statusListeners.delete(fn);
  }

  disconnect() {
    this.generation += 1;
    this.pending.clear();
    this.intentionalClose = true;
    this.terminal = true;
    this.clearReconnectTimer();
    this.clearCatchUpTimer();
    this.catchUpRetryCount = 0;
    if (this.ws) {
      this.ws.onclose = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setStatus("offline");
  }

  private openSocket() {
    if (!this.tenantId || this.terminal) {
      return;
    }
    if (this.isSocketActive()) {
      return;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = new URL(`/api/v1/ws/feed/${encodeURIComponent(this.tenantId)}`, window.location.origin);
    url.protocol = protocol;

    this.ws = new WebSocket(url.toString());
    this.ws.onopen = () => {
      this.retryCount = 0;
      if (this.lastSequence > 0) {
        this.setStatus("delayed");
        void this.catchUp();
      } else {
        this.setStatus("live");
      }
    };
    this.ws.onmessage = (event) => this.handleMessage(event.data);
    this.ws.onerror = () => {
      // The close event carries the actionable policy decision.
    };
    this.ws.onclose = (event) => {
      this.ws = null;
      if (this.intentionalClose || this.terminal) {
        return;
      }
      if (TERMINAL_CLOSE_CODES.has(event.code)) {
        this.terminal = true;
        this.setStatus("sign_in_required");
        return;
      }
      this.scheduleReconnect();
    };
  }

  private handleMessage(rawData: unknown) {
    if (typeof rawData !== "string") {
      return;
    }
    let data: FeedMessage;
    try {
      data = JSON.parse(rawData) as FeedMessage;
    } catch {
      return;
    }
    this.accept(data);
  }

  private accept(data: FeedMessage) {
    const sequence = typeof data.sequence === "number" ? data.sequence : null;
    if (sequence === null) {
      this.notify(data);
      return;
    }
    if (sequence <= this.lastSequence) return;
    if (this.lastSequence !== 0 && sequence !== this.lastSequence + 1) {
      if (this.pending.size >= 500) {
        this.pending.clear();
        this.setStatus("delayed");
        this.ws?.close();
        return;
      }
      this.pending.set(sequence, data);
      this.setStatus("delayed");
      void this.catchUp();
      return;
    }
    this.pending.delete(sequence);
    this.lastSequence = sequence;
    this.notify(data);
    while (this.pending.has(this.lastSequence + 1)) {
      const next = this.pending.get(this.lastSequence + 1)!;
      this.pending.delete(this.lastSequence + 1);
      this.lastSequence += 1;
      this.notify(next);
    }
    if (this.pending.size === 0 && this.catchUpGeneration !== this.generation && this.ws?.readyState === WebSocket.OPEN) {
      this.setStatus("live");
    }
  }

  private notify(data: FeedMessage) {
    this.listeners.forEach((fn) => fn(data));
  }

  private scheduleReconnect() {
    if (this.retryCount >= this.maxRetries) {
      this.terminal = true;
      this.setStatus("offline");
      return;
    }
    this.setStatus("reconnecting");
    const delay = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** this.retryCount);
    const jitter = delay * this.jitterRatio * Math.random();
    this.retryCount += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay + jitter);
  }

  private async catchUp() {
    if (!this.tenantId || this.lastSequence <= 0 || this.terminal) return;
    const generation = this.generation;
    if (this.catchUpGeneration === generation) return;
    this.catchUpGeneration = generation;
    try {
      for (let page = 0; page < 100 && this.generation === generation; page += 1) {
        const before = this.lastSequence;
        const params = new URLSearchParams({
          after: String(before),
          limit: String(this.catchUpLimit),
        });
        const response = await this.fetchImpl(`/api/v1/feed/events?${params.toString()}`, {
          credentials: "include",
        });
        if (this.generation !== generation) return;
        if (response.status === 401 || response.status === 403) {
          this.terminal = true;
          this.setStatus("sign_in_required");
          this.ws?.close();
          return;
        }
        if (!response.ok) {
          this.scheduleCatchUp();
          return;
        }
        const body = (await response.json()) as { items?: FeedMessage[] };
        if (this.generation !== generation) return;
        const items = body.items ?? [];
        items.forEach((item) => this.accept(item));
        if (items.length < this.catchUpLimit || this.lastSequence === before) {
          if (this.pending.size > 0) this.scheduleCatchUp();
          else if (this.ws?.readyState === WebSocket.OPEN) {
            this.catchUpRetryCount = 0;
            this.setStatus("live");
          }
          return;
        }
        if (page === 99) window.setTimeout(() => void this.catchUp(), 0);
      }
    } catch {
      this.scheduleCatchUp();
      return;
    } finally {
      if (this.catchUpGeneration === generation) this.catchUpGeneration = -1;
    }
  }

  private scheduleCatchUp() {
    if (this.terminal || this.intentionalClose || this.catchUpTimer !== null) return;
    this.setStatus("delayed");
    const delay = Math.min(30000, 5000 * 2 ** Math.min(this.catchUpRetryCount, 3));
    this.catchUpRetryCount += 1;
    this.catchUpTimer = window.setTimeout(() => {
      this.catchUpTimer = null;
      void this.catchUp();
    }, delay);
  }

  private clearCatchUpTimer() {
    if (this.catchUpTimer !== null) {
      window.clearTimeout(this.catchUpTimer);
      this.catchUpTimer = null;
    }
  }

  private setStatus(status: FeedConnectionStatus) {
    if (this.status === status) return;
    this.status = status;
    this.statusListeners.forEach((fn) => fn(status));
  }

  private clearReconnectTimer() {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private isSocketActive() {
    return this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING;
  }
}
