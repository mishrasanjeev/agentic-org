import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../contexts/AuthContext";
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
/*  Simulated event pools by domain                                    */
/* ------------------------------------------------------------------ */

const CFO_EVENTS: Omit<AgentEvent, "id" | "timestamp">[] = [
  { agent: "AP Processor", avatar: "\uD83E\uDDFE", eventType: "thinking", message: "Analyzing invoice INV-2024-0891 from Tata Steel...", domain: "finance" },
  { agent: "AP Processor", avatar: "\uD83E\uDDFE", eventType: "tool_call", message: "oracle_fusion.read_purchase_order(po_id=\"PO-7842\")", domain: "finance" },
  { agent: "AP Processor", avatar: "\uD83E\uDDFE", eventType: "tool_call", message: "gstn.validate_gstin(gstin=\"29ABCDE1234F1Z5\")", domain: "finance" },
  { agent: "AP Processor", avatar: "\uD83E\uDDFE", eventType: "result", message: "GSTIN validated \u2713 | 3-way match: \u2713 | Confidence: 96.2%", domain: "finance" },
  { agent: "AP Processor", avatar: "\uD83E\uDDFE", eventType: "tool_call", message: "banking_aa.queue_payment(amount=\u20B94,82,000, schedule=\"day_9\")", domain: "finance" },
  { agent: "Recon Agent", avatar: "\uD83D\uDD0D", eventType: "thinking", message: "Processing batch 47/50 \u2014 matching HDFC transactions...", domain: "finance" },
  { agent: "Recon Agent", avatar: "\uD83D\uDD0D", eventType: "result", message: "23/23 transactions matched | Auto-posted to GL", domain: "finance" },
  { agent: "Tax Compliance", avatar: "\uD83D\uDCCA", eventType: "thinking", message: "Computing IGST for inter-state invoice...", domain: "finance" },
  { agent: "Tax Compliance", avatar: "\uD83D\uDCCA", eventType: "result", message: "IGST \u20B986,760 computed | Added to GSTR-1 draft", domain: "finance" },
  { agent: "AP Processor", avatar: "\uD83E\uDDFE", eventType: "hitl_trigger", message: "\u26A0\uFE0F Invoice INV-2024-0892 total \u20B97,82,000 exceeds \u20B95L threshold", domain: "finance" },
  { agent: "Treasury Agent", avatar: "\uD83C\uDFE6", eventType: "thinking", message: "Forecasting weekly cash position from HDFC & ICICI pools...", domain: "finance" },
  { agent: "Treasury Agent", avatar: "\uD83C\uDFE6", eventType: "result", message: "Projected surplus \u20B912.4Cr | Sweep recommendation generated", domain: "finance" },
  { agent: "AR Collector", avatar: "\uD83D\uDCE8", eventType: "tool_call", message: "email_service.send_reminder(customer=\"Reliance Retail\", days_overdue=15)", domain: "finance" },
  { agent: "AR Collector", avatar: "\uD83D\uDCE8", eventType: "result", message: "Payment reminder sent | Outstanding: \u20B923,50,000", domain: "finance" },
];

const CHRO_EVENTS: Omit<AgentEvent, "id" | "timestamp">[] = [
  { agent: "Onboarding Bot", avatar: "\uD83D\uDC64", eventType: "thinking", message: "Setting up Day-1 checklist for Priya Sharma (Engineering)...", domain: "hr" },
  { agent: "Onboarding Bot", avatar: "\uD83D\uDC64", eventType: "tool_call", message: "workday.create_employee_profile(emp_id=\"EMP-4521\")", domain: "hr" },
  { agent: "Onboarding Bot", avatar: "\uD83D\uDC64", eventType: "tool_call", message: "gsuite.provision_account(email=\"priya.sharma@edumatica.in\")", domain: "hr" },
  { agent: "Onboarding Bot", avatar: "\uD83D\uDC64", eventType: "result", message: "Profile created \u2713 | IT assets provisioned \u2713 | Slack invite sent", domain: "hr" },
  { agent: "Leave Manager", avatar: "\uD83D\uDCC5", eventType: "thinking", message: "Processing leave request #LR-892 from Amit Verma...", domain: "hr" },
  { agent: "Leave Manager", avatar: "\uD83D\uDCC5", eventType: "result", message: "Auto-approved: 2 days CL | Balance: 8 days remaining", domain: "hr" },
  { agent: "Payroll Agent", avatar: "\uD83D\uDCB0", eventType: "thinking", message: "Computing March payroll for 342 employees...", domain: "hr" },
  { agent: "Payroll Agent", avatar: "\uD83D\uDCB0", eventType: "tool_call", message: "epfo.compute_pf_contribution(month=\"2026-03\")", domain: "hr" },
  { agent: "Payroll Agent", avatar: "\uD83D\uDCB0", eventType: "result", message: "Gross: \u20B92.1Cr | PF: \u20B918.4L | TDS: \u20B912.8L | Net: \u20B91.78Cr", domain: "hr" },
  { agent: "Talent Screener", avatar: "\uD83C\uDFAF", eventType: "thinking", message: "Ranking 47 resumes for Senior ML Engineer role...", domain: "hr" },
  { agent: "Talent Screener", avatar: "\uD83C\uDFAF", eventType: "result", message: "Top 5 shortlisted | Avg match score: 87.3%", domain: "hr" },
  { agent: "Payroll Agent", avatar: "\uD83D\uDCB0", eventType: "hitl_trigger", message: "\u26A0\uFE0F Salary revision for VP-level employee requires CFO approval", domain: "hr" },
];

