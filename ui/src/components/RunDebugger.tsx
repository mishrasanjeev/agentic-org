// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import api, { extractApiError } from "@/lib/api";

/**
 * The debugging console for one run's thread: its checkpoints as steps (the
 * node that ran, what it changed, the state as it stood, what is next), one
 * value inspected by its path, and, while the run is paused at a breakpoint,
 * step and continue. Reads `/agents/{id}/debug/threads/{thread}`.
 */

export interface DebugStep {
  index: number;
  checkpoint_id: string;
  step: number | null;
  source: string;
  node: string;
  next: string[];
  changed: string[];
  state: Record<string, unknown>;
}

export interface DebugThread {
  thread_id: string;
  total: number;
  paused: boolean;
  next: string[];
  pseudonymised: boolean;
  steps: DebugStep[];
}

interface Inspected {
  path: string;
  value: unknown;
  truncated: boolean;
  bytes: number;
}

interface Advanced {
  status: string;
  paused_before: string[];
  error?: string | null;
  reason?: string | null;
}

interface Props {
  agentId: string;
  threadId: string;
}

const threadUrl = (agentId: string, threadId: string) =>
  `/agents/${encodeURIComponent(agentId)}/debug/threads/${encodeURIComponent(threadId)}`;

export function stepLabel(step: DebugStep): string {
  if (step.source === "input") return "input";
  return step.node || "state";
}

export function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? "";
  } catch {
    return String(value);
  }
}

export default function RunDebugger({ agentId, threadId }: Props) {
  const [thread, setThread] = useState<DebugThread | null>(null);
  const [selected, setSelected] = useState<number>(0);
  const [path, setPath] = useState("output");
  const [inspected, setInspected] = useState<Inspected | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const { data } = await api.get(threadUrl(agentId, threadId));
      const next = data as DebugThread;
      setThread(next);
      setSelected(Math.max(0, next.steps.length - 1));
      setInspected(null);
    } catch (err) {
      setThread(null);
      setError(extractApiError(err, "Failed to load the run's steps."));
    }
  }, [agentId, threadId]);

  useEffect(() => {
    void load();
  }, [load]);

  const inspect = useCallback(async () => {
    const step = thread?.steps[selected];
    if (!step) return;
    setError(null);
    try {
      const { data } = await api.get(`${threadUrl(agentId, threadId)}/steps/${encodeURIComponent(step.checkpoint_id)}`, {
        params: { path },
      });
      setInspected(data as Inspected);
    } catch (err) {
      setInspected(null);
      setError(extractApiError(err, "Failed to inspect that value."));
    }
  }, [agentId, threadId, thread, selected, path]);

  const advance = useCallback(
    async (mode: "step" | "continue") => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const { data } = await api.post(`${threadUrl(agentId, threadId)}/${mode}`);
        const result = data as Advanced;
        setNotice(
          result.status === "paused"
            ? `Paused before ${result.paused_before.join(", ")}.`
            : `The run finished with status ${result.status}${result.reason ? ` (${result.reason})` : ""}.`,
        );
        await load();
      } catch (err) {
        setError(extractApiError(err, `Failed to ${mode} the run.`));
      } finally {
        setBusy(false);
      }
    },
    [agentId, threadId, load],
  );

  const step = thread?.steps[selected];

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4" data-testid="run-debugger">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium text-slate-800">Step through</span>
        <span className="font-mono text-xs text-slate-600" data-testid="debugger-thread">
          {threadId}
        </span>
      </div>
      {error && (
        <div role="alert" className="mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="debugger-notice">
          {notice}
        </div>
      )}
      {thread && (
        <div className="grid gap-3 md:grid-cols-12">
          <div className="md:col-span-4">
            {thread.pseudonymised && (
              <p className="mb-2 text-xs text-slate-500" data-testid="debugger-pseudonymised">
                A pseudonymised run: the values are what the model saw.
              </p>
            )}
            {thread.paused && (
              <div className="mb-2 flex flex-wrap items-center gap-2" data-testid="debugger-paused">
                <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">paused before {thread.next.join(", ")}</span>
                <button
                  type="button"
                  className="rounded-md border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50 disabled:opacity-50"
                  disabled={busy}
                  onClick={() => void advance("step")}
                  data-testid="debugger-step"
                >
                  Step
                </button>
                <button
                  type="button"
                  className="rounded-md border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50 disabled:opacity-50"
                  disabled={busy}
                  onClick={() => void advance("continue")}
                  data-testid="debugger-continue"
                >
                  Continue
                </button>
              </div>
            )}
            <ol className="space-y-1" data-testid="debugger-steps">
              {thread.steps.map((item) => (
                <li key={item.checkpoint_id}>
                  <button
                    type="button"
                    className={`w-full rounded-md border px-2 py-1 text-left text-xs ${item.index === selected ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"}`}
                    onClick={() => {
                      setSelected(item.index);
                      setInspected(null);
                    }}
                    data-testid={`debugger-step-${item.index}`}
                  >
                    <span className="font-medium text-slate-800">
                      {item.index}. {stepLabel(item)}
                    </span>
                    {item.next.length > 0 && <span className="ml-1 text-slate-500">→ {item.next.join(", ")}</span>}
                    {item.changed.length > 0 && (
                      <span className="ml-1 text-slate-500" data-testid={`debugger-changed-${item.index}`}>
                        changed {item.changed.join(", ")}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ol>
          </div>
          <div className="md:col-span-8">
            {step && (
              <>
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                  <label className="text-slate-700">
                    Inspect
                    <input
                      className="ml-2 rounded-md border border-slate-300 px-2 py-0.5 font-mono text-xs"
                      value={path}
                      onChange={(e) => setPath(e.target.value)}
                      data-testid="debugger-path"
                    />
                  </label>
                  <button
                    type="button"
                    className="rounded-md border border-slate-300 px-2 py-0.5 hover:bg-slate-50"
                    onClick={() => void inspect()}
                    data-testid="debugger-inspect"
                  >
                    Show
                  </button>
                </div>
                {inspected && (
                  <pre className="mb-2 max-h-64 overflow-auto rounded-md bg-slate-900 p-2 text-xs text-slate-100" data-testid="debugger-inspected">
                    {formatValue(inspected.value)}
                    {inspected.truncated ? "\n… [cut; the value is larger than the console shows]" : ""}
                  </pre>
                )}
                <pre className="max-h-96 overflow-auto rounded-md bg-slate-50 p-2 text-xs text-slate-800" data-testid="debugger-state">
                  {formatValue(step.state)}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
