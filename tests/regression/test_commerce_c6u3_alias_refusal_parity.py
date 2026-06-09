from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from connectors.commerce.grantex_commerce import (
    REST_CONSENT_ENDPOINTS,
    REST_READ_ONLY_ENDPOINTS,
    TOOL_ALIAS_TO_GRANTEX,
    GrantexCommerceConnector,
)
from core.commerce.buyer_session import (
    build_channel_neutral_buyer_response,
    start_buyer_discovery_session,
)
from core.commerce.discovery_gate import iter_public_discovery_agent_tools
from core.commerce.sales_guardrails import (
    GRANTEX_COMMERCE_DEFAULT_TOOLS,
    inventory_caution,
    normalize_grantex_error,
)


def _json_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def _connector(handler) -> GrantexCommerceConnector:
    connector = GrantexCommerceConnector(
        {"base_url": "https://grantex.test", "bearer_token": "gx_agent_test_token"}
    )
    connector._auth_headers = {
        "Authorization": "Bearer gx_agent_test_token",
        "Accept": "application/json",
    }
    connector._client = httpx.AsyncClient(
        base_url=connector.base_url,
        headers=connector._auth_headers,
        transport=httpx.MockTransport(handler),
    )
    return connector


def test_c6u3_alias_inventory_is_grantex_only() -> None:
    expected_mcp_aliases = {
        "merchant_get_profile": "merchant.get_profile",
        "catalog_search": "catalog.search",
        "catalog_get_item": "catalog.get_item",
        "inventory_check": "inventory.check",
        "cart_create": "cart.create",
        "payment_create_intent": "payment.create_intent",
        "checkout_create": "checkout.create",
        "payment_get_status": "payment.get_status",
    }

    assert TOOL_ALIAS_TO_GRANTEX == expected_mcp_aliases
    assert REST_CONSENT_ENDPOINTS == {
        "consent_request": "/v1/commerce/passports/consent-requests",
        "consent_exchange": "/v1/commerce/passports/exchange",
    }
    assert REST_READ_ONLY_ENDPOINTS == {
        "buyer_discovery_preview": (
            "/v1/commerce/merchants/{merchant_id}/agenticorg-buyer-discovery-preview"
        )
    }
    assert set(GRANTEX_COMMERCE_DEFAULT_TOOLS) == {
        "grantex_commerce:merchant_get_profile",
        "grantex_commerce:catalog_search",
        "grantex_commerce:catalog_get_item",
        "grantex_commerce:inventory_check",
        "grantex_commerce:cart_create",
        "grantex_commerce:consent_request",
        "grantex_commerce:consent_exchange",
        "grantex_commerce:buyer_discovery_preview",
        "grantex_commerce:payment_create_intent",
        "grantex_commerce:checkout_create",
        "grantex_commerce:payment_get_status",
    }

    serialized = json.dumps(
        {
            "mcp": TOOL_ALIAS_TO_GRANTEX,
            "rest": REST_CONSENT_ENDPOINTS | REST_READ_ONLY_ENDPOINTS,
            "tools": GRANTEX_COMMERCE_DEFAULT_TOOLS,
        },
        sort_keys=True,
    ).lower()
    assert "stripe" not in serialized
    assert "pinelabs" not in serialized
    assert "provider_credential" not in serialized
    assert "merchant_private" not in serialized


async def test_payment_status_missing_passport_refuses_before_grantex_call() -> None:
    connector = GrantexCommerceConnector({})

    result = await connector.payment_get_status(payment_intent_id="pi_synthetic_c6u3")

    assert result["allowed"] is False
    assert result["status"] == "refused"
    assert result["error"] == "consent_required"


async def test_payment_status_with_passport_uses_grantex_mcp_only() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return _json_response(
            {
                "jsonrpc": "2.0",
                "id": captured["body"]["id"],
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"data": {"status": "authorized"}}),
                        }
                    ]
                },
            }
        )

    connector = await _connector(handler)
    try:
        result = await connector.payment_get_status(
            payment_intent_id="pi_synthetic_c6u3",
            passport_jwt="passport.synthetic.value",
        )
    finally:
        await connector.disconnect()

    assert captured["path"] == "/mcp"
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"]["name"] == "payment.get_status"
    assert captured["body"]["params"]["arguments"] == {
        "payment_intent_id": "pi_synthetic_c6u3",
        "passport_jwt": "passport.synthetic.value",
    }
    assert result["data"]["status"] == "authorized"


