import { useEffect, useState } from "react";
import { Activity, CircleAlert } from "lucide-react";
import { AgenticOrgWS, type FeedConnectionStatus, type FeedMessage } from "@/lib/websocket";

interface Props { tenantId: string; maxItems?: number; }

const STATUS_LABELS: Record<FeedConnectionStatus, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
  delayed: "Updates delayed",
  sign_in_required: "Session ended",
  offline: "Offline",
};

function eventLabel(event: FeedMessage): string {
  const raw = typeof event.type === "string" ? event.type : "activity";
  const label = raw.replace(/[^a-zA-Z0-9._-]/g, "").replace(/[._-]+/g, " ").trim();
  return (label || "Activity").slice(0, 48);
}

function eventTime(event: FeedMessage): string | null {
  if (typeof event.created_at !== "string") return null;
  const timestamp = new Date(event.created_at);
  return Number.isNaN(timestamp.getTime()) ? null : timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function LiveFeed({ tenantId, maxItems = 7 }: Props) {
  const [events, setEvents] = useState<FeedMessage[]>([]);
  const [status, setStatus] = useState<FeedConnectionStatus>("connecting");

  useEffect(() => {
    const ws = new AgenticOrgWS();
    setEvents([]);
    const unsubscribe = ws.subscribe((data) => {
      if (data.type !== "heartbeat") {
        setEvents((prev) => [data, ...prev].slice(0, maxItems));
      }
    });
    const unsubscribeStatus = ws.subscribeStatus(setStatus);
    ws.connect(tenantId);
    return () => {
      unsubscribe();
      unsubscribeStatus();
      ws.disconnect();
    };
  }, [tenantId, maxItems]);

  return (
    <section aria-label="Live activity" className="space-y-3 min-w-0">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold flex items-center gap-2"><Activity size={16} aria-hidden="true" />Live activity</h3>
        <span role="status" className="text-xs text-muted-foreground whitespace-nowrap">{STATUS_LABELS[status]}</span>
      </div>
      {status === "sign_in_required" && (
        <p className="text-xs text-amber-700 flex items-center gap-2"><CircleAlert size={14} aria-hidden="true" />Session ended. <a href="/login" className="underline underline-offset-2">Sign in again</a></p>
      )}
      {status === "delayed" && <p className="text-xs text-amber-700">Some events are missing. Catch-up is retrying.</p>}
      {events.length === 0 ? (
        <p className="text-sm text-muted-foreground">No live events yet.</p>
      ) : (
        <ol className="space-y-1 max-h-64 overflow-y-auto" aria-label="Recent live events">
          {events.map((event, index) => (
            <li key={typeof event.sequence === "number" ? `${tenantId}-${event.sequence}` : String(event.id ?? index)} className="flex items-center justify-between gap-3 border-b border-border py-2 text-sm">
              <span className="min-w-0 truncate">{eventLabel(event)}</span>
              {eventTime(event) && <time className="text-xs text-muted-foreground whitespace-nowrap" dateTime={String(event.created_at)}>{eventTime(event)}</time>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
