// SPDX-License-Identifier: Apache-2.0
/**
 * Typed helpers for the governed case API (`/api/v1/governed-cases`).
 *
 * The backend is the authority for every rule shown here: tenancy, the
 * `governed_cases.enabled` flag, scopes and the decision-grant check. These
 * helpers only type the documents (the published JSON Schemas under
 * `schemas/`) and turn the API's `{error: {reason, detail}}` refusals into a
 * stable reason code the screens can explain.
 */
import api from "@/lib/api";

export type CaseState = "submitted" | "in_progress" | "awaiting_decision" | "decided" | "withdrawn" | "failed";
export type RiskTier = "low" | "medium" | "high" | "blocked";
export type Recommendation = "approve" | "decline" | "refer" | "request_information";
export type SectionId = "identity" | "registry" | "ownership" | "screening" | "web_presence" | "activity";
export type SectionStatus = "complete" | "partial" | "not_available" | "error";
export type Severity = "info" | "low" | "medium" | "high";

export const CASE_STATES: readonly CaseState[] = [
  "awaiting_decision",
  "in_progress",
  "submitted",
  "failed",
  "decided",
  "withdrawn",
];

export interface Evidence {
  provider: string;
  record_id: string;
  field: string;
  retrieved_at: string;
  excerpt_ref: string | null;
}

export interface Finding {
  code: string;
  severity: Severity;
  statement: string;
  evidence: Evidence[];
}

export interface MemoSection {
  section_id: SectionId;
  status: SectionStatus;
  not_available_reason?: "capability_not_supported" | "no_registry_match";
  error_reason?: string;
  findings: Finding[];
  evidence: Evidence[];
}

export interface Excerpt {
  excerpt_ref: string;
  provider: string;
  record_id: string;
  media_type: string;
  sha256: string;
}

export interface PolicyReason {
  rule_id: string;
  tier: RiskTier;
  reason: string;
  score?: number | null;
  inputs: Record<string, string | number | boolean | null>;
}

export interface PolicyResult {
  schema_version: string;
  policy: { policy_id: string; version: string; example: boolean; reviewed_by: string | null };
  inputs_digest: string;
  score: number;
  tier: RiskTier;
  reasons: PolicyReason[];
}

export interface BusinessRef {
  provider: string;
  provider_ref: string;
  jurisdiction: string;
  identifiers?: { scheme: string; value: string }[];
}

export interface UnderwritingMemo {
  schema_version: string;
  memo_id: string;
  case_id: string;
  created_at: string;
  subject: BusinessRef | null;
  sections: MemoSection[];
  policy_result: PolicyResult;
  recommendation: { proposed: Recommendation; basis: "policy_result"; requires_human_decision: true };
  missing_items: { item: string; reason: string }[];
  excerpts: Excerpt[];
  provenance: {
    agent: string;
    agent_version: string;
    prompt_version: string;
    model_id: string;
    model_confidence?: number | null;
  };
}

export interface CaseDecision {
  outcome: "approve" | "decline";
  approvers: { approver: string; decision_grant_id: string }[];
  decided_at: string;
}

export interface BusinessCase {
  schema_version: string;
  case_id: string;
  purpose: string;
  state: CaseState;
  created_at: string;
  updated_at: string;
  application: {
    legal_name: string;
    jurisdiction: string;
    trading_names?: string[];
    identifiers: { scheme: string; value: string }[];
    declared_owners: { name: string; kind: string; ownership_pct?: number | null }[];
    website?: string | null;
    declared_activity?: string | null;
  };
  subject: BusinessRef | null;
  decision: CaseDecision | null;
}

export interface CaseTransition {
  from_state: CaseState | null;
  to_state: CaseState;
  actor: string;
  reason: string;
  at: string | null;
}

export interface CaseSummary {
  case_ref: string;
  state: CaseState;
  purpose: string;
  legal_name: string | null;
  jurisdiction: string | null;
  recommendation: Recommendation | null;
  tier: RiskTier | null;
  failure_reason: string | null;
  updated_at: string | null;
}

