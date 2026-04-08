import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ContractReview {
  title: string;
  type: string;
  counterparty: string;
  status: string;
  due_date: string;
}

interface LitigationItem {
  case_title: string;
  court: string;
  status: string;
  next_hearing: string;
}

interface RiskItem {
  risk: string;
  likelihood: string;
  impact: string;
  owner: string;
  status: string;
}

interface StatutoryFiling {
  filing: string;
  due_date: string;
  status: string;
}

interface PressCoverage {
  headline: string;
  outlet: string;
  date: string;
  sentiment: string;
}

interface CBOKPIData {
  demo: boolean;
  stale?: boolean;
  company_id: string;
  // Legal
  active_contracts: number;
  pending_reviews: number;
  nda_count: number;
  contract_review_queue: ContractReview[];
  litigation_tracker: LitigationItem[];
  // Risk
  compliance_score: number;
  open_audit_findings: number;
  sanctions_screened_mtd: number;
  risk_register: RiskItem[];
  // Corporate
  next_board_meeting: string;
  days_until_agm: number;
  statutory_filings: StatutoryFiling[];
  share_register_summary: {
    total_shares: number;
    promoter_pct: number;
    public_pct: number;
    institutional_pct: number;
  };
  // Comms
  internal_comms_reach_pct: number;
  media_mentions_mtd: number;
  investor_queries_open: number;
  recent_press_coverage: PressCoverage[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

const TABS = ["Legal", "Risk", "Corporate", "Comms"] as const;
type Tab = (typeof TABS)[number];

const statusBadge: Record<string, "warning" | "default" | "success" | "destructive"> = {
  pending: "warning",
  in_review: "warning",
  upcoming: "default",
  filed: "success",
  approved: "success",
  active: "success",
  closed: "success",
  overdue: "destructive",
  open: "warning",
  mitigated: "success",
};

const LIKELIHOOD_COLORS: Record<string, string> = {
  high: "text-red-600",
  medium: "text-yellow-600",
  low: "text-green-600",
};

const IMPACT_COLORS: Record<string, string> = {
  high: "text-red-600",
  medium: "text-yellow-600",
  low: "text-green-600",
};

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "text-green-600",
  neutral: "text-gray-600",
  negative: "text-red-600",
};

// Compliance score gauge: 0-100
function ComplianceGauge({ score }: { score: number }) {
  const gaugeData = [
    { name: "Score", value: score },
    { name: "Remaining", value: 100 - score },
  ];
  const color = score >= 80 ? "#22c55e" : score >= 60 ? "#f59e0b" : "#ef4444";

  return (
    <div className="flex flex-col items-center">
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie
            data={gaugeData}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={85}
            startAngle={180}
            endAngle={0}
            paddingAngle={0}
            dataKey="value"
          >
            <Cell fill={color} />
            <Cell fill="#e5e7eb" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="text-center -mt-16">
        <p className="text-3xl font-bold" style={{ color }}>
          {score}
        </p>
        <p className="text-xs text-muted-foreground">out of 100</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CBODashboard() {
  const [data, setData] = useState<CBOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Legal");

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const [kpiResp] = await Promise.allSettled([api.get("/kpis/cbo")]);
      if (kpiResp.status === "fulfilled") {
        setData(kpiResp.value.data);
      } else {
        setError("Failed to load CBO KPIs");
      }
    } catch {
      setError("Failed to load CBO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CBO Dashboard</h2>
        <p className="text-muted-foreground">Loading business operations data...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CBO Dashboard</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CBO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">CBO Dashboard</h2>
        <div className="flex items-center gap-2">
          {data.stale && (
            <Badge variant="warning">Data may be stale</Badge>
          )}
          {data.demo && <Badge variant="secondary">Demo Data</Badge>}
        </div>
      </div>

      {/* Tab navigation */}
      <div className="flex gap-2 border-b pb-2 overflow-x-auto">
        {TABS.map((t) => (
          <Button
            key={t}
            variant={tab === t ? "default" : "ghost"}
            size="sm"
            onClick={() => setTab(t)}
          >
            {t}
          </Button>
        ))}
      </div>

      {/* ── Legal Tab ── */}
      {tab === "Legal" && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Active Contracts</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-blue-600">{formatNumber(data.active_contracts)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Pending Reviews</CardTitle>
              </CardHeader>
              <CardContent>
                <p className={`text-2xl font-bold ${data.pending_reviews > 0 ? "text-orange-600" : "text-green-600"}`}>
                  {data.pending_reviews}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">NDA Count</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-purple-600">{formatNumber(data.nda_count)}</p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Contract Review Queue</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Title</th>
                      <th className="pb-2 pr-4">Type</th>
                      <th className="pb-2 pr-4">Counterparty</th>
                      <th className="pb-2 pr-4">Status</th>
                      <th className="pb-2 text-right">Due Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.contract_review_queue.map((c, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{c.title}</td>
                        <td className="py-2 pr-4">{c.type}</td>
                        <td className="py-2 pr-4">{c.counterparty}</td>
                        <td className="py-2 pr-4">
                          <Badge variant={statusBadge[c.status] || "default"}>{c.status}</Badge>
                        </td>
                        <td className="py-2 text-right">
                          {new Date(c.due_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Litigation Tracker</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {data.litigation_tracker.map((l, idx) => (
                  <div key={idx} className="flex items-center justify-between p-3 rounded border">
                    <div>
                      <p className="text-sm font-medium">{l.case_title}</p>
                      <p className="text-xs text-muted-foreground">{l.court}</p>
                    </div>
                    <div className="text-right">
                      <Badge variant={statusBadge[l.status] || "default"}>{l.status}</Badge>
                      <p className="text-xs text-muted-foreground mt-1">
                        Next: {new Date(l.next_hearing).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Risk Tab ── */}
      {tab === "Risk" && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">Compliance Score</CardTitle>
              </CardHeader>
              <CardContent>
                <ComplianceGauge score={data.compliance_score} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Open Audit Findings</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col items-center justify-center py-8">
                <p className={`text-4xl font-bold ${data.open_audit_findings > 0 ? "text-orange-600" : "text-green-600"}`}>
                  {data.open_audit_findings}
                </p>
                <p className="text-sm text-muted-foreground mt-2">findings pending resolution</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Sanctions Screened (MTD)</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col items-center justify-center py-8">
                <p className="text-4xl font-bold text-blue-600">{formatNumber(data.sanctions_screened_mtd)}</p>
                <p className="text-sm text-muted-foreground mt-2">entities screened</p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Risk Register</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Risk</th>
                      <th className="pb-2 pr-4">Likelihood</th>
                      <th className="pb-2 pr-4">Impact</th>
                      <th className="pb-2 pr-4">Owner</th>
                      <th className="pb-2 text-right">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.risk_register.map((r, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{r.risk}</td>
                        <td className="py-2 pr-4">
                          <span className={`capitalize font-medium ${LIKELIHOOD_COLORS[r.likelihood] || ""}`}>
                            {r.likelihood}
                          </span>
                        </td>
                        <td className="py-2 pr-4">
                          <span className={`capitalize font-medium ${IMPACT_COLORS[r.impact] || ""}`}>
                            {r.impact}
                          </span>
                        </td>
                        <td className="py-2 pr-4">{r.owner}</td>
                        <td className="py-2 text-right">
                          <Badge variant={statusBadge[r.status] || "default"}>{r.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Corporate Tab ── */}
      {tab === "Corporate" && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Next Board Meeting</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xl font-bold text-blue-600">
                  {new Date(data.next_board_meeting).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" })}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Days Until AGM</CardTitle>
              </CardHeader>
              <CardContent>
                <p className={`text-2xl font-bold ${data.days_until_agm <= 30 ? "text-orange-600" : "text-blue-600"}`}>
                  {data.days_until_agm} days
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Statutory Filing Status (MCA, ROC)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {data.statutory_filings.map((f, idx) => (
                  <div key={idx} className="flex items-center justify-between p-3 rounded border">
                    <div>
                      <p className="text-sm font-medium">{f.filing}</p>
                      <p className="text-xs text-muted-foreground">
                        Due: {new Date(f.due_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
                      </p>
                    </div>
                    <Badge variant={statusBadge[f.status] || "default"}>{f.status}</Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Share Register Summary</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">Total Shares</p>
                  <p className="text-lg font-bold">{formatNumber(data.share_register_summary.total_shares)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Promoter %</p>
                  <p className="text-lg font-bold text-blue-600">{data.share_register_summary.promoter_pct}%</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Public %</p>
                  <p className="text-lg font-bold text-emerald-600">{data.share_register_summary.public_pct}%</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Institutional %</p>
                  <p className="text-lg font-bold text-purple-600">{data.share_register_summary.institutional_pct}%</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Comms Tab ── */}
      {tab === "Comms" && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Internal Comms Reach</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-blue-600">{data.internal_comms_reach_pct}%</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Media Mentions (MTD)</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-emerald-600">{formatNumber(data.media_mentions_mtd)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Investor Queries Open</CardTitle>
              </CardHeader>
              <CardContent>
                <p className={`text-2xl font-bold ${data.investor_queries_open > 0 ? "text-orange-600" : "text-green-600"}`}>
                  {data.investor_queries_open}
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Recent Press Coverage</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Headline</th>
                      <th className="pb-2 pr-4">Outlet</th>
                      <th className="pb-2 pr-4">Date</th>
                      <th className="pb-2 text-right">Sentiment</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_press_coverage.map((p, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium max-w-[300px] truncate" title={p.headline}>{p.headline}</td>
                        <td className="py-2 pr-4">{p.outlet}</td>
                        <td className="py-2 pr-4">
                          {new Date(p.date).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                        </td>
                        <td className="py-2 text-right">
                          <span className={`capitalize font-medium ${SENTIMENT_COLORS[p.sentiment] || ""}`}>
                            {p.sentiment}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
