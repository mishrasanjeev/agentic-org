import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";
import { AUTH_TYPES, authTypeLabel, buildAuthConfig, buildBasicAuthConfig } from "@/lib/connector-constants";

interface ConnectorDetail {
  connector_id: string;
  name: string;
  category: string;
  description: string | null;
  base_url: string | null;
  auth_type: string;
  tool_functions: any[];
  data_schema_ref: string | null;
  rate_limit_rpm: number;
  timeout_ms: number;
  status: string;
  health_check_at: string | null;
  created_at: string | null;
}

export default function ConnectorDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [connector, setConnector] = useState<ConnectorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  // Editable fields
  const [authType, setAuthType] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [baseUrlError, setBaseUrlError] = useState("");
  const [rateLimitRpm, setRateLimitRpm] = useState(60);
  // OAuth2-specific fields
  // UI-OAUTH-001 (Uday/Ramesh 2026-04-24): Edit form previously had
  // only Client ID, Token URL, Redirect URI — Client Secret and
  // Refresh Token were missing, so Gmail-style OAuth2 connectors could
  // not be re-authenticated here. Add the two missing fields and
  // forward them through handleSave.auth_config.
  const [oauth2ClientId, setOauth2ClientId] = useState("");
  const [oauth2ClientSecret, setOauth2ClientSecret] = useState("");
  const [oauth2RefreshToken, setOauth2RefreshToken] = useState("");
  const [oauth2TokenUrl, setOauth2TokenUrl] = useState("");
  const [oauth2RedirectUri, setOauth2RedirectUri] = useState("");
  // TC_008 (Aishwarya 2026-04-23): basic auth needs its own
  // username field in edit — the single-token flow above couldn't
  // carry it and the tester saw missing Username during Edit.
  const [basicUsername, setBasicUsername] = useState("");
  // Uday/Ramesh 2026-04-24 (Zoho org_id): connector-specific extras
  // (Zoho organization_id, NetSuite account, Shopify shop) merged
  // into auth_config on save.
  const [extraConfig, setExtraConfig] = useState("");
  const [extraConfigError, setExtraConfigError] = useState("");
  const [testing, setTesting] = useState(false);

  function validateBaseUrl(url: string): boolean {
    if (!url.trim()) return true; // empty is allowed (optional field)
    return /^https?:\/\/.+/.test(url.trim());
  }

  useEffect(() => {
    fetchConnector();
  }, [id]);

  async function fetchConnector() {
    setLoading(true);
    try {
      const { data } = await api.get(`/connectors/${id}`);
      setConnector(data);
      setAuthType(data.auth_type);
      setBaseUrl(data.base_url || "");
      setRateLimitRpm(data.rate_limit_rpm);
    } catch {
      setFeedback({ type: "error", msg: "Failed to load connector" });
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!validateBaseUrl(baseUrl)) {
      setBaseUrlError("Invalid URL — must start with http:// or https://");
      return;
    }
    setBaseUrlError("");
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
    setSaving(true);
    setFeedback(null);
    try {
      const update: Record<string, any> = {
        auth_type: authType,
        base_url: baseUrl.trim() || undefined,
        rate_limit_rpm: rateLimitRpm,
      };
      if (secretRef.trim()) update.secret_ref = secretRef.trim();
      // TC_008 (Aishwarya 2026-04-23): basic auth takes two fields; the
      // single-field helper didn't carry username. Use the basic-auth
      // helper so both username and password flow through.
      const authConfig =
        authType === "basic"
          ? buildBasicAuthConfig(basicUsername, authToken)
          : buildAuthConfig(authType, authToken);
      // Add OAuth2-specific fields — UI-OAUTH-001 (2026-04-24): Gmail
      // OAuth2 needs client_id + client_secret + refresh_token to mint
      // access tokens; Edit had only client_id.
      if (authType === "oauth2") {
        if (oauth2ClientId.trim()) authConfig.client_id = oauth2ClientId.trim();
        if (oauth2ClientSecret.trim()) authConfig.client_secret = oauth2ClientSecret.trim();
        if (oauth2RefreshToken.trim()) authConfig.refresh_token = oauth2RefreshToken.trim();
        if (oauth2TokenUrl.trim()) authConfig.token_url = oauth2TokenUrl.trim();
        if (oauth2RedirectUri.trim()) authConfig.redirect_uri = oauth2RedirectUri.trim();
      }
      if (extraParsed) {
        for (const [k, v] of Object.entries(extraParsed)) {
          (authConfig as Record<string, unknown>)[k] = v;
        }
      }
      if (Object.keys(authConfig).length > 0) update.auth_config = authConfig;

      const { data } = await api.put(`/connectors/${id}`, update);
      setConnector(data);
      setEditing(false);
      setAuthToken("");
      setSecretRef("");
      setFeedback({ type: "success", msg: "Connector updated successfully" });
    } catch (e: unknown) {
      setFeedback({ type: "error", msg: extractApiError(e, "Failed to update connector") });
    } finally {
      setSaving(false);
    }
  }

  async function handleHealthCheck() {
    setFeedback(null);
    try {
      const { data } = await api.get(`/connectors/${id}/health`);
      setFeedback({ type: data.healthy ? "success" : "error", msg: `Health: ${data.status} (${data.healthy ? "healthy" : "unhealthy"})` });
    } catch (e: unknown) {
      setFeedback({ type: "error", msg: extractApiError(e, "Health check failed") });
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setFeedback(null);
    try {
      const { data } = await api.post(`/connectors/${id}/test`);
      // Uday CA Firms 2026-04-26 BUG 2: backend returns
      //   {tested: bool, name: str, health: {status: str, http_status?: int}, error?: str}
      // The previous handler dispatched on a fictional `success`
      // field on the response — always undefined → always falsy →
      // tester saw "Connection test failed" even when the connector
      // returned status="healthy". Render against the real response
      // shape: error string (test could not run) → fail;
      // tested + health.status==="healthy" → success; everything else
      // (status="unhealthy", "not_configured", "not_connected") shows
      // the specific reason from the health probe.
      const tested = Boolean(data?.tested);
      const status: string = data?.health?.status ?? "";
      const httpStatus = data?.health?.http_status;
      if (data?.error) {
        setFeedback({ type: "error", msg: String(data.error) });
      } else if (!tested) {
        setFeedback({ type: "error", msg: "Connection test did not run" });
      } else if (status === "healthy") {
        setFeedback({
          type: "success",
          msg: httpStatus
            ? `Connection test passed (HTTP ${httpStatus})`
            : "Connection test passed",
        });
      } else if (status) {
        const reason: string = data?.health?.reason || data?.health?.error || "";
        const code = httpStatus ? ` (HTTP ${httpStatus})` : "";
        setFeedback({
          type: "error",
          msg: reason
            ? `Connection ${status}${code}: ${reason}`
            : `Connection ${status}${code}`,
        });
      } else {
        setFeedback({ type: "error", msg: "Connection test returned no health status" });
      }
    } catch (e: unknown) {
      setFeedback({ type: "error", msg: extractApiError(e, "Connection test failed") });
    } finally {
      setTesting(false);
    }
  }

  if (loading) return <p className="text-muted-foreground p-6">Loading connector...</p>;
  if (!connector) return (
    <div className="space-y-4 p-6">
      <p className="text-muted-foreground">Connector not found.</p>
      <a href="/dashboard/connectors" className="text-sm text-primary hover:underline">&larr; Back to Connectors</a>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-bold">{connector.name}</h2>
          <Badge variant={connector.status === "active" ? "default" : "destructive"}>{connector.status}</Badge>
          <Badge variant="outline">{connector.category}</Badge>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleTestConnection} disabled={testing}>{testing ? "Testing..." : "Test Connection"}</Button>
          <Button variant="outline" onClick={handleHealthCheck}>Health Check</Button>
          <Button variant="outline" onClick={() => navigate("/dashboard/connectors")}>Back</Button>
        </div>
      </div>

      {feedback && (
        <div className={`rounded-lg px-4 py-3 text-sm ${feedback.type === "success" ? "bg-green-50 text-green-800 border border-green-200" : "bg-red-50 text-red-800 border border-red-200"}`}>
          {feedback.msg}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Info Card */}
        <Card>
          <CardHeader><CardTitle className="text-sm font-semibold">Connector Info</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex justify-between py-1 border-b"><span className="text-muted-foreground">Name</span><span className="font-medium">{connector.name}</span></div>
            <div className="flex justify-between py-1 border-b"><span className="text-muted-foreground">Category</span><span className="font-medium">{connector.category}</span></div>
            <div className="flex justify-between py-1 border-b"><span className="text-muted-foreground">Base URL</span><span className="font-mono text-xs">{connector.base_url || "—"}</span></div>
            <div className="flex justify-between py-1 border-b"><span className="text-muted-foreground">Auth Type</span><span className="font-medium">{connector.auth_type}</span></div>
            <div className="flex justify-between py-1 border-b"><span className="text-muted-foreground">Rate Limit</span><span>{connector.rate_limit_rpm} RPM</span></div>
            <div className="flex justify-between py-1 border-b"><span className="text-muted-foreground">Timeout</span><span>{connector.timeout_ms}ms</span></div>
            <div className="flex justify-between py-1"><span className="text-muted-foreground">Created</span><span>{connector.created_at ? new Date(connector.created_at).toLocaleString() : "—"}</span></div>
          </CardContent>
        </Card>

        {/* Auth Config Card */}
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle className="text-sm font-semibold">Authentication Configuration</CardTitle>
              {!editing && <Button size="sm" variant="outline" onClick={() => setEditing(true)}>Edit</Button>}
            </div>
          </CardHeader>
          <CardContent>
            {editing ? (
              <div className="space-y-3">
                <div>
                  <label className="text-sm font-medium">Auth Type</label>
                  <select value={authType} onChange={(e) => setAuthType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                    {AUTH_TYPES.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium">Base URL</label>
                  <input
                    type="url"
                    value={baseUrl}
                    onChange={(e) => {
                      setBaseUrl(e.target.value);
                      if (baseUrlError && validateBaseUrl(e.target.value)) setBaseUrlError("");
                    }}
                    onBlur={() => {
                      if (baseUrl.trim() && !validateBaseUrl(baseUrl)) {
                        setBaseUrlError("Invalid URL — must start with http:// or https://");
                      } else {
                        setBaseUrlError("");
                      }
                    }}
                    placeholder="https://api.example.com"
                    className={`border rounded px-3 py-2 text-sm w-full mt-1 ${baseUrlError ? "border-red-500" : ""}`}
                  />
                  {baseUrlError && <p className="text-xs text-red-600 mt-1">{baseUrlError}</p>}
                </div>
                {authType !== "none" && (
                  <>
                    {authType === "oauth2" && (
                      <>
                        <div>
                          <label className="text-sm font-medium">Client ID</label>
                          <input type="text" value={oauth2ClientId} onChange={(e) => setOauth2ClientId(e.target.value)} placeholder="OAuth2 Client ID" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                        </div>
                        {/* UI-OAUTH-001 (Uday/Ramesh 2026-04-24):
                            Gmail / Google OAuth2 needs client_secret +
                            refresh_token on this form or the connector
                            can't mint access tokens at runtime. */}
                        <div>
                          <label className="text-sm font-medium">Client Secret</label>
                          <input type="password" value={oauth2ClientSecret} onChange={(e) => setOauth2ClientSecret(e.target.value)} placeholder="Enter new client secret (leave blank to keep existing)" autoComplete="new-password" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                        </div>
                        <div>
                          <label className="text-sm font-medium">Refresh Token</label>
                          <input type="password" value={oauth2RefreshToken} onChange={(e) => setOauth2RefreshToken(e.target.value)} placeholder="Paste refresh token (required for Gmail/Google APIs)" autoComplete="new-password" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                          <p className="text-xs text-muted-foreground mt-1">Gmail, Google Drive, and Google Calendar connectors require a refresh token obtained via the OAuth consent flow.</p>
                        </div>
                        <div>
                          <label className="text-sm font-medium">Token URL</label>
                          <input type="url" value={oauth2TokenUrl} onChange={(e) => setOauth2TokenUrl(e.target.value)} placeholder="https://accounts.google.com/o/oauth2/token" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                        </div>
                        <div>
                          <label className="text-sm font-medium">Redirect URI</label>
                          <input type="url" value={oauth2RedirectUri} onChange={(e) => setOauth2RedirectUri(e.target.value)} placeholder="https://app.agenticorg.ai/callback" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                        </div>
                      </>
                    )}
                    {/* TC_008 (Aishwarya 2026-04-23): basic auth
                        previously rendered ONLY a single Password
                        input on the Edit screen — the matching
                        Username field was missing, and the label
                        fell through to the generic "Password" helper.
                        Add an explicit Username input so Edit mirrors
                        the Create form. */}
                    {authType === "basic" && (
                      <div>
                        <label className="text-sm font-medium">Username</label>
                        <input
                          type="text"
                          value={basicUsername}
                          onChange={(e) => setBasicUsername(e.target.value)}
                          placeholder="Enter username (leave blank to keep existing)"
                          className="border rounded px-3 py-2 text-sm w-full mt-1"
                          autoComplete="username"
                        />
                      </div>
                    )}
                    {/* Uday CA Firms 2026-04-26 BUG 1: when authType is
                        oauth2 the explicit Client Secret + Refresh
                        Token fields above already cover the OAuth2
                        credential surface. The generic helper field
                        below resolves to authTypeLabel('oauth2') =
                        "Client Secret", producing TWO identically-
                        labelled inputs in the form and confusing
                        admins about which value wins (the explicit
                        field DID win at save time, but the duplicate
                        was a real UX bug). Hide the generic field
                        for oauth2; render it only for the auth types
                        whose credential sits in a single token. */}
                    {authType !== "oauth2" && (
                      <div>
                        <label className="text-sm font-medium">{authTypeLabel(authType)}</label>
                        <input
                          type="password"
                          value={authToken}
                          onChange={(e) => setAuthToken(e.target.value)}
                          placeholder="Enter new credential (leave blank to keep existing)"
                          className="border rounded px-3 py-2 text-sm w-full mt-1"
                          autoComplete={authType === "basic" ? "new-password" : "off"}
                        />
                      </div>
                    )}
                    {/* Uday/Ramesh 2026-04-24 (Zoho org_id): connector-
                        specific extras merged into auth_config. Zoho
                        Books needs ``organization_id``; NetSuite
                        ``account``; Shopify ``shop``. */}
                    <div>
                      <label className="text-sm font-medium">Extra config (JSON)</label>
                      <textarea
                        value={extraConfig}
                        onChange={(e) => setExtraConfig(e.target.value)}
                        placeholder={'{\n  "organization_id": "12345678"\n}'}
                        rows={3}
                        className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono"
                      />
                      <p className="text-xs text-muted-foreground mt-1">Connector-specific parameters (e.g. Zoho Books <code>organization_id</code>).</p>
                      {extraConfigError && <p className="text-xs text-red-600 mt-1">{extraConfigError}</p>}
                    </div>
                    <div>
                      <label className="text-sm font-medium">Secret Reference</label>
                      <input type="text" value={secretRef} onChange={(e) => setSecretRef(e.target.value)} placeholder="gcp-secret-manager://slack-bot-token" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                    </div>
                  </>
                )}
                <div>
                  <label className="text-sm font-medium">Rate Limit (RPM)</label>
                  <input type="number" value={rateLimitRpm} onChange={(e) => setRateLimitRpm(Number(e.target.value))} min={1} max={10000} className="border rounded px-3 py-2 text-sm w-full mt-1" />
                </div>
                <div className="flex gap-2 pt-2">
                  <Button size="sm" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : "Save"}</Button>
                  <Button size="sm" variant="outline" onClick={() => { setEditing(false); setAuthToken(""); setSecretRef(""); setBasicUsername(""); }}>Cancel</Button>
                </div>
              </div>
            ) : (
              <div className="space-y-2 text-sm">
                <p className="text-muted-foreground">Auth type: <span className="font-medium text-foreground">{connector.auth_type}</span></p>
                <p className="text-muted-foreground">Credentials are stored securely and not displayed.</p>
                <p className="text-muted-foreground">Click Edit to update authentication settings.</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Tools Card */}
      <Card>
        <CardHeader><CardTitle className="text-sm font-semibold">Registered Tools ({connector.tool_functions.length})</CardTitle></CardHeader>
        <CardContent>
          {connector.tool_functions.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {connector.tool_functions.map((tool: any, idx: number) => (
                <Badge key={idx} variant="outline">{typeof tool === "string" ? tool : tool.name || JSON.stringify(tool)}</Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Tools are auto-discovered from the connector implementation at runtime.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
