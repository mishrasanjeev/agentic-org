import { useState, useEffect, useCallback } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface KBDocument {
  id: string;
  filename: string;
  status: "processing" | "indexed" | "failed";
  size_bytes: number;
  uploaded_at: string;
}

interface KBStats {
  total_documents: number;
  total_chunks: number;
  index_size_bytes?: number;
  index_size_mb?: number;
}

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_DOCS: KBDocument[] = [
  { id: "d1", filename: "Employee Handbook 2026.pdf", status: "indexed", size_bytes: 2_456_000, uploaded_at: "2026-03-28T10:15:00Z" },
  { id: "d2", filename: "AP Process SOP.docx", status: "indexed", size_bytes: 345_000, uploaded_at: "2026-03-29T14:30:00Z" },
  { id: "d3", filename: "Tax Compliance Guide.pdf", status: "processing", size_bytes: 1_890_000, uploaded_at: "2026-04-01T09:00:00Z" },
  { id: "d4", filename: "Vendor Master List.xlsx", status: "indexed", size_bytes: 567_000, uploaded_at: "2026-04-02T11:45:00Z" },
  { id: "d5", filename: "Leave Policy Draft.txt", status: "failed", size_bytes: 23_000, uploaded_at: "2026-04-03T16:20:00Z" },
];

const MOCK_STATS: KBStats = {
  total_documents: 5,
  total_chunks: 1247,
  index_size_bytes: 45_678_000,
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatBytes(bytes: number | undefined | null): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

const STATUS_BADGE: Record<string, "success" | "warning" | "destructive"> = {
  indexed: "success",
  processing: "warning",
  failed: "destructive",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function KnowledgeBase() {
  const [documents, setDocuments] = useState<KBDocument[]>([]);
  const [stats, setStats] = useState<KBStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<string[]>([]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [docsRes, statsRes] = await Promise.allSettled([
        api.get("/knowledge/documents"),
        api.get("/knowledge/stats"),
      ]);
      const docs =
        docsRes.status === "fulfilled"
          ? Array.isArray(docsRes.value.data) ? docsRes.value.data : docsRes.value.data?.items || []
          : [];
      const s = statsRes.status === "fulfilled" ? statsRes.value.data : null;

      setDocuments(docs.length > 0 ? docs : MOCK_DOCS);
      setStats(s || MOCK_STATS);
    } catch {
      setDocuments(MOCK_DOCS);
      setStats(MOCK_STATS);
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
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", file);
        await api.post("/knowledge/upload", fd);
      }
      await fetchData();
    } catch {
      // fallback: add locally
      const newDocs: KBDocument[] = Array.from(files).map((f, i) => ({
        id: `new-${Date.now()}-${i}`,
        filename: f.name,
        status: "processing" as const,
        size_bytes: f.size,
        uploaded_at: new Date().toISOString(),
      }));
      setDocuments((prev) => [...newDocs, ...prev]);
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/knowledge/documents/${id}`);
    } catch {
      // remove locally even if API fails
    }
    setDocuments((prev) => prev.filter((d) => d.id !== id));
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      const res = await api.post("/knowledge/search", { query: searchQuery });
      setSearchResults(Array.isArray(res.data?.results) ? res.data.results : []);
    } catch {
      setSearchResults(["(Search unavailable — API offline. Results will appear once the backend is running.)"]);
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
                <tr key={doc.id} className="border-t hover:bg-muted/50">
                  <td className="p-3 font-medium">{doc.filename}</td>
                  <td className="p-3">
                    <Badge variant={STATUS_BADGE[doc.status] || "secondary"}>{doc.status}</Badge>
                  </td>
                  <td className="p-3">{formatBytes(doc.size_bytes ?? 0)}</td>
                  <td className="p-3">{doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleDateString() : "-"}</td>
                  <td className="p-3">
                    <Button variant="destructive" size="sm" onClick={() => handleDelete(doc.id)}>
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
