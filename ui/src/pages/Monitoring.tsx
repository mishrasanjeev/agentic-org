// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Fragment, useCallback, useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";
import SyntheticChecksPanel from "@/components/observability/SyntheticChecksPanel";
import { Link } from "react-router";

/**
 * Monitoring (administrators): the live workload and the synthetic checks. Run timelines live in Insights (the
 * runs explorer and each run's page, with the run debugger); this page links there.
 */

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

type Tab = "workload" | "checks";

const WORKLOAD_REFRESH_MS = 15_000;
const cardClass = "rounded-lg border border-slate-200 bg-white p-4 shadow-sm";


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


/** Depth of every span by walking parents; a span whose parent is not stored sits at the root. */



export default function Monitoring() {
  const [tab, setTab] = useState<Tab>("workload");
  const [error, setError] = useState<string | null>(null);

  const [workload, setWorkload] = useState<Workload | null>(null);
  const [workloadLoading, setWorkloadLoading] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  // The Checks tab exists only where synthetic checks are switched on for the deployment.
  const [checksOn, setChecksOn] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .get("/observability/checks")
      .then((response) => {
        if (live) setChecksOn(Boolean((response.data as { enabled?: boolean } | undefined)?.enabled));
      })
      .catch(() => {
        if (live) setChecksOn(false);
      });
    return () => {
      live = false;
    };
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

  const tabClass = (name: Tab) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${tab === name ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`;

  return (
    <div className="space-y-4" data-testid="monitoring-page">
      <Helmet>
        <title>Monitoring | AgenticOrg</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Monitoring</h1>
          <p className="text-sm text-slate-600">
            The live workload and the synthetic checks. One run's timeline and its debugger are on its{" "}
            <Link to="/dashboard/insights/explore/runs" className="text-blue-700 underline">
              Insights run page
            </Link>
            .
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className={tabClass("workload")} onClick={() => setTab("workload")} data-testid="tab-workload">
            Workload
          </button>
          {checksOn && (
            <button type="button" className={tabClass("checks")} onClick={() => setTab("checks")} data-testid="tab-checks">
              Checks
            </button>
          )}
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
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

      {tab === "checks" && checksOn && <SyntheticChecksPanel />}
    </div>
  );
}
