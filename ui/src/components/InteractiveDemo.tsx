import { useState, useEffect, useRef, useCallback } from "react";

/* ── Scenario definitions ── */

interface TraceLine {
  type: "system" | "agent" | "tool" | "result" | "hitl" | "confidence" | "separator";
  text: string;
  delay: number; // ms before this line appears
  color?: string;
}

interface Scenario {
  id: string;
  label: string;
  icon: string;
  agent: string;
  designation: string;
  avatar: string;
  avatarColor: string;
  domain: string;
  taskTitle: string;
  taskInput: string;
  lines: TraceLine[];
}

const SCENARIOS: Scenario[] = [
  {
    id: "invoice",
    label: "Invoice Processing",
    icon: "M9 14l6-6m-5.5.5h.01m4.99 5h.01M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16l3.5-2 3.5 2 3.5-2 3.5 2z",
    agent: "Priya",
    designation: "AP Processor — Mumbai",
    avatar: "P",
    avatarColor: "bg-emerald-500",
    domain: "finance",
    taskTitle: "Process Invoice INV-2024-4521",
    taskInput: '{ "vendor": "Tata Steel Ltd", "amount": "₹3,24,500", "po_ref": "PO-8847" }',
    lines: [
      { type: "system", text: "Task assigned to Priya (AP Processor — Mumbai)", delay: 0 },
      { type: "system", text: "Loading prompt template: ap_processor.prompt.txt", delay: 400 },
      { type: "separator", text: "", delay: 600 },
      { type: "agent", text: "STEP 1 — EXTRACT", delay: 800 },
      { type: "tool", text: "→ ocr_extract_invoice(s3_key: inv-2024-4521.pdf)", delay: 1000 },
      { type: "result", text: "  ✓ Extracted: vendor=Tata Steel, GSTIN=27AAACT2727Q1ZV, amount=₹3,24,500", delay: 1800 },
      { type: "separator", text: "", delay: 2000 },
      { type: "agent", text: "STEP 2 — VALIDATE", delay: 2200 },
      { type: "tool", text: "→ gstn_validate(gstin: 27AAACT2727Q1ZV)", delay: 2400 },
      { type: "result", text: "  ✓ GSTIN valid: Tata Steel Limited, Maharashtra", delay: 3200 },
      { type: "tool", text: "→ check_duplicate(inv_id: INV-2024-4521, vendor: tata_steel)", delay: 3400 },
      { type: "result", text: "  ✓ No duplicate found", delay: 4000 },
      { type: "separator", text: "", delay: 4200 },
      { type: "agent", text: "STEP 3 — 3-WAY MATCH", delay: 4400 },
      { type: "tool", text: "→ erp_get_po(po_ref: PO-8847)", delay: 4600 },
      { type: "result", text: "  ✓ PO amount: ₹3,25,000 | Invoice: ₹3,24,500 | Delta: ₹500 (0.15%)", delay: 5400 },
      { type: "result", text: "  ✓ Within 2% tolerance — MATCHED", delay: 5800, color: "text-emerald-400" },
      { type: "separator", text: "", delay: 6000 },
      { type: "agent", text: "STEP 4 — SCHEDULE PAYMENT", delay: 6200 },
      { type: "tool", text: "→ erp_queue_payment(date: 2026-04-02, discount: 2% early-pay)", delay: 6400 },
      { type: "result", text: "  ✓ Payment scheduled for Apr 2 — saving ₹6,490 in early-pay discount", delay: 7200 },
      { type: "separator", text: "", delay: 7400 },
      { type: "agent", text: "STEP 5 — POST TO GL", delay: 7600 },
      { type: "tool", text: "→ erp_post_journal(idempotency_key: INV-2024-4521_gl_post)", delay: 7800 },
      { type: "result", text: "  ✓ Journal entry JE-29847 posted to GL", delay: 8600 },
      { type: "separator", text: "", delay: 8800 },
      { type: "confidence", text: "Confidence: 94% | Status: COMPLETED | Latency: 2.3s | Tokens: 847", delay: 9000 },
    ],
  },
  {
    id: "onboarding",
    label: "Employee Onboarding",
    icon: "M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z",
    agent: "Maya",
    designation: "Onboarding Specialist",
    avatar: "M",
    avatarColor: "bg-purple-500",
    domain: "hr",
    taskTitle: "Onboard New Hire: Rahul Kumar (SDE-II)",
    taskInput: '{ "name": "Rahul Kumar", "role": "SDE-II", "dept": "Engineering", "start": "2026-04-01" }',
    lines: [
      { type: "system", text: "Task assigned to Maya (Onboarding Specialist)", delay: 0 },
      { type: "system", text: "Loading prompt template: onboarding_agent.prompt.txt", delay: 400 },
      { type: "separator", text: "", delay: 600 },
      { type: "agent", text: "STEP 1 — CREATE EMPLOYEE RECORD", delay: 800 },
      { type: "tool", text: "→ darwinbox.create_employee(name: Rahul Kumar, role: SDE-II)", delay: 1000 },
      { type: "result", text: "  ✓ Employee ID: EMP-10847 created in Darwinbox", delay: 1800 },
      { type: "separator", text: "", delay: 2000 },
      { type: "agent", text: "STEP 2 — PROVISION IT ACCESS", delay: 2200 },
      { type: "tool", text: "→ google_workspace.create_account(rahul.kumar@agenticorg.ai)", delay: 2400 },
      { type: "result", text: "  ✓ Email account created", delay: 3000 },
      { type: "tool", text: "→ slack.invite_user(rahul.kumar@agenticorg.ai, channels: #engineering, #general)", delay: 3200 },
      { type: "result", text: "  ✓ Slack invite sent to 2 channels", delay: 3800 },
      { type: "tool", text: "→ github.add_to_team(user: rahulkumar, team: engineering)", delay: 4000 },
      { type: "result", text: "  ✓ Added to GitHub Engineering team", delay: 4600 },
      { type: "separator", text: "", delay: 4800 },
      { type: "agent", text: "STEP 3 — SCHEDULE ORIENTATION", delay: 5000 },
      { type: "tool", text: "→ google_calendar.create_event(Day 1 Orientation, Apr 1, 10:00 AM)", delay: 5200 },
      { type: "result", text: "  ✓ Orientation scheduled with hiring manager", delay: 5800 },
      { type: "separator", text: "", delay: 6000 },
      { type: "agent", text: "STEP 4 — SEND WELCOME KIT", delay: 6200 },
      { type: "tool", text: "→ email.send(to: rahul.kumar@agenticorg.ai, template: welcome_kit)", delay: 6400 },
      { type: "result", text: "  ✓ Welcome email sent with Day 1 checklist, laptop setup guide, benefits enrollment", delay: 7200 },
      { type: "separator", text: "", delay: 7400 },
      { type: "confidence", text: "Confidence: 96% | Status: COMPLETED | Latency: 1.8s | Tokens: 623", delay: 7600 },
    ],
  },
  {
    id: "ticket",
    label: "Support Triage",
    icon: "M18.364 5.636l-3.536 3.536m0 5.656l3.536 3.536M9.172 9.172L5.636 5.636m3.536 9.192l-3.536 3.536M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-5 0a4 4 0 11-8 0 4 4 0 018 0z",
    agent: "Dev",
    designation: "Support Triage Lead",
    avatar: "D",
    avatarColor: "bg-orange-500",
    domain: "ops",
    taskTitle: "Triage: 'Payment gateway timeout in production'",
    taskInput: '{ "ticket": "TKT-9921", "subject": "Payment gateway timeout", "source": "PagerDuty", "severity": "P1" }',
    lines: [
      { type: "system", text: "Task assigned to Dev (Support Triage Lead)", delay: 0 },
      { type: "system", text: "Loading prompt template: support_triage.prompt.txt", delay: 400 },
      { type: "separator", text: "", delay: 600 },
      { type: "agent", text: "STEP 1 — CLASSIFY SEVERITY", delay: 800 },
      { type: "tool", text: "→ analyze_ticket(subject: Payment gateway timeout, source: PagerDuty)", delay: 1000 },
      { type: "result", text: "  ✓ Classification: P1 — Production, Revenue-impacting, Payment system", delay: 1800 },
      { type: "result", text: "  ✓ Category: Infrastructure > Payment Gateway > Timeout", delay: 2200, color: "text-red-400" },
      { type: "separator", text: "", delay: 2400 },
      { type: "agent", text: "STEP 2 — CHECK RELATED INCIDENTS", delay: 2600 },
      { type: "tool", text: "→ pagerduty.search_incidents(service: payment-gateway, last_24h: true)", delay: 2800 },
      { type: "result", text: "  ✓ Found 3 related alerts in last 2 hours — possible systemic issue", delay: 3600 },
      { type: "separator", text: "", delay: 3800 },
      { type: "agent", text: "STEP 3 — ROUTE TO SPECIALIST", delay: 4000 },
      { type: "tool", text: "→ get_oncall_engineer(team: payments, escalation_level: 1)", delay: 4200 },
      { type: "result", text: "  ✓ On-call: Amit Shah (Payments Team Lead)", delay: 4800 },
      { type: "tool", text: "→ slack.send_alert(channel: #payments-oncall, mention: @amitshah)", delay: 5000 },
      { type: "result", text: "  ✓ P1 alert sent to #payments-oncall with full context", delay: 5600 },
      { type: "separator", text: "", delay: 5800 },
      { type: "agent", text: "STEP 4 — CREATE WAR ROOM", delay: 6000 },
      { type: "tool", text: "→ zoom.create_meeting(title: P1 War Room — Payment Gateway, auto_record: true)", delay: 6200 },
      { type: "result", text: "  ✓ War room created, link shared in #payments-oncall", delay: 6800 },
      { type: "separator", text: "", delay: 7000 },
      { type: "hitl", text: "⚠ HITL TRIGGERED: P1 incident requires COO acknowledgment within 15 minutes", delay: 7200 },
      { type: "confidence", text: "Confidence: 91% | Status: HITL_TRIGGERED | Latency: 1.4s | Tokens: 712", delay: 7800 },
    ],
  },
  {
    id: "recon",
    label: "Bank Reconciliation",
    icon: "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z",
    agent: "Arjun",
    designation: "Recon Agent — East",
    avatar: "A",
    avatarColor: "bg-blue-500",
    domain: "finance",
    taskTitle: "Daily Bank Reconciliation — March 24, 2026",
    taskInput: '{ "date": "2026-03-24", "bank": "HDFC Current A/C", "expected_txns": 847 }',
    lines: [
      { type: "system", text: "Task assigned to Arjun (Recon Agent — East)", delay: 0 },
      { type: "system", text: "Loading prompt template: recon_agent.prompt.txt", delay: 400 },
      { type: "separator", text: "", delay: 600 },
      { type: "agent", text: "STEP 1 — FETCH DATA", delay: 800 },
      { type: "tool", text: "→ banking_api.get_transactions(date: 2026-03-24, account: HDFC-001)", delay: 1000 },
      { type: "result", text: "  ✓ Fetched 847 bank transactions (₹4.2 Cr total)", delay: 1800 },
      { type: "tool", text: "→ erp.get_gl_entries(date: 2026-03-24)", delay: 2000 },
      { type: "result", text: "  ✓ Fetched 839 GL entries", delay: 2600 },
      { type: "separator", text: "", delay: 2800 },
      { type: "agent", text: "STEP 2 — AUTO-MATCH", delay: 3000 },
      { type: "tool", text: "→ match_engine.run(bank_txns: 847, gl_entries: 839, tolerance: ₹10)", delay: 3200 },
      { type: "result", text: "  ✓ Round 1 (exact match): 812 matched", delay: 4000 },
      { type: "result", text: "  ✓ Round 2 (fuzzy ref match): 31 matched", delay: 4600 },
      { type: "result", text: "  ✓ Total matched: 843 / 847 (99.5%)", delay: 5200, color: "text-emerald-400" },
      { type: "separator", text: "", delay: 5400 },
      { type: "agent", text: "STEP 3 — ANALYZE BREAKS", delay: 5600 },
      { type: "result", text: "  Break 1: ₹15,400 — Bank debit, no GL entry (possible bank charge)", delay: 6000 },
      { type: "result", text: "  Break 2: ₹2,30,000 — GL credit, no bank entry (cheque not cleared)", delay: 6400 },
      { type: "result", text: "  Break 3: ₹8,200 — Amount mismatch (bank ₹8,200 vs GL ₹8,000)", delay: 6800 },
      { type: "result", text: "  Break 4: ₹47,500 — Timing difference (posted Mar 25 in bank)", delay: 7200 },
      { type: "separator", text: "", delay: 7400 },
      { type: "hitl", text: "⚠ HITL TRIGGERED: 4 breaks totaling ₹3,01,100 — CFO review required", delay: 7600 },
      { type: "confidence", text: "Confidence: 97% | Status: HITL_TRIGGERED | Latency: 3.1s | Tokens: 1,243", delay: 8200 },
    ],
  },
];

