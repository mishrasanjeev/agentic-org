import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const DOMAINS = ["finance", "hr", "marketing", "ops", "backoffice"];
const AGENT_TYPES: Record<string, string[]> = {
  finance: ["ap_processor", "ar_collections", "expense_auditor", "tax_filing", "revenue_forecaster", "recon_agent"],
  hr: ["talent_acquisition", "onboarding", "payroll", "performance", "learning_dev", "offboarding"],
  marketing: ["content_gen", "seo_optimizer", "campaign_analytics", "social_scheduler", "lead_scoring"],
  ops: ["support_triage", "vendor_manager", "contract_intel", "compliance_guard", "it_ops"],
  backoffice: ["legal_ops", "risk_sentinel", "facilities"],
};

export default function AgentCreate() {
  const navigate = useNavigate();
  const [domain, setDomain] = useState("finance");
  const [agentType, setAgentType] = useState(AGENT_TYPES.finance[0]);
  const [name, setName] = useState("");
  const [confidenceFloor, setConfidenceFloor] = useState(0.85);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Agent name is required"); return; }
    setSubmitting(true);
    setError("");
    try {
      const resp = await fetch("/api/v1/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), agent_type: agentType, domain, confidence_floor: confidenceFloor, status: "shadow" }),
      });
      if (!resp.ok) { setError(`Failed to create agent (${resp.status})`); return; }
      const data = await resp.json();
      navigate(`/dashboard/agents/${data.id || ""}`);
    } catch {
      setError("Failed to create agent. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Create Agent</h2>
        <Button variant="outline" onClick={() => navigate("/dashboard/agents")}>Back to Agents</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Agent Configuration</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Agent Name *</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Invoice Processor - APAC" className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">Domain</label>
                <select value={domain} onChange={(e) => { setDomain(e.target.value); setAgentType(AGENT_TYPES[e.target.value][0]); }} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {DOMAINS.map((d) => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Agent Type</label>
                <select value={agentType} onChange={(e) => setAgentType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {AGENT_TYPES[domain].map((t) => <option key={t} value={t}>{t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>)}
                </select>
              </div>
            </div>

            <div>
              <label className="text-sm font-medium">Confidence Floor: {(confidenceFloor * 100).toFixed(0)}%</label>
              <input type="range" min={0.5} max={0.99} step={0.01} value={confidenceFloor} onChange={(e) => setConfidenceFloor(Number(e.target.value))} className="w-full mt-1" />
              <p className="text-xs text-muted-foreground mt-1">Agent will escalate to HITL when confidence drops below this threshold.</p>
            </div>

            <div className="bg-muted/50 rounded-lg p-4 text-sm text-muted-foreground">
              <p>New agents start in <strong>Shadow Mode</strong> by default. They will observe and produce outputs alongside the existing process without taking any actions. Promote to Active after validation.</p>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="flex gap-3">
              <Button type="submit" disabled={submitting}>{submitting ? "Creating..." : "Create Agent"}</Button>
              <Button type="button" variant="outline" onClick={() => navigate("/dashboard/agents")}>Cancel</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