const CMO_EVENTS: Omit<AgentEvent, "id" | "timestamp">[] = [
  { agent: "Campaign Agent", avatar: "\uD83D\uDCE2", eventType: "thinking", message: "Analyzing Q1 campaign performance across Google & Meta...", domain: "marketing" },
  { agent: "Campaign Agent", avatar: "\uD83D\uDCE2", eventType: "tool_call", message: "google_ads.fetch_metrics(campaign=\"Spring_Launch_2026\")", domain: "marketing" },
  { agent: "Campaign Agent", avatar: "\uD83D\uDCE2", eventType: "result", message: "CTR: 3.2% | CPC: \u20B918.40 | ROAS: 4.7x | Budget utilization: 78%", domain: "marketing" },
  { agent: "Content Writer", avatar: "\u270D\uFE0F", eventType: "thinking", message: "Generating product description for Enterprise AI Suite...", domain: "marketing" },
  { agent: "Content Writer", avatar: "\u270D\uFE0F", eventType: "result", message: "Draft generated | Readability: Grade 8 | SEO score: 91/100", domain: "marketing" },
  { agent: "SEO Analyzer", avatar: "\uD83D\uDD0E", eventType: "tool_call", message: "semrush.keyword_analysis(domain=\"edumatica.in\")", domain: "marketing" },
  { agent: "SEO Analyzer", avatar: "\uD83D\uDD0E", eventType: "result", message: "12 keywords ranking top-10 | 3 new opportunities found", domain: "marketing" },
  { agent: "Social Monitor", avatar: "\uD83D\uDCF1", eventType: "thinking", message: "Scanning brand mentions across Twitter & LinkedIn...", domain: "marketing" },
  { agent: "Social Monitor", avatar: "\uD83D\uDCF1", eventType: "result", message: "47 mentions today | Sentiment: 82% positive | 2 escalations", domain: "marketing" },
  { agent: "Social Monitor", avatar: "\uD83D\uDCF1", eventType: "hitl_trigger", message: "\u26A0\uFE0F Negative viral post detected \u2014 requires PR team response", domain: "marketing" },
];

const COO_EVENTS: Omit<AgentEvent, "id" | "timestamp">[] = [
  { agent: "Inventory Agent", avatar: "\uD83D\uDCE6", eventType: "thinking", message: "Checking stock levels at Pune warehouse...", domain: "ops" },
  { agent: "Inventory Agent", avatar: "\uD83D\uDCE6", eventType: "tool_call", message: "sap_wms.get_stock_levels(warehouse=\"PUNE-01\")", domain: "ops" },
  { agent: "Inventory Agent", avatar: "\uD83D\uDCE6", eventType: "result", message: "SKU-A412: 2,340 units | Reorder point: 500 | Status: OK", domain: "ops" },
  { agent: "Logistics Bot", avatar: "\uD83D\uDE9A", eventType: "thinking", message: "Optimizing delivery routes for Mumbai zone...", domain: "ops" },
  { agent: "Logistics Bot", avatar: "\uD83D\uDE9A", eventType: "tool_call", message: "google_maps.optimize_routes(zone=\"MUM\", orders=34)", domain: "ops" },
  { agent: "Logistics Bot", avatar: "\uD83D\uDE9A", eventType: "result", message: "34 deliveries optimized | Est. savings: 23% fuel | ETA: 4.2hrs avg", domain: "ops" },
  { agent: "QA Inspector", avatar: "\u2705", eventType: "thinking", message: "Running quality checks on production batch #B-2026-0891...", domain: "ops" },
  { agent: "QA Inspector", avatar: "\u2705", eventType: "result", message: "Batch passed | 0 defects in 50 samples | Confidence: 99.1%", domain: "ops" },
  { agent: "Vendor Manager", avatar: "\uD83E\uDD1D", eventType: "tool_call", message: "procurement.evaluate_vendor(vendor_id=\"V-1042\")", domain: "ops" },
  { agent: "Vendor Manager", avatar: "\uD83E\uDD1D", eventType: "result", message: "Vendor score: 4.6/5 | On-time delivery: 97% | Renewed for FY27", domain: "ops" },
  { agent: "Inventory Agent", avatar: "\uD83D\uDCE6", eventType: "hitl_trigger", message: "\u26A0\uFE0F SKU-C891 below safety stock \u2014 urgent PO needed for \u20B94.2L", domain: "ops" },
];

