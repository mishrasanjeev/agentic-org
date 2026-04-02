import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import type { Agent, HITLItem, AuditEntry } from "@/types";
import {
  PieChart, Pie, Cell,
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";

const DOMAIN_COLORS: Record<string, string> = {
  finance: "#3b82f6",
  hr: "#8b5cf6",
  marketing: "#f59e0b",
  ops: "#10b981",
  backoffice: "#6366f1",
  comms: "#ec4899",
};

const STATUS_COLORS: Record<string, string> = {
  active: "#22c55e",
  shadow: "#f59e0b",
  paused: "#ef4444",
};

const PRIORITY_VARIANT: Record<string, "destructive" | "warning" | "default"> = {
  critical: "destructive",
  high: "warning",
  normal: "default",
};

export default function Dashboard() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [approvals, setApprovals] = useState<HITLItem[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchWarnings, setFetchWarnings] = useState<string[]>([]);

  useEffect(() => {
    fetchAll();
  }, []);

  async function fetchAll() {
    setLoading(true);
    const warnings: string[] = [];
    try {
      const [agentsResp, approvalsResp, auditResp] = await Promise.allSettled([
        api.get("/agents"),
        api.get("/approvals"),
        api.get("/audit", { params: { limit: 10 } }),
      ]);

      if (agentsResp.status === "fulfilled") {
        const d = agentsResp.value.data;
        setAgents(Array.isArray(d) ? d : Array.isArray(d?.items) ? d.items : []);
      } else {
        warnings.push("Agents data could not be loaded");
      }
      if (approvalsResp.status === "fulfilled") {
        const d = approvalsResp.value.data;
        setApprovals(Array.isArray(d) ? d : Array.isArray(d?.items) ? d.items : []);
      } else {
        warnings.push("Approvals data could not be loaded");
      }
      if (auditResp.status === "fulfilled") {
        const d = auditResp.value.data;
        setAuditEntries(Array.isArray(d) ? d : Array.isArray(d?.items) ? d.items : []);
      } else {
        warnings.push("Audit data could not be loaded");
      }
    } catch {
      warnings.push("Dashboard data could not be loaded");
    } finally {
      setFetchWarnings(warnings);
      setLoading(false);
    }
  }

  // Computed metrics
  const totalAgents = agents.length;
  const activeAgents = agents.filter((a) => a.status === "active").length;
  const shadowAgents = agents.filter((a) => a.status === "shadow").length;
  const pendingApprovals = approvals.filter((a) => a.status === "pending").length;

  // Status distribution for pie chart
  const statusCounts: Record<string, number> = {};
  agents.forEach((a) => {
    statusCounts[a.status] = (statusCounts[a.status] || 0) + 1;
  });
  const statusData = Object.entries(statusCounts).map(([name, value]) => ({ name, value }));

  // Domain distribution for bar chart
  const domainCounts: Record<string, number> = {};
  agents.forEach((a) => {
    const d = a.domain || "other";
    domainCounts[d] = (domainCounts[d] || 0) + 1;
  });
  const domainData = Object.entries(domainCounts).map(([name, count]) => ({ name, count }));

  // Confidence floor per agent
  const confidenceData = agents.map((a) => ({
    name: a.name.length > 12 ? a.name.slice(0, 12) + "..." : a.name,
    confidence: a.confidence_floor != null ? +(a.confidence_floor * 100).toFixed(0) : 0,
    domain: a.domain || "other",
  }));

  // Pending approval items
  const pendingItems = approvals.filter((a) => a.status === "pending");

  const metrics = [
    { label: "Total Agents", value: totalAgents, color: "text-foreground" },
    { label: "Active Agents", value: activeAgents, color: "text-green-600" },
    { label: "Pending Approvals", value: pendingApprovals, color: "text-red-600" },
    { label: "Shadow Agents", value: shadowAgents, color: "text-yellow-600" },
  ];

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">Dashboard</h2>
        <p className="text-muted-foreground">Loading dashboard data...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>Dashboard — AgenticOrg</title>
      </Helmet>
      <h2 className="text-2xl font-bold">Dashboard</h2>

      {fetchWarnings.length > 0 && (
        <div className="rounded-lg bg-yellow-50 border border-yellow-200 px-4 py-3 text-sm text-yellow-800">
          <p className="font-medium">Some data may be incomplete:</p>
          <ul className="list-disc list-inside mt-1">
            {fetchWarnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      {/* Top metrics row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {metrics.map((m) => (
          <Card key={m.label}>
            <CardHeader>
              <CardTitle className="text-sm text-muted-foreground">{m.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className={`text-3xl font-bold ${m.color}`}>{m.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* v3.0 Integration Status */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Agent Runtime</CardTitle></CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
              <span className="text-lg font-bold">LangGraph</span>
              <span className="text-xs text-muted-foreground">v1.1</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">35 agents, 54 connectors, 340+ tools</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Grantex Authorization</CardTitle></CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
              <span className="text-lg font-bold">Connected</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">Agent DIDs auto-assigned, RS256 grant tokens</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">External Access</CardTitle></CardHeader>
          <CardContent>
            <div className="flex gap-2">
              <span className="text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded font-medium">A2A Protocol</span>
              <span className="text-xs bg-purple-100 text-purple-800 px-2 py-1 rounded font-medium">MCP Server</span>
            </div>
            <a href="/dashboard/integrations" className="text-xs text-primary hover:underline mt-2 block">View integration details</a>
          </CardContent>
        </Card>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Agent Status Pie Chart */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Agent Status Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            {statusData.length === 0 ? (
              <p className="text-sm text-muted-foreground">No agents found.</p>
            ) : (
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={statusData}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={90}
                    paddingAngle={3}
                    dataKey="value"
                    label={({ name, value }) => `${name} (${value})`}
                  >
                    {statusData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={STATUS_COLORS[entry.name] || "#94a3b8"}
                      />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        {/* Domain Distribution Bar Chart */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Domain Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            {domainData.length === 0 ? (
              <p className="text-sm text-muted-foreground">No agents found.</p>
            ) : (
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={domainData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count">
                    {domainData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={DOMAIN_COLORS[entry.name] || "#94a3b8"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Confidence Floor Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Agent Confidence Floors (%)</CardTitle>
        </CardHeader>
        <CardContent>
          {confidenceData.length === 0 ? (
            <p className="text-sm text-muted-foreground">No agents found.</p>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={confidenceData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" height={60} />
                <YAxis domain={[0, 100]} />
                <Tooltip formatter={(value: number) => `${value}%`} />
                <Bar dataKey="confidence">
                  {confidenceData.map((entry, idx) => (
                    <Cell
                      key={idx}
                      fill={DOMAIN_COLORS[entry.domain] || "#94a3b8"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Bottom row: Activity Feed + Pending Approvals */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Recent Activity Feed */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Recent Activity</CardTitle>
          </CardHeader>
          <CardContent>
            {auditEntries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No recent activity.</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {auditEntries.slice(0, 10).map((entry) => {
                  const outcomeConfig = entry.outcome === "success"
                    ? { dot: "bg-green-500", variant: "success" as const, label: "Success" }
                    : entry.outcome === "failure" || entry.outcome === "error"
                    ? { dot: "bg-red-500", variant: "destructive" as const, label: "Failed" }
                    : { dot: "bg-yellow-500", variant: "warning" as const, label: entry.outcome || "Pending" };
                  return (
                    <div
                      key={entry.id}
                      className="flex items-center justify-between p-2 rounded bg-muted text-sm"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${outcomeConfig.dot}`} title={outcomeConfig.label} />
                        <Badge variant={outcomeConfig.variant}>
                          {outcomeConfig.label}
                        </Badge>
                        <span className="truncate">
                          <span className="font-medium">{entry.event_type}</span>
                          {" "}
                          <span className="text-muted-foreground">{entry.action}</span>
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground whitespace-nowrap ml-2">
                        {new Date(entry.created_at).toLocaleString()}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Pending Approvals Summary */}
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle className="text-sm font-semibold">Pending Approvals</CardTitle>
              {pendingItems.length > 0 && (
                <Badge variant="destructive">{pendingItems.length} pending</Badge>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {pendingItems.length === 0 ? (
              <p className="text-sm text-muted-foreground">No pending approvals.</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {pendingItems.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center justify-between p-3 rounded border cursor-pointer hover:bg-muted transition-colors"
                    onClick={() => navigate("/dashboard/approvals")}
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{item.title}</p>
                      <p className="text-xs text-muted-foreground">{item.trigger_type} | {item.assignee_role}</p>
                    </div>
                    <Badge variant={PRIORITY_VARIANT[item.priority] || "default"}>
                      {item.priority}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
