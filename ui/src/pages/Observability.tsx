import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";

/** Run timelines (the waterfall of one agent run) and the live workload. Reads only. */

interface RunSummary {
  trace_id: string;
  run_id: string;
  span_id: string;
  name: string;
  agent_id: string | null;
  status: string;
  run_status: string | null;
  started_at: string | null;
  duration_ms: number;
  provider: string | null;
  model: string | null;
  tokens: number | null;
  correlation_id: string | null;
}

interface RunsOut {
  enabled: boolean;
  tracing: boolean;
  runs: RunSummary[];
}

interface SpanEvent {
  name: string;
  offset_ms: number;
  attributes: Record<string, unknown>;
}

interface SpanRow {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  status: string;
  agent_id: string | null;
  offset_ms: number;
  duration_ms: number;
  attributes: Record<string, unknown>;
  events: SpanEvent[];
}

interface RunDetail {
  run_id: string;
  trace_id: string;
  started_at: string | null;
  duration_ms: number;
  spans: SpanRow[];
}

interface Workload {
  generated_at: string;
  tracing_enabled: boolean;
  timeline_enabled: boolean;
  reviews: {
    pending: number | null;
    overdue: number | null;
    soonest_due_at: string | null;
    soonest_seconds_left: number | null;
    error: string | null;
  };
  runs: {
    window_hours: number;
    runs: number | null;
    by_status: Record<string, number> | null;
    p50_duration_ms: number | null;
    error: string | null;
  };
  model_calls: {
    window_hours: number;
    calls: number | null;
    failed: number | null;
    p50_latency_ms: number | null;
    error: string | null;
  };
  guardrails: { window_hours: number; blocked: number | null; transformed: number | null; error: string | null };
}

type Tab = "traces" | "workload";

const WORKLOAD_REFRESH_MS = 15_000;
const cardClass = "rounded-lg border border-slate-200 bg-white p-4 shadow-sm";

function shortName(name: string): string {
  return name.replace(/^agenticorg\./, "");
}

function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatCountdown(seconds: number | null): string {
  if (seconds === null) return "none due";
  if (seconds <= 0) return "due now";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
  }
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

