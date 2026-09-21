// SPDX-License-Identifier: Apache-2.0
// Synthetic governed case documents shaped like the published schemas (schemas/examples).
// Every name is invented and every identifier is from a reserved range.
import type {
  CaseDetail,
  CaseSummary,
  ScreeningDisposition,
  ScreeningResult,
  UnderwritingMemo,
} from "@/lib/governedCases";

export const CASE_REF = "case_0000000000000000000000a1";

const registryEvidence = {
  provider: "mock",
  record_id: "mock:company:00000001:profile",
  field: "company_status",
  retrieved_at: "2026-09-01T09:00:00Z",
  excerpt_ref: null,
};

const ownersEvidence = {
  provider: "mock",
  record_id: "mock:company:00000001:owners",
  field: "items",
  retrieved_at: "2026-09-01T09:00:00Z",
  excerpt_ref: null,
};

const watchlistEvidence = {
  provider: "mock",
  record_id: "mock:watchlist:entry:0007",
  field: "names[0]",
  retrieved_at: "2026-09-01T09:05:00Z",
  excerpt_ref: "excerpt:mock-watchlist-0007",
};

export function memoFixture(overrides: Partial<UnderwritingMemo> = {}): UnderwritingMemo {
  return {
    schema_version: "1.0.0",
    memo_id: "memo_0001",
    case_id: CASE_REF,
    created_at: "2026-09-01T09:10:00Z",
    subject: {
      provider: "mock",
      provider_ref: "mock-gb-00000001",
      jurisdiction: "GB",
      identifiers: [{ scheme: "gb_company_number", value: "00000001" }],
    },
    sections: [
      { section_id: "registry", status: "complete", findings: [], evidence: [registryEvidence] },
      {
        section_id: "ownership",
        status: "complete",
        findings: [
          {
            code: "missing_owner",
            severity: "medium",
            statement: "Declared owner Ansel Pikeworth does not appear in the ownership graph at or above 25%.",
            evidence: [ownersEvidence],
          },
        ],
        evidence: [ownersEvidence],
      },
      {
        section_id: "screening",
        status: "partial",
        findings: [
          {
            code: "screening_hit",
            severity: "low",
            statement: "One probable false-positive sanctions hit is awaiting analyst review.",
            evidence: [watchlistEvidence, { ...watchlistEvidence, excerpt_ref: "excerpt:not-attached" }],
          },
        ],
        evidence: [
          {
            provider: "mock",
            record_id: "mock:screening:scr-0000000000000001",
            field: "hits",
            retrieved_at: "2026-09-01T09:05:00Z",
            excerpt_ref: null,
          },
        ],
      },
      {
        section_id: "web_presence",
        status: "not_available",
        not_available_reason: "capability_not_supported",
        findings: [],
        evidence: [],
      },
      { section_id: "activity", status: "error", error_reason: "provider_timeout", findings: [], evidence: [] },
    ],
    policy_result: {
      schema_version: "1.0.0",
      policy: { policy_id: "business_onboarding_uk", version: "1.2.0", example: true, reviewed_by: null },
      inputs_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000001",
      score: 40,
      tier: "medium",
      reasons: [
        {
          rule_id: "ownership_reconciled",
          tier: "medium",
          reason: "Declared owners do not reconcile with the ownership graph",
          score: 40,
          inputs: { "ownership.missing_owners": 1, "verification.status": null },
        },
      ],
    },
    recommendation: { proposed: "refer", basis: "policy_result", requires_human_decision: true },
    missing_items: [{ item: "owner_evidence", reason: "Evidence of Ansel Pikeworth's ownership is needed." }],
    excerpts: [
      {
        excerpt_ref: "excerpt:mock-watchlist-0007",
        provider: "mock",
        record_id: "mock:watchlist:entry:0007",
        media_type: "text/plain",
        sha256: "sha256:0000000000000000000000000000000000000000000000000000000000000002",
      },
    ],
    provenance: {
      agent: "business_underwriter",
      agent_version: "0.1.0",
      prompt_version: "1.0.0",
      model_id: "scripted-model",
      model_confidence: null,
    },
    ...overrides,
  };
}

