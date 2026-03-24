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

export default function SalesPipeline() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [funnel, setFunnel] = useState<Record<string, number>>({});
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [stageFilter, setStageFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState<string | null>(null);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);

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
    try {
      await api.post("/sales/pipeline/process-lead", { lead_id: leadId, action: "qualify_and_respond" });
      await fetchData();
    } catch { /* ignore */ }
    finally { setProcessing(null); }
  }

  const scoreColor = (score: number) =>
    score >= 70 ? "text-emerald-600" : score >= 40 ? "text-amber-600" : "text-slate-500";

  return (
    <div className="space-y-6">
      <Helmet>
        <title>Sales Pipeline — AgenticOrg</title>
      </Helmet>

      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Sales Pipeline</h2>
          <p className="text-sm text-muted-foreground">Aarav (AI Sales Agent) manages lead qualification and outreach</p>
        </div>
        <Button onClick={fetchData} variant="outline" size="sm">Refresh</Button>
      </div>

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
            <p className="text-sm text-muted-foreground">No leads yet. Demo requests will appear here automatically.</p>
          ) : (
            <div className="space-y-2">
              {leads.map((lead) => {
                const stage = STAGES.find((s) => s.key === lead.stage);
                return (
                  <div
                    key={lead.id}
                    className={`flex items-center gap-4 p-3 rounded-lg border hover:bg-muted/50 transition-colors cursor-pointer ${selectedLead?.id === lead.id ? "bg-muted border-primary" : ""}`}
                    onClick={() => setSelectedLead(selectedLead?.id === lead.id ? null : lead)}
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
                    <div className="text-xs text-muted-foreground text-right w-20">
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
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Selected Lead Detail */}
      {selectedLead && (
        <Card>
          <CardHeader>
            <CardTitle>{selectedLead.name} — Details</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div><span className="text-muted-foreground">Email:</span> {selectedLead.email}</div>
              <div><span className="text-muted-foreground">Company:</span> {selectedLead.company || "—"}</div>
              <div><span className="text-muted-foreground">Role:</span> {selectedLead.role || "—"}</div>
              <div><span className="text-muted-foreground">Score:</span> <span className={scoreColor(selectedLead.score)}>{selectedLead.score}/100</span></div>
              <div><span className="text-muted-foreground">Stage:</span> {selectedLead.stage}</div>
              <div><span className="text-muted-foreground">Follow-ups:</span> {selectedLead.followup_count}</div>
              <div><span className="text-muted-foreground">Created:</span> {new Date(selectedLead.created_at).toLocaleDateString()}</div>
              <div><span className="text-muted-foreground">Deal Value:</span> {selectedLead.deal_value_usd ? `$${selectedLead.deal_value_usd}` : "—"}</div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
