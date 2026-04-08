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
  PieChart,
  Pie,
  Cell,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Incident {
  title: string;
  severity: string;
  status: string;
  assignee: string;
  duration_hours: number;
}

interface SeverityBreakdown {
  P1: number;
  P2: number;
  P3: number;
  P4: number;
}

interface TicketTrend {
  date: string;
  tickets: number;
}

interface SLACompliance {
  priority: string;
  compliance_pct: number;
}

interface TicketCategory {
  category: string;
  count: number;
}

interface VendorSpend {
  category: string;
  spend: number;
}

interface Vendor {
  name: string;
  category: string;
  sla_pct: number;
  spend: number;
  contract_end: string;
}

interface MaintenanceItem {
  item: string;
  scheduled_date: string;
  status: string;
}

interface COOKPIData {
  demo: boolean;
  stale?: boolean;
  company_id: string;
  // IT Ops
  active_incidents: number;
  mttr_hours: number;
  uptime_pct: number;
  change_success_rate: number;
  severity_breakdown: SeverityBreakdown;
  recent_incidents: Incident[];
  // Support
  open_tickets: number;
  resolved_today: number;
  csat_score: number;
  deflection_rate: number;
  ticket_volume_trend: TicketTrend[];
  sla_compliance: SLACompliance[];
  top_ticket_categories: TicketCategory[];
  // Vendors
  active_vendors: number;
  contracts_expiring_30d: number;
  vendor_spend_by_category: VendorSpend[];
  vendor_scorecard: Vendor[];
  // Facilities
  open_maintenance_requests: number;
  asset_utilization_pct: number;
  travel_expense_mtd: number;
  upcoming_maintenance: MaintenanceItem[];
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

const TABS = ["IT Ops", "Support", "Vendors", "Facilities"] as const;
type Tab = (typeof TABS)[number];

const SEVERITY_COLORS: Record<string, string> = {
  P1: "#ef4444",
  P2: "#f97316",
  P3: "#f59e0b",
  P4: "#3b82f6",
};

const PIE_COLORS = ["#3b82f6", "#6366f1", "#8b5cf6", "#f59e0b", "#22c55e", "#ef4444", "#f97316"];

const statusBadge: Record<string, "warning" | "default" | "success" | "destructive"> = {
  open: "warning",
  in_progress: "default",
  resolved: "success",
  closed: "success",
  scheduled: "default",
  overdue: "destructive",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function COODashboard() {
  const [data, setData] = useState<COOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("IT Ops");

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const [kpiResp] = await Promise.allSettled([api.get("/kpis/coo")]);
      if (kpiResp.status === "fulfilled") {
        setData(kpiResp.value.data);
      } else {
        setError("Failed to load COO KPIs");
      }
    } catch {
      setError("Failed to load COO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">COO Dashboard</h2>
        <p className="text-muted-foreground">Loading operations data...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">COO Dashboard</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  // Severity chart data
  const severityData = Object.entries(data.severity_breakdown).map(([sev, count]) => ({
    severity: sev,
    count,
  }));

  // Vendor spend pie data
  const vendorSpendData = data.vendor_spend_by_category.map((v) => ({
    name: v.category,
    value: v.spend,
  }));

  return (
    <div className="space-y-6">
      <Helmet>
        <title>COO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">COO Dashboard</h2>
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

      {/* ── IT Ops Tab ── */}
      {tab === "IT Ops" && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { label: "Active Incidents", value: formatNumber(data.active_incidents), color: "text-red-600" },
              { label: "MTTR (hours)", value: `${data.mttr_hours}h`, color: "text-orange-600" },
              { label: "Uptime %", value: `${data.uptime_pct}%`, color: "text-emerald-600" },
              { label: "Change Success Rate", value: `${data.change_success_rate}%`, color: "text-blue-600" },
            ].map((m) => (
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
              <CardTitle className="text-sm font-semibold">Incident Severity Breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={severityData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="severity" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="count" name="Incidents">
                    {severityData.map((entry) => (
                      <Cell key={entry.severity} fill={SEVERITY_COLORS[entry.severity] || "#94a3b8"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Recent Incidents</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Title</th>
                      <th className="pb-2 pr-4">Severity</th>
                      <th className="pb-2 pr-4">Status</th>
                      <th className="pb-2 pr-4">Assignee</th>
                      <th className="pb-2 text-right">Duration (hrs)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_incidents.map((inc, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{inc.title}</td>
                        <td className="py-2 pr-4">
                          <Badge variant={inc.severity === "P1" ? "destructive" : "secondary"}>{inc.severity}</Badge>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={statusBadge[inc.status] || "default"}>{inc.status}</Badge>
                        </td>
                        <td className="py-2 pr-4">{inc.assignee}</td>
                        <td className="py-2 text-right">{inc.duration_hours}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* ── Support Tab ── */}
      {tab === "Support" && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { label: "Open Tickets", value: formatNumber(data.open_tickets), color: "text-orange-600" },
              { label: "Resolved Today", value: formatNumber(data.resolved_today), color: "text-emerald-600" },
              { label: "CSAT Score", value: `${data.csat_score}%`, color: "text-blue-600" },
              { label: "Deflection Rate", value: `${data.deflection_rate}%`, color: "text-purple-600" },
            ].map((m) => (
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
              <CardTitle className="text-sm font-semibold">Ticket Volume Trend (30 Days)</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={data.ticket_volume_trend}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d: string) => d.slice(5)} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="tickets" stroke="#3b82f6" strokeWidth={2} dot={false} name="Tickets" />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">SLA Compliance by Priority</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {data.sla_compliance.map((s) => (
                    <div key={s.priority} className="flex items-center justify-between p-3 rounded border">
                      <p className="text-sm font-medium">{s.priority}</p>
                      <span className={`text-sm font-bold ${s.compliance_pct >= 90 ? "text-green-600" : s.compliance_pct >= 70 ? "text-yellow-600" : "text-red-600"}`}>
                        {s.compliance_pct}%
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">Top Ticket Categories</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={data.top_ticket_categories} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis type="number" tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="category" tick={{ fontSize: 11 }} width={120} />
                    <Tooltip />
                    <Bar dataKey="count" name="Tickets" fill="#6366f1" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {/* ── Vendors Tab ── */}
      {tab === "Vendors" && (
        <>
          <div className="grid grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Active Vendors</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-blue-600">{formatNumber(data.active_vendors)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Contracts Expiring in 30 Days</CardTitle>
              </CardHeader>
              <CardContent>
                <p className={`text-2xl font-bold ${data.contracts_expiring_30d > 0 ? "text-orange-600" : "text-green-600"}`}>
                  {data.contracts_expiring_30d}
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Vendor Spend by Category</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={vendorSpendData}
                    cx="50%"
                    cy="50%"
                    outerRadius={100}
                    dataKey="value"
                    nameKey="name"
                    label={({ name, percent }: any) => `${name} ${(percent * 100).toFixed(0)}%`}
                  >
                    {vendorSpendData.map((_, idx) => (
                      <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: any) => INR.format(v)} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Vendor Scorecard</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Vendor</th>
                      <th className="pb-2 pr-4">Category</th>
                      <th className="pb-2 pr-4 text-right">SLA %</th>
                      <th className="pb-2 pr-4 text-right">Spend</th>
                      <th className="pb-2 text-right">Contract End</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.vendor_scorecard.map((v) => (
                      <tr key={v.name} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{v.name}</td>
                        <td className="py-2 pr-4">{v.category}</td>
                        <td className="py-2 pr-4 text-right">
                          <span className={v.sla_pct >= 95 ? "text-green-600" : "text-orange-600"}>
                            {v.sla_pct}%
                          </span>
                        </td>
                        <td className="py-2 pr-4 text-right">{INR.format(v.spend)}</td>
                        <td className="py-2 text-right">
                          {new Date(v.contract_end).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
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

      {/* ── Facilities Tab ── */}
      {tab === "Facilities" && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Open Maintenance Requests</CardTitle>
              </CardHeader>
              <CardContent>
                <p className={`text-2xl font-bold ${data.open_maintenance_requests > 0 ? "text-orange-600" : "text-green-600"}`}>
                  {data.open_maintenance_requests}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Asset Utilization %</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-blue-600">{data.asset_utilization_pct}%</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm text-muted-foreground">Travel Expense MTD</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-purple-600">{INR.format(data.travel_expense_mtd)}</p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Upcoming Maintenance Schedule</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {data.upcoming_maintenance.map((m, idx) => (
                  <div key={idx} className="flex items-center justify-between p-3 rounded border">
                    <div>
                      <p className="text-sm font-medium">{m.item}</p>
                      <p className="text-xs text-muted-foreground">
                        Scheduled: {new Date(m.scheduled_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
                      </p>
                    </div>
                    <Badge variant={statusBadge[m.status] || "default"}>{m.status}</Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
