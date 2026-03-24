import { useState, useEffect, useRef } from "react";

const ACTIVITIES = [
  { agent: "Priya", type: "AP Processor", domain: "finance", avatar: "P", color: "bg-emerald-500", action: "Processed invoice INV-2024-4521", detail: "3-way match passed, payment scheduled", confidence: 0.94, status: "completed" as const },
  { agent: "Arjun", type: "Recon Agent", domain: "finance", avatar: "A", color: "bg-blue-500", action: "Reconciling 847 bank transactions", detail: "99.7% auto-matched, 3 breaks flagged", confidence: 0.97, status: "running" as const },
  { agent: "Maya", type: "Onboarding", domain: "hr", avatar: "M", color: "bg-purple-500", action: "Onboarded Rahul Kumar (SDE-II)", detail: "IT provisioning, Darwinbox, Slack invite sent", confidence: 0.96, status: "completed" as const },
  { agent: "CS Bot", type: "Customer Success", domain: "ops", avatar: "C", color: "bg-orange-500", action: "Analyzing churn risk for Acme Corp", detail: "NPS drop detected, retention playbook triggered", confidence: 0.89, status: "running" as const },
  { agent: "Riya", type: "Tax Compliance", domain: "finance", avatar: "R", color: "bg-teal-500", action: "Filed GSTR-3B for March 2026", detail: "GSTN validated, ITC reconciled", confidence: 0.98, status: "completed" as const },
  { agent: "Dev", type: "Support Triage", domain: "ops", avatar: "D", color: "bg-red-500", action: "Auto-classified 42 tickets", detail: "P1: 2, P2: 8, P3: 32 — zero mis-routes", confidence: 0.91, status: "completed" as const },
  { agent: "Neha", type: "Campaign Pilot", domain: "marketing", avatar: "N", color: "bg-pink-500", action: "Launched Q1 email campaign", detail: "3 segments, 12,400 contacts, A/B test active", confidence: 0.93, status: "running" as const },
  { agent: "Vikram", type: "Payroll Engine", domain: "hr", avatar: "V", color: "bg-indigo-500", action: "Processing March payroll for 847 employees", detail: "PF/ESI/TDS computed, zero errors", confidence: 0.99, status: "completed" as const },
  { agent: "Priya", type: "AP Processor", domain: "finance", avatar: "P", color: "bg-emerald-500", action: "HITL escalated: Invoice INV-2024-4899", detail: "Amount exceeds threshold, awaiting CFO approval", confidence: 0.82, status: "hitl" as const },
  { agent: "Sana", type: "Brand Monitor", domain: "marketing", avatar: "S", color: "bg-violet-500", action: "Sentiment alert: negative spike on Twitter", detail: "Product complaint trending, PR team notified", confidence: 0.87, status: "running" as const },
];

const STATUS_STYLES = {
  completed: { dot: "bg-emerald-400", label: "Completed", text: "text-emerald-400" },
  running: { dot: "bg-blue-400 animate-pulse", label: "Running", text: "text-blue-400" },
  hitl: { dot: "bg-amber-400 animate-pulse", label: "HITL Review", text: "text-amber-400" },
};

export default function AgentActivityTicker() {
  const [visibleItems, setVisibleItems] = useState<number[]>([0, 1, 2, 3, 4]);
  const [fadeState, setFadeState] = useState<"in" | "shift">("in");
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setFadeState("shift");
      setTimeout(() => {
        setVisibleItems((prev) => {
          const next = (prev[prev.length - 1] + 1) % ACTIVITIES.length;
          return [...prev.slice(1), next];
        });
        setFadeState("in");
      }, 400);
    }, 3000);

    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, []);

  return (
    <div className="relative overflow-hidden rounded-2xl bg-slate-800/80 border border-slate-700 backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/50">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-sm font-medium text-slate-300">Live Agent Activity</span>
        </div>
        <span className="text-xs text-slate-500">{ACTIVITIES.length} virtual employees active</span>
      </div>

      {/* Activity Feed */}
      <div className="divide-y divide-slate-700/30">
        {visibleItems.map((idx, i) => {
          const a = ACTIVITIES[idx];
          const s = STATUS_STYLES[a.status];
          const isNew = i === visibleItems.length - 1 && fadeState === "in";
          const isLeaving = i === 0 && fadeState === "shift";

          return (
            <div
              key={`${idx}-${i}`}
              className={`flex items-start gap-3 px-4 py-3 transition-all duration-400 ${
                isNew ? "animate-slideIn" : isLeaving ? "opacity-0 -translate-y-2" : "opacity-100"
              }`}
            >
              {/* Avatar */}
              <div className={`w-8 h-8 rounded-full ${a.color} flex items-center justify-center text-white text-xs font-bold flex-shrink-0 mt-0.5`}>
                {a.avatar}
              </div>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium text-white">{a.agent}</span>
                  <span className="text-xs text-slate-500">{a.type}</span>
                  <div className="flex items-center gap-1 ml-auto">
                    <div className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                    <span className={`text-xs ${s.text}`}>{s.label}</span>
                  </div>
                </div>
                <p className="text-sm text-slate-300 truncate">{a.action}</p>
                <p className="text-xs text-slate-500 truncate">{a.detail}</p>
              </div>

              {/* Confidence */}
              <div className="text-right flex-shrink-0">
                <div className={`text-sm font-mono font-medium ${a.confidence >= 0.90 ? "text-emerald-400" : a.confidence >= 0.85 ? "text-amber-400" : "text-red-400"}`}>
                  {(a.confidence * 100).toFixed(0)}%
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Bottom bar */}
      <div className="px-4 py-2 border-t border-slate-700/50 flex items-center justify-between">
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> 6 completed</span>
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-blue-400" /> 3 running</span>
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-amber-400" /> 1 HITL</span>
        </div>
        <span className="text-xs text-slate-600">Auto-refreshing</span>
      </div>

      <style>{`
        @keyframes slideIn {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-slideIn { animation: slideIn 0.4s ease-out; }
      `}</style>
    </div>
  );
}
