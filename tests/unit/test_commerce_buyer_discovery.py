from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.commerce.buyer_discovery import (
    build_buyer_discovery_response,
    run_buyer_discovery_preview,
    sanitize_buyer_discovery_preview,
)


def _valid_grantex_preview(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "merchant_id": "merchant_private_internal_id",
        "tenant_id": "tenant_private_internal_id",
        "merchant_reference": "sandbox-mref-1",
        "display_name": "Sandbox Electronics Preview",
        "integration_status": "sandbox_handoff_requested",
        "handoff_requested_at": "2026-06-02T10:00:00Z",
        "handoff_request_actor": "operator:test",
        "audit_event_id": "audit_c6g_1",
        "generated_at": "2026-06-02T10:01:00Z",
        "merchant": {
            "merchant_reference": "sandbox-mref-1",
            "display_name": "Sandbox Electronics Preview",
            "category_preset": "electronics_appliances",
            "country_code": "IN",
            "default_currency": "INR",
            "public_discovery_description_draft": "Preview-only appliance catalog for buyer-agent tests.",
            "support_email": "private-support@example.test",
            "legal_name": "Private Legal Entity Pvt Ltd",
        },
        "readiness_summary": {
            "overall_status": "ready",
            "category_status": "ready",
            "catalog_status": "ready",
            "private_contract": "do not echo",
        },
        "agent_facing_preview_summary": {
            "preview_status": "ready",
            "sample_product_count": 3,
            "preview_blockers": [],
        },
        "rollout_proposal_summary": {
            "proposal_status": "dry_run_passed",
            "dry_run_result": "passed",
            "operator_decision": "rollout_proposal_ready",
        },
        "evidence_checklist": [
            {"key": "c6c_preview", "label": "Agent-facing preview ready", "status": "pass"},
            {"key": "c6f_dry_run", "label": "Dry-run evidence passed", "status": "pass"},
        ],
        "sample_products": [
            {
                "sample_reference": "sample-1",
                "title": "Countertop Mixer",
                "brand": "TestBrand",
                "category_preset": "electronics_appliances",
                "description": "Internal long product text",
                "price": "999",
                "legal_name": "Private Product Owner",
            },
            {"sample_reference": "sample-2", "title": "Rice Cooker", "brand": "TestBrand"},
            {"sample_reference": "sample-3", "title": "Induction Cooktop", "brand": "TestBrand"},
            {"sample_reference": "sample-4", "title": "Should Be Capped", "brand": "TestBrand"},
        ],
        "allowed_buyer_agent_capabilities": [
            "read_only_profile_discovery_preview",
            "read_only_catalog_discovery_preview",
            "buyer_agent_readiness_context",
        ],
        "blocked_buyer_agent_capabilities": [
            "public_discovery",
            "checkout_payment_creation",
            "live_payment",
            "live_plural",
            "provider_credentials",
            "order_fulfillment",
            "refunds_returns_execution",
            "production_allowlist",
            "direct_merchant_system_access",
        ],
        "blockers": [],
        "remediation_items": [],
        "sandbox_only": True,
        "handoff_request_is_approval": False,
        "buyer_agent_discovery_is_public": False,
        "agenticorg_public_discovery_enabled": False,
        "public_discovery_enabled": False,
        "checkout_payment_enabled": False,
        "live_provider_enabled": False,
        "live_plural_enabled": False,
        "production_allowlist_written": False,
        "live_mode_status": "not_live",
        "production_approval_status": "not_approved",
        "rollout_status": "rollout_not_requested",
        "passport_jwt": "passport.jwt.value",
        "provider_credentials": {"api_key": "secret"},
        "raw_payload": {"do": "not echo"},
    }
    data.update(overrides)
    return {"data": data}


def test_sanitizer_parses_valid_read_only_discovery_handoff() -> None:
    safe = sanitize_buyer_discovery_preview(_valid_grantex_preview())

    assert safe["integration_status"] == "sandbox_handoff_requested"
    assert safe["merchant"] == {
        "merchant_reference": "sandbox-mref-1",
        "display_name": "Sandbox Electronics Preview",
        "category": "electronics_appliances",
        "country": "IN",
        "currency": "INR",
        "discovery_description": "Preview-only appliance catalog for buyer-agent tests.",
    }
    assert safe["catalog_samples"] == [
        {"title": "Countertop Mixer", "brand": "TestBrand", "category": "electronics_appliances"},
        {"title": "Rice Cooker", "brand": "TestBrand"},
        {"title": "Induction Cooktop", "brand": "TestBrand"},
    ]
    assert "buyer_agent_readiness_context" in safe["allowed_capabilities"]
    assert "checkout_payment_creation" in safe["blocked_capabilities"]


