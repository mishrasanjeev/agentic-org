import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DomainBreakdown {
  domain: string;
  total: number;
  completed: number;
  failed: number;
  avg_confidence: number;
}

interface CFOKPIData {
  demo: boolean;
  company_id: string;
  agent_count: number;
  total_tasks_30d: number;
  success_rate: number; // percentage 0-100
  hitl_interventions: number;
  total_cost_usd: number;
  domain_breakdown: DomainBreakdown[];
  source?: string;
  cached_at?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CFODashboard() {
  const { t } = useTranslation();
  const [data, setData] = useState<CFOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
    // The company switcher writes to localStorage and forces a reload
    // (see CompanySwitcher.tsx), so we don't need an observer here.
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      // Codex 2026-04-22 multi-company isolation fix: CFO dashboard
      // previously hit /kpis/cfo without a company_id, so filter-state
      // from the company switcher was ignored and the board showed
      // tenant-wide numbers regardless of which company was selected.
      const companyId = localStorage.getItem("company_id") || "";
      const params = companyId ? { company_id: companyId } : {};
      const resp = await api.get("/kpis/cfo", { params });
      setData(resp.data);
    } catch {
      setError(t("errors.failedToLoadKpis", "Failed to load CFO KPIs"));
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">{t("kpi.cfoDashboard", "CFO Dashboard")}</h2>
        <p className="text-muted-foreground">{t("kpi.loading", "Loading...")}</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">{t("kpi.cfoDashboard", "CFO Dashboard")}</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  const domainBreakdown = data.domain_breakdown ?? [];
  const agentCount = data.agent_count ?? 0;
  const totalTasks = data.total_tasks_30d ?? 0;
  const successRate = data.success_rate ?? 0;
  const hitl = data.hitl_interventions ?? 0;
  const totalCost = data.total_cost_usd ?? 0;
  const isEmpty = agentCount === 0 && totalTasks === 0;

  const topMetrics = [
    {
      label: t("kpi.agents", "Agents"),
      value: agentCount.toLocaleString(),
      color: "text-blue-600",
    },
    {
      label: t("kpi.totalTasks", "Total Tasks (30d)"),
      value: totalTasks.toLocaleString(),
      color: "text-indigo-600",
    },
    {
      label: t("kpi.successRate", "Success Rate"),
      value: `${successRate.toFixed(1)}%`,
      color: "text-emerald-600",
    },
    {
      label: t("kpi.hitlInterventions", "HITL Interventions"),
      value: hitl.toLocaleString(),
      color: "text-orange-600",
    },
    {
      label: t("kpi.totalCost", "Total Cost (USD)"),
      value: USD.format(totalCost),
      color: "text-purple-600",
    },
  ];

  const chartData = domainBreakdown.map((d) => ({
    domain: d.domain,
    total: d.total,
  }));

  return (
    <div className="space-y-4 p-3 md:space-y-6 md:p-6" role="main" aria-label={t("kpi.cfoDashboard", "CFO Dashboard")}>
      <Helmet>
        <title>CFO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex flex-col items-start justify-between gap-2 md:flex-row md:items-center">
        <h1 className="text-xl font-bold md:text-2xl">{t("kpi.cfoDashboard", "CFO Dashboard")}</h1>
        {data.demo && <Badge variant="secondary">{t("kpi.demoData", "Demo Data")}</Badge>}
      </div>

      {isEmpty && (
        <div className="rounded-lg bg-muted px-4 py-6 text-center text-sm text-muted-foreground">
          {t("kpi.noActivity", "No agent activity yet. Once agents run tasks, KPIs will appear here.")}
        </div>
      )}

      {/* ── Row 1: Top Metric Cards ── */}
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

      {/* ── Row 2: Domain Breakdown Chart ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">{t("kpi.totalTasksPerDomain", "Total Tasks per Domain")}</CardTitle>
        </CardHeader>
        <CardContent>
          {chartData.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              No domain activity to display.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="domain" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="total" name="Total Tasks" fill="#6366f1" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* ── Row 3: Domain Breakdown Table ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">{t("kpi.domainBreakdown", "Domain Breakdown")}</CardTitle>
        </CardHeader>
        <CardContent>
          {domainBreakdown.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">
              No domains to display.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t("kpi.domain", "Domain")}</th>
                    <th className="pb-2 pr-4 text-right">{t("kpi.total", "Total Tasks")}</th>
                    <th className="pb-2 pr-4 text-right">{t("kpi.completed", "Completed")}</th>
                    <th className="pb-2 pr-4 text-right">{t("kpi.failed", "Failed")}</th>
                    <th className="pb-2 text-right">{t("kpi.avgConfidence", "Avg Confidence")}</th>
                  </tr>
                </thead>
                <tbody>
                  {domainBreakdown.map((row) => (
                    <tr key={row.domain} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-medium">{row.domain}</td>
                      <td className="py-2 pr-4 text-right">{row.total.toLocaleString()}</td>
                      <td className="py-2 pr-4 text-right text-green-600">
                        {row.completed.toLocaleString()}
                      </td>
                      <td className="py-2 pr-4 text-right text-red-600">
                        {row.failed.toLocaleString()}
                      </td>
                      <td className="py-2 text-right">
                        {(row.avg_confidence * 100).toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
