import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import KillSwitch from "@/components/KillSwitch";
import api from "@/lib/api";
import type { Agent } from "@/types";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell,
} from "recharts";

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
      const resp = await api.get(`/agents/${id}`);
      const data = resp.data;
      setAgent(data?.id ? data : null);
    } catch {
      setAgent(null);
    } finally {
      setLoading(false);
    }
  }

  async function handlePromote() {
    await api.post(`/agents/${id}/promote`);
    fetchAgent();
  }

  async function handleRollback() {
    await api.post(`/agents/${id}/rollback`);
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
          <CardContent><p className="text-xl font-bold">{agent.version || "\u2014"}</p></CardContent></Card>
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

      {activeTab === "overview" && <OverviewTab agent={agent} />}
      {activeTab === "config" && <ConfigTab agent={agent} />}
      {activeTab === "shadow" && <ShadowTab agent={agent} />}
      {activeTab === "cost" && <CostTab agent={agent} />}
    </div>
  );
}

/* ─── Overview Tab ─── */
function OverviewTab({ agent }: { agent: Agent }) {
  const fields: Array<{ label: string; value: React.ReactNode }> = [
    { label: "Agent ID", value: <span className="font-mono text-xs">{agent.id}</span> },
    { label: "Domain", value: <Badge variant="secondary">{agent.domain}</Badge> },
    { label: "Agent Type", value: agent.agent_type },
    { label: "Description", value: agent.description || "No description" },
    { label: "HITL Condition", value: agent.hitl_condition || "None" },
    { label: "Confidence Floor", value: agent.confidence_floor != null ? `${(agent.confidence_floor * 100).toFixed(0)}%` : "N/A" },
    { label: "Created", value: new Date(agent.created_at).toLocaleString() },
  ];

  return (
    <Card>
      <CardContent className="pt-6 space-y-4">
        <div className="grid grid-cols-2 gap-4 text-sm">
          {fields.map((f) => (
            <div key={f.label} className="flex flex-col gap-1">
              <span className="text-muted-foreground text-xs uppercase tracking-wide">{f.label}</span>
              <span>{f.value}</span>
            </div>
          ))}
        </div>

        {/* Authorized Tools */}
        <div className="pt-2 border-t">
          <span className="text-muted-foreground text-xs uppercase tracking-wide">Authorized Tools</span>
          <div className="flex flex-wrap gap-2 mt-2">
            {agent.authorized_tools && agent.authorized_tools.length > 0 ? (
              agent.authorized_tools.map((tool) => (
                <Badge key={tool} variant="outline">{tool}</Badge>
              ))
            ) : (
              <span className="text-sm text-muted-foreground">No tools configured</span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/* ─── Config Tab ─── */
function ConfigTab({ agent }: { agent: Agent }) {
  const configRows: Array<{ label: string; value: string }> = [
    { label: "LLM Model", value: agent.llm_model || "Not specified" },
    { label: "Max Retries", value: agent.max_retries != null ? String(agent.max_retries) : "Default" },
    { label: "Retry Backoff", value: agent.retry_backoff || "Default" },
    { label: "HITL Condition", value: agent.hitl_condition || "None" },
    { label: "Confidence Floor", value: agent.confidence_floor != null ? `${(agent.confidence_floor * 100).toFixed(0)}%` : "N/A" },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-semibold">Agent Configuration</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3">
          {configRows.map((row) => (
            <div key={row.label} className="flex items-center justify-between py-2 border-b last:border-0">
              <span className="text-sm text-muted-foreground">{row.label}</span>
              <span className="text-sm font-medium font-mono">{row.value}</span>
            </div>
          ))}
        </div>

        <div className="pt-2">
          <span className="text-sm text-muted-foreground">Authorized Tools</span>
          <div className="flex flex-wrap gap-2 mt-2">
            {agent.authorized_tools && agent.authorized_tools.length > 0 ? (
              agent.authorized_tools.map((tool) => (
                <Badge key={tool} variant="default">{tool}</Badge>
              ))
            ) : (
              <span className="text-sm text-muted-foreground">No tools configured</span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/* ─── Shadow Tab ─── */
function ShadowTab({ agent }: { agent: Agent }) {
  const sampleCount = agent.shadow_sample_count ?? 0;
  const minSamples = agent.shadow_min_samples ?? 100;
  const sampleProgress = minSamples > 0 ? Math.min((sampleCount / minSamples) * 100, 100) : 0;

  const accuracyCurrent = agent.shadow_accuracy_current != null ? agent.shadow_accuracy_current * 100 : 0;
  const accuracyFloor = agent.shadow_accuracy_floor != null ? agent.shadow_accuracy_floor * 100 : 0;

  const comparisonData = [
    { name: "Current", value: +accuracyCurrent.toFixed(1) },
    { name: "Required", value: +accuracyFloor.toFixed(1) },
  ];

  const meetsThreshold = accuracyCurrent >= accuracyFloor;
  const meetsCount = sampleCount >= minSamples;
  const promotionReady = meetsThreshold && meetsCount;

  return (
    <div className="space-y-4">
      {/* Sample Progress */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Shadow Sample Progress</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex justify-between text-sm">
            <span className="text-muted-foreground">Samples collected</span>
            <span className="font-medium">{sampleCount} / {minSamples}</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-4 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${meetsCount ? "bg-green-500" : "bg-yellow-500"}`}
              style={{ width: `${sampleProgress}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            {meetsCount
              ? "Sample count requirement met"
              : `${minSamples - sampleCount} more samples needed`}
          </p>
        </CardContent>
      </Card>

      {/* Accuracy Comparison */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Accuracy: Current vs Required</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={comparisonData} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
              <YAxis type="category" dataKey="name" width={80} />
              <Tooltip formatter={(value: number) => `${value}%`} />
              <Bar dataKey="value" barSize={28}>
                {comparisonData.map((_entry, idx) => (
                  <Cell key={idx} fill={idx === 0 ? (meetsThreshold ? "#22c55e" : "#f59e0b") : "#94a3b8"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Promotion Readiness */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium">Promotion Readiness:</span>
            <Badge variant={promotionReady ? "success" : "warning"}>
              {promotionReady ? "Ready to promote" : "Not yet ready"}
            </Badge>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div className="flex items-center gap-2">
              <span className={meetsCount ? "text-green-600" : "text-yellow-600"}>{meetsCount ? "\u2713" : "\u2717"}</span>
              Sample count ({sampleCount}/{minSamples})
            </div>
            <div className="flex items-center gap-2">
              <span className={meetsThreshold ? "text-green-600" : "text-yellow-600"}>{meetsThreshold ? "\u2713" : "\u2717"}</span>
              Accuracy ({accuracyCurrent.toFixed(1)}% / {accuracyFloor.toFixed(1)}%)
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ─── Cost Tab ─── */
function CostTab({ agent }: { agent: Agent }) {
  const monthlyCap = agent.cost_controls?.monthly_cap_usd ?? 0;
  const costCurrent = agent.cost_controls?.cost_current_usd ?? 0;
  const utilizationPct = monthlyCap > 0 ? Math.min((costCurrent / monthlyCap) * 100, 100) : 0;

  const isOverBudget = costCurrent > monthlyCap && monthlyCap > 0;
  const isNearLimit = utilizationPct >= 80 && !isOverBudget;

  const barColor = isOverBudget ? "bg-red-500" : isNearLimit ? "bg-yellow-500" : "bg-green-500";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-semibold">Cost Controls</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-muted-foreground text-xs uppercase tracking-wide">Monthly Cap</span>
            <span className="text-2xl font-bold">
              {monthlyCap > 0 ? `$${monthlyCap.toFixed(2)}` : "No cap set"}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-muted-foreground text-xs uppercase tracking-wide">Current Spend</span>
            <span className={`text-2xl font-bold ${isOverBudget ? "text-red-600" : ""}`}>
              ${costCurrent.toFixed(2)}
            </span>
          </div>
        </div>

        {/* Budget Utilization Bar */}
        {monthlyCap > 0 && (
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Budget Utilization</span>
              <span className="font-medium">{utilizationPct.toFixed(1)}%</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-5 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${barColor}`}
                style={{ width: `${Math.min(utilizationPct, 100)}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {isOverBudget
                ? "Over budget! Agent may be throttled."
                : isNearLimit
                  ? "Approaching budget limit."
                  : "Within budget."}
            </p>
          </div>
        )}

        {monthlyCap === 0 && (
          <p className="text-sm text-muted-foreground">No monthly cost cap configured for this agent.</p>
        )}
      </CardContent>
    </Card>
  );
}