export function caseDetailFixture(overrides: Partial<CaseDetail> = {}): CaseDetail {
  const memo = memoFixture();
  return {
    case: {
      schema_version: "1.0.0",
      case_id: CASE_REF,
      purpose: "aml.cdd.onboarding",
      state: "awaiting_decision",
      created_at: "2026-09-01T08:00:00Z",
      updated_at: "2026-09-01T09:10:00Z",
      application: {
        legal_name: "Marlpit Orchard Example Ltd",
        jurisdiction: "GB",
        identifiers: [{ scheme: "gb_company_number", value: "00000001" }],
        declared_owners: [{ name: "Ansel Pikeworth", kind: "person", ownership_pct: 60 }],
      },
      subject: memo.subject,
      decision: null,
    },
    memo,
    policy_result: memo.policy_result,
    ownership_graph: null,
    screening_results: [],
    screening_dispositions: [],
    parties: [],
    information_requests: [],
    decision_requests: [],
    decision: null,
    failure_reason: null,
    transitions: [
      { from_state: null, to_state: "submitted", actor: "user:00000000-0000-0000-0000-000000000001", reason: "case_submitted", at: "2026-09-01T08:00:00Z" },
      { from_state: "submitted", to_state: "in_progress", actor: "workflow:business_onboarding", reason: "investigation_started", at: "2026-09-01T08:01:00Z" },
      { from_state: "in_progress", to_state: "awaiting_decision", actor: "workflow:business_onboarding", reason: "memo_ready", at: "2026-09-01T09:10:00Z" },
    ],
    ...overrides,
  };
}

export const summaryFixture: CaseSummary = {
  case_ref: CASE_REF,
  state: "awaiting_decision",
  purpose: "aml.cdd.onboarding",
  legal_name: "Marlpit Orchard Example Ltd",
  jurisdiction: "GB",
  recommendation: "refer",
  tier: "medium",
  failure_reason: null,
  updated_at: "2026-09-01T09:10:00Z",
};

export function axiosError(status: number, data: unknown): unknown {
  return Object.assign(new Error(`status ${status}`), { response: { status, data } });
}

const hitEvidence = {
  provider: "mock",
  record_id: "mock:watchlist:wl-0001",
  field: "names[0]",
  retrieved_at: "2026-09-01T09:05:00Z",
  excerpt_ref: "excerpt:mock-watchlist-wl-0001",
};

export const HIT_ID = "hit-000000000000a001";

export function dispositionFixture(overrides: Partial<ScreeningDisposition> = {}): ScreeningDisposition {
  return {
    schema_version: "1.0.0",
    disposition_id: "dsp-000000000000000000000a01",
    case_id: CASE_REF,
    screening_id: "scr-000000000000a001",
    hit_id: HIT_ID,
    proposed_by: { agent: "screening_disposition", agent_version: "1.0.0", prompt_version: "1.0.0" },
    proposed_at: "2026-09-01T09:06:00Z",
    comparisons: [
      {
        identifier: "name",
        subject_value: "Ansel Pikeworth",
        hit_value: "Ansel Pikworth",
        result: "partial_match",
        note: "Normalised names are 0.94 similar.",
        evidence: [hitEvidence],
      },
      { identifier: "date_of_birth", subject_value: "1990-03", hit_value: "1948-07-02", result: "mismatch", note: null, evidence: [] },
      { identifier: "nationality", subject_value: "GB", hit_value: "ZZ", result: "mismatch", note: null, evidence: [] },
      { identifier: "address", subject_value: null, hit_value: null, result: "not_comparable", note: "No address on one side.", evidence: [] },
      {
        identifier: "associated_entities",
        subject_value: "Marlpit Orchard Example Ltd",
        hit_value: "Pikworth Holdings",
        result: "mismatch",
        note: null,
        evidence: [],
      },
    ],
    proposed_outcome: "false_positive",
    confidence_band: "high",
    rationale: "The name partly matches; the date of birth and nationality do not. Proposed outcome: false positive.",
    evidence: [hitEvidence],
    review: null,
    ...overrides,
  };
}

export function screeningResultFixture(): ScreeningResult {
  return {
    screening_id: "scr-000000000000a001",
    provider: "mock",
    subject_kind: "person",
    subject: { name: "Ansel Pikeworth" },
    screened_at: "2026-09-01T09:05:00Z",
    list_types: ["sanctions"],
    hits: [
      {
        hit_id: HIT_ID,
        list_type: "sanctions",
        source: { name: "Example sanctions list", authority: "Example authority", jurisdiction: "ZZ" },
        matched_name: "Ansel Pikworth",
        aliases: [],
        name_similarity: 0.94,
      },
    ],
    evidence: [hitEvidence],
  };
}
