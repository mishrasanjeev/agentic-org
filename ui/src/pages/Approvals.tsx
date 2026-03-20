import { useState, useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import ApprovalCard from "@/components/ApprovalCard";
import type { HITLItem } from "@/types";

const PRIORITIES = ["all", "critical", "high", "normal", "low"];

export default function Approvals() {
  const [items, setItems] = useState<HITLItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [tab, setTab] = useState<"pending" | "decided">("pending");

  useEffect(() => {
    fetchApprovals();
  }, []);

  async function fetchApprovals() {
    setLoading(true);
    try {
      const resp = await fetch("/api/v1/approvals");
      const data = await resp.json();
      setItems(data.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleDecide(id: string, decision: string, notes: string) {
    try {
      await fetch(`/api/v1/approvals/${id}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, notes }),
      });
      fetchApprovals();
    } catch (e) {
      console.error("Failed to submit decision", e);
    }
  }

  const pending = items.filter((i) => i.status === "pending");
  const decided = items.filter((i) => i.status !== "pending");
  const displayed = tab === "pending" ? pending : decided;
  const filtered = displayed.filter(
    (i) => priorityFilter === "all" || i.priority === priorityFilter
  );

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Approval Queue</h2>
        <Badge variant="destructive">{pending.length} pending</Badge>
      </div>

      <div className="flex gap-4 items-center border-b pb-2">
        <button onClick={() => setTab("pending")} className={`px-3 py-1 text-sm font-medium ${tab === "pending" ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
          Pending ({pending.length})
        </button>
        <button onClick={() => setTab("decided")} className={`px-3 py-1 text-sm font-medium ${tab === "decided" ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
          Decided ({decided.length})
        </button>
        <div className="ml-auto">
          <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className="border rounded px-3 py-1 text-sm">
            {PRIORITIES.map((p) => <option key={p} value={p}>{p === "all" ? "All Priorities" : p.charAt(0).toUpperCase() + p.slice(1)}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading approvals...</p>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground">{tab === "pending" ? "No pending approvals." : "No decided items."}</p>
      ) : (
        <div className="space-y-4">
          {filtered.map((item) => (
            <ApprovalCard key={item.id} item={item} onDecide={handleDecide} />
          ))}
        </div>
      )}
    </div>
  );
}