export interface CaseDetail {
  case: BusinessCase;
  memo: UnderwritingMemo | null;
  policy_result: PolicyResult | null;
  // Typed by the screens that render them.
  ownership_graph: unknown;
  screening_results: unknown[];
  screening_dispositions: unknown[];
  parties: unknown[];
  information_requests: unknown[];
  failure_reason: string | null;
  transitions: CaseTransition[];
}

/** A refusal from the governed case API, reduced to its stable reason code. */
export class CaseApiError extends Error {
  readonly reason: string;
  readonly status: number | null;
  readonly detail: string;

  constructor(reason: string, status: number | null, detail = "") {
    super(detail ? `${reason}: ${detail}` : reason);
    this.name = "CaseApiError";
    this.reason = reason;
    this.status = status;
    this.detail = detail;
  }
}

export function toCaseApiError(e: unknown): CaseApiError {
  if (e instanceof CaseApiError) return e;
  const response = (e as { response?: { status?: number; data?: unknown } } | null)?.response;
  if (!response) return new CaseApiError("network_error", null);
  const status = typeof response.status === "number" ? response.status : null;
  const data = response.data as { error?: { reason?: unknown; detail?: unknown }; detail?: unknown } | undefined;
  const reason = data?.error?.reason;
  if (typeof reason === "string" && reason) {
    const detail = data?.error?.detail;
    return new CaseApiError(reason, status, typeof detail === "string" ? detail : "");
  }
  if (status === 422) return new CaseApiError("request_invalid", status);
  if (status === 403) return new CaseApiError("forbidden", status);
  if (status === 404) return new CaseApiError("not_found", status);
  return new CaseApiError(status !== null && status >= 500 ? "server_error" : "request_failed", status);
}

const REASON_MESSAGES: Record<string, string> = {
  governed_cases_disabled: "Governed cases are not enabled for this organisation.",
  case_not_found: "This case does not exist or belongs to another organisation.",
  forbidden: "You do not have permission to view governed cases.",
  not_found: "Not found.",
  network_error: "The server could not be reached. Check your connection and try again.",
  server_error: "The server could not answer. Try again shortly.",
  request_failed: "The request failed.",
  request_invalid: "The request was not valid.",
  case_state_unknown: "That case state filter is not recognised.",
  transition_not_allowed:
    "This action needs the case to be awaiting a decision, and it is in another state now. Reload the case.",
  case_version_conflict: "The case changed while you were working on it. Reload it and try again.",
};

/** A sentence for a refusal. Unknown codes are shown as the code, never hidden. */
export function describeCaseReason(reason: string): string {
  return REASON_MESSAGES[reason] ?? `The request was refused (${reason}).`;
}

async function call<T>(request: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await request()).data;
  } catch (e) {
    throw toCaseApiError(e);
  }
}

export function casePath(caseRef: string, suffix = ""): string {
  return `/governed-cases/${encodeURIComponent(caseRef)}${suffix}`;
}

export const governedCasesApi = {
  list(state?: CaseState, limit = 100): Promise<{ cases: CaseSummary[] }> {
    return call(() => api.get("/governed-cases", { params: { ...(state ? { state } : {}), limit } }));
  },
  stats(): Promise<{ cases_by_state: Record<CaseState, number> }> {
    return call(() => api.get("/governed-cases/stats"));
  },
  get(caseRef: string): Promise<CaseDetail> {
    return call(() => api.get(casePath(caseRef)));
  },
};

export const STATE_LABELS: Record<CaseState, string> = {
  submitted: "Submitted",
  in_progress: "Investigating",
  awaiting_decision: "Awaiting decision",
  decided: "Decided",
  withdrawn: "Withdrawn",
  failed: "Failed",
};

export const TIER_LABELS: Record<RiskTier, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  blocked: "Blocked",
};

