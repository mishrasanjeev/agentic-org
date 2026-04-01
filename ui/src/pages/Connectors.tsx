import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import ConnectorCard from "@/components/ConnectorCard";
import api from "@/lib/api";
import type { Connector } from "@/types";

const CATEGORIES = ["all", "finance", "hr", "marketing", "ops", "comms"];

export default function Connectors() {
  const navigate = useNavigate();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [healthResult, setHealthResult] = useState<{ id: string; msg: string; ok: boolean } | null>(null);

  useEffect(() => {
    fetchConnectors();
  }, []);

  async function fetchConnectors() {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors");
      const raw = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      // API returns connector_id, map to id for consistency
      const items = raw.map((c: any) => ({ ...c, id: c.id || c.connector_id }));

      if (items.length > 0) {
        setConnectors(items);
      } else {
        // Fallback: show available connectors from the code registry
        // when no tenant connectors have been registered yet
        try {
          const { data: regData } = await api.get("/connectors/registry");
          const regRaw = Array.isArray(regData) ? regData : Array.isArray(regData?.items) ? regData.items : [];
          const regItems = regRaw.map((c: any) => ({ ...c, id: c.id || c.connector_id }));
          setConnectors(regItems);
        } catch {
          setConnectors([]);
        }
      }
    } catch {
      setConnectors([]);
    } finally {
      setLoading(false);
    }
  }

  async function healthCheck(id: string) {
    if (!id) return;
    setHealthResult(null);
    try {
      const { data } = await api.get(`/connectors/${id}/health`);
      const status = data.healthy ? "Healthy" : "Unhealthy";
      setHealthResult({ id, msg: `${data.name || "Connector"}: ${status} | Last check: ${data.health_check_at || "Never"}`, ok: !!data.healthy });
    } catch (err: any) {
      const detail = err.response?.data?.detail || err.message || "Unknown error";
      setHealthResult({ id, msg: `Health check failed: ${detail}`, ok: false });
    }
  }

  const filtered = connectors.filter(
    (c) => categoryFilter === "all" || c.category === categoryFilter
  );

  const stats = {
    total: connectors.length,
    active: connectors.filter((c) => c.status === "active").length,
    unhealthy: connectors.filter((c) => c.status !== "active").length,
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Connectors</h2>
        <Button onClick={() => navigate("/dashboard/connectors/new")}>Register Connector</Button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Total</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold">{stats.total}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Active</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold text-green-600">{stats.active}</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Unhealthy</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold text-red-600">{stats.unhealthy}</p></CardContent></Card>
      </div>

      {healthResult && (
        <div className={`rounded-lg px-4 py-3 text-sm flex items-center justify-between ${healthResult.ok ? "bg-green-50 text-green-800 border border-green-200" : "bg-red-50 text-red-800 border border-red-200"}`}>
          <span>{healthResult.msg}</span>
          <button onClick={() => setHealthResult(null)} className="ml-2 text-xs underline">Dismiss</button>
        </div>
      )}

      <div className="flex gap-4 items-center">
        <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} className="border rounded px-3 py-2 text-sm">
          {CATEGORIES.map((c) => <option key={c} value={c}>{c === "all" ? "All Categories" : c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
        </select>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading connectors...</p>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground">No connectors found.</p>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map((connector) => (
            <div key={connector.id} className="relative">
              <ConnectorCard connector={connector} />
              <Button variant="outline" size="sm" className="absolute bottom-3 right-3" onClick={() => healthCheck(connector.id)}>
                Health Check
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
