// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";

/**
 * Document review: processed files with their typed documents, each field drawn on the page it came
 * from with a colour for its confidence; a reviewer corrects a field and approves or rejects the file.
 */

interface FieldRow {
  name: string;
  value: string | null;
  original_value?: string | null;
  confidence: number;
  page: number | null;
  bbox: number[] | null;
  status: string;
  required: boolean;
  corrected?: boolean;
  corrected_by?: string | null;
}

interface TableRow {
  page: number;
  bbox: number[];
  header: string[];
  rows: string[][];
}

interface DocumentPart {
  index: number;
  document_type: string;
  confidence: number;
  pages: number[];
  fields: FieldRow[];
  extra_fields: FieldRow[];
  tables: TableRow[];
  review: { needed: boolean; reasons: string[] };
}

interface Summary {
  id: string;
  filename: string;
  status: string;
  pages: number;
  document_types: string[];
  review_reasons: string[];
  corrections: number;
  created_at: string | null;
}

interface Detail extends Summary {
  pages_detail: Array<{ number: number; width: number; height: number; source: string; ocr: string }>;
  documents: DocumentPart[];
  image_dpi: number;
}

export function confidenceTone(field: FieldRow): string {
  if (field.status === "missing") return "missing";
  if (field.corrected) return "corrected";
  if (field.confidence >= 0.7) return "high";
  return "low";
}

export function boxStyle(bbox: number[], page: { width: number; height: number }, rendered: { width: number; height: number }) {
  const sx = rendered.width / Math.max(1, page.width);
  const sy = rendered.height / Math.max(1, page.height);
  return { left: bbox[0] * sx, top: bbox[1] * sy, width: Math.max(2, (bbox[2] - bbox[0]) * sx), height: Math.max(2, (bbox[3] - bbox[1]) * sy) };
}

const TONE_CLASS: Record<string, string> = {
  high: "border-emerald-500",
  low: "border-amber-500",
  missing: "border-red-500",
  corrected: "border-indigo-500",
};