export const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  approve: "Approve",
  decline: "Decline",
  refer: "Refer",
  request_information: "Request information",
};

export const SECTION_LABELS: Record<SectionId, string> = {
  identity: "Identity",
  registry: "Registry",
  ownership: "Ownership",
  screening: "Screening",
  web_presence: "Web presence",
  activity: "Declared and observed activity",
};

export const NOT_AVAILABLE_MESSAGES: Record<string, string> = {
  capability_not_supported: "The verification provider does not offer this data, so this section has no findings.",
  no_registry_match: "No registry match was found for the business, so this section could not be produced.",
};

export const ERROR_REASON_MESSAGES: Record<string, string> = {
  provider_timeout: "The provider did not answer in time.",
  provider_unavailable: "The provider was unavailable.",
  provider_rate_limited: "The provider refused the call because of its rate limit.",
  provider_authentication_failed: "The provider refused the platform's credentials.",
  provider_response_invalid: "The provider answered with data that failed validation.",
  invalid_query: "The provider refused the query as invalid.",
  not_found: "The provider has no record for this query.",
};

/** Stable, attribute-safe element ids for in-page citation links. */
export function citationAnchors(memo: UnderwritingMemo): {
  recordId: (provider: string, recordId: string) => string;
  excerptId: (ref: string) => string | null;
} {
  const records = new Map<string, string>();
  const key = (provider: string, recordId: string) => `${provider}${recordId}`;
  for (const evidence of allEvidence(memo)) {
    const k = key(evidence.provider, evidence.record_id);
    if (!records.has(k)) records.set(k, `cited-record-${records.size + 1}`);
  }
  const excerpts = new Map<string, string>();
  memo.excerpts.forEach((excerpt, index) => {
    if (!excerpts.has(excerpt.excerpt_ref)) excerpts.set(excerpt.excerpt_ref, `excerpt-${index + 1}`);
  });
  return {
    recordId: (provider, recordId) => records.get(key(provider, recordId)) ?? "cited-records",
    excerptId: (ref) => excerpts.get(ref) ?? null,
  };
}

/** Every evidence entry in the memo, section evidence first, then each finding's. */
export function allEvidence(memo: UnderwritingMemo): Evidence[] {
  const out: Evidence[] = [];
  for (const section of memo.sections) {
    out.push(...section.evidence);
    for (const finding of section.findings) out.push(...finding.evidence);
  }
  return out;
}

export interface CitedRecord {
  provider: string;
  record_id: string;
  fields: string[];
  sections: SectionId[];
  retrieved_at: string[];
  excerpts: Excerpt[];
}

/** The distinct upstream records the memo cites, in first-cited order. */
export function citedRecords(memo: UnderwritingMemo): CitedRecord[] {
  const byKey = new Map<string, CitedRecord>();
  for (const section of memo.sections) {
    const entries = [...section.evidence, ...section.findings.flatMap((f) => f.evidence)];
    for (const evidence of entries) {
      const k = `${evidence.provider}${evidence.record_id}`;
      let record = byKey.get(k);
      if (!record) {
        record = { provider: evidence.provider, record_id: evidence.record_id, fields: [], sections: [], retrieved_at: [], excerpts: [] };
        byKey.set(k, record);
      }
      if (!record.fields.includes(evidence.field)) record.fields.push(evidence.field);
      if (!record.sections.includes(section.section_id)) record.sections.push(section.section_id);
      if (!record.retrieved_at.includes(evidence.retrieved_at)) record.retrieved_at.push(evidence.retrieved_at);
    }
  }
  for (const excerpt of memo.excerpts) {
    const record = byKey.get(`${excerpt.provider}${excerpt.record_id}`);
    if (record && !record.excerpts.some((e) => e.excerpt_ref === excerpt.excerpt_ref)) record.excerpts.push(excerpt);
  }
  return [...byKey.values()];
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
