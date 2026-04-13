import { useState, useEffect, useCallback } from "react";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

const STAGES = [
  { key: "new", label: "New", color: "bg-slate-500" },
  { key: "contacted", label: "Contacted", color: "bg-blue-500" },
  { key: "qualified", label: "Qualified", color: "bg-indigo-500" },
  { key: "demo_scheduled", label: "Demo Scheduled", color: "bg-purple-500" },
  { key: "demo_done", label: "Demo Done", color: "bg-violet-500" },
  { key: "trial", label: "Trial", color: "bg-amber-500" },
  { key: "negotiation", label: "Negotiation", color: "bg-orange-500" },
  { key: "closed_won", label: "Won", color: "bg-emerald-500" },
  { key: "closed_lost", label: "Lost", color: "bg-red-500" },
];

interface Lead {
  id: string; name: string; email: string; company: string; role: string;
  stage: string; score: number; followup_count: number;
  last_contacted_at: string | null; next_followup_at: string | null;
  demo_scheduled_at: string | null; deal_value_usd: number | null;
  created_at: string;
}

interface Metrics {
  total_leads: number; new_this_week: number; funnel: Record<string, number>;
  avg_score: number; emails_sent_this_week: number; stale_leads: number;
}

interface AgentResult {
  leadId: string;
  status: string;
  output: Record<string, any>;
  confidence: number | null;
  reasoning_trace: string[];
}

