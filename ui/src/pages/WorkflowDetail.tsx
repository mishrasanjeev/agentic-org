import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import api from "@/lib/api";

const LIVE_RUN_STATUSES = new Set(["running", "waiting_hitl", "waiting_delay", "waiting_event"]);

interface WorkflowRunSummary {
  id?: string;
  run_id?: string;
  workflow_def_id: string;
  status: string;
  steps_total?: number | null;
  steps_completed?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
}

function runIdFor(run: WorkflowRunSummary): string {
  return run.run_id || run.id || "";
}

export default function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [workflow, setWorkflow] = useState<any>(null);
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [runsLoading, setRunsLoading] = useState(false);
  const [triggerInFlight, setTriggerInFlight] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    fetchWorkflow();
    fetchRuns();
  }, [id]);

  const latestRun = runs[0] || null;

  useEffect(() => {
    if (!id || !latestRun || !LIVE_RUN_STATUSES.has(latestRun.status)) return;
    const interval = setInterval(() => fetchRuns({ showLoading: false }), 3000);
    return () => clearInterval(interval);
  }, [id, latestRun?.status, latestRun ? runIdFor(latestRun) : ""]);

  async function fetchWorkflow() {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/workflows/${id}`);
      setWorkflow(data);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message;
      setError(detail ? `Workflow not found: ${detail}` : "Workflow not found");
      setWorkflow(null);
    } finally {
      setLoading(false);
    }
  }

  async function fetchRuns(options: { showLoading?: boolean } = {}) {
    if (!id) return;
    if (options.showLoading !== false) setRunsLoading(true);
    setRunsError(null);
    try {
      const { data } = await api.get(`/workflows/${id}/runs`, {
        params: { per_page: 5 },
      });
      setRuns(Array.isArray(data?.items) ? data.items : []);
    } catch (e: any) {
      setRuns([]);
      setRunsError(e?.response?.data?.detail || "Failed to load recent workflow runs");
    } finally {
      setRunsLoading(false);
    }
  }

  async function triggerRun() {
    if (!id) return;
    setTriggerInFlight(true);
    setRunsError(null);
    try {
      const { data } = await api.post(`/workflows/${id}/run`, {});
      if (data?.run_id) {
        navigate(`/dashboard/workflows/${id}/runs/${data.run_id}`);
      } else {
        await fetchRuns({ showLoading: false });
      }
    } catch (e: any) {
      setRunsError(e?.response?.data?.detail || "Failed to trigger workflow run");
    } finally {
      setTriggerInFlight(false);
    }
  }

  const steps = workflow?.definition?.steps || [];
  const statusVariant: Record<string, string> = {
    running: "warning",
    waiting_hitl: "secondary",
    waiting_delay: "secondary",
    waiting_event: "secondary",
    completed: "success",
    failed: "destructive",
    cancelled: "outline",
  };
  const latestProgress = useMemo(() => {
    const total = Number(latestRun?.steps_total || 0);
    const completed = Number(latestRun?.steps_completed || 0);
    return total > 0 ? Math.round((completed / total) * 100) : 0;
  }, [latestRun]);

  if (loading) return <p className="text-muted-foreground">Loading...</p>;
  if (error || !workflow) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">Workflow Not Found</h2>
        <p className="text-muted-foreground">{error || "The requested workflow does not exist."}</p>
        <Button variant="ghost" onClick={() => navigate("/dashboard/workflows")} className="px-0">
          Back to Workflows
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-2xl font-bold">{workflow.name}</h2>
          <p className="text-muted-foreground text-sm mt-1">{workflow.description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant={workflow.is_active ? ("success" as any) : "secondary"}>
            {workflow.is_active ? "Active" : "Inactive"}
          </Badge>
          <Badge variant="outline">v{workflow.version}</Badge>
          <Button size="sm" onClick={triggerRun} disabled={triggerInFlight || !workflow.is_active}>
            {triggerInFlight ? "Starting..." : "Run Now"}
          </Button>
        </div>
      </div>

      {runsError && (
        <div className="rounded-lg bg-red-50 text-red-800 border border-red-200 px-4 py-3 text-sm">
          {runsError}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Domain</p>
          <p className="text-sm font-medium mt-1 capitalize">{workflow.domain || "-"}</p>
        </div>
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Trigger</p>
          <p className="text-sm font-medium mt-1">{workflow.trigger_type || "manual"}</p>
        </div>
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Steps</p>
          <p className="text-sm font-medium mt-1">{steps.length}</p>
        </div>
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Created</p>
          <p className="text-sm font-medium mt-1">{new Date(workflow.created_at).toLocaleDateString()}</p>
        </div>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Latest Run</CardTitle>
          <Button variant="outline" size="sm" onClick={() => fetchRuns()} disabled={runsLoading}>
            Refresh
          </Button>
        </CardHeader>
        <CardContent>
          {runsLoading && runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">Loading recent runs...</p>
          ) : latestRun ? (
            <div className="space-y-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <Badge variant={(statusVariant[latestRun.status] || "default") as any}>
                      {latestRun.status}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">{runIdFor(latestRun)}</span>
                  </div>
                  <p className="text-sm text-muted-foreground mt-2">
                    Started {latestRun.started_at ? new Date(latestRun.started_at).toLocaleString() : "not recorded"}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => navigate(`/dashboard/workflows/${id}/runs/${runIdFor(latestRun)}`)}
                >
                  View Run
                </Button>
              </div>
              <div>
                <div className="flex justify-between text-sm">
                  <span>Execution progress</span>
                  <span>
                    {latestRun.steps_completed ?? 0}/{latestRun.steps_total ?? 0}
                  </span>
                </div>
                <div className="w-full bg-muted rounded-full h-2 mt-2">
                  <div className="bg-primary rounded-full h-2 transition-all" style={{ width: `${latestProgress}%` }} />
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No workflow runs yet.</p>
          )}
        </CardContent>
      </Card>

      {steps.length > 0 && (
        <div className="border rounded-lg p-6">
          <h3 className="text-lg font-semibold mb-4">Workflow Steps</h3>
          <div className="space-y-3">
            {steps.map((step: any, i: number) => (
              <div key={step.id || i} className="flex items-center gap-4">
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-bold">
                  {i + 1}
                </div>
                <div className="flex-1 border rounded-lg p-3">
                  <p className="font-medium text-sm">{step.id}</p>
                  <p className="text-xs text-muted-foreground">
                    {step.type === "hitl" || step.type === "human_in_loop"
                      ? "Human-in-the-loop approval"
                      : `Agent: ${step.agent || step.agent_type || "-"}`}
                  </p>
                </div>
                {i < steps.length - 1 && <div className="text-muted-foreground text-lg">-&gt;</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      <Button variant="ghost" onClick={() => navigate("/dashboard/workflows")} className="px-0 text-muted-foreground">
        Back to Workflows
      </Button>
    </div>
  );
}
