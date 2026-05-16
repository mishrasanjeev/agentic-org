export interface FeedMessage {
  type?: string;
  sequence?: number | null;
  [key: string]: unknown;
}

type Listener = (data: FeedMessage) => void;

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
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private tenantId: string | null = null;
  private intentionalClose = false;
  private terminal = false;
  private retryCount = 0;
  private lastSequence = 0;
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
    if (this.ws && this.tenantId !== tenantId) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.tenantId = tenantId;
    this.intentionalClose = false;
    this.terminal = false;
    this.openSocket();
  }

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  disconnect() {
    this.intentionalClose = true;
    this.terminal = true;
    this.clearReconnectTimer();
    if (this.ws) {
      this.ws.onclose = null;
    }
    this.ws?.close();
    this.ws = null;
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
      void this.catchUp();
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
    this.emit(data);
  }

  private emit(data: FeedMessage) {
    const sequence = typeof data.sequence === "number" ? data.sequence : null;
    if (sequence !== null) {
      if (sequence <= this.lastSequence) {
        return;
      }
      this.lastSequence = sequence;
    }
    this.listeners.forEach((fn) => fn(data));
  }

  private scheduleReconnect() {
    if (this.retryCount >= this.maxRetries) {
      this.terminal = true;
      return;
    }
    const delay = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** this.retryCount);
    const jitter = delay * this.jitterRatio * Math.random();
    this.retryCount += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay + jitter);
  }

  private async catchUp() {
    if (!this.tenantId || this.lastSequence <= 0) {
      return;
    }
    const params = new URLSearchParams({
      after: String(this.lastSequence),
      limit: String(this.catchUpLimit),
    });
    let response: Response;
    try {
      response = await this.fetchImpl(`/api/v1/feed/events?${params.toString()}`, {
        credentials: "include",
      });
    } catch {
      return;
    }
    if (response.status === 401 || response.status === 403) {
      this.terminal = true;
      this.ws?.close();
      return;
    }
    if (!response.ok) {
      return;
    }
    const body = (await response.json()) as { items?: FeedMessage[] };
    (body.items ?? []).forEach((item) => this.emit(item));
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
