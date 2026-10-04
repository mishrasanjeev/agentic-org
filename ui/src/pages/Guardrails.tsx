// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";

interface Rule {
  id: string;
  name: string;
  stage: string;
  detector: string;
  action: string;
  priority: number;
  enabled: boolean;
  threshold: number;
  agent_id: string | null;
  use_case: string | null;
  risk_tier: string | null;
  options: Record<string, unknown>;
  reason: string;
}

interface Status {
  enforcing: boolean;
  mode: string;
  hooks_enabled: boolean;
  stages: string[];
  detectors: string[];
  actions: string[];
  risk_tiers: string[];
}

interface Outcome {
  rule_id: string;
  rule_name: string;
  detector: string;
  action: string;
  findings: number;
  score: number;
  kinds: string[];
  blocked: boolean;
  transformed: boolean;
}

interface DryRun {
  stage: string;
  text: string;
  allowed: boolean;
  enforced: boolean;
  findings: number;
  outcomes: Outcome[];
}

interface Draft {
  name: string;
  stage: string;
  detector: string;
  action: string;
  priority: string;
  threshold: string;
  agent_id: string;
  use_case: string;
  risk_tier: string;
  options: string;
  reason: string;
}

// A starting set of options per detector; an empty object means the detector's defaults.
const OPTION_EXAMPLES: Record<string, Record<string, unknown>> = {
  sensitive_data: { entities: ["CREDIT_CARD", "AADHAAR", "PAN"] },
  toxicity: {},
  pattern: { patterns: ["internal use only"], kind: "confidential_marker", ignore_case: true },
  injection: {},
  output_policy: { max_length: 4000, no_urls: true },
  grounding: { min_support: 0.5, require_context: false },
};

const FALLBACK: Pick<Status, "stages" | "detectors" | "actions" | "risk_tiers"> = {
  stages: ["input", "retrieval", "output", "action"],
  detectors: ["sensitive_data", "toxicity", "pattern", "injection", "output_policy", "grounding"],
  actions: ["flag", "mask", "redact", "tokenise", "block"],
  risk_tiers: ["low", "medium", "high", "critical"],
};

const EMPTY: Draft = {
  name: "",
  stage: "output",
  detector: "sensitive_data",
  action: "flag",
  priority: "100",
  threshold: "0.5",
  agent_id: "",
  use_case: "",
  risk_tier: "",
  options: JSON.stringify(OPTION_EXAMPLES.sensitive_data, null, 2),
  reason: "",
};

const cardClass = "rounded-lg border border-slate-200 bg-white p-4 shadow-sm";
const inputClass = "mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm";

