import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import KillSwitch from "@/components/KillSwitch";
import type { Agent } from "@/types";

export default function AgentDetail() {
  const { id } = useParams();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"overview" | "config" | "shadow" | "cost">("overview");

  useEffect(() => {
    if (id) fetchAgent();
  }, [id]);

  async function fetchAgent() {
    setLoading(true);
    try {
      const resp = await fetch(`/api/v1/agents/${id}`);
      if (!resp.ok) { setAgent(null); return; }
      const data = await resp.json();
      setAgent(data?.id ? data : null);
    } catch {
      setAgent(null);
    } finally {
      setLoading(false);
    }
  }

  async function handlePromote() {
    await fetch(`/api/v1/agents/${id}/promote`, { method: "POST" });
    fetchAgent();
  }

  async function handleRollback() {
    await fetch(`/api/v1/agents/${id}/rollback`, { method: "POST" });
    fetchAgent();
  }

  if (loading) return <p className="text-muted-foreground">Loading agent...</p>;
  if (!agent) return <p className="text-muted-foreground">Agent not found.</p>;

  const statusColor: Record<string, string> = {
    active: "success", shadow: "warning", paused: "destructive",
    staging: "secondary", deprecated: "outline",
  };

  const confidenceFloor = agent.confidence_floor != null ? `${(agent.confidence_floor * 100).toFixed(0)}%` : "N/A";
  const shadowAccuracy = agent.shadow_accuracy_current != null ? `${(agent.shadow_accuracy_current * 100).toFixed(1)}%` : "N/A";

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">{agent.name || `Agent ${id}`}</h2>
          <p className="text-sm text-muted-foreground">{agent.agent_type || "Unknown type"} | {agent.domain || "Unknown domain"}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handlePromote}>Promote</Button>
          <Button variant="outline" size="sm" onClick={handleRollback}>Rollback</Button>
          <KillSwitch agentId={id || ""} agentName={agent.name || "Agent"} onPaused={fetchAgent} />
        </div>
      </div>

      <div className="grid grid-cols-5 gap-4">
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Status</CardTitle></CardHeader>
          <CardContent><Badge variant={(statusColor[agent.status] || "default") as any}>{agent.status || "unknown"}</Badge></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Version</CardTitle></CardHeader>
          <CardContent><p className="text-xl font-bold">{agent.version || "—"}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Confidence Floor</CardTitle></CardHeader>
          <CardContent><p className="text-xl font-bold">{confidenceFloor}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Shadow Samples</CardTitle></CardHeader>
          <CardContent><p className="text-xl font-bold">{agent.shadow_sample_count ?? 0}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Shadow Accuracy</CardTitle></CardHeader>
          <CardContent><p className="text-xl font-bold">{shadowAccuracy}</p></CardContent></Card>
      </div>

      <div className="flex gap-4 border-b pb-2">
        {(["overview", "config", "shadow", "cost"] as const).map((tab) => (
          <button key={tab} onClick={() => setActiveTab(tab)} className={`px-3 py-1 text-sm font-medium capitalize ${activeTab === tab ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <Card>
          <CardContent className="pt-6">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-muted-foreground">Agent ID:</span> <span className="font-mono">{agent.id}</span></div>
              <div><span className="text-muted-foreground">Domain:</span> {agent.domain}</div>
              <div><span className="text-muted-foreground">Agent Type:</span> {agent.agent_type}</div>
              <div><span className="text-muted-foreground">Created:</span> {new Date(agent.created_at).toLocaleString()}</div>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === "config" && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Agent configuration, authorized tools, HITL policy, and LLM settings are displayed here.</p>
          </CardContent>
        </Card>
      )}

      {activeTab === "shadow" && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Shadow comparison results, quality gate metrics, and promotion readiness are tracked here.</p>
          </CardContent>
        </Card>
      )}

      {activeTab === "cost" && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Daily token usage, cost tracking, and budget utilization are displayed here.</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
