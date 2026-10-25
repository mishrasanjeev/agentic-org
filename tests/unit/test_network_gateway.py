# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Network checkout gateway, directory endpoint and slug rules (no database)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from api.v1 import commerce_ucp
from api.v1 import network as api
from core.commerce import network as net
from core.config import settings

TOKEN = "network-service-token-" + "x" * 24
PROFILE = "https://agenticorg.ai/ucp/platforms/agenticorg-network/profile.json"
SHOP = net.ShopRef(
    slug="tea-corner",
    tenant_id="00000000-0000-0000-0000-0000000000b1",
    merchant_id="merchant-1",
    seller_agent_id="seller-1",
    host="tea-corner.shops.example",
    display_name="Tea Corner",
)


@pytest.fixture()
def gateway(monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(settings, "network_service_token", TOKEN)
    monkeypatch.setattr(settings, "network_agent_profile", PROFILE)
    calls: list[dict] = []

    async def resolve(slug):
        if slug != SHOP.slug:
            raise net.NetworkError("unknown_shop", "No such shop in the network.", 404)
        return SHOP

    async def run(operation, **kwargs):
        calls.append({"operation": operation, **kwargs})
        return SimpleNamespace(status=201, body={"id": "chk_1", "status": "incomplete"}, headers={"X-Ucp": "1"})

    monkeypatch.setattr(net, "resolve_shop", resolve)
    monkeypatch.setattr(commerce_ucp, "run_ucp_operation", run)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, calls


@pytest.mark.asyncio
async def test_gateway_requires_the_network_service_credential(gateway):
    client, calls = gateway
    url = "/api/v1/network/shops/tea-corner/checkout-sessions"
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        response = await client.post(url, json={"line_items": []}, headers=headers)
        assert response.status_code == 401, headers
        assert response.headers["www-authenticate"].startswith("Bearer")
    assert calls == []


@pytest.mark.asyncio
async def test_gateway_runs_ucp_in_the_shop_tenant_as_the_network_platform(gateway):
    client, calls = gateway
    body = {"line_items": [{"item": {"id": "NTH-MASALA-250"}, "quantity": 1}]}
    response = await client.post(
        "/api/v1/network/shops/tea-corner/checkout-sessions",
        json=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "k-1", "Request-Id": "r-1"},
    )
    assert response.status_code == 201 and response.json()["id"] == "chk_1"
    assert response.headers["request-id"] == "r-1"
    call = calls[0]
    assert call["operation"] == "create_checkout"
    assert (call["tenant_id"], call["host"]) == (SHOP.tenant_id, SHOP.host)
    assert call["ucp_agent"] == f'profile="{PROFILE}"' and call["platform"] == PROFILE
    assert call["idempotency_key"] == "k-1" and call["resource_id"] is None

    await client.put(
        "/api/v1/network/shops/tea-corner/checkout-sessions/chk_1",
        json={"id": "chk_1"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    await client.get(
        "/api/v1/network/shops/tea-corner/checkout-sessions/chk_1", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert [(c["operation"], c["resource_id"]) for c in calls[1:]] == [
        ("update_checkout", "chk_1"),
        ("get_checkout", "chk_1"),
    ]


@pytest.mark.asyncio
async def test_gateway_refuses_unknown_shops_short_tokens_and_hidden_flags(gateway, monkeypatch):
    client, calls = gateway
    auth = {"Authorization": f"Bearer {TOKEN}"}
    unknown = await client.post("/api/v1/network/shops/nobody/checkout-sessions", json={}, headers=auth)
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "unknown_shop"

    # A configured token shorter than 32 characters is treated as no token at all.
    monkeypatch.setattr(settings, "network_service_token", "short")
    short = await client.post(
        "/api/v1/network/shops/tea-corner/checkout-sessions", json={}, headers={"Authorization": "Bearer short"}
    )
    assert short.status_code == 401

    monkeypatch.setattr(settings, "network_service_token", TOKEN)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    assert (
        await client.post("/api/v1/network/shops/tea-corner/checkout-sessions", json={}, headers=auth)
    ).status_code == 404
    monkeypatch.setattr(settings, "network_enabled", False)
    assert (await client.get("/api/v1/network/merchants")).status_code == 404
    assert calls == []


@pytest.mark.asyncio
async def test_directory_endpoint_returns_the_servable_shops(gateway, monkeypatch):
    client, _calls = gateway
    shops = [
        {"slug": "tea-corner", "name": "Tea Corner", "profile_url": "https://tea-corner.shops.example/.well-known/ucp"}
    ]

    async def directory():
        return shops

    monkeypatch.setattr(net, "directory", directory)
    response = await client.get("/api/v1/network/merchants")
    assert response.status_code == 200
    assert response.json() == {"shops": shops, "count": 1}
    assert response.headers["cache-control"] == "public, max-age=60"


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Nilgiri Tea House", "nilgiri-tea-house"),
        ("Café Ōoty & Co.", "cafe-ooty-co"),
        ("AB", "ab-shop"),
        ("   ", "shop"),
    ],
)
def test_slugify(name, slug):
    assert net.slugify(name) == slug


def test_slug_rules():
    assert net.slug_problem("nilgiritea") is None
    assert net.slug_problem("www") == "reserved_slug"
    for bad in ("ab", "-tea", "tea-", "Tea", "tea_house", "a--b", "x" * 41):
        assert net.slug_problem(bad) == "invalid_slug", bad


def test_telegram_link_and_platform_subdomain(monkeypatch):
    monkeypatch.setattr(settings, "network_telegram_bot_username", "@ExampleShopBot")
    monkeypatch.setattr(settings, "platform_subdomain_apex", "Shops.Example.")
    assert net.telegram_link("tea-corner") == "https://t.me/ExampleShopBot?start=tea-corner"
    assert net.platform_subdomain_for("tea-corner") == "tea-corner.shops.example"
    monkeypatch.setattr(settings, "network_telegram_bot_username", "")
    assert net.telegram_link("tea-corner") is None
