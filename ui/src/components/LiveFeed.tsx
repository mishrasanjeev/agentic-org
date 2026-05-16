import { useEffect, useState } from "react";
import { AgenticOrgWS, type FeedMessage } from "@/lib/websocket";

interface Props { tenantId: string; maxItems?: number; }

export default function LiveFeed({ tenantId, maxItems = 7 }: Props) {
  const [events, setEvents] = useState<FeedMessage[]>([]);
  useEffect(() => {
    const ws = new AgenticOrgWS();
    ws.connect(tenantId);
    const unsubscribe = ws.subscribe((data) => {
      if (data.type === "heartbeat") {
        return;
      }
      setEvents((prev) => [data, ...prev].slice(0, maxItems));
    });
    return () => {
      unsubscribe();
      ws.disconnect();
    };
  }, [tenantId, maxItems]);

  return (
    <div className="space-y-2">
      <h3 className="font-semibold">Live Activity</h3>
      {events.length === 0 ? <p className="text-sm text-muted-foreground">Waiting for events...</p> :
        events.map((e, i) => (
          <div
            key={typeof e.sequence === "number" ? `${tenantId}-${e.sequence}` : String(e.id ?? e.created_at ?? i)}
            className="text-sm p-2 rounded bg-muted"
          >
            {String(e.type ?? "event")}: {JSON.stringify(e.payload ?? e).slice(0, 80)}...
          </div>
        ))
      }
    </div>
  );
}