/* ── Typewriter hook ── */
function useTypewriter(text: string, speed: number, active: boolean) {
  const [displayed, setDisplayed] = useState("");
  useEffect(() => {
    if (!active) { setDisplayed(text); return; }
    setDisplayed("");
    let i = 0;
    const interval = setInterval(() => {
      i++;
      setDisplayed(text.slice(0, i));
      if (i >= text.length) clearInterval(interval);
    }, speed);
    return () => clearInterval(interval);
  }, [text, speed, active]);
  return displayed;
}

/* ── Trace Line Component ── */
function TraceLineView({ line, animate }: { line: TraceLine; animate: boolean }) {
  const text = useTypewriter(line.text, 12, animate && line.type !== "separator");

  if (line.type === "separator") return <div className="h-1" />;

  const styles: Record<string, string> = {
    system: "text-slate-500 text-xs italic",
    agent: "text-blue-400 font-bold text-sm",
    tool: "text-violet-400 text-sm font-mono",
    result: line.color || "text-slate-300 text-sm font-mono",
    hitl: "text-amber-400 text-sm font-semibold",
    confidence: "text-emerald-400 text-sm font-mono font-semibold",
  };

  const prefix: Record<string, string> = {
    system: "SYS",
    agent: "AGT",
    tool: "TOOL",
    result: "OUT",
    hitl: "HITL",
    confidence: "DONE",
  };

  return (
    <div className="flex gap-3 group">
      <span className={`text-xs font-mono w-8 flex-shrink-0 mt-0.5 ${
        line.type === "hitl" ? "text-amber-600" :
        line.type === "confidence" ? "text-emerald-600" :
        line.type === "agent" ? "text-blue-600" :
        "text-slate-700"
      }`}>
        {prefix[line.type]}
      </span>
      <span className={styles[line.type]}>
        {text}
        {animate && text.length < line.text.length && <span className="inline-block w-1.5 h-3.5 bg-blue-400 ml-0.5 animate-pulse" />}
      </span>
    </div>
  );
}

