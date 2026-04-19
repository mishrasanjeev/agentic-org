import { useState, useEffect, lazy, Suspense } from "react";
import { useParams, useNavigate } from "react-router-dom";
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

const ChatPanel = lazy(() => import("@/components/ChatPanel"));

export default function AgentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"overview" | "config" | "prompt" | "shadow" | "cost" | "scopes" | "learning" | "voice">("overview");
  const [chatOpen, setChatOpen] = useState(false);

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
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  // Run-task dialog (replaces window.prompt, which is blocked in
  // embedded browsers and some desktop shells).
  const [runDialogOpen, setRunDialogOpen] = useState(false);
  const [runTask, setRunTask] = useState("");

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
      const detail = err.response?.data?.detail || "Rollback failed";
      if (detail.toLowerCase().includes("no previous") || detail.toLowerCase().includes("version")) {
        setActionError("No previous version available. Switch to Shadow mode first to create a version checkpoint.");
      } else {
        setActionError(`${detail} — Switch to Shadow mode first to create a version checkpoint.`);
      }
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

  async function handleDelete() {
    if (!window.confirm(`Are you sure you want to delete this agent? This action cannot be undone.`)) return;
    setActionLoading("delete");
    setActionError(null);
    try {
      await api.delete(`/agents/${id}`);
      navigate("/dashboard/agents");
    } catch (err: any) {
      setActionError(err.response?.data?.detail || "Delete failed");
      setActionLoading(null);
    }
  }

  function openRunDialog() {
    setRunTask("");
    setActionError(null);
    setActionNotice(null);
    setRunDialogOpen(true);
  }

  async function submitRun() {
    const task = runTask.trim();
    if (!task) return;

    setRunDialogOpen(false);
    setActionLoading("run");
    setActionError(null);
    setActionNotice(null);
    try {
      await api.post(`/agents/${id}/run`, {
        action: "run",
        inputs: { task },
      });
      setActionNotice("Agent run started successfully.");
    } catch (err: any) {
      setActionError(err.response?.data?.detail || "Run failed");
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
            <span title={!agent.version || agent.version === "1.0" || (!agent.shadow_sample_count && agent.status === "active") ? "No previous version available" : undefined}>
              <Button
                variant="outline"
                size="sm"
                onClick={handleRollback}
                disabled={actionLoading !== null || (!agent.version || agent.version === "1.0" || (!agent.shadow_sample_count && agent.status === "active"))}
              >
                {actionLoading === "rollback" ? "Rolling back..." : "Rollback"}
              </Button>
            </span>
            {agent.status !== "paused" && (
              <KillSwitch agentId={id || ""} agentName={displayName} onPaused={fetchAgent} />
            )}
            {(agent.status === "paused" || agent.status === "inactive") && (
              <Button
                variant="destructive"
                size="sm"
                onClick={handleDelete}
                disabled={actionLoading !== null}
              >
                {actionLoading === "delete" ? "Deleting..." : "Delete Agent"}
              </Button>
            )}
            <Button
              variant="default"
              size="sm"
              onClick={openRunDialog}
              disabled={actionLoading !== null}
            >
              {actionLoading === "run" ? "Running..." : "Run Agent"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setChatOpen(true)}
            >
              Chat with Agent
            </Button>
          </div>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
          {actionNotice && <p className="text-xs text-emerald-600">{actionNotice}</p>}
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
        {(["overview", "config", "prompt", "shadow", "cost", "scopes", "learning", "voice"] as const).map((tab) => (
          <button key={tab} onClick={() => { setActiveTab(tab); setActionError(null); }} className={`px-3 py-1 text-sm font-medium capitalize ${activeTab === tab ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "overview" && <OverviewTab agent={agent} onUpdated={fetchAgent} />}
      {activeTab === "config" && <ConfigTab agent={agent} />}
      {activeTab === "prompt" && <PromptTab agent={agent} />}
      {activeTab === "shadow" && <ShadowTab agent={agent} />}
      {activeTab === "cost" && <CostTab agent={agent} />}
      {activeTab === "scopes" && <ScopesTab agent={agent} />}
      {activeTab === "learning" && <LearningTab agent={agent} />}
      {activeTab === "voice" && <VoiceTab agent={agent} />}

      <Suspense fallback={null}>
        {chatOpen && (
          <ChatPanel
            open={chatOpen}
            onClose={() => setChatOpen(false)}
            agentId={id}
            agentName={agent.employee_name || agent.name}
          />
        )}
      </Suspense>

      {runDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60">
          <div className="w-full max-w-md rounded-lg border bg-background p-6 shadow-lg">
            <h3 className="text-lg font-semibold mb-1">
              Run {agent?.employee_name || agent?.name || "agent"}
            </h3>
            <p className="text-sm text-muted-foreground mb-3">
              Describe the task (e.g., &ldquo;Process today&apos;s invoices&rdquo;,
              &ldquo;Generate sales report&rdquo;).
            </p>
            <textarea
              value={runTask}
              onChange={(e) => setRunTask(e.target.value)}
              placeholder="What should the agent do?"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[100px] focus:outline-none focus:ring-1 focus:ring-primary"
              autoFocus
            />
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" size="sm" onClick={() => setRunDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => void submitRun()}
                disabled={!runTask.trim()}
              >
                Run
              </Button>
            </div>
          </div>
        </div>
      )}
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

        {/* Explainer Panel + Feedback (PRD v4 Section 6 & 8) */}
        <ExplainerPanel agentId={agent.id} />
      </CardContent>
    </Card>
  );
}

/* ─── Explainer Panel (collapsible "Why did the agent do this?") ─── */
function ExplainerPanel({ agentId }: { agentId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [explanation, setExplanation] = useState<{
    bullets?: string[];
    confidence?: number;
    tools_cited?: string[];
    readability_grade?: number;
  } | null>(null);
  const [runResult, setRunResult] = useState<{ task_id?: string; status?: string } | null>(null);
  const [feedbackSent, setFeedbackSent] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const [correctionText, setCorrectionText] = useState("");

  // Load the latest run explanation when expanded. Real trace data
  // from GET /agents/{id}/explanation/latest (PR-C1) — replaces the
  // hardcoded-bullet mock that shipped pre-Enterprise-Readiness.
  const [explanationHasRun, setExplanationHasRun] = useState<boolean | null>(null);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;

    // Keep the feedback fetch so the thumbs-up/down controls still have a
    // run_id to attach to.
    api.get(`/agents/${agentId}/feedback?limit=1`).then(({ data }) => {
      if (cancelled) return;
      const items = Array.isArray(data) ? data : data?.items || [];
      if (items.length > 0) {
        setRunResult({ task_id: items[0].run_id, status: "completed" });
      }
    }).catch(() => {});

    api
      .get(`/agents/${agentId}/explanation/latest`)
      .then(({ data }) => {
        if (cancelled) return;
        if (!data?.has_run) {
          setExplanationHasRun(false);
          setExplanation(null);
          return;
        }
        setExplanationHasRun(true);
        setExplanation({
          bullets: Array.isArray(data.bullets) ? data.bullets : [],
          confidence: typeof data.confidence === "number" ? data.confidence : undefined,
          tools_cited: Array.isArray(data.tools_cited) ? data.tools_cited : [],
          // readability_grade is deliberately not returned — we won't
          // fabricate it client-side.
          readability_grade: undefined,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setExplanationHasRun(null);
        setExplanation(null);
      });

    return () => {
      cancelled = true;
    };
  }, [expanded, agentId]);

  async function sendFeedback(type: string) {
    if (!runResult?.task_id) return;
    try {
      await api.post(`/agents/${agentId}/feedback`, {
        run_id: runResult.task_id,
        feedback_type: type,
        text: type === "correction" ? correctionText : "",
        corrected_output: type === "correction" ? { corrected_text: correctionText } : undefined,
      });
      setFeedbackSent(type);
      if (type === "correction") {
        setCorrecting(false);
        setCorrectionText("");
      }
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="pt-2 border-t" data-testid="explainer-panel">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm font-medium text-primary hover:underline w-full text-left"
      >
        <span className={`transition-transform ${expanded ? "rotate-90" : ""}`}>&#9656;</span>
        Why did the agent do this?
      </button>

      {expanded && (
        <div className="mt-3 bg-muted/30 rounded-lg p-4 space-y-3" data-testid="explainer-body">
          {explanationHasRun === false ? (
            <p className="text-sm text-muted-foreground" data-testid="explainer-empty">
              No explanation yet. Run the agent at least once to generate a real
              trace.
            </p>
          ) : explanation && explanation.bullets ? (
            <>
              <ul className="list-disc list-inside text-sm space-y-1">
                {explanation.bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>

              {/* Confidence bar */}
              {explanation.confidence != null && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Confidence</span>
                    <span>{(explanation.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="h-full rounded-full bg-primary transition-all"
                      style={{ width: `${explanation.confidence * 100}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Tools used */}
              {explanation.tools_cited && explanation.tools_cited.length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">Tools used:</span>
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {explanation.tools_cited.map((t) => (
                      <Badge key={t} variant="outline" className="text-[10px]">{t}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Readability */}
              {explanation.readability_grade != null && (
                <p className="text-xs text-muted-foreground">
                  Readability grade: {explanation.readability_grade.toFixed(1)} (Flesch-Kincaid)
                </p>
              )}

              {/* Feedback buttons */}
              <div className="flex items-center gap-3 pt-2 border-t">
                <span className="text-xs text-muted-foreground">Was this helpful?</span>
                <button
                  onClick={() => sendFeedback("thumbs_up")}
                  className={`text-lg hover:scale-110 transition-transform ${feedbackSent === "thumbs_up" ? "opacity-100" : "opacity-50 hover:opacity-80"}`}
                  title="Thumbs up"
                  disabled={feedbackSent !== null}
                >
                  &#128077;
                </button>
                <button
                  onClick={() => sendFeedback("thumbs_down")}
                  className={`text-lg hover:scale-110 transition-transform ${feedbackSent === "thumbs_down" ? "opacity-100" : "opacity-50 hover:opacity-80"}`}
                  title="Thumbs down"
                  disabled={feedbackSent !== null}
                >
                  &#128078;
                </button>
                {!correcting && (
                  <button
                    onClick={() => setCorrecting(true)}
                    className="text-xs text-primary hover:underline ml-2"
                    disabled={feedbackSent !== null}
                  >
                    Correct this
                  </button>
                )}
                {feedbackSent && (
                  <span className="text-xs text-green-600 ml-2">Feedback recorded</span>
                )}
              </div>

              {/* Correction text area */}
              {correcting && (
                <div className="space-y-2">
                  <textarea
                    value={correctionText}
                    onChange={(e) => setCorrectionText(e.target.value)}
                    placeholder="What should the correct output be?"
                    className="w-full border rounded px-3 py-2 text-sm min-h-[80px]"
                  />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => sendFeedback("correction")} disabled={!correctionText.trim()}>
                      Submit Correction
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => { setCorrecting(false); setCorrectionText(""); }}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Run the agent to see an explanation of its decisions.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Learning Tab (Feedback + Amendments) ─── */
function LearningTab({ agent }: { agent: Agent }) {
  const [feedback, setFeedback] = useState<any[]>([]);
  const [amendments, setAmendments] = useState<string[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<{ amendment?: string; reason?: string; confidence?: number } | null>(null);
  const [loadingFeedback, setLoadingFeedback] = useState(true);

  useEffect(() => {
    loadData();
  }, [agent.id]);

  async function loadData() {
    setLoadingFeedback(true);
    try {
      const [fbResp, amResp] = await Promise.all([
        api.get(`/agents/${agent.id}/feedback?limit=50`),
        api.get(`/agents/${agent.id}/amendments`),
      ]);
      setFeedback(fbResp.data?.feedback || []);
      setAmendments(amResp.data?.amendments || []);
    } catch {
      /* ignore */
    } finally {
      setLoadingFeedback(false);
    }
  }

  async function triggerAnalysis() {
    setAnalyzing(true);
    setAnalysisResult(null);
    try {
      const resp = await api.post(`/agents/${agent.id}/feedback/analyze`);
      setAnalysisResult(resp.data);
    } catch {
      setAnalysisResult({ amendment: "", reason: "Analysis failed", confidence: 0 });
    } finally {
      setAnalyzing(false);
    }
  }

  async function applyAmendment(amendment: string) {
    try {
      // Apply by updating the agent's prompt_amendments via the API
      const current = [...amendments, amendment];
      await api.patch(`/agents/${agent.id}`, { prompt_amendments: current });
      setAmendments(current);
      setAnalysisResult(null);
    } catch {
      /* ignore */
    }
  }

  const feedbackTypeColors: Record<string, string> = {
    thumbs_up: "bg-green-100 text-green-700",
    thumbs_down: "bg-red-100 text-red-700",
    correction: "bg-blue-100 text-blue-700",
    hitl_reject: "bg-yellow-100 text-yellow-700",
  };

  return (
    <div className="space-y-4">
      {/* Active Amendments */}
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center">
            <CardTitle className="text-sm font-semibold">Learned Rules (Amendments)</CardTitle>
            <Button variant="outline" size="sm" onClick={triggerAnalysis} disabled={analyzing}>
              {analyzing ? "Analyzing..." : "Analyze Feedback"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {amendments.length > 0 ? (
            <div className="space-y-2">
              {amendments.map((a, idx) => (
                <div key={idx} className="flex items-start justify-between bg-muted/40 rounded px-3 py-2">
                  <span className="text-sm flex-1">{a}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="ml-2 text-xs"
                    onClick={() => setAmendments(amendments.filter((_, i) => i !== idx))}
                  >
                    Dismiss
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No learned rules yet. Submit feedback and run analysis to generate amendments.</p>
          )}

          {/* Analysis result */}
          {analysisResult && analysisResult.amendment && (
            <div className="border border-primary/30 rounded-lg p-3 bg-primary/5 space-y-2">
              <p className="text-sm font-medium">Suggested Amendment:</p>
              <p className="text-sm">{analysisResult.amendment}</p>
              <p className="text-xs text-muted-foreground">Reason: {analysisResult.reason}</p>
              {analysisResult.confidence != null && (
                <p className="text-xs text-muted-foreground">Confidence: {(analysisResult.confidence * 100).toFixed(0)}%</p>
              )}
              <div className="flex gap-2">
                <Button size="sm" onClick={() => applyAmendment(analysisResult.amendment!)}>
                  Apply
                </Button>
                <Button variant="outline" size="sm" onClick={() => setAnalysisResult(null)}>
                  Dismiss
                </Button>
              </div>
            </div>
          )}
          {analysisResult && !analysisResult.amendment && analysisResult.reason && (
            <p className="text-sm text-muted-foreground">{analysisResult.reason}</p>
          )}
        </CardContent>
      </Card>

      {/* Feedback Timeline */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Feedback Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          {loadingFeedback ? (
            <p className="text-sm text-muted-foreground">Loading feedback...</p>
          ) : feedback.length > 0 ? (
            <div className="space-y-2">
              {feedback.map((entry, idx) => (
                <div key={idx} className="flex items-start gap-3 border-b last:border-0 pb-2">
                  <span className={`text-xs px-2 py-0.5 rounded font-medium ${feedbackTypeColors[entry.feedback_type] || "bg-gray-100 text-gray-700"}`}>
                    {entry.feedback_type?.replace(/_/g, " ")}
                  </span>
                  <div className="flex-1">
                    {entry.text && <p className="text-sm">{entry.text}</p>}
                    {entry.corrected_output && (
                      <p className="text-xs text-muted-foreground mt-1">Corrected: {typeof entry.corrected_output === "string" ? entry.corrected_output : JSON.stringify(entry.corrected_output)}</p>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {entry.created_at ? new Date(entry.created_at).toLocaleString() : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No feedback submitted yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/* ──�� Config Tab ─── */
function ConfigTab({ agent }: { agent: Agent }) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [editLlmModel, setEditLlmModel] = useState(agent.llm_model || "");
  const [editMaxRetries, setEditMaxRetries] = useState(agent.max_retries ?? 3);
  const [editHitlCondition, setEditHitlCondition] = useState(agent.hitl_condition || "");
  const [editConfidenceFloor, setEditConfidenceFloor] = useState(
    agent.confidence_floor != null ? Math.round(agent.confidence_floor * 100) : 70
  );

  const LLM_OPTIONS = [
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-3-opus",
    "claude-3-sonnet",
    "llama-3-70b",
  ];

  function startEditing() {
    setEditLlmModel(agent.llm_model || "");
    setEditMaxRetries(agent.max_retries ?? 3);
    setEditHitlCondition(agent.hitl_condition || "");
    setEditConfidenceFloor(agent.confidence_floor != null ? Math.round(agent.confidence_floor * 100) : 70);
    setSaveError(null);
    setEditing(true);
  }

  async function handleSaveConfig() {
    setSaving(true);
    setSaveError(null);
    try {
      const payload: Record<string, any> = {
        confidence_floor: editConfidenceFloor / 100,
      };
      if (editLlmModel) {
        payload.llm = { model: editLlmModel };
      }
      if (editHitlCondition) {
        payload.hitl_policy = { condition: editHitlCondition };
      }
      await api.patch(`/agents/${agent.id}`, payload);
      setEditing(false);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      const msg = typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail?.message
          ? detail.message
          : JSON.stringify(detail) || "Failed to save configuration";
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  }

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
        <div className="flex justify-between items-center">
          <CardTitle className="text-sm font-semibold">Agent Configuration</CardTitle>
          {!editing && (
            <Button variant="outline" size="sm" onClick={startEditing}>
              Edit
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {editing ? (
          <div className="space-y-4">
            {/* LLM Model */}
            <div className="flex flex-col gap-1">
              <label className="text-sm text-muted-foreground">LLM Model</label>
              <select
                value={editLlmModel}
                onChange={(e) => setEditLlmModel(e.target.value)}
                className="border rounded px-3 py-1.5 text-sm"
              >
                <option value="">Default (Gemini)</option>
                {LLM_OPTIONS.map((model) => (
                  <option key={model} value={model}>{model}</option>
                ))}
              </select>
            </div>

            {/* Max Retries */}
            <div className="flex flex-col gap-1">
              <label className="text-sm text-muted-foreground">Max Retries</label>
              <input
                type="number"
                min={0}
                max={10}
                value={editMaxRetries}
                onChange={(e) => setEditMaxRetries(Number(e.target.value))}
                className="border rounded px-3 py-1.5 text-sm w-32"
              />
            </div>

            {/* HITL Condition */}
            <div className="flex flex-col gap-1">
              <label className="text-sm text-muted-foreground">HITL Condition</label>
              <input
                type="text"
                value={editHitlCondition}
                onChange={(e) => setEditHitlCondition(e.target.value)}
                placeholder="e.g. confidence < 0.7 or amount > 10000"
                className="border rounded px-3 py-1.5 text-sm"
              />
            </div>

            {/* Confidence Floor */}
            <div className="flex flex-col gap-1">
              <label className="text-sm text-muted-foreground">Confidence Floor (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={editConfidenceFloor}
                onChange={(e) => setEditConfidenceFloor(Number(e.target.value))}
                className="border rounded px-3 py-1.5 text-sm w-32"
              />
            </div>

            {saveError && <p className="text-sm text-destructive">{saveError}</p>}

            <div className="flex gap-2">
              <Button size="sm" onClick={handleSaveConfig} disabled={saving}>
                {saving ? "Saving..." : "Save Config"}
              </Button>
              <Button variant="outline" size="sm" onClick={() => { setEditing(false); setSaveError(null); }}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <>
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
          </>
        )}
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
  const [retesting, setRetesting] = useState(false);
  const [genResult, setGenResult] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const sampleCount = agent.shadow_sample_count ?? 0;
  const minSamples = agent.shadow_min_samples ?? 20;
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

  async function handleRetest() {
    setRetesting(true);
    setGenResult(null);
    try {
      await api.post(`/agents/${agent.id}/retest`);
      setGenResult({ type: "success", msg: "Shadow retest completed. Refresh to see updated results." });
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Shadow retest failed. Please try again.";
      setGenResult({ type: "error", msg: typeof detail === "string" ? detail : JSON.stringify(detail) });
    } finally {
      setRetesting(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Sample Progress */}
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center">
            <CardTitle className="text-sm font-semibold">Shadow Sample Progress</CardTitle>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={generateSample}
                disabled={generating || retesting || meetsCount}
              >
                {generating ? "Generating..." : "Generate Test Sample"}
              </Button>
              {sampleCount > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRetest}
                  disabled={retesting || generating}
                >
                  {retesting ? "Retesting..." : "Retest"}
                </Button>
              )}
            </div>
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
              <Tooltip formatter={(value: any) => `${value}%`} />
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

/* ─── Scope Helpers ─── */
type PermissionLevel = "READ" | "WRITE" | "DELETE" | "ADMIN";

function getToolPermission(toolName: string): PermissionLevel {
  if (/^(get_|fetch_|list_|query|search_)/.test(toolName)) return "READ";
  if (/^(create_|update_|send_|post_)/.test(toolName)) return "WRITE";
  if (/^(delete_|remove_)/.test(toolName)) return "DELETE";
  if (/^(bulk_|reset_|admin_)/.test(toolName)) return "ADMIN";
  return "READ";
}

const PERMISSION_COLORS: Record<PermissionLevel, string> = {
  READ: "bg-green-100 text-green-700 border-green-300",
  WRITE: "bg-blue-100 text-blue-700 border-blue-300",
  DELETE: "bg-red-100 text-red-700 border-red-300",
  ADMIN: "bg-purple-100 text-purple-700 border-purple-300",
};

function PermissionBadge({ perm }: { perm: PermissionLevel }) {
  return (
    <span className={`inline-block text-[10px] font-semibold px-1.5 py-0 rounded border ${PERMISSION_COLORS[perm]}`}>
      {perm}
    </span>
  );
}

function buildScopeString(tool: string, domain: string, perm: PermissionLevel): string {
  const connector = domain || "default";
  return `tool:${connector}:${perm.toLowerCase()}:${tool}`;
}

/* ─── Voice Tab ─── */
function VoiceTab({ agent }: { agent: Agent }) {
  // Mock: check if voice is configured (use agent config or a flag)
  const voiceConfigured = !!(agent as any).voice_config;

  // Mock call log data
  const callLog = [
    { timestamp: "2026-04-04T10:30:00Z", duration: "2m 15s", status: "completed" },
    { timestamp: "2026-04-04T09:45:00Z", duration: "1m 42s", status: "completed" },
    { timestamp: "2026-04-04T08:20:00Z", duration: "0m 38s", status: "missed" },
    { timestamp: "2026-04-03T16:10:00Z", duration: "3m 05s", status: "completed" },
    { timestamp: "2026-04-03T14:55:00Z", duration: "0m 12s", status: "failed" },
  ];

  const voiceNavigate = useNavigate();

  if (!voiceConfigured) {
    return (
      <Card>
        <CardContent className="pt-6 text-center space-y-4">
          <p className="text-muted-foreground">Voice not enabled for this agent.</p>
          <div className="flex justify-center gap-3">
            <Button onClick={() => voiceNavigate("/dashboard/voice-setup")} className="relative z-10">
              Set up Voice
            </Button>
            <Button variant="outline" onClick={() => voiceNavigate(-1 as any)} className="relative z-10">
              Back
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center">
            <CardTitle className="text-sm font-semibold">Voice Call Log</CardTitle>
            <button
              onClick={() => voiceNavigate("/dashboard/voice-setup")}
              className="text-xs text-primary hover:underline relative z-10 cursor-pointer"
            >
              Voice Setup
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground uppercase tracking-wide">
                  <th className="pb-2 pr-4">Timestamp</th>
                  <th className="pb-2 pr-4">Duration</th>
                  <th className="pb-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {callLog.map((entry, idx) => (
                  <tr key={idx} className="border-b last:border-0">
                    <td className="py-2 pr-4 text-xs text-muted-foreground font-mono">
                      {new Date(entry.timestamp).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4">{entry.duration}</td>
                    <td className="py-2">
                      <Badge variant={entry.status === "completed" ? "success" : entry.status === "missed" ? "warning" : "destructive"} className="text-[10px]">
                        {entry.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ─── Scopes Tab ─── */
function ScopesTab({ agent }: { agent: Agent }) {
  const tools = agent.authorized_tools || [];
  const domain = agent.domain || "default";

  // Mock enforcement log data
  const enforcementLog = [
    { timestamp: "2026-04-04T09:12:33Z", tool: tools[0] || "get_contact", connector: "salesforce", result: "allowed" as const, reason: "Scope matched: READ" },
    { timestamp: "2026-04-04T09:10:15Z", tool: tools[1] || "update_record", connector: "salesforce", result: "allowed" as const, reason: "Scope matched: WRITE" },
    { timestamp: "2026-04-04T08:55:42Z", tool: "delete_account", connector: "salesforce", result: "denied" as const, reason: "No DELETE scope granted" },
    { timestamp: "2026-04-04T08:30:01Z", tool: "admin_reset_org", connector: "internal", result: "denied" as const, reason: "ADMIN scope not in grant token" },
    { timestamp: "2026-04-04T08:15:22Z", tool: tools[0] || "get_contact", connector: "hubspot", result: "allowed" as const, reason: "Scope matched: READ" },
  ];

  // Mock token statuses: most active, one expiring, one expired
  function getTokenStatus(idx: number): { color: string; label: string } {
    if (idx === tools.length - 1 && tools.length > 2) return { color: "bg-red-500", label: "Expired" };
    if (idx === tools.length - 2 && tools.length > 1) return { color: "bg-yellow-500", label: "Expiring soon" };
    return { color: "bg-green-500", label: "Active" };
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Grantex Scopes</CardTitle>
        </CardHeader>
        <CardContent>
          {tools.length > 0 ? (
            <div className="overflow-x-auto -mx-2 px-2">
              <table className="w-full min-w-[700px] text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground uppercase tracking-wide">
                    <th className="pb-2 pr-4">Tool</th>
                    <th className="pb-2 pr-4">Permission</th>
                    <th className="pb-2 pr-4">Connector</th>
                    <th className="pb-2 pr-4">Scope String</th>
                    <th className="pb-2 pr-4">Status</th>
                    <th className="pb-2">Grant Token</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((tool, idx) => {
                    const perm = getToolPermission(tool);
                    const scope = buildScopeString(tool, domain, perm);
                    const tokenStatus = getTokenStatus(idx);
                    return (
                      <tr key={tool} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{tool}</td>
                        <td className="py-2 pr-4"><PermissionBadge perm={perm} /></td>
                        <td className="py-2 pr-4 text-xs">{domain}</td>
                        <td className="py-2 pr-4"><code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">{scope}</code></td>
                        <td className="py-2 pr-4">
                          <Badge variant={tokenStatus.label === "Expired" ? "destructive" : tokenStatus.label === "Expiring soon" ? "warning" : "success"} className="text-[10px]">
                            {tokenStatus.label === "Expired" ? "expired" : "active"}
                          </Badge>
                        </td>
                        <td className="py-2">
                          <span className="inline-flex items-center gap-1.5 text-xs">
                            <span className={`w-2 h-2 rounded-full ${tokenStatus.color}`} />
                            {tokenStatus.label}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No tools configured — no scopes to display.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Enforcement Log</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground uppercase tracking-wide">
                  <th className="pb-2 pr-4">Timestamp</th>
                  <th className="pb-2 pr-4">Tool</th>
                  <th className="pb-2 pr-4">Connector</th>
                  <th className="pb-2 pr-4">Result</th>
                  <th className="pb-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {enforcementLog.map((entry, idx) => (
                  <tr key={idx} className="border-b last:border-0">
                    <td className="py-2 pr-4 text-xs text-muted-foreground font-mono">
                      {new Date(entry.timestamp).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4 font-medium">{entry.tool}</td>
                    <td className="py-2 pr-4">{entry.connector}</td>
                    <td className="py-2 pr-4">
                      <Badge variant={entry.result === "allowed" ? "success" : "destructive"} className="text-[10px]">
                        {entry.result}
                      </Badge>
                    </td>
                    <td className="py-2 text-xs text-muted-foreground">{entry.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground mt-3">Showing recent enforcement decisions. Full audit log available via API.</p>
        </CardContent>
      </Card>
    </div>
  );
}