function scopeOf(rule: Rule): string {
  const parts = [
    rule.agent_id ? `agent ${rule.agent_id}` : "",
    rule.use_case ? `use case ${rule.use_case}` : "",
    rule.risk_tier ? `${rule.risk_tier} risk` : "",
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(", ") : "every call";
}

function draftOf(rule: Rule): Draft {
  return {
    name: rule.name,
    stage: rule.stage,
    detector: rule.detector,
    action: rule.action,
    priority: String(rule.priority),
    threshold: String(rule.threshold),
    agent_id: rule.agent_id ?? "",
    use_case: rule.use_case ?? "",
    risk_tier: rule.risk_tier ?? "",
    options: JSON.stringify(rule.options ?? {}, null, 2),
    reason: rule.reason ?? "",
  };
}

export default function Guardrails() {
  const [status, setStatus] = useState<Status | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);

  const [tryStage, setTryStage] = useState("output");
  const [tryText, setTryText] = useState("");
  const [tryContext, setTryContext] = useState("");
  const [tryUserInput, setTryUserInput] = useState("");
  // The call the dry run stands for: a rule narrowed to an agent, a use case or a risk tier matches only when named.
  const [tryAgent, setTryAgent] = useState("");
  const [tryUseCase, setTryUseCase] = useState("");
  const [tryRiskTier, setTryRiskTier] = useState("");
  const [dryRun, setDryRun] = useState<DryRun | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResponse, rulesResponse] = await Promise.all([
        api.get("/guardrails/status"),
        api.get("/guardrails/rules"),
      ]);
      setStatus(statusResponse.data as Status);
      setRules(rulesResponse.data as Rule[]);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the guardrail rules."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const lists = {
    stages: status?.stages ?? FALLBACK.stages,
    detectors: status?.detectors ?? FALLBACK.detectors,
    actions: status?.actions ?? FALLBACK.actions,
    risk_tiers: status?.risk_tiers ?? FALLBACK.risk_tiers,
  };

  // What happens to a live call, from the deployment and tenant status; the dry run's own result does not say.
  const liveMode = !status
    ? "the live mode is unknown"
    : !status.hooks_enabled
      ? "live calls are not evaluated (the hooks are off)"
      : status.enforcing
        ? "live calls are enforced"
        : "live calls are flag-only";

  const act = async (id: string, work: () => Promise<unknown>, failure: string) => {
    setBusy(id);
    setError(null);
    try {
      await work();
      await load();
    } catch (err) {
      setError(extractApiError(err, failure));
    } finally {
      setBusy(null);
    }
  };

  const set = (field: keyof Draft, value: string) => setDraft((current) => ({ ...current, [field]: value }));

  const changeDetector = (detector: string) =>
    setDraft((current) => ({
      ...current,
      detector,
      options: JSON.stringify(OPTION_EXAMPLES[detector] ?? {}, null, 2),
    }));

  const reset = () => {
    setDraft(EMPTY);
    setEditing(null);
  };

  const save = async () => {
    let options: unknown;
    try {
      options = draft.options.trim() ? JSON.parse(draft.options) : {};
    } catch {
      setError("The options are not valid JSON.");
      return;
    }
    const body = {
      name: draft.name.trim(),
      stage: draft.stage,
      detector: draft.detector,
      action: draft.action,
      priority: Number(draft.priority),
      threshold: Number(draft.threshold),
      agent_id: draft.agent_id.trim() || null,
      use_case: draft.use_case.trim() || null,
      risk_tier: draft.risk_tier || null,
      options,
      reason: draft.reason.trim(),
    };
    await act(
      "save",
      async () => {
        if (editing) await api.patch(`/guardrails/rules/${editing}`, body);
        else await api.post("/guardrails/rules", body);
        reset();
      },
      editing ? "Failed to change the rule." : "Failed to add the rule.",
    );
  };

  const evaluate = async () => {
    setBusy("evaluate");
    setError(null);
    try {
      const blocks = (value: string) =>
        value
          .split(/\n\s*\n/)
          .map((item) => item.trim())
          .filter(Boolean);
      const context = blocks(tryContext);
      const userInput = blocks(tryUserInput);
      const body: Record<string, unknown> = { stage: tryStage, text: tryText };
      if (tryAgent.trim()) body.agent_id = tryAgent.trim();
      if (tryUseCase.trim()) body.use_case = tryUseCase.trim();
      if (tryRiskTier) body.risk_tier = tryRiskTier;
      if (tryStage === "output" && context.length > 0) body.context = context;
      if (tryStage === "output" && userInput.length > 0) body.user_input = userInput;
      const response = await api.post("/guardrails/evaluate", body);
      setDryRun(response.data as DryRun);
    } catch (err) {
      setDryRun(null);
      setError(extractApiError(err, "Failed to run the dry run."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4" data-testid="guardrails-page">
      <Helmet>
        <title>Guardrails | AgenticOrg</title>
      </Helmet>
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Guardrails</h1>
        <p className="text-sm text-slate-600">
          Rules for what a model is sent, what it retrieves, what it answers and what a tool is asked to do.
        </p>
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {status && !status.hooks_enabled && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800" data-testid="hooks-off">
          The guardrail hooks are off in this deployment (set AGENTICORG_GUARDRAILS_HOOKS_ENABLED). Rules are stored and
          can be dry-run, but no live call is evaluated.
        </div>
      )}
      {status && status.hooks_enabled && !status.enforcing && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800" data-testid="flag-only">
          Flag-only mode: rules record what they find and change nothing. Turn on the guardrails.enforce flag for the
          tenant to apply transforms and blocks.
        </div>
      )}
      {status && status.hooks_enabled && status.enforcing && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="enforcing">
          Enforcing: transforms and blocks apply to live calls and are audited.
        </div>
      )}

      <div className={cardClass}>
        <h2 className="mb-2 text-sm font-semibold text-slate-800">Rules</h2>
        {loading && rules.length === 0 && <p className="text-sm text-slate-500">Loading…</p>}
        {!loading && rules.length === 0 && (
          <p className="text-sm text-slate-500" data-testid="rules-empty">
            No rules yet. Add one below.
          </p>
        )}
        {rules.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="py-1">Name</th>
                <th>Stage</th>
                <th>Detector</th>
                <th>Action</th>
                <th>Priority</th>
                <th>Applies to</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className="border-t border-slate-100" data-testid={`rule-row-${rule.id}`}>
                  <td className="py-1.5 font-medium text-slate-800">
                    {rule.name}
                    {!rule.enabled && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">disabled</span>}
                  </td>
                  <td>{rule.stage}</td>
                  <td>{rule.detector}</td>
                  <td>{rule.action}</td>
                  <td>{rule.priority}</td>
                  <td className="text-slate-600">{scopeOf(rule)}</td>
                  <td className="space-x-2 text-right">
                    <button
                      type="button"
                      className="text-blue-700 hover:underline"
                      onClick={() => {
                        setEditing(rule.id);
                        setDraft(draftOf(rule));
                      }}
                      data-testid={`rule-edit-${rule.id}`}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="text-blue-700 hover:underline disabled:text-slate-400"
                      disabled={busy === rule.id}
                      onClick={() =>
                        void act(
                          rule.id,
                          () => api.patch(`/guardrails/rules/${rule.id}`, { enabled: !rule.enabled }),
                          "Failed to change the rule.",
                        )
                      }
                      data-testid={`rule-toggle-${rule.id}`}
                    >
                      {rule.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      className="text-red-700 hover:underline disabled:text-slate-400"
                      disabled={busy === rule.id}
                      onClick={() => {
                        if (!window.confirm(`Delete the rule "${rule.name}"?`)) return;
                        if (editing === rule.id) reset();
                        void act(rule.id, () => api.delete(`/guardrails/rules/${rule.id}`), "Failed to delete the rule.");
                      }}
                      data-testid={`rule-delete-${rule.id}`}
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

      <div className={cardClass} data-testid="rule-form">
        <h2 className="mb-2 text-sm font-semibold text-slate-800">{editing ? "Change the rule" : "Add a rule"}</h2>
        <div className="grid gap-3 md:grid-cols-4">
          <label className="text-sm text-slate-700 md:col-span-2">
            Name
            <input className={inputClass} value={draft.name} maxLength={120} onChange={(e) => set("name", e.target.value)} data-testid="rule-name" />
          </label>
          <label className="text-sm text-slate-700">
            Stage
            <select className={inputClass} value={draft.stage} onChange={(e) => set("stage", e.target.value)} data-testid="rule-stage">
              {lists.stages.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Detector
            <select className={inputClass} value={draft.detector} onChange={(e) => changeDetector(e.target.value)} data-testid="rule-detector">
              {lists.detectors.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Action
            <select className={inputClass} value={draft.action} onChange={(e) => set("action", e.target.value)} data-testid="rule-action">
              {lists.actions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Priority (lower runs first)
            <input type="number" min={0} className={inputClass} value={draft.priority} onChange={(e) => set("priority", e.target.value)} data-testid="rule-priority" />
          </label>
          <label className="text-sm text-slate-700">
            Threshold (0 to 1)
            <input type="number" min={0} max={1} step={0.05} className={inputClass} value={draft.threshold} onChange={(e) => set("threshold", e.target.value)} data-testid="rule-threshold" />
          </label>
          <label className="text-sm text-slate-700">
            Risk tier
            <select className={inputClass} value={draft.risk_tier} onChange={(e) => set("risk_tier", e.target.value)} data-testid="rule-risk-tier">
              <option value="">any</option>
              {lists.risk_tiers.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700 md:col-span-2">
            Agent id (blank for every agent)
            <input className={inputClass} value={draft.agent_id} maxLength={64} onChange={(e) => set("agent_id", e.target.value)} data-testid="rule-agent" />
          </label>
          <label className="text-sm text-slate-700 md:col-span-2">
            Use case (blank for every use case)
            <input className={inputClass} value={draft.use_case} maxLength={64} onChange={(e) => set("use_case", e.target.value)} data-testid="rule-use-case" />
          </label>
        </div>
        <label className="mt-3 block text-sm text-slate-700">
          Options (JSON; an empty object uses the detector's defaults)
          <textarea className={`${inputClass} h-28 font-mono text-xs`} value={draft.options} onChange={(e) => set("options", e.target.value)} data-testid="rule-options" />
        </label>
        <label className="mt-3 block text-sm text-slate-700">
          Reason
          <input className={inputClass} value={draft.reason} maxLength={500} onChange={(e) => set("reason", e.target.value)} data-testid="rule-reason" />
        </label>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
            disabled={!draft.name.trim() || busy === "save"}
            onClick={() => void save()}
            data-testid="rule-save"
          >
            {editing ? "Save changes" : "Add rule"}
          </button>
          {editing && (
            <button type="button" className="rounded-md bg-slate-100 px-3 py-1.5 text-sm text-slate-700" onClick={reset} data-testid="rule-cancel">
              Cancel
            </button>
          )}
        </div>
      </div>

      <div className={cardClass} data-testid="dry-run">
        <h2 className="mb-1 text-sm font-semibold text-slate-800">Dry run</h2>
        <p className="mb-3 text-xs text-slate-500">
          Runs the rules for a stage over a text and shows what each would do. Nothing is enforced, metered or audited.
          Use synthetic text only.
        </p>
        <div className="grid gap-3 md:grid-cols-4">
          <label className="text-sm text-slate-700">
            Stage
            <select className={inputClass} value={tryStage} onChange={(e) => setTryStage(e.target.value)} data-testid="try-stage">
              {lists.stages.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700 md:col-span-3">
            Text
            <textarea className={`${inputClass} h-20`} value={tryText} onChange={(e) => setTryText(e.target.value)} data-testid="try-text" />
          </label>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <label className="text-sm text-slate-700">
            Agent id of the call (for rules narrowed to an agent)
            <input className={inputClass} value={tryAgent} maxLength={64} onChange={(e) => setTryAgent(e.target.value)} data-testid="try-agent" />
          </label>
          <label className="text-sm text-slate-700">
            Use case of the call
            <input className={inputClass} value={tryUseCase} maxLength={64} onChange={(e) => setTryUseCase(e.target.value)} data-testid="try-use-case" />
          </label>
          <label className="text-sm text-slate-700">
            Risk tier of the call
            <select className={inputClass} value={tryRiskTier} onChange={(e) => setTryRiskTier(e.target.value)} data-testid="try-risk-tier">
              <option value="">none</option>
              {lists.risk_tiers.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          A rule narrowed to an agent, a use case or a risk tier takes part only when the dry run names the same one.
        </p>
        {tryStage === "output" && (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <label className="block text-sm text-slate-700">
              Retrieved context for a grounding rule (separate texts with a blank line)
              <textarea className={`${inputClass} h-20`} value={tryContext} onChange={(e) => setTryContext(e.target.value)} data-testid="try-context" />
            </label>
            <label className="block text-sm text-slate-700">
              What the user wrote (counts as support unless the rule excludes it)
              <textarea className={`${inputClass} h-20`} value={tryUserInput} onChange={(e) => setTryUserInput(e.target.value)} data-testid="try-user-input" />
            </label>
          </div>
        )}
        <button
          type="button"
          className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-400"
          disabled={!tryText.trim() || busy === "evaluate"}
          onClick={() => void evaluate()}
          data-testid="try-run"
        >
          Run
        </button>
        {dryRun && (
          <div className="mt-3 space-y-2 text-sm" data-testid="try-result">
            <p>
              <span className={`rounded px-1.5 py-0.5 text-xs ${dryRun.allowed ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"}`}>
                {dryRun.allowed ? "allowed" : "blocked"}
              </span>
              <span className="ml-2 text-slate-600" data-testid="try-live-mode">
                {dryRun.findings} finding{dryRun.findings === 1 ? "" : "s"}; {liveMode}
              </span>
            </p>
            {dryRun.outcomes.length === 0 && <p className="text-slate-500">No rule matched.</p>}
            {dryRun.outcomes.length > 0 && (
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr>
                    <th className="py-1">Rule</th>
                    <th>Detector</th>
                    <th>Action</th>
                    <th>Findings</th>
                    <th>Kinds</th>
                  </tr>
                </thead>
                <tbody>
                  {dryRun.outcomes.map((outcome) => (
                    <tr key={outcome.rule_id} className="border-t border-slate-100">
                      <td className="py-1.5">{outcome.rule_name}</td>
                      <td>{outcome.detector}</td>
                      <td>
                        {outcome.action}
                        {outcome.blocked ? " (blocks)" : outcome.transformed ? " (rewrites)" : ""}
                      </td>
                      <td>{outcome.findings}</td>
                      <td className="text-slate-600">{outcome.kinds.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <label className="block text-slate-700">
              Text after the rules
              <textarea readOnly className={`${inputClass} h-20 bg-slate-50`} value={dryRun.text} data-testid="try-output" />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
