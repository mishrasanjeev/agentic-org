import { useState, useEffect, useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AgentScope {
  agent_id: string;
  agent_name: string;
  connector: string;
  tools: string[];
  permission: "READ" | "WRITE" | "DELETE" | "ADMIN";
  status: "active" | "shadow" | "paused";
  calls_24h: number;
  denials_24h: number;
}

interface EnforceEntry {
  id: string;
  timestamp: string;
  agent_name: string;
  connector: string;
  tool: string;
  permission: string;
  result: "allowed" | "denied";
  reason: string;
}

/* ------------------------------------------------------------------ */
/*  Permission badge colors                                            */
/* ------------------------------------------------------------------ */

const PERMISSION_VARIANT: Record<string, "default" | "success" | "warning" | "destructive" | "secondary"> = {
  READ: "success",
  WRITE: "warning",
  DELETE: "destructive",
  ADMIN: "default",
};

const PERMISSIONS = ["All", "READ", "WRITE", "DELETE", "ADMIN"];

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ScopeDashboard() {
  const [scopes, setScopes] = useState<AgentScope[]>([]);
  const [loading, setLoading] = useState(true);
  const [connectorFilter, setConnectorFilter] = useState("All");
  const [permissionFilter, setPermissionFilter] = useState("All");
  const [agentSearch, setAgentSearch] = useState("");

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    try {
      // Attempt to load real data from both endpoints
      const [agentsRes, enforceRes] = await Promise.allSettled([
        api.get("/agents"),
        api.get("/audit/enforce"),
      ]);

      const agents = agentsRes.status === "fulfilled"
        ? (Array.isArray(agentsRes.value.data) ? agentsRes.value.data : agentsRes.value.data?.items || [])
        : [];
      const enforceData = enforceRes.status === "fulfilled"
        ? (Array.isArray(enforceRes.value.data) ? enforceRes.value.data : enforceRes.value.data?.items || [])
        : [];

      // Build scope entries from real data
      const scopeMap = new Map<string, AgentScope>();
      for (const entry of enforceData as EnforceEntry[]) {
        const key = `${entry.agent_name}::${entry.connector}`;
        if (!scopeMap.has(key)) {
          const agent = agents.find((a: any) => a.name === entry.agent_name);
          scopeMap.set(key, {
            agent_id: agent?.id || entry.agent_name,
            agent_name: entry.agent_name,
            connector: entry.connector,
            tools: [],
            permission: (entry.permission as AgentScope["permission"]) || "READ",
            status: agent?.status || "active",
            calls_24h: 0,
            denials_24h: 0,
          });
        }
        const scope = scopeMap.get(key)!;
        if (entry.tool && !scope.tools.includes(entry.tool)) scope.tools.push(entry.tool);
        scope.calls_24h += 1;
        if (entry.result === "denied") scope.denials_24h += 1;
      }
      setScopes(Array.from(scopeMap.values()));
    } catch {
      setScopes([]);
    } finally {
      setLoading(false);
    }
  }

  /* ---- Derived values ---- */

  const connectors = useMemo(() => {
    const set = new Set(scopes.map((s) => s.connector));
    return ["All", ...Array.from(set).sort()];
  }, [scopes]);

  const filtered = useMemo(() => {
    return scopes.filter((s) => {
      if (connectorFilter !== "All" && s.connector !== connectorFilter) return false;
      if (permissionFilter !== "All" && s.permission !== permissionFilter) return false;
      if (agentSearch && !s.agent_name.toLowerCase().includes(agentSearch.toLowerCase())) return false;
      return true;
    });
  }, [scopes, connectorFilter, permissionFilter, agentSearch]);

  const totalAgents = new Set(scopes.map((s) => s.agent_id)).size;
  const totalCalls = scopes.reduce((sum, s) => sum + s.calls_24h, 0);
  const totalDenials = scopes.reduce((sum, s) => sum + s.denials_24h, 0);
  const denialRate = totalCalls > 0 ? ((totalDenials / totalCalls) * 100).toFixed(1) : "0.0";

  const stats = [
    { label: "Total Agents", value: totalAgents },
    { label: "Tool Calls (24h)", value: totalCalls },
    { label: "Denials (24h)", value: totalDenials },
    { label: "Denial Rate", value: `${denialRate}%` },
  ];

  return (
    <div className="space-y-6">
      <Helmet><title>Scope Dashboard — AgenticOrg</title></Helmet>

      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Scope Dashboard</h2>
        <Button variant="outline" onClick={fetchData}>Refresh</Button>
      </div>

      {/* Aggregate Stats */}
      <div className="grid grid-cols-4 gap-4">
        {stats.map(({ label, value }) => (
          <Card key={label}>
            <CardHeader>
              <CardTitle className="text-sm text-muted-foreground">{label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-bold">{value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Filter Controls */}
      <div className="flex gap-4 items-center flex-wrap">
        <input
          type="text"
          placeholder="Search agent..."
          value={agentSearch}
          onChange={(e) => setAgentSearch(e.target.value)}
          className="border rounded px-3 py-2 text-sm w-64"
        />
        <select
          value={connectorFilter}
          onChange={(e) => setConnectorFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
        >
          {connectors.map((c) => (
            <option key={c} value={c}>{c === "All" ? "All Connectors" : c}</option>
          ))}
        </select>
        <select
          value={permissionFilter}
          onChange={(e) => setPermissionFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
        >
          {PERMISSIONS.map((p) => (
            <option key={p} value={p}>{p === "All" ? "All Permissions" : p}</option>
          ))}
        </select>
        {(agentSearch || connectorFilter !== "All" || permissionFilter !== "All") && (
          <span className="text-sm text-muted-foreground">
            Showing {filtered.length} of {scopes.length} entries
          </span>
        )}
      </div>

      {/* Main Table */}
      {loading ? (
        <p className="text-muted-foreground">Loading scope data...</p>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground">No scope entries found.</p>
      ) : (
        <div className="border rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="text-left p-3">Agent</th>
                <th className="text-left p-3">Connector</th>
                <th className="text-left p-3">Tools</th>
                <th className="text-left p-3">Permission</th>
                <th className="text-left p-3">Status (24h)</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((scope) => (
                <tr key={`${scope.agent_id}-${scope.connector}`} className="border-t hover:bg-muted/50">
                  <td className="p-3 font-medium">{scope.agent_name}</td>
                  <td className="p-3">
                    <Badge variant="outline">{scope.connector}</Badge>
                  </td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-1">
                      {scope.tools.map((tool) => (
                        <Badge key={tool} variant="secondary">{tool}</Badge>
                      ))}
                    </div>
                  </td>
                  <td className="p-3">
                    <Badge variant={PERMISSION_VARIANT[scope.permission] || "secondary"}>
                      {scope.permission}
                    </Badge>
                  </td>
                  <td className="p-3">
                    <span className="text-green-600 font-medium">{scope.calls_24h} calls</span>
                    {scope.denials_24h > 0 && (
                      <span className="text-red-600 font-medium ml-2">/ {scope.denials_24h} denied</span>
                    )}
                    {scope.denials_24h === 0 && (
                      <span className="text-muted-foreground ml-2">/ 0 denied</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
