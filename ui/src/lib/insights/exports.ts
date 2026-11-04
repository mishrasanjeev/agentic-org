// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Background exports (POST /insights/exports): files the worker makes when an
 * inline export would be too large, run bundles and (with the packs) PDFs and
 * packs. Files are listed on the Downloads page for seven days and fetched
 * through the API (GET /insights/exports/{id}/download), never a signed URL.
 */
import api from "@/lib/api";

export type ExportKind =
  | "metric"
  | "explorer"
  | "run_bundle"
  | "view_pdf"
  | "view_pptx"
  | "finance_pack"
  | "evidence_pack"
  | "partner_pack";
export type ExportStatus = "queued" | "running" | "ready" | "failed" | "expired";

export interface ExportJob {
  id: string;
  kind: ExportKind;
  format: string;
  key: string | null;
  period: { from: string | null; to: string | null };
  include_pii: boolean;
  status: ExportStatus;
  error: string | null;
  row_count: number | null;
  size_bytes: number | null;
  sha256: string | null;
  filename: string | null;
  created_at: string | null;
  finished_at: string | null;
  expires_at: string | null;
  signed_at: string | null;
  signature: Record<string, unknown> | null;
  /** An evidence pack's verification: verified, signatures_verified or broken. */
  verification_status: string | null;
  download: string | null;
}

export interface ExportRequest {
  kind: ExportKind;
  format: string;
  params: Record<string, unknown>;
  include_pii?: boolean;
}

const STATUSES: readonly ExportStatus[] = ["queued", "running", "ready", "failed", "expired"];

function normaliseJob(raw: unknown): ExportJob | null {
  if (!raw || typeof raw !== "object") return null;
  const j = raw as Record<string, unknown>;
  if (typeof j.id !== "string") return null;
  const str = (v: unknown) => (typeof v === "string" ? v : null);
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const period = (j.period && typeof j.period === "object" ? j.period : {}) as Record<string, unknown>;
  return {
    id: j.id,
    kind: (str(j.kind) ?? "metric") as ExportKind,
    format: str(j.format) ?? "",
    key: str(j.key),
    period: { from: str(period.from), to: str(period.to) },
    include_pii: j.include_pii === true,
    status: (STATUSES.includes(j.status as ExportStatus) ? j.status : "failed") as ExportStatus,
    error: str(j.error),
    row_count: num(j.row_count),
    size_bytes: num(j.size_bytes),
    sha256: str(j.sha256),
    filename: str(j.filename),
    created_at: str(j.created_at),
    finished_at: str(j.finished_at),
    expires_at: str(j.expires_at),
    signed_at: str(j.signed_at),
    signature: j.signature && typeof j.signature === "object" ? (j.signature as Record<string, unknown>) : null,
    verification_status: str(j.verification_status),
    download: str(j.download),
  };
}

/** True while the worker still has work to do on any of these jobs. */
export function anyPending(jobs: readonly ExportJob[]): boolean {
  return jobs.some((j) => j.status === "queued" || j.status === "running");
}

export const exportsApi = {
  async create(request: ExportRequest): Promise<ExportJob> {
    const { data } = await api.post("/insights/exports", { include_pii: false, ...request });
    const job = normaliseJob((data as { export?: unknown })?.export);
    if (!job) throw new Error("export_response_invalid");
    return job;
  },
  async list(): Promise<{ jobs: ExportJob[]; storage: boolean }> {
    const { data } = await api.get("/insights/exports");
    const raw = (data as { exports?: unknown; storage?: unknown }) ?? {};
    const jobs = Array.isArray(raw.exports) ? raw.exports.map(normaliseJob).filter((j): j is ExportJob => j !== null) : [];
    return { jobs, storage: raw.storage !== false };
  },
  /** An auditor or administrator signs a ready evidence pack (once). */
  async sign(id: string, statement: string): Promise<ExportJob> {
    const { data } = await api.post(`/insights/exports/${encodeURIComponent(id)}/sign`, { statement });
    const job = normaliseJob((data as { export?: unknown })?.export);
    if (!job) throw new Error("export_response_invalid");
    return job;
  },
  async download(job: ExportJob): Promise<Blob> {
    const { data } = await api.get(`/insights/exports/${encodeURIComponent(job.id)}/download`, {
      responseType: "blob",
    });
    return data as Blob;
  },
};

/** Whether an error body (JSON or a Blob of JSON) says the export may be made in the background instead. */
export async function offersBackground(error: unknown): Promise<boolean> {
  const data = (error as { response?: { data?: unknown; status?: number } })?.response;
  if (!data || data.status !== 413) return false;
  let body: unknown = data.data;
  if (typeof Blob !== "undefined" && body instanceof Blob) {
    try {
      body = JSON.parse(await body.text());
    } catch {
      return false;
    }
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  return Boolean(detail && typeof detail === "object" && (detail as { background?: unknown }).background === true);
}
