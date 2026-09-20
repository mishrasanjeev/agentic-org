import { useState, useEffect, useMemo, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Cell, LabelList,
} from "recharts";
import ProductOwnership from "../components/ProductOwnership";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AgentScore {
  agent: string;
  domain: string;
  quality: number;
  safety: number;
  performance: number;
  reliability: number;
  security: number;
  cost: number;
  composite: number;
  grade: string;
}

interface DomainSummary {
  domain: string;
  composite: number;
  grade: string;
  agentCount: number;
}

interface PlatformSummary {
  stpRate: number;
  hitlRate: number;
  meanConfidence: number;
  uptimeSla: number;
}

type DataQuality = "measured" | "simulated" | "demo" | "unknown";

interface ScorecardMeta {
  evaluatedAt: string;
  goldenTestCases: number;
  version: string;
  /** Provenance flag from the backend: measured | simulated | demo. */
  dataQuality: DataQuality;
}

interface EvalsData {
  meta: ScorecardMeta;
  platform: PlatformSummary;
  domains: DomainSummary[];
  agents: AgentScore[];
}

interface JevShadowPlan {
  status: string;
  effective_mode: string;
  active_routing_enabled: boolean;
  non_executing: boolean;
  corpus: {
    case_count: number;
    domains: string[];
    content_policy: string;
  };
  controls: {
    sample_rate: number;
    max_calls_per_run: number;
    failure_threshold: number;
    cooldown_seconds: number;
  };
  review_gates: {
    minimum_agreement_rate: number;
    maximum_invalid_or_unavailable: number;
    maximum_p95_latency_ms: number;
    human_review_required: boolean;
  };
  reporting: {
    status: string;
    run_policy: string;
  };
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const DOMAIN_COLORS: Record<string, string> = {
  finance: "#3b82f6",
  hr: "#8b5cf6",
  marketing: "#f59e0b",
  ops: "#10b981",
  backoffice: "#6366f1",
};

const DOMAIN_BG: Record<string, string> = {
  finance: "from-blue-500 to-blue-600",
  hr: "from-blue-500 to-blue-600",
  marketing: "from-amber-500 to-amber-600",
  ops: "from-emerald-500 to-emerald-600",
  backoffice: "from-cyan-500 to-teal-600",
};

const GRADE_COLORS: Record<string, string> = {
  "A+": "bg-emerald-100 text-emerald-800 border-emerald-300",
  A: "bg-green-100 text-green-800 border-green-300",
  "B+": "bg-lime-100 text-lime-800 border-lime-300",
  B: "bg-yellow-100 text-yellow-800 border-yellow-300",
  C: "bg-orange-100 text-orange-800 border-orange-300",
  F: "bg-red-100 text-red-800 border-red-300",
};

type SortKey = "agent" | "domain" | "quality" | "safety" | "performance" | "reliability" | "security" | "cost" | "composite" | "grade";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function scoreColor(v: number): string {
  const pct = v > 1 ? v : v * 100;
  if (pct >= 90) return "bg-emerald-100 text-emerald-800";
  if (pct >= 80) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
}

function fmtPct(v: number): string {
  const pct = v > 1 ? v : v * 100;
  return `${Math.round(pct)}%`;
}

function metricCardColor(v: number): string {
  const pct = v > 1 ? v : v * 100;
  if (pct >= 90) return "border-emerald-400 bg-emerald-50";
  if (pct >= 80) return "border-yellow-400 bg-yellow-50";
  return "border-red-400 bg-red-50";
}

function metricTextColor(v: number): string {
  const pct = v > 1 ? v : v * 100;
  if (pct >= 90) return "text-emerald-700";
  if (pct >= 80) return "text-yellow-700";
  return "text-red-700";
}

function parseDataQuality(raw: any): DataQuality {
  const v = typeof raw?.data_quality === "string" ? raw.data_quality.toLowerCase() : "";
  if (v === "measured" || v === "simulated" || v === "demo") return v;
  // Legacy baseline payloads only carried ``_is_baseline``.
  if (raw?._is_baseline === true) return "demo";
  return "unknown";
}

const DATA_QUALITY_BANNER: Record<Exclude<DataQuality, "measured">, { title: string; body: string }> = {
  demo: {
    title: "Baseline placeholder \u2014 no measurements",
    body: "The evaluation runner has not produced a scorecard for this environment. The numbers below are a static baseline placeholder, not measured results.",
  },
  simulated: {
    title: "Simulated scorecard \u2014 not from live agent runs",
    body: "These scores come from a simulated evaluation pass, not from live agent runs. Treat them as illustrative only.",
  },
  unknown: {
    title: "Data provenance not reported",
    body: "The evaluation API did not report whether these numbers are measured. Do not treat them as verified results.",
  },
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-IN", {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Evals() {
  const [data, setData] = useState<EvalsData | null>(null);
  const [jevPlan, setJevPlan] = useState<JevShadowPlan | null>(null);
  const [jevPlanError, setJevPlanError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [domainFilter, setDomainFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("composite");
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    fetch("/api/v1/evals")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((raw: any) => {
        // Transform API shape to UI shape
        const pm = raw.platform_metrics || {};
        // Build agent→domain map from domain_aggregates and case_results
        const agentDomainMap: Record<string, string> = {};
        for (const [domName] of Object.entries(raw.domain_aggregates || {})) {
          // Find agents belonging to this domain from case_results
          for (const c of (raw.case_results || [])) {
            if (c.domain === domName || !c.domain) {
              // If case has no domain, match by checking if agent is in this domain's cases
            }
            if (c.domain) agentDomainMap[c.agent_type] = c.domain;
          }
        }
        // Fallback: derive domain from agent_type naming convention
        const AGENT_DOMAINS: Record<string, string> = {
          ap_processor: "finance", ar_collections: "finance", recon_agent: "finance",
          tax_compliance: "finance", close_agent: "finance", fpa_agent: "finance",
          onboarding: "hr", payroll_engine: "hr", talent_acquisition: "hr",
          performance_coach: "hr", offboarding: "hr", ld_coordinator: "hr",
          campaign_pilot: "marketing", content_factory: "marketing", seo_strategist: "marketing",
          crm_intelligence: "marketing", brand_monitor: "marketing",
          support_triage: "ops", it_operations: "ops", compliance_guard: "ops",
          contract_intelligence: "ops", vendor_manager: "ops",
        };

        const agents: AgentScore[] = Object.entries(raw.agent_aggregates || {}).map(
          ([name, a]: [string, any]) => {
            const s = a.avg_scores || {};
            return {
              agent: name,
              domain: agentDomainMap[name] || AGENT_DOMAINS[name] || "",
              quality: s.quality || 0,
              safety: s.safety || 0,
              performance: s.performance || 0,
              reliability: s.reliability || 0,
              security: s.security || 0,
              cost: s.cost || 0,
              composite: a.avg_composite || 0,
              grade: a.grade || "?",
            };
          }
        );
        const domains: DomainSummary[] = Object.entries(raw.domain_aggregates || {}).map(
          ([name, d]: [string, any]) => ({
            domain: name,
            composite: d.avg_composite || 0,
            grade: d.grade || "?",
            agentCount: d.agent_count || d.cases_evaluated || 0,
          })
        );
        const parsed: EvalsData = {
          meta: {
            evaluatedAt: raw.generated_at || "",
            goldenTestCases: pm.total_cases || 0,
            version: raw.version || "not reported",
            dataQuality: parseDataQuality(raw),
          },
          platform: {
            stpRate: pm.stp_rate ?? 0,
            hitlRate: pm.hitl_rate ?? 0,
            meanConfidence: pm.mean_confidence ?? pm.avg_composite ?? 0,
            uptimeSla: pm.uptime_sla ?? 0,
          },
          domains,
          agents,
        };
        setData(parsed);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load evals");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    fetch("/api/v1/evals/jev-shadow")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((plan: JevShadowPlan) => setJevPlan(plan))
      .catch((e: unknown) => setJevPlanError(e instanceof Error ? e.message : "Unavailable"));
  }, []);

  const handleSort = useCallback((key: SortKey) => {
    setSortKey((prev) => {
      if (prev === key) { setSortAsc((a) => !a); return key; }
      setSortAsc(key === "agent" || key === "domain");
      return key;
    });
  }, []);

  const filteredAgents = useMemo(() => {
    if (!data) return [];
    let agents = data.agents;
    if (domainFilter !== "all") {
      agents = agents.filter((a) => a.domain === domainFilter);
    }
    const sorted = [...agents].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      if (typeof aVal === "number" && typeof bVal === "number") {
        return sortAsc ? aVal - bVal : bVal - aVal;
      }
      return 0;
    });
    return sorted;
  }, [data, domainFilter, sortKey, sortAsc]);

  const chartData = useMemo(() => {
    if (!data) return [];
    return [...data.agents]
      .sort((a, b) => b.composite - a.composite)
      .map((a) => ({ name: a.agent, composite: a.composite > 1 ? a.composite : Math.round(a.composite * 100), domain: a.domain }));
  }, [data]);

  /* ---- Loading / Error states ---- */
  if (loading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-slate-600 text-sm">Loading evaluation matrix...</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <main className="min-h-screen bg-white flex items-center justify-center px-4">
        <div className="max-w-xl text-center">
          <h1 className="text-3xl font-bold text-slate-900">AI Agent Evaluations and Quality Gates</h1>
          <p className="mt-4 text-red-600 font-semibold" role="alert">Live evaluation data is unavailable</p>
          <p className="mt-2 text-slate-600">
            AgenticOrg does not substitute cached or fabricated scores when the evaluation API cannot be reached.
            Reload the page to request the current scorecard, or contact support if the problem continues.
          </p>
          <p className="mt-3 text-sm text-slate-500">{error ?? "Unknown error"}</p>
          <a className="mt-6 inline-flex font-semibold text-blue-600 hover:underline" href="/support">Contact AgenticOrg support</a>
        </div>
      </main>
    );
  }

