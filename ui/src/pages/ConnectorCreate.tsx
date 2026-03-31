import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";

const CATEGORIES = ["finance", "hr", "marketing", "ops", "comms"];
const AUTH_TYPES = ["oauth2", "api_key", "basic", "bolt_bot_token", "certificate", "none"];

const AUTH_FIELD_HINTS: Record<string, string> = {
  oauth2: "Client ID, Client Secret, Token URL",
  api_key: "API key or token",
  basic: "Username and password",
  bolt_bot_token: "Slack Bot User OAuth Token (xoxb-...)",
  certificate: "Certificate path or PEM content",
  none: "No authentication required",
};

export default function ConnectorCreate() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [category, setCategory] = useState("finance");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState("api_key");
  const [secretRef, setSecretRef] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [rateLimitRpm, setRateLimitRpm] = useState(100);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Connector name is required"); return; }
    setSubmitting(true);
    setError("");
    try {
      const authConfig: Record<string, string> = {};
      if (authToken.trim()) {
        if (authType === "bolt_bot_token") authConfig.bot_token = authToken.trim();
        else if (authType === "api_key") authConfig.api_key = authToken.trim();
        else if (authType === "oauth2") authConfig.client_secret = authToken.trim();
        else if (authType === "basic") authConfig.password = authToken.trim();
        else authConfig.token = authToken.trim();
      }
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
    } catch {
      setError("Failed to register connector. Please try again.");
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
                <select value={authType} onChange={(e) => setAuthType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
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
                  <div>
                    <label className="text-sm font-medium">
                      {authType === "bolt_bot_token" ? "Bot Token" : authType === "api_key" ? "API Key" : "Auth Credential"}
                    </label>
                    <input
                      type="password"
                      value={authToken}
                      onChange={(e) => setAuthToken(e.target.value)}
                      placeholder={authType === "bolt_bot_token" ? "xoxb-..." : "Enter credential"}
                      className="border rounded px-3 py-2 text-sm w-full mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-sm font-medium">Secret Reference (optional)</label>
                    <input
                      type="text"
                      value={secretRef}
                      onChange={(e) => setSecretRef(e.target.value)}
                      placeholder="e.g. gcp-secret-manager://slack-bot-token"
                      className="border rounded px-3 py-2 text-sm w-full mt-1"
                    />
                    <p className="text-xs text-muted-foreground mt-1">GCP Secret Manager URI or environment variable name. Used at runtime instead of inline credentials.</p>
                  </div>
                </>
              )}
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
