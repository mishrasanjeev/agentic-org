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

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_PACKS: IndustryPack[] = [
  {
    id: "healthcare",
    name: "Healthcare",
    description: "HIPAA-compliant patient data processing, appointment scheduling, insurance claim automation, and medical records management.",
    icon: "H",
    agent_count: 6,
    agents: [
      { name: "Claims Processor", type: "Finance" },
      { name: "Patient Scheduler", type: "Operations" },
      { name: "Insurance Verifier", type: "Compliance" },
      { name: "Medical Records Indexer", type: "Knowledge" },
      { name: "Prescription Monitor", type: "Compliance" },
      { name: "Billing Reconciler", type: "Finance" },
    ],
    workflows: [
      { name: "Insurance Claim Pipeline", description: "End-to-end claim submission and tracking" },
      { name: "Patient Onboarding", description: "Automated patient registration and insurance verification" },
    ],
    required_connectors: ["ehr_system", "insurance_api", "email_service"],
    installed: false,
  },
  {
    id: "legal",
    name: "Legal",
    description: "Contract review, compliance monitoring, case management, legal document generation, and regulatory filing automation.",
    icon: "L",
    agent_count: 5,
    agents: [
      { name: "Contract Reviewer", type: "Compliance" },
      { name: "Case Manager", type: "Operations" },
      { name: "Legal Doc Generator", type: "Knowledge" },
      { name: "Regulatory Filing Bot", type: "Compliance" },
      { name: "Due Diligence Analyzer", type: "Research" },
    ],
    workflows: [
      { name: "Contract Review Pipeline", description: "Automated clause extraction and risk flagging" },
      { name: "Regulatory Filing", description: "Auto-fill and submit regulatory documents" },
    ],
    required_connectors: ["document_store", "email_service", "gstn"],
    installed: true,
  },
  {
    id: "insurance",
    name: "Insurance",
    description: "Policy underwriting, claims adjudication, fraud detection, customer onboarding, and actuarial data processing.",
    icon: "I",
    agent_count: 7,
    agents: [
      { name: "Underwriting Agent", type: "Finance" },
      { name: "Claims Adjudicator", type: "Operations" },
      { name: "Fraud Detector", type: "Compliance" },
      { name: "Policy Renewal Bot", type: "Operations" },
      { name: "Customer Onboarder", type: "HR" },
      { name: "Actuarial Processor", type: "Finance" },
      { name: "Lapse Prevention Agent", type: "Marketing" },
    ],
    workflows: [
      { name: "Claims Pipeline", description: "From submission to settlement with fraud checks" },
      { name: "Policy Renewal Flow", description: "Automated renewal reminders and processing" },
    ],
    required_connectors: ["insurance_core", "banking_aa", "email_service"],
    installed: false,
  },
  {
    id: "manufacturing",
    name: "Manufacturing",
    description: "Supply chain optimization, quality control automation, equipment maintenance scheduling, and production planning.",
    icon: "M",
    agent_count: 5,
    agents: [
      { name: "Supply Chain Optimizer", type: "Operations" },
      { name: "QC Inspector", type: "Compliance" },
      { name: "Maintenance Scheduler", type: "Operations" },
      { name: "Production Planner", type: "Operations" },
      { name: "Vendor Manager", type: "Finance" },
    ],
    workflows: [
      { name: "Production Order Pipeline", description: "From order receipt to production scheduling" },
      { name: "Predictive Maintenance", description: "Equipment health monitoring and auto-scheduling" },
    ],
    required_connectors: ["erp_system", "iot_gateway", "email_service"],
    installed: false,
  },
];

const ICON_COLORS: Record<string, string> = {
  Healthcare: "from-red-500 to-pink-600",
  Legal: "from-amber-500 to-orange-600",
  Insurance: "from-blue-500 to-cyan-600",
  Manufacturing: "from-green-500 to-emerald-600",
};

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
          ? Array.isArray(packsRes.value.data) ? packsRes.value.data : packsRes.value.data?.items || []
          : [];
      const installed =
        installedRes.status === "fulfilled"
          ? Array.isArray(installedRes.value.data) ? installedRes.value.data : installedRes.value.data?.items || []
          : [];

      if (allPacks.length > 0) {
        const installedIds = new Set(installed.map((i: any) => i.id || i.name));
        setPacks(allPacks.map((p: IndustryPack) => ({ ...p, installed: installedIds.has(p.id) })));
      } else {
        setPacks(MOCK_PACKS);
      }
    } catch {
      setPacks(MOCK_PACKS);
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
      <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
        {packs.map((pack) => (
          <Card
            key={pack.id}
            className="cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => setSelectedPack(pack)}
          >
            <CardHeader>
              <div className="flex items-start justify-between">
                <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${ICON_COLORS[pack.name] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-xl`}>
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

      {/* Detail panel */}
      {selectedPack && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={() => setSelectedPack(null)}>
          <div className="bg-background border rounded-lg p-6 w-full max-w-2xl shadow-lg max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${ICON_COLORS[selectedPack.name] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-xl`}>
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
