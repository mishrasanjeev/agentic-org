import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../contexts/AuthContext";
import api from "@/lib/api";
import {
  LineChart,
  Line,
  ResponsiveContainer,
  YAxis,
} from "recharts";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type EventType = "thinking" | "tool_call" | "result" | "hitl_trigger";

interface AgentEvent {
  id: number;
  timestamp: string;
  agent: string;
  avatar: string;
  eventType: EventType;
  message: string;
  domain: string;
}

interface WorkflowStep {
  label: string;
  status: "completed" | "running" | "pending";
}

/* ------------------------------------------------------------------ */
/*  No simulated event pools — events come from the API                */
/* ------------------------------------------------------------------ */

const ROLE_TO_DOMAIN: Record<string, string[]> = {
  cfo: ["finance"],
  chro: ["hr"],
  cmo: ["marketing"],
  coo: ["ops"],
  admin: ["finance", "hr", "marketing", "ops"],
  auditor: ["finance", "hr", "marketing", "ops"],
};

/* ------------------------------------------------------------------ */
/*  Workflow steps per domain                                          */
/* ------------------------------------------------------------------ */

const DOMAIN_WORKFLOWS: Record<string, { name: string; steps: string[] }> = {
  finance: {
    name: "Invoice Processing Pipeline",
    steps: ["Receive Invoice", "Extract Fields", "GSTIN Validation", "3-Way Match", "Payment Queue"],
  },
  hr: {
    name: "Employee Onboarding Flow",
    steps: ["Offer Accepted", "Profile Setup", "IT Provisioning", "Team Assignment", "Day-1 Checklist"],
  },
  marketing: {
    name: "Campaign Launch Pipeline",
    steps: ["Brief Review", "Creative Gen", "A/B Setup", "Launch", "Performance Track"],
  },
  ops: {
    name: "Order Fulfillment Pipeline",
    steps: ["Order Received", "Stock Check", "Pick & Pack", "Route Optimize", "Dispatch"],
  },
};

/* ------------------------------------------------------------------ */
/*  Event type styling                                                 */
/* ------------------------------------------------------------------ */

