# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The payment hand-off on the network surfaces: the gateway's complete, the Telegram tool, the public routes."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from api.v1 import commerce_ucp
from api.v1 import network as api
from core.commerce import network as net
from core.commerce import network_telegram as bot
from core.commerce import payment_handoff as ph
from core.config import settings
from core.test_doubles.scripted_model import ScriptedChatModel, final, tool_call
from tests.app_routes import http_routes

TOKEN = "network-service-token-" + "x" * 24
PROFILE = "https://agenticorg.ai/ucp/platforms/agenticorg-network/profile.json"
SHOP = net.ShopRef(
    slug="tea-corner",
    tenant_id="00000000-0000-0000-0000-0000000000d1",
    merchant_id="merchant-1",
    seller_agent_id="seller-1",
    host="tea-corner.shops.example",
    display_name="Tea Corner",
)
LINK = "https://tea-corner.myshopify.com/cart/4401:1"
HANDOFF = {
    "id": "chk_1",
    "status": "requires_escalation",
    "continue_url": LINK,
    "currency": "INR",
    "totals": [{"type": "total", "amount": 44900}],
    "messages": [
        {"type": "error", "code": "payment_handoff", "severity": "requires_buyer_input", "content": ph.SHOPIFY_MESSAGE}
    ],
}


