import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const TRIGGER_TYPES = ["manual", "schedule", "webhook", "event"];

export default function WorkflowCreate() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [triggerType, setTriggerType] = useState("manual");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Workflow name is required"); return; }
    setSubmitting(true);
    setError("");
    try {
      const resp = await fetch("/api/v1/workflows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), trigger_type: triggerType, steps: [] }),
      });
      if (!resp.ok) { setError(`Failed to create workflow (${resp.status})`); return; }
      const data = await resp.json();
      navigate(`/dashboard/workflows/${data.id || ""}`);
    } catch {
      setError("Failed to create workflow. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

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

            <div>
              <label className="text-sm font-medium">Trigger Type</label>
              <select value={triggerType} onChange={(e) => setTriggerType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                {TRIGGER_TYPES.map((t) => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
              </select>
            </div>

            <div className="bg-muted/50 rounded-lg p-4 text-sm text-muted-foreground">
              <p>After creation, use the Workflow Builder to add steps and configure agent orchestration.</p>
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
