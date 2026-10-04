// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import api, { extractApiError } from "@/lib/api";

interface Check {
  id: string;
  name: string;
  kind: string;
  config: Record<string, unknown>;
  interval_minutes: number;
  enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
}

interface ChecksOut {
  enabled: boolean;
  kinds: string[];
  limit: number;
  checks: Check[];
}

interface CheckResult {
  id: string;
  check_id: string;
  status: string;
  latency_ms: number;
  reasons: string[];
  detail: Record<string, unknown>;
  trigger: string;
  started_at: string;
}

// A starting configuration per kind; every input is the administrator's own synthetic text.
const EXAMPLES: Record<string, Record<string, unknown>> = {
  model: { prompt: "Reply with the single word ready.", contains: "ready", max_latency_ms: 20000 },
  knowledge: { query: "leave policy", top_k: 5, min_results: 1 },
  guardrail: { stage: "input", text: "Synthetic card number 4111 1111 1111 1111", expect: "detected" },
  audit_chain: { recent: 1000 },
};

const cardClass = "rounded-lg border border-slate-200 bg-white p-4 shadow-sm";

function statusClass(status: string | null): string {
  if (status === "ok") return "bg-emerald-100 text-emerald-800";
  if (status === "failed") return "bg-amber-100 text-amber-800";
  if (status === "error") return "bg-red-100 text-red-800";
  return "bg-slate-100 text-slate-600";
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "never";
}