export default function SalesPipeline() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [funnel, setFunnel] = useState<Record<string, number>>({});
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [stageFilter, setStageFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState<string | null>(null);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [agentResult, setAgentResult] = useState<AgentResult | null>(null);
  const [showAddLead, setShowAddLead] = useState(false);
  const [newLead, setNewLead] = useState({ name: "", email: "", company: "", role: "", deal_value_usd: "" });
  const [addingLead, setAddingLead] = useState(false);
  const [importingCsv, setImportingCsv] = useState(false);
  const [importMsg, setImportMsg] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [pipelineRes, metricsRes] = await Promise.all([
        api.get("/sales/pipeline", { params: stageFilter ? { stage: stageFilter } : {} }),
        api.get("/sales/metrics"),
      ]);
      setLeads(pipelineRes.data.leads || []);
      setFunnel(pipelineRes.data.funnel || {});
      setMetrics(metricsRes.data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [stageFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  async function triggerAgent(leadId: string) {
    setProcessing(leadId);
    setAgentResult(null);
    try {
      const { data } = await api.post("/sales/pipeline/process-lead", { lead_id: leadId, action: "qualify_and_respond" });
      // Store the agent result for display
      setAgentResult({
        leadId,
        status: data.status || "completed",
        output: data.output || {},
        confidence: data.confidence ?? null,
        reasoning_trace: data.reasoning_trace || [],
      });
      await fetchData();
      // Auto-select the processed lead to show details
      const updatedLead = leads.find(l => l.id === leadId);
      if (updatedLead) setSelectedLead(updatedLead);
    } catch { /* ignore */ }
    finally { setProcessing(null); }
  }

  async function handleAddLead(e: React.FormEvent) {
    e.preventDefault();
    if (!newLead.name.trim() || !newLead.email.trim()) return;
    setAddingLead(true);
    try {
      await api.post("/sales/pipeline/leads", {
        name: newLead.name.trim(),
        email: newLead.email.trim(),
        company: newLead.company.trim() || undefined,
        role: newLead.role.trim() || undefined,
        deal_value_usd: newLead.deal_value_usd ? Number(newLead.deal_value_usd) : undefined,
      });
      setNewLead({ name: "", email: "", company: "", role: "", deal_value_usd: "" });
      setShowAddLead(false);
      await fetchData();
    } catch { /* ignore */ }
    finally { setAddingLead(false); }
  }

  async function handleCsvImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportingCsv(true);
    setImportMsg(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post("/sales/import-csv", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const count = data.imported?.length ?? 0;
      setImportMsg({ type: "success", msg: `Imported ${count} lead${count !== 1 ? "s" : ""} from CSV` });
      await fetchData();
    } catch {
      setImportMsg({ type: "error", msg: "CSV import failed. Ensure columns: name, email, company, role" });
    } finally {
      setImportingCsv(false);
      e.target.value = "";  // reset file input
    }
  }

  const scoreColor = (score: number) =>
    score >= 70 ? "text-emerald-600" : score >= 40 ? "text-amber-600" : "text-slate-500";

  return (
    <div className="space-y-6">
      <Helmet>
        <title>Sales Pipeline — AgenticOrg</title>
      </Helmet>

      <div className="flex justify-between items-center flex-wrap gap-2">
        <div>
          <h2 className="text-2xl font-bold">Sales Pipeline</h2>
          <p className="text-sm text-muted-foreground">Aarav (AI Sales Agent) manages lead qualification and outreach</p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setShowAddLead(!showAddLead)} variant={showAddLead ? "outline" : "default"} size="sm">
            {showAddLead ? "Cancel" : "Add Lead"}
          </Button>
          <Button variant="outline" size="sm" disabled={importingCsv} onClick={() => document.getElementById("csv-import-input")?.click()}>
            {importingCsv ? "Importing..." : "Import CSV"}
          </Button>
          <input id="csv-import-input" type="file" accept=".csv" className="hidden" onChange={handleCsvImport} />
          <Button onClick={fetchData} variant="outline" size="sm">Refresh</Button>
        </div>
      </div>

      {importMsg && (
        <div className={`rounded-lg px-4 py-3 text-sm ${importMsg.type === "success" ? "bg-green-50 text-green-800 border border-green-200" : "bg-red-50 text-red-800 border border-red-200"}`}>
          {importMsg.msg}
        </div>
      )}

      {/* Add Lead Form */}
      {showAddLead && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Add New Lead</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={handleAddLead} className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Name *</label>
                  <input type="text" value={newLead.name} onChange={(e) => setNewLead({ ...newLead, name: e.target.value })} placeholder="John Doe" className="border rounded px-3 py-2 text-sm w-full mt-1" required />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Email *</label>
                  <input type="email" value={newLead.email} onChange={(e) => setNewLead({ ...newLead, email: e.target.value })} placeholder="john@company.com" className="border rounded px-3 py-2 text-sm w-full mt-1" required />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Company</label>
                  <input type="text" value={newLead.company} onChange={(e) => setNewLead({ ...newLead, company: e.target.value })} placeholder="Acme Corp" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Role</label>
                  <input type="text" value={newLead.role} onChange={(e) => setNewLead({ ...newLead, role: e.target.value })} placeholder="VP Engineering" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Deal Value (USD)</label>
                  <input type="number" value={newLead.deal_value_usd} onChange={(e) => setNewLead({ ...newLead, deal_value_usd: e.target.value })} placeholder="10000" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                </div>
              </div>
              <Button type="submit" size="sm" disabled={addingLead}>
                {addingLead ? "Adding..." : "Add Lead"}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Metrics Cards */}
      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <Card>
            <CardContent className="pt-4 pb-3 text-center">
              <p className="text-2xl font-bold">{metrics.total_leads}</p>
              <p className="text-xs text-muted-foreground">Total Leads</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4 pb-3 text-center">
              <p className="text-2xl font-bold text-blue-600">+{metrics.new_this_week}</p>
              <p className="text-xs text-muted-foreground">This Week</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4 pb-3 text-center">
              <p className="text-2xl font-bold">{metrics.avg_score}</p>
              <p className="text-xs text-muted-foreground">Avg Score</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4 pb-3 text-center">
              <p className="text-2xl font-bold text-emerald-600">{metrics.emails_sent_this_week}</p>
              <p className="text-xs text-muted-foreground">Emails Sent</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4 pb-3 text-center">
              <p className="text-2xl font-bold text-amber-600">{metrics.stale_leads}</p>
              <p className="text-xs text-muted-foreground">Stale Leads</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4 pb-3 text-center">
              <p className="text-2xl font-bold text-emerald-600">{funnel["closed_won"] || 0}</p>
              <p className="text-xs text-muted-foreground">Won</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Funnel Bar */}
      <Card>
        <CardContent className="pt-4 pb-3">
          <div className="flex gap-1 h-8 rounded-lg overflow-hidden">
            {STAGES.map((s) => {
              const count = funnel[s.key] || 0;
              const total = Object.values(funnel).reduce((a, b) => a + b, 0) || 1;
              const pct = (count / total) * 100;
              if (pct === 0) return null;
              return (
                <div
                  key={s.key}
                  className={`${s.color} flex items-center justify-center text-xs text-white font-medium cursor-pointer hover:opacity-80 transition-opacity`}
                  style={{ width: `${Math.max(pct, 3)}%` }}
                  title={`${s.label}: ${count}`}
                  onClick={() => setStageFilter(stageFilter === s.key ? "" : s.key)}
                >
                  {count > 0 && count}
                </div>
              );
            })}
          </div>
          <div className="flex gap-3 mt-2 flex-wrap">
            {STAGES.map((s) => (
              <button
                key={s.key}
                onClick={() => setStageFilter(stageFilter === s.key ? "" : s.key)}
                className={`flex items-center gap-1.5 text-xs ${stageFilter === s.key ? "font-bold" : "text-muted-foreground"}`}
              >
                <span className={`w-2 h-2 rounded-full ${s.color}`} />
                {s.label} ({funnel[s.key] || 0})
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Lead Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            {stageFilter ? `${STAGES.find(s => s.key === stageFilter)?.label} Leads` : "All Leads"} ({leads.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : leads.length === 0 ? (
            <div className="text-center py-8">
              <p className="text-sm text-muted-foreground mb-3">No leads yet. Add a lead or demo requests will appear here automatically.</p>
              <Button size="sm" onClick={() => setShowAddLead(true)}>Add Your First Lead</Button>
            </div>
          ) : (
            <div className="space-y-2">
              {leads.map((lead) => {
                const stage = STAGES.find((s) => s.key === lead.stage);
                const isSelected = selectedLead?.id === lead.id;
                return (
                  <div key={lead.id}>
                    <div
                      className={`flex items-center gap-4 p-3 rounded-lg border hover:bg-muted/50 transition-colors cursor-pointer ${isSelected ? "bg-muted border-primary" : ""}`}
                      onClick={() => setSelectedLead(isSelected ? null : lead)}
                    >
                      {/* Score */}
                      <div className={`text-lg font-bold w-10 text-center ${scoreColor(lead.score)}`}>
                        {lead.score}
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{lead.name}</span>
                          {lead.company && <span className="text-xs text-muted-foreground">@ {lead.company}</span>}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-xs text-muted-foreground">{lead.email}</span>
                          {lead.role && <span className="text-xs text-muted-foreground">| {lead.role}</span>}
                        </div>
                      </div>

                      {/* Stage */}
                      <Badge className={`${stage?.color} text-white text-xs`}>
                        {stage?.label || lead.stage}
                      </Badge>

                      {/* Follow-ups */}
                      <div className="text-xs text-muted-foreground text-right w-20 hidden sm:block">
                        {lead.followup_count} emails
                        {lead.next_followup_at && (
                          <div className="text-xs text-amber-600">
                            Due {new Date(lead.next_followup_at).toLocaleDateString()}
                          </div>
                        )}
                      </div>

                      {/* Action */}
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={processing === lead.id}
                        onClick={(e) => { e.stopPropagation(); triggerAgent(lead.id); }}
                      >
                        {processing === lead.id ? "Running..." : "Run Agent"}
                      </Button>
                    </div>

                    {/* Inline Detail Panel */}
                    {isSelected && (
                      <div className="ml-4 mr-4 mt-1 mb-2 p-4 rounded-lg border border-primary/20 bg-muted/30">
                        <h4 className="text-sm font-semibold mb-3">{lead.name} — Details</h4>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                          <div><span className="text-muted-foreground text-xs block">Email</span> {lead.email}</div>
                          <div><span className="text-muted-foreground text-xs block">Company</span> {lead.company || "—"}</div>
                          <div><span className="text-muted-foreground text-xs block">Role</span> {lead.role || "—"}</div>
                          <div><span className="text-muted-foreground text-xs block">Score</span> <span className={scoreColor(lead.score)}>{lead.score}/100</span></div>
                          <div><span className="text-muted-foreground text-xs block">Stage</span> {stage?.label || lead.stage}</div>
                          <div><span className="text-muted-foreground text-xs block">Follow-ups</span> {lead.followup_count}</div>
                          <div><span className="text-muted-foreground text-xs block">Created</span> {new Date(lead.created_at).toLocaleDateString()}</div>
                          <div><span className="text-muted-foreground text-xs block">Deal Value</span> {lead.deal_value_usd ? `$${lead.deal_value_usd.toLocaleString()}` : "—"}</div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Agent Result Display */}
      {agentResult && (
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle className="text-sm">Agent Execution Result</CardTitle>
              <div className="flex items-center gap-2">
                <Badge variant={agentResult.status === "completed" ? "success" : agentResult.status === "error" ? "destructive" : "secondary"}>
                  {agentResult.status}
                </Badge>
                {agentResult.confidence !== null && (
                  <Badge variant="outline">
                    Confidence: {Math.round((agentResult.confidence > 1 ? agentResult.confidence : agentResult.confidence * 100))}%
                  </Badge>
                )}
                <Button variant="outline" size="sm" onClick={() => setAgentResult(null)}>Dismiss</Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Qualification Output */}
            {agentResult.output.qualification && (
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase mb-1">Qualification</p>
                <div className="bg-muted rounded p-3 text-sm">
                  {typeof agentResult.output.qualification === "object" ? (
                    <pre className="font-mono text-xs whitespace-pre-wrap">{JSON.stringify(agentResult.output.qualification, null, 2)}</pre>
                  ) : (
                    <p>{String(agentResult.output.qualification)}</p>
                  )}
                </div>
              </div>
            )}

            {/* Recommendation */}
            {agentResult.output.recommendation && (
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase mb-1">Next Action Recommendation</p>
                <div className="bg-blue-50 border border-blue-200 rounded p-3 text-sm text-blue-900">
                  {String(agentResult.output.recommendation)}
                </div>
              </div>
            )}

            {/* Email Content */}
            {agentResult.output.email && (
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase mb-1">Email Content</p>
                <div className="bg-muted rounded p-3 text-sm space-y-1">
                  {agentResult.output.email.subject && <p><span className="font-medium">Subject:</span> {agentResult.output.email.subject}</p>}
                  {agentResult.output.email.body && <pre className="font-mono text-xs whitespace-pre-wrap mt-2">{agentResult.output.email.body}</pre>}
                </div>
              </div>
            )}

            {/* Lead Score Update */}
            {agentResult.output.lead_score !== undefined && (
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground uppercase">Updated Score:</span>
                <span className={`font-bold ${scoreColor(agentResult.output.lead_score)}`}>{agentResult.output.lead_score}/100</span>
              </div>
            )}

            {/* Lead Stage Update */}
            {agentResult.output.lead_stage && (
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground uppercase">Updated Stage:</span>
                <Badge variant="secondary">{agentResult.output.lead_stage}</Badge>
              </div>
            )}

            {/* Reasoning Trace */}
            {agentResult.reasoning_trace.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Show reasoning trace ({agentResult.reasoning_trace.length} steps)</summary>
                <div className="mt-2 bg-muted rounded p-3 space-y-1 font-mono">
                  {agentResult.reasoning_trace.map((step, i) => (
                    <div key={i} className="text-muted-foreground">{step}</div>
                  ))}
                </div>
              </details>
            )}

            {/* Raw output fallback if no structured fields */}
            {!agentResult.output.qualification && !agentResult.output.recommendation && !agentResult.output.email && Object.keys(agentResult.output).length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase mb-1">Agent Output</p>
                <pre className="bg-muted rounded p-3 text-xs font-mono whitespace-pre-wrap max-h-60 overflow-auto">
                  {JSON.stringify(agentResult.output, null, 2)}
                </pre>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