  const domains = ["all", ...data.domains.map((d) => d.domain)];
  const SCORE_COLS: { key: SortKey; label: string }[] = [
    { key: "quality", label: "Quality" },
    { key: "safety", label: "Safety" },
    { key: "performance", label: "Perf" },
    { key: "reliability", label: "Reliability" },
    { key: "security", label: "Security" },
    { key: "cost", label: "Cost" },
    { key: "composite", label: "Composite" },
  ];

  return (
    <div className="min-h-screen bg-white">
      {/* ---- Header ---- */}
      <header className="bg-slate-900 text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-base">
                AO
              </div>
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">Evaluation Matrix</h1>
                <p className="text-slate-400 text-sm mt-0.5">
                  Last evaluated: {data.meta.evaluatedAt ? formatDate(data.meta.evaluatedAt) : "Not reported"}
                </p>
              </div>
            </div>
            <a
              href="https://agenticorg.ai"
              className="text-sm text-slate-400 hover:text-white transition-colors underline underline-offset-2"
            >
              agenticorg.ai
            </a>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-14">
        {data.meta.dataQuality !== "measured" && (
          <section
            role="alert"
            data-testid={`evals-data-quality-${data.meta.dataQuality}`}
            className="rounded-2xl border-2 border-amber-400 bg-amber-50 p-6"
          >
            <h2 className="text-lg font-bold text-amber-900">
              {DATA_QUALITY_BANNER[data.meta.dataQuality].title}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-amber-900">
              {DATA_QUALITY_BANNER[data.meta.dataQuality].body}
            </p>
          </section>
        )}
        <section className="rounded-2xl border border-blue-200 bg-blue-50 p-6">
          <h2 className="text-lg font-bold text-slate-900">How to interpret this scorecard</h2>
          <p className="mt-2 text-sm leading-relaxed text-slate-700">
            Values are rendered from <code className="rounded bg-white px-1.5 py-0.5">GET /api/v1/evals</code>.
            They describe the reported test cases, evaluator version, and timestamp shown on this page. They are not a production SLA, a guarantee for every prompt, or a substitute for tenant-specific evaluation and human review.
          </p>
        </section>

        <section
          className="rounded-2xl border border-slate-200 bg-slate-50 p-6"
          data-testid="jev-shadow-plan"
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Jev Shadow Evaluation</h2>
              <p className="mt-1 max-w-3xl text-sm leading-relaxed text-slate-600">
                A redacted operator contract for comparing Jev&apos;s typed routing advice with
                AgenticOrg&apos;s existing route. Loading this page does not call Jev, execute tools,
                or change routing authority.
              </p>
            </div>
            <span className="inline-flex w-fit rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
              Advisory only
            </span>
          </div>
          {jevPlanError ? (
            <p className="mt-5 text-sm text-slate-500">Shadow plan unavailable: {jevPlanError}</p>
          ) : jevPlan ? (
            <>
              <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Corpus</p>
                  <p className="mt-1 text-2xl font-bold text-slate-900">{jevPlan.corpus.case_count}</p>
                  <p className="text-xs text-slate-500">synthetic cases</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Effective mode</p>
                  <p className="mt-1 text-lg font-bold text-slate-900">{jevPlan.effective_mode}</p>
                  <p className="text-xs text-slate-500">existing runtime remains authoritative</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Per-run budget</p>
                  <p className="mt-1 text-2xl font-bold text-slate-900">{jevPlan.controls.max_calls_per_run}</p>
                  <p className="text-xs text-slate-500">maximum provider calls</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Evaluation status</p>
                  <p className="mt-1 text-lg font-bold text-slate-900">{jevPlan.reporting.status}</p>
                  <p className="text-xs text-slate-500">explicit CLI run required</p>
                </div>
              </div>
              <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-600">
                <span>Agreement gate: {Math.round(jevPlan.review_gates.minimum_agreement_rate * 100)}%+</span>
                <span>P95 latency gate: {jevPlan.review_gates.maximum_p95_latency_ms} ms</span>
                <span>Sample rate: {Math.round(jevPlan.controls.sample_rate * 100)}%</span>
                <span>Human review: {jevPlan.review_gates.human_review_required ? "required" : "not required"}</span>
              </div>
            </>
          ) : (
            <p className="mt-5 text-sm text-slate-500">Loading shadow plan...</p>
          )}
        </section>

        {/* ---- Section 1: Reported Platform Metrics ---- */}
        <section>
          <h2 className="text-xl font-bold text-slate-900 mb-6">Reported Platform Metrics</h2>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {([
              { label: "Reported Auto-completion Rate", value: data.platform.stpRate },
              { label: "Reported HITL Rate", value: data.platform.hitlRate },
              { label: "Reported Mean Confidence", value: data.platform.meanConfidence },
              { label: "Reported Uptime Metric", value: data.platform.uptimeSla },
            ] as const).map((m) => (
              <div
                key={m.label}
                className={`rounded-xl border-2 p-5 ${metricCardColor(m.value)}`}
              >
                <p className="text-sm font-medium text-slate-600 mb-1">{m.label}</p>
                <p className={`text-3xl font-bold ${metricTextColor(m.value)}`}>
                  {m.value > 1 ? `${m.value}%` : `${(m.value * 100).toFixed(m.value >= 0.99 ? 1 : 0)}%`}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* ---- Section 2: Domain Scores ---- */}
        <section>
          <h2 className="text-xl font-bold text-slate-900 mb-6">Domain Scores</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
            {data.domains.map((d) => (
              <div
                key={d.domain}
                className={`rounded-xl bg-gradient-to-br ${DOMAIN_BG[d.domain] ?? "from-slate-500 to-slate-600"} text-white p-5 shadow-md`}
              >
                <p className="text-sm font-medium opacity-80 capitalize">{d.domain}</p>
                <p className="text-3xl font-bold mt-1">{d.composite > 1 ? d.composite : (d.composite * 100).toFixed(1)}%</p>
                <div className="flex items-center justify-between mt-3">
                  <span className="inline-block bg-white/20 rounded-full px-2.5 py-0.5 text-xs font-semibold">
                    {d.grade}
                  </span>
                  <span className="text-xs opacity-75">{d.agentCount} test cases</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ---- Section 3: Per-Agent Table ---- */}
        <section>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
            <h2 className="text-xl font-bold text-slate-900">Per-Agent Scores</h2>
            <div className="flex flex-wrap gap-2">
              {domains.map((d) => (
                <button
                  key={d}
                  onClick={() => setDomainFilter(d)}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold capitalize transition-colors ${
                    domainFilter === d
                      ? "bg-slate-900 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                  }`}
                >
                  {d === "all" ? "All Domains" : d}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  {([
                    { key: "agent" as SortKey, label: "Agent" },
                    { key: "domain" as SortKey, label: "Domain" },
                    ...SCORE_COLS,
                    { key: "grade" as SortKey, label: "Grade" },
                  ]).map((col) => (
                    <th
                      key={col.key}
                      onClick={() => handleSort(col.key)}
                      className="px-4 py-3 text-left font-semibold text-slate-700 cursor-pointer hover:bg-slate-100 select-none whitespace-nowrap"
                    >
                      {col.label}
                      {sortKey === col.key && (
                        <span className="ml-1 text-xs">{sortAsc ? "\u25B2" : "\u25BC"}</span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredAgents.map((a, i) => (
                  <tr
                    key={a.agent}
                    className={`border-b border-slate-100 ${i % 2 === 0 ? "bg-white" : "bg-slate-50/50"}`}
                  >
                    <td className="px-4 py-3 font-medium text-slate-900 whitespace-nowrap">{a.agent}</td>
                    <td className="px-4 py-3">
                      <span
                        className="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize"
                        style={{
                          backgroundColor: `${DOMAIN_COLORS[a.domain] ?? "#64748b"}20`,
                          color: DOMAIN_COLORS[a.domain] ?? "#64748b",
                        }}
                      >
                        {a.domain}
                      </span>
                    </td>
                    {(["quality", "safety", "performance", "reliability", "security", "cost", "composite"] as const).map((k) => (
                      <td key={k} className="px-4 py-3">
                        <span className={`inline-block rounded-md px-2 py-0.5 text-xs font-semibold ${scoreColor(a[k])}`}>
                          {fmtPct(a[k])}
                        </span>
                      </td>
                    ))}
                    <td className="px-4 py-3">
                      <span className={`inline-block border rounded-md px-2.5 py-0.5 text-xs font-bold ${GRADE_COLORS[a.grade] ?? "bg-slate-100 text-slate-700 border-slate-300"}`}>
                        {a.grade}
                      </span>
                    </td>
                  </tr>
                ))}
                {filteredAgents.length === 0 && (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-slate-400">
                      No agents match the selected filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* ---- Section 4: Comparison Bar Chart ---- */}
        <section>
          <h2 className="text-xl font-bold text-slate-900 mb-6">Agent Comparison</h2>
          <div className="bg-slate-50 rounded-xl border border-slate-200 p-6">
            <ResponsiveContainer width="100%" height={Math.max(400, chartData.length * 36)}>
              <BarChart data={chartData} layout="vertical" margin={{ left: 140, right: 40, top: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 12 }} />
                <Tooltip
                  formatter={(value: any) => [`${value}%`, "Composite Score"]}
                  contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
                />
                <Bar dataKey="composite" radius={[0, 4, 4, 0]} barSize={22}>
                  {chartData.map((entry) => (
                    <Cell key={entry.name} fill={DOMAIN_COLORS[entry.domain] ?? "#64748b"} />
                  ))}
                  <LabelList dataKey="composite" position="right" formatter={(v: any) => `${v}%`} style={{ fontSize: 11 }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <div className="flex flex-wrap gap-4 mt-4 justify-center">
              {Object.entries(DOMAIN_COLORS).map(([domain, color]) => (
                <div key={domain} className="flex items-center gap-1.5">
                  <div className="w-3 h-3 rounded-sm" style={{ backgroundColor: color }} />
                  <span className="text-xs text-slate-600 capitalize">{domain}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ---- Section 5: Methodology ---- */}
        <section>
          <h2 className="text-xl font-bold text-slate-900 mb-6">Methodology</h2>
          <div className="grid md:grid-cols-2 gap-8">
            <div>
              <h3 className="font-semibold text-slate-800 mb-3">Six Evaluation Dimensions</h3>
              <ul className="space-y-2 text-sm text-slate-600">
                <li className="flex gap-2">
                  <span className="font-semibold text-slate-800 w-24 shrink-0">Quality</span>
                  <span>Accuracy, completeness, and correctness of agent outputs against golden test cases.</span>
                </li>
                <li className="flex gap-2">
                  <span className="font-semibold text-slate-800 w-24 shrink-0">Safety</span>
                  <span>Guardrail compliance, PII handling, prompt injection resistance, and output filtering.</span>
                </li>
                <li className="flex gap-2">
                  <span className="font-semibold text-slate-800 w-24 shrink-0">Performance</span>
                  <span>Latency (p50/p95/p99), throughput, and token efficiency under load.</span>
                </li>
                <li className="flex gap-2">
                  <span className="font-semibold text-slate-800 w-24 shrink-0">Reliability</span>
                  <span>Uptime, error rates, retry success, and graceful degradation behavior.</span>
                </li>
                <li className="flex gap-2">
                  <span className="font-semibold text-slate-800 w-24 shrink-0">Security</span>
                  <span>Authentication, authorization, audit logging, and data encryption at rest/in transit.</span>
                </li>
                <li className="flex gap-2">
                  <span className="font-semibold text-slate-800 w-24 shrink-0">Cost</span>
                  <span>Token spend per task, cost-per-resolution, and budget adherence relative to baselines.</span>
                </li>
              </ul>
            </div>

            <div>
              <h3 className="font-semibold text-slate-800 mb-3">Grading Scale</h3>
              <div className="overflow-hidden rounded-lg border border-slate-200">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-50">
                      <th className="px-4 py-2 text-left font-semibold text-slate-700">Grade</th>
                      <th className="px-4 py-2 text-left font-semibold text-slate-700">Composite Range</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { grade: "A+", range: "95 - 100%" },
                      { grade: "A", range: "90 - 94%" },
                      { grade: "B+", range: "85 - 89%" },
                      { grade: "B", range: "80 - 84%" },
                      { grade: "C", range: "70 - 79%" },
                      { grade: "F", range: "Below 70%" },
                    ].map((row) => (
                      <tr key={row.grade} className="border-t border-slate-100">
                        <td className="px-4 py-2">
                          <span className={`inline-block border rounded-md px-2 py-0.5 text-xs font-bold ${GRADE_COLORS[row.grade]}`}>
                            {row.grade}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-slate-600">{row.range}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="mt-4 text-sm text-slate-500">
                Scorecard reports{" "}
                <span className="font-semibold text-slate-700">
                  {data.meta.goldenTestCases.toLocaleString()}
                </span>{" "}
                test cases &middot; Evaluator version {data.meta.version}
              </p>
            </div>
          </div>
        </section>
      </main>

      {/* ---- Footer ---- */}
      <footer className="bg-slate-900 text-slate-400 mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-5 text-sm">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-[10px]">
              AO
            </div>
            <span>AgenticOrg Evaluation Matrix</span>
          </div>
          <ProductOwnership compact />
        </div>
      </footer>
    </div>
  );
}
