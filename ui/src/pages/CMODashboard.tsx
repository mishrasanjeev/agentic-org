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

interface CMOKPIData {
  demo: boolean;
  company_id: string;
  agent_count: number;
  total_tasks_30d: number;
  success_rate: number;
  hitl_interventions: number;
  total_cost_usd: number;
  domain_breakdown: DomainBreakdown[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

const MARKETING_DOMAINS = new Set([
  "marketing",
  "content",
  "social",
  "email",
  "seo",
  "ads",
  "brand",
  "campaign",
]);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CMODashboard() {
  const { t } = useTranslation();
  const [data, setData] = useState<CMOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.get("/kpis/cmo");
      setData(resp.data);
    } catch {
      setError("Failed to load CMO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">{t("kpi.cmoDashboard", "CMO Dashboard")}</h2>
        <p className="text-muted-foreground">{t("kpi.loading", "Loading...")}</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">{t("kpi.cmoDashboard", "CMO Dashboard")}</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  const domains = data.domain_breakdown ?? [];
  const marketingDomains = domains.filter((d) =>
    MARKETING_DOMAINS.has(d.domain.toLowerCase())
  );
  const displayDomains = marketingDomains.length > 0 ? marketingDomains : domains;

  const kpiCards = [
    { label: t("kpi.agents", "Agents"), value: formatNumber(data.agent_count ?? 0), color: "text-blue-600" },
    { label: t("kpi.totalTasks", "Total Tasks (30d)"), value: formatNumber(data.total_tasks_30d ?? 0), color: "text-emerald-600" },
    { label: t("kpi.successRate", "Success Rate"), value: `${(data.success_rate ?? 0).toFixed(1)}%`, color: "text-purple-600" },
    { label: t("kpi.hitlInterventions", "HITL Interventions"), value: formatNumber(data.hitl_interventions ?? 0), color: "text-orange-600" },
    { label: t("kpi.totalCost", "Total Cost (USD)"), value: USD.format(data.total_cost_usd ?? 0), color: "text-rose-600" },
  ];

  return (
    <div className="space-y-4 p-3 md:space-y-6 md:p-6" role="main" aria-label={t("kpi.cmoDashboard", "CMO Dashboard")}>
      <Helmet>
        <title>CMO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex flex-col items-start justify-between gap-2 md:flex-row md:items-center">
        <h1 className="text-xl font-bold md:text-2xl">{t("kpi.cmoDashboard", "CMO Dashboard")}</h1>
        {data.demo && <Badge variant="secondary">{t("kpi.demoData", "Demo Data")}</Badge>}
      </div>

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        {kpiCards.map((m) => (
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

      {/* ── Domain Breakdown ── */}
      {displayDomains.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            {t("kpi.noActivity", "No agent activity yet. Once agents run tasks, KPIs will appear here.")}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">{t("kpi.domainBreakdown", "Domain Breakdown")}</CardTitle>
            </CardHeader>
            <CardContent>
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
                    {displayDomains.map((d) => (
                      <tr key={d.domain} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{d.domain}</td>
                        <td className="py-2 pr-4 text-right">{formatNumber(d.total)}</td>
                        <td className="py-2 pr-4 text-right text-green-600">
                          {formatNumber(d.completed)}
                        </td>
                        <td className="py-2 pr-4 text-right text-red-600">
                          {formatNumber(d.failed)}
                        </td>
                        <td className="py-2 text-right text-muted-foreground">
                          {(d.avg_confidence * 100).toFixed(0)}%
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
              <CardTitle className="text-sm font-semibold">{t("kpi.totalTasksPerDomain", "Total Tasks per Domain")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={displayDomains}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="domain" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="completed" name="Completed" fill="#22c55e" stackId="a" />
                  <Bar dataKey="failed" name="Failed" fill="#ef4444" stackId="a" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
