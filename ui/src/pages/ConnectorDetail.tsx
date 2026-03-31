import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";
import { AUTH_TYPES, authTypeLabel, buildAuthConfig } from "@/lib/connector-constants";

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
  const [rateLimitRpm, setRateLimitRpm] = useState(60);

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
    setSaving(true);
    setFeedback(null);
    try {
      const update: Record<string, any> = {
        auth_type: authType,
        base_url: baseUrl.trim() || undefined,
        rate_limit_rpm: rateLimitRpm,
      };
      if (secretRef.trim()) update.secret_ref = secretRef.trim();
      const authConfig = buildAuthConfig(authType, authToken);
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
                  <input type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1" />
                </div>
                {authType !== "none" && (
                  <>
                    <div>
                      <label className="text-sm font-medium">{authTypeLabel(authType)}</label>
                      <input type="password" value={authToken} onChange={(e) => setAuthToken(e.target.value)} placeholder="Enter new credential (leave blank to keep existing)" className="border rounded px-3 py-2 text-sm w-full mt-1" />
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
                  <Button size="sm" variant="outline" onClick={() => { setEditing(false); setAuthToken(""); setSecretRef(""); }}>Cancel</Button>
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
