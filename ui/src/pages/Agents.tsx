import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import AgentCard from "@/components/AgentCard";
import KillSwitch from "@/components/KillSwitch";
import api from "@/lib/api";
import type { Agent } from "@/types";

const DOMAINS = ["all", "finance", "hr", "marketing", "ops", "backoffice"];
const STATUSES = ["all", "active", "shadow", "paused", "staging", "deprecated"];

export default function Agents() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [domainFilter, setDomainFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetchAgents();
  }, [domainFilter, statusFilter]);

  async function fetchAgents() {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (domainFilter !== "all") params.domain = domainFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      const { data } = await api.get("/agents", { params });
      const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setAgents(items);
    } catch {
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }

  const filtered = agents.filter(
    (a) => !search || a.name.toLowerCase().includes(search.toLowerCase()) || a.agent_type.toLowerCase().includes(search.toLowerCase())
  );

  const stats = {
    total: agents.length,
    active: agents.filter((a) => a.status === "active").length,
    shadow: agents.filter((a) => a.status === "shadow").length,
    paused: agents.filter((a) => a.status === "paused").length,
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Agent Fleet</h2>
        <Button onClick={() => navigate("/dashboard/agents/new")}>Create Agent</Button>
      </div>

      <div className="grid grid-cols-4 gap-4">
        {Object.entries(stats).map(([label, value]) => (
          <Card key={label}>
            <CardHeader><CardTitle className="text-sm text-muted-foreground capitalize">{label}</CardTitle></CardHeader>
            <CardContent><p className="text-3xl font-bold">{value}</p></CardContent>
          </Card>
        ))}
      </div>

      <div className="flex gap-4 items-center">
        <input
          type="text"
          placeholder="Search agents..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border rounded px-3 py-2 text-sm w-64"
        />
        <select value={domainFilter} onChange={(e) => setDomainFilter(e.target.value)} className="border rounded px-3 py-2 text-sm">
          {DOMAINS.map((d) => <option key={d} value={d}>{d === "all" ? "All Domains" : d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="border rounded px-3 py-2 text-sm">
          {STATUSES.map((s) => <option key={s} value={s}>{s === "all" ? "All Statuses" : s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
        </select>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading agents...</p>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground">No agents found.</p>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map((agent) => (
            <div key={agent.id} className="relative">
              <AgentCard agent={agent} onClick={() => navigate(`/dashboard/agents/${agent.id}`)} />
              {agent.status === "active" && (
                <div className="absolute top-2 right-2">
                  <KillSwitch agentId={agent.id} agentName={agent.name} onPaused={fetchAgents} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
