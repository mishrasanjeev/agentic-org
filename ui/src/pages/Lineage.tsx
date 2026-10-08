// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ScanSearch } from "lucide-react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { APPROVAL_ROLES } from "@/lib/roles";

/**
 * Lineage: where a kept thing came from and what was made of it. Find a node, trace it upstream to its
 * sources or downstream to what used it, see each version and the processing history, and look after
 * the sync sources that feed the platform.
 */

export interface LineageNode {
  id: string;
  kind: string;
  ref: string;
  source: string;
  version: string;
  observed_at: string | null;
  attributes: Record<string, unknown>;
}

export interface LineageStep {
  id: string;
  from_node: string;
  to_node: string;
  step: string;
  tool: string;
  params_hash: string;
  at: string | null;
}

interface Trace {
  root: LineageNode;
  direction: string;
  hops: number;
  nodes: LineageNode[];
  steps: LineageStep[];
  truncated: boolean;
}

interface Description {
  node: LineageNode;
  versions: LineageNode[];
  sources: Array<{ kind: string; ref: string; version: string; declared?: boolean }>;
  history: Array<{ step: string; tool: string; at: string | null; from: { kind: string; ref: string }; to: { kind: string; ref: string } }>;
  complete: boolean;
  truncated: boolean;
}

interface SyncSource {
  id: string;
  name: string;
  kind: string;
  url: string;
  item_kind: string;
  interval_minutes: number;
  enabled: boolean;
  cursor: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  running?: boolean;
}

interface SyncRun {
  id: string;
  trigger: string;
  status: string;
  started_at: string | null;
  received: number;
  processed: number;
  skipped: number;
  failed: number;
  errors: string[];
}

/** Columns from origin to use, so a trace reads left to right. */
export const KIND_ORDER = ["source", "document", "record", "transcript", "chunk", "embedding", "finding", "draft", "model_use"];
const COLUMN = 150;
const ROW = 44;
const MARGIN = 70;

/** Node positions: a column per kind present (in KIND_ORDER), a row per node within its column. */
export function layout(nodes: LineageNode[]): { positions: Record<string, { x: number; y: number }>; width: number; height: number } {
  const kinds = KIND_ORDER.filter((kind) => nodes.some((n) => n.kind === kind));
  for (const node of nodes) if (!kinds.includes(node.kind)) kinds.push(node.kind);
  const rows: Record<string, number> = {};
  const positions: Record<string, { x: number; y: number }> = {};
  for (const node of nodes) {
    const column = kinds.indexOf(node.kind);
    const row = rows[node.kind] ?? 0;
    rows[node.kind] = row + 1;
    positions[node.id] = { x: MARGIN + column * COLUMN, y: 40 + row * ROW };
  }
  const deepest = Math.max(1, ...Object.values(rows));
  return { positions, width: MARGIN * 2 + Math.max(0, kinds.length - 1) * COLUMN, height: 60 + deepest * ROW };
}

/** A reference shortened for a node label: the end of a URL or path is what tells two apart. */
export function shortRef(ref: string, max = 22): string {
  if (ref.length <= max) return ref;
  return `…${ref.slice(ref.length - (max - 1))}`;
}

function when(value: string | null | undefined): string {
  return value ? value.replace("T", " ").slice(0, 19) : "—";
}

