import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, CheckCircle2, RefreshCw, Save } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import api, { extractApiError } from "@/lib/api";

type Category = "CRM" | "Ads" | "Analytics" | "Email";

interface ProviderOption {
  connectorName: string;
  label: string;
  authType: "oauth2" | "api_key";
  requiredCredentials: string[];
  configFields?: string[];
}

interface CategoryStatus {
  category: Category;
  connector_name: string;
  display_name: string;
  source: string;
  readiness_state: string;
  proof_scope: string;
  environment_type: string;
  local_test_only: boolean;
  mock_or_test_double: boolean;
  credential_values_redacted: boolean;
}

interface FormState {
  connectorName: string;
  credentials: Record<string, string>;
  config: Record<string, string>;
}

const CATEGORY_ORDER: Category[] = ["CRM", "Ads", "Analytics", "Email"];

const PROVIDERS: Record<Category, ProviderOption[]> = {
  CRM: [
    {
      connectorName: "hubspot",
      label: "HubSpot Sandbox",
      authType: "oauth2",
      requiredCredentials: ["access_token"],
      configFields: ["account_id"],
    },
    {
      connectorName: "salesforce",
      label: "Salesforce Sandbox",
      authType: "oauth2",
      requiredCredentials: ["instance_url", "refresh_token", "client_id", "client_secret"],
    },
  ],
  Ads: [
    {
      connectorName: "google_ads",
      label: "Google Ads Test Customer",
      authType: "oauth2",
      requiredCredentials: [
        "developer_token",
        "refresh_token",
        "customer_id",
        "client_id",
        "client_secret",
      ],
    },
    {
      connectorName: "meta_ads",
      label: "Meta Ads Sandbox",
      authType: "oauth2",
      requiredCredentials: ["access_token", "ad_account_id"],
    },
    {
      connectorName: "linkedin_ads",
      label: "LinkedIn Ads Sandbox",
      authType: "oauth2",
      requiredCredentials: ["refresh_token", "account_id", "client_id", "client_secret"],
    },
  ],
  Analytics: [
    {
      connectorName: "ga4",
      label: "GA4 Sandbox Property",
      authType: "oauth2",
      requiredCredentials: ["property_id", "refresh_token", "client_id", "client_secret"],
    },
  ],
  Email: [
    {
      connectorName: "sendgrid",
      label: "SendGrid Sandbox",
      authType: "api_key",
      requiredCredentials: ["api_key", "sender_identity"],
    },
    {
      connectorName: "mailchimp",
      label: "Mailchimp Test Account",
      authType: "api_key",
      requiredCredentials: ["api_key", "server_prefix", "audience_id"],
    },
  ],
};

const FIELD_LABELS: Record<string, string> = {
  access_token: "Access Token",
  account_id: "Account ID",
  ad_account_id: "Ad Account ID",
  api_key: "API Key",
  audience_id: "Audience ID",
  client_id: "Client ID",
  client_secret: "Client Secret",
  customer_id: "Customer ID",
  developer_token: "Developer Token",
  instance_url: "Instance URL",
  property_id: "Property ID",
  refresh_token: "Refresh Token",
  sender_identity: "Sender Identity",
  server_prefix: "Server Prefix",
};

function initialState(): Record<Category, FormState> {
  return Object.fromEntries(
    CATEGORY_ORDER.map((category) => [
      category,
      {
        connectorName: PROVIDERS[category][0].connectorName,
        credentials: {},
        config: {},
      },
    ]),
  ) as Record<Category, FormState>;
}

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] || field.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function providerFor(category: Category, connectorName: string): ProviderOption {
  return PROVIDERS[category].find((provider) => provider.connectorName === connectorName) || PROVIDERS[category][0];
}

function credentialInputType(field: string): string {
  return field.includes("url") || field === "instance_url" ? "url" : "password";
}

function compactValues(values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values)
      .map(([key, value]) => [key, value.trim()])
      .filter(([, value]) => value),
  );
}

