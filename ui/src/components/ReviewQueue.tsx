// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import api, { extractApiError } from "@/lib/api";

/**
 * The unified review queue: everything waiting for a person in one list, by priority then age. A
 * selected item shows what it is, the fields the reviewer may edit before deciding, a notes box
 * and the decision; the backend applies the edits and decides through the store that owns the item.
 */

export interface QueueItem {
  kind: string;
  id: string;
  title: string;
  summary: string;
  priority: string;
  status: string;
  requested_by: string | null;
  created_at: string | null;
  due_at: string | null;
  age_seconds: number | null;
  path: string;
  actions: string[];
}

export interface EditableField {
  name: string;
  value: string;
  document_index?: number;
  status?: string;
}

interface Detail {
  kind: string;
  item: Record<string, unknown> & { title?: string; summary?: string; context?: Record<string, unknown> };
  editable: EditableField[];
  decidable: boolean;
}

export const KIND_LABELS: Record<string, string> = {
  approval: "Approval",
  document: "Document",
  draft: "Draft",
  case: "Case",
};

export function ageLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

export function editKey(field: EditableField): string {
  return `${field.document_index ?? 0}:${field.name}`;
}

export default function ReviewQueue() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [kind, setKind] = useState<string>("");
  const [selected, setSelected] = useState<QueueItem | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const openSeq = useRef(0);

  const load = useCallback(async () => {
    setError(null);
    try {
      const params: Record<string, string> = { limit: "100" };
      if (kind) params.kind = kind;
      const { data } = await api.get("/workbench/queue", { params });
      const payload = data as { items: QueueItem[]; allowed_kinds: string[]; counts: Record<string, number> };
      setItems(payload.items);
      setAllowed(payload.allowed_kinds);
      setCounts(payload.counts || {});
    } catch (err) {
      setError(extractApiError(err, "Failed to load the review queue."));
    }
  }, [kind]);

  useEffect(() => {
    void load();
  }, [load]);

  const open = async (item: QueueItem) => {
    const seq = ++openSeq.current;
    setSelected(item);
    setDetail(null);
    setEdits({});
    setNotes("");
    setNotice(null);
    setError(null);
    try {
      const { data } = await api.get(`/workbench/queue/${item.kind}/${encodeURIComponent(item.id)}`);
      if (seq !== openSeq.current) return; // a later selection superseded this one
      setDetail(data as Detail);
    } catch (err) {
      if (seq !== openSeq.current) return;
      setError(extractApiError(err, "Failed to load the item."));
    }
  };

  const decide = async (decision: "approve" | "reject") => {
    if (!selected || !detail) return;
    setBusy(true);
    setError(null);
    try {
      const changed = detail.editable
        .filter((f) => edits[editKey(f)] !== undefined && edits[editKey(f)] !== f.value)
        .map((f) => ({ name: f.name, value: edits[editKey(f)], document_index: f.document_index ?? 0 }));
      await api.post(`/workbench/queue/${selected.kind}/${encodeURIComponent(selected.id)}/decide`, { decision, notes, edits: changed });
      setNotice(`${KIND_LABELS[selected.kind] || selected.kind} ${decision === "approve" ? "approved" : "rejected"}${changed.length ? ` with ${changed.length} edit(s)` : ""}.`);
      setSelected(null);
      setDetail(null);
      await load();
    } catch (err) {
      setError(extractApiError(err, "The decision was not recorded."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-12" data-testid="review-queue">
      <div className="space-y-2 lg:col-span-5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <button type="button" className={`rounded-full border px-2 py-0.5 ${kind === "" ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-300 text-slate-700"}`} onClick={() => setKind("")} data-testid="queue-kind-all">
            All
          </button>
          {allowed.map((k) => (
            <button
              key={k}
              type="button"
              className={`rounded-full border px-2 py-0.5 ${kind === k ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-300 text-slate-700"}`}
              onClick={() => setKind(k)}
              data-testid={`queue-kind-${k}`}
            >
              {KIND_LABELS[k] || k}
              {counts[k] !== undefined ? <span className="ml-1 text-xs text-slate-500">{counts[k]}</span> : null}
            </button>
          ))}
          <button type="button" className="ml-auto rounded-md border border-slate-300 px-2 py-0.5 text-slate-700" onClick={() => void load()} data-testid="queue-refresh">
            Refresh
          </button>
        </div>
        {error && (
          <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </div>
        )}
        {notice && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="queue-notice">
            {notice}
          </div>
        )}
        <ul className="divide-y divide-slate-200 rounded-md border border-slate-200" data-testid="queue-list">
          {items.length === 0 && <li className="px-3 py-2 text-sm text-slate-500">Nothing waits for you.</li>}
          {items.map((item) => (
            <li key={`${item.kind}:${item.id}`}>
              <button
                type="button"
                className={`flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm ${selected?.id === item.id ? "bg-indigo-50" : "hover:bg-slate-50"}`}
                onClick={() => void open(item)}
                data-testid="queue-item"
              >
                <span>
                  <span className="mr-2 rounded bg-slate-100 px-1.5 text-xs uppercase text-slate-600">{KIND_LABELS[item.kind] || item.kind}</span>
                  <span className="font-medium text-slate-900">{item.title}</span>
                  <span className="block text-xs text-slate-500">{item.summary}</span>
                </span>
                <span className="shrink-0 text-right text-xs text-slate-500">
                  <span className={`block ${item.priority === "high" || item.priority === "critical" ? "font-semibold text-red-700" : ""}`}>{item.priority}</span>
                  <span>{ageLabel(item.age_seconds)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="lg:col-span-7">
        {!selected && <p className="text-sm text-slate-500">Select an item to review it.</p>}
        {selected && !detail && !error && <p className="text-sm text-slate-500">Loading…</p>}
        {selected && detail && (
          <div className="space-y-3 rounded-md border border-slate-200 bg-white p-4" data-testid="queue-detail">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-slate-900">{selected.title}</h2>
              <Link to={selected.path} className="text-sm text-indigo-700 hover:underline">
                Open in {KIND_LABELS[selected.kind] || selected.kind} page
              </Link>
            </div>
            <p className="text-sm text-slate-600">{selected.summary}</p>
            {selected.kind === "approval" && detail.item.context && (
              <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-700" data-testid="queue-context">
                {JSON.stringify(detail.item.context, null, 2)}
              </pre>
            )}
            {detail.editable.length > 0 && (
              <div className="space-y-2" data-testid="queue-editable">
                <h3 className="text-sm font-medium text-slate-800">Edit before the decision</h3>
                {detail.editable.map((field) => (
                  <label key={editKey(field)} className="block text-sm text-slate-700">
                    {field.name}
                    {field.document_index !== undefined ? ` (document ${field.document_index + 1})` : ""}
                    {field.value.length > 120 ? (
                      <textarea
                        className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                        rows={4}
                        value={edits[editKey(field)] ?? field.value}
                        onChange={(e) => setEdits((prev) => ({ ...prev, [editKey(field)]: e.target.value }))}
                      />
                    ) : (
                      <input
                        className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                        value={edits[editKey(field)] ?? field.value}
                        onChange={(e) => setEdits((prev) => ({ ...prev, [editKey(field)]: e.target.value }))}
                        data-testid={`queue-edit-${field.name}`}
                      />
                    )}
                  </label>
                ))}
              </div>
            )}
            {selected.kind === "case" ? (
              <p className="text-sm text-slate-600" data-testid="queue-case-note">
                A governed case is decided on its own page.
              </p>
            ) : (
              <>
                <label className="block text-sm text-slate-700">
                  Notes
                  <textarea className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="queue-notes" />
                </label>
                <div className="flex gap-2">
                  <button type="button" className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy || !detail.decidable} onClick={() => void decide("approve")}>
                    Approve
                  </button>
                  <button type="button" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50" disabled={busy || !detail.decidable} onClick={() => void decide("reject")}>
                    Reject
                  </button>
                  {!detail.decidable && <span className="self-center text-xs text-slate-500">This item is no longer open for a decision.</span>}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
