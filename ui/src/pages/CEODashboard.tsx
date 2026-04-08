import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FinanceQuadrant {
  cash_runway_months: number;
  ar_total: number;
  ap_total: number;
  pending_invoices: number;
}

interface HRQuadrant {
  headcount: number;
  attrition_rate: number;
  open_positions: number;
}

interface MarketingQuadrant {
  mqls: number;
  cac: number;
  campaign_roi: number;
}

interface OpsQuadrant {
  ticket_sla_pct: number;
  active_incidents: number;
  vendor_spend_mtd: number;
}

interface Escalation {
  item: string;
  department: string;
  urgency: string;
  requested_by: string;
  age_hours: number;
}

interface AgentAction {
  agent: string;
  action: string;
  domain: string;
  timestamp: string;
}

interface CEOKPIData {
  demo: boolean;
  stale?: boolean;
  company_id: string;
  // Top KPIs
  revenue_mtd: number;
  total_employees: number;
  active_incidents: number;
  pipeline_value: number;
  health_score: number;
  // Quadrants
  finance: FinanceQuadrant;
  hr: HRQuadrant;
  marketing: MarketingQuadrant;
  ops: OpsQuadrant;
  // Bottom section
  recent_escalations: Escalation[];
  agent_actions: AgentAction[];
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

const URGENCY_COLORS: Record<string, string> = {
  high: "text-red-600",
  medium: "text-yellow-600",
  low: "text-green-600",
};

const URGENCY_BADGE: Record<string, "destructive" | "warning" | "default"> = {
  high: "destructive",
  medium: "warning",
  low: "default",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CEODashboard() {
  const [data, setData] = useState<CEOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const [kpiResp] = await Promise.allSettled([api.get("/kpis/ceo")]);
      if (kpiResp.status === "fulfilled") {
        setData(kpiResp.value.data);
      } else {
        setError("Failed to load CEO KPIs");
      }
    } catch {
      setError("Failed to load CEO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CEO Dashboard</h2>
        <p className="text-muted-foreground">Loading executive overview...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CEO Dashboard</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  const healthColor =
    data.health_score >= 80
      ? "text-green-600"
      : data.health_score >= 60
        ? "text-yellow-600"
        : "text-red-600";

  const topMetrics = [
    { label: "Revenue MTD", value: INR.format(data.revenue_mtd), color: "text-blue-600" },
    { label: "Total Employees", value: formatNumber(data.total_employees), color: "text-emerald-600" },
    { label: "Active Incidents", value: formatNumber(data.active_incidents), color: "text-red-600" },
    { label: "Pipeline Value", value: INR.format(data.pipeline_value), color: "text-purple-600" },
    { label: "Health Score", value: `${data.health_score}/100`, color: healthColor },
  ];

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CEO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">CEO Dashboard</h2>
        <div className="flex items-center gap-2">
          {data.stale && (
            <Badge variant="warning">Data may be stale</Badge>
          )}
          {data.demo && <Badge variant="secondary">Demo Data</Badge>}
        </div>
      </div>

      {/* ── Top KPI Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
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

      {/* ── 4 Quadrants ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Finance Quadrant */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-semibold">Finance</CardTitle>
            <Link to="/dashboard/cfo" className="text-xs text-blue-600 hover:underline">
              View Details &rarr;
            </Link>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Cash Runway</p>
                <p className="text-lg font-bold">{data.finance.cash_runway_months} mo</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">AR / AP</p>
                <p className="text-lg font-bold">{lakhs(data.finance.ar_total)} / {lakhs(data.finance.ap_total)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Pending Invoices</p>
                <p className="text-lg font-bold text-orange-600">{data.finance.pending_invoices}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* HR Quadrant */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-semibold">HR</CardTitle>
            <Link to="/dashboard/chro" className="text-xs text-blue-600 hover:underline">
              View Details &rarr;
            </Link>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Headcount</p>
                <p className="text-lg font-bold">{formatNumber(data.hr.headcount)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Attrition Rate</p>
                <p className={`text-lg font-bold ${data.hr.attrition_rate > 15 ? "text-red-600" : "text-green-600"}`}>
                  {data.hr.attrition_rate}%
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Open Positions</p>
                <p className="text-lg font-bold text-purple-600">{data.hr.open_positions}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Marketing Quadrant */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-semibold">Marketing</CardTitle>
            <Link to="/dashboard/cmo" className="text-xs text-blue-600 hover:underline">
              View Details &rarr;
            </Link>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">MQLs</p>
                <p className="text-lg font-bold">{formatNumber(data.marketing.mqls)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">CAC</p>
                <p className="text-lg font-bold">{INR.format(data.marketing.cac)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Campaign ROI</p>
                <p className="text-lg font-bold text-emerald-600">{data.marketing.campaign_roi}x</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Operations Quadrant */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-semibold">Operations</CardTitle>
            <Link to="/dashboard/coo" className="text-xs text-blue-600 hover:underline">
              View Details &rarr;
            </Link>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Ticket SLA</p>
                <p className={`text-lg font-bold ${data.ops.ticket_sla_pct >= 90 ? "text-green-600" : "text-orange-600"}`}>
                  {data.ops.ticket_sla_pct}%
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Incidents</p>
                <p className={`text-lg font-bold ${data.ops.active_incidents > 0 ? "text-red-600" : "text-green-600"}`}>
                  {data.ops.active_incidents}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Vendor Spend MTD</p>
                <p className="text-lg font-bold">{lakhs(data.ops.vendor_spend_mtd)}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Recent Escalations ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Recent Escalations</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="pb-2 pr-4">Item</th>
                  <th className="pb-2 pr-4">Department</th>
                  <th className="pb-2 pr-4">Urgency</th>
                  <th className="pb-2 pr-4">Requested By</th>
                  <th className="pb-2 text-right">Age (hrs)</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_escalations.map((e, idx) => (
                  <tr key={idx} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-medium">{e.item}</td>
                    <td className="py-2 pr-4">{e.department}</td>
                    <td className="py-2 pr-4">
                      <Badge variant={URGENCY_BADGE[e.urgency] || "default"}>
                        <span className={URGENCY_COLORS[e.urgency] || ""}>{e.urgency}</span>
                      </Badge>
                    </td>
                    <td className="py-2 pr-4">{e.requested_by}</td>
                    <td className="py-2 text-right">{e.age_hours}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* ── Agent Observatory ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Agent Observatory (Last 10 Actions)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="pb-2 pr-4">Agent</th>
                  <th className="pb-2 pr-4">Action</th>
                  <th className="pb-2 pr-4">Domain</th>
                  <th className="pb-2 text-right">Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {data.agent_actions.map((a, idx) => (
                  <tr key={idx} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-medium">{a.agent}</td>
                    <td className="py-2 pr-4">{a.action}</td>
                    <td className="py-2 pr-4">
                      <Badge variant="secondary">{a.domain}</Badge>
                    </td>
                    <td className="py-2 text-right text-muted-foreground">
                      {new Date(a.timestamp).toLocaleString("en-IN", {
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
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
