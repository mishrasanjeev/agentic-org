from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from connectors.commerce.grantex_commerce import TOOL_ALIAS_TO_GRANTEX, GrantexCommerceConnector

pytestmark = pytest.mark.asyncio


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


async def test_alias_mapping_uses_safe_agenticorg_names() -> None:
    assert TOOL_ALIAS_TO_GRANTEX == {
        "merchant_get_profile": "merchant.get_profile",
        "catalog_search": "catalog.search",
        "catalog_get_item": "catalog.get_item",
        "inventory_check": "inventory.check",
        "cart_create": "cart.create",
        "payment_create_intent": "payment.create_intent",
        "checkout_create": "checkout.create",
        "payment_get_status": "payment.get_status",
    }
    assert all("." not in alias for alias in TOOL_ALIAS_TO_GRANTEX)

    connector = GrantexCommerceConnector({})
    assert set(connector._tool_registry) == {
        "merchant_get_profile",
        "catalog_search",
        "catalog_get_item",
        "inventory_check",
        "cart_create",
        "consent_request",
        "consent_exchange",
        "payment_create_intent",
        "checkout_create",
        "payment_get_status",
    }


async def test_consent_request_uses_rest_endpoint_and_bearer_auth() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return _json_response({"data": {"consent_request_id": "cons_req_1"}}, 201)

    connector = await _connector(handler)
    try:
        result = await connector.consent_request(
            merchant_id="merchant_1",
            passport_type="checkout",
            max_amount=250000,
            currency="INR",
        )
    finally:
        await connector.disconnect()

    assert captured["path"] == "/v1/commerce/passports/consent-requests"
    assert captured["authorization"] == "Bearer gx_agent_test_token"
    assert captured["body"]["passport_type"] == "checkout"
    assert result["data"]["consent_request_id"] == "cons_req_1"


async def test_consent_exchange_uses_rest_endpoint() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return _json_response({"data": {"passport_jwt": "passport.jwt.value"}})

    connector = await _connector(handler)
    try:
        result = await connector.consent_exchange(consent_request_id="cons_req_1")
    finally:
        await connector.disconnect()

    assert captured["path"] == "/v1/commerce/passports/exchange"
    assert captured["body"] == {"consent_request_id": "cons_req_1"}
    assert result["data"]["passport_jwt"] == "passport.jwt.value"


async def test_idempotency_key_required_for_payment_affecting_tools() -> None:
    connector = GrantexCommerceConnector({})

    cart = await connector.cart_create(merchant_id="merchant_1", currency="INR", line_items=[])
    payment = await connector.payment_create_intent(
        merchant_id="merchant_1",
        cart_id="cart_1",
        passport_jwt="passport.jwt.value",
        amount_minor_units=1000,
        currency="INR",
    )
    checkout = await connector.checkout_create(
        payment_intent_id="pi_1",
        passport_jwt="passport.jwt.value",
        success_url="https://example.test/success",
        cancel_url="https://example.test/cancel",
    )

    assert cart["error"] == "idempotency_key_required"
    assert payment["error"] == "idempotency_key_required"
    assert checkout["error"] == "idempotency_key_required"


async def test_payment_create_intent_calls_mcp_with_idempotency_key() -> None:
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
                            "text": json.dumps({"data": {"payment_intent_id": "pi_1"}}),
                        }
                    ]
                },
            }
        )

    connector = await _connector(handler)
    try:
        result = await connector.payment_create_intent(
            merchant_id="merchant_1",
            cart_id="cart_1",
            passport_jwt="passport.jwt.value",
            amount_minor_units=1000,
            currency="INR",
            idempotency_key="idem_payment_1",
            provider_key="mock",
        )
    finally:
        await connector.disconnect()

    assert captured["path"] == "/mcp"
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"]["name"] == "payment.create_intent"
    assert captured["body"]["params"]["arguments"]["idempotency_key"] == "idem_payment_1"
    assert result["data"]["payment_intent_id"] == "pi_1"


async def test_checkout_create_calls_mcp_with_idempotency_key() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _json_response(
            {
                "jsonrpc": "2.0",
                "id": captured["body"]["id"],
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"data": {"checkout_id": "chk_1"}})}
                    ]
                },
            }
        )

    connector = await _connector(handler)
    try:
        result = await connector.checkout_create(
            payment_intent_id="pi_1",
            passport_jwt="passport.jwt.value",
            success_url="https://example.test/success",
            cancel_url="https://example.test/cancel",
            idempotency_key="idem_checkout_1",
        )
    finally:
        await connector.disconnect()

    assert captured["body"]["params"]["name"] == "checkout.create"
    assert captured["body"]["params"]["arguments"]["idempotency_key"] == "idem_checkout_1"
    assert result["data"]["checkout_id"] == "chk_1"


async def test_grantex_rest_error_envelope_is_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "error": {
                    "code": "merchant_emergency_disabled",
                    "message": "Merchant emergency disable is active.",
                    "decision_id": "decision_1",
                    "audit_event_id": "audit_1",
                }
            },
            403,
        )

    connector = await _connector(handler)
    try:
        result = await connector.consent_request(merchant_id="merchant_1", passport_type="checkout")
    finally:
        await connector.disconnect()

    assert result["error"] == "merchant_emergency_disabled"
    assert result["refusal"] is True
    assert result["decision_id"] == "decision_1"
    assert result["audit_event_id"] == "audit_1"


async def test_grantex_mcp_tool_error_is_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _json_response(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "error": {
                                        "code": "passport_revoked",
                                        "message": "Commerce Passport is revoked.",
                                    }
                                }
                            ),
                        }
                    ],
                },
            }
        )

    connector = await _connector(handler)
    try:
        result = await connector.payment_get_status(
            payment_intent_id="pi_1",
            passport_jwt="passport.jwt.value",
        )
    finally:
        await connector.disconnect()

    assert result["error"] == "passport_revoked"
    assert result["refusal"] is True


async def test_connector_never_logs_sensitive_values() -> None:
    source = open("connectors/commerce/grantex_commerce.py").read()
    assert "logger." not in source
    assert "print(" not in source

    connector = GrantexCommerceConnector({})
    result = await connector.payment_create_intent(
        merchant_id="merchant_1",
        cart_id="cart_1",
        passport_jwt="passport.jwt.value",
        amount_minor_units=1000,
        currency="INR",
        idempotency_key="idem_payment_1",
        provider_key="plural",
    )

    serialized = json.dumps(result, sort_keys=True)
    assert "passport.jwt.value" not in serialized
    assert "idem_payment_1" not in serialized

