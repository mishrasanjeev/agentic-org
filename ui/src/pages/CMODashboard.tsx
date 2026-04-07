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
  PieChart,
  Pie,
  Cell,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ROASByChannel {
  [channel: string]: number;
}

interface EmailPerformance {
  open_rate: number;
  click_rate: number;
  unsubscribe_rate: number;
}

interface SocialEngagement {
  [platform: string]: number;
}

interface SessionTrend {
  date: string;
  sessions: number;
}

interface WebsiteTraffic {
  sessions: number;
  users: number;
  bounce_rate: number;
  sessions_trend: SessionTrend[];
}

interface TopPage {
  page: string;
  views: number;
  avg_time_sec: number;
}

interface CMOKPIData {
  demo: boolean;
  company_id: string;
  cac: number;
  cac_trend: number;
  mqls: number;
  mqls_trend: number;
  sqls: number;
  sqls_trend: number;
  pipeline_value: number;
  pipeline_trend: number;
  roas_by_channel: ROASByChannel;
  email_performance: EmailPerformance;
  social_engagement: SocialEngagement;
  website_traffic: WebsiteTraffic;
  content_top_pages: TopPage[];
  brand_sentiment_score: number;
  brand_sentiment_trend: number;
  pending_content_approvals: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

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

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

const CHANNEL_COLORS: Record<string, string> = {
  "Google Ads": "#4285F4",
  "Meta Ads": "#1877F2",
  LinkedIn: "#0A66C2",
  Organic: "#22c55e",
};

const SOCIAL_COLORS: Record<string, string> = {
  Twitter: "#1DA1F2",
  LinkedIn: "#0A66C2",
  Instagram: "#E4405F",
};

// Sentiment gauge: 0-100 scale rendered as a donut
function SentimentGauge({ score, trend }: { score: number; trend: number }) {
  const gaugeData = [
    { name: "Score", value: score },
    { name: "Remaining", value: 100 - score },
  ];
  const color =
    score >= 70 ? "#22c55e" : score >= 50 ? "#f59e0b" : "#ef4444";

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
        <TrendIndicator value={trend} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CMODashboard() {
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
      const [kpiResp] = await Promise.allSettled([api.get("/kpis/cmo")]);
      if (kpiResp.status === "fulfilled") {
        setData(kpiResp.value.data);
      } else {
        setError("Failed to load CMO KPIs");
      }
    } catch {
      setError("Failed to load CMO KPIs");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CMO Dashboard</h2>
        <p className="text-muted-foreground">Loading marketing data...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">CMO Dashboard</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  // Prepare chart data
  const roasData = Object.entries(data.roas_by_channel).map(([channel, roas]) => ({
    channel,
    roas,
  }));

  const emailData = [
    { metric: "Open Rate", value: data.email_performance.open_rate },
    { metric: "Click Rate", value: data.email_performance.click_rate },
    { metric: "Unsub Rate", value: data.email_performance.unsubscribe_rate },
  ];

  const socialData = Object.entries(data.social_engagement).map(
    ([platform, engagements]) => ({
      platform,
      engagements,
    })
  );

  const topMetrics = [
    {
      label: "Customer Acquisition Cost",
      value: INR.format(data.cac),
      trend: data.cac_trend,
      color: "text-blue-600",
      trendInverted: true, // lower CAC is better
    },
    {
      label: "MQLs This Month",
      value: formatNumber(data.mqls),
      trend: data.mqls_trend,
      color: "text-emerald-600",
      trendInverted: false,
    },
    {
      label: "SQLs This Month",
      value: formatNumber(data.sqls),
      trend: data.sqls_trend,
      color: "text-purple-600",
      trendInverted: false,
    },
    {
      label: "Pipeline Value",
      value: INR.format(data.pipeline_value),
      trend: data.pipeline_trend,
      color: "text-orange-600",
      trendInverted: false,
    },
  ];

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CMO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">CMO Dashboard</h2>
        {data.demo && <Badge variant="secondary">Demo Data</Badge>}
      </div>

      {/* ── Row 1: Top Metric Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {topMetrics.map((m) => (
          <Card key={m.label}>
            <CardHeader>
              <CardTitle className="text-sm text-muted-foreground">
                {m.label}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
              <TrendIndicator value={m.trend} />
              {m.trendInverted && m.trend < 0 && (
                <span className="text-xs text-muted-foreground ml-1">(good)</span>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── Row 2: ROAS by Channel ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">
            Return on Ad Spend (ROAS) by Channel
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={roasData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="channel" tick={{ fontSize: 12 }} />
              <YAxis
                tick={{ fontSize: 12 }}
                tickFormatter={(v: number) => `${v}x`}
              />
              <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}x`} />
              <Bar dataKey="roas" name="ROAS">
                {roasData.map((entry) => (
                  <Cell
                    key={entry.channel}
                    fill={CHANNEL_COLORS[entry.channel] || "#94a3b8"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* ── Row 3: Email Performance + Social Engagement ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Email Performance (%)</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={emailData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="metric" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={(v: number) => `${v}%`} />
                <Tooltip formatter={(v: any) => `${v}%`} />
                <Bar dataKey="value" name="Rate" fill="#8b5cf6" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">
              Social Engagement by Platform
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={socialData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="platform" tick={{ fontSize: 12 }} />
                <YAxis
                  tick={{ fontSize: 12 }}
                  tickFormatter={(v: number) =>
                    v >= 1000 ? `${(v / 1000).toFixed(1)}K` : `${v}`
                  }
                />
                <Tooltip
                  formatter={(v: any) => formatNumber(v)}
                />
                <Bar dataKey="engagements" name="Engagements">
                  {socialData.map((entry) => (
                    <Cell
                      key={entry.platform}
                      fill={SOCIAL_COLORS[entry.platform] || "#94a3b8"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* ── Row 4: Website Traffic + Top Content ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Website Traffic</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex gap-6 mb-4">
              <div>
                <p className="text-xs text-muted-foreground">Sessions</p>
                <p className="text-lg font-bold">{formatNumber(data.website_traffic.sessions)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Users</p>
                <p className="text-lg font-bold">{formatNumber(data.website_traffic.users)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Bounce Rate</p>
                <p className="text-lg font-bold">{data.website_traffic.bounce_rate}%</p>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={data.website_traffic.sessions_trend}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10 }}
                  tickFormatter={(d: string) => d.slice(5)}
                />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="sessions"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={false}
                  name="Sessions"
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Top Content Pages</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">Page</th>
                    <th className="pb-2 pr-4 text-right">Views</th>
                    <th className="pb-2 text-right">Avg Time</th>
                  </tr>
                </thead>
                <tbody>
                  {data.content_top_pages.map((p) => (
                    <tr key={p.page} className="border-b last:border-0">
                      <td className="py-2 pr-4 max-w-[200px] truncate" title={p.page}>
                        {p.page}
                      </td>
                      <td className="py-2 pr-4 text-right font-medium">
                        {formatNumber(p.views)}
                      </td>
                      <td className="py-2 text-right text-muted-foreground">
                        {Math.floor(p.avg_time_sec / 60)}m {p.avg_time_sec % 60}s
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Row 5: Brand Sentiment + Pending Content Approvals ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Brand Sentiment Score</CardTitle>
          </CardHeader>
          <CardContent>
            <SentimentGauge
              score={data.brand_sentiment_score}
              trend={data.brand_sentiment_trend}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">
              Pending Content Approvals
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-center justify-center py-8">
            <p className="text-5xl font-bold text-orange-600">
              {data.pending_content_approvals}
            </p>
            <p className="text-sm text-muted-foreground mt-2">
              blog posts, social posts & campaigns awaiting review
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
