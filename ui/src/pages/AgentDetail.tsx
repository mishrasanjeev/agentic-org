import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import KillSwitch from "@/components/KillSwitch";
import api, { agentsApi } from "@/lib/api";
import type { Agent, PromptEditHistoryEntry } from "@/types";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell,
} from "recharts";

export default function AgentDetail() {
  const { id } = useParams();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"overview" | "config" | "prompt" | "shadow" | "cost">("overview");

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

  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function handlePromote() {
    setActionLoading("promote");
    setActionError(null);
    try {
      await api.post(`/agents/${id}/promote`);
      fetchAgent();
    } catch (err: any) {
      setActionError(err.response?.data?.detail || "Promote failed");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleRollback() {
    setActionLoading("rollback");
    setActionError(null);
    try {
      await api.post(`/agents/${id}/rollback`);
      fetchAgent();
    } catch (err: any) {
      setActionError(err.response?.data?.detail || "Rollback failed");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleResume() {
    setActionLoading("resume");
    setActionError(null);
    try {
      await agentsApi.resume(id || "");
      fetchAgent();
    } catch (err: any) {
      setActionError(err.response?.data?.detail || "Resume failed");
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) return <p className="text-muted-foreground">Loading agent...</p>;
  if (!agent) return (
    <div className="space-y-4">
      <p className="text-muted-foreground">Agent not found.</p>
      <a href="/dashboard/agents" className="text-sm text-primary hover:underline">&larr; Back to Agents</a>
    </div>
  );

  const statusColor: Record<string, string> = {
    active: "success", shadow: "warning", paused: "destructive",
    staging: "secondary", deprecated: "outline",
  };

  const confidenceFloor = agent.confidence_floor != null ? `${(agent.confidence_floor * 100).toFixed(0)}%` : "N/A";
  const shadowAccuracy = agent.shadow_accuracy_current != null ? `${(agent.shadow_accuracy_current * 100).toFixed(1)}%` : "N/A";

  const displayName = agent.employee_name || agent.name;

  return (
    <div className="space-y-6">
      {/* Persona Header */}
      <div className="flex justify-between items-start">
        <div className="flex items-center gap-4">
          {agent.avatar_url ? (
            <img src={agent.avatar_url} alt={displayName} className="w-16 h-16 rounded-full object-cover" />
          ) : (
            <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center text-2xl font-bold text-primary">
              {displayName.charAt(0).toUpperCase()}
            </div>
          )}
          <div>
            <h2 className="text-2xl font-bold">{displayName}</h2>
            <p className="text-sm text-muted-foreground">
              {agent.designation || agent.agent_type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())} | {agent.domain || "Unknown domain"}
            </p>
            {agent.specialization && (
              <p className="text-xs text-muted-foreground mt-1">Specialization: {agent.specialization}</p>
            )}
            {agent.reporting_to && (
              <p className="text-xs text-muted-foreground mt-1">Reports to: <span className="font-medium text-foreground">{agent.reporting_to}</span></p>
            )}
            {agent.is_builtin && <Badge variant="outline" className="mt-1">Built-in</Badge>}
            <div className="flex flex-wrap gap-1.5 mt-2">
              <Badge variant="secondary" className="text-[10px]">LangGraph</Badge>
              {(agent as any).config?.grantex?.grantex_did && (
                <Badge variant="outline" className="text-[10px] font-mono">{(agent as any).config.grantex.grantex_did}</Badge>
              )}
              {(agent as any).config?.grantex?.grantex_agent_id && (
                <Badge variant="default" className="text-[10px]">Grantex Registered</Badge>
              )}
            </div>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="flex gap-2">
            {agent.status === "paused" ? (
              <Button variant="outline" size="sm" onClick={handleResume} disabled={actionLoading !== null}>
                {actionLoading === "resume" ? "Resuming..." : "Resume"}
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={handlePromote} disabled={actionLoading !== null || agent.status === "active"}>
                {actionLoading === "promote" ? "Promoting..." : "Promote"}
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={handleRollback} disabled={actionLoading !== null}>
              {actionLoading === "rollback" ? "Rolling back..." : "Rollback"}
            </Button>
            {agent.status !== "paused" && (
              <KillSwitch agentId={id || ""} agentName={displayName} onPaused={fetchAgent} />
            )}
          </div>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
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
        {(["overview", "config", "prompt", "shadow", "cost"] as const).map((tab) => (
          <button key={tab} onClick={() => setActiveTab(tab)} className={`px-3 py-1 text-sm font-medium capitalize ${activeTab === tab ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "overview" && <OverviewTab agent={agent} onUpdated={fetchAgent} />}
      {activeTab === "config" && <ConfigTab agent={agent} />}
      {activeTab === "prompt" && <PromptTab agent={agent} />}
      {activeTab === "shadow" && <ShadowTab agent={agent} />}
      {activeTab === "cost" && <CostTab agent={agent} />}
    </div>
  );
}

/* ─── Overview Tab ─── */
function OverviewTab({ agent, onUpdated }: { agent: Agent; onUpdated: () => void }) {
  const [editingParent, setEditingParent] = useState(false);
  const [parentCandidates, setParentCandidates] = useState<Agent[]>([]);
  const [selectedParentId, setSelectedParentId] = useState(agent.parent_agent_id || "");
  const [savingParent, setSavingParent] = useState(false);

  useEffect(() => {
    if (editingParent) {
      agentsApi.list({ domain: agent.domain, status: "active" }).then(({ data }) => {
        const items = (Array.isArray(data) ? data : data.items || []).filter((a: Agent) => a.id !== agent.id);
        setParentCandidates(items);
      }).catch(() => setParentCandidates([]));
    }
  }, [editingParent, agent.domain, agent.id]);

  async function saveParent() {
    setSavingParent(true);
    try {
      const parent = parentCandidates.find((a) => a.id === selectedParentId);
      await agentsApi.update(agent.id, {
        parent_agent_id: selectedParentId || null,
        reporting_to: parent ? (parent.employee_name || parent.name) : null,
      });
      setEditingParent(false);
      onUpdated();
    } catch { /* ignore */ } finally {
      setSavingParent(false);
    }
  }

  const fields: Array<{ label: string; value: React.ReactNode }> = [
    { label: "Agent ID", value: <span className="font-mono text-xs">{agent.id}</span> },
    { label: "Domain", value: <Badge variant="secondary">{agent.domain}</Badge> },
    { label: "Agent Type", value: agent.agent_type },
    { label: "Description", value: agent.description || "No description" },
    { label: "HITL Condition", value: agent.hitl_condition || "None" },
    { label: "Confidence Floor", value: agent.confidence_floor != null ? `${(agent.confidence_floor * 100).toFixed(0)}%` : "N/A" },
    { label: "LLM Model", value: agent.llm_model || "Default (Gemini)" },
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

        {/* Reports To — Editable */}
        <div className="pt-2 border-t">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-xs uppercase tracking-wide">Reports To (Org Chart)</span>
            {!editingParent && (
              <Button variant="outline" size="sm" onClick={() => { setSelectedParentId(agent.parent_agent_id || ""); setEditingParent(true); }}>
                Edit
              </Button>
            )}
          </div>
          {editingParent ? (
            <div className="mt-2 flex items-center gap-2">
              <select
                value={selectedParentId}
                onChange={(e) => setSelectedParentId(e.target.value)}
                className="border rounded px-3 py-1.5 text-sm flex-1"
              >
                <option value="">— No parent (escalates to human) —</option>
                {parentCandidates.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.employee_name || a.name} ({a.agent_type.replace(/_/g, " ")})
                  </option>
                ))}
              </select>
              <Button size="sm" onClick={saveParent} disabled={savingParent}>
                {savingParent ? "Saving..." : "Save"}
              </Button>
              <Button variant="outline" size="sm" onClick={() => setEditingParent(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <p className="text-sm mt-1">
              {agent.reporting_to
                ? <><span className="font-medium">{agent.reporting_to}</span> <span className="text-muted-foreground">(escalates to parent agent)</span></>
                : <span className="text-muted-foreground">None — escalates directly to human</span>}
            </p>
          )}
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

/* ─── Prompt Tab ─── */
function PromptTab({ agent }: { agent: Agent }) {
  const [history, setHistory] = useState<PromptEditHistoryEntry[]>([]);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(agent.system_prompt_text || "");
  const [editReason, setEditReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const isLocked = agent.status === "active" || agent.is_builtin;

  useEffect(() => {
    agentsApi.promptHistory(agent.id).then(({ data }) => setHistory(data || [])).catch(() => {});
  }, [agent.id]);

  async function handleSavePrompt() {
    setSaving(true);
    setSaveError(null);
    try {
      await agentsApi.update(agent.id, {
        system_prompt_text: editText,
        prompt_change_reason: editReason || undefined,
      });
      setEditing(false);
      setEditReason("");
      // Refresh history
      agentsApi.promptHistory(agent.id).then(({ data }) => setHistory(data || [])).catch(() => {});
    } catch (err: any) {
      setSaveError(err.response?.data?.detail || "Failed to save prompt");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center">
            <CardTitle className="text-sm font-semibold">System Prompt</CardTitle>
            {isLocked ? (
              <div className="flex items-center gap-2">
                <Badge variant="destructive">Locked</Badge>
                <span className="text-xs text-muted-foreground">Clone this agent to edit prompt</span>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <Badge variant="secondary">Editable</Badge>
                {!editing && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => { setEditText(agent.system_prompt_text || ""); setEditing(true); }}
                  >
                    Edit
                  </Button>
                )}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {editing && !isLocked ? (
            <div className="space-y-3">
              <textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm font-mono min-h-[200px] max-h-96 overflow-auto"
                rows={12}
                placeholder="Enter system prompt text..."
              />
              <div>
                <label className="text-xs text-muted-foreground">Change reason (optional)</label>
                <input
                  type="text"
                  value={editReason}
                  onChange={(e) => setEditReason(e.target.value)}
                  placeholder="e.g. Updated tone, added compliance instructions"
                  className="w-full border rounded px-3 py-1.5 text-sm mt-1"
                />
              </div>
              {saveError && <p className="text-sm text-destructive">{saveError}</p>}
              <div className="flex gap-2">
                <Button size="sm" onClick={handleSavePrompt} disabled={saving || !editText.trim()}>
                  {saving ? "Saving..." : "Save Prompt"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => { setEditing(false); setSaveError(null); }}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : agent.system_prompt_text ? (
            <pre className="bg-muted rounded p-4 text-xs font-mono whitespace-pre-wrap max-h-96 overflow-auto">
              {agent.system_prompt_text}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">
              This agent uses a built-in file-based prompt template ({agent.agent_type}.prompt.txt).
            </p>
          )}
        </CardContent>
      </Card>

      {/* Edit History */}
      {history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Prompt Edit History</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {history.map((entry) => (
                <div key={entry.id} className="border-l-2 border-muted pl-3 py-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>{entry.edited_by || "System"}</span>
                    <span>{new Date(entry.created_at).toLocaleString()}</span>
                  </div>
                  {entry.change_reason && (
                    <p className="text-sm mt-1">Reason: {entry.change_reason}</p>
                  )}
                  <p className="text-xs text-muted-foreground mt-1">
                    {entry.prompt_before ? `Changed ${entry.prompt_before.length} → ${entry.prompt_after.length} chars` : `Initial prompt (${entry.prompt_after.length} chars)`}
                  </p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/* ─── Shadow Tab ─── */
function ShadowTab({ agent }: { agent: Agent }) {
  const [generating, setGenerating] = useState(false);
  const [genResult, setGenResult] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const sampleCount = agent.shadow_sample_count ?? 0;
  const minSamples = agent.shadow_min_samples ?? 10;
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

  async function generateSample() {
    setGenerating(true);
    setGenResult(null);
    try {
      await api.post(`/agents/${agent.id}/run`, {
        action: "shadow_sample",
        inputs: { mode: "test", generate_sample: true },
      });
      setGenResult({ type: "success", msg: "Shadow sample generated successfully. Refresh to see updated count." });
    } catch (err: any) {
      setGenResult({ type: "error", msg: err.response?.data?.detail || "Failed to generate sample. The agent may need to be configured first." });
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Sample Progress */}
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center">
            <CardTitle className="text-sm font-semibold">Shadow Sample Progress</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={generateSample}
              disabled={generating || meetsCount}
            >
              {generating ? "Generating..." : "Generate Test Sample"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {genResult && (
            <div className={`rounded-lg px-3 py-2 text-sm ${genResult.type === "success" ? "bg-green-50 text-green-800 border border-green-200" : "bg-red-50 text-red-800 border border-red-200"}`}>
              {genResult.msg}
            </div>
          )}
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
              : `${minSamples - sampleCount} more samples needed — use "Generate Test Sample" to collect samples`}
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
