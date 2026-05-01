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
  const [errors, setErrors] = useState<string[]>([]);
  // 2026-04-30 enterprise gap fix: surface the backend's
  // ``data_source`` field so the page tells the truth about whether
  // the chart shows real history or just a single live snapshot.
  const [dataSourceNote, setDataSourceNote] = useState<string | null>(null);

  useEffect(() => {
    fetchSLAData();
  }, []);

  async function fetchSLAData() {
    setLoading(true);
    setErrors([]);
    setDataSourceNote(null);
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

      const fetchErrors: string[] = [];
      if (healthRes.status === "rejected") {
        const reason: any = healthRes.reason;
        const status = reason?.response?.status;
        fetchErrors.push(
          `/health unavailable${status ? ` (HTTP ${status})` : ""} — current platform status cannot be determined.`
        );
      }
      if (checksRes.status === "rejected") {
        const reason: any = checksRes.reason;
        const status = reason?.response?.status;
        fetchErrors.push(
          `/health/checks unavailable${status ? ` (HTTP ${status})` : ""} — check history shows live snapshot only.`
        );
      }
      if (uptimeRes.status === "rejected") {
        const reason: any = uptimeRes.reason;
        const status = reason?.response?.status;
        fetchErrors.push(
          `/health/uptime unavailable${status ? ` (HTTP ${status})` : ""} — uptime chart will be empty.`
        );
      }
      setErrors(fetchErrors);

      setMetrics([
        { label: "Uptime", current: isHealthy === null ? "N/A" : isHealthy ? "Healthy" : "Degraded", target: "99.9%", ok: isHealthy },
        { label: "API P95 Latency", current: healthData?.p95_latency || "N/A", target: "< 2s", ok: isHealthy },
        { label: "Agent Success Rate", current: healthData?.agent_success_rate || "N/A", target: "> 95%", ok: isHealthy },
        { label: "HITL Response Time", current: healthData?.hitl_response_time || "N/A", target: "< 4 hrs", ok: isHealthy },
      ]);

      // The /health/checks contract returns either a bare array (legacy)
      // or { data_source, note, items: [...] } (Phase 1 honest shape).
      if (checksRes.status === "fulfilled") {
        const body = checksRes.value.data;
        const rawChecks = Array.isArray(body)
          ? body
          : (body?.items || []);
        setHealthChecks(rawChecks);
        // Capture the backend's honest "this is a live snapshot, not
        // history" note so the user sees it explicitly.
        if (body && body.data_source === "live_snapshot" && body.note) {
          setDataSourceNote(body.note);
        }
      } else {
        // Fall back to the /health snapshot when /health/checks isn't
        // available — but mark it explicitly so the user knows.
        if (healthData) {
          setHealthChecks([{
            timestamp: new Date().toISOString(),
            status: isHealthy ? "healthy" : "unhealthy",
          }]);
        } else {
          setHealthChecks([]);
        }
      }

      if (uptimeRes.status === "fulfilled") {
        const body = uptimeRes.value.data;
        const rawUptime = Array.isArray(body) ? body : (body?.items || []);
        setUptimeData(rawUptime);
      } else {
        setUptimeData([]);
      }
    } catch (e: any) {
      setMetrics([
        { label: "Uptime", current: "N/A", target: "99.9%", ok: null },
        { label: "API P95 Latency", current: "N/A", target: "< 2s", ok: null },
        { label: "Agent Success Rate", current: "N/A", target: "> 95%", ok: null },
        { label: "HITL Response Time", current: "N/A", target: "< 4 hrs", ok: null },
      ]);
      setHealthChecks([]);
      setUptimeData([]);
      setErrors([`Failed to load SLA data: ${e?.message || "unknown error"}`]);
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

      {/* Backend's honest data-source note (Phase 1: live snapshot only,
          history persistence is a follow-up). Surface it so users
          don't mistake the chart's single point for a flatlined system. */}
      {dataSourceNote && (
        <div
          role="status"
          className="rounded border border-amber-500/40 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm flex items-start gap-3"
        >
          <span className="font-medium shrink-0">Note:</span>
          <span className="flex-1">{dataSourceNote}</span>
        </div>
      )}

      {/* Errors banners — surface partial outages so users can tell which
          metrics below are real vs. fallback. */}
      {errors.length > 0 && (
        <div role="alert" className="space-y-2">
          {errors.map((msg, i) => (
            <div
              key={i}
              className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm flex items-start gap-3"
            >
              <span className="font-medium text-destructive shrink-0">Warning:</span>
              <span className="flex-1">{msg}</span>
            </div>
          ))}
        </div>
      )}

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
