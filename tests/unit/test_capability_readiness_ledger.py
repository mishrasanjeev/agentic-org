"""Focused tests for the capability readiness contracts and promotion policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from core.claims.schema import ClaimTreatment, GateResult, InternalMaturity, PublicAvailability
from core.readiness.contracts import (
    CANONICAL_GATE_IDS,
    REQUIRED_OWNER_ROLES,
    CapabilityRegistration,
    CurrentReadinessState,
    EvidenceEnvironment,
    EvidenceFact,
    EvidenceTrustState,
    GateAttestation,
    OwnerRotation,
    ReadinessTransition,
    ReviewRenewal,
    ScopeDisposition,
)
from core.readiness.ledger import ReadinessConflictError, compute_event_hash, verify_event_chain
from core.readiness.policy import (
    ReadinessValidationError,
    validate_owner_rotation,
    validate_promotion,
    validate_registration,
    validate_review_renewal,
)

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
COMPANY_ID = UUID("22222222-2222-4222-8222-222222222222")
CAPABILITY_ID = "HR-C01"
APPROVER_ID = "hr-governance@example.com"


def _owners() -> dict[str, str]:
    return {role: f"{role}@example.com" for role in REQUIRED_OWNER_ROLES}


def _traceability() -> dict[str, tuple[str, ...]]:
    return {
        "gap_ids": ("P0-08",),
        "roadmap_ids": ("HR-02",),
        "implementation_refs": ("core/readiness/policy.py",),
        "migration_refs": ("migrations/versions/v6_z5_capability_readiness_ledger.py",),
        "test_refs": ("tests/unit/test_capability_readiness_ledger.py",),
        "threat_privacy_refs": ("docs/readiness/DOMAIN_READINESS_STANDARD.md",),
        "runbook_refs": ("docs/runbooks/hr-screening.md",),
        "slo_dashboard_refs": ("docs/readiness/DOMAIN_READINESS_STANDARD.md",),
        "release_manifest_refs": ("docs/readiness/CAPABILITY_READINESS_REGISTER.md",),
    }


def _registration(**changes: object) -> CapabilityRegistration:
    value = CapabilityRegistration(
        capability_id=CAPABILITY_ID,
        domain="hr",
        title="Governed candidate screening",
        description="Evidence-only screening with a recorded human decision.",
        scope_disposition=ScopeDisposition.MANDATORY,
        scope_condition=None,
        scope_details={"jurisdictions": ["IN"], "decision_boundary": "recommendation_only"},
        required_gate_ids=CANONICAL_GATE_IDS,
        owners=_owners(),
        approver_ids=(APPROVER_ID,),
        traceability=_traceability(),
        limitations=("No automated final adverse employment decision.",),
        feature_flag="readiness_hr_screening",
        review_expires_at=NOW + timedelta(days=90),
    )
    return replace(value, **changes)


def _current(internal: InternalMaturity = InternalMaturity.MISSING) -> CurrentReadinessState:
    return CurrentReadinessState(
        internal_maturity=internal,
        release_gate=GateResult.BLOCKED,
        public_availability=PublicAvailability.UNAVAILABLE,
        claim_state=ClaimTreatment.HIDDEN,
        gate_results={},
        permitted_claim_ids=(),
    )


def _evidence(
    environment: EvidenceEnvironment,
    *,
    evidence_type: str = "implementation_test",
    tenant_id: UUID = TENANT_ID,
    company_id: UUID | None = COMPANY_ID,
    expires_at: datetime | None = None,
    claim_ids: tuple[str, ...] = (),
) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=uuid4(),
        tenant_id=tenant_id,
        company_id=company_id,
        capability_id=CAPABILITY_ID,
        evidence_version=f"v-{environment.value}-{uuid4().hex[:8]}",
        evidence_type=evidence_type,
        artifact_uri="evidence://readiness/test-artifact",
        sha256_checksum="a" * 64,
        environment=environment,
        provider_account_class="test-fixture",
        product_version="4.8.0",
        source_commit_sha="a" * 40,
        trust_state=EvidenceTrustState.VERIFIED,
        submitted_by="submitter@example.com",
        reviewed_at=NOW - timedelta(days=1),
        expires_at=expires_at or NOW + timedelta(days=30),
        reviewed_by="reviewer@example.com",
        supports_gate_ids=CANONICAL_GATE_IDS,
        supports_claim_ids=claim_ids,
    )


def _attestations(status: GateResult, evidence_ids: tuple[UUID, ...] = ()) -> tuple[GateAttestation, ...]:
    reviewed = status is GateResult.PASSED
    reasoned = status in {GateResult.BLOCKED, GateResult.EXPIRED}
    return tuple(
        GateAttestation(
            gate_id=gate_id,
            status=status,
            evidence_ids=evidence_ids if reviewed else (),
            reviewed_by="reviewer@example.com" if reviewed else None,
            reviewed_at=NOW - timedelta(hours=1) if reviewed else None,
            reason="Gate failed closed." if reasoned else None,
        )
        for gate_id in CANONICAL_GATE_IDS
    )


def _transition(
    *,
    internal: InternalMaturity,
    release_gate: GateResult,
    public: PublicAvailability = PublicAvailability.UNAVAILABLE,
    claim: ClaimTreatment = ClaimTreatment.HIDDEN,
    attestations: tuple[GateAttestation, ...] | None = None,
    evidence_ids: tuple[UUID, ...] = (),
    permitted_claim_ids: tuple[str, ...] = (),
    approved_by: str = APPROVER_ID,
    approval_reference: str | None = None,
) -> ReadinessTransition:
    return ReadinessTransition(
        target_internal_maturity=internal,
        target_release_gate=release_gate,
        target_public_availability=public,
        target_claim_state=claim,
        gate_attestations=attestations if attestations is not None else _attestations(GateResult.NOT_ASSESSED),
        evidence_ids=evidence_ids,
        permitted_claim_ids=permitted_claim_ids,
        limitations=("Human review remains mandatory.",),
        reason="Evidence review completed.",
        approved_by=approved_by,
        approval_reference=approval_reference,
    )


def _validate(
    current,
    transition,
    evidence,
    *,
    scope_disposition=ScopeDisposition.MANDATORY,
    review_expires_at: datetime = NOW + timedelta(days=30),
):
    return validate_promotion(
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        capability_id=CAPABILITY_ID,
        scope_disposition=scope_disposition,
        required_gate_ids=CANONICAL_GATE_IDS if scope_disposition is not ScopeDisposition.OUT_OF_SCOPE else (),
        approver_ids=(APPROVER_ID,),
        current=current,
        transition=transition,
        evidence=evidence,
        requested_by="requester@example.com",
        review_expires_at=review_expires_at,
        now=NOW,
    )


def test_registration_accepts_complete_traceability() -> None:
    validate_registration(_registration(), now=NOW)


def test_registration_requires_id_owners_traceability_and_limitations() -> None:
    with pytest.raises(ReadinessValidationError, match="stable documented"):
        validate_registration(_registration(capability_id="HR screening"), now=NOW)
    with pytest.raises(ReadinessValidationError, match="all owner roles"):
        validate_registration(_registration(owners={"product": "owner@example.com"}), now=NOW)
    with pytest.raises(ReadinessValidationError, match="traceability"):
        validate_registration(_registration(traceability={}), now=NOW)
    with pytest.raises(ReadinessValidationError, match="explicit limitation"):
        validate_registration(_registration(limitations=()), now=NOW)


def test_registration_enforces_scope_gate_contracts() -> None:
    with pytest.raises(ReadinessValidationError, match="every canonical"):
        validate_registration(_registration(required_gate_ids=("quality",)), now=NOW)
    with pytest.raises(ReadinessValidationError, match="scope condition"):
        validate_registration(_registration(scope_disposition=ScopeDisposition.CONDITIONAL), now=NOW)
    validate_registration(
        _registration(
            scope_disposition=ScopeDisposition.OUT_OF_SCOPE,
            scope_condition="Deferred by the supported-scope matrix.",
            required_gate_ids=(),
        ),
        now=NOW,
    )


def test_implemented_state_can_advance_without_public_or_gate_promotion() -> None:
    proof = _evidence(EvidenceEnvironment.TEST)
    decision = _validate(
        _current(),
        _transition(
            internal=InternalMaturity.IMPLEMENTED,
            release_gate=GateResult.NOT_ASSESSED,
            evidence_ids=(proof.evidence_id,),
        ),
        (proof,),
    )
    assert decision.release_gate is GateResult.NOT_ASSESSED
    assert decision.evidence_snapshot[0]["sha256_checksum"] == "a" * 64


def test_expired_capability_review_blocks_transition() -> None:
    with pytest.raises(ReadinessValidationError, match="capability review is expired"):
        _validate(
            _current(),
            _transition(internal=InternalMaturity.MISSING, release_gate=GateResult.BLOCKED),
            (),
            review_expires_at=NOW,
        )


def test_public_beta_cannot_exceed_internal_maturity() -> None:
    proof = _evidence(EvidenceEnvironment.TEST)
    with pytest.raises(ReadinessValidationError, match="public availability exceeds"):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.SCAFFOLDED,
                release_gate=GateResult.NOT_ASSESSED,
                public=PublicAvailability.BETA,
                evidence_ids=(proof.evidence_id,),
            ),
            (proof,),
        )


@pytest.mark.parametrize("failure", ["expired", "cross_tenant", "cross_company"])
def test_expired_or_cross_scope_evidence_fails_closed(failure: str) -> None:
    kwargs: dict[str, object] = {}
    if failure == "expired":
        kwargs["expires_at"] = NOW - timedelta(seconds=1)
    elif failure == "cross_tenant":
        kwargs["tenant_id"] = uuid4()
    else:
        kwargs["company_id"] = uuid4()
    proof = _evidence(EvidenceEnvironment.TEST, **kwargs)
    with pytest.raises(ReadinessValidationError):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.IMPLEMENTED,
                release_gate=GateResult.NOT_ASSESSED,
                evidence_ids=(proof.evidence_id,),
            ),
            (proof,),
        )


def test_unverified_evidence_cannot_satisfy_promotion() -> None:
    proof = replace(_evidence(EvidenceEnvironment.TEST), trust_state=EvidenceTrustState.UNVERIFIED)
    with pytest.raises(ReadinessValidationError, match="not independently verified"):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.IMPLEMENTED,
                release_gate=GateResult.NOT_ASSESSED,
                evidence_ids=(proof.evidence_id,),
            ),
            (proof,),
        )


def test_governance_policy_bounds_renewal_and_rejects_noop_rotation() -> None:
    with pytest.raises(ReadinessValidationError, match="between 1 and 90"):
        validate_review_renewal(
            ReviewRenewal(expected_sequence=0, valid_for_days=91, reason="annual review"),
            current_expires_at=NOW,
            now=NOW,
        )
    with pytest.raises(ReadinessValidationError, match="must change"):
        validate_owner_rotation(
            OwnerRotation(
                expected_sequence=0,
                owners=_owners(),
                approver_ids=(APPROVER_ID,),
                reason="rotation",
            ),
            current_owners=_owners(),
            current_approver_ids=(APPROVER_ID,),
        )


def test_evidence_backed_production_requires_full_proof_and_segregation() -> None:
    claim_id = "claim.hr.screening.human_reviewed"
    sandbox = _evidence(EvidenceEnvironment.VENDOR_SANDBOX)
    pilot = _evidence(EvidenceEnvironment.CONTROLLED_PILOT)
    production = _evidence(
        EvidenceEnvironment.PRODUCTION,
        evidence_type="claim_approval",
        claim_ids=(claim_id,),
    )
    selected = (sandbox, pilot, production)
    transition = _transition(
        internal=InternalMaturity.PRODUCTION_PROVEN,
        release_gate=GateResult.PASSED,
        public=PublicAvailability.LIMITED_AVAILABILITY,
        claim=ClaimTreatment.EVIDENCE_BACKED,
        attestations=_attestations(GateResult.PASSED, (production.evidence_id,)),
        evidence_ids=tuple(item.evidence_id for item in selected),
        permitted_claim_ids=(claim_id,),
        approval_reference="approval://readiness/123",
    )
    decision = _validate(_current(InternalMaturity.SANDBOX_PROVEN), transition, selected)
    assert decision.release_gate is GateResult.PASSED
    assert len(decision.evidence_snapshot) == 3


def test_evidence_backed_claim_rejects_unbound_claim_id() -> None:
    proof = _evidence(EvidenceEnvironment.PRODUCTION, evidence_type="claim_approval")
    with pytest.raises(ReadinessValidationError, match="claim-approval evidence"):
        _validate(
            _current(InternalMaturity.PRODUCTION_PROVEN),
            _transition(
                internal=InternalMaturity.PRODUCTION_PROVEN,
                release_gate=GateResult.PASSED,
                public=PublicAvailability.LIMITED_AVAILABILITY,
                claim=ClaimTreatment.EVIDENCE_BACKED,
                attestations=_attestations(GateResult.PASSED, (proof.evidence_id,)),
                evidence_ids=(proof.evidence_id,),
                permitted_claim_ids=("claim.hr.screening.accuracy",),
                approval_reference="approval://readiness/124",
            ),
            (proof,),
        )


def test_out_of_scope_capability_cannot_be_promoted() -> None:
    with pytest.raises(ReadinessValidationError, match="out-of-scope"):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.SCAFFOLDED,
                release_gate=GateResult.BLOCKED,
                attestations=(),
            ),
            (),
            scope_disposition=ScopeDisposition.OUT_OF_SCOPE,
        )


def test_gate_attestations_reject_duplicates_future_reviews_and_missing_reason() -> None:
    proof = _evidence(EvidenceEnvironment.TEST)
    duplicate = list(_attestations(GateResult.PASSED, (proof.evidence_id,)))
    duplicate[0] = replace(duplicate[0], evidence_ids=(proof.evidence_id, proof.evidence_id))
    with pytest.raises(ReadinessValidationError, match="duplicate evidence"):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.IMPLEMENTED,
                release_gate=GateResult.PASSED,
                attestations=tuple(duplicate),
                evidence_ids=(proof.evidence_id,),
            ),
            (proof,),
        )
    future = list(_attestations(GateResult.PASSED, (proof.evidence_id,)))
    future[0] = replace(future[0], reviewed_at=NOW + timedelta(seconds=1))
    with pytest.raises(ReadinessValidationError, match="future review"):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.IMPLEMENTED,
                release_gate=GateResult.PASSED,
                attestations=tuple(future),
                evidence_ids=(proof.evidence_id,),
            ),
            (proof,),
        )
    blocked = list(_attestations(GateResult.BLOCKED))
    blocked[0] = replace(blocked[0], reason=None)
    with pytest.raises(ReadinessValidationError, match="requires a reason"):
        _validate(
            _current(),
            _transition(
                internal=InternalMaturity.SCAFFOLDED,
                release_gate=GateResult.BLOCKED,
                attestations=tuple(blocked),
            ),
            (),
        )


def test_event_hash_is_canonical_and_chained() -> None:
    first = compute_event_hash(None, {"b": 2, "a": 1})
    assert first == compute_event_hash(None, {"a": 1, "b": 2})
    assert compute_event_hash(first, {"a": 1}) != compute_event_hash(None, {"a": 1})


def _promotion_event(sequence: int, previous_hash: str | None):
    from core.models.capability_readiness import CapabilityPromotionEvent

    event = CapabilityPromotionEvent(
        readiness_record_id=uuid4(),
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        capability_id=CAPABILITY_ID,
        sequence=sequence,
        event_type="registered" if sequence == 0 else "promoted",
        from_internal_maturity=None if sequence == 0 else InternalMaturity.MISSING.value,
        to_internal_maturity=(InternalMaturity.MISSING.value if sequence == 0 else InternalMaturity.IMPLEMENTED.value),
        from_release_gate=None if sequence == 0 else GateResult.BLOCKED.value,
        to_release_gate=GateResult.BLOCKED.value,
        from_public_availability=None if sequence == 0 else PublicAvailability.UNAVAILABLE.value,
        to_public_availability=PublicAvailability.UNAVAILABLE.value,
        from_claim_state=None if sequence == 0 else ClaimTreatment.HIDDEN.value,
        to_claim_state=ClaimTreatment.HIDDEN.value,
        scope_disposition_snapshot=ScopeDisposition.MANDATORY.value,
        gate_results_snapshot={},
        evidence_snapshot=[],
        ownership_snapshot={"owners": _owners(), "approver_ids": [APPROVER_ID]},
        traceability_snapshot={key: list(value) for key, value in _traceability().items()},
        permitted_claim_ids=[],
        limitations=["Human review remains mandatory."],
        requested_by="requester@example.com",
        approved_by=APPROVER_ID,
        approval_reference=None,
        reason="Capability registered" if sequence == 0 else "Evidence review completed.",
        policy_version="capability-readiness-v1",
        previous_event_hash=previous_hash,
        event_hash="",
    )
    from core.readiness.ledger import _event_payload

    event.event_hash = compute_event_hash(previous_hash, _event_payload(event))
    return event


def test_verify_event_chain_recomputes_payload_and_rejects_tampering() -> None:
    first = _promotion_event(0, None)
    second = _promotion_event(1, first.event_hash)
    second.readiness_record_id = first.readiness_record_id
    from core.readiness.ledger import _event_payload

    second.event_hash = compute_event_hash(first.event_hash, _event_payload(second))
    assert verify_event_chain([first, second]) == second.event_hash

    second.reason = "tampered after hashing"
    with pytest.raises(ReadinessConflictError, match="event hash is invalid"):
        verify_event_chain([first, second])


def test_verify_event_chain_rejects_sequence_predecessor_and_scope_breaks() -> None:
    first = _promotion_event(0, None)
    second = _promotion_event(1, first.event_hash)
    second.readiness_record_id = first.readiness_record_id
    from core.readiness.ledger import _event_payload

    second.event_hash = compute_event_hash(first.event_hash, _event_payload(second))
    second.sequence = 2
    with pytest.raises(ReadinessConflictError, match="sequence"):
        verify_event_chain([first, second])
    second.sequence = 1
    second.previous_event_hash = "f" * 64
    with pytest.raises(ReadinessConflictError, match="predecessor"):
        verify_event_chain([first, second])
    second.previous_event_hash = first.event_hash
    second.company_id = uuid4()
    with pytest.raises(ReadinessConflictError, match="crosses capability scope"):
        verify_event_chain([first, second])
