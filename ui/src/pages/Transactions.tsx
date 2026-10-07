// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { APPROVAL_ROLES } from "@/lib/roles";

/**
 * Transactions: the findings the detectors raised with a person's disposition, and the fund-flow
 * graph around an entity drawn by hop, expanded on click, with the heaviest paths and the export.
 */

interface Finding {
  id: string;
  kind: string;
  entity_kind: string;
  entity_ref: string;
  severity: string;
  status: string;
  summary: string;
  facts: Record<string, unknown>;
  record_refs: string[];
  detected_at: string | null;
  disposition: Record<string, unknown>;
  case_ref: string | null;
}

interface Node {
  id: string;
  kind: string;
  label: string;
  hop: number;
  in: number;
  out: number;
  records: number;
  root?: boolean;
  findings: Array<{ id: string; kind: string; severity: string; status: string }>;
}

interface Edge {
  from: string;
  to: string;
  amount: number;
  count: number;
  first_at: string | null;
  last_at: string | null;
  channels: string[];
}

interface Graph {
  root: { kind: string; ref: string; accounts: string[] };
  hops: number;
  nodes: Node[];
  edges: Edge[];
  paths: Array<{ hops: string[]; start: string; carried: number; steps: Array<{ from: string; to: string; amount: number }> }>;
  truncated: boolean;
  totals: { nodes: number; edges: number; records: number };
}

export const KINDS = ["account", "customer", "counterparty"];
const COLUMN = 170;
const ROW = 46;

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
}

/** Node positions by hop (columns) and order within a hop (rows), for the SVG. */
export function layout(nodes: Node[]): Record<string, { x: number; y: number }> {
  const byHop: Record<number, Node[]> = {};
  for (const node of nodes) (byHop[node.hop] ||= []).push(node);
  const out: Record<string, { x: number; y: number }> = {};
  for (const [hop, items] of Object.entries(byHop)) {
    items.forEach((node, index) => {
      out[node.id] = { x: 90 + Number(hop) * COLUMN, y: 40 + index * ROW };
    });
  }
  return out;
}

export function graphHeight(nodes: Node[]): number {
  const counts: Record<number, number> = {};
  for (const node of nodes) counts[node.hop] = (counts[node.hop] || 0) + 1;
  return 80 + Math.max(1, ...Object.values(counts)) * ROW;
}

/** Wide enough for the last hop: a column per hop after the root, plus the margin. */
export function graphWidth(nodes: Node[]): number {
  return 90 + (Math.max(0, ...nodes.map((n) => n.hop)) + 1) * COLUMN;
}

