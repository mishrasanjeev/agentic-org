import { useState, useEffect, useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import type { AuditEntry } from "@/types";

export default function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [page, setPage] = useState(1);
  const perPage = 50;

  useEffect(() => {
    fetchAudit();
  }, [page]);

  async function fetchAudit() {
    setLoading(true);
    try {
      // Fetch without event_type filter — we filter client-side for partial matching
      const params: Record<string, string> = { page: String(page), per_page: String(perPage) };
      const { data } = await api.get("/audit", { params });
      const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setEntries(items);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }

  // Client-side partial match filtering for event_type
  const filteredEntries = useMemo(() => {
    if (!eventTypeFilter.trim()) return entries;
    const needle = eventTypeFilter.toLowerCase().trim();
    return entries.filter((entry) =>
      entry.event_type.toLowerCase().includes(needle) ||
      entry.action?.toLowerCase().includes(needle)
    );
  }, [entries, eventTypeFilter]);

  function downloadJSON(data: AuditEntry[], filename: string) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function downloadCSV(data: AuditEntry[], filename: string) {
    const headers = ["id", "event_type", "actor_type", "action", "outcome", "created_at"];
    const rows = data.map((entry) =>
      headers.map((h) => {
        const val = String(entry[h as keyof AuditEntry] ?? "");
        return `"${val.replace(/"/g, '""')}"`;
      }).join(",")
    );
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function exportJSON() {
    const datestamp = new Date().toISOString().slice(0, 10);
    downloadJSON(filteredEntries, `audit-evidence-${datestamp}.json`);
  }

  function exportCSV() {
    const datestamp = new Date().toISOString().slice(0, 10);
    downloadCSV(filteredEntries, `audit-log-${datestamp}.csv`);
  }

  const outcomeColor = (outcome: string) => {
    if (outcome === "success") return "success";
    if (outcome === "failure" || outcome === "error") return "destructive";
    return "secondary";
  };

  return (
    <div className="space-y-6">
      <Helmet><title>Audit Log — AgenticOrg</title></Helmet>
      <div className="flex justify-between items-center flex-wrap gap-2">
        <h2 className="text-2xl font-bold">Audit Log</h2>
        <div className="flex gap-2">
          <Button variant="outline" onClick={exportJSON}>Export Evidence Package</Button>
          <Button variant="outline" onClick={exportCSV}>Download CSV</Button>
        </div>
      </div>

      <div className="flex gap-4 items-center">
        <div className="relative">
          <input
            type="text"
            placeholder="Filter by event type (partial match)..."
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
            className="border rounded px-3 py-2 text-sm w-64 pr-8"
          />
          {eventTypeFilter && (
            <button
              onClick={() => setEventTypeFilter("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label="Clear filter"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          )}
        </div>
        {eventTypeFilter && (
          <span className="text-sm text-muted-foreground">
            Showing {filteredEntries.length} of {entries.length} entries
          </span>
        )}
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading audit entries...</p>
      ) : filteredEntries.length === 0 ? (
        <p className="text-muted-foreground">
          {eventTypeFilter ? `No audit entries matching "${eventTypeFilter}". Try a shorter search term.` : "No audit entries found."}
        </p>
      ) : (
        <div className="border rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="text-left p-3">Timestamp</th>
                <th className="text-left p-3">Event Type</th>
                <th className="text-left p-3">Actor</th>
                <th className="text-left p-3">Action</th>
                <th className="text-left p-3">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {filteredEntries.map((entry) => (
                <tr key={entry.id} className="border-t hover:bg-muted/50">
                  <td className="p-3 font-mono text-xs">{new Date(entry.created_at).toLocaleString()}</td>
                  <td className="p-3"><Badge variant="outline">{entry.event_type}</Badge></td>
                  <td className="p-3">{entry.actor_type}</td>
                  <td className="p-3">{entry.action}</td>
                  <td className="p-3"><Badge variant={outcomeColor(entry.outcome) as any}>{entry.outcome}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex justify-between items-center">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
        <span className="text-sm text-muted-foreground">Page {page}</span>
        <Button variant="outline" size="sm" disabled={entries.length < perPage} onClick={() => setPage(page + 1)}>Next</Button>
      </div>
    </div>
  );
}
