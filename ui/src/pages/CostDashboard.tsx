import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import api from "@/lib/api";

interface CostSummary {
  tenant_id: string;
  period: string;
  start: string;
  end: string;
  total_usd: number;
  task_count: number;
  by_domain: Record<string, number>;
  by_agent: Record<string, number>;
}

interface CostPoint {
  date: string;
  cost_usd: number;
  tasks: number;
}

interface AgentCostRow {
  agent_id: string;
  agent_type: string;
  domain: string;
  total_usd: number;
  task_count: number;
}

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export default function CostDashboard() {
  const { t } = useTranslation();
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("monthly");
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [trend, setTrend] = useState<CostPoint[]>([]);
  const [topAgents, setTopAgents] = useState<AgentCostRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      api.get<CostSummary>(`/costs/summary?period=${period}`),
      api.get<CostPoint[]>(`/costs/trend?days=30`),
      api.get<AgentCostRow[]>(`/costs/top-agents?days=30&limit=10`),
    ])
      .then(([s, tr, ta]) => {
        if (cancelled) return;
        setSummary(s.data);
        setTrend(tr.data);
        setTopAgents(ta.data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e?.message || e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  const domainData = summary
    ? Object.entries(summary.by_domain).map(([domain, total]) => ({
        domain,
        total,
      }))
    : [];

  return (
    <div className="space-y-4 p-4 md:space-y-6 md:p-6">
      <Helmet>
        <title>{t("cost.title", "Cost Dashboard")} — AgenticOrg</title>
      </Helmet>

      <div className="flex flex-col items-start justify-between gap-2 md:flex-row md:items-center">
        <h1 className="text-2xl font-semibold">
          {t("cost.title", "Cost Dashboard")}
        </h1>
        <div className="flex gap-2">
          {(["daily", "weekly", "monthly"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              aria-pressed={period === p}
              className={`rounded-md px-3 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-primary ${
                period === p
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted hover:bg-muted/80"
              }`}
            >
              {t(`cost.period.${p}`, p)}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div
          className="rounded border border-destructive bg-destructive/10 p-3 text-sm text-destructive"
          role="alert"
        >
          {error}
        </div>
      )}

      {loading && !summary && (
        <div className="text-muted-foreground">{t("common.loading", "Loading…")}</div>
      )}

      {summary && (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>{t("cost.totalSpend", "Total spend")}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-bold">
                  {USD.format(summary.total_usd)}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t("cost.periodLabel", "this {{period}}", { period })}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>{t("cost.taskCount", "Tasks executed")}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-bold">{summary.task_count}</div>
                <div className="text-xs text-muted-foreground">
                  {t("cost.avgCost", "avg")} {" "}
                  {summary.task_count > 0
                    ? USD.format(summary.total_usd / summary.task_count)
                    : USD.format(0)}{" "}
                  {t("cost.perTask", "per task")}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>{t("cost.domains", "Domains")}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {domainData.map((d) => (
                    <Badge key={d.domain} variant="secondary">
                      {d.domain}: {USD.format(d.total)}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{t("cost.trend", "30-day cost trend")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={trend}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" />
                  <YAxis />
                  <Tooltip formatter={(v: number) => USD.format(v)} />
                  <Line
                    type="monotone"
                    dataKey="cost_usd"
                    stroke="#8884d8"
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t("cost.byDomain", "Spend by domain")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={domainData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="domain" />
                    <YAxis />
                    <Tooltip formatter={(v: number) => USD.format(v)} />
                    <Bar dataKey="total" fill="#82ca9d" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>{t("cost.topAgents", "Top agents (30d)")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2" aria-label={t("cost.topAgents", "Top agents")}>
                  {topAgents.map((a) => (
                    <li
                      key={a.agent_id}
                      className="flex items-center justify-between text-sm"
                    >
                      <div>
                        <div className="font-medium">{a.agent_type}</div>
                        <div className="text-xs text-muted-foreground">
                          {a.domain} · {a.task_count} tasks
                        </div>
                      </div>
                      <div className="font-semibold">{USD.format(a.total_usd)}</div>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
