import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
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
  LineChart,
  Line,
  Legend,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgingBuckets {
  "0_30": number;
  "31_60": number;
  "61_90": number;
  "90_plus": number;
}

interface MonthlyPL {
  month: string;
  revenue: number;
  cogs: number;
  gross_margin: number;
  opex: number;
  net_income: number;
}

interface BankBalance {
  account: string;
  balance: number;
  currency: string;
}

interface TaxFiling {
  filing: string;
  due_date: string;
  status: string;
}

interface CFOKPIData {
  demo: boolean;
  company_id: string;
  cash_runway_months: number;
  cash_runway_trend: number;
  burn_rate: number;
  burn_rate_trend: number;
  dso_days: number;
  dso_trend: number;
  dpo_days: number;
  dpo_trend: number;
  ar_aging: AgingBuckets;
  ap_aging: AgingBuckets;
  monthly_pl: MonthlyPL[];
  bank_balances: BankBalance[];
  pending_approvals_count: number;
  tax_calendar: TaxFiling[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function formatCurrency(value: number, currency: string): string {
  return currency === "USD" ? USD.format(value) : INR.format(value);
}

function lakhs(value: number): string {
  return (value / 100_000).toFixed(1) + "L";
}

function TrendIndicator({ value }: { value: number }) {
  const isPositive = value >= 0;
  return (
    <span
      className={`text-xs font-medium ${isPositive ? "text-green-600" : "text-red-600"}`}
    >
      {isPositive ? "\u2191" : "\u2193"} {Math.abs(value).toFixed(1)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Aging chart data builder
// ---------------------------------------------------------------------------

function agingChartData(aging: AgingBuckets) {
  return [
    { bucket: "0-30d", amount: aging["0_30"] / 100_000 },
    { bucket: "31-60d", amount: aging["31_60"] / 100_000 },
    { bucket: "61-90d", amount: aging["61_90"] / 100_000 },
    { bucket: "90+d", amount: aging["90_plus"] / 100_000 },
  ];
}

const AGING_COLORS = ["#3b82f6", "#f59e0b", "#f97316", "#ef4444"];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CFODashboard() {
  const [data, setData] = useState<CFOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const [kpiResp] = await Promise.allSettled([api.get("/kpis/cfo")]);
      if (kpiResp.status === "fulfilled") {
        setData(kpiResp.value.data);
      } else {
        setError("Failed to load CFO KPIs");
      }
    } catch {
      setError("Failed to load CFO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CFO Dashboard</h2>
        <p className="text-muted-foreground">Loading finance data...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CFO Dashboard</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  // Prepare P&L table MoM changes
  const plRows = data.monthly_pl.map((row, idx) => {
    const prev = idx > 0 ? data.monthly_pl[idx - 1] : null;
    function mom(curr: number, prevVal: number | undefined): number | null {
      if (prevVal == null || prevVal === 0) return null;
      return ((curr - prevVal) / prevVal) * 100;
    }
    return {
      ...row,
      revenue_mom: prev ? mom(row.revenue, prev.revenue) : null,
      cogs_mom: prev ? mom(row.cogs, prev.cogs) : null,
      gross_margin_mom: prev ? mom(row.gross_margin, prev.gross_margin) : null,
      opex_mom: prev ? mom(row.opex, prev.opex) : null,
      net_income_mom: prev ? mom(row.net_income, prev.net_income) : null,
    };
  });

  // Sparkline data for the P&L line chart
  const plChartData = data.monthly_pl.map((r) => ({
    month: r.month.slice(5),
    Revenue: r.revenue / 100_000,
    COGS: r.cogs / 100_000,
    "Net Income": r.net_income / 100_000,
  }));

  const topMetrics = [
    {
      label: "Cash Runway",
      value: `${data.cash_runway_months} mo`,
      trend: data.cash_runway_trend,
      color: "text-blue-600",
    },
    {
      label: "Monthly Burn Rate",
      value: INR.format(data.burn_rate),
      trend: data.burn_rate_trend,
      color: "text-orange-600",
    },
    {
      label: "DSO (Days)",
      value: `${data.dso_days}d`,
      trend: data.dso_trend,
      color: "text-emerald-600",
    },
    {
      label: "DPO (Days)",
      value: `${data.dpo_days}d`,
      trend: data.dpo_trend,
      color: "text-purple-600",
    },
  ];

  const statusBadge: Record<string, "warning" | "default" | "success"> = {
    pending: "warning",
    upcoming: "default",
    filed: "success",
  };

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CFO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">CFO Dashboard</h2>
        {data.demo && (
          <Badge variant="secondary">Demo Data</Badge>
        )}
      </div>

      {/* ── Row 1: Top Metric Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {topMetrics.map((m) => (
          <Card key={m.label}>
            <CardHeader>
              <CardTitle className="text-sm text-muted-foreground">{m.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
              <TrendIndicator value={m.trend} />
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── Row 2: AR & AP Aging Charts ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">
              Accounts Receivable Aging (INR Lakhs)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={agingChartData(data.ar_aging)}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="bucket" tick={{ fontSize: 12 }} />
                <YAxis
                  tick={{ fontSize: 12 }}
                  tickFormatter={(v: number) => `${v}L`}
                />
                <Tooltip formatter={(v: number) => `${v.toFixed(1)}L`} />
                <Bar dataKey="amount" name="AR Amount">
                  {agingChartData(data.ar_aging).map((_, idx) => (
                    <rect key={idx} fill={AGING_COLORS[idx]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">
              Accounts Payable Aging (INR Lakhs)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={agingChartData(data.ap_aging)}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="bucket" tick={{ fontSize: 12 }} />
                <YAxis
                  tick={{ fontSize: 12 }}
                  tickFormatter={(v: number) => `${v}L`}
                />
                <Tooltip formatter={(v: number) => `${v.toFixed(1)}L`} />
                <Bar dataKey="amount" name="AP Amount" fill="#6366f1" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* ── Row 3: Monthly P&L ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* P&L Table */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Monthly P&L Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">Month</th>
                    <th className="pb-2 pr-4 text-right">Revenue</th>
                    <th className="pb-2 pr-4 text-right">COGS</th>
                    <th className="pb-2 pr-4 text-right">Gross Margin</th>
                    <th className="pb-2 pr-4 text-right">OPEX</th>
                    <th className="pb-2 text-right">Net Income</th>
                  </tr>
                </thead>
                <tbody>
                  {plRows.map((row) => (
                    <tr key={row.month} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-medium">{row.month}</td>
                      <td className="py-2 pr-4 text-right">
                        {lakhs(row.revenue)}
                        {row.revenue_mom != null && (
                          <span className={`ml-1 text-xs ${row.revenue_mom >= 0 ? "text-green-600" : "text-red-600"}`}>
                            {row.revenue_mom >= 0 ? "+" : ""}{row.revenue_mom.toFixed(1)}%
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right">
                        {lakhs(row.cogs)}
                        {row.cogs_mom != null && (
                          <span className={`ml-1 text-xs ${row.cogs_mom <= 0 ? "text-green-600" : "text-red-600"}`}>
                            {row.cogs_mom >= 0 ? "+" : ""}{row.cogs_mom.toFixed(1)}%
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right">
                        {lakhs(row.gross_margin)}
                        {row.gross_margin_mom != null && (
                          <span className={`ml-1 text-xs ${row.gross_margin_mom >= 0 ? "text-green-600" : "text-red-600"}`}>
                            {row.gross_margin_mom >= 0 ? "+" : ""}{row.gross_margin_mom.toFixed(1)}%
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right">
                        {lakhs(row.opex)}
                        {row.opex_mom != null && (
                          <span className={`ml-1 text-xs ${row.opex_mom <= 0 ? "text-green-600" : "text-red-600"}`}>
                            {row.opex_mom >= 0 ? "+" : ""}{row.opex_mom.toFixed(1)}%
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-right font-semibold">
                        {lakhs(row.net_income)}
                        {row.net_income_mom != null && (
                          <span className={`ml-1 text-xs ${row.net_income_mom >= 0 ? "text-green-600" : "text-red-600"}`}>
                            {row.net_income_mom >= 0 ? "+" : ""}{row.net_income_mom.toFixed(1)}%
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* P&L Trend mini chart */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">P&L Trend (Lakhs)</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={plChartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v}L`} />
                <Tooltip formatter={(v: number) => `${v.toFixed(1)}L`} />
                <Legend />
                <Line type="monotone" dataKey="Revenue" stroke="#3b82f6" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="COGS" stroke="#f59e0b" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="Net Income" stroke="#22c55e" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* ── Row 4: Bank Balances + Pending Approvals ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Bank Balances</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {data.bank_balances.map((b) => (
                <div
                  key={b.account}
                  className="flex items-center justify-between p-3 rounded bg-muted"
                >
                  <div>
                    <p className="text-sm font-medium">{b.account}</p>
                    <p className="text-xs text-muted-foreground">{b.currency}</p>
                  </div>
                  <p className="text-lg font-bold">
                    {formatCurrency(b.balance, b.currency)}
                  </p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Pending Finance Approvals</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-center justify-center py-8">
            <p className="text-5xl font-bold text-orange-600">
              {data.pending_approvals_count}
            </p>
            <p className="text-sm text-muted-foreground mt-2">items awaiting review</p>
          </CardContent>
        </Card>
      </div>

      {/* ── Row 5: Tax Calendar ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Tax & Compliance Calendar</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {data.tax_calendar.map((t) => (
              <div
                key={t.filing}
                className="flex items-center justify-between p-3 rounded border"
              >
                <div>
                  <p className="text-sm font-medium">{t.filing}</p>
                  <p className="text-xs text-muted-foreground">
                    Due: {new Date(t.due_date).toLocaleDateString("en-IN", {
                      day: "numeric",
                      month: "short",
                      year: "numeric",
                    })}
                  </p>
                </div>
                <Badge variant={statusBadge[t.status] || "default"}>
                  {t.status}
                </Badge>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