export default function CMOVendorSandboxConnectors() {
  const navigate = useNavigate();
  const [forms, setForms] = useState<Record<Category, FormState>>(() => initialState());
  const [statusRows, setStatusRows] = useState<CategoryStatus[]>([]);
  const [missingCategories, setMissingCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const statusByCategory = useMemo(
    () => new Map(statusRows.map((row) => [row.category, row])),
    [statusRows],
  );

  async function fetchStatus() {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors/cmo-vendor-sandbox");
      setStatusRows(Array.isArray(data?.categories) ? data.categories : []);
      setMissingCategories(Array.isArray(data?.missing_categories) ? data.missing_categories : []);
    } catch (err: unknown) {
      setFeedback({ type: "error", message: extractApiError(err, "Failed to load CMO connector status") });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchStatus();
  }, []);

  function updateProvider(category: Category, connectorName: string) {
    setForms((current) => ({
      ...current,
      [category]: {
        connectorName,
        credentials: {},
        config: {},
      },
    }));
  }

  function updateCredential(category: Category, key: string, value: string) {
    setForms((current) => ({
      ...current,
      [category]: {
        ...current[category],
        credentials: { ...current[category].credentials, [key]: value },
      },
    }));
  }

  function updateConfig(category: Category, key: string, value: string) {
    setForms((current) => ({
      ...current,
      [category]: {
        ...current[category],
        config: { ...current[category].config, [key]: value },
      },
    }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFeedback(null);

    const missing = CATEGORY_ORDER.flatMap((category) => {
      const provider = providerFor(category, forms[category].connectorName);
      return provider.requiredCredentials
        .filter((field) => !forms[category].credentials[field]?.trim())
        .map((field) => `${category}: ${fieldLabel(field)}`);
    });
    if (missing.length > 0) {
      setFeedback({ type: "error", message: `Missing required fields: ${missing.join(", ")}` });
      return;
    }

    const connectors = Object.fromEntries(
      CATEGORY_ORDER.map((category) => {
        const provider = providerFor(category, forms[category].connectorName);
        return [
          category,
          {
            connector_name: provider.connectorName,
            display_name: provider.label,
            auth_type: provider.authType,
            credentials: compactValues(forms[category].credentials),
            config: compactValues(forms[category].config),
          },
        ];
      }),
    );

    setSaving(true);
    try {
      const { data } = await api.post("/connectors/cmo-vendor-sandbox", { connectors });
      setFeedback({
        type: "success",
        message: data?.message || "CMO vendor-sandbox connectors saved",
      });
      setForms(initialState());
      await fetchStatus();
    } catch (err: unknown) {
      setFeedback({ type: "error", message: extractApiError(err, "Failed to save CMO connectors") });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-2xl font-bold">CMO Vendor Sandbox Connectors</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Configure the tenant CRM, ads, analytics, and email connector rows used by the CMO weekly report preflight.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={() => navigate("/dashboard/connectors")}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Connectors
          </Button>
          <Button type="button" variant="outline" onClick={() => fetchStatus()} disabled={loading}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {feedback && (
        <div
          className={`rounded-md border px-4 py-3 text-sm ${
            feedback.type === "success"
              ? "border-green-200 bg-green-50 text-green-800"
              : "border-red-200 bg-red-50 text-red-800"
          }`}
        >
          {feedback.message}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Current DB Preflight Rows</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading status...</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-4">
              {CATEGORY_ORDER.map((category) => {
                const row = statusByCategory.get(category);
                const ready = row?.source === "db" && row.readiness_state === "ready";
                return (
                  <div key={category} className="rounded-md border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{category}</span>
                      <Badge variant={ready ? "success" : "secondary"}>
                        {ready ? "DB ready" : "Missing"}
                      </Badge>
                    </div>
                    <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                      <p>{row?.display_name || "No ConnectorConfig row"}</p>
                      {row && <p>{row.proof_scope} | {row.environment_type}</p>}
                      {row && <p>local={String(row.local_test_only)} mock={String(row.mock_or_test_double)}</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {missingCategories.length > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              Missing categories: {missingCategories.join(", ")}
            </p>
          )}
        </CardContent>
      </Card>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid gap-4 xl:grid-cols-2">
          {CATEGORY_ORDER.map((category) => {
            const state = forms[category];
            const selectedProvider = providerFor(category, state.connectorName);
            return (
              <Card key={category}>
                <CardHeader>
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="text-sm font-semibold">{category}</CardTitle>
                    <Badge variant="outline">{selectedProvider.authType}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <label className="text-sm font-medium">{category} Provider</label>
                    <select
                      value={state.connectorName}
                      onChange={(event) => updateProvider(category, event.target.value)}
                      className="mt-1 w-full rounded border px-3 py-2 text-sm"
                      aria-label={`${category} Provider`}
                    >
                      {PROVIDERS[category].map((provider) => (
                        <option key={provider.connectorName} value={provider.connectorName}>
                          {provider.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    {selectedProvider.requiredCredentials.map((field) => (
                      <div key={field}>
                        <label className="text-sm font-medium">{fieldLabel(field)}</label>
                        <input
                          type={credentialInputType(field)}
                          value={state.credentials[field] || ""}
                          onChange={(event) => updateCredential(category, field, event.target.value)}
                          className="mt-1 w-full rounded border px-3 py-2 text-sm"
                          autoComplete="off"
                          aria-label={`${category} ${fieldLabel(field)}`}
                        />
                      </div>
                    ))}
                    {(selectedProvider.configFields || []).map((field) => (
                      <div key={field}>
                        <label className="text-sm font-medium">{fieldLabel(field)}</label>
                        <input
                          type="text"
                          value={state.config[field] || ""}
                          onChange={(event) => updateConfig(category, field, event.target.value)}
                          className="mt-1 w-full rounded border px-3 py-2 text-sm"
                          aria-label={`${category} ${fieldLabel(field)} optional`}
                        />
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <CheckCircle2 className="h-4 w-4" />
            Values are encrypted server-side and never returned by the API.
          </div>
          <Button type="submit" disabled={saving}>
            <Save className="mr-2 h-4 w-4" />
            {saving ? "Saving..." : "Save CMO Sandbox Connectors"}
          </Button>
        </div>
      </form>
    </div>
  );
}