@pytest.mark.parametrize(
    ("request_text", "refusal_code"),
    [
        ("Checkout and pay for this item.", "checkout_payment_not_enabled"),
        ("Use live provider routing for this buyer.", "live_provider_not_enabled"),
        ("Call the merchant private API endpoint.", "merchant_private_api_not_allowed"),
        ("Track delivery for this order.", "fulfillment_not_enabled"),
        ("Start a refund.", "refund_return_not_enabled"),
    ],
)
async def test_blocked_buyer_session_intents_never_call_grantex(
    request_text: str,
    refusal_code: str,
) -> None:
    class FakeConnector:
        def __init__(self) -> None:
            self.calls = 0

        async def buyer_discovery_preview(self, **params: str) -> dict[str, Any]:
            self.calls += 1
            raise AssertionError("blocked buyer intents must not call Grantex preview")

    connector = FakeConnector()

    response = await start_buyer_discovery_session(
        connector,
        merchant_id="merchant_synthetic_c6u3",
        request_text=request_text,
        channel="future_channel",
    )

    assert connector.calls == 0
    assert response["status"] == "refused"
    assert response["refusal_code"] == refusal_code
    assert response["evidence_summary"]["grantex_call_status"] == "not_attempted"


def test_public_discovery_hides_commerce_agent_tools_when_gate_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED", raising=False)

    visible = dict(
        iter_public_discovery_agent_tools(
            {
                "commerce_sales_agent": GRANTEX_COMMERCE_DEFAULT_TOOLS,
                "support_triage": ["create_ticket"],
            }
        )
    )

    assert "commerce_sales_agent" not in visible
    assert visible == {"support_triage": ["create_ticket"]}


def test_stale_inventory_is_translated_to_buyer_safe_caution() -> None:
    result = inventory_caution(
        {
            "items": [
                {
                    "variant_id": "variant_synthetic_c6u3",
                    "availability_status": "unknown",
                    "stale": True,
                    "source_system": "synthetic_fixture",
                }
            ]
        }
    )

    assert result["caution_required"] is True
    assert "Do not guarantee availability" in result["message"]
    serialized = json.dumps(result, sort_keys=True).lower()
    assert "raw_payload" not in serialized
    assert "provider" not in serialized
    assert "merchant private" not in serialized


def test_grantex_error_translation_redacts_private_details() -> None:
    db_url = "postgres" + "://user:pass@merchant-private.internal/db"
    private_url = "https://" + "merchant-private.internal/orders/123"
    error_payload = {
        "error": {
            "code": "provider_unavailable",
            "message": (
                "Provider failure contained raw_payload={token=synthetic-token} "
                f"{db_url} {private_url} passport=passport.synthetic.value"
            ),
            "audit_event_id": "audit_synthetic_c6u3",
            "decision_id": "decision_synthetic_c6u3",
            "remediation": "Do not retry with webhook_secret=synthetic-webhook-token.",
        }
    }

    normalized = normalize_grantex_error(error_payload, 502)
    serialized = json.dumps(normalized, sort_keys=True)

    assert normalized["error"] == "provider_unavailable"
    assert normalized["refusal"] is True
    assert normalized["audit_event_id"] == "audit_synthetic_c6u3"
    assert normalized["decision_id"] == "decision_synthetic_c6u3"
    assert "postgres://" not in serialized
    assert "merchant-private.internal" not in serialized
    assert "synthetic-token" not in serialized
    assert "passport.synthetic.value" not in serialized
    assert "webhook_secret" not in serialized


def test_channel_targets_are_documented_only_not_live_exposure() -> None:
    response = build_channel_neutral_buyer_response(
        None,
        request_text="Show merchant preview.",
        channel="chatgpt",
    )

    assert response["channel_neutral"] is True
    assert response["status"] == "unavailable"
    assert response["refusal_code"] == "grantex_discovery_unavailable"
    assert response["evidence_summary"]["non_enabling"] is True
    assert response["evidence_summary"]["preview_only"] is True
    for target in ("chatgpt", "claude", "gemini", "whatsapp", "telegram"):
        assert target in response["supported_channel_targets"]
