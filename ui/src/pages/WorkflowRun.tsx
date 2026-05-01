import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

interface StepExecution {
  step_id: string;
  step_type: string;
  status: string;
  agent_id?: string;
  confidence?: number;
  latency_ms?: number;
  error?: string;
  replanned?: boolean;
}

interface RunDetail {
  id: string;
  workflow_def_id: string;
  status: string;
  steps_total: number;
  steps_completed: number;
  started_at: string;
  completed_at?: string;
  steps: StepExecution[];
}

export default function WorkflowRun() {
  const { runId } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelInFlight, setCancelInFlight] = useState(false);

  useEffect(() => {
    if (runId) fetchRun();
  }, [runId]);

  // Auto-refresh while workflow is running
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const interval = setInterval(fetchRun, 3000);
    return () => clearInterval(interval);
  }, [run?.status]);

  async function fetchRun() {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/workflows/runs/${runId}`);
      setRun(data?.run_id ? { ...data, id: data.run_id } : data?.id ? data : null);
    } catch (e: any) {
      setRun(null);
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.response?.data?.message;
      setError(
        status === 404
          ? "Run not found — it may have been deleted, or the URL is wrong."
          : status
            ? `Failed to load workflow run (HTTP ${status})${detail ? `: ${detail}` : ""}`
            : `Failed to load workflow run: ${e?.message || "network error"}`
      );
    } finally {
      setLoading(false);
    }
  }

  async function cancelRun() {
    setCancelInFlight(true);
    setError(null);
    try {
      await api.post(`/workflows/runs/${runId}/cancel`);
      // Re-fetch immediately so the UI reflects the new status without
      // waiting for the next auto-refresh tick.
      await fetchRun();
    } catch (e: any) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.response?.data?.message;
      setError(
        status
          ? `Cancel failed (HTTP ${status})${detail ? `: ${detail}` : ""}`
          : `Cancel failed: ${e?.message || "network error"}`
      );
    } finally {
      setCancelInFlight(false);
    }
  }

  const statusColor: Record<string, string> = {
    running: "warning", completed: "success", failed: "destructive",
    waiting_hitl: "secondary", cancelled: "outline",
  };

  const stepStatusColor: Record<string, string> = {
    completed: "success", failed: "destructive", pending: "outline",
    running: "warning", waiting_hitl: "secondary", skipped: "outline",
    replanned: "outline",
  };

  if (loading) return <p className="text-muted-foreground">Loading workflow run...</p>;
  if (!run) {
    // 2026-04-30 enterprise gap fix: distinguish "load failed" from
    // "run truly doesn't exist" so operators can act on the difference.
    return (
      <div className="space-y-4">
        <div
          role="alert"
          className="rounded border border-destructive/40 bg-destructive/5 p-4 flex items-start gap-3"
        >
          <span className="font-medium text-destructive shrink-0">Error:</span>
          <span className="flex-1">{error || "Run not found."}</span>
          <Button size="sm" variant="outline" onClick={fetchRun}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  const progress = run.steps_total > 0 ? Math.round((run.steps_completed / run.steps_total) * 100) : 0;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Workflow Run</h2>
          <p className="text-sm text-muted-foreground font-mono">{run.id}</p>
        </div>
        <div className="flex gap-2 items-center">
          <Badge variant={(statusColor[run.status] || "default") as any}>{run.status}</Badge>
          {run.status === "running" && (
            <Button
              variant="destructive"
              size="sm"
              onClick={cancelRun}
              disabled={cancelInFlight}
            >
              {cancelInFlight ? "Cancelling…" : "Cancel"}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={fetchRun}>Refresh</Button>
        </div>
      </div>

      {/* Surface fetch / cancel errors so the buttons don't fail silently. */}
      {error && (
        <div
          role="alert"
          className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm flex items-start gap-3"
        >
          <span className="font-medium text-destructive shrink-0">Error:</span>
          <span className="flex-1">{error}</span>
        </div>
      )}

      <div className="grid grid-cols-4 gap-4">
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Progress</CardTitle></CardHeader>
          <CardContent>
            <p className="text-xl font-bold">{run.steps_completed}/{run.steps_total}</p>
            <div className="w-full bg-muted rounded-full h-2 mt-2">
              <div className="bg-primary rounded-full h-2 transition-all" style={{ width: `${progress}%` }} />
            </div>
          </CardContent>
        </Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Started</CardTitle></CardHeader>
          <CardContent><p className="text-sm">{new Date(run.started_at).toLocaleString()}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Completed</CardTitle></CardHeader>
          <CardContent><p className="text-sm">{run.completed_at ? new Date(run.completed_at).toLocaleString() : "In progress"}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Workflow</CardTitle></CardHeader>
          <CardContent><p className="text-sm font-mono">{run.workflow_def_id}</p></CardContent></Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Step Executions</CardTitle></CardHeader>
        <CardContent>
          {run.steps && run.steps.length > 0 ? (
            <div className="space-y-3">
              {run.steps.map((step, i) => (
                <div key={step.step_id} className="flex items-center justify-between border rounded p-3">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-mono text-muted-foreground w-6">{i + 1}</span>
                    <div>
                      <p className="text-sm font-medium">{step.step_id}</p>
                      <p className="text-xs text-muted-foreground">{step.step_type}{step.agent_id ? ` | ${step.agent_id}` : ""}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {step.confidence != null && <span className="text-xs text-muted-foreground">{(step.confidence * 100).toFixed(0)}%</span>}
                    {step.latency_ms != null && <span className="text-xs text-muted-foreground">{step.latency_ms}ms</span>}
                    {(step.replanned || step.status === "replanned") && (
                      <Badge variant="outline" className="bg-orange-100 text-orange-800 border-orange-300" data-testid="replanned-badge">Replanned</Badge>
                    )}
                    <Badge variant={(stepStatusColor[step.status] || "default") as any}>{step.status}</Badge>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No step execution data available.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
