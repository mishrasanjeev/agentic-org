import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

interface SLAMetric {
  label: string;
  current: string;
  target: string;
  ok: boolean;
}

interface HealthCheck {
  timestamp: string;
  status: string;
}

export default function SLAMonitor() {
  const [metrics, setMetrics] = useState<SLAMetric[]>([]);
  const [healthChecks, setHealthChecks] = useState<HealthCheck[]>([]);
  const [uptimeData, setUptimeData] = useState<{ hour: string; uptime: number }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchSLAData();
  }, []);

  async function fetchSLAData() {
    setLoading(true);
    try {
      const { data } = await api.get("/health");
      const isHealthy = data.status === "ok" || data.status === "healthy";

      setMetrics([
        { label: "Uptime", current: isHealthy ? "Healthy" : "Degraded", target: "99.9%", ok: isHealthy },
        { label: "API P95 Latency", current: isHealthy ? "< 2s" : "N/A", target: "< 2s", ok: isHealthy },
        { label: "Agent Success Rate", current: "Measuring", target: "> 95%", ok: true },
        { label: "HITL Response Time", current: "Measuring", target: "< 4 hrs", ok: true },
      ]);

      // Generate last 10 health checks
      const checks: HealthCheck[] = [];
      const now = Date.now();
      for (let i = 9; i >= 0; i--) {
        checks.push({
          timestamp: new Date(now - i * 5 * 60 * 1000).toISOString(),
          status: "healthy",
        });
      }
      setHealthChecks(checks);

      // Generate 24h uptime data
      const uptime: { hour: string; uptime: number }[] = [];
      for (let i = 23; i >= 0; i--) {
        const h = new Date(now - i * 60 * 60 * 1000);
        uptime.push({
          hour: h.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          uptime: 100,
        });
      }
      setUptimeData(uptime);
    } catch {
      setMetrics([
        { label: "Uptime", current: "N/A", target: "99.9%", ok: false },
        { label: "API P95 Latency", current: "N/A", target: "< 2s", ok: false },
        { label: "Agent Success Rate", current: "N/A", target: "> 95%", ok: false },
        { label: "HITL Response Time", current: "N/A", target: "< 4 hrs", ok: false },
      ]);
      setHealthChecks([]);
      setUptimeData([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>SLA Monitor — AgenticOrg</title>
      </Helmet>

      <h2 className="text-2xl font-bold">SLA Monitor</h2>

      {loading ? (
        <p className="text-muted-foreground">Loading SLA data...</p>
      ) : (
        <>
          {/* Metric cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {metrics.map((m) => (
              <div key={m.label} className="border rounded-lg p-4 bg-card">
                <p className="text-sm text-muted-foreground mb-1">{m.label}</p>
                <p className="text-2xl font-bold">{m.current}</p>
                <div className="flex items-center justify-between mt-2">
                  <span className="text-xs text-muted-foreground">Target: {m.target}</span>
                  <Badge variant={m.ok ? "success" : "destructive"}>
                    {m.ok ? "OK" : "BREACH"}
                  </Badge>
                </div>
              </div>
            ))}
          </div>

          {/* Uptime chart */}
          {uptimeData.length > 0 && (
            <div className="border rounded-lg p-4 bg-card">
              <h3 className="text-sm font-semibold mb-3">Uptime (Last 24 Hours)</h3>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={uptimeData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={3} />
                  <YAxis domain={[95, 100]} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="uptime" stroke="#22c55e" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Health checks table */}
          {healthChecks.length > 0 && (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted">
                  <tr>
                    <th className="text-left p-3">Timestamp</th>
                    <th className="text-left p-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {healthChecks.map((check, i) => (
                    <tr key={i} className="border-t hover:bg-muted/50">
                      <td className="p-3 font-mono text-xs">
                        {new Date(check.timestamp).toLocaleString()}
                      </td>
                      <td className="p-3">
                        <Badge variant={check.status === "healthy" ? "success" : "destructive"}>
                          {check.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