@pytest.fixture()
def gateway(monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(settings, "network_service_token", TOKEN)
    monkeypatch.setattr(settings, "network_agent_profile", PROFILE)
    calls: list[dict] = []

    async def resolve(slug):
        return SHOP

    async def run(operation, **kwargs):
        calls.append({"operation": operation, **kwargs})
        return SimpleNamespace(status=200, body=dict(HANDOFF), headers={})

    monkeypatch.setattr(net, "resolve_shop", resolve)
    monkeypatch.setattr(commerce_ucp, "run_ucp_operation", run)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, calls


AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/api/v1/network/shops/tea-corner/checkout-sessions/chk_1/complete"


@pytest.mark.asyncio
async def test_gateway_complete_is_a_handoff_only(gateway):
    client, calls = gateway
    for body in (None, {}):
        response = await client.post(URL, json=body, headers={**AUTH, "Idempotency-Key": "k-1"})
        assert response.status_code == 200
        assert response.json()["continue_url"] == LINK
    call = calls[0]
    assert call["operation"] == "complete_checkout" and call["resource_id"] == "chk_1"
    assert call["handoff_only"] is True and call["raw_body"] == b"{}"
    assert call["platform"] == PROFILE and call["tenant_id"] == SHOP.tenant_id


@pytest.mark.parametrize(
    "body",
    [
        {"payment": {"instruments": [{"id": "i", "handler_id": "plural_tokenized", "credential": {"token": "t"}}]}},
        {"ai.agenticorg.commerce": {"decision_id": "arbdec_1"}},
        {"ai.agenticorg.commerce": {"mandate_request": {"presence": "human_not_present"}}},
        {"ap2": {"checkout_mandate": "a.b.c"}},
        {"payment_data": {"token": "spt_1"}},
        ["not", "an", "object"],
    ],
)
@pytest.mark.asyncio
async def test_gateway_complete_refuses_anything_that_would_pay(gateway, body):
    client, calls = gateway
    response = await client.post(URL, json=body, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["code"] == "payment_execution_not_allowed"
    assert calls == []


@pytest.mark.asyncio
async def test_gateway_complete_needs_the_service_credential(gateway):
    client, calls = gateway
    assert (await client.post(URL, json={}, headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert calls == []


@pytest.mark.asyncio
async def test_gateway_create_records_the_mcp_surface(gateway):
    client, calls = gateway
    await client.post("/api/v1/network/shops/tea-corner/checkout-sessions", json={"line_items": []}, headers=AUTH)
    assert calls[0]["surface"] == "mcp" and calls[0]["handoff_only"] is False


# --- Telegram ---------------------------------------------------------------------------------------


def test_for_model_surfaces_the_payment_link_and_not_as_a_problem():
    summary = bot._for_model({"http_status": 200, **HANDOFF})["summary"]
    assert summary["continue_url"] == LINK
    assert summary["problems"] == [] and summary["ready"] is False and summary["total"] == "Rs 449.00"
    insecure = bot._for_model({"http_status": 200, **HANDOFF, "continue_url": "http://evil.example"})["summary"]
    assert insecure["continue_url"] is None


@pytest.mark.asyncio
async def test_telegram_get_payment_link_asks_for_the_handoff(monkeypatch):
    seen = []

    async def checkout(slug, operation, body, session_id=None, surface="telegram"):
        seen.append((slug, operation, body, session_id))
        return {"http_status": 200, "shop": slug, **HANDOFF}

    monkeypatch.setattr(api, "network_checkout", checkout)
    answer = await bot.ShopperTools("tea-corner").get_payment_link("", "chk_1")
    assert seen == [("tea-corner", "complete_checkout", None, "chk_1")]
    assert answer["summary"]["continue_url"] == LINK


@pytest.mark.asyncio
async def test_the_shopper_gives_the_buyer_the_link_once_the_checkout_is_ready(monkeypatch):
    async def under_cap(_estimate=0.0, tenant_id=None):
        return None

    async def checkout(slug, operation, body, session_id=None, surface="telegram"):
        if operation == "complete_checkout":
            return {"http_status": 200, "shop": slug, **HANDOFF}
        return {"http_status": 200, "shop": slug, "id": "chk_1", "status": "ready_for_complete", "totals": []}

    monkeypatch.setattr("core.llm.router.assert_under_gemini_cap", under_cap)
    monkeypatch.setattr(api, "network_checkout", checkout)
    model = ScriptedChatModel(
        steps=[
            tool_call("get_payment_link", shop="tea-corner", checkout_id="chk_1"),
            final(f"Your total is Rs 449.00. Pay on the shop's page: {LINK}"),
        ]
    )
    reply = await bot.run_shopper({"shop": "tea-corner", "turns": []}, "I'm ready to pay", model_factory=lambda: model)
    assert LINK in reply
    model.assert_consumed()
    assert "get_payment_link" in model.calls[0][0].content
    assert "summary.continue_url" in bot.SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_network_checkout_records_the_telegram_surface(monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    seen = {}

    async def resolve(slug):
        return SHOP

    async def run(operation, shop, raw, **kwargs):
        seen.update(operation=operation, **kwargs)
        return SimpleNamespace(status=201, body={"id": "chk_9"}, headers={})

    monkeypatch.setattr(net, "resolve_shop", resolve)
    monkeypatch.setattr(api, "_run_for_network", run)
    await api.network_checkout("tea-corner", "create_checkout", {"line_items": []})
    assert seen["surface"] == "telegram"


# --- public routes ----------------------------------------------------------------------------------


def test_the_public_payment_routes_are_declared_and_exempt_from_the_session_middlewares():
    from api.main import app
    from api.route_metadata import ROUTE_METADATA_ATTR
    from auth.csrf_middleware import _EXEMPT_PREFIXES
    from auth.grantex_middleware import GrantexAuthMiddleware
    from auth.middleware import AuthMiddleware

    paths: dict[str, set[str]] = {}
    for route in http_routes(app):
        path = route.path
        if path.startswith("/api/v1/payments/plural/"):
            paths.setdefault(path, set()).update(route.methods)
            meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
            assert meta["auth_required"] is False and meta["public_reason"]
    assert paths == {
        "/api/v1/payments/plural/return/{tenant_id}/{checkout_id}": {"GET", "POST"},
        "/api/v1/payments/plural/webhooks/{tenant_id}/{webhook_key}": {"POST"},
    }
    for prefixes in (GrantexAuthMiddleware.EXEMPT_PREFIXES, AuthMiddleware.EXEMPT_PREFIXES, _EXEMPT_PREFIXES):
        assert "/api/v1/payments/plural/" in prefixes
    # The merchant routes stay behind authentication.
    for route in http_routes(app):
        path = route.path
        if path.startswith("/api/v1/commerce/runtime/") and ("/payments" in path or path.endswith("/orders")):
            assert getattr(route.endpoint, ROUTE_METADATA_ATTR)["auth_required"] is True, path


@pytest.mark.asyncio
async def test_the_return_page_refuses_malformed_paths_without_reading_anything():
    from api.v1 import commerce_payments

    for tenant, checkout in (("not-a-uuid", "chk_1"), ("00000000-0000-0000-0000-0000000000d1", "../etc")):
        page = await commerce_payments._return_page(tenant, checkout)
        assert page.status_code == 404 and b"could not find" in page.body
        assert page.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_the_webhook_route_answers_404_for_an_unknown_endpoint_and_400_for_a_bad_signature(monkeypatch):
    from api.v1 import commerce_payments
    from core.commerce import plural_hosted
    from core.commerce.executors.plural import PluralWebhookError

    recorded = []

    async def ledger(**kwargs):
        recorded.append(kwargs)

    outcomes = iter([PluralWebhookError("unknown_endpoint"), PluralWebhookError("signature_invalid")])

    async def apply(**_kwargs):
        raise next(outcomes)

    monkeypatch.setattr(plural_hosted, "apply_webhook", apply)
    monkeypatch.setattr(commerce_payments, "record_commerce_event_in_own_transaction", ledger)
    app = FastAPI()
    app.include_router(commerce_payments.public_router, prefix="/api/v1")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    url = "/api/v1/payments/plural/webhooks/00000000-0000-0000-0000-0000000000d1/" + "a" * 32
    assert (await client.post(url, content=b"{}")).status_code == 404
    refused = await client.post(url, content=b"{}")
    assert refused.status_code == 400 and refused.json()["detail"] == "signature_invalid"
    assert [r["outcome"] for r in recorded] == ["refused"]
    assert (await client.post("/api/v1/payments/plural/webhooks/not-a-tenant/x", content=b"{}")).status_code == 404
