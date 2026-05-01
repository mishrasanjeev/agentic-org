import { useState, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface UseCase {
  id: string;
  emoji: string;
  title: string;
  domain: string;
  domainLabel: string;
  agentId: string;
  agentName: string;
  input: Record<string, unknown>;
}

interface TraceLine {
  text: string;
  color: "blue" | "amber" | "green" | "red" | "gray";
}

interface RunSummary {
  status: string;
  confidence: number | null;
  latency: string;
  hitlTriggered: boolean;
}

/* ------------------------------------------------------------------ */
/*  Use-case data                                                      */
/* ------------------------------------------------------------------ */

const USE_CASES: UseCase[] = [
  {
    id: "process-invoice",
    emoji: "\uD83E\uDDFE",
    title: "Process Invoice",
    domain: "finance",
    domainLabel: "Finance (CFO)",
    agentId: "a0000001-0000-0000-0001-000000000001",
    agentName: "AP Processor",
    input: {
      action: "process_invoice",
      inputs: {
        invoice_id: "INV-2024-DEMO",
        vendor: "Tata Steel Ltd",
        amount: 782000,
        gstin: "29ABCDE1234F1Z5",
        po_number: "PO-7842",
        po_amount: 782000,
        grn_number: "GRN-4521",
        grn_amount: 782000,
        invoice_date: "2026-03-20",
        due_date: "2026-04-19",
        currency: "INR",
        line_items: [
          { description: "Hot Rolled Steel Coil 3mm", qty: 50, unit_price: 12640, amount: 632000 },
          { description: "Freight and Handling", qty: 1, unit_price: 150000, amount: 150000 },
        ],
        bank_details: { ifsc: "SBIN0001234", account: "38429876543" },
      },
    },
  },
  {
    id: "reconcile-bank",
    emoji: "\uD83C\uDFE6",
    title: "Reconcile Bank Transactions",
    domain: "finance",
    domainLabel: "Finance (CFO)",
    agentId: "a0000001-0000-0000-0001-000000000003",
    agentName: "Recon Agent",
    input: {
      action: "daily_reconciliation",
      inputs: {
        account: "HDFC-2847",
        date: "2026-03-23",
        bank_transactions: [
          { ref: "TXN-4471", amount: 245000, type: "debit" },
          { ref: "TXN-4472", amount: 89000, type: "credit" },
          { ref: "TXN-4473", amount: 156000, type: "debit" },
        ],
        gl_entries: [
          { ref: "TXN-4471", amount: 245000 },
          { ref: "TXN-4472", amount: 89000 },
        ],
      },
    },
  },
  {
    id: "screen-resume",
    emoji: "\uD83D\uDCC4",
    title: "Screen Resume",
    domain: "hr",
    domainLabel: "HR (CHRO)",
    agentId: "a0000001-0000-0000-0002-000000000003",
    agentName: "Talent Acquisition",
    input: {
      action: "screen_resume",
      inputs: {
        candidate: "Priya Mehta",
        role: "Senior Backend Engineer",
        experience_years: 7,
        skills: ["Python", "Go", "Kubernetes", "PostgreSQL", "AWS"],
        education: "IIT Bombay B.Tech CS",
        current_ctc: 2800000,
        expected_ctc: 3500000,
        notice_period_days: 60,
      },
    },
  },
  {
    id: "compute-payroll",
    emoji: "\uD83D\uDCB0",
    title: "Compute Payroll",
    domain: "hr",
    domainLabel: "HR (CHRO)",
    agentId: "a0000001-0000-0000-0002-000000000002",
    agentName: "Payroll Engine",
    input: {
      action: "compute_payroll",
      inputs: {
        employee_id: "EMP-4521",
        name: "Rahul Sharma",
        basic: 85000,
        hra: 42500,
        special_allowance: 22500,
        pf_contribution: true,
        professional_tax_state: "Karnataka",
        tds_regime: "new",
      },
    },
  },
  {
    id: "score-lead",
    emoji: "\uD83C\uDFAF",
    title: "Score Lead",
    domain: "marketing",
    domainLabel: "Marketing (CMO)",
    agentId: "a0000001-0000-0000-0003-000000000004",
    agentName: "CRM Intelligence",
    input: {
      action: "score_lead",
      inputs: {
        lead_name: "Acme Corp",
        source: "hubspot_form",
        company_size: "500-1000",
        industry: "BFSI",
        pages_visited: 12,
        email_opens: 8,
        demo_requested: true,
        budget_range: "10L-50L",
      },
    },
  },
  {
    id: "analyze-sentiment",
    emoji: "\uD83D\uDCCA",
    title: "Analyze Brand Sentiment",
    domain: "marketing",
    domainLabel: "Marketing (CMO)",
    agentId: "a0000001-0000-0000-0003-000000000005",
    agentName: "Brand Monitor",
    input: {
      action: "analyze_sentiment",
      inputs: {
        brand: "AgenticOrg",
        platform: "twitter",
        timeframe: "last_24h",
        mention_count: 47,
        positive: 15,
        negative: 28,
        neutral: 4,
        top_complaint: "checkout page broken",
        trending_hashtag: "#agenticorgdown",
      },
    },
  },
  {
    id: "classify-ticket",
    emoji: "\uD83C\uDFAB",
    title: "Classify Support Ticket",
    domain: "operations",
    domainLabel: "Operations (COO)",
    agentId: "a0000001-0000-0000-0004-000000000001",
    agentName: "Support Triage",
    input: {
      action: "classify_ticket",
      inputs: {
        ticket_id: "TKT-2026-8847",
        subject: "Payment failed after entering OTP",
        body: "I tried to pay for my order #ORD-4521 but after entering OTP the page showed error 500. Amount was debited from my bank account but order shows unpaid. Please help urgently.",
        customer_tier: "premium",
        channel: "email",
      },
    },
  },
  {
    id: "incident-response",
    emoji: "\uD83D\uDEA8",
    title: "Respond to P1 Incident",
    domain: "operations",
    domainLabel: "Operations (COO)",
    agentId: "a0000001-0000-0000-0004-000000000002",
    agentName: "IT Operations",
    input: {
      action: "incident_response",
      inputs: {
        incident_id: "INC-2026-0415",
        severity: "P1",
        service: "payment-gateway",
        error_rate: 0.3,
        affected_region: "asia-south1",
        first_detected: "2026-03-23T14:30:00Z",
        symptoms: [
          "5xx errors on /checkout",
          "connection pool exhaustion",
          "latency spike 200ms to 8s",
        ],
      },
    },
  },
];

/* ------------------------------------------------------------------ */
/*  Domain colors                                                      */
/* ------------------------------------------------------------------ */

const DOMAIN_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  finance: { border: "border-emerald-500/40", bg: "bg-emerald-500/10", text: "text-emerald-400" },
  hr: { border: "border-violet-500/40", bg: "bg-violet-500/10", text: "text-violet-400" },
  marketing: { border: "border-amber-500/40", bg: "bg-amber-500/10", text: "text-amber-400" },
  operations: { border: "border-blue-500/40", bg: "bg-blue-500/10", text: "text-blue-400" },
};

