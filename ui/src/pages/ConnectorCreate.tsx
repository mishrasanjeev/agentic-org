import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api, { extractApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { isAdminUser } from "@/lib/roles";
import { AUTH_TYPES, AUTH_FIELD_HINTS } from "@/lib/connector-constants";

const CATEGORIES = ["finance", "hr", "marketing", "ops", "comms"];

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
  custom: [],
  none: [],
};

const NATIVE_AUTH_FIELDS: Record<string, { key: string; label: string; placeholder: string }[]> = {
  whatsapp: [
    { key: "access_token", label: "Meta access token", placeholder: "Enter access token" },
    { key: "phone_number_id", label: "Phone number ID", placeholder: "Enter phone number ID" },
  ],
  twilio: [
    { key: "account_sid", label: "Account SID", placeholder: "Enter account SID" },
    { key: "auth_token", label: "Auth token", placeholder: "Enter auth token" },
  ],
  gmail: [
    { key: "client_id", label: "OAuth client ID", placeholder: "Enter client ID" },
    { key: "client_secret", label: "OAuth client secret", placeholder: "Enter client secret" },
    { key: "refresh_token", label: "OAuth refresh token", placeholder: "Enter refresh token" },
  ],
  pinelabs_plural: [
    { key: "client_id", label: "Client ID", placeholder: "Enter sandbox client ID" },
    { key: "client_secret", label: "Client secret", placeholder: "Enter sandbox client secret" },
    { key: "merchant_id", label: "Merchant ID", placeholder: "Enter merchant ID" },
  ],
};

