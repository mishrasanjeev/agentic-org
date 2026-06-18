from __future__ import annotations

import json
from typing import Any

from core.commerce.buyer_discovery import build_buyer_discovery_response
from core.commerce.buyer_session import build_channel_neutral_buyer_response
from core.commerce.sales_guardrails import validate_claims_against_tool_data


def _preview_payload(sample: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "merchant_id": "merchant_private_c6u4",
        "tenant_id": "tenant_private_c6u4",
        "merchant_reference": "merchant_synthetic_c6u4",
        "display_name": "Synthetic Appliance Preview",
        "integration_status": "sandbox_handoff_requested",
        "generated_at": "2026-06-09T00:00:00Z",
        "audit_event_id": "audit_synthetic_c6u4",
        "merchant": {
            "merchant_reference": "merchant_synthetic_c6u4",
            "display_name": "Synthetic Appliance Preview",
            "category_preset": "electronics_appliances",
            "country_code": "IN",
            "default_currency": "INR",
            "public_discovery_description_draft": "Synthetic public-safe appliance preview.",
        },
        "readiness_summary": {
            "overall_status": "ready",
            "catalog_status": "ready",
        },
        "agent_facing_preview_summary": {
            "preview_status": "ready",
            "sample_product_count": 1,
        },
        "rollout_proposal_summary": {
            "proposal_status": "dry_run_passed",
        },
        "evidence_checklist": [
            {"key": "source_freshness", "label": "Source freshness checked", "status": "pass"}
        ],
        "sample_products": [sample],
        "allowed_buyer_agent_capabilities": ["read_only_catalog_discovery_preview"],
        "blocked_buyer_agent_capabilities": [
            "public_discovery",
            "checkout_payment_creation",
            "live_payment",
            "live_plural",
            "provider_credentials",
            "order_fulfillment",
            "refunds_returns_execution",
            "production_allowlist",
        ],
        "blockers": [],
        "remediation_items": [],
        "sandbox_only": True,
        "buyer_agent_discovery_is_public": False,
        "agenticorg_public_discovery_enabled": False,
        "public_discovery_enabled": False,
        "checkout_payment_enabled": False,
        "live_provider_enabled": False,
        "live_plural_enabled": False,
        "production_approval_status": "not_approved",
        "live_mode_status": "not_live",
    }
    data.update(overrides)
    return {"data": data}


def _sample_product(**overrides: Any) -> dict[str, Any]:
    sample = {
        "title": "Synthetic Lamp",
        "brand": "SyntheticBrand",
        "category_preset": "electronics_appliances",
        "source_system": "manual_upload",
        "last_synced_at": "2026-06-09T00:00:00Z",
        "variants": [
            {
                "sku": "variant_synthetic_c6u4",
                "price_amount": 1299,
                "currency": "INR",
                "availability_status": "in_stock",
                "warranty_summary": "Synthetic one-year limited warranty summary.",
                "return_policy_summary": "Synthetic seven-day unopened return summary.",
            }
        ],
    }
    sample.update(overrides)
    return sample


def test_preview_price_is_projected_as_non_final_when_tax_is_missing() -> None:
    response = build_buyer_discovery_response(_preview_payload(_sample_product()))

    assert response["status"] == "preview_only"
    facts = response["catalog_samples"][0]["commercial_facts"]
    assert facts["price"]["status"] == "preview_only"
    assert facts["price"]["display"] == "INR 1299"
    assert facts["price"]["final_price_confirmed"] is False
    assert "Final tax" in facts["tax"]["message"]
    assert facts["tax"]["status"] == "unknown"
    assert "delivery" in facts["unsupported"]
    assert "settlement" in facts["unsupported"]


def test_missing_warranty_return_delivery_and_support_are_not_invented() -> None:
    response = build_buyer_discovery_response(
        _preview_payload(
            _sample_product(
                variants=[
                    {
                        "sku": "variant_synthetic_c6u4_missing",
                        "price_amount": 799,
                        "currency": "INR",
                        "availability_status": "in_stock",
                    }
                ]
            )
        )
    )

    facts = response["catalog_samples"][0]["commercial_facts"]
    assert facts["warranty"]["status"] == "unknown"
    assert facts["return_policy"]["status"] == "unknown"
    for unsupported in ("delivery", "support", "fulfillment", "refunds", "payout"):
        assert unsupported in facts["unsupported"]


def test_stale_inventory_and_private_source_metadata_are_buyer_safe() -> None:
    response = build_buyer_discovery_response(
        _preview_payload(
            _sample_product(
                source_system="https://merchant-private.internal/pim",
                raw_payload={"token": "synthetic-secret"},
                variants=[
                    {
                        "sku": "variant_synthetic_c6u4_stale",
                        "price_amount": 799,
                        "currency": "INR",
                        "availability_status": "unknown",
                        "stale": True,
                        "last_synced_at": "2026-06-01T00:00:00Z",
                        "source_system": "merchant-private-pim",
                    }
                ],
            )
        )
    )

    sample = response["catalog_samples"][0]
    assert sample["commercial_facts"]["inventory"]["status"] == "unknown_or_stale"
    assert sample["commercial_facts"]["inventory"]["caution_required"] is True
    assert sample["commercial_facts"]["inventory"]["stock_promise"] is False
    assert sample["source_summary"]["source"] == "grantex_controlled_source"
    assert sample["source_summary"]["freshness_status"] == "stale"

    serialized = json.dumps(response, sort_keys=True).lower()
    assert "merchant-private.internal" not in serialized
    assert "raw_payload" not in serialized
    assert "synthetic-secret" not in serialized
    assert "merchant_private_c6u4" not in serialized
    assert "tenant_private_c6u4" not in serialized


def test_channel_response_preserves_projection_but_keeps_public_discovery_hidden() -> None:
    response = build_channel_neutral_buyer_response(
        _preview_payload(_sample_product()),
        request_text="Show product price and availability.",
        channel="web_chat",
    )

    assert response["status"] == "preview_only"
    assert response["evidence_summary"]["preview_only"] is True
    assert response["evidence_summary"]["safety_labels"]["public_discovery_enabled"] is False
    sample = response["catalog_samples"][0]
    assert sample["commercial_facts"]["price"]["final_price_confirmed"] is False
    assert sample["commercial_facts"]["inventory"]["stock_promise"] is False


def test_unsupported_commercial_claims_are_refused_without_grantex_source_facts() -> None:
    result = validate_claims_against_tool_data(
        (
            "This is the final price with all taxes included, guaranteed in stock, "
            "delivery tomorrow, customer support, refund, settlement, payout, "
            "tracking, discount, coupon, and no-cost EMI."
        ),
        {"catalog_samples": [{"title": "Synthetic Lamp"}]},
    )

    assert result["allowed"] is False
    unsupported = set(result["details"]["unsupported_claims"])
    assert {
        "final_price",
        "inventory",
        "delivery",
        "support",
        "refund",
        "settlement",
        "fulfillment",
        "discount",
        "emi",
    }.issubset(unsupported)