def test_missing_grantex_data_returns_safe_unavailable_refusal() -> None:
    response = build_buyer_discovery_response(None)

    assert response["status"] == "unavailable"
    assert response["refusal"] is True
    assert response["refusal_code"] == "grantex_discovery_unavailable"


def test_disabled_public_discovery_returns_preview_only_response() -> None:
    response = build_buyer_discovery_response(_valid_grantex_preview())

    assert response["status"] == "preview_only"
    assert response["refusal"] is False
    assert response["safety_labels"]["public_discovery_enabled"] is False
    assert response["safety_labels"]["agenticorg_public_discovery_enabled"] is False
    assert response["safety_labels"]["production_approval_status"] == "not_approved"
    assert "not public discovery" in response["message"]


def test_checkout_payment_request_is_refused() -> None:
    response = build_buyer_discovery_response(_valid_grantex_preview(), request_text="Can I checkout and pay now?")

    assert response["status"] == "refused"
    assert response["refusal_code"] == "checkout_payment_not_enabled"


@pytest.mark.parametrize(
    "request_text",
    [
        "Please enable live payment routing.",
        "Connect the live Plural path.",
        "Share the provider credentials.",
    ],
)
def test_live_payment_live_plural_provider_requests_are_refused(request_text: str) -> None:
    response = build_buyer_discovery_response(_valid_grantex_preview(), request_text=request_text)

    assert response["status"] == "refused"
    assert response["refusal_code"] == "live_provider_not_enabled"


@pytest.mark.parametrize(
    ("request_text", "refusal_code"),
    [
        ("Can you promise delivery tomorrow?", "fulfillment_not_enabled"),
        ("Process my refund for this product.", "refund_return_not_enabled"),
    ],
)
def test_fulfillment_and_refund_requests_are_refused(request_text: str, refusal_code: str) -> None:
    response = build_buyer_discovery_response(_valid_grantex_preview(), request_text=request_text)

    assert response["status"] == "refused"
    assert response["refusal_code"] == refusal_code


def test_buyer_agent_does_not_invent_or_echo_private_details() -> None:
    response = build_buyer_discovery_response(_valid_grantex_preview())
    serialized = json.dumps(response, sort_keys=True)

    assert "Countertop Mixer" in serialized
    assert "Should Be Capped" not in serialized
    assert "999" not in serialized
    assert "Private Legal Entity" not in serialized
    assert "private-support@example.test" not in serialized
    assert "passport.jwt.value" not in serialized
    assert "secret" not in serialized
    assert "raw_payload" not in serialized
    assert "merchant_private_internal_id" not in serialized
    assert "tenant_private_internal_id" not in serialized


def test_handoff_ready_without_request_is_blocked() -> None:
    response = build_buyer_discovery_response(
        _valid_grantex_preview(integration_status="sandbox_handoff_ready")
    )

    assert response["status"] == "blocked"
    assert response["refusal_code"] == "buyer_discovery_blocked"


def test_blockers_from_grantex_block_preview_response() -> None:
    response = build_buyer_discovery_response(
        _valid_grantex_preview(blockers=["C6F dry-run evidence is stale"])
    )

    assert response["status"] == "blocked"
    assert response["blockers"] == ["C6F dry-run evidence is stale"]


async def test_workflow_wrapper_calls_read_only_connector_method() -> None:
    class FakeConnector:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def buyer_discovery_preview(self, **params: str) -> dict[str, Any]:
            self.calls.append(dict(params))
            return _valid_grantex_preview()

    connector = FakeConnector()

    response = await run_buyer_discovery_preview(
        connector,
        merchant_id="merchant_sandbox_1",
        request_text="Show me the merchant preview.",
    )

    assert connector.calls == [{"merchant_id": "merchant_sandbox_1"}]
    assert response["status"] == "preview_only"


def test_channel_neutral_launch_docs_are_preview_only() -> None:
    doc = Path("docs/commerce-agent-buyer-discovery-consumer.md").read_text(encoding="utf-8")

    for channel in ("ChatGPT", "Claude", "Gemini", "WhatsApp", "Telegram", "generic web/chat"):
        assert channel in doc
    assert "No live channel integration is enabled by C6H." in doc