export default function ConnectorCreate() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedType = import.meta.env.VITE_NATIVE_CONNECTOR_PREFILL_ENABLED === "true"
    ? searchParams.get("type")
    : null;
  const { user } = useAuth();
  // Bug sheet 2026-09-14 rows 17-19/22: admins register shared connectors;
  // other connector roles register personal ones (the backend decides).
  const isAdmin = isAdminUser(user);
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
  const [error, setError] = useState("");
  const [nativeProvider, setNativeProvider] = useState<{ name: string; display_name: string } | null>(null);
  const [providerLoading, setProviderLoading] = useState(Boolean(requestedType));
  const [providerError, setProviderError] = useState("");

  useEffect(() => {
    setNativeProvider(null);
    setProviderError("");
    setProviderLoading(Boolean(requestedType));
    setName("");
    setCategory("finance");
    setBaseUrl("");
    setAuthType("api_key");
    setAuthFields({});
    setSecretRef("");
    setExtraConfig("");
    setExtraConfigError("");
    setRateLimitRpm(100);
    setError("");
    if (!requestedType) return;
    let active = true;
    api.get<{ items: Array<{ name: string; display_name: string; category: string; auth_type: string; base_url?: string }> }>(
      "/connectors/registry",
    ).then(({ data }) => {
      if (!active) return;
      const provider = data.items.find((item) => item.name === requestedType);
      if (!provider) {
        setProviderError("This native connector is not in the current registry. Return to Connectors and choose an available provider.");
        return;
      }
      setNativeProvider(provider);
      setName(provider.name);
      setCategory(provider.category);
      setAuthType(provider.auth_type);
      setBaseUrl(provider.base_url || "");
    }).catch((err: unknown) => {
      if (active) setProviderError(extractApiError(err, "Could not load the native connector registry."));
    }).finally(() => {
      if (active) setProviderLoading(false);
    });
    return () => { active = false; };
  }, [requestedType]);

  function handleAuthTypeChange(newType: string) {
    setAuthType(newType);
    setAuthFields({});
    setError("");
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
    if (!extraConfig.trim()) return {};
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (requestedType && (!nativeProvider || providerError)) return;
    if (!name.trim()) {
      setError("Connector name is required");
      return;
    }
    const extraParsed = parseExtraConfig();
    if (extraParsed === null) return;

    setSubmitting(true);
    setError("");
    try {
      const authConfig = { ...buildMultiAuthConfig(), ...extraParsed } as Record<string, unknown>;
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
        <Button variant="outline" onClick={() => navigate("/dashboard/connectors")}>
          Back to Connectors
        </Button>
      </div>

      {!isAdmin && (
        <div data-testid="personal-connector-note" className="rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-800">
          <p className="font-medium">This connector will be personal</p>
          <p className="text-xs mt-1">Only you and tenant admins can view or change it. Ask a tenant admin to register a shared connector for your team.</p>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Connector Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Provider</label>
              <select
                value={nativeProvider?.name || "custom"}
                disabled
                className="border rounded px-3 py-2 text-sm w-full mt-1 bg-muted"
                data-testid="provider-select"
              >
                <option value={nativeProvider?.name || "custom"}>
                  {nativeProvider?.display_name || "Custom / Generic Connector"}
                </option>
              </select>
            </div>

            <div>
              <label className="text-sm font-medium">Connector Name *</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                readOnly={Boolean(nativeProvider)}
                placeholder="e.g. zoho_books, Slack, SAP S/4HANA"
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              />
            </div>

            <div>
              <label className="text-sm font-medium">Base URL</label>
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com"
                className="border rounded px-3 py-2 text-sm w-full mt-1"
              />
              <p className="text-xs text-muted-foreground mt-1">
                The API endpoint for this connector. Zoho Books regions are inferred from this URL.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label htmlFor="connector-category" className="text-sm font-medium">Category</label>
                <select
                  id="connector-category"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  disabled={Boolean(nativeProvider)}
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                >
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {c.charAt(0).toUpperCase() + c.slice(1)}
                    </option>
                  ))}
                  {nativeProvider && !CATEGORIES.includes(category) && (
                    <option value={category}>{category}</option>
                  )}
                </select>
              </div>
              <div>
                <label htmlFor="connector-auth-type" className="text-sm font-medium">Auth Type</label>
                <select
                  id="connector-auth-type"
                  value={authType}
                  onChange={(e) => handleAuthTypeChange(e.target.value)}
                  disabled={Boolean(nativeProvider)}
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                >
                  {AUTH_TYPES.map((a) => (
                    <option key={a} value={a}>
                      {a.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                    </option>
                  ))}
                  {nativeProvider && !AUTH_TYPES.some((type) => type === authType) && (
                    <option value={authType}>{authType.replace(/_/g, " ")}</option>
                  )}
                </select>
              </div>
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
              <p className="text-xs text-muted-foreground">
                {nativeProvider && NATIVE_AUTH_FIELDS[nativeProvider.name]
                  ? "Enter credentials approved for this provider. Registration does not authorize the upstream account or test runtime access."
                  : AUTH_FIELD_HINTS[authType] || "Configure authentication credentials"}
              </p>
              {authType === "oauth2" && !nativeProvider && (
                <p className="text-xs text-muted-foreground">
                  OAuth2 connectors are registered server-side. For Zoho Books, include organization_id and
                  refresh_token in Extra config so the backend can validate readiness without a browser redirect.
                </p>
              )}
              {authType !== "none" && (
                <>
                  {(NATIVE_AUTH_FIELDS[nativeProvider?.name || ""] || FALLBACK_AUTH_FIELDS[authType] || []).map((field) => (
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
                  </div>
                </>
              )}
              <div>
                <label className="text-sm font-medium">Extra config (optional, JSON)</label>
                <textarea
                  value={extraConfig}
                  onChange={(e) => setExtraConfig(e.target.value)}
                  placeholder={'{\n  "organization_id": "12345678",\n  "refresh_token": "1000.xxxxx"\n}'}
                  rows={4}
                  className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Connector-specific parameters, such as Zoho Books organization_id.
                </p>
                {extraConfigError && <p className="text-xs text-red-600 mt-1">{extraConfigError}</p>}
              </div>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}
            {providerError && <p role="alert" className="text-sm text-destructive">{providerError}</p>}

            <div className="flex gap-3">
              <Button type="submit" disabled={submitting || providerLoading || Boolean(providerError)}>
                {submitting ? "Registering..." : "Register Connector"}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate("/dashboard/connectors")}>
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
