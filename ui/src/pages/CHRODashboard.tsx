import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  LineChart,
  Line,
  Legend,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DeptBreakdown {
  department: string;
  headcount: number;
  attrition_pct: number;
  avg_tenure_years: number;
}

interface HeadcountTrend {
  month: string;
  headcount: number;
}

interface PayrollRun {
  month: string;
  total_ctc: number;
  pf: number;
  esi: number;
  pt: number;
  tds: number;
  status: string;
}

interface OpenPosition {
  role: string;
  department: string;
  days_open: number;
  applicants: number;
}

interface RecruitmentFunnel {
  applied: number;
  screened: number;
  interviewed: number;
  offered: number;
  accepted: number;
}

interface TimeToHireTrend {
  month: string;
  days: number;
}

interface SurveyConcern {
  concern: string;
  mentions: number;
}

interface AttritionRisk {
  department: string;
  risk_level: string;
}

interface ComplianceFiling {
  filing: string;
  period: string;
  due_date: string;
  status: string;
}

interface CHROKPIData {
  demo: boolean;
  stale?: boolean;
  company_id: string;
  total_employees: number;
  attrition_rate: number;
  new_joiners_mtd: number;
  open_positions: number;
  department_breakdown: DeptBreakdown[];
  headcount_trend: HeadcountTrend[];
  payroll_current_month_status: string;
  payroll_total_ctc: number;
  payroll_pf: number;
  payroll_esi: number;
  payroll_pt: number;
  payroll_tds: number;
  payroll_history: PayrollRun[];
  recruitment_funnel: RecruitmentFunnel;
  open_positions_list: OpenPosition[];
  time_to_hire_trend: TimeToHireTrend[];
  enps_score: number;
  pulse_survey_score: number;
  attrition_risk: AttritionRisk[];
  top_concerns: SurveyConcern[];
  epfo_filings: ComplianceFiling[];
  esi_filings: ComplianceFiling[];
  pt_filings: ComplianceFiling[];
  pending_compliance_items: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

function lakhs(value: number): string {
  return (value / 100_000).toFixed(1) + "L";
}

const TABS = ["Workforce", "Payroll", "Recruitment", "Engagement", "Compliance"] as const;
type Tab = (typeof TABS)[number];

const statusBadge: Record<string, "warning" | "default" | "success" | "destructive"> = {
  pending: "warning",
  upcoming: "default",
  filed: "success",
  processed: "success",
  overdue: "destructive",
};

const RISK_COLORS: Record<string, string> = {
  high: "bg-red-100 text-red-800",
  medium: "bg-yellow-100 text-yellow-800",
  low: "bg-green-100 text-green-800",
};

const FUNNEL_COLORS = ["#3b82f6", "#6366f1", "#8b5cf6", "#f59e0b", "#22c55e"];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CHRODashboard() {
  const [data, setData] = useState<CHROKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Workforce");

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const [kpiResp] = await Promise.allSettled([api.get("/kpis/chro")]);
      if (kpiResp.status === "fulfilled") {
        setData(kpiResp.value.data);
      } else {
        setError("Failed to load CHRO KPIs");
      }
    } catch {
      setError("Failed to load CHRO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CHRO Dashboard</h2>
        <p className="text-muted-foreground">Loading HR data...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CHRO Dashboard</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  const topMetrics = [
    { label: "Total Employees", value: formatNumber(data.total_employees), color: "text-blue-600" },
    { label: "Attrition Rate", value: `${data.attrition_rate}%`, color: "text-red-600" },
    { label: "New Joiners (MTD)", value: formatNumber(data.new_joiners_mtd), color: "text-emerald-600" },
    { label: "Open Positions", value: formatNumber(data.open_positions), color: "text-purple-600" },
  ];

  // Funnel data for recruitment
  const funnelData = data.recruitment_funnel
    ? [
        { stage: "Applied", count: data.recruitment_funnel.applied },
        { stage: "Screened", count: data.recruitment_funnel.screened },
        { stage: "Interviewed", count: data.recruitment_funnel.interviewed },
        { stage: "Offered", count: data.recruitment_funnel.offered },
        { stage: "Accepted", count: data.recruitment_funnel.accepted },
      ]
    : [];

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CHRO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">CHRO Dashboard</h2>
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

      {/* ── Workforce Tab ── */}
      {tab === "Workforce" && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {topMetrics.map((m) => (
              <Card key={m.label}>
                <CardHeader>
                  <CardTitle className="text-sm text-muted-foreground">{m.label}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
                </CardContent>
              </Card>
            ))}
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Department Breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Department</th>
                      <th className="pb-2 pr-4 text-right">Headcount</th>
                      <th className="pb-2 pr-4 text-right">Attrition %</th>
                      <th className="pb-2 text-right">Avg Tenure (yrs)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.department_breakdown.map((d) => (
                      <tr key={d.department} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{d.department}</td>
                        <td className="py-2 pr-4 text-right">{d.headcount}</td>
                        <td className="py-2 pr-4 text-right">
                          <span className={d.attrition_pct > 15 ? "text-red-600" : "text-green-600"}>
                            {d.attrition_pct}%
                          </span>
                        </td>
                        <td className="py-2 text-right">{d.avg_tenure_years}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Headcount Trend (12 Months)</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={data.headcount_trend}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} tickFormatter={(m: string) => m.slice(5)} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="headcount" stroke="#3b82f6" strokeWidth={2} dot={false} name="Headcount" />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Payroll Tab ── */}
      {tab === "Payroll" && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Current Month Payroll Status</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-4 mb-6">
                <Badge variant={data.payroll_current_month_status === "processed" ? "success" : "warning"}>
                  {data.payroll_current_month_status === "processed" ? "Processed" : "Pending"}
                </Badge>
              </div>
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">Total CTC</p>
                  <p className="text-lg font-bold">{INR.format(data.payroll_total_ctc)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">PF Contribution</p>
                  <p className="text-lg font-bold">{INR.format(data.payroll_pf)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">ESI</p>
                  <p className="text-lg font-bold">{INR.format(data.payroll_esi)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Professional Tax</p>
                  <p className="text-lg font-bold">{INR.format(data.payroll_pt)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">TDS on Salary</p>
                  <p className="text-lg font-bold">{INR.format(data.payroll_tds)}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Payroll Run History (Last 6 Months)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Month</th>
                      <th className="pb-2 pr-4 text-right">Total CTC</th>
                      <th className="pb-2 pr-4 text-right">PF</th>
                      <th className="pb-2 pr-4 text-right">ESI</th>
                      <th className="pb-2 pr-4 text-right">PT</th>
                      <th className="pb-2 pr-4 text-right">TDS</th>
                      <th className="pb-2 text-right">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.payroll_history.map((r) => (
                      <tr key={r.month} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{r.month}</td>
                        <td className="py-2 pr-4 text-right">{lakhs(r.total_ctc)}</td>
                        <td className="py-2 pr-4 text-right">{lakhs(r.pf)}</td>
                        <td className="py-2 pr-4 text-right">{lakhs(r.esi)}</td>
                        <td className="py-2 pr-4 text-right">{lakhs(r.pt)}</td>
                        <td className="py-2 pr-4 text-right">{lakhs(r.tds)}</td>
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

      {/* ── Recruitment Tab ── */}
      {tab === "Recruitment" && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Recruitment Funnel</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={funnelData} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="stage" tick={{ fontSize: 12 }} width={100} />
                  <Tooltip />
                  <Bar dataKey="count" name="Candidates">
                    {funnelData.map((_, idx) => (
                      <rect key={idx} fill={FUNNEL_COLORS[idx % FUNNEL_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Open Positions</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Role</th>
                      <th className="pb-2 pr-4">Department</th>
                      <th className="pb-2 pr-4 text-right">Days Open</th>
                      <th className="pb-2 text-right">Applicants</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.open_positions_list.map((p) => (
                      <tr key={`${p.role}-${p.department}`} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{p.role}</td>
                        <td className="py-2 pr-4">{p.department}</td>
                        <td className="py-2 pr-4 text-right">
                          <span className={p.days_open > 30 ? "text-red-600" : ""}>{p.days_open}</span>
                        </td>
                        <td className="py-2 text-right">{p.applicants}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Time-to-Hire Trend</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={data.time_to_hire_trend}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} tickFormatter={(m: string) => m.slice(5)} />
                  <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v}d`} />
                  <Tooltip formatter={(v: any) => `${v} days`} />
                  <Legend />
                  <Line type="monotone" dataKey="days" stroke="#8b5cf6" strokeWidth={2} dot={false} name="Avg Days to Hire" />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Engagement Tab ── */}
      {tab === "Engagement" && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">Employee Net Promoter Score (eNPS)</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col items-center justify-center py-8">
                <p className={`text-5xl font-bold ${data.enps_score >= 30 ? "text-green-600" : data.enps_score >= 0 ? "text-yellow-600" : "text-red-600"}`}>
                  {data.enps_score}
                </p>
                <p className="text-sm text-muted-foreground mt-2">eNPS Score</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">Pulse Survey Score</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col items-center justify-center py-8">
                <p className={`text-5xl font-bold ${data.pulse_survey_score >= 70 ? "text-green-600" : data.pulse_survey_score >= 50 ? "text-yellow-600" : "text-red-600"}`}>
                  {data.pulse_survey_score}%
                </p>
                <p className="text-sm text-muted-foreground mt-2">overall satisfaction</p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Attrition Risk by Department</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {data.attrition_risk.map((r) => (
                  <div key={r.department} className={`p-3 rounded ${RISK_COLORS[r.risk_level] || "bg-gray-100"}`}>
                    <p className="text-sm font-medium">{r.department}</p>
                    <p className="text-xs capitalize">{r.risk_level} risk</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Top Concerns from Surveys</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {data.top_concerns.map((c) => (
                  <div key={c.concern} className="flex items-center justify-between p-3 rounded border">
                    <p className="text-sm font-medium">{c.concern}</p>
                    <Badge variant="secondary">{c.mentions} mentions</Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Compliance Tab ── */}
      {tab === "Compliance" && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Pending Compliance Items</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center py-6">
              <p className={`text-4xl font-bold ${data.pending_compliance_items > 0 ? "text-orange-600" : "text-green-600"}`}>
                {data.pending_compliance_items}
              </p>
              <p className="text-sm text-muted-foreground mt-2">items pending action</p>
            </CardContent>
          </Card>

          {[
            { title: "EPFO Filing Status", items: data.epfo_filings },
            { title: "ESI Filing Status", items: data.esi_filings },
            { title: "Professional Tax Filing Status", items: data.pt_filings },
          ].map((section) => (
            <Card key={section.title}>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">{section.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {section.items.map((f) => (
                    <div key={`${f.filing}-${f.period}`} className="flex items-center justify-between p-3 rounded border">
                      <div>
                        <p className="text-sm font-medium">{f.filing}</p>
                        <p className="text-xs text-muted-foreground">
                          {f.period} — Due: {new Date(f.due_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
                        </p>
                      </div>
                      <Badge variant={statusBadge[f.status] || "default"}>{f.status}</Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </>
      )}
    </div>
  );
}
