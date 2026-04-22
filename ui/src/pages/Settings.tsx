import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";

interface FleetLimits {
  max_active_agents: number;
  max_agents_per_domain: Record<string, number>;
  max_shadow_agents: number;
  max_replicas_per_type: number;
}

interface ApiKeyRecord {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  status: string;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

const DEFAULT_LIMITS: FleetLimits = {
  max_active_agents: 35,
  max_agents_per_domain: { finance: 20, hr: 20, marketing: 20, ops: 20, backoffice: 20 },
  max_shadow_agents: 10,
  max_replicas_per_type: 20,
};

type DataRegion = "IN" | "EU" | "US";

interface GovernanceConfig {
  pii_masking: boolean;
  data_region: DataRegion;
  audit_retention_years: number;
  updated_by: string | null;
  updated_at: string | null;
}

interface IntegrationsStatus {
  grantex_configured: boolean;
  composio_configured: boolean;
  ragflow_configured: boolean;
}

export default function Settings() {
  const [limits, setLimits] = useState<FleetLimits>(DEFAULT_LIMITS);
  const [piiMasking, setPiiMasking] = useState(true);
  const [dataRegion, setDataRegion] = useState<DataRegion>("IN");
  const [auditRetention, setAuditRetention] = useState(7);
  const [governanceLoading, setGovernanceLoading] = useState(true);
  const [governanceSaving, setGovernanceSaving] = useState(false);
  const [governanceSaved, setGovernanceSaved] = useState(false);
  const [governanceError, setGovernanceError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Integration status (Grantex/Composio/RAGFlow) — loaded from
  // /integrations/status so the badges reflect real deployment config
  // instead of a hardcoded "Configured" dot.
  const [integrations, setIntegrations] = useState<IntegrationsStatus | null>(null);

  // API Keys state
  const [apiKeys, setApiKeys] = useState<ApiKeyRecord[]>([]);
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyExpiry, setNewKeyExpiry] = useState<string>("");
  const [creatingKey, setCreatingKey] = useState(false);
  const [newKeySecret, setNewKeySecret] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);

  useEffect(() => {
    fetchSettings();
    fetchGovernance();
    fetchApiKeys();
    fetchIntegrations();
  }, []);

  async function fetchIntegrations() {
    try {
      const { data } = await api.get<IntegrationsStatus>("/integrations/status");
      setIntegrations(data);
    } catch {
      setIntegrations(null);
    }
  }

  async function fetchSettings() {
    try {
      const { data } = await api.get("/config/fleet_limits");
      // TC_011 (Aishwarya 2026-04-22): the server used to reply with
      // ``max_agents_per_domain: {}`` when no fleet_limits row existed;
      // the UI replaced the pre-seeded 5-domain map with the empty
      // object, so the input grid rendered zero rows. Merge the server
      // response over the defaults so missing keys don't erase the
      // pre-seeded domains. Backend default now also returns the 6
      // canonical domains pre-populated.
      if (data && typeof data === "object") {
        setLimits({
          ...DEFAULT_LIMITS,
          ...data,
          max_agents_per_domain: {
            ...DEFAULT_LIMITS.max_agents_per_domain,
            ...(data.max_agents_per_domain ?? {}),
          },
        });
      }
    } catch {
      // Use defaults
    }
  }

  async function fetchGovernance() {
    setGovernanceLoading(true);
    try {
      const { data } = await api.get<GovernanceConfig>("/governance/config");
      setPiiMasking(!!data.pii_masking);
      setDataRegion(data.data_region);
      setAuditRetention(data.audit_retention_years);
    } catch {
      // Keep local defaults — the PUT endpoint will create the row on first write.
    } finally {
      setGovernanceLoading(false);
    }
  }

  async function saveGovernance() {
    setGovernanceSaving(true);
    setGovernanceSaved(false);
    setGovernanceError(null);
    try {
      await api.put<GovernanceConfig>("/governance/config", {
        pii_masking: piiMasking,
        data_region: dataRegion,
        audit_retention_years: auditRetention,
      });
      setGovernanceSaved(true);
      setTimeout(() => setGovernanceSaved(false), 3000);
    } catch (e: unknown) {
      const resp = (e as { response?: { status?: number; data?: { detail?: string } } })?.response;
      if (resp?.status === 403) {
        setGovernanceError("You must be a tenant admin to change governance settings.");
      } else {
        setGovernanceError(resp?.data?.detail || "Failed to save governance settings.");
      }
    } finally {
      setGovernanceSaving(false);
    }
  }

