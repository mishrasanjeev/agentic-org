from __future__ import annotations

from pathlib import Path

from core.commerce.oacp_artifacts import (
    OACP_C6W3_VALID_ARTIFACT_FIXTURES,
    evaluate_agenticorg_c6w5_commitment_boundary,
)

C6W5_DOC_PATH = Path("docs/reports/commerce-agent-c6w5-commitment-boundary-resolver.md")


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


def test_c6w5_allows_non_binding_preview_with_source_and_freshness_labels() -> None:
    result = evaluate_agenticorg_c6w5_commitment_boundary(
        action="compare_catalog_summaries",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
    )

    assert result["action_class"] == "non_binding_preview"
    assert result["allowed_to_preview"] is True
    assert result["allowed_to_prepare"] is False
    assert result["allowed_to_execute"] is False
    assert result["risk_tier"] == "informational"
    assert result["source_authority"] == "grantex_canonical_oacp_artifact_authority"
    assert "catalog_snapshot_C6W3" in result["source_artifact_ids"]
    assert "checkout_create" in result["blocked_capabilities"]
    assert result["commerce_facts_invented"] is False
    assert "not purchase approval" in result["buyer_safe_message"]


def test_c6w5_prepares_commitment_bound_actions_offline_without_execution() -> None:
    result = evaluate_agenticorg_c6w5_commitment_boundary(
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

    assert result["action_class"] == "commitment_bound"
    assert result["allowed_to_preview"] is True
    assert result["allowed_to_prepare"] is True
    assert result["allowed_to_execute"] is False
    assert result["refusal_or_escalation_reason"] == "prepared_not_executed_c6w5"
    assert result["offline_mode_status"] == "offline_prepared_not_executed"
    assert "price" in result["required_fresh_artifact_families"]


def test_c6w5_fails_closed_for_missing_stale_or_ambiguous_commitment_inputs() -> None:
    without_price = [artifact for artifact in _artifacts() if artifact["envelope"]["artifact_type"] != "price"]
    missing = evaluate_agenticorg_c6w5_commitment_boundary(
        action="price_lock",
        cached_artifacts=without_price,
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert missing["allowed_to_prepare"] is False
    assert missing["refusal_or_escalation_reason"] == "required_artifact_missing:price"

    price = OACP_C6W3_VALID_ARTIFACT_FIXTURES["price"]
    stale_price = {"envelope": {**price["envelope"], "freshness_class": "stale"}, "payload": price["payload"]}
    stale = evaluate_agenticorg_c6w5_commitment_boundary(
        action="price_lock",
        cached_artifacts=[artifact for artifact in _artifacts() if artifact["envelope"]["artifact_type"] != "price"]
        + [stale_price],
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert stale["allowed_to_prepare"] is False
    assert stale["refusal_or_escalation_reason"] == "artifact_freshness_missing_stale_or_ambiguous"

    ambiguous = evaluate_agenticorg_c6w5_commitment_boundary(
        action="price_lock",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        revocation_snapshot_age_seconds=30,
    )
    assert ambiguous["allowed_to_prepare"] is False
    assert ambiguous["refusal_or_escalation_reason"] == "risk_context_missing_or_ambiguous"


def test_c6w5_blocks_live_and_publication_actions_and_documents_boundaries() -> None:
    blocked = evaluate_agenticorg_c6w5_commitment_boundary(
        action="live_payment_execution",
        cached_artifacts=_artifacts(),
        adapter_preview=_preview(),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
    )

    assert blocked["action_class"] == "always_blocked"
    assert blocked["allowed_to_preview"] is False
    assert blocked["allowed_to_prepare"] is False
    assert blocked["allowed_to_execute"] is False
    assert blocked["offline_mode_status"] == "offline_blocked"

    doc = C6W5_DOC_PATH.read_text(encoding="utf-8")
    for heading in (
        "Scope",
        "Commitment Boundary Model",
        "Offline Commitment Mode",
        "TTL And Risk Defaults",
        "What This Does Not Enable",
        "Future Slices",
    ):
        assert f"## {heading}" in doc
    assert "AgenticOrg does not invent commerce facts" in doc
    assert "Grantex does not become a synchronous toll booth" in doc
