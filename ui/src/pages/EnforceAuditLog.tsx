import { useState, useEffect, useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

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
/*  Mock data (used when API endpoint is not yet available)            */
/* ------------------------------------------------------------------ */

function generateMockEntries(): EnforceEntry[] {
  const agents = ["AP Processor", "Recon Agent", "Tax Compliance", "Payroll Agent", "Onboarding Bot", "Campaign Agent", "Treasury Agent", "AR Collector", "Leave Manager", "SEO Analyzer", "Social Monitor", "Content Writer"];
  const connectors = ["oracle_fusion", "banking_aa", "gstn", "workday", "gsuite", "google_ads", "hdfc_bank", "email_service", "semrush", "twitter_api", "wordpress"];
  const tools = ["read_purchase_order", "create_invoice", "fetch_transactions", "validate_gstin", "compute_payroll", "provision_account", "fetch_metrics", "update_budget", "fetch_balance", "send_reminder", "keyword_analysis", "create_post", "delete_scheduled_transfer", "file_gstr1", "approve_payment"];
  const permissions = ["READ", "WRITE", "DELETE", "ADMIN"];
  const reasons_allowed = ["Scope matched", "Permission granted by policy", "Admin override active", "Tool within agent manifest"];
  const reasons_denied = ["Tool not in agent scope", "Permission level insufficient", "Connector not authorized for agent", "Rate limit exceeded", "Cross-tenant access blocked"];

  const entries: EnforceEntry[] = [];
  const now = Date.now();
  for (let i = 0; i < 200; i++) {
    const isDenied = Math.random() < 0.15;
    entries.push({
      id: `enf-${String(i + 1).padStart(4, "0")}`,
      timestamp: new Date(now - i * 120000 - Math.random() * 60000).toISOString(),
      agent_name: agents[Math.floor(Math.random() * agents.length)],
      connector: connectors[Math.floor(Math.random() * connectors.length)],
      tool: tools[Math.floor(Math.random() * tools.length)],
      permission: permissions[Math.floor(Math.random() * permissions.length)],
      result: isDenied ? "denied" : "allowed",
      reason: isDenied
        ? reasons_denied[Math.floor(Math.random() * reasons_denied.length)]
        : reasons_allowed[Math.floor(Math.random() * reasons_allowed.length)],
    });
  }
  return entries;
}

const MOCK_ENTRIES = generateMockEntries();

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function EnforceAuditLog() {
  const [entries, setEntries] = useState<EnforceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [resultFilter, setResultFilter] = useState<"All" | "allowed" | "denied">("All");
  const [agentFilter, setAgentFilter] = useState("All");
  const [connectorFilter, setConnectorFilter] = useState("All");
  const [page, setPage] = useState(1);
  const perPage = 50;

  useEffect(() => {
    fetchEntries();
  }, []);

  async function fetchEntries() {
    setLoading(true);
    try {
      const { data } = await api.get("/audit/enforce");
      const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setEntries(items.length > 0 ? items : MOCK_ENTRIES);
    } catch {
      setEntries(MOCK_ENTRIES);
    } finally {
      setLoading(false);
    }
  }

  /* ---- Derived filter options ---- */

  const agents = useMemo(() => {
    const set = new Set(entries.map((e) => e.agent_name));
    return ["All", ...Array.from(set).sort()];
  }, [entries]);

  const connectors = useMemo(() => {
    const set = new Set(entries.map((e) => e.connector));
    return ["All", ...Array.from(set).sort()];
  }, [entries]);

  /* ---- Filtered + paginated data ---- */

  const filtered = useMemo(() => {
    return entries.filter((e) => {
      if (resultFilter !== "All" && e.result !== resultFilter) return false;
      if (agentFilter !== "All" && e.agent_name !== agentFilter) return false;
      if (connectorFilter !== "All" && e.connector !== connectorFilter) return false;
      return true;
    });
  }, [entries, resultFilter, agentFilter, connectorFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const paginated = filtered.slice((page - 1) * perPage, page * perPage);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [resultFilter, agentFilter, connectorFilter]);

  /* ---- CSV Export ---- */

  function exportCSV() {
    const headers = ["timestamp", "agent_name", "connector", "tool", "permission", "result", "reason"];
    const rows = filtered.map((entry) =>
      headers.map((h) => {
        const val = String(entry[h as keyof EnforceEntry] ?? "");
        return `"${val.replace(/"/g, '""')}"`;
      }).join(",")
    );
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const datestamp = new Date().toISOString().slice(0, 10);
    a.download = `enforce-audit-${datestamp}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  /* ---- Result badge ---- */

  function resultBadge(result: string) {
    if (result === "allowed") return <Badge variant="success">Allowed</Badge>;
    if (result === "denied") return <Badge variant="destructive">Denied</Badge>;
    return <Badge variant="secondary">{result}</Badge>;
  }

  function permissionBadge(perm: string) {
    const variants: Record<string, "success" | "warning" | "destructive" | "default"> = {
      READ: "success",
      WRITE: "warning",
      DELETE: "destructive",
      ADMIN: "default",
    };
    return <Badge variant={variants[perm] || "secondary"}>{perm}</Badge>;
  }

  return (
    <div className="space-y-6">
      <Helmet><title>Enforce Audit Log — AgenticOrg</title></Helmet>

      <div className="flex justify-between items-center flex-wrap gap-2">
        <h2 className="text-2xl font-bold">Enforce Audit Log</h2>
        <div className="flex gap-2">
          <Button variant="outline" onClick={fetchEntries}>Refresh</Button>
          <Button variant="outline" onClick={exportCSV}>Download CSV</Button>
        </div>
      </div>

      {/* Filter Controls */}
      <div className="flex gap-4 items-center flex-wrap">
        <select
          value={resultFilter}
          onChange={(e) => setResultFilter(e.target.value as "All" | "allowed" | "denied")}
          className="border rounded px-3 py-2 text-sm"
        >
          <option value="All">All Results</option>
          <option value="allowed">Allowed</option>
          <option value="denied">Denied</option>
        </select>
        <select
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
        >
          {agents.map((a) => (
            <option key={a} value={a}>{a === "All" ? "All Agents" : a}</option>
          ))}
        </select>
        <select
          value={connectorFilter}
          onChange={(e) => setConnectorFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
        >
          {connectors.map((c) => (
            <option key={c} value={c}>{c === "All" ? "All Connectors" : c}</option>
          ))}
        </select>
        <span className="text-sm text-muted-foreground">
          {filtered.length} entries{resultFilter !== "All" || agentFilter !== "All" || connectorFilter !== "All" ? " (filtered)" : ""}
        </span>
      </div>

      {/* Table */}
      {loading ? (
        <p className="text-muted-foreground">Loading enforce audit entries...</p>
      ) : paginated.length === 0 ? (
        <p className="text-muted-foreground">No enforce audit entries found.</p>
      ) : (
        <div className="border rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="text-left p-3">Timestamp</th>
                <th className="text-left p-3">Agent</th>
                <th className="text-left p-3">Connector</th>
                <th className="text-left p-3">Tool</th>
                <th className="text-left p-3">Permission</th>
                <th className="text-left p-3">Result</th>
                <th className="text-left p-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {paginated.map((entry) => (
                <tr key={entry.id} className="border-t hover:bg-muted/50">
                  <td className="p-3 font-mono text-xs whitespace-nowrap">
                    {new Date(entry.timestamp).toLocaleString()}
                  </td>
                  <td className="p-3 font-medium">{entry.agent_name}</td>
                  <td className="p-3">
                    <Badge variant="outline">{entry.connector}</Badge>
                  </td>
                  <td className="p-3">
                    <Badge variant="secondary">{entry.tool}</Badge>
                  </td>
                  <td className="p-3">{permissionBadge(entry.permission)}</td>
                  <td className="p-3">{resultBadge(entry.result)}</td>
                  <td className="p-3 text-muted-foreground text-xs max-w-xs truncate" title={entry.reason}>
                    {entry.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      <div className="flex justify-between items-center">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </Button>
        <span className="text-sm text-muted-foreground">
          Page {page} of {totalPages}
        </span>
        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}
