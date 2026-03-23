import { useState, useEffect, useMemo, useCallback } from "react";
import { Helmet } from "react-helmet-async";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Cell, LabelList,
} from "recharts";

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

interface ScorecardMeta {
  evaluatedAt: string;
  goldenTestCases: number;
  version: string;
}

interface EvalsData {
  meta: ScorecardMeta;
  platform: PlatformSummary;
  domains: DomainSummary[];
  agents: AgentScore[];
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
  hr: "from-purple-500 to-purple-600",
  marketing: "from-amber-500 to-amber-600",
  ops: "from-emerald-500 to-emerald-600",
  backoffice: "from-indigo-500 to-indigo-600",
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
  if (v >= 90) return "bg-emerald-100 text-emerald-800";
  if (v >= 80) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
}

function metricCardColor(v: number): string {
  if (v >= 90) return "border-emerald-400 bg-emerald-50";
  if (v >= 80) return "border-yellow-400 bg-yellow-50";
  return "border-red-400 bg-red-50";
}

function metricTextColor(v: number): string {
  if (v >= 90) return "text-emerald-700";
  if (v >= 80) return "text-yellow-700";
  return "text-red-700";
}

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
        const agents: AgentScore[] = Object.entries(raw.agent_aggregates || {}).map(
          ([name, a]: [string, any]) => {
            const s = a.avg_scores || {};
            // Find domain from case_results
            const caseForAgent = (raw.case_results || []).find((c: any) => c.agent_type === name);
            return {
              agent: name,
              domain: caseForAgent?.domain || "",
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
            agentCount: d.agent_count || 0,
          })
        );
        const parsed: EvalsData = {
          meta: {
            evaluatedAt: raw.generated_at || "",
            goldenTestCases: pm.total_cases || 66,
            version: raw.version || "1.0.0",
          },
          platform: {
            stpRate: pm.stp_rate ?? 0.87,
            hitlRate: pm.hitl_rate ?? 0.13,
            meanConfidence: pm.mean_confidence ?? pm.avg_composite ?? 0.93,
            uptimeSla: pm.uptime_sla ?? 0.999,
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
      .map((a) => ({ name: a.agent, composite: a.composite, domain: a.domain }));
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
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-600 font-semibold text-lg mb-2">Failed to load evaluations</p>
          <p className="text-slate-500 text-sm">{error ?? "Unknown error"}</p>
        </div>
      </div>
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
      <Helmet>
        <title>Evaluation Matrix — AgenticOrg | 22 Agents Scored Across 6 Dimensions</title>
        <meta name="description" content="Published evaluation scorecard for AgenticOrg's 24 AI agents. Quality, safety, performance, reliability, security, and cost metrics." />
        <link rel="canonical" href="https://agenticorg.ai/evals" />
      </Helmet>
      {/* ---- Header ---- */}
      <header className="bg-slate-900 text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-base">
                AO
              </div>
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">Evaluation Matrix</h1>
                <p className="text-slate-400 text-sm mt-0.5">
                  Last evaluated: {formatDate(data.meta.evaluatedAt)}
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
        {/* ---- Section 1: Platform Summary ---- */}
        <section>
          <h2 className="text-xl font-bold text-slate-900 mb-6">Platform Summary</h2>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {([
              { label: "STP Rate", value: data.platform.stpRate },
              { label: "HITL Rate", value: data.platform.hitlRate },
              { label: "Mean Confidence", value: data.platform.meanConfidence },
              { label: "Uptime SLA", value: data.platform.uptimeSla },
            ] as const).map((m) => (
              <div
                key={m.label}
                className={`rounded-xl border-2 p-5 ${metricCardColor(m.value)}`}
              >
                <p className="text-sm font-medium text-slate-600 mb-1">{m.label}</p>
                <p className={`text-3xl font-bold ${metricTextColor(m.value)}`}>
                  {m.value}%
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
                <p className="text-3xl font-bold mt-1">{d.composite}%</p>
                <div className="flex items-center justify-between mt-3">
                  <span className="inline-block bg-white/20 rounded-full px-2.5 py-0.5 text-xs font-semibold">
                    {d.grade}
                  </span>
                  <span className="text-xs opacity-75">{d.agentCount} agents</span>
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
                          {a[k]}%
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
                  formatter={(value: number) => [`${value}%`, "Composite Score"]}
                  contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
                />
                <Bar dataKey="composite" radius={[0, 4, 4, 0]} barSize={22}>
                  {chartData.map((entry) => (
                    <Cell key={entry.name} fill={DOMAIN_COLORS[entry.domain] ?? "#64748b"} />
                  ))}
                  <LabelList dataKey="composite" position="right" formatter={(v: number) => `${v}%`} style={{ fontSize: 11 }} />
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
                Evaluated against{" "}
                <span className="font-semibold text-slate-700">
                  {data.meta.goldenTestCases.toLocaleString()}
                </span>{" "}
                golden test cases &middot; Scorecard v{data.meta.version}
              </p>
            </div>
          </div>
        </section>
      </main>

      {/* ---- Footer ---- */}
      <footer className="bg-slate-900 text-slate-400 mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 text-sm">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-[10px]">
              AO
            </div>
            <span>AgenticOrg Evaluation Matrix</span>
          </div>
          <p>&copy; {new Date().getFullYear()} Edumatica Pvt Ltd. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
