// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState } from "react";
import api, { extractApiError } from "@/lib/api";

interface ModelOption {
  provider: string;
  model: string;
}

interface ModelsOut {
  enabled: boolean;
  models: ModelOption[];
  limits: { models: number; max_tokens: number };
}

interface ModelResult {
  model: string;
  ok: boolean;
  output: string;
  served_model: string | null;
  latency_ms: number;
  tokens: number;
  cost_usd: number;
  error_type: string | null;
}

interface CompareOut {
  results: ModelResult[];
  total_cost_usd: number;
}

interface Parameter {
  name: string;
  type?: string;
  required?: boolean;
  default?: string | number | boolean | null;
  description?: string;
}

/**
 * One stored prompt against several models, side by side: each model's answer
 * with its latency, tokens and cost. Renders nothing where comparison is off
 * for the deployment. Every run makes one billed model call per model.
 */
export default function PromptCompare({ templateId, parameters }: { templateId: string; parameters: Parameter[] }) {
  const [options, setOptions] = useState<ModelsOut | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CompareOut | null>(null);
  // Each run takes a number; a response is shown only if no later run, and no change of template, has happened since.
  const generation = useRef(0);

  useEffect(() => {
    let live = true;
    api
      .get("/prompt-templates/compare/models")
      .then((response) => {
        if (live) setOptions(response.data as ModelsOut);
      })
      .catch(() => {
        // An addition to the page: where it cannot load (or the caller is not an administrator) it is absent.
        if (live) setOptions(null);
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    generation.current += 1;
    setResult(null);
    setError(null);
    setValues({});
    setBusy(false);
  }, [templateId]);

  if (!options || !options.enabled) return null;

  const limit = options.limits.models;
  const toggle = (model: string) =>
    setChosen((current) =>
      current.includes(model) ? current.filter((name) => name !== model) : current.length < limit ? [...current, model] : current,
    );

  const run = async () => {
    const mine = ++generation.current;
    setBusy(true);
    setError(null);
    try {
      // Only what was typed is sent, so a parameter left blank takes its declared default.
      const supplied: Record<string, string> = {};
      Object.entries(values).forEach(([name, value]) => {
        if (value !== "") supplied[name] = value;
      });
      const response = await api.post("/prompt-templates/compare", {
        template_id: templateId,
        values: supplied,
        input,
        models: chosen,
      });
      if (mine !== generation.current) return;
      setResult(response.data as CompareOut);
    } catch (err) {
      if (mine !== generation.current) return;
      setResult(null);
      setError(extractApiError(err, "The comparison did not run."));
    } finally {
      if (mine === generation.current) setBusy(false);
    }
  };

  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4" data-testid="prompt-compare">
      <h3 className="text-sm font-semibold text-slate-800">Compare models</h3>
      <p className="mt-1 text-xs text-slate-500">
        Runs this prompt with one input against up to {limit} models and shows the answers side by side. Each run makes
        one billed model call per model. Use synthetic input.
      </p>
      {error && (
        <div role="alert" className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {parameters.length > 0 && (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {parameters.map((parameter) => (
            <label key={parameter.name} className="text-sm text-slate-700">
              {parameter.name}
              {parameter.required === false ? "" : " *"}
              <input
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                value={values[parameter.name] ?? ""}
                placeholder={
                  parameter.default === undefined || parameter.default === null ? "" : `default: ${String(parameter.default)}`
                }
                onChange={(e) => setValues((current) => ({ ...current, [parameter.name]: e.target.value }))}
                data-testid={`compare-value-${parameter.name}`}
              />
            </label>
          ))}
        </div>
      )}
      <label className="mt-3 block text-sm text-slate-700">
        Input
        <textarea
          className="mt-1 h-20 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          data-testid="compare-input"
        />
      </label>
      <fieldset className="mt-3">
        <legend className="text-sm text-slate-700">
          Models ({chosen.length} of {limit})
        </legend>
        <div className="mt-1 flex flex-wrap gap-3">
          {options.models.map((option) => (
            <label key={option.model} className="flex items-center gap-1 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={chosen.includes(option.model)}
                disabled={!chosen.includes(option.model) && chosen.length >= limit}
                onChange={() => toggle(option.model)}
                data-testid={`compare-model-${option.model}`}
              />
              {option.model}
            </label>
          ))}
        </div>
      </fieldset>
      <button
        type="button"
        className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
        disabled={busy || chosen.length === 0 || !input.trim()}
        onClick={() => void run()}
        data-testid="compare-run"
      >
        {busy ? "Running…" : "Run comparison"}
      </button>

      {result && (
        <div className="mt-4" data-testid="compare-result">
          <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(result.results.length, 1)}, minmax(0, 1fr))` }}>
            {result.results.map((item) => (
              <div key={item.model} className="rounded-md border border-slate-200 p-3" data-testid={`compare-column-${item.model}`}>
                <p className="text-sm font-medium text-slate-800">{item.model}</p>
                <p className="text-xs text-slate-500">
                  {item.latency_ms} ms · {item.tokens} tokens · ${item.cost_usd.toFixed(4)}
                </p>
                {item.ok ? (
                  <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{item.output}</pre>
                ) : (
                  <p className="mt-2 text-sm text-red-700">Failed ({item.error_type ?? "error"})</p>
                )}
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">Total cost of this run: ${result.total_cost_usd.toFixed(4)}</p>
        </div>
      )}
    </div>
  );
}
