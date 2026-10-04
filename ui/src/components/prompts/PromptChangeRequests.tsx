// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import api, { extractApiError } from "@/lib/api";

interface TemplateState {
  name?: string | null;
  agent_type?: string | null;
  domain?: string | null;
  template_text?: string | null;
  description?: string | null;
  variables?: unknown[] | null;
}

interface ChangeRequest {
  id: string;
  template_id: string | null;
  kind: string;
  domain: string;
  proposed: TemplateState;
  reason: string | null;
  status: string;
  requested_by: string;
  requested_at: string | null;
  current?: TemplateState | null;
}

interface ChangesOut {
  maker_checker: boolean;
  changes: ChangeRequest[];
}

const KIND_LABELS: Record<string, string> = {
  create: "New template",
  update: "Change",
  rollback: "Rollback",
  delete: "Delete",
};

// Every field a change request can carry, in the order a reviewer reads them.
const FIELDS: Array<{ key: keyof TemplateState; label: string }> = [
  { key: "name", label: "Name" },
  { key: "agent_type", label: "Agent type" },
  { key: "domain", label: "Domain" },
  { key: "description", label: "Description" },
  { key: "template_text", label: "Template text" },
  { key: "variables", label: "Parameters" },
];

function titleOf(change: ChangeRequest): string {
  return change.proposed.name || change.current?.name || change.template_id || "template";
}

function shown(value: unknown): string {
  if (value === undefined || value === null || value === "") return "(empty)";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

/** The fields the request would change: every key it carries, including one set to nothing. */
function changedFields(change: ChangeRequest): Array<{ key: string; label: string; now: string; proposed: string }> {
  return FIELDS.filter(({ key }) => key in change.proposed).map(({ key, label }) => ({
    key,
    label,
    now: change.current ? shown(change.current[key]) : "(no template yet)",
    proposed: shown(change.proposed[key]),
  }));
}

/**
 * Prompt changes waiting for a second person. Shown where maker-checker is on
 * or a request is still pending; the proposer cannot approve their own change
 * (the API refuses it) and can withdraw it. ``refreshKey`` changes whenever the
 * page writes a template, so a newly queued request appears without a reload.
 */
export default function PromptChangeRequests({ onDecided, refreshKey = 0 }: { onDecided?: () => void; refreshKey?: number }) {
  const [data, setData] = useState<ChangesOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState<ChangeRequest | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await api.get("/prompt-templates/changes", { params: { status: "pending" } });
      setData(response.data as ChangesOut);
      setLoadError(null);
    } catch (err) {
      setData(null);
      setLoadError(extractApiError(err, "The approval queue could not be loaded."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const review = async (change: ChangeRequest) => {
    setError(null);
    setNote("");
    try {
      const response = await api.get(`/prompt-templates/changes/${change.id}`);
      setOpen(response.data as ChangeRequest);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the change."));
    }
  };

  const decide = async (action: "approve" | "reject" | "withdraw") => {
    if (!open) return;
    setBusy(true);
    setError(null);
    try {
      // One literal path per decision, so each call names the route it uses.
      const body = { note: note.trim() || null };
      if (action === "withdraw") await api.post(`/prompt-templates/changes/${open.id}/withdraw`);
      else if (action === "approve") await api.post(`/prompt-templates/changes/${open.id}/approve`, body);
      else await api.post(`/prompt-templates/changes/${open.id}/reject`, body);
      setOpen(null);
      await load();
      onDecided?.();
    } catch (err) {
      setError(extractApiError(err, "The decision was not recorded."));
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800" data-testid="prompt-changes-error">
        {loadError} Changes may be waiting for approval.{" "}
        <button type="button" className="underline" onClick={() => void load()} data-testid="prompt-changes-retry">
          Retry
        </button>
      </div>
    );
  }
  if (!data || (!data.maker_checker && data.changes.length === 0)) return null;

  const fields = open ? changedFields(open) : [];

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4" data-testid="prompt-changes">
      <h2 className="text-sm font-semibold text-amber-900">Changes waiting for approval</h2>
      <p className="mt-1 text-xs text-amber-800">
        {data.maker_checker
          ? "Maker-checker is on: a prompt change takes effect only after a second person approves it."
          : "Maker-checker is off now; these requests were made while it was on."}
      </p>
      {error && (
        <div role="alert" className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {data.changes.length === 0 && (
        <p className="mt-2 text-sm text-amber-900" data-testid="prompt-changes-empty">
          Nothing is waiting.
        </p>
      )}
      <ul className="mt-2 space-y-1">
        {data.changes.map((change) => (
          <li key={change.id} className="flex items-center justify-between text-sm" data-testid={`prompt-change-${change.id}`}>
            <span>
              <span className="font-medium">{KIND_LABELS[change.kind] ?? change.kind}</span>: {titleOf(change)}{" "}
              <span className="text-amber-800">by {change.requested_by}</span>
            </span>
            <button
              type="button"
              className="text-blue-700 hover:underline"
              onClick={() => void review(change)}
              data-testid={`prompt-change-review-${change.id}`}
            >
              Review
            </button>
          </li>
        ))}
      </ul>

      {open && (
        <div className="mt-3 rounded-md border border-slate-200 bg-white p-3 text-sm" data-testid="prompt-change-detail">
          <p className="font-medium text-slate-800">
            {KIND_LABELS[open.kind] ?? open.kind}: {titleOf(open)}
          </p>
          {open.reason && <p className="text-slate-600">Reason: {open.reason}</p>}
          {open.kind === "delete" && <p className="mt-2 text-slate-700">The template would be deleted.</p>}
          {open.kind !== "delete" && fields.length === 0 && (
            <p className="mt-2 text-slate-700" data-testid="prompt-change-nothing">
              This request changes no field.
            </p>
          )}
          {open.kind !== "delete" &&
            fields.map((item) => (
              <div key={item.key} className="mt-2" data-testid={`prompt-change-field-${item.key}`}>
                <p className="text-xs font-medium uppercase text-slate-500">{item.label}</p>
                <div className="grid gap-3 md:grid-cols-2">
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs" data-testid={`prompt-change-now-${item.key}`}>
                    {item.now}
                  </pre>
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-emerald-50 p-2 text-xs" data-testid={`prompt-change-proposed-${item.key}`}>
                    {item.proposed}
                  </pre>
                </div>
              </div>
            ))}
          {open.kind !== "delete" && fields.length > 0 && (
            <p className="mt-1 text-xs text-slate-500">Left: as it is now. Right: as proposed. Fields not listed are not changed.</p>
          )}
          <label className="mt-3 block text-slate-700">
            Note (required to reject)
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={note}
              maxLength={500}
              onChange={(e) => setNote(e.target.value)}
              data-testid="prompt-change-note"
            />
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
              disabled={busy}
              onClick={() => void decide("approve")}
              data-testid="prompt-change-approve"
            >
              Approve
            </button>
            <button
              type="button"
              className="rounded-md bg-red-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
              disabled={busy || !note.trim()}
              onClick={() => void decide("reject")}
              data-testid="prompt-change-reject"
            >
              Reject
            </button>
            <button
              type="button"
              className="rounded-md bg-slate-100 px-3 py-1.5 text-sm text-slate-700 disabled:text-slate-400"
              disabled={busy}
              onClick={() => void decide("withdraw")}
              data-testid="prompt-change-withdraw"
            >
              Withdraw (my own request)
            </button>
            <button type="button" className="px-3 py-1.5 text-sm text-slate-600" onClick={() => setOpen(null)}>
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
