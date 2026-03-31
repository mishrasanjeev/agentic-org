import { useState, useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import ApprovalCard from "@/components/ApprovalCard";
import api from "@/lib/api";
import type { HITLItem } from "@/types";

const PRIORITIES = ["all", "critical", "high", "normal", "low"];

export default function Approvals() {
  const [items, setItems] = useState<HITLItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [tab, setTab] = useState<"pending" | "decided">("pending");
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  useEffect(() => {
    fetchApprovals();
  }, []);

  async function fetchApprovals() {
    setLoading(true);
    try {
      // Fetch both pending and decided in parallel to ensure all items are shown
      const [pendingResp, decidedResp] = await Promise.allSettled([
        api.get("/approvals", { params: { status: "pending" } }),
        api.get("/approvals", { params: { status: "decided" } }),
      ]);
      const extract = (r: PromiseSettledResult<any>) =>
        r.status === "fulfilled"
          ? (Array.isArray(r.value.data) ? r.value.data : Array.isArray(r.value.data?.items) ? r.value.data.items : [])
          : [];
      const allItems = [...extract(pendingResp), ...extract(decidedResp)];
      // Deduplicate by id in case both endpoints return overlapping items
      const seen = new Set<string>();
      const unique = allItems.filter((item: any) => {
        if (seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
      });
      setItems(unique);
    } catch {
      // Fallback: try fetching without status filter
      try {
        const { data } = await api.get("/approvals");
        const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
        setItems(items);
      } catch {
        setItems([]);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleDecide(id: string, decision: string, notes: string) {
    setFeedback(null);
    try {
      await api.post(`/approvals/${id}/decide`, { decision, notes });
      setFeedback({ type: "success", msg: `Decision "${decision}" submitted successfully.` });
      fetchApprovals();
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "Failed to submit decision";
      setFeedback({ type: "error", msg: detail });
    }
  }

  const now = new Date();
  const pending = items.filter((i) => i.status === "pending" && (!i.expires_at || new Date(i.expires_at) > now));
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

      {feedback && (
        <div className={`rounded-lg px-4 py-3 text-sm ${feedback.type === "success" ? "bg-green-50 text-green-800 border border-green-200" : "bg-red-50 text-red-800 border border-red-200"}`}>
          {feedback.msg}
        </div>
      )}

      {loading ? (
        <p className="text-muted-foreground">Loading approvals...</p>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground">{tab === "pending" ? "No pending approvals." : "No decided items."}</p>
      ) : (
        <div className="space-y-4">
          {filtered.map((item) => (
            <ApprovalCard key={item.id} item={item} onDecide={handleDecide} readonly={tab === "decided"} />
          ))}
        </div>
      )}
    </div>
  );
}
