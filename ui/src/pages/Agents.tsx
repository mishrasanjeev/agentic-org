import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import AgentCard from "@/components/AgentCard";
import KillSwitch from "@/components/KillSwitch";
import api, { agentsApi } from "@/lib/api";
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
  const [showImport, setShowImport] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<any>(null);

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

  async function handleCsvImport() {
    if (!importFile) return;
    // Validate file before upload
    if (!importFile.name.toLowerCase().endsWith(".csv")) {
      setImportResult({ error: "Only CSV files are supported. Please select a .csv file." });
      return;
    }
    if (importFile.size > 5 * 1024 * 1024) {
      setImportResult({ error: "File too large. Maximum size is 5 MB." });
      return;
    }
    if (importFile.size === 0) {
      setImportResult({ error: "File is empty. Please select a valid CSV file." });
      return;
    }
    setImporting(true);
    setImportResult(null);
    try {
      const { data } = await agentsApi.importCsv(importFile);
      setImportResult(data);
      fetchAgents();
    } catch (err: any) {
      setImportResult({ error: err.response?.data?.detail || "Import failed" });
    } finally {
      setImporting(false);
    }
  }

  function downloadTemplate() {
    const csv = "name,agent_type,domain,designation,specialization,reporting_to_name,org_level,llm_model,confidence_floor\nPriya Sharma,ap_processor,finance,Senior AP Analyst,Domestic invoices Mumbai,VP Finance,2,gemini-2.5-flash,0.88\n";
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "agent_import_template.csv"; a.click();
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Agent Fleet</h2>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setShowImport(!showImport)}>Import CSV</Button>
          <Button variant="outline" onClick={() => navigate("/dashboard/agents/from-sop")}>Create from SOP</Button>
          <Button onClick={() => navigate("/dashboard/agents/new")}>Create Agent</Button>
        </div>
      </div>

      {/* CSV Import Panel */}
      {showImport && (
        <Card>
          <CardContent className="pt-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">Import Agents from CSV</p>
                <p className="text-xs text-muted-foreground">Upload a CSV with your org hierarchy. Agents are created in shadow mode. Parent links set via "reporting_to_name" column.</p>
              </div>
              <Button variant="outline" size="sm" onClick={downloadTemplate}>Download Template</Button>
            </div>
            <div className="flex gap-2 items-center">
              <input type="file" accept=".csv" onChange={(e) => { setImportFile(e.target.files?.[0] || null); setImportResult(null); }} className="text-sm" />
              <Button size="sm" onClick={handleCsvImport} disabled={!importFile || importing}>
                {importing ? "Importing..." : "Upload & Import"}
              </Button>
            </div>
            {importResult && (
              <div className={`text-sm p-3 rounded ${importResult.error ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"}`}>
                {importResult.error ? (
                  <p>{importResult.error}</p>
                ) : (
                  <>
                    <p><strong>{importResult.imported}</strong> agents imported | <strong>{importResult.parent_links_set}</strong> parent links set | <strong>{importResult.skipped}</strong> skipped</p>
                    {importResult.skip_details?.length > 0 && (
                      <details className="mt-1"><summary className="cursor-pointer text-xs">Skipped rows</summary>
                        <ul className="text-xs mt-1">{importResult.skip_details.map((s: any, i: number) => <li key={i}>{s.reason}: {s.row?.name || "unknown"}</li>)}</ul>
                      </details>
                    )}
                  </>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

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
