import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

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

interface CEOKPIData {
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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CEODashboard() {
  const [data, setData] = useState<CEOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchData() {
      setLoading(true);
      setError(null);
      try {
        const resp = await api.get("/kpis/ceo");
        setData(resp.data);
      } catch {
        setError("Failed to load CEO KPIs");
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

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

  const successColor =
    data.success_rate >= 0.9
      ? "text-green-600"
      : data.success_rate >= 0.7
        ? "text-yellow-600"
        : "text-red-600";

  const topMetrics = [
    { label: "Agents", value: formatNumber(data.agent_count), color: "text-blue-600" },
    { label: "Total Tasks (30d)", value: formatNumber(data.total_tasks_30d), color: "text-emerald-600" },
    { label: "Success Rate", value: `${(data.success_rate * 100).toFixed(1)}%`, color: successColor },
    { label: "HITL Interventions", value: formatNumber(data.hitl_interventions), color: "text-orange-600" },
    { label: "Total Cost", value: USD.format(data.total_cost_usd), color: "text-purple-600" },
  ];

  const domains = data.domain_breakdown || [];
  const maxTotal = Math.max(1, ...domains.map((d) => d.total));
  const hasActivity = data.total_tasks_30d > 0 || domains.length > 0;

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CEO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">CEO Dashboard</h2>
        {data.demo && <Badge variant="secondary">Demo Data</Badge>}
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

      {!hasActivity ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No agent activity yet. Once your agents start handling tasks, their
            performance will appear here.
          </CardContent>
        </Card>
      ) : (
        <>
          {/* ── Domain Breakdown Bar Chart ── */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Domain Activity</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {domains.map((d) => (
                  <div key={d.domain}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="font-medium capitalize">{d.domain}</span>
                      <span className="text-muted-foreground">{d.total} tasks</span>
                    </div>
                    <div className="h-2 w-full rounded bg-muted overflow-hidden">
                      <div
                        className="h-full bg-blue-500"
                        style={{ width: `${(d.total / maxTotal) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* ── Domain Breakdown Table ── */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Domain Breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">Domain</th>
                      <th className="pb-2 pr-4 text-right">Total</th>
                      <th className="pb-2 pr-4 text-right">Completed</th>
                      <th className="pb-2 pr-4 text-right">Failed</th>
                      <th className="pb-2 text-right">Avg Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {domains.map((d) => (
                      <tr key={d.domain} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium capitalize">{d.domain}</td>
                        <td className="py-2 pr-4 text-right">{formatNumber(d.total)}</td>
                        <td className="py-2 pr-4 text-right text-green-600">{formatNumber(d.completed)}</td>
                        <td className="py-2 pr-4 text-right text-red-600">{formatNumber(d.failed)}</td>
                        <td className="py-2 text-right">{(d.avg_confidence * 100).toFixed(1)}%</td>
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