export default function SyntheticChecksPanel() {
  const [data, setData] = useState<ChecksOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [results, setResults] = useState<CheckResult[]>([]);

  const [name, setName] = useState("");
  const [kind, setKind] = useState("model");
  const [config, setConfig] = useState(JSON.stringify(EXAMPLES.model, null, 2));
  const [interval, setInterval] = useState("60");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.get("/observability/checks");
      setData(response.data as ChecksOut);
    } catch (err) {
      setData(null);
      setError(extractApiError(err, "Failed to load the synthetic checks."));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadResults = useCallback(async (checkId: string) => {
    setSelected(checkId);
    try {
      const response = await api.get(`/observability/checks/${checkId}/results`, { params: { limit: "20" } });
      setResults((response.data as { results: CheckResult[] }).results);
    } catch (err) {
      setResults([]);
      setError(extractApiError(err, "Failed to load the results."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (id: string, work: () => Promise<unknown>, failure: string, after?: () => Promise<void>) => {
    setBusy(id);
    setError(null);
    try {
      await work();
      await load();
      if (after) await after();
    } catch (err) {
      setError(extractApiError(err, failure));
    } finally {
      setBusy(null);
    }
  };

  const changeKind = (next: string) => {
    setKind(next);
    setConfig(JSON.stringify(EXAMPLES[next] ?? {}, null, 2));
  };

  const create = async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(config);
    } catch {
      setError("The configuration is not valid JSON.");
      return;
    }
    await act(
      "create",
      async () => {
        await api.post("/observability/checks", {
          name: name.trim(),
          kind,
          config: parsed,
          interval_minutes: Number(interval),
          enabled: true,
        });
        setName("");
      },
      "Failed to add the check.",
    );
  };

  const kinds = data?.kinds ?? Object.keys(EXAMPLES);
  const atLimit = data ? data.checks.length >= data.limit : false;

  return (
    <div className="space-y-4" data-testid="checks-panel">
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {data && !data.enabled && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800" data-testid="checks-off">
          Synthetic checks are off in this deployment (set AGENTICORG_SYNTHETIC_CHECKS_ENABLED). Nothing runs and no check
          can be added; stored checks and results can still be read and removed.
        </div>
      )}

      <div className={cardClass}>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-800">Synthetic checks</h2>
          <button
            type="button"
            className="rounded-md bg-slate-100 px-3 py-1 text-sm text-slate-700 hover:bg-slate-200"
            onClick={() => void load()}
            data-testid="checks-refresh"
          >
            Refresh
          </button>
        </div>
        {loading && !data && <p className="text-sm text-slate-500">Loading…</p>}
        {data && data.checks.length === 0 && (
          <p className="text-sm text-slate-500" data-testid="checks-empty">
            No checks yet. Add one below.
          </p>
        )}
        {data && data.checks.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="py-1">Name</th>
                <th>Kind</th>
                <th>Every</th>
                <th>Last result</th>
                <th>Last run</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.checks.map((check) => (
                <tr key={check.id} className="border-t border-slate-100" data-testid={`check-row-${check.id}`}>
                  <td className="py-1.5 font-medium text-slate-800">{check.name}</td>
                  <td>{check.kind}</td>
                  <td>{check.interval_minutes} min</td>
                  <td>
                    <span className={`rounded px-1.5 py-0.5 text-xs ${statusClass(check.last_status)}`}>
                      {check.enabled ? (check.last_status ?? "not run") : "disabled"}
                    </span>
                  </td>
                  <td className="text-slate-600">{formatTime(check.last_run_at)}</td>
                  <td className="space-x-2 text-right">
                    <button
                      type="button"
                      className="text-blue-700 hover:underline disabled:text-slate-400"
                      disabled={busy === check.id || !data.enabled}
                      onClick={() =>
                        void act(
                          check.id,
                          () => api.post(`/observability/checks/${check.id}/run`),
                          "Failed to run the check.",
                          () => loadResults(check.id),
                        )
                      }
                      data-testid={`check-run-${check.id}`}
                    >
                      Run now
                    </button>
                    <button
                      type="button"
                      className="text-blue-700 hover:underline"
                      onClick={() => void loadResults(check.id)}
                      data-testid={`check-results-${check.id}`}
                    >
                      Results
                    </button>
                    <button
                      type="button"
                      className="text-blue-700 hover:underline disabled:text-slate-400"
                      disabled={busy === check.id}
                      onClick={() =>
                        void act(
                          check.id,
                          () => api.patch(`/observability/checks/${check.id}`, { enabled: !check.enabled }),
                          "Failed to change the check.",
                        )
                      }
                      data-testid={`check-toggle-${check.id}`}
                    >
                      {check.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      className="text-red-700 hover:underline disabled:text-slate-400"
                      disabled={busy === check.id}
                      onClick={() => {
                        if (!window.confirm(`Delete the check "${check.name}" and its results?`)) return;
                        void act(check.id, () => api.delete(`/observability/checks/${check.id}`), "Failed to delete the check.");
                        if (selected === check.id) setSelected(null);
                      }}
                      data-testid={`check-delete-${check.id}`}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selected && (
        <div className={cardClass} data-testid="check-results">
          <h2 className="mb-2 text-sm font-semibold text-slate-800">Newest results</h2>
          {results.length === 0 && <p className="text-sm text-slate-500">No results yet.</p>}
          {results.length > 0 && (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr>
                  <th className="py-1">When</th>
                  <th>Status</th>
                  <th>Latency</th>
                  <th>Trigger</th>
                  <th>Reasons</th>
                </tr>
              </thead>
              <tbody>
                {results.map((result) => (
                  <tr key={result.id} className="border-t border-slate-100">
                    <td className="py-1.5 text-slate-600">{formatTime(result.started_at)}</td>
                    <td>
                      <span className={`rounded px-1.5 py-0.5 text-xs ${statusClass(result.status)}`}>{result.status}</span>
                    </td>
                    <td>{result.latency_ms} ms</td>
                    <td>{result.trigger}</td>
                    <td className="text-slate-600">
                      {result.reasons.length > 0
                        ? result.reasons.join(", ")
                        : String(result.detail.error_type ?? "–")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <div className={cardClass}>
        <h2 className="mb-2 text-sm font-semibold text-slate-800">Add a check</h2>
        <p className="mb-3 text-xs text-slate-500">
          Use synthetic inputs only. A result keeps the status, the latency and counts; never an answer or retrieved text.
        </p>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-sm text-slate-700">
            Name
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              data-testid="check-name"
            />
          </label>
          <label className="text-sm text-slate-700">
            Kind
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={kind}
              onChange={(e) => changeKind(e.target.value)}
              data-testid="check-kind"
            >
              {kinds.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Every (minutes, 5 to 1440)
            <input
              type="number"
              min={5}
              max={1440}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={interval}
              onChange={(e) => setInterval(e.target.value)}
              data-testid="check-interval"
            />
          </label>
        </div>
        <label className="mt-3 block text-sm text-slate-700">
          Configuration (JSON)
          <textarea
            className="mt-1 h-32 w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs"
            value={config}
            onChange={(e) => setConfig(e.target.value)}
            data-testid="check-config"
          />
        </label>
        <button
          type="button"
          className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
          disabled={!name.trim() || busy === "create" || atLimit || !data?.enabled}
          onClick={() => void create()}
          data-testid="check-create"
        >
          Add check
        </button>
        {atLimit && <p className="mt-2 text-xs text-slate-500">This tenant has reached its limit of {data?.limit} checks.</p>}
      </div>
    </div>
  );
}
