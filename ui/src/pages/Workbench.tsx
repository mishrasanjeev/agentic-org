// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useParams } from "react-router";
import api, { extractApiError } from "@/lib/api";
import BusinessConsole from "@/components/BusinessConsole";
import ReviewQueue from "@/components/ReviewQueue";

/**
 * Workbench shell: the role-shaped consoles a person works from. The index lists the caller's
 * workbenches; a workbench shows its tabs with the number of items waiting behind each. A tab that
 * lives in the shell (content drafts) renders here; every other tab opens the page it names, which
 * authorises on its own.
 */

export interface TabRow {
  key: string;
  title: string;
  path: string;
  source: string;
  sensitive: boolean;
  actions: string[];
}

export interface WorkbenchRow {
  name: string;
  title: string;
  description: string;
  tabs: TabRow[];
  held_by: string;
}

export interface WorkbenchSummary extends WorkbenchRow {
  counts: Record<string, number | null>;
  waiting: number;
}

interface DraftRow {
  id: string;
  service: string;
  kind: string;
  status: string;
  title: string;
  created_by: string | null;
  created_at: string | null;
}

export const SHELL_PREFIX = "/dashboard/workbench/";

/** A tab whose page is the shell itself, keyed by its path's last segment. */
export function shellPanelOf(tab: TabRow): string | null {
  if (!tab.path.startsWith(SHELL_PREFIX)) return null;
  const parts = tab.path.slice(SHELL_PREFIX.length).split("/");
  return parts.length === 2 ? parts[1] : null;
}

export function countLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value > 999 ? "999+" : String(value);
}

