// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";
import api, { extractApiError } from "@/lib/api";

interface Dataset {
  id: string;
  name: string;
  description: string | null;
  latest_version: number;
  case_count: number;
  updated_at: string | null;
}

interface VersionSummary {
  version: number;
  case_count: number;
  content_hash: string;
  note: string | null;
  created_at: string | null;
}

interface ListOut {
  enabled: boolean;
  datasets: Dataset[];
  limits: { datasets: number; cases: number; cases_per_run: number };
}

interface CaseResult {
  id: string;
  result: "passed" | "failed" | "error";
  failed_checks?: string[];
  error_type?: string;
}

interface RunOut {
  version: number;
  content_hash: string;
  cases_total: number;
  offset: number;
  cases_run: number;
  complete: boolean;
  model: string;
  passed: number;
  failed: number;
  errors: number;
  pass_rate: number | null;
  cost_usd: number;
  results: CaseResult[];
}

const EXAMPLE = JSON.stringify(
  [{ id: "refund-window", input: "Can I return an item after 40 days?", contains: ["30 days"] }],
  null,
  2,
);

function parseCases(text: string): { cases?: unknown[]; problem?: string } {
  try {
    const parsed: unknown = JSON.parse(text);
    if (!Array.isArray(parsed) || parsed.length === 0) return { problem: "Cases are a non-empty JSON list." };
    return { cases: parsed };
  } catch {
    return { problem: "Cases are not valid JSON." };
  }
}

/**
 * Evaluation datasets: named sets of reference cases kept as versions that are
 * never changed. Create one, save a new version, read any version, and score a
 * prompt against one with a model. Renders nothing where datasets are off for
 * the deployment or the caller is not an administrator.
 */