/* ------------------------------------------------------------------ */
/*  Auth helper                                                        */
/* ------------------------------------------------------------------ */

async function getAuthToken(): Promise<string> {
  // SEC-002 (PR-F): authenticated users now ride on the HttpOnly
  // session cookie — no localStorage token to grab. The demo path
  // below still uses a sessionStorage cache (different key, short-
  // lived, intentionally separate from the user session) so the
  // public Playground works for anonymous visitors without
  // requiring login.
  const cached = sessionStorage.getItem("playground_token");
  if (cached) return cached;
  const resp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "ceo@agenticorg.local", password: "ceo123!" }),
  });
  if (!resp.ok) {
    throw new Error("Demo login failed — make sure the backend is running and the demo user is seeded.");
  }
  const data: { access_token?: string } = await resp.json();
  if (!data.access_token) {
    throw new Error("Login succeeded but no access token returned.");
  }
  sessionStorage.setItem("playground_token", data.access_token);
  return data.access_token;
}

/* ------------------------------------------------------------------ */
/*  Trace color mapping                                                */
/* ------------------------------------------------------------------ */

const TRACE_COLOR_MAP: Record<string, string> = {
  blue: "text-blue-400",
  amber: "text-amber-400",
  green: "text-emerald-400",
  red: "text-red-400",
  gray: "text-slate-400",
};

