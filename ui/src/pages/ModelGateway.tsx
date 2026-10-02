// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

/**
 * Model gateway console: routing policies, access policies, per-model limits,
 * the dry run, the routing records and the cost comparison.
 *
 * Everything here reads and writes the tenant-admin endpoints under
 * /model-gateway; the gateway itself stays behind the authority flag
 * model_gateway.enabled, which the status card reports.
 */

interface Target {
  provider: string;
  model: string;
  weight: number;
}

interface RoutingPolicy {
  id: string;
  name: string;
  priority: number;
  enabled: boolean;
  use_case: string | null;
  sensitivity: string | null;
  agent_id: string | null;
  business_unit: string | null;
  language: string | null;
  provider: string | null;
  model: string | null;
  tier: string | null;
  targets: Target[] | null;
  allowed_providers: string[] | null;
  in_region_only: boolean;
  cost_aware: boolean;
  max_failure_rate: number | null;
  reason: string;
}

interface AccessPolicy {
  id: string;
  name: string;
  priority: number;
  enabled: boolean;
  use_case: string | null;
  sensitivity: string | null;
  agent_id: string | null;
  business_unit: string | null;
  application: string | null;
  principal: string | null;
  provider: string | null;
  model: string | null;
  effect: string;
  allowed_providers: string[] | null;
  allowed_models: string[] | null;
  reason: string;
}

interface Limit {
  id: string;
  provider: string;
  model: string | null;
  enabled: boolean;
  max_concurrency: number | null;
  requests_per_minute: number | null;
  reason: string;
}

interface GatewayStatus {
  enabled: boolean;
  active_policies: unknown[];
  active_access_policies: unknown[];
  active_limits: unknown[];
}

interface RoutingRecord {
  id: string;
  correlation_id: string;
  use_case: string;
  agent_id: string | null;
  provider: string;
  model: string;
  requested_model: string | null;
  fallback_from: string | null;
  outcome: string;
  error_type: string | null;
  latency_ms: number;
  tokens: number;
  cost_usd: number;
  signed: boolean;
  created_at: string;
}

interface CostRow {
  provider: string;
  model: string;
  list_price: { input_per_million: number; output_per_million: number; source: string } | null;
  blended_per_million_usd: number | null;
  observed: { calls: number; failures: number; failure_rate: number; avg_latency_ms: number; avg_cost_usd: number; total_cost_usd: number } | null;
}

type Tab = "policies" | "access" | "limits" | "dryrun" | "records" | "costs";

const TABS: { key: Tab; label: string }[] = [
  { key: "policies", label: "Routing policies" },
  { key: "access", label: "Access policies" },
  { key: "limits", label: "Limits" },
  { key: "dryrun", label: "Dry run" },
  { key: "records", label: "Records" },
  { key: "costs", label: "Costs" },
];

const SENSITIVITIES = ["", "public", "internal", "confidential", "restricted"];
const TIERS = ["", "tier1", "tier2", "tier3"];

function listOf(text: string): string[] | undefined {
  const items = text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return items.length ? items : undefined;
}

function optional(text: string): string | undefined {
  const value = text.trim();
  return value ? value : undefined;
}

/** A priority as the API takes it: a whole number from 0 (lower wins); empty means the default 100; anything else is invalid. */
function parsePriority(text: string): number | null {
  const value = text.trim();
  if (value === "") return 100;
  if (!/^\d+$/.test(value)) return null;
  return Number(value);
}

function routeSummary(p: RoutingPolicy): string {
  if (p.targets && p.targets.length) {
    const split = p.targets.map((t) => `${t.provider}/${t.model}×${t.weight}`).join(", ");
    return p.cost_aware ? `cheapest healthy of ${split}` : `split ${split}`;
  }
  if (p.provider && p.model) return `${p.provider}/${p.model}`;
  if (p.provider) return p.provider;
  if (p.model) return p.model;
  if (p.tier) return p.tier;
  return "fence only";
}