/* ── Main Component ── */
export default function InteractiveDemo() {
  const [activeScenario, setActiveScenario] = useState(0);
  const [visibleLines, setVisibleLines] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const timeoutsRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const scenario = SCENARIOS[activeScenario];

  const clearTimeouts = useCallback(() => {
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];
  }, []);

  const runDemo = useCallback(() => {
    clearTimeouts();
    setVisibleLines(0);
    setIsRunning(true);

    scenario.lines.forEach((line, i) => {
      const t = setTimeout(() => {
        setVisibleLines(i + 1);
        // Auto-scroll
        if (feedRef.current) {
          feedRef.current.scrollTop = feedRef.current.scrollHeight;
        }
        if (i === scenario.lines.length - 1) {
          setIsRunning(false);
        }
      }, line.delay);
      timeoutsRef.current.push(t);
    });
  }, [scenario, clearTimeouts]);

  // Auto-run on scenario change
  useEffect(() => {
    const t = setTimeout(() => runDemo(), 500);
    return () => { clearTimeout(t); clearTimeouts(); };
  }, [activeScenario]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-cycle scenarios
  useEffect(() => {
    if (isRunning) return;
    const t = setTimeout(() => {
      setActiveScenario((prev) => (prev + 1) % SCENARIOS.length);
    }, 4000);
    return () => clearTimeout(t);
  }, [isRunning, activeScenario]);

  const lastLine = scenario.lines[visibleLines - 1];
  const isDone = lastLine?.type === "confidence";
  const isHITL = scenario.lines.some((l, i) => l.type === "hitl" && i < visibleLines);

  return (
    <div className="max-w-5xl mx-auto">
      {/* Scenario Tabs */}
      <div className="flex flex-wrap gap-2 mb-6 justify-center">
        {SCENARIOS.map((s, i) => (
          <button
            key={s.id}
            onClick={() => { setActiveScenario(i); }}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              i === activeScenario
                ? "bg-gradient-to-r from-blue-500 to-violet-600 text-white shadow-lg shadow-blue-500/25"
                : "bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 border border-slate-700"
            }`}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={s.icon} />
            </svg>
            {s.label}
          </button>
        ))}
      </div>

      {/* Terminal */}
      <div className="rounded-2xl overflow-hidden border border-slate-700 bg-slate-950 shadow-2xl">
        {/* Title bar */}
        <div className="flex items-center gap-2 px-4 py-3 bg-slate-900 border-b border-slate-800">
          <div className="flex gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-500/80" />
            <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
            <div className="w-3 h-3 rounded-full bg-green-500/80" />
          </div>
          <div className="flex-1 mx-4">
            <div className="bg-slate-800 rounded-md px-3 py-1 text-xs text-slate-400 text-center flex items-center justify-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full ${isRunning ? "bg-blue-400 animate-pulse" : isDone ? "bg-emerald-400" : "bg-slate-600"}`} />
              AgenticOrg Agent Terminal — {scenario.agent} ({scenario.designation})
            </div>
          </div>
          <button
            onClick={runDemo}
            disabled={isRunning}
            className={`text-xs px-3 py-1 rounded-md transition-all ${
              isRunning
                ? "bg-slate-800 text-slate-600 cursor-not-allowed"
                : "bg-blue-600 text-white hover:bg-blue-500"
            }`}
          >
            {isRunning ? "Running..." : "Re-run"}
          </button>
        </div>

        {/* Agent + Task Header */}
        <div className="px-5 py-4 border-b border-slate-800/50 bg-slate-900/50">
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-full ${scenario.avatarColor} flex items-center justify-center text-white font-bold text-sm`}>
              {scenario.avatar}
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-white font-semibold text-sm">{scenario.agent}</span>
                <span className="text-xs text-slate-500">{scenario.designation}</span>
                <span className="text-xs bg-slate-800 text-slate-400 px-2 py-0.5 rounded ml-auto">{scenario.domain}</span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">{scenario.taskTitle}</p>
            </div>
          </div>

          {/* Task Input */}
          <div className="mt-3 bg-slate-800/50 rounded-lg px-3 py-2">
            <span className="text-xs text-slate-600">Input: </span>
            <code className="text-xs text-slate-400 font-mono">{scenario.taskInput}</code>
          </div>
        </div>

        {/* Trace Output */}
        <div
          ref={feedRef}
          className="px-5 py-4 space-y-1.5 max-h-[400px] overflow-y-auto scroll-smooth"
          style={{ minHeight: "280px" }}
        >
          {scenario.lines.slice(0, visibleLines).map((line, i) => (
            <TraceLineView
              key={`${scenario.id}-${i}`}
              line={line}
              animate={i === visibleLines - 1}
            />
          ))}

          {isRunning && visibleLines > 0 && !isDone && (
            <div className="flex items-center gap-2 mt-2 text-xs text-slate-600">
              <div className="flex gap-1">
                <div className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: "0ms" }} />
                <div className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: "150ms" }} />
                <div className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
              Processing...
            </div>
          )}
        </div>

        {/* Status bar */}
        <div className="px-5 py-3 border-t border-slate-800 bg-slate-900/50 flex items-center justify-between">
          <div className="flex items-center gap-4 text-xs">
            <span className={`flex items-center gap-1.5 ${isDone ? "text-emerald-400" : isHITL ? "text-amber-400" : isRunning ? "text-blue-400" : "text-slate-600"}`}>
              <span className={`w-2 h-2 rounded-full ${isDone ? "bg-emerald-400" : isHITL ? "bg-amber-400 animate-pulse" : isRunning ? "bg-blue-400 animate-pulse" : "bg-slate-600"}`} />
              {isDone ? (isHITL ? "HITL Triggered" : "Completed") : isRunning ? "Executing" : "Ready"}
            </span>
            <span className="text-slate-600">Steps: {Math.min(visibleLines, scenario.lines.filter(l => l.type === "agent").length)}/{scenario.lines.filter(l => l.type === "agent").length}</span>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-600">
            <span>Gemini 2.5 Flash</span>
            <span>|</span>
            <span>LLM + Tools</span>
          </div>
        </div>
      </div>
    </div>
  );
}