/* ------------------------------------------------------------------ */
/*  parseTraceLines — turns reasoning_trace + output into colored lines */
/* ------------------------------------------------------------------ */

function parseTraceLines(
  agentName: string,
  result: Record<string, unknown>,
): TraceLine[] {
  const lines: TraceLine[] = [];
  const traces = (result.reasoning_trace ?? result.trace ?? []) as string[];
  const output = result.output ?? result.result ?? result;

  // Opening
  lines.push({ text: `> Agent "${agentName}" starting...`, color: "gray" });
  lines.push({ text: "Calling LLM for reasoning...", color: "blue" });

  // Walk traces
  for (const t of traces) {
    const lower = t.toLowerCase();
    if (lower.includes("hitl") || lower.includes("human") || lower.includes("approval")) {
      lines.push({ text: `\u26A0\uFE0F HITL Triggered: ${t}`, color: "red" });
    } else if (lower.includes("tool") || lower.includes("calling") || lower.includes("fetch")) {
      lines.push({ text: `[tool] ${t}`, color: "gray" });
    } else if (lower.includes("result") || lower.includes("confidence") || lower.includes("done")) {
      lines.push({ text: t, color: "green" });
    } else {
      lines.push({ text: t, color: "blue" });
    }
  }

  // Extract performance from real response
  const perf = result.performance as Record<string, unknown> | undefined;
  const tokensUsed = traces.find(t => t.includes("tokens"))?.match(/(\d+)\s*tokens/)?.[1] || "~1000";
  const latencyMs = perf?.total_latency_ms ?? "—";
  lines.push({ text: `LLM responded: gemini-2.5-flash, ${tokensUsed} tokens (${typeof latencyMs === "number" ? (latencyMs / 1000).toFixed(1) + "s" : "—"})`, color: "amber" });

  // Parse output intelligently — show key fields, not raw JSON
  const out = (typeof output === "object" && output !== null) ? output as Record<string, unknown> : {};
  const rawOut = out.raw_output as string | undefined;
  const parsed = rawOut ? (() => { try { return JSON.parse(rawOut); } catch { return out; } })() : out;
  const p = (typeof parsed === "object" && parsed !== null) ? parsed as Record<string, unknown> : out;

  // Show meaningful fields as clean lines
  if (p.status) lines.push({ text: `Status: ${p.status}`, color: "green" });
  if (p.invoice_id) lines.push({ text: `Invoice: ${p.invoice_id}`, color: "green" });
  if (p.match_delta !== undefined) lines.push({ text: `3-Way Match Delta: ${p.match_delta ?? "N/A"}`, color: p.match_delta === 0 ? "green" : "amber" });
  if (p.payment_scheduled_date) lines.push({ text: `Payment Scheduled: ${p.payment_scheduled_date}`, color: "green" });
  if (p.gl_posting_id) lines.push({ text: `GL Posting: ${p.gl_posting_id}`, color: "green" });
  if (p.escalation_reason) lines.push({ text: `Escalation: ${p.escalation_reason}`, color: "red" });
  if (p.score !== undefined) lines.push({ text: `Score: ${p.score}`, color: "green" });
  if (p.classification) lines.push({ text: `Classification: ${p.classification}`, color: "green" });
  if (p.priority) lines.push({ text: `Priority: ${p.priority}`, color: "amber" });
  if (p.net_pay) lines.push({ text: `Net Pay: ₹${Number(p.net_pay).toLocaleString("en-IN")}`, color: "green" });
  if (p.pf_deduction) lines.push({ text: `PF Deduction: ₹${Number(p.pf_deduction).toLocaleString("en-IN")}`, color: "gray" });
  if (p.tds) lines.push({ text: `TDS: ₹${Number(p.tds).toLocaleString("en-IN")}`, color: "gray" });
  if (p.recommendation) lines.push({ text: `Recommendation: ${p.recommendation}`, color: "green" });
  if (p.root_cause) lines.push({ text: `Root Cause: ${p.root_cause}`, color: "amber" });
  if (p.sentiment_score !== undefined) lines.push({ text: `Sentiment: ${p.sentiment_score}`, color: Number(p.sentiment_score) < 0 ? "red" : "green" });
  if (p.matched !== undefined) lines.push({ text: `Matched: ${p.matched}/${p.total ?? "?"}`, color: "green" });
  if (p.breaks !== undefined) lines.push({ text: `Breaks Found: ${p.breaks}`, color: Number(p.breaks) > 0 ? "red" : "green" });

  // Show processing trace from agent if available
  const agentTrace = (p.processing_trace ?? p.trace ?? p.steps) as string[] | undefined;
  if (Array.isArray(agentTrace)) {
    for (const step of agentTrace) {
      lines.push({ text: `  → ${step}`, color: "gray" });
    }
  }

  // Show raw output only if we didn't extract any meaningful fields
  const meaningfulFields = ["status", "invoice_id", "score", "classification", "net_pay", "matched", "recommendation", "root_cause", "sentiment_score"];
  const hasMeaningful = meaningfulFields.some(f => p[f] !== undefined);
  if (!hasMeaningful && Object.keys(p).length > 0) {
    lines.push({ text: JSON.stringify(p, null, 2).substring(0, 500), color: "gray" });
  }

  // Tool call results
  const toolCalls = (result.tool_calls ?? []) as Array<Record<string, unknown>>;
  if (toolCalls.length > 0) {
    lines.push({ text: `--- Tool Calls (${toolCalls.length}) ---`, color: "amber" });
    for (const tc of toolCalls) {
      const status = tc.status as string;
      const color = status === "success" ? "green" : "red";
      lines.push({ text: `  ${tc.tool_name}: ${status} (${tc.latency_ms}ms)`, color });
    }
  }
  const toolResults = (p.tool_results ?? []) as Array<Record<string, unknown>>;
  if (toolResults.length > 0) {
    for (const tr of toolResults) {
      const res = tr.result as Record<string, unknown> | undefined;
      lines.push({ text: `  ${tr.connector}.${tr.tool} result:`, color: "green" });
      if (res) {
        const preview = JSON.stringify(res).substring(0, 300);
        lines.push({ text: `    ${preview}`, color: "gray" });
      }
    }
  }

  // Confidence
  const confidence = (result.confidence as number) ?? (p.confidence as number) ?? null;
  if (confidence !== null) {
    const pct = confidence > 1 ? confidence : confidence * 100;
    lines.push({ text: `Confidence: ${Math.round(pct)}%`, color: pct >= 90 ? "green" : pct >= 80 ? "amber" : "red" });
  }

  // HITL info
  const hitl = result.hitl_request as Record<string, unknown> | undefined;
  if (hitl) {
    lines.push({ text: `⚠️ Human approval required: ${hitl.trigger_condition || "Threshold exceeded"}`, color: "red" });
  }

  lines.push({ text: "> Run complete.", color: "gray" });
  return lines;
}