function matchSummary(p: { use_case: string | null; sensitivity: string | null; agent_id: string | null; business_unit: string | null; language?: string | null; application?: string | null; principal?: string | null; provider?: string | null; model?: string | null }): string {
  const parts: string[] = [];
  if (p.use_case) parts.push(`use case ${p.use_case}`);
  if (p.sensitivity) parts.push(p.sensitivity);
  if (p.agent_id) parts.push(`agent ${p.agent_id}`);
  if (p.business_unit) parts.push(`unit ${p.business_unit}`);
  if (p.language) parts.push(`language ${p.language}`);
  if (p.application) parts.push(`application ${p.application}`);
  if (p.principal) parts.push(`principal ${p.principal}`);
  if (p.provider) parts.push(`provider ${p.provider}`);
  if (p.model) parts.push(`model ${p.model}`);
  return parts.length ? parts.join(", ") : "every call";
}

const inputClass = "border rounded px-2 py-1 text-sm w-full";
const labelClass = "text-xs text-muted-foreground";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className={labelClass}>{label}</span>
      {children}
    </label>
  );
}

export default function ModelGateway() {
  const [tab, setTab] = useState<Tab>("policies");
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [policies, setPolicies] = useState<RoutingPolicy[]>([]);
  const [access, setAccess] = useState<AccessPolicy[]>([]);
  const [limits, setLimits] = useState<Limit[]>([]);
  const [records, setRecords] = useState<RoutingRecord[] | null>(null);
  const [costs, setCosts] = useState<CostRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [policyForm, setPolicyForm] = useState({
    name: "",
    priority: "100",
    use_case: "",
    sensitivity: "",
    agent_id: "",
    business_unit: "",
    language: "",
    provider: "",
    model: "",
    tier: "",
    targets: "",
    allowed_providers: "",
    in_region_only: false,
    cost_aware: false,
    max_failure_rate: "",
    reason: "",
  });
  const [accessForm, setAccessForm] = useState({
    name: "",
    priority: "100",
    use_case: "",
    sensitivity: "",
    agent_id: "",
    business_unit: "",
    language: "",
    application: "",
    principal: "",
    provider: "",
    model: "",
    effect: "allow",
    allowed_providers: "",
    allowed_models: "",
    reason: "",
  });
  const [limitForm, setLimitForm] = useState({ provider: "", model: "", max_concurrency: "", requests_per_minute: "", reason: "" });
  const [dryRunForm, setDryRunForm] = useState({
    use_case: "agent_run",
    requested_provider: "",
    requested_model: "",
    sensitivity: "",
    agent_id: "",
    business_unit: "",
    language: "",
    application: "",
    principal: "",
  });
  const [dryRun, setDryRun] = useState<Record<string, unknown> | null>(null);
  const [recordFilter, setRecordFilter] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, policiesRes, accessRes, limitsRes] = await Promise.all([
        api.get("/model-gateway/status"),
        api.get("/model-gateway/policies"),
        api.get("/model-gateway/access-policies"),
        api.get("/model-gateway/limits"),
      ]);
      setStatus(statusRes.data);
      setPolicies(policiesRes.data || []);
      setAccess(accessRes.data || []);
      setLimits(limitsRes.data || []);
    } catch (e) {
      setError(extractApiError(e, "Failed to load the model gateway"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const fetchRecords = useCallback(async (correlationId: string) => {
    setError(null);
    try {
      const params: Record<string, string> = { limit: "100" };
      if (correlationId.trim()) params.correlation_id = correlationId.trim();
      const res = await api.get("/model-gateway/records", { params });
      setRecords(res.data || []);
    } catch (e) {
      setError(extractApiError(e, "Failed to load the routing records"));
    }
  }, []);

  const fetchCosts = useCallback(async () => {
    setError(null);
    try {
      const res = await api.get("/model-gateway/costs");
      setCosts(res.data?.models || []);
    } catch (e) {
      setError(extractApiError(e, "Failed to load the cost comparison"));
    }
  }, []);

  useEffect(() => {
    if (tab === "records" && records === null) fetchRecords("");
    if (tab === "costs" && costs === null) fetchCosts();
  }, [tab, records, costs, fetchRecords, fetchCosts]);

  async function run(action: () => Promise<void>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(done);
      await fetchAll();
    } catch (e) {
      setError(extractApiError(e, "The change was not saved"));
    } finally {
      setBusy(false);
    }
  }

  function createPolicy() {
    const priority = parsePriority(policyForm.priority);
    if (priority === null) {
      setError("Priority must be a whole number from 0 (lower wins).");
      return;
    }
    let targets: Target[] | undefined;
    if (policyForm.targets.trim()) {
      try {
        targets = JSON.parse(policyForm.targets);
      } catch {
        setError("Targets must be JSON: [{\"provider\": \"...\", \"model\": \"...\", \"weight\": 1}]");
        return;
      }
    }
    const payload: Record<string, unknown> = {
      name: policyForm.name.trim(),
      priority,
      use_case: optional(policyForm.use_case),
      sensitivity: optional(policyForm.sensitivity),
      agent_id: optional(policyForm.agent_id),
      business_unit: optional(policyForm.business_unit),
      language: optional(policyForm.language),
      provider: optional(policyForm.provider),
      model: optional(policyForm.model),
      tier: optional(policyForm.tier),
      targets,
      allowed_providers: listOf(policyForm.allowed_providers),
      in_region_only: policyForm.in_region_only,
      cost_aware: policyForm.cost_aware,
      max_failure_rate: policyForm.max_failure_rate.trim() ? Number(policyForm.max_failure_rate) : undefined,
      reason: policyForm.reason.trim(),
    };
    run(async () => {
      await api.post("/model-gateway/policies", payload);
      setPolicyForm({ ...policyForm, name: "", reason: "" });
    }, `Routing policy ${payload.name} created`);
  }

  function createAccessPolicy() {
    const priority = parsePriority(accessForm.priority);
    if (priority === null) {
      setError("Priority must be a whole number from 0 (lower wins).");
      return;
    }
    const payload: Record<string, unknown> = {
      name: accessForm.name.trim(),
      priority,
      use_case: optional(accessForm.use_case),
      sensitivity: optional(accessForm.sensitivity),
      agent_id: optional(accessForm.agent_id),
      business_unit: optional(accessForm.business_unit),
      language: optional(accessForm.language),
      application: optional(accessForm.application),
      principal: optional(accessForm.principal),
      provider: optional(accessForm.provider),
      model: optional(accessForm.model),
      effect: accessForm.effect,
      allowed_providers: accessForm.effect === "allow" ? listOf(accessForm.allowed_providers) : undefined,
      allowed_models: accessForm.effect === "allow" ? listOf(accessForm.allowed_models) : undefined,
      reason: accessForm.reason.trim(),
    };
    run(async () => {
      await api.post("/model-gateway/access-policies", payload);
      setAccessForm({ ...accessForm, name: "", reason: "" });
    }, `Access policy ${payload.name} created`);
  }

  function createLimit() {
    const payload: Record<string, unknown> = {
      provider: limitForm.provider.trim(),
      model: optional(limitForm.model),
      max_concurrency: limitForm.max_concurrency.trim() ? Number(limitForm.max_concurrency) : undefined,
      requests_per_minute: limitForm.requests_per_minute.trim() ? Number(limitForm.requests_per_minute) : undefined,
      reason: limitForm.reason.trim(),
    };
    run(async () => {
      await api.post("/model-gateway/limits", payload);
      setLimitForm({ provider: "", model: "", max_concurrency: "", requests_per_minute: "", reason: "" });
    }, `Limit on ${payload.provider}${payload.model ? ` ${payload.model}` : ""} created`);
  }

  function resourcePath(kind: "policies" | "access-policies" | "limits", id: string) {
    switch (kind) {
      case "policies":
        return `/model-gateway/policies/${id}`;
      case "access-policies":
        return `/model-gateway/access-policies/${id}`;
      case "limits":
        return `/model-gateway/limits/${id}`;
    }
  }

  function toggle(kind: "policies" | "access-policies" | "limits", id: string, enabled: boolean, name: string) {
    run(async () => {
      await api.patch(resourcePath(kind, id), { enabled: !enabled });
    }, `${name} ${enabled ? "disabled" : "enabled"}`);
  }

  function remove(kind: "policies" | "access-policies" | "limits", id: string, name: string) {
    if (!window.confirm(`Delete ${name}?`)) return;
    run(async () => {
      await api.delete(resourcePath(kind, id));
    }, `${name} deleted`);
  }

  async function evaluate() {
    setBusy(true);
    setError(null);
    setDryRun(null);
    try {
      const payload: Record<string, unknown> = { use_case: dryRunForm.use_case.trim() || "agent_run" };
      for (const key of ["requested_provider", "requested_model", "sensitivity", "agent_id", "business_unit", "language", "application", "principal"] as const) {
        const value = dryRunForm[key].trim();
        if (value) payload[key] = value;
      }
      const res = await api.post("/model-gateway/evaluate", payload);
      setDryRun(res.data);
    } catch (e) {
      setError(extractApiError(e, "The dry run failed"));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="p-6">Loading model gateway…</div>;

  return (
    <div className="p-6 space-y-6" data-testid="model-gateway-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Model gateway</h1>
          <p className="text-sm text-muted-foreground">
            Routing and access policies, per-model limits, routing records and cost comparison for every model call.
          </p>
        </div>
        {status && (
          <div className="flex items-center gap-2" data-testid="gateway-status">
            <Badge variant={status.enabled ? "success" : "secondary"}>{status.enabled ? "Gateway on" : "Gateway off"}</Badge>
            <span className="text-xs text-muted-foreground">
              {status.active_policies.length} routing, {status.active_access_policies.length} access, {status.active_limits.length} limits active
            </span>
          </div>
        )}
      </div>

      {error && <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">{error}</div>}
      {notice && <div className="rounded border border-green-200 bg-green-50 p-3 text-sm text-green-800" role="status">{notice}</div>}

      <div className="flex flex-wrap gap-2" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            data-testid={`tab-${t.key}`}
            className={`px-3 py-1 rounded text-sm ${tab === t.key ? "bg-primary text-primary-foreground" : "bg-muted"}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "policies" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle className="text-lg">Routing policies</CardTitle></CardHeader>
            <CardContent>
              {policies.length === 0 ? (
                <p className="text-sm text-muted-foreground">No routing policies. Every call keeps the model its agent or caller asked for.</p>
              ) : (
                <table className="w-full text-sm" data-testid="policies-table">
                  <thead><tr className="text-left text-xs text-muted-foreground"><th>Priority</th><th>Name</th><th>Matches</th><th>Routes to</th><th>Fence</th><th></th></tr></thead>
                  <tbody>
                    {policies.map((p) => (
                      <tr key={p.id} className="border-t" data-testid={`policy-${p.name}`}>
                        <td className="py-2">{p.priority}</td>
                        <td className="py-2 font-medium">{p.name}{!p.enabled && <Badge variant="secondary" className="ml-2">disabled</Badge>}</td>
                        <td className="py-2">{matchSummary(p)}</td>
                        <td className="py-2">{routeSummary(p)}{p.in_region_only && <Badge variant="warning" className="ml-2">in region</Badge>}</td>
                        <td className="py-2">{p.allowed_providers ? p.allowed_providers.join(", ") : "—"}</td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <Button size="sm" variant="outline" disabled={busy} onClick={() => toggle("policies", p.id, p.enabled, p.name)}>{p.enabled ? "Disable" : "Enable"}</Button>{" "}
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => remove("policies", p.id, p.name)}>Delete</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-lg">New routing policy</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <Field label="Name"><input data-testid="policy-name" className={inputClass} value={policyForm.name} onChange={(e) => setPolicyForm({ ...policyForm, name: e.target.value })} /></Field>
              <Field label="Priority (lower wins)"><input data-testid="policy-priority" className={inputClass} value={policyForm.priority} onChange={(e) => setPolicyForm({ ...policyForm, priority: e.target.value })} /></Field>
              <Field label="Use case (agent_run, agent_resume, completion; empty matches all)"><input data-testid="policy-use-case" className={inputClass} value={policyForm.use_case} onChange={(e) => setPolicyForm({ ...policyForm, use_case: e.target.value })} /></Field>
              <Field label="Sensitivity"><select data-testid="policy-sensitivity" className={inputClass} value={policyForm.sensitivity} onChange={(e) => setPolicyForm({ ...policyForm, sensitivity: e.target.value })}>{SENSITIVITIES.map((s) => <option key={s} value={s}>{s || "any"}</option>)}</select></Field>
              <Field label="Business unit"><input data-testid="policy-business-unit" className={inputClass} value={policyForm.business_unit} onChange={(e) => setPolicyForm({ ...policyForm, business_unit: e.target.value })} /></Field>
              <Field label="Agent id"><input className={inputClass} value={policyForm.agent_id} onChange={(e) => setPolicyForm({ ...policyForm, agent_id: e.target.value })} /></Field>
              <Field label="Language"><input className={inputClass} value={policyForm.language} onChange={(e) => setPolicyForm({ ...policyForm, language: e.target.value })} /></Field>
              <Field label="Provider"><input data-testid="policy-provider" className={inputClass} value={policyForm.provider} onChange={(e) => setPolicyForm({ ...policyForm, provider: e.target.value })} /></Field>
              <Field label="Model"><input data-testid="policy-model" className={inputClass} value={policyForm.model} onChange={(e) => setPolicyForm({ ...policyForm, model: e.target.value })} /></Field>
              <Field label="Tier"><select className={inputClass} value={policyForm.tier} onChange={(e) => setPolicyForm({ ...policyForm, tier: e.target.value })}>{TIERS.map((t) => <option key={t} value={t}>{t || "none"}</option>)}</select></Field>
              <Field label='Targets (JSON: [{"provider","model","weight"}])'><textarea data-testid="policy-targets" className={inputClass} rows={2} value={policyForm.targets} onChange={(e) => setPolicyForm({ ...policyForm, targets: e.target.value })} /></Field>
              <Field label="Allowed providers (comma-separated fence)"><input data-testid="policy-allowed-providers" className={inputClass} value={policyForm.allowed_providers} onChange={(e) => setPolicyForm({ ...policyForm, allowed_providers: e.target.value })} /></Field>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" data-testid="policy-in-region" checked={policyForm.in_region_only} onChange={(e) => setPolicyForm({ ...policyForm, in_region_only: e.target.checked })} />In-region providers only</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" data-testid="policy-cost-aware" checked={policyForm.cost_aware} onChange={(e) => setPolicyForm({ ...policyForm, cost_aware: e.target.checked })} />Cost-aware (cheapest healthy target)</label>
              <Field label="Max failure rate (0 to 1, cost-aware)"><input className={inputClass} value={policyForm.max_failure_rate} onChange={(e) => setPolicyForm({ ...policyForm, max_failure_rate: e.target.value })} /></Field>
              <Field label="Reason"><input data-testid="policy-reason" className={inputClass} value={policyForm.reason} onChange={(e) => setPolicyForm({ ...policyForm, reason: e.target.value })} /></Field>
              <Button data-testid="policy-create" disabled={busy || !policyForm.name.trim()} onClick={createPolicy}>Create policy</Button>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "access" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle className="text-lg">Access policies</CardTitle></CardHeader>
            <CardContent>
              {access.length === 0 ? (
                <p className="text-sm text-muted-foreground">No access policies. Every caller may use the model the routing chose.</p>
              ) : (
                <table className="w-full text-sm" data-testid="access-table">
                  <thead><tr className="text-left text-xs text-muted-foreground"><th>Priority</th><th>Name</th><th>Matches</th><th>Effect</th><th></th></tr></thead>
                  <tbody>
                    {access.map((p) => (
                      <tr key={p.id} className="border-t" data-testid={`access-${p.name}`}>
                        <td className="py-2">{p.priority}</td>
                        <td className="py-2 font-medium">{p.name}{!p.enabled && <Badge variant="secondary" className="ml-2">disabled</Badge>}</td>
                        <td className="py-2">{matchSummary(p)}</td>
                        <td className="py-2">
                          <Badge variant={p.effect === "deny" ? "destructive" : "success"}>{p.effect}</Badge>
                          {p.allowed_providers && <span className="ml-2 text-xs">providers {p.allowed_providers.join(", ")}</span>}
                          {p.allowed_models && <span className="ml-2 text-xs">models {p.allowed_models.join(", ")}</span>}
                        </td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <Button size="sm" variant="outline" disabled={busy} onClick={() => toggle("access-policies", p.id, p.enabled, p.name)}>{p.enabled ? "Disable" : "Enable"}</Button>{" "}
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => remove("access-policies", p.id, p.name)}>Delete</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-lg">New access policy</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <Field label="Name"><input data-testid="access-name" className={inputClass} value={accessForm.name} onChange={(e) => setAccessForm({ ...accessForm, name: e.target.value })} /></Field>
              <Field label="Priority (lower wins)"><input className={inputClass} value={accessForm.priority} onChange={(e) => setAccessForm({ ...accessForm, priority: e.target.value })} /></Field>
              <Field label="Application (an API key's name, agent:<id>, console)"><input data-testid="access-application" className={inputClass} value={accessForm.application} onChange={(e) => setAccessForm({ ...accessForm, application: e.target.value })} /></Field>
              <Field label="Principal (as the audit rows record it)"><input className={inputClass} value={accessForm.principal} onChange={(e) => setAccessForm({ ...accessForm, principal: e.target.value })} /></Field>
              <Field label="Business unit"><input className={inputClass} value={accessForm.business_unit} onChange={(e) => setAccessForm({ ...accessForm, business_unit: e.target.value })} /></Field>
              <Field label="Language"><input data-testid="access-language" className={inputClass} value={accessForm.language} onChange={(e) => setAccessForm({ ...accessForm, language: e.target.value })} /></Field>
              <Field label="Agent id"><input className={inputClass} value={accessForm.agent_id} onChange={(e) => setAccessForm({ ...accessForm, agent_id: e.target.value })} /></Field>
              <Field label="Use case"><input className={inputClass} value={accessForm.use_case} onChange={(e) => setAccessForm({ ...accessForm, use_case: e.target.value })} /></Field>
              <Field label="Sensitivity"><select className={inputClass} value={accessForm.sensitivity} onChange={(e) => setAccessForm({ ...accessForm, sensitivity: e.target.value })}>{SENSITIVITIES.map((s) => <option key={s} value={s}>{s || "any"}</option>)}</select></Field>
              <Field label="Provider chosen by routing"><input className={inputClass} value={accessForm.provider} onChange={(e) => setAccessForm({ ...accessForm, provider: e.target.value })} /></Field>
              <Field label="Model chosen by routing"><input data-testid="access-model" className={inputClass} value={accessForm.model} onChange={(e) => setAccessForm({ ...accessForm, model: e.target.value })} /></Field>
              <Field label="Effect"><select data-testid="access-effect" className={inputClass} value={accessForm.effect} onChange={(e) => setAccessForm({ ...accessForm, effect: e.target.value })}><option value="allow">allow</option><option value="deny">deny</option></select></Field>
              {accessForm.effect === "allow" && (
                <>
                  <Field label="Allowed providers (comma-separated)"><input className={inputClass} value={accessForm.allowed_providers} onChange={(e) => setAccessForm({ ...accessForm, allowed_providers: e.target.value })} /></Field>
                  <Field label="Allowed models (comma-separated)"><input className={inputClass} value={accessForm.allowed_models} onChange={(e) => setAccessForm({ ...accessForm, allowed_models: e.target.value })} /></Field>
                </>
              )}
              <Field label="Reason"><input className={inputClass} value={accessForm.reason} onChange={(e) => setAccessForm({ ...accessForm, reason: e.target.value })} /></Field>
              <Button data-testid="access-create" disabled={busy || !accessForm.name.trim()} onClick={createAccessPolicy}>Create access policy</Button>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "limits" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle className="text-lg">Per-model limits</CardTitle></CardHeader>
            <CardContent>
              {limits.length === 0 ? (
                <p className="text-sm text-muted-foreground">No limits. Every provider and model takes whatever the callers send.</p>
              ) : (
                <table className="w-full text-sm" data-testid="limits-table">
                  <thead><tr className="text-left text-xs text-muted-foreground"><th>Provider</th><th>Model</th><th>Concurrency</th><th>Per minute</th><th></th></tr></thead>
                  <tbody>
                    {limits.map((l) => (
                      <tr key={l.id} className="border-t" data-testid={`limit-${l.provider}-${l.model ?? "all"}`}>
                        <td className="py-2 font-medium">{l.provider}{!l.enabled && <Badge variant="secondary" className="ml-2">disabled</Badge>}</td>
                        <td className="py-2">{l.model ?? <span className="text-muted-foreground">whole provider</span>}</td>
                        <td className="py-2">{l.max_concurrency ?? "—"}</td>
                        <td className="py-2">{l.requests_per_minute ?? "—"}</td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <Button size="sm" variant="outline" disabled={busy} onClick={() => toggle("limits", l.id, l.enabled, `Limit on ${l.provider}`)}>{l.enabled ? "Disable" : "Enable"}</Button>{" "}
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => remove("limits", l.id, `the limit on ${l.provider}${l.model ? ` ${l.model}` : ""}`)}>Delete</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-lg">New limit</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <Field label="Provider"><input data-testid="limit-provider" className={inputClass} value={limitForm.provider} onChange={(e) => setLimitForm({ ...limitForm, provider: e.target.value })} /></Field>
              <Field label="Model (empty for the whole provider)"><input data-testid="limit-model" className={inputClass} value={limitForm.model} onChange={(e) => setLimitForm({ ...limitForm, model: e.target.value })} /></Field>
              <Field label="Max calls in flight"><input data-testid="limit-concurrency" className={inputClass} value={limitForm.max_concurrency} onChange={(e) => setLimitForm({ ...limitForm, max_concurrency: e.target.value })} /></Field>
              <Field label="Calls per minute"><input data-testid="limit-rpm" className={inputClass} value={limitForm.requests_per_minute} onChange={(e) => setLimitForm({ ...limitForm, requests_per_minute: e.target.value })} /></Field>
              <Field label="Reason"><input className={inputClass} value={limitForm.reason} onChange={(e) => setLimitForm({ ...limitForm, reason: e.target.value })} /></Field>
              <Button data-testid="limit-create" disabled={busy || !limitForm.provider.trim()} onClick={createLimit}>Create limit</Button>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "dryrun" && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle className="text-lg">Dry run a request</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <p className="text-xs text-muted-foreground">What the routing and access policies would decide for a described request, whether or not the gateway is on. Limits are not applied; nothing is metered or logged.</p>
              <Field label="Use case"><input data-testid="dryrun-use-case" className={inputClass} value={dryRunForm.use_case} onChange={(e) => setDryRunForm({ ...dryRunForm, use_case: e.target.value })} /></Field>
              <Field label="Requested provider"><input className={inputClass} value={dryRunForm.requested_provider} onChange={(e) => setDryRunForm({ ...dryRunForm, requested_provider: e.target.value })} /></Field>
              <Field label="Requested model"><input data-testid="dryrun-model" className={inputClass} value={dryRunForm.requested_model} onChange={(e) => setDryRunForm({ ...dryRunForm, requested_model: e.target.value })} /></Field>
              <Field label="Sensitivity"><select className={inputClass} value={dryRunForm.sensitivity} onChange={(e) => setDryRunForm({ ...dryRunForm, sensitivity: e.target.value })}>{SENSITIVITIES.map((s) => <option key={s} value={s}>{s || "unknown"}</option>)}</select></Field>
              <Field label="Agent id"><input className={inputClass} value={dryRunForm.agent_id} onChange={(e) => setDryRunForm({ ...dryRunForm, agent_id: e.target.value })} /></Field>
              <Field label="Business unit"><input data-testid="dryrun-business-unit" className={inputClass} value={dryRunForm.business_unit} onChange={(e) => setDryRunForm({ ...dryRunForm, business_unit: e.target.value })} /></Field>
              <Field label="Language"><input data-testid="dryrun-language" className={inputClass} value={dryRunForm.language} onChange={(e) => setDryRunForm({ ...dryRunForm, language: e.target.value })} /></Field>
              <Field label="Application"><input className={inputClass} value={dryRunForm.application} onChange={(e) => setDryRunForm({ ...dryRunForm, application: e.target.value })} /></Field>
              <Field label="Principal"><input className={inputClass} value={dryRunForm.principal} onChange={(e) => setDryRunForm({ ...dryRunForm, principal: e.target.value })} /></Field>
              <Button data-testid="dryrun-run" disabled={busy} onClick={evaluate}>Evaluate</Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-lg">Decision</CardTitle></CardHeader>
            <CardContent>
              {dryRun ? (
                <div className="space-y-2" data-testid="dryrun-result">
                  <Badge variant={dryRun.refused ? "destructive" : "success"}>{dryRun.refused ? "refused" : "routed"}</Badge>
                  <Badge variant="outline" className="ml-2">{dryRun.enabled ? "gateway on" : "gateway off: would apply once on"}</Badge>
                  <pre className="text-xs bg-muted rounded p-2 overflow-auto">{JSON.stringify(dryRun.refused ? dryRun.error ?? dryRun : dryRun.decision, null, 2)}</pre>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Run an evaluation to see the decision.</p>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "records" && (
        <Card>
          <CardHeader><CardTitle className="text-lg">Routing records</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex gap-2 items-end">
              <Field label="Correlation id"><input data-testid="records-filter" className={inputClass} value={recordFilter} onChange={(e) => setRecordFilter(e.target.value)} /></Field>
              <Button data-testid="records-search" variant="outline" onClick={() => fetchRecords(recordFilter)}>Search</Button>
            </div>
            {records === null ? (
              <p className="text-sm text-muted-foreground">Loading records…</p>
            ) : records.length === 0 ? (
              <p className="text-sm text-muted-foreground">No routing records. They are written while the gateway is on and AGENTICORG_MODEL_GATEWAY_RECORDS_ENABLED is set.</p>
            ) : (
              <table className="w-full text-sm" data-testid="records-table">
                <thead><tr className="text-left text-xs text-muted-foreground"><th>When</th><th>Correlation</th><th>Use case</th><th>Model</th><th>Outcome</th><th>Latency</th><th>Tokens</th><th>Cost</th><th>Signed</th></tr></thead>
                <tbody>
                  {records.map((r) => (
                    <tr key={r.id} className="border-t" data-testid={`record-${r.correlation_id}`}>
                      <td className="py-2 whitespace-nowrap">{new Date(r.created_at).toLocaleString()}</td>
                      <td className="py-2 font-mono text-xs">{r.correlation_id}</td>
                      <td className="py-2">{r.use_case}{r.agent_id && <span className="text-xs text-muted-foreground"> · {r.agent_id}</span>}</td>
                      <td className="py-2">{r.provider}/{r.model}{r.fallback_from && <span className="text-xs text-muted-foreground"> (from {r.fallback_from})</span>}</td>
                      <td className="py-2"><Badge variant={r.outcome === "completed" ? "success" : "destructive"}>{r.outcome}</Badge>{r.error_type && <span className="ml-1 text-xs">{r.error_type}</span>}</td>
                      <td className="py-2">{r.latency_ms} ms</td>
                      <td className="py-2">{r.tokens}</td>
                      <td className="py-2">${r.cost_usd.toFixed(5)}</td>
                      <td className="py-2"><Badge variant={r.signed ? "success" : "destructive"}>{r.signed ? "signed" : "tampered"}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "costs" && (
        <Card>
          <CardHeader><CardTitle className="text-lg">Cost comparison</CardTitle></CardHeader>
          <CardContent>
            {costs === null ? (
              <p className="text-sm text-muted-foreground">Loading costs…</p>
            ) : (
              <table className="w-full text-sm" data-testid="costs-table">
                <thead><tr className="text-left text-xs text-muted-foreground"><th>Provider</th><th>Model</th><th>Input / 1M</th><th>Output / 1M</th><th>Blended / 1M</th><th>Calls</th><th>Failure rate</th><th>Avg latency</th><th>Total cost</th></tr></thead>
                <tbody>
                  {costs.map((c) => (
                    <tr key={`${c.provider}/${c.model}`} className="border-t" data-testid={`cost-${c.provider}-${c.model}`}>
                      <td className="py-2">{c.provider}</td>
                      <td className="py-2 font-medium">{c.model}</td>
                      <td className="py-2">{c.list_price ? `$${c.list_price.input_per_million}` : <span className="text-muted-foreground">unpriced</span>}</td>
                      <td className="py-2">{c.list_price ? `$${c.list_price.output_per_million}` : "—"}</td>
                      <td className="py-2">{c.blended_per_million_usd !== null ? `$${c.blended_per_million_usd}` : "—"}</td>
                      <td className="py-2">{c.observed ? c.observed.calls : "—"}</td>
                      <td className="py-2">{c.observed ? `${(c.observed.failure_rate * 100).toFixed(1)}%` : "—"}</td>
                      <td className="py-2">{c.observed ? `${c.observed.avg_latency_ms} ms` : "—"}</td>
                      <td className="py-2">{c.observed ? `$${c.observed.total_cost_usd.toFixed(4)}` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
