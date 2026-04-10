import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

interface SLAMetric {
  label: string;
  current: string;
  target: string;
  ok: boolean | null;
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
      const [healthRes, checksRes, uptimeRes] = await Promise.allSettled([
        api.get("/health"),
        api.get("/health/checks"),
        api.get("/health/uptime"),
      ]);

      // Parse health status
      const healthData = healthRes.status === "fulfilled" ? healthRes.value.data : null;
      const isHealthy = healthData
        ? healthData.status === "ok" || healthData.status === "healthy"
        : null;

      setMetrics([
        { label: "Uptime", current: isHealthy === null ? "N/A" : isHealthy ? "Healthy" : "Degraded", target: "99.9%", ok: isHealthy },
        { label: "API P95 Latency", current: healthData?.p95_latency || "N/A", target: "< 2s", ok: isHealthy },
        { label: "Agent Success Rate", current: healthData?.agent_success_rate || "N/A", target: "> 95%", ok: isHealthy },
        { label: "HITL Response Time", current: healthData?.hitl_response_time || "N/A", target: "< 4 hrs", ok: isHealthy },
      ]);

      // Parse health checks from API (if endpoint exists)
      if (checksRes.status === "fulfilled") {
        const rawChecks = Array.isArray(checksRes.value.data)
          ? checksRes.value.data
          : checksRes.value.data?.items || [];
        setHealthChecks(rawChecks);
      } else {
        // Single health check from the /health endpoint
        if (healthData) {
          setHealthChecks([{
            timestamp: new Date().toISOString(),
            status: isHealthy ? "healthy" : "unhealthy",
          }]);
        } else {
          setHealthChecks([]);
        }
      }

      // Parse uptime data from API (if endpoint exists)
      if (uptimeRes.status === "fulfilled") {
        const rawUptime = Array.isArray(uptimeRes.value.data)
          ? uptimeRes.value.data
          : uptimeRes.value.data?.items || [];
        setUptimeData(rawUptime);
      } else {
        setUptimeData([]);
      }
    } catch {
      setMetrics([
        { label: "Uptime", current: "N/A", target: "99.9%", ok: null },
        { label: "API P95 Latency", current: "N/A", target: "< 2s", ok: null },
        { label: "Agent Success Rate", current: "N/A", target: "> 95%", ok: null },
        { label: "HITL Response Time", current: "N/A", target: "< 4 hrs", ok: null },
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
      ) : metrics.length === 0 || metrics.every((m) => m.ok === null) ? (
        <p className="text-muted-foreground">No monitoring data yet.</p>
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
                  <Badge variant={m.ok === null ? "secondary" : m.ok ? "success" : "destructive"}>
                    {m.ok === null ? "N/A" : m.ok ? "OK" : "BREACH"}
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