  async function fetchApiKeys() {
    try {
      const { data } = await api.get("/org/api-keys");
      setApiKeys(data);
    } catch {
      // Ignore — table may not exist yet
    }
  }

  async function createApiKey() {
    if (!newKeyName.trim()) return;
    setCreatingKey(true);
    setKeyError(null);
    setNewKeySecret(null);
    try {
      const payload: Record<string, unknown> = { name: newKeyName.trim() };
      if (newKeyExpiry) payload.expires_days = parseInt(newKeyExpiry, 10);
      const { data } = await api.post("/org/api-keys", payload);
      setNewKeySecret(data.key);
      setNewKeyName("");
      setNewKeyExpiry("");
      fetchApiKeys();
    } catch (e: any) {
      setKeyError(e?.response?.data?.detail || "Failed to create API key");
    } finally {
      setCreatingKey(false);
    }
  }

  async function revokeApiKey(keyId: string) {
    try {
      await api.delete(`/org/api-keys/${keyId}`);
      fetchApiKeys();
    } catch {
      // Ignore
    }
  }

  function copyToClipboard(text: string) {
    navigator.clipboard.writeText(text);
    setCopiedKey(true);
    setTimeout(() => setCopiedKey(false), 2000);
  }

  async function saveSettings() {
    setSaving(true);
    setSaved(false);
    setSaveError(null);
    try {
      await api.put("/config/fleet_limits", limits);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || "Failed to save settings. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Settings</h2>

      <Card>
        <CardHeader><CardTitle>Fleet Governance Limits</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Max Active Agents</label>
              <input type="number" value={limits.max_active_agents} onChange={(e) => setLimits({ ...limits, max_active_agents: Number(e.target.value) })} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
            <div>
              <label className="text-sm font-medium">Max Shadow Agents</label>
              <input type="number" value={limits.max_shadow_agents} onChange={(e) => setLimits({ ...limits, max_shadow_agents: Number(e.target.value) })} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
            <div>
              <label className="text-sm font-medium">Max Replicas Per Agent Type</label>
              <input type="number" value={limits.max_replicas_per_type} onChange={(e) => setLimits({ ...limits, max_replicas_per_type: Number(e.target.value) })} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Max Agents Per Domain</label>
            <div className="grid grid-cols-5 gap-2 mt-1">
              {Object.entries(limits.max_agents_per_domain).map(([domain, count]) => (
                <div key={domain}>
                  <label className="text-xs text-muted-foreground capitalize">{domain}</label>
                  <input type="number" value={count} onChange={(e) => setLimits({ ...limits, max_agents_per_domain: { ...limits.max_agents_per_domain, [domain]: Number(e.target.value) } })} className="border rounded px-2 py-1 text-sm w-full" />
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card data-testid="governance-card">
        <CardHeader><CardTitle>Compliance & Data</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="text-sm font-medium">PII Masking</label>
              <select
                data-testid="governance-pii-masking"
                disabled={governanceLoading || governanceSaving}
                value={piiMasking ? "enabled" : "disabled"}
                onChange={(e) => setPiiMasking(e.target.value === "enabled")}
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              >
                <option value="enabled">Enabled (required for production)</option>
                <option value="disabled">Disabled (dev only)</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Data Region</label>
              <select
                data-testid="governance-data-region"
                disabled={governanceLoading || governanceSaving}
                value={dataRegion}
                onChange={(e) => setDataRegion(e.target.value as DataRegion)}
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              >
                <option value="IN">India (asia-south1)</option>
                <option value="EU">EU (europe-west1)</option>
                <option value="US">US (us-central1)</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Audit Retention (years)</label>
              <input
                data-testid="governance-audit-retention"
                disabled={governanceLoading || governanceSaving}
                type="number"
                value={auditRetention}
                onChange={(e) => setAuditRetention(Number(e.target.value))}
                min={1}
                max={10}
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button
              data-testid="governance-save"
              onClick={saveGovernance}
              disabled={governanceLoading || governanceSaving}
            >
              {governanceSaving ? "Saving…" : "Save Compliance Settings"}
            </Button>
            {governanceSaved && (
              <span data-testid="governance-saved" className="text-sm text-emerald-600">
                Saved — audit entry written.
              </span>
            )}
            {governanceError && (
              <span data-testid="governance-error" className="text-sm text-red-600">
                {governanceError}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>API Keys</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Generate API keys for SDK, CLI, and MCP server access. Keys are shown only once — store them securely.
          </p>

          {/* New key secret banner */}
          {newKeySecret && (
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
              <p className="text-sm font-medium text-emerald-800 mb-2">API key created! Copy it now — it won't be shown again.</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-white border rounded px-3 py-2 text-xs font-mono text-slate-800 break-all">{newKeySecret}</code>
                <Button size="sm" variant="outline" onClick={() => copyToClipboard(newKeySecret)}>
                  {copiedKey ? "Copied!" : "Copy"}
                </Button>
              </div>
            </div>
          )}

          {/* Create new key */}
          <div className="flex gap-2 items-end">
            <div className="flex-1">
              <label className="text-sm font-medium">Key Name</label>
              <input
                type="text"
                placeholder="e.g. Production SDK, ChatGPT MCP"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              />
            </div>
            <div className="w-40">
              <label className="text-sm font-medium">Expires In</label>
              <select value={newKeyExpiry} onChange={(e) => setNewKeyExpiry(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                <option value="">Never</option>
                <option value="30">30 days</option>
                <option value="90">90 days</option>
                <option value="180">6 months</option>
                <option value="365">1 year</option>
              </select>
            </div>
            <Button onClick={createApiKey} disabled={creatingKey || !newKeyName.trim()}>
              {creatingKey ? "Creating..." : "Generate Key"}
            </Button>
          </div>
          {keyError && <p className="text-sm text-red-600">{keyError}</p>}

          {/* Existing keys */}
          {apiKeys.length > 0 && (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium">Name</th>
                    <th className="text-left px-4 py-2 font-medium">Key Prefix</th>
                    <th className="text-left px-4 py-2 font-medium">Status</th>
                    <th className="text-left px-4 py-2 font-medium">Last Used</th>
                    <th className="text-left px-4 py-2 font-medium">Created</th>
                    <th className="text-right px-4 py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {apiKeys.map((k) => (
                    <tr key={k.id} className="border-t">
                      <td className="px-4 py-2 font-medium">{k.name}</td>
                      <td className="px-4 py-2 font-mono text-xs">{k.prefix}...</td>
                      <td className="px-4 py-2">
                        <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${k.status === "active" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${k.status === "active" ? "bg-green-500" : "bg-red-500"}`} />
                          {k.status}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-muted-foreground">{k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : "Never"}</td>
                      <td className="px-4 py-2 text-muted-foreground">{new Date(k.created_at).toLocaleDateString()}</td>
                      <td className="px-4 py-2 text-right">
                        {k.status === "active" && (
                          <Button size="sm" variant="destructive" onClick={() => revokeApiKey(k.id)}>Revoke</Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {apiKeys.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-4">No API keys yet. Generate one to use with the Python SDK, TypeScript SDK, CLI, or MCP server.</p>
          )}

          {/* Usage examples */}
          <div className="bg-slate-50 rounded-lg p-4 space-y-3">
            <p className="text-sm font-medium">Quick Start</p>
            <div className="grid md:grid-cols-3 gap-3">
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Python SDK</p>
                <pre className="bg-slate-900 text-slate-100 rounded px-3 py-2 text-xs overflow-x-auto"><code>{`pip install agenticorg
from agenticorg import AgenticOrg
client = AgenticOrg(api_key="ao_sk_...")`}</code></pre>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">TypeScript SDK</p>
                <pre className="bg-slate-900 text-slate-100 rounded px-3 py-2 text-xs overflow-x-auto"><code>{`npm i agenticorg-sdk
import { AgenticOrg } from "agenticorg-sdk"
const c = new AgenticOrg({ apiKey: "ao_sk_..." })`}</code></pre>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">MCP Server</p>
                <pre className="bg-slate-900 text-slate-100 rounded px-3 py-2 text-xs overflow-x-auto"><code>{`AGENTICORG_API_KEY=ao_sk_...
npx agenticorg-mcp-server`}</code></pre>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Grantex Integration</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Agents auto-register on Grantex for external access via A2A and MCP protocols.
            Configure your Grantex instance URL below. API key is stored in GCP Secret Manager.
          </p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Grantex Base URL</label>
              <input type="url" defaultValue="https://api.grantex.dev" className="border rounded px-3 py-2 text-sm w-full mt-1" readOnly />
              <p className="text-xs text-muted-foreground mt-1">Configurable for self-hosted Grantex. Set via GRANTEX_BASE_URL env var.</p>
            </div>
            <div>
              <label className="text-sm font-medium">API Key Status</label>
              <div
                className="flex items-center gap-2 mt-2"
                data-testid="grantex-api-key-status"
              >
                {integrations === null ? (
                  <>
                    <span className="w-2.5 h-2.5 rounded-full bg-slate-300" />
                    <span className="text-sm text-muted-foreground">Checking…</span>
                  </>
                ) : integrations.grantex_configured ? (
                  <>
                    <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
                    <span className="text-sm">Configured (GRANTEX_API_KEY set)</span>
                  </>
                ) : (
                  <>
                    <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                    <span className="text-sm">Not configured — set GRANTEX_API_KEY to enable</span>
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Agent Runtime</label>
              <div className="flex items-center gap-2 mt-2">
                <span className="text-sm font-mono bg-muted px-2 py-1 rounded">LangGraph v1.1</span>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">External Protocols</label>
              <div className="flex gap-2 mt-2">
                <span className="text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded">A2A</span>
                <span className="text-xs bg-purple-100 text-purple-800 px-2 py-1 rounded">MCP</span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* TC_010 (Aishwarya 2026-04-22): User Management section was
          missing from the page. Expose the real role-mapping entry
          points (per-company role CRUD lives under Companies →
          Settings → Company Role Mapping) with a clear link, plus
          the tenant-level Grantex admin scope management. Full
          invite UI + SSO provisioning is tracked as a separate
          enhancement; this section at least closes the "where do
          I manage users?" question. */}
      <Card data-testid="user-management-card">
        <CardHeader>
          <CardTitle>User Management</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            Tenant-wide roles are assigned per company. Per-company
            role mapping (partner / manager / senior_associate /
            associate / audit_reviewer) is managed from each
            company's detail page under <span className="font-medium">Settings → Company Role Mapping</span>.
          </p>
          <ul className="list-disc pl-6 space-y-1 text-muted-foreground">
            <li>
              Invite a user: share the signup link
              <code className="ml-1 bg-muted px-1.5 py-0.5 rounded text-xs">{"/signup?invite={tenant_id}"}</code>
              — the first sign-in binds them to this tenant.
            </li>
            <li>
              Promote to tenant admin: issue a Grantex grant with the
              <code className="ml-1 bg-muted px-1.5 py-0.5 rounded text-xs">agenticorg:admin</code> scope.
            </li>
            <li>
              Revoke access: disable the user's company role or
              revoke the API key(s) issued to them.
            </li>
          </ul>
        </CardContent>
      </Card>

      {/* TC_010 (Aishwarya 2026-04-22): Webhook Configuration section
          was missing. We do not yet support arbitrary outbound
          webhooks, but we expose the inbound receivers the platform
          already accepts so callers can wire their existing systems.
          Outbound webhook CRUD is tracked separately. */}
      <Card data-testid="webhooks-card">
        <CardHeader>
          <CardTitle>Webhook Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            AgenticOrg accepts inbound webhooks from the following
            providers. Configure these URLs in the provider's dashboard
            with the signing secret issued from
            <span className="font-medium"> Settings → API Keys</span>.
          </p>
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-2 font-medium">Provider</th>
                <th className="text-left py-2 font-medium">Inbound URL</th>
              </tr>
            </thead>
            <tbody className="text-muted-foreground">
              <tr className="border-b">
                <td className="py-2">SendGrid (Email events)</td>
                <td className="py-2 font-mono text-xs">/api/v1/webhooks/email/sendgrid</td>
              </tr>
              <tr className="border-b">
                <td className="py-2">Mailchimp</td>
                <td className="py-2 font-mono text-xs">/api/v1/webhooks/email/mailchimp</td>
              </tr>
              <tr>
                <td className="py-2">MoEngage</td>
                <td className="py-2 font-mono text-xs">/api/v1/webhooks/email/moengage</td>
              </tr>
            </tbody>
          </table>
          <p className="text-xs text-muted-foreground">
            Outbound webhook delivery (AgenticOrg → your endpoint on
            agent events) is planned — track this feature in the
            <span className="font-medium"> /dashboard/observatory </span>
            subscriptions panel when available.
          </p>
        </CardContent>
      </Card>

      <div className="flex gap-4 items-center">
        <Button onClick={saveSettings} disabled={saving}>{saving ? "Saving..." : "Save Settings"}</Button>
        {saved && <span className="text-sm text-green-600">Settings saved successfully.</span>}
        {saveError && <span className="text-sm text-red-600">{saveError}</span>}
      </div>
    </div>
  );
}