function statusClass(status: string | null): string {
  switch (status) {
    case "completed":
    case "ok":
      return "bg-emerald-100 text-emerald-800";
    case "failed":
    case "error":
    case "guardrail_blocked":
    case "timeout":
      return "bg-red-100 text-red-800";
    case "hitl_triggered":
      return "bg-amber-100 text-amber-800";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

/** Depth of every span by walking parents; a span whose parent is not stored sits at the root. */
function depthsOf(spans: SpanRow[]): Map<string, number> {
  const byId = new Map(spans.map((span) => [span.span_id, span]));
  const depths = new Map<string, number>();
  function depth(span: SpanRow, guard: number): number {
    const known = depths.get(span.span_id);
    if (known !== undefined) return known;
    const parent = span.parent_span_id ? byId.get(span.parent_span_id) : undefined;
    const value = parent && guard < 32 ? depth(parent, guard + 1) + 1 : 0;
    depths.set(span.span_id, value);
    return value;
  }
  spans.forEach((span) => depth(span, 0));
  return depths;
}

function spanSummary(span: SpanRow): string {
  const a = span.attributes;
  const parts: string[] = [];
  if (a["llm.provider"] || a["llm.model"]) parts.push(`${a["llm.provider"] ?? ""}/${a["llm.model"] ?? ""}`);
  if (a["llm.input_tokens"] !== undefined || a["llm.output_tokens"] !== undefined) {
    parts.push(`${a["llm.input_tokens"] ?? 0} in / ${a["llm.output_tokens"] ?? 0} out`);
  }
  if (a["tool.name"]) parts.push(`${a["connector.id"] ?? ""}:${a["tool.name"]} ${a["tool.outcome"] ?? ""}`);
  if (a["search.results"] !== undefined) parts.push(`${a["search.results"]} results`);
  if (a["agent.run.status"]) parts.push(String(a["agent.run.status"]));
  return parts.join(" · ");
}

function eventLabel(event: SpanEvent): string {
  const a = event.attributes;
  if (event.name === "guardrail.outcome") {
    return `guardrail ${a.stage ?? ""} ${a.detector ?? ""} ${a.action ?? ""}${a.applied ? " applied" : ""}`;
  }
  if (event.name === "model_gateway.decision") {
    return `gateway ${a.provider ?? ""}/${a.model ?? ""} ${a.reason ?? ""}`;
  }
  if (event.name === "exception") return `exception ${a["exception.type"] ?? ""}`;
  return event.name;
}

export default function Observability() {
  const [tab, setTab] = useState<Tab>("traces");
  const [error, setError] = useState<string | null>(null);

  const [runs, setRuns] = useState<RunsOut | null>(null);
  const [runsLoading, setRunsLoading] = useState(false);
  const [agentFilter, setAgentFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [workload, setWorkload] = useState<Workload | null>(null);
  const [workloadLoading, setWorkloadLoading] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { limit: "50" };
      if (agentFilter.trim()) params.agent_id = agentFilter.trim();
      const { data } = await api.get("/observability/runs", { params });
      setRuns(data as RunsOut);
    } catch (err) {
      setRuns(null);
      setError(extractApiError(err, "Failed to load run timelines."));
    } finally {
      setRunsLoading(false);
    }
  }, [agentFilter]);

  const loadDetail = useCallback(async (runId: string) => {
    setSelected(runId);
    setDetailLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/observability/runs/${runId}`);
      setDetail(data as RunDetail);
    } catch (err) {
      setDetail(null);
      setError(extractApiError(err, "Failed to load the run timeline."));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const loadWorkload = useCallback(async () => {
    setWorkloadLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/observability/workload");
      const next = data as Workload;
      setWorkload(next);
      setSecondsLeft(next.reviews?.soonest_seconds_left ?? null);
    } catch (err) {
      setWorkload(null);
      setError(extractApiError(err, "Failed to load the workload."));
    } finally {
      setWorkloadLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === "traces") void loadRuns();
  }, [tab, loadRuns]);

  useEffect(() => {
    if (tab !== "workload") return;
    void loadWorkload();
    const refresh = window.setInterval(() => void loadWorkload(), WORKLOAD_REFRESH_MS);
    return () => window.clearInterval(refresh);
  }, [tab, loadWorkload]);

  const hasDeadline = secondsLeft !== null;
  useEffect(() => {
    if (tab !== "workload" || !hasDeadline) return;
    const tick = window.setInterval(() => setSecondsLeft((value) => (value === null ? null : Math.max(0, value - 1))), 1000);
    return () => window.clearInterval(tick);
  }, [tab, hasDeadline]);

  const depths = useMemo(() => (detail ? depthsOf(detail.spans) : new Map<string, number>()), [detail]);
  const total = detail && detail.duration_ms > 0 ? detail.duration_ms : 1;

  const tabClass = (name: Tab) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${tab === name ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`;

  return (
    <div className="space-y-4" data-testid="observability-page">
      <Helmet>
        <title>Observability | AgenticOrg</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Observability</h1>
          <p className="text-sm text-slate-600">
            The waterfall of one agent run and the live workload. Identifiers, timings and outcomes only; never content.
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className={tabClass("traces")} onClick={() => setTab("traces")} data-testid="tab-traces">
            Traces
          </button>
          <button type="button" className={tabClass("workload")} onClick={() => setTab("workload")} data-testid="tab-workload">
            Workload
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}

      {tab === "traces" && (
        <div className="space-y-4">
          {runs && !runs.enabled && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800" data-testid="timeline-off">
              Run timelines are not being recorded in this deployment
              {runs.tracing ? " (tracing is on; set AGENTICORG_TRACING_TIMELINE_ENABLED)" : " (tracing is off)"}.
              Runs that were recorded earlier still appear below.
            </div>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-sm text-slate-700">
              Agent id
              <input
                className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm"
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                data-testid="trace-agent-filter"
              />
            </label>
            <button
              type="button"
              className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
              onClick={() => void loadRuns()}
              data-testid="traces-refresh"
            >
              Refresh
            </button>
            {runsLoading && <span className="text-sm text-slate-500">Loading…</span>}
          </div>

          <div className={cardClass}>
            <table className="w-full text-sm" data-testid="traces-table">
              <thead>
                <tr className="text-left text-slate-500">
                  <th className="py-1">Started</th>
                  <th>Agent</th>
                  <th>Run</th>
                  <th>Status</th>
                  <th>Model</th>
                  <th className="text-right">Tokens</th>
                  <th className="text-right">Duration</th>
                </tr>
              </thead>
              <tbody>
                {runs && runs.runs.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-3 text-slate-500">
                      No stored runs.
                    </td>
                  </tr>
                )}
                {runs?.runs.map((row) => (
                  <tr
                    key={row.span_id}
                    className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === row.run_id ? "bg-slate-50" : ""}`}
                    onClick={() => void loadDetail(row.run_id)}
                    data-testid={`trace-row-${row.run_id}`}
                  >
                    <td className="py-1">{row.started_at ? new Date(row.started_at).toLocaleString() : "–"}</td>
                    <td className="font-mono text-xs">{row.agent_id ?? "–"}</td>
                    <td>{shortName(row.name)}</td>
                    <td>
                      <span className={`rounded px-1.5 py-0.5 text-xs ${statusClass(row.run_status ?? row.status)}`}>
                        {row.run_status ?? row.status}
                      </span>
                    </td>
                    <td className="font-mono text-xs">{row.provider || row.model ? `${row.provider ?? ""}/${row.model ?? ""}` : "–"}</td>
                    <td className="text-right">{row.tokens ?? "–"}</td>
                    <td className="text-right">{formatMs(row.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {detailLoading && <p className="text-sm text-slate-500">Loading timeline…</p>}
          {detail && (
            <div className={cardClass} data-testid="waterfall">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-mono text-xs text-slate-600">
                  run {detail.run_id} · trace {detail.trace_id}
                </span>
                <span className="text-slate-600">
                  {detail.spans.length} spans · {formatMs(detail.duration_ms)}
                </span>
              </div>
              <div className="space-y-1">
                {detail.spans.map((span) => {
                  const depth = depths.get(span.span_id) ?? 0;
                  const left = Math.min(100, (span.offset_ms / total) * 100);
                  const width = Math.max(0.5, Math.min(100 - left, (span.duration_ms / total) * 100));
                  return (
                    <div key={span.span_id} className="grid grid-cols-12 items-center gap-2 text-xs" data-testid={`span-row-${span.span_id}`}>
                      <div className="col-span-4 truncate" style={{ paddingLeft: `${depth * 12}px` }} title={span.name}>
                        <span className="font-medium text-slate-800">{shortName(span.name)}</span>
                        <span className="ml-1 text-slate-500">{spanSummary(span)}</span>
                        {span.events.length > 0 && (
                          <div className="mt-0.5 flex flex-wrap gap-1">
                            {span.events.map((event, index) => (
                              <span key={`${span.span_id}-${index}`} className="rounded bg-slate-100 px-1 text-[10px] text-slate-700" data-testid="span-event">
                                {eventLabel(event)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="col-span-6 h-4 rounded bg-slate-100">
                        <div
                          className={`h-4 rounded ${span.status === "error" ? "bg-red-400" : span.name.includes("reason") ? "bg-indigo-400" : span.name.includes("tool") ? "bg-emerald-400" : span.name.includes("knowledge") ? "bg-sky-400" : "bg-slate-500"}`}
                          style={{ marginLeft: `${left}%`, width: `${width}%` }}
                          data-testid="span-bar"
                        />
                      </div>
                      <div className="col-span-2 text-right">
                        <span className={`mr-1 rounded px-1 ${statusClass(span.status)}`}>{span.status}</span>
                        {formatMs(span.duration_ms)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "workload" && (
        <div className="space-y-4" data-testid="workload">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <button
              type="button"
              className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
              onClick={() => void loadWorkload()}
              data-testid="workload-refresh"
            >
              Refresh
            </button>
            {workloadLoading && <span>Loading…</span>}
            {workload && <span>as of {new Date(workload.generated_at).toLocaleTimeString()}</span>}
          </div>
          {workload && (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div className={cardClass} data-testid="workload-reviews">
                <h2 className="mb-2 text-sm font-semibold text-slate-800">Reviews</h2>
                {workload.reviews.error && <p className="text-sm text-red-700">Unavailable ({workload.reviews.error})</p>}
                {!workload.reviews.error && (
                  <dl className="grid grid-cols-2 gap-1 text-sm">
                    <dt className="text-slate-500">Pending</dt>
                    <dd data-testid="reviews-pending">{workload.reviews.pending ?? "–"}</dd>
                    <dt className="text-slate-500">Overdue</dt>
                    <dd data-testid="reviews-overdue" className={workload.reviews.overdue ? "text-red-700" : ""}>
                      {workload.reviews.overdue ?? "–"}
                    </dd>
                    <dt className="text-slate-500">Soonest deadline</dt>
                    <dd data-testid="reviews-countdown">{formatCountdown(secondsLeft)}</dd>
                  </dl>
                )}
              </div>
              <div className={cardClass} data-testid="workload-runs">
                <h2 className="mb-2 text-sm font-semibold text-slate-800">Runs, last {workload.runs.window_hours}h</h2>
                {workload.runs.error && <p className="text-sm text-red-700">Unavailable ({workload.runs.error})</p>}
                {!workload.runs.error && (
                  <dl className="grid grid-cols-2 gap-1 text-sm">
                    <dt className="text-slate-500">Runs</dt>
                    <dd data-testid="runs-total">{workload.runs.runs ?? "–"}</dd>
                    {Object.entries(workload.runs.by_status ?? {}).map(([status, count]) => (
                      <Fragment key={status}>
                        <dt className="text-slate-500">{status}</dt>
                        <dd>{count}</dd>
                      </Fragment>
                    ))}
                    <dt className="text-slate-500">p50 duration</dt>
                    <dd>{formatMs(workload.runs.p50_duration_ms)}</dd>
                  </dl>
                )}
                {!workload.timeline_enabled && (
                  <p className="mt-2 text-xs text-slate-500">Run timelines are off; only recorded runs count.</p>
                )}
              </div>
              <div className={cardClass} data-testid="workload-model-calls">
                <h2 className="mb-2 text-sm font-semibold text-slate-800">Model calls, last {workload.model_calls.window_hours}h</h2>
                {workload.model_calls.error && <p className="text-sm text-red-700">Unavailable ({workload.model_calls.error})</p>}
                {!workload.model_calls.error && (
                  <dl className="grid grid-cols-2 gap-1 text-sm">
                    <dt className="text-slate-500">Calls</dt>
                    <dd data-testid="model-calls-total">{workload.model_calls.calls ?? "–"}</dd>
                    <dt className="text-slate-500">Failed</dt>
                    <dd>{workload.model_calls.failed ?? "–"}</dd>
                    <dt className="text-slate-500">p50 latency</dt>
                    <dd>{formatMs(workload.model_calls.p50_latency_ms)}</dd>
                  </dl>
                )}
              </div>
              <div className={cardClass} data-testid="workload-guardrails">
                <h2 className="mb-2 text-sm font-semibold text-slate-800">Guardrails, last {workload.guardrails.window_hours}h</h2>
                {workload.guardrails.error && <p className="text-sm text-red-700">Unavailable ({workload.guardrails.error})</p>}
                {!workload.guardrails.error && (
                  <dl className="grid grid-cols-2 gap-1 text-sm">
                    <dt className="text-slate-500">Blocked</dt>
                    <dd data-testid="guardrails-blocked">{workload.guardrails.blocked ?? "–"}</dd>
                    <dt className="text-slate-500">Transformed</dt>
                    <dd>{workload.guardrails.transformed ?? "–"}</dd>
                  </dl>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