export default function Lineage() {
  const auth = useAuth();
  // Running a sync source is a write (approvals:write); every other role here only reads.
  const canWrite = APPROVAL_ROLES.includes(auth.user?.role || "");
  const [kinds, setKinds] = useState<string[]>(KIND_ORDER);
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<LineageNode[]>([]);
  const [direction, setDirection] = useState("both");
  const [hops, setHops] = useState(4);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [selected, setSelected] = useState<LineageNode | null>(null);
  const [description, setDescription] = useState<Description | null>(null);
  const descriptionRequest = useRef(0);
  const [sources, setSources] = useState<SyncSource[]>([]);
  const [runs, setRuns] = useState<{ sourceId: string; runs: SyncRun[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const search = useCallback(async () => {
    setError(null);
    try {
      const params: Record<string, string> = { limit: "50" };
      if (kind) params.kind = kind;
      if (query.trim()) params.q = query.trim();
      const { data } = await api.get("/lineage/nodes", { params });
      setFound((data as { nodes: LineageNode[] }).nodes);
    } catch (err) {
      setError(extractApiError(err, "Failed to search the lineage."));
    }
  }, [kind, query]);

  const loadSources = useCallback(async () => {
    try {
      const { data } = await api.get("/lineage/sync/sources");
      setSources((data as { sources: SyncSource[] }).sources);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the sync sources."));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const { data } = await api.get("/lineage/status");
        const listed = (data as { kinds?: string[] }).kinds;
        if (Array.isArray(listed) && listed.length) setKinds(listed);
      } catch {
        // The status only narrows the kind list; the defaults stand without it.
      }
    })();
    void search();
    void loadSources();
    // Only on mount: later searches run from the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const describe = useCallback(async (node: LineageNode) => {
    const request = ++descriptionRequest.current;
    setSelected(node);
    setDescription(null);
    try {
      const { data } = await api.get(`/lineage/nodes/${encodeURIComponent(node.kind)}/${encodeURIComponent(node.ref)}`, {
        params: node.version ? { version: node.version } : {},
      });
      if (request === descriptionRequest.current) setDescription(data as Description);
    } catch (err) {
      if (request === descriptionRequest.current) setError(extractApiError(err, "Failed to describe the node."));
    }
  }, []);

  const traceFrom = useCallback(
    async (node: LineageNode) => {
      setBusy(true);
      setError(null);
      try {
        const { data } = await api.get(`/lineage/trace/${encodeURIComponent(node.kind)}/${encodeURIComponent(node.ref)}`, {
          params: { direction, hops: String(hops), ...(node.version ? { version: node.version } : {}) },
        });
        setTrace(data as Trace);
        await describe((data as Trace).root);
      } catch (err) {
        setTrace(null);
        setError(extractApiError(err, "Failed to trace the lineage."));
      } finally {
        setBusy(false);
      }
    },
    [direction, hops, describe],
  );

  const runSource = async (source: SyncSource) => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post(`/lineage/sync/sources/${source.id}/run`);
      const run = data as SyncRun;
      setNotice(`${source.name}: ${run.status}, ${run.processed} processed, ${run.skipped} unchanged, ${run.failed} failed.`);
      await loadSources();
    } catch (err) {
      setError(extractApiError(err, "The sync run did not start."));
    } finally {
      setBusy(false);
    }
  };

  const showRuns = async (source: SyncSource) => {
    try {
      const { data } = await api.get(`/lineage/sync/sources/${source.id}/runs`, { params: { limit: "10" } });
      setRuns({ sourceId: source.id, runs: (data as { runs: SyncRun[] }).runs });
    } catch (err) {
      setError(extractApiError(err, "Failed to load the runs."));
    }
  };

  const drawn = useMemo(() => (trace ? layout(trace.nodes) : null), [trace]);

  return (
    <div className="space-y-4 p-4">
      <Helmet>
        <title>Lineage</title>
      </Helmet>
      <h1 className="text-xl font-semibold text-slate-900">Lineage</h1>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="lineage-notice">
          {notice}
        </div>
      )}
      <section aria-label="Find" className="space-y-2">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <label className="text-sm text-slate-700">
            Kind
            <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={kind} onChange={(e) => setKind(e.target.value)} data-testid="lineage-kind">
              <option value="">All</option>
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Reference contains
            <input className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={query} onChange={(e) => setQuery(e.target.value)} data-testid="lineage-query" />
          </label>
          <label className="text-sm text-slate-700">
            Direction
            <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={direction} onChange={(e) => setDirection(e.target.value)} data-testid="lineage-direction">
              <option value="both">Both</option>
              <option value="upstream">Upstream (sources)</option>
              <option value="downstream">Downstream (uses)</option>
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Hops
            <input type="number" min={1} max={8} className="ml-2 w-16 rounded-md border border-slate-300 px-2 py-1 text-sm" value={hops} onChange={(e) => setHops(Math.max(1, Math.min(8, Number(e.target.value) || 1)))} data-testid="lineage-hops" />
          </label>
          <button type="submit" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white" data-testid="lineage-search">
            Search
          </button>
        </form>
        <ul className="max-h-56 divide-y divide-slate-200 overflow-auto rounded-md border border-slate-200" data-testid="lineage-found">
          {found.length === 0 && <li className="px-3 py-2 text-sm text-slate-500">No provenance kept yet for this search.</li>}
          {found.map((node) => (
            <li key={node.id}>
              <button type="button" className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm hover:bg-slate-50" onClick={() => void traceFrom(node)} disabled={busy} data-testid="lineage-found-node">
                <span>
                  <span className="mr-2 rounded bg-slate-100 px-1.5 text-xs uppercase text-slate-600">{node.kind}</span>
                  <span className="text-slate-900">{node.ref}</span>
                </span>
                <span className="shrink-0 text-xs text-slate-500">{node.version ? shortRef(node.version, 16) : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      {trace && drawn && (
        <section aria-label="Graph" className="grid gap-3 lg:grid-cols-12" data-testid="lineage-graph">
          <div className="overflow-x-auto rounded-md border border-slate-200 bg-white lg:col-span-8">
            {trace.truncated && <p className="px-3 pt-2 text-xs text-amber-800">The trace was cut at its bounds; more lineage exists beyond it.</p>}
            <svg role="img" aria-label={`Lineage of ${trace.root.ref}`} width={drawn.width} height={drawn.height} viewBox={`0 0 ${drawn.width} ${drawn.height}`}>
              {trace.steps.map((step) => {
                const a = drawn.positions[step.from_node];
                const b = drawn.positions[step.to_node];
                if (!a || !b) return null;
                return (
                  <g key={step.id}>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#6366f1" strokeOpacity="0.6" strokeWidth={1.5} />
                    <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} fontSize="9" fill="#475569" textAnchor="middle">
                      {step.step}
                    </text>
                  </g>
                );
              })}
              {trace.nodes.map((node) => {
                const p = drawn.positions[node.id];
                if (!p) return null;
                const isRoot = node.id === trace.root.id;
                return (
                  <g key={node.id} onClick={() => void describe(node)} onDoubleClick={() => void traceFrom(node)} style={{ cursor: "pointer" }} data-testid="lineage-node">
                    <circle cx={p.x} cy={p.y} r={isRoot ? 11 : 8} fill={isRoot ? "#4338ca" : node.kind === "source" ? "#059669" : "#94a3b8"} />
                    <text x={p.x} y={p.y + 19} fontSize="9" fill="#0f172a" textAnchor="middle">
                      {shortRef(node.ref)}
                    </text>
                  </g>
                );
              })}
            </svg>
            <ul aria-label="Graph nodes" className="max-h-48 divide-y divide-slate-200 overflow-auto border-t border-slate-200">
              {trace.nodes.map((node) => (
                <li key={node.id} className="flex items-center gap-2 px-3 py-1">
                  <button type="button" aria-label={`Inspect ${node.kind} ${node.ref}`} aria-pressed={selected?.id === node.id} className="min-h-11 min-w-0 flex-1 break-all text-left text-xs text-slate-800 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600" onClick={() => void describe(node)}>
                    {node.kind} · {node.ref}
                  </button>
                  <button type="button" aria-label={`Trace from ${node.kind} ${node.ref}`} title={`Trace from ${node.kind} ${node.ref}`} disabled={busy} className="flex h-11 w-11 shrink-0 items-center justify-center rounded border border-slate-300 text-indigo-700 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600" onClick={() => void traceFrom(node)}>
                    <ScanSearch size={16} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3 text-sm lg:col-span-4" data-testid="lineage-detail">
            {selected ? (
              <>
                <p className="font-medium text-slate-900">
                  {selected.kind} · <span className="break-all">{selected.ref}</span>
                </p>
                <p className="text-xs text-slate-600">
                  Version {selected.version || "—"} · observed {when(selected.observed_at)}
                </p>
                {description && (
                  <>
                    <p className={`text-xs ${description.complete ? "text-emerald-800" : "text-amber-800"}`} data-testid="lineage-complete">
                      {description.truncated ? "Source tracing is incomplete at the traversal limit." : description.complete ? "Traced to its source." : "No source recorded; the origin shown is the one it declared."}
                    </p>
                    <div>
                      <p className="text-xs font-semibold text-slate-700">Sources</p>
                      <ul className="list-disc pl-5 text-xs text-slate-700">
                        {description.sources.map((s) => (
                          <li key={`${s.ref}:${s.version}`} className="break-all">
                            {s.ref}
                            {s.declared ? " (declared)" : ""}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-700">History</p>
                      <ol className="list-decimal pl-5 text-xs text-slate-700" data-testid="lineage-history">
                        {description.history.map((h, i) => (
                          <li key={i}>
                            {h.step} by {h.tool || "—"} · {when(h.at)}
                          </li>
                        ))}
                      </ol>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-700">Versions ({description.versions.length})</p>
                      <ul className="text-xs text-slate-700" data-testid="lineage-versions">
                        {description.versions.map((v) => (
                          <li key={v.id}>
                            <button type="button" className="text-indigo-700 hover:underline" onClick={() => void traceFrom(v)}>
                              {shortRef(v.version || "(none)", 28)}
                            </button>{" "}
                            · {when(v.observed_at)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </>
                )}
              </>
            ) : (
              <p className="text-slate-500">Select a node.</p>
            )}
          </div>
        </section>
      )}
      <section aria-label="Sync sources" className="space-y-2">
        <h2 className="text-base font-semibold text-slate-900">Sync sources</h2>
        <table className="w-full text-left text-sm" data-testid="lineage-sources">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="py-1">Name</th>
              <th>Items</th>
              <th>Every</th>
              <th>Last run</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {sources.length === 0 && (
              <tr>
                <td colSpan={6} className="py-2 text-slate-500">
                  No sync sources.
                </td>
              </tr>
            )}
            {sources.map((source) => (
              <tr key={source.id} data-testid="lineage-source">
                <td className="py-1.5">
                  {source.name}
                  {!source.enabled && <span className="ml-2 text-xs text-slate-500">(off)</span>}
                </td>
                <td>{source.item_kind}</td>
                <td>{source.interval_minutes} min</td>
                <td>{when(source.last_run_at)}</td>
                <td>{source.running ? "running" : source.last_status || "—"}</td>
                <td className="space-x-2 text-right">
                  <button type="button" className="text-xs text-indigo-700 hover:underline" onClick={() => void showRuns(source)} data-testid="lineage-source-runs">
                    Runs
                  </button>
                  {canWrite && (
                    <button type="button" className="rounded-md border border-slate-300 px-2 py-0.5 text-xs text-slate-700 disabled:opacity-50" disabled={busy || !!source.running} onClick={() => void runSource(source)} data-testid="lineage-source-run">
                      Run now
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs && (
          <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 text-xs" data-testid="lineage-runs">
            {runs.runs.length === 0 && <li className="px-3 py-1.5 text-slate-500">No runs yet.</li>}
            {runs.runs.map((run) => (
              <li key={run.id} className="px-3 py-1.5">
                {when(run.started_at)} · {run.trigger} · {run.status}: {run.received} received, {run.processed} processed, {run.skipped} unchanged, {run.failed} failed
                {run.errors.length > 0 && <span className="block text-red-700">{run.errors.slice(0, 3).join("; ")}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
