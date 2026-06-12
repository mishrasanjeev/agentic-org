from __future__ import annotations

from pathlib import Path

from core.commerce.oacp_artifacts import (
    OACP_C6W3_VALID_ARTIFACT_FIXTURES,
    evaluate_agenticorg_c6w5_commitment_boundary,
    prepare_agenticorg_c6w6_commitment_envelope,
)

C6W6_DOC_PATH = Path("docs/reports/commerce-agent-c6w6-prepared-commitment-envelopes.md")


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


def _price_lock_decision() -> dict[str, object]:
    return evaluate_agenticorg_c6w5_commitment_boundary(
        action="price_lock",
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


def test_c6w6_prepares_buyer_and_merchant_confirmation_envelopes() -> None:
    decision = _price_lock_decision()
    buyer = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="buyer_confirmation_request",
        resolver_decision=decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["merchant-ledger-preview-ref", "https://private.example/merchant/api"],
        unsupported_capabilities=["checkout_create", "payment_authorize"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )

    assert buyer["generated"] is True
    envelope = buyer["envelope"]
    assert envelope["envelope_kind"] == "buyer_confirmation_request"
    assert envelope["envelope_status"] == "prepared_only"
    assert envelope["action_class"] == "commitment_bound"
    assert envelope["requested_action"] == "price_lock"
    assert envelope["allowed_to_execute"] is False
    assert envelope["prepared_only"] is True
    assert envelope["non_authoritative_for_transaction"] is True
    assert envelope["no_checkout_payment_enablement"] is True
    assert envelope["no_live_provider_enablement"] is True
    assert envelope["no_public_discovery_enablement"] is True
    assert envelope["next_system_step_label"] == "local_human_confirmation_handoff"
    assert "price_C6W3" in envelope["source_artifact_ids"]
    assert "price" in envelope["required_fresh_artifact_families"]
    assert "redacted_private_evidence_ref" in envelope["redacted_evidence_refs"]
    assert "http" not in envelope["next_human_step"]
    assert envelope["commerce_facts_invented"] is False

    merchant = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="merchant_confirmation_request",
        resolver_decision=decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["price-evidence-ref"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert merchant["generated"] is True
    assert "no order, hold, checkout, or payment is created" in merchant["envelope"]["seller_safe_message"]
    assert merchant["envelope"]["next_system_step_label"] == "merchant_source_confirmation_handoff_label"


def test_c6w6_prepares_refresh_mandate_and_support_handoff_envelopes() -> None:
    refresh_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="ask_refresh_source_facts",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    refresh = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="seller_source_refresh_request",
        resolver_decision=refresh_decision,
        created_at="2026-06-11T00:01:15.000Z",
    )
    assert refresh["generated"] is True
    assert refresh["envelope"]["next_system_step_label"] == "seller_source_refresh_handoff_label"

    mandate_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="prepare_mandate_capability_check_request",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    mandate = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="mandate_capability_evidence_request",
        resolver_decision=mandate_decision,
        created_at="2026-06-11T00:01:15.000Z",
    )
    assert mandate["generated"] is True
    assert mandate["envelope"]["max_ttl_seconds"] <= 120
    assert "no provider rail is called" in mandate["envelope"]["seller_safe_message"]

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
    support = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="support_escalation_preparation",
        resolver_decision=support_decision,
        created_at="2026-06-11T00:01:15.000Z",
        currency="USD",
        amount_minor_units=5000,
        total_quantity=1,
    )
    assert support["generated"] is True
    assert "must not promise SLA" in support["envelope"]["seller_safe_message"]


def test_c6w6_fails_closed_for_missing_executable_blocked_and_ambiguous_inputs() -> None:
    missing = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="buyer_confirmation_request",
        resolver_decision=None,
        created_at="2026-06-11T00:01:15.000Z",
    )
    assert missing["generated"] is False
    assert missing["refusal_code"] == "resolver_decision_missing"

    decision = _price_lock_decision()
    executable = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="buyer_confirmation_request",
        resolver_decision={**decision, "allowed_to_execute": True},
        created_at="2026-06-11T00:01:15.000Z",
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert executable["generated"] is False
    assert executable["refusal_code"] == "resolver_decision_allows_execution"

    blocked_decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="live_payment_execution",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
    )
    blocked = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="buyer_confirmation_request",
        resolver_decision=blocked_decision,
        created_at="2026-06-11T00:01:15.000Z",
    )
    assert blocked["generated"] is False
    assert blocked["refusal_code"] == "action_blocked_in_c6w6"

    ambiguous = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="buyer_confirmation_request",
        resolver_decision=decision,
        created_at="2026-06-11T00:01:15.000Z",
    )
    assert ambiguous["generated"] is False
    assert ambiguous["refusal_code"] == "risk_context_missing_or_ambiguous"


def test_c6w6_documents_prepared_only_boundaries() -> None:
    doc = C6W6_DOC_PATH.read_text(encoding="utf-8")
    for heading in (
        "Scope",
        "Envelope Kinds",
        "Required Fields",
        "Fail-Closed Rules",
        "Confirmation Handoff",
        "Toll Booth Boundary",
        "What This Does Not Enable",
        "Future Slices",
    ):
        assert f"## {heading}" in doc
    assert "allowed_to_execute remains false" in doc
