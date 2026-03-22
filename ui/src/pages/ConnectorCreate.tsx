import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";

const CATEGORIES = ["finance", "hr", "marketing", "ops", "comms"];
const AUTH_TYPES = ["oauth2", "api_key", "basic", "certificate", "none"];

export default function ConnectorCreate() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [category, setCategory] = useState("finance");
  const [authType, setAuthType] = useState("api_key");
  const [rateLimitRpm, setRateLimitRpm] = useState(100);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Connector name is required"); return; }
    setSubmitting(true);
    setError("");
    try {
      await api.post("/connectors", { name: name.trim(), category, auth_type: authType, rate_limit_rpm: rateLimitRpm });
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
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. SAP S/4HANA" className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium">Category</label>
                <select value={category} onChange={(e) => setCategory(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Auth Type</label>
                <select value={authType} onChange={(e) => setAuthType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {AUTH_TYPES.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ").toUpperCase()}</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Rate Limit (RPM)</label>
                <input type="number" value={rateLimitRpm} onChange={(e) => setRateLimitRpm(Number(e.target.value))} min={1} max={10000} className="border rounded px-3 py-2 text-sm w-full mt-1" />
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