const DOMAIN_EVENTS: Record<string, Omit<AgentEvent, "id" | "timestamp">[]> = {
  finance: CFO_EVENTS,
  hr: CHRO_EVENTS,
  marketing: CMO_EVENTS,
  ops: COO_EVENTS,
};

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

  // Build the event pool for this user's domains
  const eventPool = domains.flatMap((d) => DOMAIN_EVENTS[d] || []);

  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [, setEventIdx] = useState(0);
  const [txCount, setTxCount] = useState(2847);
  const [hitlCount, setHitlCount] = useState(4);
  const [throughputData, setThroughputData] = useState<{ t: number; v: number }[]>(
    () => Array.from({ length: 20 }, (_, i) => ({ t: i, v: 40 + Math.floor(Math.random() * 30) }))
  );
  const [workflowStepIdx, setWorkflowStepIdx] = useState(2); // start mid-pipeline
  const feedRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  // Pick the primary domain for the workflow display
  const primaryDomain = domains[0];
  const workflow = DOMAIN_WORKFLOWS[primaryDomain] || DOMAIN_WORKFLOWS.finance;

  const workflowSteps: WorkflowStep[] = workflow.steps.map((label, i) => ({
    label,
    status: i < workflowStepIdx ? "completed" : i === workflowStepIdx ? "running" : "pending",
  }));

  // Active agent count from recent events
  const activeAgentCount = new Set(events.slice(0, 15).map((e) => e.agent)).size || domains.length;

  // Memoised add-event to avoid closure issues
  const addEvent = useCallback(() => {
    setEventIdx((prev) => {
      const idx = prev % eventPool.length;
      const template = eventPool[idx];
      const now = new Date();
      const ts = now.toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });

      const newEvent: AgentEvent = {
        ...template,
        id: nextId.current++,
        timestamp: ts,
      };

      setEvents((old) => [newEvent, ...old].slice(0, 80));

      // Increment counters
      if (template.eventType === "result") {
        setTxCount((c) => c + Math.floor(Math.random() * 3) + 1);
      }
      if (template.eventType === "hitl_trigger") {
        setHitlCount((c) => c + 1);
      }

      // Update throughput sparkline
      setThroughputData((old) => {
        const next = [...old.slice(1), { t: old[old.length - 1].t + 1, v: 40 + Math.floor(Math.random() * 35) }];
        return next;
      });

      return prev + 1;
    });
  }, [eventPool]);

  // Advance the workflow step periodically
  useEffect(() => {
    const stepTimer = setInterval(() => {
      setWorkflowStepIdx((prev) => (prev + 1) % workflow.steps.length);
    }, 8000);
    return () => clearInterval(stepTimer);
  }, [workflow.steps.length]);

  // Main event ticker
  useEffect(() => {
    const interval = setInterval(addEvent, 2200);
    return () => clearInterval(interval);
  }, [addEvent]);

  // Auto-scroll feed
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = 0;
    }
  }, [events.length]);

  const avgConfidence = 93.2;
  const autoMatchRate = 99.7;

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
          <span className="text-white font-semibold">{activeAgentCount}</span> agents active
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
              <p className="text-slate-600 text-xs mt-8 text-center">Waiting for agent events...</p>
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
        <StatCounter label="Transactions Today" value={txCount.toLocaleString("en-IN")} color="text-emerald-400" />
        <Divider />
        <StatCounter label="Auto-match Rate" value={`${autoMatchRate}%`} color="text-blue-400" />
        <Divider />
        <StatCounter label="Avg Confidence" value={`${avgConfidence}%`} color="text-violet-400" />
        <Divider />
        <StatCounter label="HITL Escalations" value={String(hitlCount)} color="text-red-400" />
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
