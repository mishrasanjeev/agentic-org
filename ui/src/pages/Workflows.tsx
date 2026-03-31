import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import type { Workflow } from "@/types";

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWorkflows();
  }, []);

  async function fetchWorkflows() {
    setLoading(true);
    try {
      const { data } = await api.get("/workflows");
      const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setWorkflows(items);
    } catch {
      setWorkflows([]);
    } finally {
      setLoading(false);
    }
  }

  async function triggerRun(wfId: string) {
    setError(null);
    try {
      const { data } = await api.post(`/workflows/${wfId}/run`, {});
      if (data.run_id) {
        navigate(`/dashboard/workflows/${wfId}/runs/${data.run_id}`);
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to trigger workflow run");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Workflows</h2>
        <Button onClick={() => navigate("/dashboard/workflows/new")}>Create Workflow</Button>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 text-red-800 border border-red-200 px-4 py-3 text-sm">{error}</div>
      )}

      {loading ? (
        <p className="text-muted-foreground">Loading workflows...</p>
      ) : workflows.length === 0 ? (
        <p className="text-muted-foreground">No workflows configured yet.</p>
      ) : (
        <div className="space-y-4">
          {workflows.map((wf) => (
            <Card key={wf.id} className="hover:shadow-md transition-shadow">
              <CardHeader>
                <div className="flex justify-between items-center">
                  <CardTitle className="text-base">{wf.name}</CardTitle>
                  <div className="flex items-center gap-2">
                    <Badge variant={wf.is_active ? "success" as any : "secondary"}>{wf.is_active ? "Active" : "Inactive"}</Badge>
                    <span className="text-sm text-muted-foreground">v{wf.version}</span>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex justify-between items-center">
                  <div className="text-sm text-muted-foreground">
                    Trigger: <span className="font-medium">{wf.trigger_type || "manual"}</span>
                    {" | "}Created: <span className="font-medium">{new Date(wf.created_at).toLocaleDateString()}</span>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => navigate(`/dashboard/workflows/${wf.id}`)}>View</Button>
                    <Button size="sm" onClick={() => triggerRun(wf.id)}>Run Now</Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
