import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api, { extractApiError } from "@/lib/api";
import { AUTH_TYPES, AUTH_FIELD_HINTS } from "@/lib/connector-constants";

// Uday 2026-05-14 — connector creation is now driven by the backend
// provider registry (``GET /connectors/oauth/providers``). The UI no
// longer hardcodes OAuth field lists per provider: the registry tells us
// which fields each provider needs (region for Zoho, FIU ID for Banking
// AA, GSTIN for GSTN, etc.). Generic ad-hoc connectors still use the
// fallback ``api_key`` / ``basic`` flows from the AUTH_TYPES list.

const CATEGORIES = ["finance", "hr", "marketing", "ops", "comms"];

type ProviderFieldOption = { value: string; label: string };

interface ProviderField {
  key: string;
  label: string;
  placeholder: string;
  help_text: string;
  secret: boolean;
  required: boolean;
  options: ProviderFieldOption[];
}

interface ProviderSchema {
  connector_name: string;
  display_name: string;
  category: string;
  auth_flow: string;
  scopes: string[];
  requires_organization_id: boolean;
  supports_refresh_token: boolean;
  documentation_url: string;
  regions: string[];
  user_fields: ProviderField[];
}

/** Fallback field lists for connectors NOT in the provider registry. */
const FALLBACK_AUTH_FIELDS: Record<string, { key: string; label: string; placeholder: string }[]> = {
  api_key: [{ key: "api_key", label: "API Key", placeholder: "Enter API key" }],
  oauth2: [
    { key: "client_id", label: "Client ID", placeholder: "Enter client ID" },
    { key: "client_secret", label: "Client Secret", placeholder: "Enter client secret" },
  ],
  basic: [
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
  const [extraConfig, setExtraConfig] = useState("");
  const [extraConfigError, setExtraConfigError] = useState("");
  const [rateLimitRpm, setRateLimitRpm] = useState(100);
  const [submitting, setSubmitting] = useState(false);
  const [oauthStarting, setOauthStarting] = useState(false);
  const [error, setError] = useState("");

  // Provider registry catalog.
  const [providers, setProviders] = useState<ProviderSchema[]>([]);
  const [providerKey, setProviderKey] = useState<string>("custom");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get<ProviderSchema[]>("/connectors/oauth/providers");
        if (!cancelled) setProviders(Array.isArray(data) ? data : []);
      } catch {
        // Non-fatal — UI gracefully falls back to the manual flow.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedProvider: ProviderSchema | null = useMemo(() => {
    if (providerKey === "custom") return null;
    return providers.find((p) => p.connector_name === providerKey) ?? null;
  }, [providerKey, providers]);

  function handleProviderSelect(key: string) {
    setProviderKey(key);
    setAuthFields({});
    setError("");
    if (key === "custom") return;
    const spec = providers.find((p) => p.connector_name === key);
    if (!spec) return;
    setName(spec.connector_name);
    setCategory(spec.category);
    // Pre-select the right auth_type so the eventual /connectors POST
    // (for manual flows) is consistent.
    if (spec.auth_flow === "oauth2_authorization_code") setAuthType("oauth2");
    else if (spec.auth_flow === "api_key") setAuthType("api_key");
    else if (spec.auth_flow === "client_credentials") setAuthType("oauth2");
    else setAuthType("none");
  }

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

  function parseExtraConfig(): Record<string, unknown> | null {
    setExtraConfigError("");
    if (extraConfig.trim()) {
      try {
        const parsed = JSON.parse(extraConfig);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setExtraConfigError("Extra config must be a JSON object.");
          return null;
        }
        return parsed as Record<string, unknown>;
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Invalid JSON";
        setExtraConfigError(`Invalid JSON: ${msg}`);
        return null;
      }
    }
    return {};
  }

  function validateProviderFields(spec: ProviderSchema): string | null {
    for (const f of spec.user_fields) {
      if (!f.required) continue;
      const v = (authFields[f.key] || "").trim();
      if (!v) return `${f.label} is required.`;
    }
    return null;
  }

  async function startOAuthAuthorization() {
    if (selectedProvider) {
      const validationError = validateProviderFields(selectedProvider);
      if (validationError) {
        setError(validationError);
        return;
      }
    } else {
      if (!name.trim()) {
        setError("Connector name is required");
        return;
      }
    }
    const extraParsed = parseExtraConfig();
    if (extraParsed === null) return;

    const userFields: Record<string, string> = {};
    if (selectedProvider) {
      for (const f of selectedProvider.user_fields) {
        const value = (authFields[f.key] || "").trim();
        if (value) userFields[f.key] = value;
      }
    } else {
      // Legacy path — generic OAuth2 form.
      const clientId = (authFields.client_id || "").trim();
      const clientSecret = (authFields.client_secret || "").trim();
      if (!clientId || !clientSecret) {
        setError("Client ID and Client Secret are required before authorization.");
        return;
      }
      userFields.client_id = clientId;
      userFields.client_secret = clientSecret;
    }

    setOauthStarting(true);
    setError("");
    try {
      const { data } = await api.post("/connectors/oauth/initiate", {
        connector_name: (selectedProvider?.connector_name || name).trim(),
        user_fields: userFields,
        // Legacy shape — backend still accepts these for older callers.
        client_id: userFields.client_id,
        client_secret: userFields.client_secret,
        base_url: baseUrl.trim() || undefined,
        category: selectedProvider?.category || category,
        extra_config: extraParsed,
      });
      if (!data?.authorization_url) throw new Error("Missing authorization URL");
      window.location.assign(String(data.authorization_url));
    } catch (e: unknown) {
      setError(extractApiError(e, "Failed to start OAuth authorization."));
    } finally {
      setOauthStarting(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (selectedProvider?.auth_flow === "oauth2_authorization_code" || (!selectedProvider && authType === "oauth2")) {
      await startOAuthAuthorization();
      return;
    }
    if (!name.trim()) {
      setError("Connector name is required");
      return;
    }
    const extraParsed = parseExtraConfig();
    if (extraParsed === null) return;
    setSubmitting(true);
    setError("");
    try {
      // Non-OAuth providers persist directly via /connectors.
      const authConfig = { ...buildMultiAuthConfig(), ...extraParsed } as Record<string, unknown>;
      await api.post("/connectors", {
        name: name.trim(),
        category: selectedProvider?.category || category,
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

  const renderFallbackFields = !selectedProvider;
  const provider = selectedProvider;

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
            {providers.length > 0 && (
              <div>
                <label className="text-sm font-medium">Provider</label>
                <select
                  value={providerKey}
                  onChange={(e) => handleProviderSelect(e.target.value)}
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                  data-testid="provider-select"
                >
                  <option value="custom">Custom / generic connector</option>
                  {providers.map((p) => (
                    <option key={p.connector_name} value={p.connector_name}>
                      {p.display_name} ({p.auth_flow.replace(/_/g, " ")})
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground mt-1">
                  Pick a managed provider to use AgenticOrg's automated OAuth flow and
                  provider-specific fields. Pick "Custom" for a generic API connector.
                </p>
              </div>
            )}

            {renderFallbackFields && (
              <div>
                <label className="text-sm font-medium">Connector Name *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Slack, SAP S/4HANA"
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                />
              </div>
            )}

            <div>
              <label className="text-sm font-medium">Base URL</label>
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={provider ? "Defaults to the provider's region API base" : "https://api.example.com"}
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              />
              <p className="text-xs text-muted-foreground mt-1">
                {provider
                  ? "Optional — the provider registry already knows the right base URL for the selected region."
                  : "The API endpoint for this connector (e.g., https://slack.com/api)"}
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium">Category</label>
                <select
                  value={provider?.category || category}
                  onChange={(e) => setCategory(e.target.value)}
                  disabled={Boolean(provider)}
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                >
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
                </select>
              </div>
              {renderFallbackFields && (
                <div>
                  <label className="text-sm font-medium">Auth Type</label>
                  <select value={authType} onChange={(e) => handleAuthTypeChange(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                    {AUTH_TYPES.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>)}
                  </select>
                </div>
              )}
              <div>
                <label className="text-sm font-medium">Rate Limit (RPM)</label>
                <input
                  type="number"
                  value={rateLimitRpm}
                  onChange={(e) => setRateLimitRpm(Number(e.target.value))}
                  min={1}
                  max={10000}
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                />
              </div>
            </div>

            <div className="border rounded-lg p-4 space-y-3 bg-muted/30">
              <p className="text-sm font-medium">Authentication</p>
              {provider ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    {provider.display_name} uses{" "}
                    <code>{provider.auth_flow.replace(/_/g, " ")}</code>.
                    {provider.documentation_url && (
                      <>
                        {" "}
                        <a
                          href={provider.documentation_url}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                        >
                          Provider docs
                        </a>
                      </>
                    )}
                    .
                  </p>
                  {provider.user_fields.map((f) => (
                    <div key={f.key}>
                      <label className="text-sm font-medium" htmlFor={`pf-${f.key}`}>
                        {f.label}
                        {f.required ? " *" : ""}
                      </label>
                      {f.options.length > 0 ? (
                        <select
                          id={`pf-${f.key}`}
                          value={authFields[f.key] || ""}
                          onChange={(e) => setAuthField(f.key, e.target.value)}
                          className="border rounded px-3 py-2 text-sm w-full mt-1"
                          data-testid={`field-${f.key}`}
                        >
                          <option value="">{f.placeholder || "Select…"}</option>
                          {f.options.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          id={`pf-${f.key}`}
                          type={f.secret ? "password" : "text"}
                          value={authFields[f.key] || ""}
                          onChange={(e) => setAuthField(f.key, e.target.value)}
                          placeholder={f.placeholder}
                          className="border rounded px-3 py-2 text-sm w-full mt-1"
                          data-testid={`field-${f.key}`}
                          autoComplete="off"
                        />
                      )}
                      {f.help_text && (
                        <p className="text-xs text-muted-foreground mt-1">{f.help_text}</p>
                      )}
                    </div>
                  ))}
                </>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">{AUTH_FIELD_HINTS[authType] || "Configure authentication credentials"}</p>
                  {authType === "oauth2" && (
                    <p className="text-xs text-muted-foreground">
                      Enter the OAuth app credentials, then authorize in the provider consent screen.
                      AgenticOrg will exchange the code server-side and store the refresh token encrypted.
                    </p>
                  )}
                  {authType !== "none" && (
                    <>
                      {(FALLBACK_AUTH_FIELDS[authType] || []).map((field) => (
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
                </>
              )}
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="flex gap-3">
              <Button type="submit" disabled={submitting || oauthStarting}>
                {provider?.auth_flow === "oauth2_authorization_code"
                  ? (oauthStarting ? "Starting authorization..." : "Authorize Connector")
                  : authType === "oauth2"
                    ? (oauthStarting ? "Starting authorization..." : "Authorize Connector")
                    : (submitting ? "Registering..." : "Register Connector")}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate("/dashboard/connectors")}>Cancel</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
