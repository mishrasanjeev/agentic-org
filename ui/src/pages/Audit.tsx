import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { AuditEntry } from "@/types";

export default function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [page, setPage] = useState(1);
  const perPage = 50;

  useEffect(() => {
    fetchAudit();
  }, [page, eventTypeFilter]);

  async function fetchAudit() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
      if (eventTypeFilter) params.set("event_type", eventTypeFilter);
      const resp = await fetch(`/api/v1/audit?${params}`);
      const data = await resp.json();
      setEntries(data.items || []);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }

  async function exportAudit() {
    try {
      const resp = await fetch("/api/v1/compliance/evidence-package");
      const data = await resp.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-evidence-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("Export failed", e);
    }
  }

  const outcomeColor = (outcome: string) => {
    if (outcome === "success") return "success";
    if (outcome === "failure" || outcome === "error") return "destructive";
    return "secondary";
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Audit Log</h2>
        <Button variant="outline" onClick={exportAudit}>Export Evidence Package</Button>
      </div>

      <div className="flex gap-4 items-center">
        <input
          type="text"
          placeholder="Filter by event type..."
          value={eventTypeFilter}
          onChange={(e) => { setEventTypeFilter(e.target.value); setPage(1); }}
          className="border rounded px-3 py-2 text-sm w-64"
        />
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading audit entries...</p>
      ) : entries.length === 0 ? (
        <p className="text-muted-foreground">No audit entries found.</p>
      ) : (
        <div className="border rounded overflow-hidden">
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
              {entries.map((entry) => (
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
