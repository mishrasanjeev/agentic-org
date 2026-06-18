from __future__ import annotations

from pathlib import Path

from core.commerce.oacp_artifacts import (
    OACP_C6W3_VALID_ARTIFACT_FIXTURES,
    evaluate_agenticorg_c6w5_commitment_boundary,
    prepare_agenticorg_c6w6_commitment_envelope,
    reconcile_agenticorg_c6w7_prepared_response,
)

C6W7_DOC_PATH = Path("docs/reports/commerce-agent-c6w7-response-reconciliation.md")


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


def _envelope(kind: str = "buyer_confirmation_request") -> dict[str, object]:
    prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind=kind,  # type: ignore[arg-type]
        resolver_decision=_decision(),
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["prepared-price-ref"],
        unsupported_capabilities=["checkout_create", "payment_authorize"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert prepared["generated"] is True
    return prepared["envelope"]  # type: ignore[return-value]


def test_c6w7_reconciles_buyer_and_merchant_responses_as_prepared_only() -> None:
    buyer = reconcile_agenticorg_c6w7_prepared_response(
        envelope=_envelope(),
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-confirmation-cache-ref"],
        response_claimed_action="price_lock",
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert buyer["reconciled"] is True
    reconciliation = buyer["reconciliation"]
    assert reconciliation["response_kind"] == "buyer_confirmation_response"
    assert reconciliation["response_status"] == "accepted_for_preparation"
    assert reconciliation["requested_action"] == "price_lock"
    assert reconciliation["allowed_to_execute"] is False
    assert reconciliation["prepared_only"] is True
    assert reconciliation["reconciled_only"] is True
    assert reconciliation["non_authoritative_for_transaction"] is True
    assert reconciliation["no_checkout_payment_enablement"] is True
    assert reconciliation["no_live_provider_enablement"] is True
    assert reconciliation["no_public_discovery_enablement"] is True
    assert "price_C6W3" in reconciliation["source_artifact_ids"]
    assert "buyer-confirmation-cache-ref" in reconciliation["response_evidence_refs"]
    assert "http" not in reconciliation["next_system_step_label"]
    assert "no order, hold, checkout, payment" in reconciliation["buyer_safe_message"]
    assert reconciliation["commerce_facts_invented"] is False

    merchant_prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="merchant_confirmation_request",
        resolver_decision=_decision(),
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["prepared-price-ref"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert merchant_prepared["generated"] is True
    merchant = reconcile_agenticorg_c6w7_prepared_response(
        envelope=merchant_prepared["envelope"],
        response_kind="merchant_confirmation_response",
        response_status="needs_human_review",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["merchant-confirmation-cache-ref"],
        response_claimed_envelope_id=merchant_prepared["envelope"]["envelope_id"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert merchant["reconciled"] is True
    assert merchant["reconciliation"]["allowed_to_prepare"] is False
    assert merchant["reconciliation"]["next_system_step_label"] == "human_review_reconciliation_label"


def test_c6w7_reconciles_refresh_mandate_and_support_response_kinds() -> None:
    refresh_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="ask_refresh_source_facts",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    refresh_prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="seller_source_refresh_request",
        resolver_decision=refresh_decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["refresh-request-ref"],
    )
    assert refresh_prepared["generated"] is True
    refresh = reconcile_agenticorg_c6w7_prepared_response(
        envelope=refresh_prepared["envelope"],
        response_kind="seller_source_refresh_response",
        response_status="needs_source_refresh",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["refreshed-artifact-id-only-ref"],
    )
    assert refresh["reconciled"] is True
    assert refresh["reconciliation"]["response_status"] == "needs_source_refresh"

    mandate_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="prepare_mandate_capability_check_request",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    mandate_prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="mandate_capability_evidence_request",
        resolver_decision=mandate_decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["mandate-request-ref"],
    )
    assert mandate_prepared["generated"] is True
    mandate = reconcile_agenticorg_c6w7_prepared_response(
        envelope=mandate_prepared["envelope"],
        response_kind="mandate_capability_evidence_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["mandate-capability-cache-ref"],
        response_evidence_issued_at="2026-06-11T00:00:45.000Z",
    )
    assert mandate["reconciled"] is True
    assert mandate["reconciliation"]["max_ttl_seconds"] <= 120

    support_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="support_escalation_sla_promise",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
        currency="USD",
        amount_minor_units=5000,
        total_quantity=1,
    )
    support_prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="support_escalation_preparation",
        resolver_decision=support_decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["support-request-ref"],
        currency="USD",
        amount_minor_units=5000,
        total_quantity=1,
    )
    assert support_prepared["generated"] is True
    support = reconcile_agenticorg_c6w7_prepared_response(
        envelope=support_prepared["envelope"],
        response_kind="support_escalation_response",
        response_status="rejected",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["support-response-ref"],
        currency="USD",
        amount_minor_units=5000,
        total_quantity=1,
    )
    assert support["reconciled"] is True
    assert "does not create operational obligations" in support["reconciliation"]["seller_safe_message"]


def test_c6w7_fails_closed_for_unsafe_or_mismatched_response_evidence() -> None:
    prepared = _envelope()
    missing = reconcile_agenticorg_c6w7_prepared_response(
        envelope=None,
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-ref"],
    )
    assert missing["reconciled"] is False
    assert missing["refusal_code"] == "prepared_envelope_missing"

    executable = reconcile_agenticorg_c6w7_prepared_response(
        envelope={**prepared, "allowed_to_execute": True},
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-ref"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert executable["reconciled"] is False
    assert executable["refusal_code"] == "prepared_envelope_allows_execution"

    mismatch = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared,
        response_kind="merchant_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["merchant-ref"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert mismatch["reconciled"] is False
    assert mismatch["refusal_code"] == "response_kind_envelope_mismatch"

    private_ref = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared,
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["https://private.example/customer/address"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert private_ref["reconciled"] is False
    assert private_ref["refusal_code"] == "private_or_forbidden_response_field"

    execution = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared,
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-ref"],
        response_flags=["payment_executed"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert execution["reconciled"] is False
    assert execution["refusal_code"] == "response_indicates_forbidden_execution"


def test_c6w7_fails_closed_for_stale_ambiguous_old_mandate_and_conflicting_evidence() -> None:
    prepared = _envelope()
    stale = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared,
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:10:30.000Z",
        response_evidence_refs=["buyer-ref"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert stale["reconciled"] is False
    assert stale["refusal_code"] == "source_freshness_missing_or_stale"

    ambiguous = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared,
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-ref"],
    )
    assert ambiguous["reconciled"] is False
    assert ambiguous["refusal_code"] == "risk_context_missing_or_ambiguous"

    conflict = reconcile_agenticorg_c6w7_prepared_response(
        envelope=prepared,
        response_kind="buyer_confirmation_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["buyer-ref"],
        response_claimed_action="inventory_hold",
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert conflict["reconciled"] is False
    assert conflict["refusal_code"] == "response_conflicts_with_envelope"

    mandate_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="prepare_mandate_capability_check_request",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    mandate_prepared = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="mandate_capability_evidence_request",
        resolver_decision=mandate_decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["mandate-request-ref"],
    )
    assert mandate_prepared["generated"] is True
    old_mandate = reconcile_agenticorg_c6w7_prepared_response(
        envelope=mandate_prepared["envelope"],
        response_kind="mandate_capability_evidence_response",
        response_status="accepted_for_preparation",
        created_at="2026-06-11T00:01:30.000Z",
        response_evidence_refs=["mandate-cache-ref"],
        response_evidence_issued_at="2026-06-10T23:58:00.000Z",
    )
    assert old_mandate["reconciled"] is False
    assert old_mandate["refusal_code"] == "mandate_evidence_stale"


def test_c6w7_documents_response_reconciliation_boundaries() -> None:
    doc = C6W7_DOC_PATH.read_text(encoding="utf-8")
    for heading in (
        "Scope",
        "Response Evidence Kinds",
        "Reconciliation Output",
        "Status Enum",
        "Fail-Closed Rules",
        "Human And Source Responses",
        "Toll Booth Boundary",
        "What This Does Not Enable",
        "Future Slices",
    ):
        assert f"## {heading}" in doc
    assert "allowed_to_execute remains false" in doc
    assert "reconciled_only remains true" in doc
