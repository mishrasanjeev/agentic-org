from __future__ import annotations

from pathlib import Path
from typing import cast

from core.commerce.oacp_artifacts import (
    OACP_C6W3_VALID_ARTIFACT_FIXTURES,
    OACP_C6W9_DRY_RUN_VERIFICATION_KINDS,
    OACP_C6W9_VERIFIER_STATUSES,
    EligibilityPacketKind,
    PreparedEnvelopeKind,
    ReconciliationStatus,
    ResponseEvidenceKind,
    evaluate_agenticorg_c6w5_commitment_boundary,
    prepare_agenticorg_c6w6_commitment_envelope,
    prepare_agenticorg_c6w8_eligibility_packet,
    reconcile_agenticorg_c6w7_prepared_response,
    verify_agenticorg_c6w9_execution_handoff_dry_run,
)

C6W9_DOC_PATH = Path("docs/reports/commerce-agent-c6w9-dry-run-verifier.md")


def _artifacts() -> list[dict[str, object]]:
    return [
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["merchant_capability"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["seller_agent_capability"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["catalog_snapshot"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["offer"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["price"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["inventory"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["policy"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["mandate_capability"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["commitment_evidence"],
        OACP_C6W3_VALID_ARTIFACT_FIXTURES["protocol_adapter"],
    ]


def _preview() -> dict[str, object]:
    return {
        "generated": True,
        "status": "preview_only",
        "surface": "mcp_tool_resource_capability",
        "source_artifact_ids": [artifact["envelope"]["artifact_id"] for artifact in _artifacts()],
        "source_artifact_families": [artifact["envelope"]["artifact_type"] for artifact in _artifacts()],
        "source_authority": "grantex_canonical_oacp_artifact_authority",
        "generated_at": "2026-06-11T00:00:30.000Z",
        "expires_at": "2026-06-11T00:02:00.000Z",
        "max_ttl_seconds": 90,
        "freshness_tier": "fresh",
        "unsupported_capabilities": [
            "checkout_create",
            "payment_authorize",
            "payment_capture",
            "live_provider_call",
            "merchant_private_api_call",
            "protocol_publication",
        ],
        "blocked_capabilities": ["checkout_create", "payment_authorize", "payment_capture"],
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "surface_payload": {
            "preview_only": True,
            "internal_only": True,
            "non_publication": True,
            "source_refs": [
                {
                    "artifact_id": artifact["envelope"]["artifact_id"],
                    "artifact_type": artifact["envelope"]["artifact_type"],
                }
                for artifact in _artifacts()
            ],
        },
    }


def _decision(action: str = "price_lock") -> dict[str, object]:
    return evaluate_agenticorg_c6w5_commitment_boundary(
        action=action,
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        revocation_snapshot_age_seconds=30,
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
        max_quantity_per_sku=1,
    )


def _envelope(kind: PreparedEnvelopeKind = "buyer_confirmation_request") -> dict[str, object]:
    prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind=kind,
        resolver_decision=_decision(),
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["prepared-price-ref"],
        unsupported_capabilities=["checkout_create", "payment_authorize"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert prepared["generated"] is True
    return cast(dict[str, object], prepared["envelope"])


def _reconciliation(
    *,
    response_status: ReconciliationStatus = "accepted_for_preparation",
    response_kind: ResponseEvidenceKind = "buyer_confirmation_response",
    envelope_kind: PreparedEnvelopeKind = "buyer_confirmation_request",
) -> dict[str, object]:
    reconciled = reconcile_agenticorg_c6w7_prepared_response(
        envelope=_envelope(envelope_kind),
        response_kind=response_kind,
        response_status=response_status,
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-confirmation-cache-ref"],
        response_claimed_action="price_lock",
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert reconciled["reconciled"] is True
    return cast(dict[str, object], reconciled["reconciliation"])


def _mandate_reconciliation() -> dict[str, object]:
    mandate_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="prepare_mandate_capability_check_request",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="mandate_capability_evidence_request",
        resolver_decision=mandate_decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["mandate-request-ref"],
    )
    assert prepared["generated"] is True
    reconciled = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared["envelope"],
        response_kind="mandate_capability_evidence_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["mandate-capability-cache-ref"],
        response_evidence_issued_at="2026-06-11T00:00:45.000Z",
    )
    assert reconciled["reconciled"] is True
    return cast(dict[str, object], reconciled["reconciliation"])


def _eligibility_packet(
    *,
    packet_kind: EligibilityPacketKind = "execution_handoff_eligibility_packet",
    reconciliation: dict[str, object] | None = None,
) -> dict[str, object]:
    prepared = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind=packet_kind,
        reconciliation=reconciliation or _reconciliation(),
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert prepared["prepared"] is True
    return cast(dict[str, object], prepared["packet"])


def test_c6w9_verifies_accepted_handoff_and_audit_contracts() -> None:
    packet = _eligibility_packet()
    dry_run = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="execution_controller_handoff_dry_run",
        eligibility_packet=packet,
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert dry_run["verified"] is True
    assert dry_run["status"] == "dry_run_accepted_for_future_controller"
    verification = dry_run["verification"]
    assert verification["verification_kind"] == "execution_controller_handoff_dry_run"
    assert verification["verification_status"] == "dry_run_accepted_for_future_controller"
    assert verification["eligibility_packet_id"] == packet["packet_id"]
    assert verification["packet_kind"] == "execution_handoff_eligibility_packet"
    assert verification["requested_action"] == "price_lock"
    assert verification["allowed_for_future_handoff"] is True
    assert verification["allowed_to_execute"] is False
    assert verification["dry_run_only"] is True
    assert verification["eligibility_only"] is True
    assert verification["non_authoritative_for_transaction"] is True
    assert verification["no_checkout_payment_enablement"] is True
    assert verification["no_live_provider_enablement"] is True
    assert verification["no_public_discovery_enablement"] is True
    assert "price_C6W3" in verification["source_artifact_ids"]
    assert "buyer-confirmation-cache-ref" in verification["response_evidence_refs"]
    assert packet["reconciliation_id"] in verification["audit_lineage_refs"]
    assert verification["contract_checks"]["non_enablement_flags_intact"] is True
    assert verification["contract_checks"]["no_executable_url_or_target"] is True
    assert verification["audit_readiness_checks"]["decision_lineage_complete"] is True
    assert "not execution readiness" in verification["operator_safe_message"]
    assert verification["commerce_facts_invented"] is False

    audit_packet = _eligibility_packet(packet_kind="audit_trail_preparation_packet")
    audit = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="audit_readiness_verification",
        eligibility_packet=audit_packet,
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert audit["verified"] is True
    assert audit["verification"]["verification_kind"] == "audit_readiness_verification"
    assert audit["verification"]["allowed_to_execute"] is False


def test_c6w9_reports_non_accepted_statuses_without_handoff_permission() -> None:
    missing = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="missing_contract_requirement",
        eligibility_packet=_eligibility_packet(
            packet_kind="missing_evidence_packet",
            reconciliation=_reconciliation(response_status="needs_source_refresh"),
        ),
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert missing["verified"] is True
    assert missing["status"] == "missing_contract_requirement"
    assert missing["verification"]["allowed_for_future_handoff"] is False

    review = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="manual_review_required_verification",
        eligibility_packet=_eligibility_packet(
            packet_kind="manual_review_packet",
            reconciliation=_reconciliation(response_status="needs_human_review"),
        ),
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert review["verified"] is True
    assert review["status"] == "needs_human_review"

    for response_status in ("rejected", "stale", "mismatched"):
        blocked = verify_agenticorg_c6w9_execution_handoff_dry_run(
            verification_kind="blocked_handoff_verification",
            eligibility_packet=_eligibility_packet(
                packet_kind="blocked_execution_packet",
                reconciliation=_reconciliation(response_status=cast(ReconciliationStatus, response_status)),
            ),
            created_at="2026-06-11T00:02:00.000Z",
            provided_confirmations=["buyer_confirmation"],
            amount_minor_units=200000,
            currency="INR",
            total_quantity=1,
        )
        assert blocked["verified"] is True
        assert blocked["verification"]["allowed_to_execute"] is False
        assert blocked["verification"]["allowed_for_future_handoff"] is False

    expired = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="blocked_handoff_verification",
        eligibility_packet=_eligibility_packet(),
        created_at="2026-06-11T00:12:01.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert expired["verified"] is True
    assert expired["status"] == "expired"

    mismatched = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="blocked_handoff_verification",
        eligibility_packet=_eligibility_packet(),
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        claimed_packet_id="wrong-packet-id",
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert mismatched["verified"] is True
    assert mismatched["status"] == "mismatched"

    unsupported = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="blocked_handoff_verification",
        eligibility_packet=_eligibility_packet(
            packet_kind="blocked_execution_packet",
            reconciliation={**_reconciliation(), "risk_tier": "critical"},
        ),
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert unsupported["verified"] is True
    assert unsupported["status"] == "unsupported"

    assert "blocked_handoff_verification" in OACP_C6W9_DRY_RUN_VERIFICATION_KINDS
    assert "unsafe" in OACP_C6W9_VERIFIER_STATUSES


def test_c6w9_fails_closed_for_unsafe_or_mismatched_verifier_input() -> None:
    packet = _eligibility_packet()
    missing = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="execution_controller_handoff_dry_run",
        eligibility_packet=None,
        created_at="2026-06-11T00:02:00.000Z",
    )
    assert missing["verified"] is False
    assert missing["refusal_code"] == "packet_missing"

    executable = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="execution_controller_handoff_dry_run",
        eligibility_packet={**packet, "allowed_to_execute": True},
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert executable["verified"] is False
    assert executable["refusal_code"] == "packet_allows_execution"

    non_eligibility = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="execution_controller_handoff_dry_run",
        eligibility_packet={**packet, "eligibility_only": False},
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert non_eligibility["verified"] is False
    assert non_eligibility["refusal_code"] == "packet_not_prepared_reconciled_or_eligibility_only"

    unsafe_ref = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="execution_controller_handoff_dry_run",
        eligibility_packet={**packet, "response_evidence_refs": ["raw_jwt_private_ref"]},
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert unsafe_ref["verified"] is False
    assert unsafe_ref["refusal_code"] == "private_or_forbidden_verification_field"

    missing_confirmation = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="missing_contract_requirement",
        eligibility_packet=packet,
        created_at="2026-06-11T00:02:00.000Z",
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert missing_confirmation["verified"] is True
    assert missing_confirmation["status"] == "missing_contract_requirement"
    assert "confirmation:buyer_confirmation" in missing_confirmation["verification"]["missing_requirements"]

    ambiguous_risk = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="missing_contract_requirement",
        eligibility_packet=packet,
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
    )
    assert ambiguous_risk["verified"] is True
    assert ambiguous_risk["status"] == "missing_contract_requirement"

    kind_mismatch = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="execution_controller_handoff_dry_run",
        eligibility_packet=_eligibility_packet(
            packet_kind="manual_review_packet",
            reconciliation=_reconciliation(response_status="needs_human_review"),
        ),
        created_at="2026-06-11T00:02:00.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert kind_mismatch["verified"] is False
    assert kind_mismatch["refusal_code"] == "verification_kind_status_mismatch"


def test_c6w9_fails_closed_for_stale_mandate_evidence() -> None:
    prepared = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=_mandate_reconciliation(),
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["mandate_capability_evidence"],
        mandate_evidence_issued_at="2026-06-11T00:00:45.000Z",
    )
    assert prepared["prepared"] is True
    stale_mandate = verify_agenticorg_c6w9_execution_handoff_dry_run(
        verification_kind="blocked_handoff_verification",
        eligibility_packet=cast(dict[str, object], prepared["packet"]),
        created_at="2026-06-11T00:01:55.000Z",
        provided_confirmations=["mandate_capability_evidence"],
        mandate_evidence_issued_at="2026-06-10T23:58:00.000Z",
    )
    assert stale_mandate["verified"] is True
    assert stale_mandate["status"] == "stale"
    assert stale_mandate["verification"]["allowed_to_execute"] is False


def test_c6w9_documents_dry_run_verifier_boundaries() -> None:
    doc = C6W9_DOC_PATH.read_text(encoding="utf-8")
    for heading in (
        "Scope",
        "Dry-Run Result Kinds",
        "Verifier Statuses",
        "Required Fields",
        "Contract Checks",
        "Audit Readiness",
        "Fail-Closed Rules",
        "Dry-Run Acceptance Is Not Execution",
        "Toll Booth Boundary",
        "What This Does Not Enable",
        "Future Slices",
    ):
        assert f"## {heading}" in doc
    assert "allowed_to_execute remains false" in doc
    assert "dry_run_only remains true" in doc
