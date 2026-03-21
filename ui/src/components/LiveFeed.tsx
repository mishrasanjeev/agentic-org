import { useEffect, useState } from "react";
import { AgenticOrgWS } from "@/lib/websocket";

interface Props { tenantId: string; maxItems?: number; }

export default function LiveFeed({ tenantId, maxItems = 7 }: Props) {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    const ws = new AgenticOrgWS();
    ws.connect(tenantId);
    ws.subscribe((data) => setEvents((prev) => [data, ...prev].slice(0, maxItems)));
    return () => ws.disconnect();
  }, [tenantId, maxItems]);

  return (
    <div className="space-y-2">
      <h3 className="font-semibold">Live Activity</h3>
      {events.length === 0 ? <p className="text-sm text-muted-foreground">Waiting for events...</p> :
        events.map((e, i) => (
          <div key={i} className="text-sm p-2 rounded bg-muted">{e.type}: {JSON.stringify(e).slice(0, 80)}...</div>
        ))
      }
    </div>
  );
}
