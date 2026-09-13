import { useState, useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import ApprovalCard from "@/components/ApprovalCard";
import api, { extractApiError } from "@/lib/api";
import type { HITLItem } from "@/types";

const PRIORITIES = ["all", "critical", "high", "normal", "low"];
// Server caps per_page at 100. The decided tab merges "decided" + "rejected"
// pages, so its total is the sum of both statuses' totals.
const PER_PAGE = 50;
const DECIDED_STATUSES = ["decided", "rejected"] as const;

interface PageResult {
  items: HITLItem[];
  total: number;
  pages: number;
}

function extractPage(data: any): PageResult {
  if (Array.isArray(data)) return { items: data, total: data.length, pages: 1 };
  const items = Array.isArray(data?.items) ? data.items : [];
  const total = typeof data?.total === "number" ? data.total : items.length;
  const pages = typeof data?.pages === "number" ? data.pages : 1;
  return { items, total, pages };
}

export default function Approvals() {
  const [items, setItems] = useState<HITLItem[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [pendingTotal, setPendingTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [tab, setTab] = useState<"pending" | "decided">("pending");
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  useEffect(() => {
    fetchApprovals(tab, page);
  }, [tab, page]); // eslint-disable-line react-hooks/exhaustive-deps

  function switchTab(next: "pending" | "decided") {
    if (next === tab) return;
    setPage(1);
    setTab(next);
  }

  async function fetchApprovals(which: "pending" | "decided", pageNum: number) {
    setLoading(true);
    setError(null);
    try {
      const statuses = which === "pending" ? ["pending"] : [...DECIDED_STATUSES];
      const results = await Promise.all(
        statuses.map((status) =>
          api
            .get("/approvals", {
              params: { status, page: pageNum, per_page: PER_PAGE },
              timeout: 10000,
            })
            .then((r) => extractPage(r.data)),
        ),
      );
      // Deduplicate by id in case statuses overlap server-side
      const seen = new Set<string>();
      const merged = results
        .flatMap((r) => r.items)
        .filter((item) => {
          if (seen.has(item.id)) return false;
          seen.add(item.id);
          return true;
        });
      setItems(merged);
      setTotal(results.reduce((acc, r) => acc + r.total, 0));
      setPages(Math.max(1, ...results.map((r) => r.pages)));
      if (which === "pending") setPendingTotal(results[0].total);
    } catch (e: unknown) {
      // Explicit error state: never render "No pending approvals" on a failed fetch.
      setItems([]);
      setTotal(0);
      setPages(1);
      setError(extractApiError(e, "Failed to load approvals"));
    } finally {
      setLoading(false);
    }
  }

  async function handleDecide(id: string, decision: string, notes: string) {
    setFeedback(null);
    try {
      await api.post(`/approvals/${id}/decide`, { decision, notes });
      setFeedback({ type: "success", msg: `Decision "${decision}" submitted successfully.` });
      fetchApprovals(tab, page);
    } catch (e: unknown) {
      setFeedback({ type: "error", msg: extractApiError(e, "Failed to submit decision") });
    }
  }

  const now = new Date();
  const displayed =
    tab === "pending"
      ? items.filter((i) => i.status === "pending" && (!i.expires_at || new Date(i.expires_at) > now))
      : items.filter((i) => i.status !== "pending");
  const filtered = displayed.filter(
    (i) => priorityFilter === "all" || i.priority === priorityFilter
  );
  const pendingCount = pendingTotal ?? (tab === "pending" ? total : 0);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Approval Queue</h2>
        <Badge variant="destructive">{pendingCount} pending</Badge>
      </div>

      <div className="flex gap-4 items-center border-b pb-2">
        <button onClick={() => switchTab("pending")} className={`px-3 py-1 text-sm font-medium ${tab === "pending" ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
          Pending{tab === "pending" ? ` (${total})` : pendingTotal !== null ? ` (${pendingTotal})` : ""}
        </button>
        <button onClick={() => switchTab("decided")} className={`px-3 py-1 text-sm font-medium ${tab === "decided" ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
          Decided{tab === "decided" ? ` (${total})` : ""}
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
      ) : error ? (
        <div
          className="rounded-lg px-4 py-3 text-sm bg-red-50 text-red-800 border border-red-200 flex items-center justify-between"
          data-testid="approvals-error"
        >
          <span>Failed to load approvals: {error}</span>
          <Button variant="outline" size="sm" onClick={() => fetchApprovals(tab, page)}>Retry</Button>
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-muted-foreground">{tab === "pending" ? "No pending approvals." : "No decided items."}</p>
      ) : (
        <div className="space-y-4">
          {filtered.map((item) => (
            <ApprovalCard key={item.id} item={item} onDecide={handleDecide} readonly={tab === "decided"} />
          ))}
        </div>
      )}

      {!loading && !error && (total > PER_PAGE || pages > 1) && (
        <div className="flex items-center justify-between text-sm text-muted-foreground" data-testid="approvals-pagination">
          <span>
            Showing {displayed.length} of {total} on page {page} of {pages}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              Previous
            </Button>
            <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
