import { useState, useEffect, useCallback } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface PackAgent {
  name: string;
  type: string;
}

interface PackWorkflow {
  name: string;
  description: string;
}

interface IndustryPack {
  id: string;
  name: string;
  description: string;
  icon: string;
  agent_count: number;
  agents: PackAgent[];
  workflows: PackWorkflow[];
  required_connectors: string[];
  installed: boolean;
}

const ICON_COLORS: Record<string, string> = {
  healthcare: "from-red-500 to-pink-600",
  legal: "from-amber-500 to-orange-600",
  insurance: "from-blue-500 to-cyan-600",
  manufacturing: "from-green-500 to-emerald-600",
  "ca-firm": "from-cyan-500 to-fuchsia-600",
};

const ICON_LABELS: Record<string, string> = {
  healthcare: "H",
  legal: "L",
  insurance: "I",
  manufacturing: "M",
  "ca-firm": "CA",
};

function titleCase(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function normalizeAgents(rawAgents: unknown): PackAgent[] {
  if (!Array.isArray(rawAgents)) return [];
  return rawAgents.map((agent, index) => {
    if (!agent || typeof agent !== "object") {
      const fallback = String(agent ?? `Agent ${index + 1}`);
      return { name: fallback, type: fallback };
    }
    const record = agent as Record<string, unknown>;
    const type = String(record.type || record.domain || `agent_${index + 1}`);
    return {
      name: String(record.name || titleCase(type)),
      type,
    };
  });
}

function normalizeWorkflows(rawWorkflows: unknown): PackWorkflow[] {
  if (!Array.isArray(rawWorkflows)) return [];
  return rawWorkflows.map((workflow, index) => {
    if (typeof workflow === "string") {
      return {
        name: titleCase(workflow),
        description: workflow,
      };
    }
    if (workflow && typeof workflow === "object") {
      const record = workflow as Record<string, unknown>;
      const rawName = String(record.name || record.id || `workflow_${index + 1}`);
      return {
        name: titleCase(rawName),
        description: String(record.description || rawName),
      };
    }
    const fallback = `workflow_${index + 1}`;
    return { name: titleCase(fallback), description: fallback };
  });
}

function deriveRequiredConnectors(rawPack: Record<string, unknown>, agents: PackAgent[]): string[] {
  const explicit = rawPack.required_connectors;
  if (Array.isArray(explicit)) {
    return explicit.map((value) => String(value));
  }

  const rawAgents = Array.isArray(rawPack.agents) ? rawPack.agents : [];
  const connectors = new Set<string>();

  rawAgents.forEach((agent, index) => {
    const normalized = agents[index];
    if (normalized?.type) connectors.add(normalized.type);

    if (!agent || typeof agent !== "object") return;
    const tools = Array.isArray((agent as Record<string, unknown>).tools)
      ? ((agent as Record<string, unknown>).tools as unknown[])
      : [];

    tools.forEach((tool) => {
      const toolName = String(tool);
      connectors.add(toolName.includes(":") ? toolName.split(":")[0] : toolName);
    });
  });

  return Array.from(connectors).sort();
}

function normalizePack(rawPack: unknown, installedIds: Set<string>): IndustryPack | null {
  if (!rawPack || typeof rawPack !== "object") return null;

  const record = rawPack as Record<string, unknown>;
  const id = String(record.id || record.name || "");
  if (!id) return null;

  const agents = normalizeAgents(record.agents);
  const workflows = normalizeWorkflows(record.workflows);
  const name = String(record.display_name || record.name || id);

  return {
    id,
    name,
    description: String(record.description || ""),
    icon: String(record.icon || ICON_LABELS[id] || name.charAt(0).toUpperCase()),
    agent_count:
      typeof record.agent_count === "number" ? record.agent_count : agents.length,
    agents,
    workflows,
    required_connectors: deriveRequiredConnectors(record, agents),
    installed: installedIds.has(id),
  };
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function IndustryPacks() {
  const [packs, setPacks] = useState<IndustryPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedPack, setSelectedPack] = useState<IndustryPack | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [packsRes, installedRes] = await Promise.allSettled([
        api.get("/packs"),
        api.get("/packs/installed"),
      ]);

      const allPacks =
        packsRes.status === "fulfilled"
          ? Array.isArray(packsRes.value.data)
            ? packsRes.value.data
            : packsRes.value.data?.packs || packsRes.value.data?.items || []
          : [];
      const installed =
        installedRes.status === "fulfilled"
          ? Array.isArray(installedRes.value.data)
            ? installedRes.value.data
            : installedRes.value.data?.installed || installedRes.value.data?.items || []
          : [];

      const installedIds = new Set<string>(
        (installed as unknown[]).map((item: unknown): string => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const record = item as Record<string, unknown>;
            return String(record.id || record.name || "");
          }
          return "";
        }).filter(Boolean)
      );

      setPacks(
        (allPacks as unknown[])
          .map((pack: unknown) => normalizePack(pack, installedIds))
          .filter((pack: IndustryPack | null): pack is IndustryPack => Boolean(pack))
      );
    } catch {
      setPacks([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleInstall = async (pack: IndustryPack) => {
    setActionLoading(pack.id);
    try {
      await api.post(`/packs/${pack.id}/install`);
    } catch {
      // optimistic update
    }
    setPacks((prev) => prev.map((p) => (p.id === pack.id ? { ...p, installed: true } : p)));
    if (selectedPack?.id === pack.id) setSelectedPack({ ...pack, installed: true });
    setActionLoading(null);
  };

  const handleUninstall = async (pack: IndustryPack) => {
    setActionLoading(pack.id);
    try {
      await api.delete(`/packs/${pack.id}`);
    } catch {
      // optimistic update
    }
    setPacks((prev) => prev.map((p) => (p.id === pack.id ? { ...p, installed: false } : p)));
    if (selectedPack?.id === pack.id) setSelectedPack({ ...pack, installed: false });
    setActionLoading(null);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading industry packs...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Industry Packs</h2>
        <Button variant="outline" onClick={fetchData}>Refresh</Button>
      </div>

      {/* Pack grid */}
      {packs.length === 0 ? (
        <p className="text-muted-foreground">No industry packs available.</p>
      ) : (
      <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
        {packs.map((pack) => (
          <Card
            key={pack.id}
            className="cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => setSelectedPack(pack)}
          >
            <CardHeader>
              <div className="flex items-start justify-between">
                <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${ICON_COLORS[pack.id] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-xl`}>
                  {pack.icon}
                </div>
                {pack.installed && <Badge variant="success">Installed</Badge>}
              </div>
              <CardTitle className="text-lg mt-2">{pack.name}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground mb-3 line-clamp-2">{pack.description}</p>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">{pack.agent_count} agents</span>
                {pack.installed ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={actionLoading === pack.id}
                    onClick={(e) => { e.stopPropagation(); handleUninstall(pack); }}
                  >
                    {actionLoading === pack.id ? "..." : "Uninstall"}
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    disabled={actionLoading === pack.id}
                    onClick={(e) => { e.stopPropagation(); handleInstall(pack); }}
                  >
                    {actionLoading === pack.id ? "Installing..." : "Install"}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      )}

      {/* Detail panel */}
      {selectedPack && (
        <div className="fixed inset-0 bg-slate-950/60 z-50 flex items-center justify-center" onClick={() => setSelectedPack(null)}>
          <div className="bg-background border rounded-lg p-6 w-full max-w-2xl shadow-lg max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${ICON_COLORS[selectedPack.id] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-xl`}>
                  {selectedPack.icon}
                </div>
                <div>
                  <h3 className="text-xl font-bold">{selectedPack.name}</h3>
                  <p className="text-sm text-muted-foreground">{selectedPack.agent_count} agents</p>
                </div>
              </div>
              <button onClick={() => setSelectedPack(null)} className="p-1 rounded hover:bg-muted" aria-label="Close">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>

            <p className="text-sm text-muted-foreground mb-4">{selectedPack.description}</p>

            {/* Agents */}
            <h4 className="text-sm font-semibold mb-2">Agents</h4>
            <div className="border rounded overflow-hidden mb-4">
              <table className="w-full text-sm">
                <thead className="bg-muted">
                  <tr>
                    <th className="text-left p-2">Agent Name</th>
                    <th className="text-left p-2">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedPack.agents.map((a, i) => (
                    <tr key={i} className="border-t">
                      <td className="p-2 font-medium">{a.name}</td>
                      <td className="p-2"><Badge variant="outline">{a.type}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Workflows */}
            <h4 className="text-sm font-semibold mb-2">Workflows</h4>
            <div className="space-y-2 mb-4">
              {selectedPack.workflows.map((w, i) => (
                <div key={i} className="border rounded p-3">
                  <p className="font-medium text-sm">{w.name}</p>
                  <p className="text-xs text-muted-foreground">{w.description}</p>
                </div>
              ))}
            </div>

            {/* Required connectors */}
            <h4 className="text-sm font-semibold mb-2">Required Connectors</h4>
            <div className="flex flex-wrap gap-1.5 mb-4">
              {selectedPack.required_connectors.map((c) => (
                <Badge key={c} variant="secondary">{c}</Badge>
              ))}
            </div>

            {/* Install/Uninstall */}
            <div className="flex justify-end gap-2 pt-2 border-t">
              <Button variant="outline" onClick={() => setSelectedPack(null)}>Close</Button>
              {selectedPack.installed ? (
                <Button
                  variant="destructive"
                  disabled={actionLoading === selectedPack.id}
                  onClick={() => handleUninstall(selectedPack)}
                >
                  {actionLoading === selectedPack.id ? "Removing..." : "Uninstall Pack"}
                </Button>
              ) : (
                <Button
                  disabled={actionLoading === selectedPack.id}
                  onClick={() => handleInstall(selectedPack)}
                >
                  {actionLoading === selectedPack.id ? "Installing..." : "Install Pack"}
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
