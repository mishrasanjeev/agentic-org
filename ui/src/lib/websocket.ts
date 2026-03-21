export class AgenticOrgWS {
  private ws: WebSocket | null = null;
  private listeners: ((data: any) => void)[] = [];

  connect(tenantId: string) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws/feed/${tenantId}`);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.listeners.forEach((fn) => fn(data));
    };
    this.ws.onclose = () => setTimeout(() => this.connect(tenantId), 3000);
  }

  subscribe(fn: (data: any) => void) { this.listeners.push(fn); }
  disconnect() { this.ws?.close(); }
}