/* ------------------------------------------------------------------ */
/*  Playground Component                                               */
/* ------------------------------------------------------------------ */

/* ── Your Agents section — shows user-created agents from API ── */
function UserAgentsSection({ onRun, running, selectedId }: { onRun: (uc: any) => void; running: boolean; selectedId?: string }) {
  const [agents, setAgents] = useState<any[]>([]);
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({});
  const [inputErrors, setInputErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    // SEC-002 (PR-F): cookie-first. The browser ships the HttpOnly
    // session cookie automatically because of credentials: "include".
    // Server returns 401 when there is no session — we treat that as
    // "no custom agents to show" (anonymous Playground visitor).
    fetch("/api/v1/agents?per_page=50", { credentials: "include" })
      .then((r) => {
        if (!r.ok) return { items: [] };
        return r.json();
      })
      .then((data) => {
        const items = data.items || [];
        // Filter to non-builtin agents only
        const custom = items.filter((a: any) => !a.is_builtin);
        setAgents(custom);
        // Initialize default input for each agent
        const defaults: Record<string, string> = {};
        for (const a of custom) {
          defaults[a.id] = JSON.stringify({ action: "process", inputs: { text: "Hello, process this request" } }, null, 2);
        }
        setCustomInputs(defaults);
      })
      .catch(() => {});
  }, []);

  if (agents.length === 0) return null;

  function handleRun(agent: any) {
    const raw = customInputs[agent.id] || '{ "action": "process", "inputs": {} }';
    try {
      const parsed = JSON.parse(raw);
      setInputErrors((prev) => ({ ...prev, [agent.id]: "" }));
      const uc = {
        id: `custom-${agent.id}`,
        agentId: agent.id,
        agentName: agent.employee_name || agent.name,
        title: agent.designation || agent.agent_type.replace(/_/g, " "),
        domain: agent.domain,
        emoji: agent.domain === "finance" ? "\uD83D\uDCB0" : agent.domain === "hr" ? "\uD83D\uDC65" : agent.domain === "marketing" ? "\uD83D\uDCE3" : "\u2699\uFE0F",
        input: parsed,
      };
      onRun(uc);
    } catch {
      setInputErrors((prev) => ({ ...prev, [agent.id]: "Invalid JSON" }));
    }
  }

  return (
    <section>
      <h2 className="text-xl font-semibold text-slate-200 mb-4">Your Agents</h2>
      <p className="text-sm text-slate-400 mb-4">Agents you created — edit the input JSON and click Run.</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {agents.map((agent) => {
          const ucId = `custom-${agent.id}`;
          const isActive = selectedId === ucId;
          return (
            <div
              key={agent.id}
              className={`p-4 rounded-xl border transition-all duration-200 ${
                isActive
                  ? "border-blue-500 bg-blue-500/10 ring-1 ring-blue-500"
                  : "border-slate-800 bg-slate-900"
              }`}
            >
              <div className="flex items-start gap-3 mb-3">
                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center text-white text-xs font-bold">
                  {(agent.employee_name || agent.name || "A").charAt(0)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-white text-sm">{agent.employee_name || agent.name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">{agent.agent_type} | {agent.domain}</p>
                  {agent.specialization && <p className="text-xs text-slate-500 mt-0.5 truncate">{agent.specialization}</p>}
                </div>
              </div>
              <textarea
                value={customInputs[agent.id] || ""}
                onChange={(e) => {
                  setCustomInputs((prev) => ({ ...prev, [agent.id]: e.target.value }));
                  setInputErrors((prev) => ({ ...prev, [agent.id]: "" }));
                }}
                className="w-full bg-black border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 min-h-[80px] max-h-40 resize-y focus:border-blue-500 focus:outline-none"
                placeholder='{ "action": "process", "inputs": { ... } }'
              />
              {inputErrors[agent.id] && (
                <p className="text-xs text-red-400 mt-1">{inputErrors[agent.id]}</p>
              )}
              <button
                onClick={() => handleRun(agent)}
                disabled={running}
                className={`mt-2 w-full text-center py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  running
                    ? "bg-slate-700 text-slate-400 cursor-not-allowed"
                    : "bg-blue-600 hover:bg-blue-500 text-white cursor-pointer"
                }`}
              >
                {running && isActive ? "Running..." : "Run"}
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function Playground() {
  const [selectedUseCase, setSelectedUseCase] = useState<UseCase | null>(null);
  const [running, setRunning] = useState(false);
  const [visibleLines, setVisibleLines] = useState<TraceLine[]>([]);
  const [allLines, setAllLines] = useState<TraceLine[]>([]);
  const [animIdx, setAnimIdx] = useState(0);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const terminalRef = useRef<HTMLDivElement>(null);
  const animTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* Auto-scroll terminal */
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [visibleLines]);

  /* Animate lines one-by-one */
  useEffect(() => {
    if (animIdx < allLines.length) {
      animTimerRef.current = setTimeout(() => {
        setVisibleLines((prev) => [...prev, allLines[animIdx]]);
        setAnimIdx((prev) => prev + 1);
      }, 500);
    } else if (allLines.length > 0 && animIdx >= allLines.length) {
      setRunning(false);
    }
    return () => {
      if (animTimerRef.current) clearTimeout(animTimerRef.current);
    };
  }, [animIdx, allLines]);

  /* Run an agent */
  const runAgent = useCallback(async (uc: UseCase) => {
    // Reset
    setSelectedUseCase(uc);
    setVisibleLines([]);
    setAllLines([]);
    setAnimIdx(0);
    setSummary(null);
    setError(null);
    setRunning(true);
    import("@/components/Analytics").then(m => m.trackEvent("agent_run", { agent_name: uc.agentName, source: "playground" })).catch(() => {});

    const startTime = performance.now();

    try {
      const token = await getAuthToken();
      const resp = await fetch(`/api/v1/agents/${uc.agentId}/run`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(uc.input),
      });

      if (!resp.ok) {
        // If 401, clear cached demo token and retry once. We don't
        // touch the real user session cookie here — that path goes
        // through the global API client redirector in lib/api.ts.
        if (resp.status === 401) {
          sessionStorage.removeItem("playground_token");
          const newToken = await getAuthToken();
          const retry = await fetch(`/api/v1/agents/${uc.agentId}/run`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${newToken}`,
            },
            body: JSON.stringify(uc.input),
          });
          if (!retry.ok) {
            throw new Error(`Agent returned ${retry.status}: ${await retry.text()}`);
          }
          const retryData = await retry.json();
          const elapsed = ((performance.now() - startTime) / 1000).toFixed(1);
          const lines = parseTraceLines(uc.agentName, retryData);
          setAllLines(lines);
          setSummary({
            status: (retryData.status as string) ?? "completed",
            confidence: (retryData.confidence as number) ?? null,
            latency: `${elapsed}s`,
            hitlTriggered: !!(retryData.hitl_triggered ?? retryData.requires_approval),
          });
          return;
        }
        throw new Error(`Agent returned ${resp.status}: ${await resp.text()}`);
      }

      const data = await resp.json();
      const elapsed = ((performance.now() - startTime) / 1000).toFixed(1);
      const lines = parseTraceLines(uc.agentName, data);
      setAllLines(lines);
      setSummary({
        status: (data.status as string) ?? "completed",
        confidence: (data.confidence as number) ?? null,
        latency: `${elapsed}s`,
        hitlTriggered: !!(data.hitl_triggered ?? data.requires_approval),
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setError(msg);
      setAllLines([
        { text: `> Agent "${uc.agentName}" starting...`, color: "gray" },
        { text: `Error: ${msg}`, color: "red" },
      ]);
      setRunning(false);
    }
  }, []);

  /* Group use cases by domain */
  const domains = ["finance", "hr", "marketing", "operations"] as const;
  const domainLabels: Record<string, string> = {
    finance: "Finance (CFO)",
    hr: "HR (CHRO)",
    marketing: "Marketing (CMO)",
    operations: "Operations (COO)",
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Helmet>
        <title>Agent Playground — Try AgenticOrg Live</title>
        <meta name="description" content="Try AgenticOrg AI agents live. No signup required. Pick a use case, click Run, and watch an AI agent work in real-time." />
        <link rel="canonical" href="https://agenticorg.ai/playground" />
      </Helmet>

      {/* ============================================================ */}
      {/* HEADER                                                        */}
      {/* ============================================================ */}
      <header className="border-b border-slate-800 bg-slate-950/95 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link to="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-sm">
                AO
              </div>
            </Link>
            <div className="h-6 w-px bg-slate-700" />
            <div>
              <h1 className="text-white font-semibold text-lg leading-tight">Agent Playground</h1>
              <p className="text-slate-400 text-xs">Try it live — no signup required</p>
            </div>
          </div>
          <a
            href="https://agenticorg.ai"
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-400 hover:text-white text-sm transition-colors flex items-center gap-1.5"
          >
            agenticorg.ai
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* ============================================================ */}
        {/* USE CASE SELECTOR                                            */}
        {/* ============================================================ */}
        <section>
          <h2 className="text-xl font-semibold text-slate-200 mb-6">
            Pick a use case to run
          </h2>

          <div className="space-y-6">
            {domains.map((domain) => {
              const ucs = USE_CASES.filter((u) => u.domain === domain);
              const colors = DOMAIN_COLORS[domain];
              return (
                <div key={domain}>
                  <h3 className={`text-sm font-medium mb-3 ${colors.text}`}>
                    {domainLabels[domain]}
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {ucs.map((uc) => {
                      const isActive = selectedUseCase?.id === uc.id;
                      return (
                        <button
                          key={uc.id}
                          onClick={() => runAgent(uc)}
                          disabled={running}
                          className={`text-left p-4 rounded-xl border transition-all duration-200 ${
                            isActive
                              ? `${colors.border} ${colors.bg} ring-1 ring-offset-0 ring-current`
                              : "border-slate-800 bg-slate-900 hover:border-slate-600 hover:bg-slate-800/80"
                          } ${running ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
                        >
                          <div className="flex items-start gap-3">
                            <span className="text-2xl leading-none mt-0.5">{uc.emoji}</span>
                            <div className="min-w-0">
                              <p className="font-medium text-white text-sm">{uc.title}</p>
                              <p className="text-xs text-slate-400 mt-0.5">
                                Agent: {uc.agentName}
                              </p>
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* ============================================================ */}
        {/* YOUR AGENTS (fetched from API)                                */}
        {/* ============================================================ */}
        <UserAgentsSection onRun={(uc) => runAgent(uc)} running={running} selectedId={selectedUseCase?.id} />

        {/* ============================================================ */}
        {/* LIVE OUTPUT                                                   */}
        {/* ============================================================ */}
        {(selectedUseCase || error) && (
          <section className="space-y-4">
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-semibold text-slate-200">
                Live Output
              </h2>
              {running && (
                <span className="flex items-center gap-2 text-sm text-blue-400">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
                  </span>
                  Running...
                </span>
              )}
            </div>

            {/* Input preview */}
            {selectedUseCase && (
              <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
                <p className="text-xs text-slate-500 mb-1 font-mono">
                  POST /api/v1/agents/{selectedUseCase.agentId}/run
                </p>
                <pre className="text-xs text-slate-300 overflow-x-auto font-mono leading-relaxed">
                  {JSON.stringify(selectedUseCase.input, null, 2)}
                </pre>
              </div>
            )}

            {/* Terminal */}
            <div className="bg-black border border-slate-800 rounded-xl overflow-hidden shadow-2xl">
              {/* Terminal title bar */}
              <div className="flex items-center gap-2 px-4 py-2.5 bg-slate-900 border-b border-slate-800">
                <div className="flex gap-1.5">
                  <div className="w-3 h-3 rounded-full bg-red-500/80" />
                  <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                  <div className="w-3 h-3 rounded-full bg-green-500/80" />
                </div>
                <span className="text-xs text-slate-500 ml-2 font-mono">
                  {selectedUseCase ? `${selectedUseCase.agentName} — agent trace` : "terminal"}
                </span>
              </div>

              {/* Terminal body */}
              <div
                ref={terminalRef}
                className="p-4 font-mono text-sm leading-relaxed max-h-[28rem] overflow-y-auto"
              >
                {visibleLines.length === 0 && !error && (
                  <span className="text-slate-600">Waiting for agent output...</span>
                )}
                {visibleLines.map((line, i) => (
                  <div
                    key={i}
                    className={`${TRACE_COLOR_MAP[line.color]} whitespace-pre-wrap break-all animate-fadeIn`}
                  >
                    {line.text}
                  </div>
                ))}
                {running && (
                  <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5 align-middle" />
                )}
              </div>
            </div>

            {/* Summary card */}
            {summary && !running && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                <h3 className="text-sm font-medium text-slate-300 mb-3">Run Summary</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Status</p>
                    <p className={`text-sm font-medium ${summary.status === "completed" ? "text-emerald-400" : "text-amber-400"}`}>
                      {summary.status}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Confidence</p>
                    <p className="text-sm font-medium text-white">
                      {summary.confidence !== null
                        ? `${Math.round(summary.confidence * 100)}%`
                        : "N/A"}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Latency</p>
                    <p className="text-sm font-medium text-white">{summary.latency}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">HITL Triggered</p>
                    <p className={`text-sm font-medium ${summary.hitlTriggered ? "text-red-400" : "text-emerald-400"}`}>
                      {summary.hitlTriggered ? "Yes" : "No"}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Error state */}
            {error && !running && (
              <div className="bg-red-950/30 border border-red-900/50 rounded-xl p-4">
                <p className="text-sm text-red-400">{error}</p>
                <p className="text-xs text-slate-500 mt-2">
                  Make sure the backend is running and the demo user is seeded.
                </p>
              </div>
            )}
          </section>
        )}
      </main>

      {/* Inline animation keyframe */}
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .animate-fadeIn {
          animation: fadeIn 0.3s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