export default function Transactions() {
  const auth = useAuth();
  // Dispositions need approvals:write; the backend refuses everyone else, so the controls follow the same role list.
  const canDecide = APPROVAL_ROLES.includes(auth.user?.role || "");
  const [findings, setFindings] = useState<Finding[]>([]);
  const [status, setStatus] = useState("open");
  const [selected, setSelected] = useState<Finding | null>(null);
  const [notes, setNotes] = useState("");
  const [kind, setKind] = useState("account");
  const [ref, setRef] = useState("");
  const [hops, setHops] = useState(2);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadFindings = useCallback(async () => {
    setError(null);
    try {
      const { data } = await api.get("/txn/findings", { params: { limit: "100", ...(status ? { status } : {}) } });
      setFindings((data as { findings: Finding[] }).findings);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the findings."));
    }
  }, [status]);

  useEffect(() => {
    void loadFindings();
  }, [loadFindings]);

  const loadGraph = useCallback(
    async (which: string, entity: string, depth: number) => {
      if (!entity.trim()) return;
      setBusy(true);
      setError(null);
      try {
        const { data } = await api.get(`/txn/graph/${which}/${encodeURIComponent(entity.trim())}`, { params: { hops: String(depth) } });
        setGraph(data as Graph);
      } catch (err) {
        setGraph(null);
        setError(extractApiError(err, "Failed to build the fund-flow graph."));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const decide = async (outcome: "dismiss" | "confirm" | "escalate") => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/txn/findings/${selected.id}/disposition`, { outcome, notes });
      setNotice(`Finding ${outcome === "dismiss" ? "dismissed" : outcome === "confirm" ? "confirmed" : "escalated"}.`);
      setSelected(null);
      setNotes("");
      await loadFindings();
    } catch (err) {
      setError(extractApiError(err, "The disposition was not recorded."));
    } finally {
      setBusy(false);
    }
  };

  const exportCsv = async () => {
    if (!graph) return;
    setError(null);
    try {
      // Through the configured API client, so the download reaches the API host with the session.
      const { data } = await api.get(`/txn/graph/${graph.root.kind}/${encodeURIComponent(graph.root.ref)}/export`, {
        params: { format: "csv", hops: String(graph.hops) },
        responseType: "blob",
      });
      const url = URL.createObjectURL(data as Blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `fund-flow-${graph.root.ref.slice(0, 32)}.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractApiError(err, "The export did not download."));
    }
  };

  const positions = useMemo(() => (graph ? layout(graph.nodes) : {}), [graph]);
  const maxEdge = useMemo(() => (graph ? Math.max(1, ...graph.edges.map((e) => e.amount)) : 1), [graph]);

  return (
    <div className="space-y-4 p-4">
      <Helmet>
        <title>Transactions</title>
      </Helmet>
      <h1 className="text-xl font-semibold text-slate-900">Transactions</h1>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="txn-notice">
          {notice}
        </div>
      )}
      <section aria-label="Findings" className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-slate-900">Findings</h2>
          <label className="text-sm text-slate-700">
            Status
            <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={status} onChange={(e) => setStatus(e.target.value)} data-testid="txn-status">
              <option value="open">Open</option>
              <option value="confirmed">Confirmed</option>
              <option value="escalated">Escalated</option>
              <option value="dismissed">Dismissed</option>
              <option value="">All</option>
            </select>
          </label>
        </div>
        <div className="grid gap-3 lg:grid-cols-12">
          <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 lg:col-span-7" data-testid="txn-findings">
            {findings.length === 0 && <li className="px-3 py-2 text-sm text-slate-500">No findings.</li>}
            {findings.map((finding) => (
              <li key={finding.id}>
                <button type="button" className={`flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm ${selected?.id === finding.id ? "bg-indigo-50" : "hover:bg-slate-50"}`} onClick={() => setSelected(finding)} data-testid="txn-finding">
                  <span>
                    <span className="mr-2 rounded bg-slate-100 px-1.5 text-xs uppercase text-slate-600">{finding.kind.replace("_", " ")}</span>
                    <span className="text-slate-900">{finding.summary}</span>
                  </span>
                  <span className={`shrink-0 text-xs ${finding.severity === "high" ? "font-semibold text-red-700" : "text-slate-500"}`}>{finding.severity}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="lg:col-span-5">
            {selected ? (
              <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3 text-sm" data-testid="txn-finding-detail">
                <p className="font-medium text-slate-900">
                  {selected.entity_kind} {selected.entity_ref} · {selected.status}
                </p>
                <p className="text-slate-700">{selected.summary}</p>
                <p className="text-xs text-slate-500">{selected.record_refs.length} supporting record(s)</p>
                <button type="button" className="text-xs text-indigo-700 hover:underline" onClick={() => { setKind("account"); setRef(selected.entity_ref); void loadGraph("account", selected.entity_ref, hops); }} data-testid="txn-finding-graph">
                  Show the fund flow
                </button>
                {selected.status === "open" && canDecide && (
                  <>
                    <label className="block text-sm text-slate-700">
                      Notes
                      <textarea className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="txn-notes" />
                    </label>
                    <div className="flex gap-2">
                      <button type="button" className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy} onClick={() => void decide("confirm")}>
                        Confirm
                      </button>
                      <button type="button" className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy} onClick={() => void decide("escalate")}>
                        Escalate
                      </button>
                      <button type="button" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 disabled:opacity-50" disabled={busy || !notes.trim()} onClick={() => void decide("dismiss")} data-testid="txn-dismiss">
                        Dismiss
                      </button>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <p className="text-sm text-slate-500">Select a finding.</p>
            )}
          </div>
        </div>
      </section>
      <section aria-label="Fund flow" className="space-y-2">
        <h2 className="text-base font-semibold text-slate-900">Fund flow</h2>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void loadGraph(kind, ref, hops);
          }}
        >
          <label className="text-sm text-slate-700">
            Kind
            <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={kind} onChange={(e) => setKind(e.target.value)} data-testid="txn-kind">
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Entity
            <input className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={ref} onChange={(e) => setRef(e.target.value)} data-testid="txn-ref" />
          </label>
          <label className="text-sm text-slate-700">
            Hops
            <input type="number" min={1} max={4} className="ml-2 w-16 rounded-md border border-slate-300 px-2 py-1 text-sm" value={hops} onChange={(e) => setHops(Math.max(1, Math.min(4, Number(e.target.value) || 1)))} data-testid="txn-hops" />
          </label>
          <button type="submit" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy || !ref.trim()} data-testid="txn-build">
            Build graph
          </button>
          {graph && (
            <button type="button" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700" onClick={() => void exportCsv()} data-testid="txn-export">
              Export CSV
            </button>
          )}
        </form>
        {graph && (
          <div className="grid gap-3 lg:grid-cols-12" data-testid="txn-graph">
            <div className="overflow-x-auto rounded-md border border-slate-200 bg-white lg:col-span-8">
              <svg role="img" aria-label={`Fund flow around ${graph.root.ref}`} width={graphWidth(graph.nodes)} height={graphHeight(graph.nodes)} viewBox={`0 0 ${graphWidth(graph.nodes)} ${graphHeight(graph.nodes)}`}>
                {graph.edges.map((edge) => {
                  const a = positions[edge.from];
                  const b = positions[edge.to];
                  if (!a || !b) return null;
                  return (
                    <g key={`${edge.from}->${edge.to}`}>
                      <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#6366f1" strokeOpacity="0.6" strokeWidth={1 + (4 * edge.amount) / maxEdge} />
                      <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} fontSize="9" fill="#475569" textAnchor="middle">
                        {money(edge.amount)}
                      </text>
                    </g>
                  );
                })}
                {graph.nodes.map((node) => {
                  const p = positions[node.id];
                  if (!p) return null;
                  const flagged = node.findings.length > 0;
                  return (
                    <g key={node.id} onClick={() => void loadGraph(node.kind === "counterparty" ? "counterparty" : "account", node.id, hops)} style={{ cursor: "pointer" }} data-testid="txn-node">
                      <circle cx={p.x} cy={p.y} r={node.root ? 12 : 9} fill={flagged ? "#dc2626" : node.root ? "#4338ca" : "#94a3b8"} />
                      <text x={p.x} y={p.y + 20} fontSize="9" fill="#0f172a" textAnchor="middle">
                        {node.label.length > 18 ? `${node.label.slice(0, 16)}…` : node.label}
                      </text>
                    </g>
                  );
                })}
              </svg>
            </div>
            <div className="space-y-2 text-sm lg:col-span-4">
              <p className="text-slate-700">
                {graph.totals.nodes} nodes · {graph.totals.edges} edges · {graph.totals.records} records{graph.truncated ? " · truncated" : ""}
              </p>
              <h3 className="text-xs font-medium uppercase text-slate-500">Heaviest paths</h3>
              <ol className="space-y-1" data-testid="txn-paths">
                {graph.paths.length === 0 && <li className="text-slate-500">No outward flow.</li>}
                {graph.paths.map((path, index) => (
                  <li key={index} className="text-slate-800">
                    {path.start} → {path.hops.join(" → ")} <span className="text-xs text-slate-500">carries {money(path.carried)}</span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