const EVENT_STYLES: Record<EventType, { badge: string; border: string; bg: string; label: string }> = {
  thinking: {
    badge: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    border: "border-l-amber-500",
    bg: "bg-slate-800/50",
    label: "THINKING",
  },
  tool_call: {
    badge: "bg-blue-500/20 text-blue-400 border-blue-500/30",
    border: "border-l-blue-500",
    bg: "bg-slate-800/50",
    label: "TOOL CALL",
  },
  result: {
    badge: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
    border: "border-l-emerald-500",
    bg: "bg-slate-800/50",
    label: "RESULT",
  },
  hitl_trigger: {
    badge: "bg-red-500/20 text-red-400 border-red-500/30",
    border: "border-l-red-500",
    bg: "bg-red-950/30",
    label: "HITL",
  },
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Observatory() {
  const { user } = useAuth();
  const role = user?.role || "admin";
  const domains = ROLE_TO_DOMAIN[role] || ["finance", "hr", "marketing", "ops"];

  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [txCount, setTxCount] = useState(0);
  const [hitlCount, setHitlCount] = useState(0);
  const [throughputData, setThroughputData] = useState<{ t: number; v: number }[]>(
    () => Array.from({ length: 20 }, (_, i) => ({ t: i, v: 0 }))
  );
  const [workflowStepIdx, setWorkflowStepIdx] = useState(0);
  const feedRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const lastFetchedId = useRef<string | null>(null);

  // Pick the primary domain for the workflow display
  const primaryDomain = domains[0];
  const workflow = DOMAIN_WORKFLOWS[primaryDomain] || DOMAIN_WORKFLOWS.finance;

  const workflowSteps: WorkflowStep[] = workflow.steps.map((label, i) => ({
    label,
    status: i < workflowStepIdx ? "completed" : i === workflowStepIdx ? "running" : "pending",
  }));

  // Active agent count from recent events
  const activeAgentCount = new Set(events.slice(0, 15).map((e) => e.agent)).size;

  // Map an audit entry from the API into an AgentEvent
  const mapAuditEntry = useCallback((entry: any): AgentEvent => {
    const eventType: EventType =
      entry.event_type === "hitl_trigger" ? "hitl_trigger"
        : entry.event_type === "tool_call" ? "tool_call"
        : entry.event_type === "thinking" ? "thinking"
        : "result";
    const ts = entry.timestamp
      ? new Date(entry.timestamp).toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : new Date().toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    return {
      id: nextId.current++,
      timestamp: ts,
      agent: entry.agent_name || entry.agent || "Agent",
      avatar: entry.avatar || "\u2699\uFE0F",
      eventType,
      message: entry.message || entry.detail || entry.action || "",
      domain: entry.domain || "ops",
    };
  }, []);

  // Poll API for real events
  const fetchEvents = useCallback(async () => {
    try {
      const { data } = await api.get("/audit", { params: { limit: 20 } });
      const raw: any[] = Array.isArray(data) ? data : data?.items || [];
      if (raw.length === 0) return;

      // Detect new entries since last fetch
      const newestId = raw[0]?.id || raw[0]?.timestamp;
      if (newestId === lastFetchedId.current) return;
      lastFetchedId.current = newestId;

      const mapped = raw.map(mapAuditEntry);
      setEvents((old) => {
        const merged = [...mapped, ...old];
        // Deduplicate by keeping unique messages (first occurrence)
        const seen = new Set<string>();
        return merged.filter((e) => {
          const key = `${e.agent}::${e.message}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        }).slice(0, 80);
      });

      // Update counters from fetched data
      const newResults = mapped.filter((e) => e.eventType === "result").length;
      const newHitl = mapped.filter((e) => e.eventType === "hitl_trigger").length;
      if (newResults > 0) setTxCount((c) => c + newResults);
      if (newHitl > 0) setHitlCount((c) => c + newHitl);

      // Update throughput sparkline with actual event count
      setThroughputData((old) => {
        const next = [...old.slice(1), { t: old[old.length - 1].t + 1, v: mapped.length }];
        return next;
      });
    } catch {
      // API not available — leave feed empty with "Waiting for agent activity..." message
    }
  }, [mapAuditEntry]);

  // Initial fetch + polling interval
  useEffect(() => {
    fetchEvents();
    const interval = setInterval(fetchEvents, 5000);
    return () => clearInterval(interval);
  }, [fetchEvents]);

  // Advance the workflow step periodically (only when we have events)
  useEffect(() => {
    if (events.length === 0) return;
    const stepTimer = setInterval(() => {
      setWorkflowStepIdx((prev) => (prev + 1) % workflow.steps.length);
    }, 8000);
    return () => clearInterval(stepTimer);
  }, [workflow.steps.length, events.length]);

  // Auto-scroll feed
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = 0;
    }
  }, [events.length]);

  return (
    <div className="observatory -m-6 min-h-full bg-slate-900 text-white">
      {/* --- Inline styles for pulsing animation --- */}
      <style>{`
        @keyframes obs-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        .obs-pulse {
          animation: obs-pulse 1.5s ease-in-out infinite;
        }
        @keyframes obs-think {
          0% { opacity: 0.3; }
          50% { opacity: 1; }
          100% { opacity: 0.3; }
        }
        .obs-thinking {
          animation: obs-think 1.2s ease-in-out infinite;
        }
        .obs-feed-item {
          animation: obs-fadein 0.3s ease-out;
        }
        @keyframes obs-fadein {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      {/* ============ TOP BAR ============ */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight">Agent Observatory</h1>
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-xs font-semibold text-emerald-400">
            <span className="obs-pulse inline-block w-2 h-2 rounded-full bg-emerald-400" />
            LIVE
          </span>
        </div>
        <span className="text-sm text-slate-400">
          {activeAgentCount > 0
            ? <><span className="text-white font-semibold">{activeAgentCount}</span> agents active</>
            : "No agents active"
          }
        </span>
      </div>

      {/* ============ MAIN PANELS ============ */}
      <div className="flex gap-0 h-[calc(100vh-10.5rem)]">

        {/* --- LEFT 60%: Active Workflow --- */}
        <div className="w-[60%] border-r border-slate-700 p-6 flex flex-col">
          <div className="mb-6">
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Active Workflow</p>
            <h2 className="text-lg font-semibold">{workflow.name}</h2>
          </div>

          {/* Step Timeline */}
          <div className="flex items-start gap-0 mb-8 overflow-x-auto pb-2">
            {workflowSteps.map((step, i) => (
              <div key={step.label} className="flex items-center">
                <div className="flex flex-col items-center min-w-[110px]">
                  <div
                    className={`
                      w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold border-2
                      ${step.status === "completed"
                        ? "bg-emerald-500/20 border-emerald-500 text-emerald-400"
                        : step.status === "running"
                          ? "bg-amber-500/20 border-amber-500 text-amber-400 obs-thinking"
                          : "bg-slate-800 border-slate-600 text-slate-500"}
                    `}
                  >
                    {step.status === "completed" ? "\u2713" : step.status === "running" ? "\u25CF" : i + 1}
                  </div>
                  <span className={`mt-2 text-xs text-center leading-tight ${
                    step.status === "completed"
                      ? "text-emerald-400"
                      : step.status === "running"
                        ? "text-amber-400 font-semibold"
                        : "text-slate-500"
                  }`}>
                    {step.label}
                  </span>
                  {step.status === "running" && (
                    <span className="mt-1 text-[10px] text-amber-400/70 obs-thinking">processing...</span>
                  )}
                </div>
                {i < workflowSteps.length - 1 && (
                  <div className={`w-8 h-0.5 mt-1 ${
                    step.status === "completed" ? "bg-emerald-500" : "bg-slate-700"
                  }`} />
                )}
              </div>
            ))}
          </div>

          {/* Throughput Sparkline */}
          <div className="mt-auto">
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-2">Throughput (events/min)</p>
            <div className="bg-slate-800/60 rounded-lg p-3 border border-slate-700">
              <ResponsiveContainer width="100%" height={100}>
                <LineChart data={throughputData}>
                  <YAxis domain={[20, 80]} hide />
                  <Line
                    type="monotone"
                    dataKey="v"
                    stroke="#10b981"
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* --- RIGHT 40%: Live Agent Feed --- */}
        <div className="w-[40%] flex flex-col">
          <div className="px-4 py-3 border-b border-slate-700">
            <p className="text-xs text-slate-500 uppercase tracking-wider">Live Agent Feed</p>
          </div>
          <div
            ref={feedRef}
            className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5"
            style={{ fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace" }}
          >
            {events.length === 0 && (
              <p className="text-slate-600 text-xs mt-8 text-center">Waiting for agent activity...</p>
            )}
            {events.map((evt) => {
              const style = EVENT_STYLES[evt.eventType];
              return (
                <div
                  key={evt.id}
                  className={`obs-feed-item rounded-md px-3 py-2 border-l-[3px] ${style.border} ${style.bg}`}
                >
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[10px] text-slate-500">{evt.timestamp}</span>
                    <span className="text-xs">{evt.avatar}</span>
                    <span className="text-xs font-semibold text-slate-300">{evt.agent}</span>
                    <span className={`ml-auto text-[9px] font-bold px-1.5 py-0.5 rounded border ${style.badge}`}>
                      {style.label}
                    </span>
                  </div>
                  <p className={`text-xs leading-relaxed ${
                    evt.eventType === "hitl_trigger" ? "text-red-300" : "text-slate-400"
                  }`}>
                    {evt.message}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ============ BOTTOM STATS BAR ============ */}
      <div className="border-t border-slate-700 px-6 py-3 flex items-center justify-around bg-slate-800/50">
        <StatCounter label="Transactions Today" value={txCount > 0 ? txCount.toLocaleString("en-IN") : "--"} color="text-emerald-400" />
        <Divider />
        <StatCounter label="Active Agents" value={activeAgentCount > 0 ? String(activeAgentCount) : "--"} color="text-blue-400" />
        <Divider />
        <StatCounter label="Events Received" value={events.length > 0 ? String(events.length) : "--"} color="text-violet-400" />
        <Divider />
        <StatCounter label="HITL Escalations" value={hitlCount > 0 ? String(hitlCount) : "--"} color="text-red-400" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Small sub-components                                               */
/* ------------------------------------------------------------------ */

function StatCounter({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="text-center">
      <p className={`text-lg font-bold tabular-nums ${color}`}>{value}</p>
      <p className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</p>
    </div>
  );
}

function Divider() {
  return <div className="w-px h-8 bg-slate-700" />;
}
