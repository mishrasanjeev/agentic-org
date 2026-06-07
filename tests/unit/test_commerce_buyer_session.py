from __future__ import annotations

import json
from typing import Any

import pytest

from core.commerce.buyer_session import (
    BUYER_SESSION_CONTRACT,
    build_channel_neutral_buyer_response,
    classify_buyer_session_intent,
    start_buyer_discovery_session,
)


def _valid_grantex_preview(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "merchant_id": "mch_private_123",
        "tenant_id": "tenant_private_123",
        "merchant_reference": "safe-merchant-ref",
        "display_name": "Grounded Preview Store",
        "integration_status": "sandbox_handoff_requested",
        "generated_at": "2026-06-07T10:00:00Z",
        "audit_event_id": "audit_safe_ref",
        "merchant": {
            "display_name": "Grounded Preview Store",
            "category_preset": "electronics_appliances",
            "country_code": "IN",
            "default_currency": "INR",
            "public_discovery_description_draft": "Preview-only catalog evidence.",
            "legal_name": "Private Legal Pvt Ltd",
            "private_contact": "owner@example.test",
        },
        "readiness_summary": {"overall_status": "ready", "private_contract": "hidden"},
        "agent_facing_preview_summary": {"preview_status": "ready", "sample_product_count": 2},
        "rollout_proposal_summary": {"proposal_status": "dry_run_passed"},
        "evidence_checklist": [{"key": "preview", "label": "Preview ready", "status": "pass"}],
        "sample_products": [
            {
                "title": "Grounded Mixer",
                "brand": "SafeBrand",
                "category_preset": "electronics_appliances",
                "price": "999",
                "raw_payload": {"internal": True},
            },
            {"title": "Grounded Cooker", "brand": "SafeBrand"},
        ],
        "allowed_buyer_agent_capabilities": ["read_only_profile_discovery_preview"],
        "blocked_buyer_agent_capabilities": [
            "checkout_payment_creation",
            "live_payment",
            "refunds_returns_execution",
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
        "passport_jwt": "passport.jwt.secret",
        "provider_credentials": {"api_key": "secret"},
    }
    data.update(overrides)
    return {"data": data}


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Show me this merchant catalog preview.", "read_only_discovery"),
        ("Can I checkout and pay?", "checkout_payment"),
        ("Enable live Plural for this seller.", "live_provider"),
        ("Track my delivery.", "fulfillment"),
        ("Process a refund.", "refund_return"),
        ("Write me a poem.", "unsupported"),
    ],
)
def test_buyer_session_classifies_intents(text: str, intent: str) -> None:
    assert classify_buyer_session_intent(text) == intent


@pytest.mark.parametrize(
    ("text", "refusal_code"),
    [
        ("Can I checkout and pay?", "checkout_payment_not_enabled"),
        ("Enable live provider credentials.", "live_provider_not_enabled"),
        ("Ship this tomorrow.", "fulfillment_not_enabled"),
        ("Start a return.", "refund_return_not_enabled"),
        ("Book a flight.", "unsupported_not_enabled"),
    ],
)
async def test_buyer_session_refuses_blocked_intents_without_grantex_call(
    text: str,
    refusal_code: str,
) -> None:
    class FakeConnector:
        def __init__(self) -> None:
            self.calls = 0

        async def buyer_discovery_preview(self, **params: str) -> dict[str, Any]:
            self.calls += 1
            return _valid_grantex_preview()

    connector = FakeConnector()

    response = await start_buyer_discovery_session(
        connector,
        merchant_id="mch_private_123",
        request_text=text,
        channel="whatsapp",
    )

    assert connector.calls == 0
    assert response["status"] == "refused"
    assert response["refusal_code"] == refusal_code
    assert response["channel"] == "whatsapp"
    assert response["evidence_summary"]["grantex_call_status"] == "not_attempted"
    assert response["merchant_preview"] == {}


async def test_buyer_session_calls_grantex_for_read_only_discovery() -> None:
    class FakeConnector:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def buyer_discovery_preview(self, **params: str) -> dict[str, Any]:
            self.calls.append(dict(params))
            return _valid_grantex_preview()

    connector = FakeConnector()

    response = await start_buyer_discovery_session(
        connector,
        merchant_id="mch_private_123",
        request_text="Show me the product preview.",
        channel="chatgpt",
    )

    assert connector.calls == [{"merchant_id": "mch_private_123"}]
    assert response["contract"] == BUYER_SESSION_CONTRACT
    assert response["status"] == "preview_only"
    assert response["merchant_preview"]["display_name"] == "Grounded Preview Store"
    assert response["catalog_samples"] == [
        {"title": "Grounded Mixer", "brand": "SafeBrand", "category": "electronics_appliances"},
        {"title": "Grounded Cooker", "brand": "SafeBrand"},
    ]
    assert response["allowed_capabilities"] == ["read_only_profile_discovery_preview"]
    assert "checkout_payment_creation" in response["blocked_capabilities"]
    assert response["source_reference"]["system"] == "grantex"
    assert response["refusal_code"] is None
    assert response["evidence_summary"]["grantex_grounded"] is True


def test_buyer_session_response_is_channel_neutral() -> None:
    response = build_channel_neutral_buyer_response(
        _valid_grantex_preview(),
        request_text="Compare products.",
        channel="telegram",
    )

    assert response["channel_neutral"] is True
    assert response["channel"] == "telegram"
    for target in ("chatgpt", "claude", "gemini", "whatsapp", "telegram", "web_chat", "future_channel"):
        assert target in response["supported_channel_targets"]
    for key in (
        "status",
        "message",
        "merchant_preview",
        "catalog_samples",
        "allowed_capabilities",
        "blocked_capabilities",
        "source_reference",
        "refusal_code",
        "evidence_summary",
    ):
        assert key in response


def test_buyer_session_redacts_private_fields_and_stays_grounded() -> None:
    response = build_channel_neutral_buyer_response(
        _valid_grantex_preview(),
        request_text="What products can I see?",
    )
    serialized = json.dumps(response, sort_keys=True)

    assert "Grounded Preview Store" in serialized
    assert "Grounded Mixer" in serialized
    assert "mch_private_123" not in serialized
    assert "tenant_private_123" not in serialized
    assert "Private Legal" not in serialized
    assert "owner@example.test" not in serialized
    assert "passport.jwt.secret" not in serialized
    assert "secret" not in serialized
    assert "raw_payload" not in serialized
    assert "999" not in serialized
    assert "discount" not in serialized.lower()
    assert "delivery" not in serialized.lower()
    assert "refund promise" not in serialized.lower()


def test_buyer_session_missing_grantex_data_returns_safe_refusal_shape() -> None:
    response = build_channel_neutral_buyer_response(None, request_text="Show merchant preview.")

    assert response["status"] == "unavailable"
    assert response["refusal_code"] == "grantex_discovery_unavailable"
    assert response["merchant_preview"] == {}
    assert response["catalog_samples"] == []
    assert response["source_reference"]["system"] == "grantex"
    assert response["evidence_summary"]["preview_only"] is True


def test_buyer_session_grantex_error_envelope_returns_missing_data_refusal() -> None:
    response = build_channel_neutral_buyer_response(
        {"error": "grantex_transport_error", "message": "network unavailable"},
        request_text="Show merchant preview.",
    )

    assert response["status"] == "unavailable"
    assert response["refusal_code"] == "grantex_discovery_unavailable"
