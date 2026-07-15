"""Database-independent, fail-closed readiness promotion policy."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from core.claims.schema import ClaimTreatment, GateResult, InternalMaturity, PublicAvailability
from core.readiness.contracts import (
    CANONICAL_GATE_IDS,
    CAPABILITY_ID_PATTERN,
    CLAIM_ID_PATTERN,
    CONDITIONAL_MINIMUM_GATE_IDS,
    INTERNAL_MATURITY_RANK,
    PUBLIC_MINIMUM_INTERNAL,
    REQUIRED_OWNER_ROLES,
    REQUIRED_TRACEABILITY_GROUPS,
    SHA256_PATTERN,
    VERSION_PATTERN,
    CapabilityRegistration,
    CurrentReadinessState,
    EvidenceEnvironment,
    EvidenceFact,
    EvidenceRegistration,
    EvidenceTrustState,
    OwnerRotation,
    PromotionDecision,
    ReadinessTransition,
    ReviewRenewal,
    ScopeDisposition,
)


class ReadinessValidationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(problems))


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _duplicates(values: tuple[object, ...]) -> bool:
    return len(values) != len(set(values))


def validate_registration(value: CapabilityRegistration, *, now: datetime) -> None:
    errors: list[str] = []
    if not _aware(now):
        errors.append("policy clock must be timezone-aware")
    if not CAPABILITY_ID_PATTERN.fullmatch(value.capability_id) or len(value.capability_id) > 160:
        errors.append("capability_id must be a stable documented or lowercase dotted identifier")
    if not value.domain.strip() or not value.title.strip() or not value.description.strip():
        errors.append("domain, title, and description are required")
    if not _aware(value.review_expires_at) or value.review_expires_at <= now:
        errors.append("review_expires_at must be a future timezone-aware timestamp")
    missing_owners = REQUIRED_OWNER_ROLES - set(value.owners)
    if missing_owners or any(not value.owners.get(role, "").strip() for role in REQUIRED_OWNER_ROLES):
        errors.append(f"all owner roles are required: {', '.join(sorted(REQUIRED_OWNER_ROLES))}")
    if not value.approver_ids or any(not item.strip() for item in value.approver_ids):
        errors.append("at least one non-empty approver ID is required")
    if _duplicates(value.approver_ids):
        errors.append("approver IDs must be unique")
    missing_traceability = REQUIRED_TRACEABILITY_GROUPS - set(value.traceability)
    if missing_traceability or any(not value.traceability.get(group) for group in REQUIRED_TRACEABILITY_GROUPS):
        errors.append(f"all traceability groups are required: {', '.join(sorted(REQUIRED_TRACEABILITY_GROUPS))}")
    if not value.limitations:
        errors.append("at least one explicit limitation is required until GA promotion")
    gates = set(value.required_gate_ids)
    if _duplicates(value.required_gate_ids) or not gates.issubset(CANONICAL_GATE_IDS):
        errors.append("required_gate_ids must be unique canonical gate IDs")
    if value.scope_disposition is ScopeDisposition.MANDATORY and gates != set(CANONICAL_GATE_IDS):
        errors.append("mandatory capabilities require every canonical readiness gate")
    elif value.scope_disposition is ScopeDisposition.CONDITIONAL:
        if not (value.scope_condition or "").strip():
            errors.append("conditional capabilities require an explicit scope condition")
        if not CONDITIONAL_MINIMUM_GATE_IDS.issubset(gates):
            errors.append("conditional capabilities omit a minimum safety gate")
    elif value.scope_disposition is ScopeDisposition.OUT_OF_SCOPE:
        if gates:
            errors.append("out-of-scope capabilities cannot have required gates")
        if not (value.scope_condition or "").strip():
            errors.append("out-of-scope capabilities require a recorded rationale")
    if errors:
        raise ReadinessValidationError(errors)


def validate_evidence_registration(value: EvidenceRegistration, *, now: datetime) -> None:
    errors: list[str] = []
    if not _aware(now):
        errors.append("policy clock must be timezone-aware")
    if not VERSION_PATTERN.fullmatch(value.evidence_version) or not VERSION_PATTERN.fullmatch(value.evidence_type):
        errors.append("evidence_version and evidence_type must be stable version identifiers")
    if not value.artifact_uri.strip() or len(value.artifact_uri) > 2048:
        errors.append("artifact_uri is required and must be at most 2048 characters")
    if not SHA256_PATTERN.fullmatch(value.sha256_checksum):
        errors.append("sha256_checksum must be 64 lowercase hexadecimal characters")
    if not value.provider_account_class.strip() or not value.product_version.strip():
        errors.append("provider account class and product version are required")
    if not re_full_commit(value.source_commit_sha):
        errors.append("source_commit_sha must contain 7 to 64 hexadecimal characters")
    if not all(_aware(item) for item in (value.observed_at, value.expires_at)):
        errors.append("evidence timestamps must be timezone-aware")
    elif not value.observed_at <= now < value.expires_at:
        errors.append("evidence must be observed by now and remain unexpired")
    if _duplicates(value.supports_gate_ids) or not set(value.supports_gate_ids).issubset(CANONICAL_GATE_IDS):
        errors.append("supports_gate_ids must be unique canonical gate IDs")
    if _duplicates(value.supports_claim_ids) or any(
        not CLAIM_ID_PATTERN.fullmatch(item) for item in value.supports_claim_ids
    ):
        errors.append("supports_claim_ids must be unique stable claim IDs")
    if errors:
        raise ReadinessValidationError(errors)


def validate_review_renewal(
    value: ReviewRenewal,
    *,
    current_expires_at: datetime,
    now: datetime,
) -> datetime:
    errors: list[str] = []
    if value.expected_sequence < 0:
        errors.append("expected_sequence must be nonnegative")
    if not 1 <= value.valid_for_days <= 90:
        errors.append("valid_for_days must be between 1 and 90")
    if not value.reason.strip():
        errors.append("a review-renewal reason is required")
    if not _aware(now) or not _aware(current_expires_at):
        errors.append("review-renewal timestamps must be timezone-aware")
        expires_at = now
    else:
        expires_at = now + timedelta(days=value.valid_for_days)
        if expires_at <= current_expires_at:
            errors.append("review renewal must extend the current expiry")
    if errors:
        raise ReadinessValidationError(errors)
    return expires_at


def validate_owner_rotation(
    value: OwnerRotation,
    *,
    current_owners: dict[str, str],
    current_approver_ids: tuple[str, ...],
) -> None:
    errors: list[str] = []
    if value.expected_sequence < 0:
        errors.append("expected_sequence must be nonnegative")
    if not value.reason.strip():
        errors.append("an owner-rotation reason is required")
    missing_owners = REQUIRED_OWNER_ROLES - set(value.owners)
    if missing_owners or any(not value.owners.get(role, "").strip() for role in REQUIRED_OWNER_ROLES):
        errors.append(f"all owner roles are required: {', '.join(sorted(REQUIRED_OWNER_ROLES))}")
    if not value.approver_ids or any(not item.strip() for item in value.approver_ids):
        errors.append("at least one non-empty approver ID is required")
    if _duplicates(value.approver_ids):
        errors.append("approver IDs must be unique")
    if value.owners == current_owners and tuple(sorted(value.approver_ids)) == tuple(sorted(current_approver_ids)):
        errors.append("owner rotation must change an owner or approver")
    if errors:
        raise ReadinessValidationError(errors)


def re_full_commit(value: str) -> bool:
    return 7 <= len(value) <= 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _gate_snapshot(
    transition: ReadinessTransition,
    required_gate_ids: tuple[str, ...],
    now: datetime,
) -> tuple[GateResult, dict[str, object], list[str]]:
    errors: list[str] = []
    by_gate = {item.gate_id: item for item in transition.gate_attestations}
    if len(by_gate) != len(transition.gate_attestations) or set(by_gate) != set(required_gate_ids):
        return GateResult.BLOCKED, {}, ["gate attestations must contain each required gate exactly once"]
    snapshot: dict[str, object] = {}
    statuses: list[GateResult] = []
    for gate_id in required_gate_ids:
        item = by_gate[gate_id]
        statuses.append(item.status)
        if _duplicates(item.evidence_ids):
            errors.append(f"gate {gate_id} contains duplicate evidence IDs")
        if item.status is GateResult.PASSED:
            if not item.evidence_ids or not item.reviewed_by or item.reviewed_at is None:
                errors.append(f"passed gate {gate_id} requires evidence, reviewer, and review time")
            elif not _aware(item.reviewed_at) or item.reviewed_at > now:
                errors.append(f"passed gate {gate_id} has an invalid or future review time")
        elif item.status in {GateResult.BLOCKED, GateResult.EXPIRED} and not (item.reason or "").strip():
            errors.append(f"{item.status.value} gate {gate_id} requires a reason")
        snapshot[gate_id] = {
            "status": item.status.value,
            "evidence_ids": [str(evidence_id) for evidence_id in item.evidence_ids],
            "reviewed_by": item.reviewed_by,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            "reason": item.reason,
        }
    if not statuses or any(status in {GateResult.BLOCKED, GateResult.EXPIRED} for status in statuses):
        computed = GateResult.BLOCKED
    elif all(status is GateResult.PASSED for status in statuses):
        computed = GateResult.PASSED
    elif all(status is GateResult.NOT_ASSESSED for status in statuses):
        computed = GateResult.NOT_ASSESSED
    else:
        computed = GateResult.IN_REVIEW
    return computed, snapshot, errors


def validate_promotion(
    *,
    tenant_id: UUID,
    company_id: UUID | None,
    capability_id: str,
    scope_disposition: ScopeDisposition,
    required_gate_ids: tuple[str, ...],
    approver_ids: tuple[str, ...],
    current: CurrentReadinessState,
    transition: ReadinessTransition,
    evidence: tuple[EvidenceFact, ...],
    requested_by: str,
    review_expires_at: datetime,
    now: datetime,
) -> PromotionDecision:
    errors: list[str] = []
    if not _aware(now):
        raise ReadinessValidationError(["policy clock must be timezone-aware"])
    if not _aware(review_expires_at) or review_expires_at <= now:
        errors.append("capability review is expired or not timezone-aware")
    if not transition.reason.strip() or not transition.limitations:
        errors.append("a transition reason and explicit limitations are required")
    if transition.approved_by not in approver_ids:
        errors.append("approved_by is not a registered capability approver")
    if _duplicates(transition.evidence_ids) or _duplicates(transition.permitted_claim_ids):
        errors.append("evidence IDs and permitted claim IDs must be unique")
    if any(not CLAIM_ID_PATTERN.fullmatch(item) for item in transition.permitted_claim_ids):
        errors.append("permitted_claim_ids contains an invalid claim ID")
    computed_gate, gate_results, gate_errors = _gate_snapshot(transition, required_gate_ids, now)
    errors.extend(gate_errors)
    if transition.target_release_gate is not computed_gate:
        errors.append(f"target_release_gate must equal computed gate state {computed_gate.value}")
    evidence_by_id = {item.evidence_id: item for item in evidence}
    if len(evidence_by_id) != len(evidence) or set(evidence_by_id) != set(transition.evidence_ids):
        errors.append("every selected evidence ID must resolve exactly once in capability scope")
    valid_evidence: list[EvidenceFact] = []
    for item in evidence:
        if (item.tenant_id, item.company_id, item.capability_id) != (tenant_id, company_id, capability_id):
            errors.append(f"evidence {item.evidence_id} crosses the capability scope")
            continue
        if not SHA256_PATTERN.fullmatch(item.sha256_checksum):
            errors.append(f"evidence {item.evidence_id} has an invalid checksum")
            continue
        if item.trust_state is not EvidenceTrustState.VERIFIED:
            errors.append(f"evidence {item.evidence_id} is not independently verified")
            continue
        if not item.reviewed_by or item.reviewed_at is None or item.reviewed_by == item.submitted_by:
            errors.append(f"evidence {item.evidence_id} lacks an independent reviewer")
            continue
        if not _aware(item.reviewed_at) or not _aware(item.expires_at) or not item.reviewed_at <= now < item.expires_at:
            errors.append(f"evidence {item.evidence_id} is future-reviewed or expired")
            continue
        valid_evidence.append(item)
    for attestation in transition.gate_attestations:
        if attestation.status is GateResult.PASSED:
            for evidence_id in attestation.evidence_ids:
                fact = evidence_by_id.get(evidence_id)
                if fact not in valid_evidence or attestation.gate_id not in (fact.supports_gate_ids if fact else ()):
                    errors.append(f"gate {attestation.gate_id} references invalid or unrelated evidence")
    if scope_disposition is ScopeDisposition.OUT_OF_SCOPE:
        if (
            transition.target_internal_maturity is not InternalMaturity.MISSING
            or transition.target_release_gate is not GateResult.BLOCKED
            or transition.target_public_availability is not PublicAvailability.UNAVAILABLE
            or transition.target_claim_state is not ClaimTreatment.HIDDEN
            or transition.permitted_claim_ids
        ):
            errors.append("out-of-scope capabilities must remain missing, blocked, unavailable, and hidden")
    minimum_internal = PUBLIC_MINIMUM_INTERNAL[transition.target_public_availability]
    if INTERNAL_MATURITY_RANK[transition.target_internal_maturity] < INTERNAL_MATURITY_RANK[minimum_internal]:
        errors.append("public availability exceeds internal maturity")
    if transition.target_public_availability is not PublicAvailability.UNAVAILABLE:
        passed = {item.gate_id for item in transition.gate_attestations if item.status is GateResult.PASSED}
        if not {"public_truth", "security_privacy"}.issubset(passed):
            errors.append("public availability requires passed public-truth and security/privacy gates")
        if not valid_evidence:
            errors.append("public availability requires current reviewed evidence")
    increasing = (
        INTERNAL_MATURITY_RANK[transition.target_internal_maturity] > INTERNAL_MATURITY_RANK[current.internal_maturity]
    )
    environments = {item.environment for item in valid_evidence}
    if (
        increasing
        and INTERNAL_MATURITY_RANK[transition.target_internal_maturity]
        >= INTERNAL_MATURITY_RANK[InternalMaturity.IMPLEMENTED]
        and not valid_evidence
    ):
        errors.append("implementation maturity promotion requires current reviewed evidence")
    if (
        increasing
        and INTERNAL_MATURITY_RANK[transition.target_internal_maturity]
        >= INTERNAL_MATURITY_RANK[InternalMaturity.INTEGRATED]
        and not environments.intersection(
            {
                EvidenceEnvironment.INTEGRATION,
                EvidenceEnvironment.STAGING,
                EvidenceEnvironment.VENDOR_SANDBOX,
                EvidenceEnvironment.CONTROLLED_PILOT,
                EvidenceEnvironment.PRODUCTION,
            }
        )
    ):
        errors.append("integrated maturity requires integration-or-higher evidence")
    if (
        increasing
        and INTERNAL_MATURITY_RANK[transition.target_internal_maturity]
        >= INTERNAL_MATURITY_RANK[InternalMaturity.SANDBOX_PROVEN]
        and EvidenceEnvironment.VENDOR_SANDBOX not in environments
    ):
        errors.append("sandbox-proven maturity requires vendor-sandbox evidence")
    if (
        increasing
        and INTERNAL_MATURITY_RANK[transition.target_internal_maturity]
        >= INTERNAL_MATURITY_RANK[InternalMaturity.PRODUCTION_PROVEN]
        and not {EvidenceEnvironment.CONTROLLED_PILOT, EvidenceEnvironment.PRODUCTION}.issubset(environments)
    ):
        errors.append("production-proven maturity requires controlled-pilot and production evidence")
    if transition.target_internal_maturity is InternalMaturity.GA and computed_gate is not GateResult.PASSED:
        errors.append("GA internal maturity requires every release gate to pass")
    if (
        transition.target_public_availability is PublicAvailability.GA
        and transition.target_internal_maturity is not InternalMaturity.GA
    ):
        errors.append("GA public availability requires GA internal maturity")
    claim_support = {
        claim_id
        for item in valid_evidence
        if item.evidence_type == "claim_approval"
        for claim_id in item.supports_claim_ids
    }
    permitted = set(transition.permitted_claim_ids)
    if transition.target_claim_state is ClaimTreatment.HIDDEN and permitted:
        errors.append("hidden capabilities cannot permit public claim IDs")
    elif transition.target_claim_state is not ClaimTreatment.HIDDEN and not permitted:
        errors.append("public claim treatment requires at least one exact permitted claim ID")
    if transition.target_claim_state in {
        ClaimTreatment.QUALIFIED,
        ClaimTreatment.EVIDENCE_BACKED,
    } and not permitted.issubset(claim_support):
        errors.append("qualified/evidence-backed claims require current claim-approval evidence")
    if transition.target_claim_state is ClaimTreatment.EVIDENCE_BACKED:
        if computed_gate is not GateResult.PASSED or transition.target_internal_maturity not in {
            InternalMaturity.PRODUCTION_PROVEN,
            InternalMaturity.GA,
        }:
            errors.append("evidence-backed claims require passed gates and production-proven maturity")
        if requested_by == transition.approved_by or not transition.approval_reference:
            errors.append("evidence-backed claims require segregated approval and an approval reference")
    if (
        transition.target_internal_maturity is current.internal_maturity
        and transition.target_release_gate is current.release_gate
        and transition.target_public_availability is current.public_availability
        and transition.target_claim_state is current.claim_state
        and tuple(sorted(transition.permitted_claim_ids)) == tuple(sorted(current.permitted_claim_ids))
        and gate_results == current.gate_results
    ):
        errors.append("no-op readiness transitions are not allowed")
    if errors:
        raise ReadinessValidationError(errors)
    snapshot = tuple(
        {
            "evidence_id": str(item.evidence_id),
            "version": item.evidence_version,
            "type": item.evidence_type,
            "artifact_uri": item.artifact_uri,
            "sha256_checksum": item.sha256_checksum,
            "environment": item.environment.value,
            "provider_account_class": item.provider_account_class,
            "product_version": item.product_version,
            "source_commit_sha": item.source_commit_sha,
            "trust_state": item.trust_state.value,
            "submitted_by": item.submitted_by,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            "expires_at": item.expires_at.isoformat(),
            "reviewed_by": item.reviewed_by,
        }
        for item in sorted(valid_evidence, key=lambda row: str(row.evidence_id))
    )
    return PromotionDecision(computed_gate, gate_results, snapshot)
