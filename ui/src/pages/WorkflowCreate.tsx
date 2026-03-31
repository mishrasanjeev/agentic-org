import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";

const TRIGGER_TYPES = ["manual", "schedule", "webhook", "api_event", "email_received"];
const DOMAINS = ["finance", "hr", "marketing", "ops", "backoffice"];

const STEP_TEMPLATE = JSON.stringify([
  {
    step: 1,
    name: "Step 1",
    agent_type: "ap_processor",
    action: "process",
    inputs: {},
    on_success: "next",
    on_failure: "halt",
  },
], null, 2);

export default function WorkflowCreate() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [domain, setDomain] = useState("finance");
  const [triggerType, setTriggerType] = useState("manual");
  const [stepsJson, setStepsJson] = useState(STEP_TEMPLATE);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  function validateSteps(): { valid: boolean; parsed: any[] } {
    try {
      const parsed = JSON.parse(stepsJson);
      if (!Array.isArray(parsed)) return { valid: false, parsed: [] };
      return { valid: true, parsed };
    } catch {
      return { valid: false, parsed: [] };
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Workflow name is required"); return; }
    const { valid, parsed } = validateSteps();
    if (!valid) { setError("Steps must be valid JSON array. Check syntax and try again."); return; }
    setSubmitting(true);
    setError("");
    try {
      const { data } = await api.post("/workflows", {
        name: name.trim(),
        version,
        domain,
        trigger_type: triggerType,
        definition: { steps: parsed },
      });
      navigate(`/dashboard/workflows/${data.id || ""}`);
    } catch {
      setError("Failed to create workflow. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const stepsValid = validateSteps().valid;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Create Workflow</h2>
        <Button variant="outline" onClick={() => navigate("/dashboard/workflows")}>Back to Workflows</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Workflow Configuration</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Workflow Name *</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Invoice Processing Pipeline" className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium">Version</label>
                <input type="text" value={version} onChange={(e) => setVersion(e.target.value)} placeholder="1.0.0" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
              <div>
                <label className="text-sm font-medium">Domain</label>
                <select value={domain} onChange={(e) => setDomain(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {DOMAINS.map((d) => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Trigger Type</label>
                <select value={triggerType} onChange={(e) => setTriggerType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {TRIGGER_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>)}
                </select>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-sm font-medium">Define Steps (JSON) *</label>
                {!stepsValid && stepsJson.trim() && (
                  <span className="text-xs text-destructive">Invalid JSON</span>
                )}
              </div>
              <textarea
                value={stepsJson}
                onChange={(e) => setStepsJson(e.target.value)}
                placeholder='[{"step": 1, "name": "Step 1", "agent_type": "ap_processor", "action": "process", "inputs": {}}]'
                className={`border rounded px-3 py-2 text-sm w-full mt-1 font-mono ${!stepsValid && stepsJson.trim() ? "border-destructive" : ""}`}
                rows={10}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Define workflow steps as a JSON array. Each step should have: step (number), name, agent_type, action, inputs, on_success, on_failure.
              </p>
            </div>

            <div className="bg-muted/50 rounded-lg p-4 text-sm text-muted-foreground">
              <p>After creation, you can use the visual Workflow Builder to modify steps and configure agent orchestration.</p>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="flex gap-3">
              <Button type="submit" disabled={submitting}>{submitting ? "Creating..." : "Create Workflow"}</Button>
              <Button type="button" variant="outline" onClick={() => navigate("/dashboard/workflows")}>Cancel</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
