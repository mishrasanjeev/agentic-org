from __future__ import annotations

from pathlib import Path

from core.commerce.oacp_artifacts import (
    OACP_C6W4_PROTOCOL_ADAPTER_SURFACES,
    evaluate_agenticorg_oacp_adapter_preview_use,
    summarize_oacp_adapter_preview_for_buyer,
)

C6W4_DOC_PATH = Path("docs/reports/commerce-agent-c6w4-oacp-adapter-preview-consumption.md")


def _preview(surface: str = "a2a_agent_card_task_capability") -> dict[str, object]:
    return {
        "generated": True,
        "status": "preview_only",
        "surface": surface,
        "source_artifact_ids": ["merchant_capability_C6W3", "seller_agent_capability_C6W3", "protocol_adapter_C6W3"],
        "source_artifact_families": ["merchant_capability", "seller_agent_capability", "protocol_adapter"],
        "source_authority": "grantex_canonical_oacp_artifact_authority",
        "generated_at": "2026-06-11T00:01:00.000Z",
        "expires_at": "2026-06-11T00:05:00.000Z",
        "max_ttl_seconds": 240,
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
                {"artifact_id": "merchant_capability_C6W3", "artifact_type": "merchant_capability"},
                {"artifact_id": "seller_agent_capability_C6W3", "artifact_type": "seller_agent_capability"},
            ],
            "tasks": [
                {
                    "task": "answer_policy_question",
                    "mode": "non_binding_preview",
                    "transaction_authority": False,
                }
            ],
        },
    }


def test_c6w4_agenticorg_accepts_valid_preview_surfaces_for_non_binding_use() -> None:
    for surface in OACP_C6W4_PROTOCOL_ADAPTER_SURFACES:
        result = evaluate_agenticorg_oacp_adapter_preview_use(preview=_preview(surface), action="browse")
        assert result["allowed"] is True
        assert result["commerce_facts_invented"] is False
        assert result["freshness_tier"] == "fresh"
        assert "protocol_adapter_C6W3" in result["source_artifact_ids"]


def test_c6w4_refuses_adapter_previews_as_transaction_or_provider_authority() -> None:
    preview = _preview()

    for action in (
        "checkout_create",
        "payment_authorize",
        "payment_intent",
        "payment_capture",
        "live_provider_call",
        "merchant_private_api_call",
        "protocol_publication",
    ):
        result = evaluate_agenticorg_oacp_adapter_preview_use(preview=preview, action=action)
        assert result["allowed"] is False
        assert result["refusal_code"] == "adapter_not_transaction_authority"


def test_c6w4_refuses_preview_missing_safety_source_or_known_surface() -> None:
    missing_flag = {**_preview(), "no_checkout_payment_enablement": False}
    assert evaluate_agenticorg_oacp_adapter_preview_use(
        preview=missing_flag,
        action="browse",
    )["refusal_code"] == "adapter_preview_not_buyer_safe"

    missing_source = {**_preview(), "source_artifact_ids": []}
    assert evaluate_agenticorg_oacp_adapter_preview_use(
        preview=missing_source,
        action="browse",
    )["refusal_code"] == "adapter_source_missing"

    unknown_surface = {**_preview(), "surface": "third_party_unbounded_card"}
    assert evaluate_agenticorg_oacp_adapter_preview_use(
        preview=unknown_surface,
        action="browse",
    )["refusal_code"] == "adapter_surface_unknown"


def test_c6w4_buyer_summary_preserves_source_freshness_and_unsupported_wording() -> None:
    summary = summarize_oacp_adapter_preview_for_buyer(_preview("mcp_tool_resource_capability"))

    assert summary["allowed"] is True
    assert "sourced from Grantex OACP artifacts" in summary["wording"]
    assert summary["freshness_tier"] == "fresh"
    assert "merchant_capability_C6W3" in summary["source_artifact_ids"]
    assert "checkout_create" in summary["unsupported_capabilities"]
    assert summary["non_authoritative_for_transaction"] is True
    assert summary["commerce_facts_invented"] is False


def test_c6w4_agenticorg_doc_captures_preview_consumption_boundaries() -> None:
    doc = C6W4_DOC_PATH.read_text(encoding="utf-8")

    for heading in (
        "Scope",
        "Buyer-Agent Handling",
        "Seller-Agent Handling",
        "Third-Party Agent Cards",
        "Blocked Actions",
        "What This Does Not Enable",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg does not invent commerce facts" in doc
    assert "adapter previews are not transaction authority" in doc
