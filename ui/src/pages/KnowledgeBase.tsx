import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface KBDocument {
  // TC_005 fix: backend returns `document_id`, frontend used `id` → every
  // row had `id === undefined`, so `filter(d => d.id !== id)` kept nothing
  // on delete (all records dropped from the UI). Now we read the canonical
  // field from the backend directly.
  document_id: string;
  filename: string;
  status: "processing" | "indexed" | "failed";
  size_bytes: number;
  // TC_010 fix: backend was returning `created_at`, UI rendered
  // `doc.uploaded_at` (undefined) → "-". Backend now sends both fields;
  // we read the user-facing name.
  uploaded_at: string;
  created_at?: string;
}

interface KBStats {
  total_documents: number;
  total_chunks: number;
  index_size_bytes?: number;
  index_size_mb?: number;
}


/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatBytes(bytes: number | undefined | null): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function formatUploadedDate(iso: string | null | undefined): string {
  // TC_010/TC_015-style guard: never render `new Date(null)`, which
  // produces an epoch-1970 string; fall back to a neutral dash instead.
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

// Status badges for the three canonical states. Backend normalises the
// legacy "ready" label to "indexed" (see _normalize_status in
// api/v1/knowledge.py) so we don't have to keep a fallback here.
const STATUS_BADGE: Record<
  KBDocument["status"],
  "success" | "warning" | "destructive"
> = {
  indexed: "success",
  processing: "warning",
  failed: "destructive",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

// Codex 2026-04-22 audit: window.confirm only has OK/Cancel, so the
// "upload duplicate" flow forced Cancel to mean "add a second copy",
// which users hit by accident. A small inline picker lets Cancel mean
// abort (safe default) and splits the real choice into Replace vs Keep
// both.
type DuplicateAction = "replace" | "keep_both" | "cancel";

function DuplicateDecisionModal({
  filename,
  onDecide,
}: {
  filename: string;
  onDecide: (action: DuplicateAction) => void;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="dup-dialog-title"
    >
      <div className="bg-white rounded-lg shadow-lg max-w-md w-full p-6">
        <h3 id="dup-dialog-title" className="text-lg font-semibold mb-2">
          Duplicate file
        </h3>
        <p className="text-sm text-slate-600 mb-4">
          <span className="font-medium">&ldquo;{filename}&rdquo;</span> is
          already in the knowledge base. What would you like to do?
        </p>
        <div className="flex flex-col gap-2">
          <Button onClick={() => onDecide("replace")} className="w-full">
            Replace existing (old version is deleted + reindexed)
          </Button>
          <Button
            onClick={() => onDecide("keep_both")}
            variant="outline"
            className="w-full"
          >
            Keep both (upload as a second copy with the same name)
          </Button>
          <Button
            onClick={() => onDecide("cancel")}
            variant="ghost"
            className="w-full"
          >
            Cancel (do not upload)
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function KnowledgeBase() {
  // Codex 2026-04-22 i18n tripwire: the page must surface through the
  // language switcher. Individual strings are wrapped in follow-up PRs.
  const { t } = useTranslation();
  void t;
  const [documents, setDocuments] = useState<KBDocument[]>([]);
  const [stats, setStats] = useState<KBStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<string[]>([]);
  // TC_002 (Aishwarya 2026-04-23): surface real backend errors to the user
  // instead of a blanket "API offline" line.
  const [searchError, setSearchError] = useState<string | null>(null);
  // Modal state for the duplicate-file decision flow.
  const [dupPrompt, setDupPrompt] = useState<{
    filename: string;
    resolve: (action: DuplicateAction) => void;
  } | null>(null);

  const askDuplicateDecision = useCallback(
    (filename: string) =>
      new Promise<DuplicateAction>((resolve) => {
        setDupPrompt({
          filename,
          resolve: (action) => {
            setDupPrompt(null);
            resolve(action);
          },
        });
      }),
    [],
  );

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [docsRes, statsRes] = await Promise.allSettled([
        api.get("/knowledge/documents"),
        api.get("/knowledge/stats"),
      ]);
      const rawDocs =
        docsRes.status === "fulfilled"
          ? Array.isArray(docsRes.value.data) ? docsRes.value.data : docsRes.value.data?.items || []
          : [];
      // Normalise on read so the rest of the component can assume
      // document_id and uploaded_at are present even if a given server
      // build still ships the legacy field shape.
      const docs: KBDocument[] = rawDocs.map(
        (d: Record<string, unknown>): KBDocument => ({
          document_id: String(d.document_id ?? d.id ?? ""),
          filename: String(d.filename ?? d.name ?? ""),
          status: (["processing", "indexed", "failed"].includes(
            d.status as string,
          )
            ? d.status
            : "indexed") as KBDocument["status"],
          size_bytes: Number(d.size_bytes ?? 0),
          uploaded_at: String(
            d.uploaded_at ?? d.created_at ?? "",
          ),
          created_at: typeof d.created_at === "string" ? d.created_at : undefined,
        }),
      );
      const s = statsRes.status === "fulfilled" ? statsRes.value.data : null;

      setDocuments(docs);
      setStats(s || null);
    } catch {
      setDocuments([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  /* ---- Upload ---- */

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const existing = new Set(
        documents.map((d) => d.filename.toLowerCase()),
      );
      for (const file of Array.from(files)) {
        // TC_011 client-side dedup + Codex 2026-04-22 audit (F4):
        // the prior window.confirm() flow forced Cancel to mean "upload
        // as a second copy", which trapped users. The 3-way modal
        // (DuplicateDecisionModal above) lets Cancel mean abort and
        // splits Replace (real overwrite via ?replace=true) from
        // Keep-both (adds a second doc via ?allow_duplicate=true).
        const isDuplicate = existing.has(file.name.toLowerCase());
        let action: DuplicateAction = "replace";
        if (isDuplicate) {
          action = await askDuplicateDecision(file.name);
          if (action === "cancel") continue;
        } else {
          action = "keep_both"; // not actually a duplicate — just normal upload
        }
        const fd = new FormData();
        fd.append("file", file);
        try {
          let url = "/knowledge/upload";
          if (isDuplicate && action === "replace") {
            url += "?replace=true";
          } else if (isDuplicate && action === "keep_both") {
            url += "?allow_duplicate=true";
          }
          await api.post(url, fd);
          existing.add(file.name.toLowerCase());
        } catch (err: unknown) {
          // If the server rejects a specific file (e.g. 409), keep going
          // with the rest instead of aborting the whole batch.
          const status = (err as { response?: { status?: number } })?.response
            ?.status;
          if (status === 409) {
            window.alert(
              `"${file.name}" already exists on the server. ` +
                "Re-select the file and choose Replace in the prompt to overwrite it.",
            );
            continue;
          }
          throw err;
        }
      }
      await fetchData();
    } catch (err: unknown) {
      // TC_007 (Aishwarya 2026-04-21): the previous catch optimistically
      // added EVERY file in the batch to local state as
      // status="processing", which is how a cancelled-duplicate file
      // ended up appearing in the list even though the user had
      // declined to upload it. A failure here must NOT fabricate
      // local entries — it must surface as an error and leave the
      // last-known-good server state (already fetched above or below)
      // as the source of truth.
      const status = (err as { response?: { status?: number } })?.response?.status;
      const message = status
        ? `Upload failed (HTTP ${status}). Please try again.`
        : "Upload failed. Please try again.";
      window.alert(message);
      // Refetch so the UI stays consistent with the server even after
      // a partial failure.
      try {
        await fetchData();
      } catch {
        /* fetchData already logs and resets state */
      }
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (documentId: string) => {
    // TC_005 fix: previously we filtered local state optimistically on
    // `d.id !== id`, but frontend docs had no `id` (only `document_id`),
    // so `undefined !== undefined` was always false → every row fell out
    // of the filter. Now we pass the canonical id, await the response,
    // and refetch from the server when the DELETE succeeds. The refetch
    // ensures an eventual-consistency outcome: if the backend reports a
    // failure, the document reappears naturally.
    if (!documentId) return;
    try {
      await api.delete(`/knowledge/documents/${documentId}`);
    } catch (err) {
      // Surface the failure but don't mutate local state — refetch will
      // reconcile with whatever the server currently believes.
      console.error("delete failed", err);
    }
    await fetchData();
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearchError(null);
    try {
      const res = await api.post("/knowledge/search", { query: searchQuery });
      const results = Array.isArray(res.data?.results) ? res.data.results : [];
      setSearchResults(results);
      // TC_002 (Aishwarya 2026-04-23): when the backend returns zero
      // results, show a dedicated empty state — not the previous
      // "API offline" line, which misled testers into filing bugs
      // against a working search.
      if (results.length === 0) {
        setSearchError(t("knowledge.search.noResults", "No matching chunks in the knowledge base for this query."));
      }
    } catch (e) {
      // TC_002 (Aishwarya 2026-04-23): the bare catch previously
      // showed "(Search unavailable — API offline...)" for ANY error,
      // which is why the tester filed "Something went wrong". Surface
      // the backend's structured error detail so testers and operators
      // see what actually failed.
      setSearchResults([]);
      setSearchError(extractApiError(e, t("knowledge.search.failed", "Search failed — please try again.")));
    }
  };

  /* ---- Drag & drop handlers ---- */

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") setDragActive(true);
    else if (e.type === "dragleave") setDragActive(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    handleUpload(e.dataTransfer.files);
  };

  /* ---- Render ---- */

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading knowledge base...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {dupPrompt && (
        <DuplicateDecisionModal
          filename={dupPrompt.filename}
          onDecide={dupPrompt.resolve}
        />
      )}
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Knowledge Base</h2>
        <Button variant="outline" onClick={fetchData}>Refresh</Button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-3 gap-4">
          <Card>
            <CardHeader><CardTitle className="text-sm text-muted-foreground">Total Documents</CardTitle></CardHeader>
            <CardContent><p className="text-3xl font-bold">{stats.total_documents ?? 0}</p></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm text-muted-foreground">Total Chunks</CardTitle></CardHeader>
            <CardContent><p className="text-3xl font-bold">{(stats.total_chunks ?? 0).toLocaleString()}</p></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm text-muted-foreground">Index Size</CardTitle></CardHeader>
            <CardContent><p className="text-3xl font-bold">{stats.index_size_mb != null ? `${stats.index_size_mb.toFixed(1)} MB` : formatBytes(stats.index_size_bytes)}</p></CardContent>
          </Card>
        </div>
      )}

      {/* Upload area */}
      <div
        onDragEnter={handleDrag}
        onDragOver={handleDrag}
        onDragLeave={handleDrag}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
          dragActive ? "border-primary bg-primary/5" : "border-muted-foreground/30"
        }`}
      >
        <div className="flex flex-col items-center gap-2">
          <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <p className="text-sm text-muted-foreground">
            {uploading ? "Uploading..." : "Drag & drop files here, or click to browse"}
          </p>
          <p className="text-xs text-muted-foreground">Supported: PDF, Word, Excel, TXT</p>
          <label className="mt-2">
            <input
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.xls,.xlsx,.txt"
              className="hidden"
              onChange={(e) => handleUpload(e.target.files)}
            />
            <span className="inline-flex items-center justify-center rounded-md font-medium transition-colors h-9 px-3 text-sm border border-border bg-background hover:bg-accent cursor-pointer">
              Browse Files
            </span>
          </label>
        </div>
      </div>

      {/* Search */}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Test a query against the knowledge base..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          className="flex-1 border rounded px-3 py-2 text-sm"
        />
        <Button onClick={handleSearch} size="sm">Search</Button>
      </div>
      {searchResults.length > 0 && (
        <div className="border rounded-lg p-4 bg-muted/20 space-y-2">
          <h3 className="text-sm font-semibold">Search Results</h3>
          {searchResults.map((r, i) => (
            <p key={i} className="text-sm text-muted-foreground">{r}</p>
          ))}
        </div>
      )}
      {searchError && searchResults.length === 0 && (
        <div
          className="border border-amber-200 bg-amber-50 text-amber-900 rounded-lg p-3 text-sm"
          role="alert"
          aria-live="polite"
          data-testid="kb-search-error"
        >
          {searchError}
        </div>
      )}

      {/* Document table */}
      {documents.length === 0 ? (
        <p className="text-muted-foreground text-sm">No documents uploaded yet.</p>
      ) : (
        <div className="border rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="text-left p-3">Filename</th>
                <th className="text-left p-3">Status</th>
                <th className="text-left p-3">Size</th>
                <th className="text-left p-3">Uploaded</th>
                <th className="text-left p-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.document_id} className="border-t hover:bg-muted/50">
                  <td className="p-3 font-medium">{doc.filename}</td>
                  <td className="p-3">
                    <Badge variant={STATUS_BADGE[doc.status] || "secondary"}>{doc.status}</Badge>
                  </td>
                  <td className="p-3">{formatBytes(doc.size_bytes ?? 0)}</td>
                  <td className="p-3">{formatUploadedDate(doc.uploaded_at)}</td>
                  <td className="p-3">
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => handleDelete(doc.document_id)}
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