export default function EvalDatasets() {
  const [list, setList] = useState<ListOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [casesText, setCasesText] = useState(EXAMPLE);
  const [open, setOpen] = useState<Dataset | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [shown, setShown] = useState<number | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [system, setSystem] = useState("");
  const [run, setRun] = useState<RunOut | null>(null);
  // Each load of a dataset takes a number; a response for an earlier one is dropped.
  const generation = useRef(0);

  const load = useCallback(async () => {
    try {
      const response = await api.get("/eval-datasets");
      setList(response.data as ListOut);
    } catch {
      // An addition to the page: where it cannot load it is absent.
      setList(null);
    }
  }, []);

  useEffect(() => {
    void load();
    api
      .get("/prompt-templates/compare/models")
      .then((response) => {
        const data = response.data as { enabled: boolean; models: { model: string }[] };
        setModels(data.enabled ? data.models.map((option) => option.model) : []);
      })
      .catch(() => setModels([]));
  }, [load]);

  if (!list || !list.enabled) return null;

  const fail = (err: unknown, fallback: string) => {
    setNotice(null);
    setError(extractApiError(err, fallback));
  };

  const select = async (dataset: Dataset, version?: number) => {
    const mine = ++generation.current;
    setError(null);
    setRun(null);
    try {
      const detail = await api.get(`/eval-datasets/${dataset.id}`);
      const wanted = version ?? (detail.data.latest_version as number);
      const body = await api.get(`/eval-datasets/${dataset.id}/versions/${wanted}`);
      if (mine !== generation.current) return;
      setOpen({ ...dataset, latest_version: detail.data.latest_version, case_count: detail.data.case_count });
      setVersions(detail.data.versions as VersionSummary[]);
      setShown(wanted);
      setCasesText(JSON.stringify(body.data.cases, null, 2));
      setNote("");
    } catch (err) {
      if (mine === generation.current) fail(err, "The dataset could not be loaded.");
    }
  };

  const close = () => {
    generation.current += 1;
    setOpen(null);
    setVersions([]);
    setShown(null);
    setRun(null);
    setCasesText(EXAMPLE);
    setNote("");
  };

  const save = async () => {
    const { cases, problem } = parseCases(casesText);
    if (!cases) {
      setError(problem ?? null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (open) {
        const response = await api.post(`/eval-datasets/${open.id}/versions`, {
          cases,
          note: note || null,
          expected_latest: open.latest_version,
        });
        setNotice(`Saved as version ${response.data.version.version}.`);
        await load();
        await select(open);
      } else {
        const response = await api.post("/eval-datasets", { name, cases, note: note || null });
        setNotice(`Created ${response.data.name} at version 1.`);
        setName("");
        await load();
      }
    } catch (err) {
      fail(err, "The cases were not saved.");
    } finally {
      setBusy(false);
    }
  };

  const archive = async () => {
    if (!open) return;
    setBusy(true);
    try {
      await api.delete(`/eval-datasets/${open.id}`);
      setNotice(`Archived ${open.name}.`);
      close();
      await load();
    } catch (err) {
      fail(err, "The dataset was not archived.");
    } finally {
      setBusy(false);
    }
  };

  const score = async () => {
    if (!open || shown === null) return;
    const mine = generation.current;
    setBusy(true);
    setError(null);
    try {
      const response = await api.post(`/eval-datasets/${open.id}/run`, { version: shown, system, model });
      if (mine === generation.current) setRun(response.data as RunOut);
    } catch (err) {
      if (mine === generation.current) {
        setRun(null);
        fail(err, "The run did not complete.");
      }
    } finally {
      setBusy(false);
    }
  };

  const editingLatest = open !== null && shown === open.latest_version;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4" data-testid="eval-datasets">
      <h3 className="text-sm font-semibold text-slate-800">Evaluation datasets</h3>
      <p className="mt-1 text-xs text-slate-500">
        Reference cases kept as versions that are never changed: saving cases makes a new version. A dataset holds up to{" "}
        {list.limits.cases} cases; a run scores up to {list.limits.cases_per_run} at a time. Use synthetic content.
      </p>
      {error && (
        <div role="alert" className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
        </div>
      )}

      <ul className="mt-3 divide-y divide-slate-100" data-testid="eval-dataset-list">
        {list.datasets.length === 0 && <li className="py-2 text-sm text-slate-500">No datasets yet.</li>}
        {list.datasets.map((dataset) => (
          <li key={dataset.id} className="flex items-center justify-between py-2 text-sm">
            <span className="text-slate-800">
              {dataset.name}
              <span className="ml-2 text-xs text-slate-500">
                v{dataset.latest_version} · {dataset.case_count} cases
              </span>
            </span>
            <button
              type="button"
              className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-700"
              onClick={() => void select(dataset)}
              data-testid={`eval-dataset-open-${dataset.id}`}
            >
              Open
            </button>
          </li>
        ))}
      </ul>

      <div className="mt-4 border-t border-slate-100 pt-3">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium text-slate-800" data-testid="eval-dataset-heading">
            {open ? `${open.name}, version ${shown}` : "New dataset"}
          </h4>
          {open && (
            <span className="flex gap-2">
              <button type="button" className="text-xs text-slate-600 underline" onClick={close} data-testid="eval-dataset-close">
                Close
              </button>
              <button
                type="button"
                className="text-xs text-red-700 underline"
                disabled={busy}
                onClick={() => void archive()}
                data-testid="eval-dataset-archive"
              >
                Archive
              </button>
            </span>
          )}
        </div>
        {open && versions.length > 1 && (
          <label className="mt-2 block text-xs text-slate-600">
            Version
            <select
              className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-xs"
              value={shown ?? ""}
              onChange={(e) => void select(open, Number(e.target.value))}
              data-testid="eval-dataset-version"
            >
              {versions.map((version) => (
                <option key={version.version} value={version.version}>
                  v{version.version} · {version.case_count} cases{version.note ? ` · ${version.note}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {!open && (
          <label className="mt-2 block text-sm text-slate-700">
            Name
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              data-testid="eval-dataset-name"
            />
          </label>
        )}
        <label className="mt-2 block text-sm text-slate-700">
          Cases (JSON): each has an input and at least one of contains, not_contains, equals, matches
          <textarea
            className="mt-1 h-40 w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs"
            value={casesText}
            onChange={(e) => setCasesText(e.target.value)}
            data-testid="eval-dataset-cases"
          />
        </label>
        <label className="mt-2 block text-sm text-slate-700">
          Note
          <input
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            data-testid="eval-dataset-note"
          />
        </label>
        {open && !editingLatest && (
          <p className="mt-2 text-xs text-slate-500">
            This is an earlier version. Saving stores these cases as version {open.latest_version + 1}.
          </p>
        )}
        <button
          type="button"
          className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
          disabled={busy || (!open && !name.trim())}
          onClick={() => void save()}
          data-testid="eval-dataset-save"
        >
          {open ? "Save as new version" : "Create dataset"}
        </button>
      </div>

      {open && models.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3" data-testid="eval-dataset-run">
          <h4 className="text-sm font-medium text-slate-800">Score a prompt against version {shown}</h4>
          <p className="mt-1 text-xs text-slate-500">
            One billed model call per case, the first {list.limits.cases_per_run} cases of the version.
          </p>
          <label className="mt-2 block text-sm text-slate-700">
            Prompt
            <textarea
              className="mt-1 h-20 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              data-testid="eval-run-system"
            />
          </label>
          <label className="mt-2 block text-sm text-slate-700">
            Model
            <select
              className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              data-testid="eval-run-model"
            >
              <option value="">Choose a model</option>
              {models.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
            disabled={busy || !model || !system.trim()}
            onClick={() => void score()}
            data-testid="eval-run-start"
          >
            {busy ? "Running…" : "Run"}
          </button>
          {run && (
            <div className="mt-3 text-sm text-slate-800" data-testid="eval-run-result">
              <p>
                {run.passed} passed, {run.failed} failed, {run.errors} errors of {run.cases_run} cases
                {run.pass_rate === null ? "" : ` (${Math.round(run.pass_rate * 100)}%)`} with {run.model}, version{" "}
                {run.version}. Cost ${run.cost_usd.toFixed(4)}.
              </p>
              {!run.complete && (
                <p className="text-xs text-amber-700">
                  Partial: cases {run.offset + 1} to {run.offset + run.cases_run} of {run.cases_total}.
                </p>
              )}
              <ul className="mt-1 text-xs text-slate-600">
                {run.results
                  .filter((item) => item.result !== "passed")
                  .map((item) => (
                    <li key={item.id}>
                      {item.id}: {item.result === "error" ? `error (${item.error_type ?? "unknown"})` : (item.failed_checks ?? []).join(", ")}
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
