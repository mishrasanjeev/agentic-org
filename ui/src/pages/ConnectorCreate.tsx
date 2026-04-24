import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api, { extractApiError } from "@/lib/api";
import { AUTH_TYPES, AUTH_FIELD_HINTS } from "@/lib/connector-constants";

const CATEGORIES = ["finance", "hr", "marketing", "ops", "comms"];

/** Which credential fields to show for each auth type */
const AUTH_TYPE_FIELDS: Record<string, { key: string; label: string; placeholder: string }[]> = {
  api_key: [
    { key: "api_key", label: "API Key", placeholder: "Enter API key" },
  ],
  oauth2: [
    { key: "client_id", label: "Client ID", placeholder: "Enter client ID" },
    { key: "client_secret", label: "Client Secret", placeholder: "Enter client secret" },
    { key: "refresh_token", label: "Refresh Token", placeholder: "Enter refresh token (optional)" },
  ],
  basic: [
    // TC_008 (Aishwarya 2026-04-23): basic-auth creates were landing
    // username/password into api_key/api_secret keys, but the native
    // connector classes (e.g. connectors/ops/servicenow.py,
    // connectors/marketing/salesforce.py) read config["username"] /
    // config["password"] — so credentials saved via Create never made
    // it to the connector. Use the keys the connectors actually read.
    { key: "username", label: "Username", placeholder: "Enter username" },
    { key: "password", label: "Password", placeholder: "Enter password" },
  ],
  bolt_bot_token: [
    { key: "api_key", label: "Bot Token", placeholder: "xoxb-..." },
    { key: "api_secret", label: "Signing Secret", placeholder: "Enter signing secret (optional)" },
  ],
  certificate: [
    { key: "client_id", label: "Client ID", placeholder: "Enter client ID" },
    { key: "client_secret", label: "Certificate / PEM", placeholder: "Paste certificate content" },
  ],
  none: [],
};

export default function ConnectorCreate() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [category, setCategory] = useState("finance");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState("api_key");
  const [secretRef, setSecretRef] = useState("");
  const [authFields, setAuthFields] = useState<Record<string, string>>({});
  // Uday/Ramesh 2026-04-24 (Zoho org_id): extra connector-specific
  // config key=value pairs (JSON) merged into auth_config. Zoho Books
  // needs ``organization_id``; NetSuite needs ``account``; HubSpot
  // needs ``portal_id``; Shopify needs ``shop``. Rather than hardcode
  // per-connector fields, accept a JSON blob.
  const [extraConfig, setExtraConfig] = useState("");
  const [extraConfigError, setExtraConfigError] = useState("");
  const [rateLimitRpm, setRateLimitRpm] = useState(100);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  function handleAuthTypeChange(newType: string) {
    setAuthType(newType);
    setAuthFields({});
  }

  function setAuthField(key: string, value: string) {
    setAuthFields((prev) => ({ ...prev, [key]: value }));
  }

  function buildMultiAuthConfig(): Record<string, string> {
    const config: Record<string, string> = {};
    for (const [key, value] of Object.entries(authFields)) {
      if (value.trim()) config[key] = value.trim();
    }
    return config;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Connector name is required"); return; }
    setExtraConfigError("");
    let extraParsed: Record<string, unknown> | null = null;
    if (extraConfig.trim()) {
      try {
        const parsed = JSON.parse(extraConfig);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setExtraConfigError("Extra config must be a JSON object.");
          return;
        }
        extraParsed = parsed as Record<string, unknown>;
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Invalid JSON";
        setExtraConfigError(`Invalid JSON: ${msg}`);
        return;
      }
    }
    setSubmitting(true);
    setError("");
    try {
      const authConfig = { ...buildMultiAuthConfig(), ...(extraParsed || {}) } as Record<string, unknown>;
      await api.post("/connectors", {
        name: name.trim(),
        category,
        base_url: baseUrl.trim() || undefined,
        auth_type: authType,
        auth_config: Object.keys(authConfig).length > 0 ? authConfig : undefined,
        secret_ref: secretRef.trim() || undefined,
        rate_limit_rpm: rateLimitRpm,
      });
      navigate("/dashboard/connectors");
    } catch (e: unknown) {
      setError(extractApiError(e, "Failed to register connector. Please try again."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Register Connector</h2>
        <Button variant="outline" onClick={() => navigate("/dashboard/connectors")}>Back to Connectors</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Connector Configuration</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Connector Name *</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Slack, SAP S/4HANA" className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>

            <div>
              <label className="text-sm font-medium">Base URL</label>
              <input type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.example.com" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              <p className="text-xs text-muted-foreground mt-1">The API endpoint for this connector (e.g., https://slack.com/api)</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium">Category</label>
                <select value={category} onChange={(e) => setCategory(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Auth Type</label>
                <select value={authType} onChange={(e) => handleAuthTypeChange(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {AUTH_TYPES.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Rate Limit (RPM)</label>
                <input type="number" value={rateLimitRpm} onChange={(e) => setRateLimitRpm(Number(e.target.value))} min={1} max={10000} className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
            </div>

            <div className="border rounded-lg p-4 space-y-3 bg-muted/30">
              <p className="text-sm font-medium">Authentication</p>
              <p className="text-xs text-muted-foreground">{AUTH_FIELD_HINTS[authType] || "Configure authentication credentials"}</p>
              {authType !== "none" && (
                <>
                  {(AUTH_TYPE_FIELDS[authType] || []).map((field) => (
                    <div key={field.key}>
                      <label className="text-sm font-medium">{field.label}</label>
                      <input
                        type="password"
                        value={authFields[field.key] || ""}
                        onChange={(e) => setAuthField(field.key, e.target.value)}
                        placeholder={field.placeholder}
                        className="border rounded px-3 py-2 text-sm w-full mt-1"
                      />
                    </div>
                  ))}
                  <div>
                    <label className="text-sm font-medium">Secret Reference (optional)</label>
                    <input
                      type="text"
                      value={secretRef}
                      onChange={(e) => setSecretRef(e.target.value)}
                      placeholder="e.g. gcp://projects/my-project/secrets/my-secret/versions/latest"
                      className="border rounded px-3 py-2 text-sm w-full mt-1"
                    />
                    <p className="text-xs text-muted-foreground mt-1">GCP Secret Manager URI. Used at runtime instead of inline credentials.</p>
                  </div>
                </>
              )}
              {/* Uday/Ramesh 2026-04-24 (Zoho org_id): connector-
                  specific extras that don't fit the auth template —
                  Zoho Books needs ``organization_id``, NetSuite needs
                  ``account``, Shopify needs ``shop``. Merged into
                  auth_config server-side. */}
              <div>
                <label className="text-sm font-medium">Extra config (optional, JSON)</label>
                <textarea
                  value={extraConfig}
                  onChange={(e) => setExtraConfig(e.target.value)}
                  placeholder={'{\n  "organization_id": "12345678"\n}'}
                  rows={3}
                  className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono"
                />
                <p className="text-xs text-muted-foreground mt-1">Connector-specific parameters (e.g. Zoho Books <code>organization_id</code>, NetSuite <code>account</code>, Shopify <code>shop</code>).</p>
                {extraConfigError && <p className="text-xs text-red-600 mt-1">{extraConfigError}</p>}
              </div>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="flex gap-3">
              <Button type="submit" disabled={submitting}>{submitting ? "Registering..." : "Register Connector"}</Button>
              <Button type="button" variant="outline" onClick={() => navigate("/dashboard/connectors")}>Cancel</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