export default function Documents() {
  const [status, setStatus] = useState<string>("review");
  const [documents, setDocuments] = useState<Summary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [page, setPage] = useState(1);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setError(null);
    try {
      const params: Record<string, string> = { limit: "50" };
      if (status) params.status = status;
      const { data } = await api.get("/idp/documents", { params });
      setDocuments((data as { documents: Summary[] }).documents);
    } catch (err) {
      setError(extractApiError(err, "Failed to load documents."));
    }
  }, [status]);

  const loadDetail = useCallback(async (id: string) => {
    setError(null);
    try {
      const { data } = await api.get(`/idp/documents/${id}`);
      setDetail(data as Detail);
      setPage(1);
      setEdits({});
      setFocus(null);
    } catch (err) {
      setDetail(null);
      setError(extractApiError(err, "Failed to load the document."));
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (selected) void loadDetail(selected);
  }, [selected, loadDetail]);

  useEffect(() => {
    if (!detail) return;
    let revoked = false;
    let url: string | null = null;
    api
      .get(`/idp/documents/${detail.id}/pages/${page}.png`, { responseType: "blob" })
      .then((res) => {
        if (revoked) return;
        url = URL.createObjectURL(res.data as Blob);
        setImageUrl(url);
      })
      .catch((err) => setError(extractApiError(err, "Failed to render the page.")));
    return () => {
      revoked = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [detail, page]);

  const pageInfo = useMemo(() => detail?.pages_detail.find((p) => p.number === page) ?? null, [detail, page]);

  const saveField = async (part: DocumentPart, field: FieldRow) => {
    if (!detail) return;
    const key = `${part.index}:${field.name}`;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post(`/idp/documents/${detail.id}/fields`, {
        document_index: part.index,
        field: field.name,
        value: edits[key] ?? "",
      });
      setDetail(data as Detail);
      setNotice(`Saved ${field.name}.`);
    } catch (err) {
      setError(extractApiError(err, "Failed to save the correction."));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (decision: "approve" | "reject") => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/idp/documents/${detail.id}/decide`, { decision, notes: "" });
      setNotice(decision === "approve" ? "Approved." : "Rejected.");
      await loadList();
      await loadDetail(detail.id);
    } catch (err) {
      setError(extractApiError(err, `Failed to ${decision}.`));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 p-4">
      <Helmet>
        <title>Documents</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-slate-900">Documents</h1>
        <label className="text-sm text-slate-700">
          Status
          <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={status} onChange={(e) => setStatus(e.target.value)} data-testid="documents-status">
            <option value="review">Needs review</option>
            <option value="processed">Processed</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="">All</option>
          </select>
        </label>
      </div>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="documents-notice">
          {notice}
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-3">
          <ul className="space-y-1" data-testid="documents-list">
            {documents.length === 0 && (
              <li className="text-sm text-slate-500" data-testid="documents-empty">
                No documents here.
              </li>
            )}
            {documents.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className={`w-full rounded-md border px-2 py-1 text-left text-sm ${row.id === selected ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"}`}
                  onClick={() => setSelected(row.id)}
                  data-testid={`document-row-${row.id}`}
                >
                  <span className="font-medium text-slate-800">{row.filename}</span>
                  <span className="ml-1 text-xs text-slate-500">
                    {row.document_types.join(", ") || "untyped"} · {row.pages} page{row.pages === 1 ? "" : "s"} · {row.status}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="lg:col-span-5">
          {detail && pageInfo && (
            <div className="rounded-lg border border-slate-200 bg-white p-2" data-testid="document-page">
              <div className="mb-1 flex items-center justify-between text-xs text-slate-600">
                <span>
                  Page {page} of {detail.pages} · {pageInfo.source}
                  {pageInfo.ocr === "unavailable" ? " · OCR unavailable" : ""}
                </span>
                <span className="flex gap-1">
                  <button type="button" className="rounded border border-slate-300 px-2" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} data-testid="page-prev">
                    ‹
                  </button>
                  <button type="button" className="rounded border border-slate-300 px-2" disabled={page >= detail.pages} onClick={() => setPage((p) => p + 1)} data-testid="page-next">
                    ›
                  </button>
                </span>
              </div>
              <div className="relative inline-block">
                {imageUrl && (
                  <img
                    src={imageUrl}
                    alt={`Page ${page}`}
                    className="max-w-full"
                    onLoad={(e) => setImageSize({ width: e.currentTarget.clientWidth, height: e.currentTarget.clientHeight })}
                    data-testid="page-image"
                  />
                )}
                {imageSize &&
                  detail.documents.flatMap((part) =>
                    [...part.fields, ...part.extra_fields]
                      .filter((f) => f.page === page && f.bbox)
                      .map((f) => {
                        const key = `${part.index}:${f.name}`;
                        const style = boxStyle(f.bbox as number[], pageInfo, imageSize);
                        return (
                          <div
                            key={key}
                            className={`absolute border-2 ${TONE_CLASS[confidenceTone(f)]} ${focus === key ? "bg-indigo-200/40" : "bg-transparent"}`}
                            style={style}
                            title={`${f.name}: ${f.value ?? ""} (${Math.round(f.confidence * 100)}%)`}
                            onClick={() => setFocus(key)}
                            data-testid={`box-${key}`}
                          />
                        );
                      }),
                  )}
                {imageSize &&
                  detail.documents.flatMap((part) =>
                    part.tables
                      .filter((t) => t.page === page)
                      .map((t, i) => (
                        <div key={`table-${part.index}-${i}`} className="absolute border border-dashed border-sky-500" style={boxStyle(t.bbox, pageInfo, imageSize)} data-testid={`table-box-${part.index}-${i}`} />
                      )),
                  )}
              </div>
            </div>
          )}
        </div>
        <div className="lg:col-span-4">
          {detail && (
            <div className="space-y-3" data-testid="document-fields">
              {detail.review_reasons.length > 0 && (
                <ul className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="document-reasons">
                  {detail.review_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
              {detail.documents.map((part) => (
                <div key={part.index} className="rounded-lg border border-slate-200 bg-white p-3">
                  <div className="mb-2 text-sm font-medium text-slate-800">
                    {part.document_type} · {Math.round(part.confidence * 100)}% · pages {part.pages.join(", ")}
                  </div>
                  <ul className="space-y-1">
                    {[...part.fields, ...part.extra_fields].map((f) => {
                      const key = `${part.index}:${f.name}`;
                      const tone = confidenceTone(f);
                      return (
                        <li key={key} className={`rounded-md border px-2 py-1 text-xs ${focus === key ? "border-indigo-400" : "border-slate-100"}`} data-testid={`field-${key}`}>
                          <div className="flex items-center justify-between gap-2">
                            <button type="button" className="font-medium text-slate-800" onClick={() => { setFocus(key); if (f.page) setPage(f.page); }} data-testid={`field-focus-${key}`}>
                              {f.name}
                            </button>
                            <span className={`rounded px-1 ${tone === "high" ? "bg-emerald-100 text-emerald-800" : tone === "low" ? "bg-amber-100 text-amber-800" : tone === "missing" ? "bg-red-100 text-red-800" : "bg-indigo-100 text-indigo-800"}`} data-testid={`tone-${key}`}>
                              {tone === "missing" ? "missing" : tone === "corrected" ? "corrected" : `${Math.round(f.confidence * 100)}%`}
                            </span>
                          </div>
                          {detail.status === "review" || detail.status === "processed" ? (
                            <div className="mt-1 flex gap-1">
                              <input
                                className="flex-1 rounded-md border border-slate-300 px-1 py-0.5 text-xs"
                                value={edits[key] ?? f.value ?? ""}
                                onChange={(e) => setEdits((prev) => ({ ...prev, [key]: e.target.value }))}
                                data-testid={`field-input-${key}`}
                              />
                              <button type="button" className="rounded-md border border-slate-300 px-2 text-xs hover:bg-slate-50 disabled:opacity-50" disabled={busy} onClick={() => void saveField(part, f)} data-testid={`field-save-${key}`}>
                                Save
                              </button>
                            </div>
                          ) : (
                            <div className="mt-1 text-slate-700">{f.value ?? "–"}</div>
                          )}
                          {f.corrected && f.original_value !== undefined && <div className="text-[10px] text-slate-500">extracted: {f.original_value ?? "–"}</div>}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
              {(detail.status === "review" || detail.status === "processed") && (
                <div className="flex gap-2">
                  <button type="button" className="rounded-md bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-50" disabled={busy} onClick={() => void decide("approve")} data-testid="document-approve">
                    Approve
                  </button>
                  <button type="button" className="rounded-md border border-red-300 px-3 py-1 text-sm text-red-700 disabled:opacity-50" disabled={busy} onClick={() => void decide("reject")} data-testid="document-reject">
                    Reject
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
