from __future__ import annotations

from pathlib import Path
from typing import cast

from core.commerce.oacp_artifacts import (
    OACP_C6W3_VALID_ARTIFACT_FIXTURES,
    PreparedEnvelopeKind,
    ReconciliationStatus,
    ResponseEvidenceKind,
    evaluate_agenticorg_c6w5_commitment_boundary,
    prepare_agenticorg_c6w6_commitment_envelope,
    prepare_agenticorg_c6w8_eligibility_packet,
    reconcile_agenticorg_c6w7_prepared_response,
)

C6W8_DOC_PATH = Path("docs/reports/commerce-agent-c6w8-eligibility-packets.md")


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


def test_c6w8_prepares_future_handoff_eligibility_and_audit_packets() -> None:
    accepted = _reconciliation()
    packet = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=accepted,
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert packet["prepared"] is True
    eligibility = packet["packet"]
    assert eligibility["packet_kind"] == "execution_handoff_eligibility_packet"
    assert eligibility["eligibility_status"] == "eligible_for_future_handoff"
    assert eligibility["response_status"] == "accepted_for_preparation"
    assert eligibility["requested_action"] == "price_lock"
    assert eligibility["allowed_for_future_handoff"] is True
    assert eligibility["allowed_to_execute"] is False
    assert eligibility["prepared_only"] is True
    assert eligibility["reconciled_only"] is True
    assert eligibility["eligibility_only"] is True
    assert eligibility["non_authoritative_for_transaction"] is True
    assert eligibility["no_checkout_payment_enablement"] is True
    assert eligibility["no_live_provider_enablement"] is True
    assert eligibility["no_public_discovery_enablement"] is True
    assert "price_C6W3" in eligibility["source_artifact_ids"]
    assert "buyer-confirmation-cache-ref" in eligibility["response_evidence_refs"]
    assert accepted["reconciliation_id"] in eligibility["audit_lineage_refs"]
    assert "http" not in eligibility["next_system_step_label"]
    assert "Nothing has been executed" in eligibility["buyer_safe_message"]
    assert eligibility["commerce_facts_invented"] is False

    audit = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="audit_trail_preparation_packet",
        reconciliation=accepted,
        created_at="2026-06-11T00:01:45.000Z",
        audit_lineage_refs=["redacted-lineage-ref"],
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert audit["prepared"] is True
    assert audit["packet"]["packet_kind"] == "audit_trail_preparation_packet"
    assert audit["packet"]["audit_lineage_refs"] == ["redacted-lineage-ref"]


def test_c6w8_prepares_missing_manual_blocked_and_unsupported_packets() -> None:
    missing = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="missing_evidence_packet",
        reconciliation=_reconciliation(response_status="needs_source_refresh"),
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert missing["prepared"] is True
    assert missing["packet"]["eligibility_status"] == "missing_evidence"
    assert missing["packet"]["allowed_for_future_handoff"] is False

    review = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="manual_review_packet",
        reconciliation=_reconciliation(response_status="needs_human_review"),
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert review["prepared"] is True
    assert review["packet"]["eligibility_status"] == "needs_human_review"

    for status in ("rejected", "blocked", "stale", "expired", "mismatched"):
        blocked = prepare_agenticorg_c6w8_eligibility_packet(
            packet_kind="blocked_execution_packet",
            reconciliation=_reconciliation(response_status=cast(ReconciliationStatus, status)),
            created_at="2026-06-11T00:01:45.000Z",
            provided_confirmations=["buyer_confirmation"],
            amount_minor_units=200000,
            currency="INR",
            total_quantity=1,
        )
        assert blocked["prepared"] is True
        assert blocked["packet"]["allowed_to_execute"] is False
        assert blocked["packet"]["allowed_for_future_handoff"] is False

    critical = {**_reconciliation(), "risk_tier": "critical"}
    unsupported = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="blocked_execution_packet",
        reconciliation=critical,
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert unsupported["prepared"] is True
    assert unsupported["packet"]["eligibility_status"] == "unsupported"


def test_c6w8_fails_closed_for_unsafe_executable_mismatched_or_ambiguous_input() -> None:
    accepted = _reconciliation()
    missing = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=None,
        created_at="2026-06-11T00:01:45.000Z",
    )
    assert missing["prepared"] is False
    assert missing["refusal_code"] == "reconciliation_missing"

    executable = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation={**accepted, "allowed_to_execute": True},
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert executable["prepared"] is False
    assert executable["refusal_code"] == "reconciliation_allows_execution"

    private_ref = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=accepted,
        created_at="2026-06-11T00:01:45.000Z",
        audit_lineage_refs=["raw_jwt_private_ref"],
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert private_ref["prepared"] is False
    assert private_ref["refusal_code"] == "private_or_forbidden_packet_field"

    execution_flag = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=accepted,
        created_at="2026-06-11T00:01:45.000Z",
        packet_flags=["order_created"],
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert execution_flag["prepared"] is False
    assert execution_flag["refusal_code"] == "packet_indicates_forbidden_execution"

    ambiguous = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=accepted,
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
    )
    assert ambiguous["prepared"] is False
    assert ambiguous["refusal_code"] == "risk_context_missing_or_ambiguous"

    missing_confirmation = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="missing_evidence_packet",
        reconciliation=accepted,
        created_at="2026-06-11T00:01:45.000Z",
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert missing_confirmation["prepared"] is True
    assert missing_confirmation["packet"]["eligibility_status"] == "missing_evidence"
    assert "confirmation:buyer_confirmation" in missing_confirmation["packet"]["missing_requirements"]

    kind_mismatch = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=_reconciliation(response_status="needs_human_review"),
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["buyer_confirmation"],
        amount_minor_units=200000,
        currency="INR",
        total_quantity=1,
    )
    assert kind_mismatch["prepared"] is False
    assert kind_mismatch["refusal_code"] == "packet_kind_status_mismatch"


def test_c6w8_fails_closed_for_stale_mandate_evidence() -> None:
    old_mandate = prepare_agenticorg_c6w8_eligibility_packet(
        packet_kind="execution_handoff_eligibility_packet",
        reconciliation=_mandate_reconciliation(),
        created_at="2026-06-11T00:01:45.000Z",
        provided_confirmations=["mandate_capability_evidence"],
        mandate_evidence_issued_at="2026-06-10T23:58:00.000Z",
    )
    assert old_mandate["prepared"] is False
    assert old_mandate["refusal_code"] == "mandate_evidence_stale"


def test_c6w8_documents_eligibility_packet_boundaries() -> None:
    doc = C6W8_DOC_PATH.read_text(encoding="utf-8")
    for heading in (
        "Scope",
        "Packet Kinds",
        "Eligibility Statuses",
        "Required Fields",
        "Fail-Closed Rules",
        "Evidence Lineage",
        "Eligibility Is Not Execution",
        "Toll Booth Boundary",
        "What This Does Not Enable",
        "Future Slices",
    ):
        assert f"## {heading}" in doc
    assert "allowed_to_execute remains false" in doc
    assert "eligibility_only remains true" in doc