function DraftsPanel() {
  const [drafts, setDrafts] = useState<DraftRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const { data } = await api.get("/content/drafts", { params: { status: "pending_approval", limit: "50" } });
      setDrafts((data as { drafts: DraftRow[] }).drafts);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the drafts."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusy(id);
    setError(null);
    try {
      await api.post(`/content/drafts/${id}/decide`, { decision, notes: "" });
      await load();
    } catch (err) {
      setError(extractApiError(err, "The decision was not recorded."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-2" data-testid="workbench-drafts">
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {drafts.length === 0 && !error && <p className="text-sm text-slate-500">No drafts wait for a decision.</p>}
      <ul className="divide-y divide-slate-200 rounded-md border border-slate-200">
        {drafts.map((draft) => (
          <li key={draft.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm" data-testid="workbench-draft">
            <div>
              <div className="font-medium text-slate-900">{draft.title || draft.kind}</div>
              <div className="text-xs text-slate-500">
                {draft.service} · {draft.created_by || "unknown author"} · {draft.created_at ? new Date(draft.created_at).toLocaleString() : ""}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className="rounded-md bg-emerald-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                disabled={busy === draft.id}
                onClick={() => void decide(draft.id, "approve")}
              >
                Approve
              </button>
              <button
                type="button"
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 disabled:opacity-50"
                disabled={busy === draft.id}
                onClick={() => void decide(draft.id, "reject")}
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function Workbench() {
  const { name, tab } = useParams();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [workbenches, setWorkbenches] = useState<WorkbenchRow[]>([]);
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadIndex = useCallback(async () => {
    setError(null);
    try {
      const { data } = await api.get("/workbench");
      const payload = data as { enabled: boolean; workbenches: WorkbenchRow[] };
      setEnabled(payload.enabled);
      setWorkbenches(payload.workbenches || []);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the workbenches."));
    }
  }, []);

  const loadSummary = useCallback(async (which: string) => {
    setError(null);
    try {
      const { data } = await api.get(`/workbench/${encodeURIComponent(which)}/summary`);
      setSummary(data as WorkbenchSummary);
    } catch (err) {
      setSummary(null);
      setError(extractApiError(err, "Failed to load the workbench."));
    }
  }, []);

  useEffect(() => {
    void loadIndex();
  }, [loadIndex]);

  useEffect(() => {
    if (name) void loadSummary(name);
    else setSummary(null);
  }, [name, loadSummary]);

  const current = useMemo(() => {
    if (!summary) return null;
    return summary.tabs.find((t) => t.key === tab) || summary.tabs[0] || null;
  }, [summary, tab]);

  const panel = current ? shellPanelOf(current) : null;

  return (
    <div className="space-y-4 p-4">
      <Helmet>
        <title>{summary ? `${summary.title} workbench` : "Workbenches"}</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-slate-900">
          {summary ? (
            <>
              <Link to="/dashboard/workbench" className="text-indigo-700 hover:underline">
                Workbenches
              </Link>
              <span className="text-slate-400"> / </span>
              {summary.title}
            </>
          ) : (
            "Workbenches"
          )}
        </h1>
        {summary && (
          <button
            type="button"
            className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
            onClick={() => name && void loadSummary(name)}
            data-testid="workbench-refresh"
          >
            Refresh counts
          </button>
        )}
      </div>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {enabled === false && (
        <p className="text-sm text-slate-600" data-testid="workbench-off">
          Workbenches are off for this deployment.
        </p>
      )}
      {enabled && !name && (
        <div className="grid gap-3 md:grid-cols-2" data-testid="workbench-index">
          {workbenches.length === 0 && <p className="text-sm text-slate-500">No workbench is held by your role or assigned to you.</p>}
          {workbenches.map((bench) => (
            <Link
              key={bench.name}
              to={`/dashboard/workbench/${bench.name}`}
              className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm hover:border-indigo-400"
              data-testid="workbench-card"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-slate-900">{bench.title}</h2>
                <span className="text-xs text-slate-500">{bench.held_by === "assignment" ? "assigned" : "by role"}</span>
              </div>
              <p className="mt-1 text-sm text-slate-600">{bench.description}</p>
              <p className="mt-2 text-xs text-slate-500">{bench.tabs.map((t) => t.title).join(" · ")}</p>
            </Link>
          ))}
        </div>
      )}
      {enabled && name && summary && (
        <div className="space-y-3">
          <p className="text-sm text-slate-600">{summary.description}</p>
          <nav className="flex flex-wrap gap-1 border-b border-slate-200" aria-label="Workbench tabs" data-testid="workbench-tabs">
            {summary.tabs.map((t) => {
              const active = current?.key === t.key;
              return (
                <Link
                  key={t.key}
                  to={`/dashboard/workbench/${summary.name}/${t.key}`}
                  className={`-mb-px rounded-t-md border px-3 py-1.5 text-sm ${active ? "border-slate-200 border-b-white bg-white font-medium text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900"}`}
                  aria-current={active ? "page" : undefined}
                >
                  {t.title}
                  <span className="ml-2 rounded-full bg-slate-100 px-1.5 text-xs text-slate-700" data-testid={`workbench-count-${t.key}`}>
                    {countLabel(summary.counts[t.key])}
                  </span>
                </Link>
              );
            })}
          </nav>
          {current && panel === "drafts" && <DraftsPanel />}
          {current && panel === "queue" && <ReviewQueue />}
          {current && panel === "console" && <BusinessConsole />}
          {current && panel === null && (
            <div className="rounded-md border border-slate-200 bg-white p-4 text-sm" data-testid="workbench-open">
              <p className="text-slate-700">
                {countLabel(summary.counts[current.key])} waiting in {current.title}
                {current.actions.length > 0 ? ` · you may ${current.actions.join(", ")}` : ""}
                {current.sensitive ? " · sensitive" : ""}
              </p>
              <Link to={current.path} className="mt-2 inline-block rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white">
                Open {current.title}
              </Link>
            </div>
          )}
          {current && panel !== null && panel !== "drafts" && panel !== "queue" && panel !== "console" && (
            <p className="text-sm text-slate-500" data-testid="workbench-pending">
              {current.title} arrives with the next workbench release.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
