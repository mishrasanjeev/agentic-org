# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The shop site on a network shop's own host: the merchant-host middleware sends the storefront paths
to /shop-site only for a listed shop's host, the routes answer by Host, and the network feed index."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.middleware.merchant_host import SHOP_SITE_PREFIX, MerchantHostMiddleware, is_shop_site_path
from api.v1 import network as network_api
from api.v1 import shop_site as routes
from core.commerce import network as net
from core.commerce import shop_site_store
from core.commerce.shop_site import ShopSite, site_products
from core.commerce.ucp import store
from core.config import settings

SHOP_HOST = "tea-corner.agenticorg.ai"
OTHER_MERCHANT = "agent.other-merchant.example"
SERVICE_HOST = "agenticorg-api-example.a.run.app"
SHOP = net.ShopRef(
    slug="tea-corner",
    tenant_id="00000000-0000-0000-0000-0000000000b1",
    merchant_id="merchant-1",
    seller_agent_id="seller-1",
    host=SHOP_HOST,
    display_name="Tea Corner",
)
PRODUCT = {
    "id": "catalog_upload_product:abc",
    "title": "Masala chai",
    "description": {"plain": "Strong tea."},
    "media": [{"type": "image", "url": "https://cdn.example/chai.jpg"}],
    "variants": [
        {"id": "v1", "title": "Default", "sku": "CHAI-1", "price": {"amount": 44900, "currency": "INR"},
         "availability": {"available": True}},
    ],
}  # fmt: skip


def _site(verified: bool = True) -> ShopSite:
    return ShopSite(
        slug="tea-corner",
        name="Tea Corner",
        host=SHOP_HOST,
        catalog_verified=verified,
        products=site_products([PRODUCT]) if verified else (),
    )


@pytest.mark.parametrize(
    ("path", "site"),
    [
        ("/", True),
        ("/robots.txt", True),
        ("/sitemap.xml", True),
        ("/llms.txt", True),
        ("/products/masala-chai-1234567890", True),
        ("/feeds/google.xml", True),
        ("/products", False),
        ("/api/v1/health", False),
        ("/ucp/catalog/search", False),
        ("/shop-site/", False),
    ],
)
def test_shop_site_paths(path: str, site: bool) -> None:
    assert is_shop_site_path(path) is site


def test_the_middleware_and_the_routes_share_one_prefix() -> None:
    assert routes.router.prefix == SHOP_SITE_PREFIX == "/shop-site"


# --- middleware -------------------------------------------------------------------------------


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse({"path": request.url.path, "query": request.url.query, "host": request.headers.get("host")})


async def _cached(request: Request) -> JSONResponse:
    return JSONResponse({"path": request.url.path}, headers={"Cache-Control": "private, max-age=300"})


def _get(path: str, *, host: str, forwarded: str | None = None) -> httpx.Response:
    app = MerchantHostMiddleware(
        Starlette(
            routes=[
                Route("/", _echo),
                Route("/robots.txt", _echo),
                Route("/shop-site/", _echo),
                Route("/shop-site/robots.txt", _cached),
                Route("/shop-site/products/{handle}", _echo),
                Route("/api/v1/thing", _echo),
            ]
        )
    )
    headers = {"host": host}
    if forwarded:
        headers["x-forwarded-host"] = forwarded

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{host}") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(run())


@pytest.fixture()
def shops(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    hosts = {SHOP_HOST}

    async def shop_for_host(host: str) -> net.ShopRef | None:
        return SHOP if host in hosts else None

    monkeypatch.setattr(net, "shop_for_host", shop_for_host)
    # Indexed UCP serving hosts: the listed shop, and a merchant that is not on the network.
    bindings = {SHOP_HOST, OTHER_MERCHANT}

    async def find_serving_binding(host: str) -> object | None:
        return object() if host in bindings else None

    monkeypatch.setattr(store, "find_serving_binding", find_serving_binding)
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", False)
    return hosts


def test_a_listed_shops_host_gets_its_storefront(shops: set[str]) -> None:
    response = _get("/", host=SHOP_HOST)
    assert response.json() == {"path": "/shop-site/", "query": "", "host": SHOP_HOST}
    assert response.headers["cache-control"] == "no-store"
    product = _get("/products/masala-chai-1234567890?variant=abc", host=SHOP_HOST).json()
    assert product == {"path": "/shop-site/products/masala-chai-1234567890", "query": "variant=abc", "host": SHOP_HOST}
    # A policy the route sets itself is kept.
    assert _get("/robots.txt", host=SHOP_HOST).headers["cache-control"] == "private, max-age=300"


def test_any_other_host_reaches_the_application_unchanged(shops: set[str]) -> None:
    response = _get("/", host="api.agenticorg.ai")
    assert response.json()["path"] == "/"
    assert "cache-control" not in response.headers
    assert _get("/api/v1/thing", host=SHOP_HOST).json()["path"] == "/api/v1/thing"


def test_through_firebase_only_a_verified_forwarded_host_counts(
    shops: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_firebase_site", "merchant-domains-example")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    verified = {SHOP_HOST}

    async def active(host: str) -> bool:
        return host in verified

    monkeypatch.setattr(store, "firebase_domain_active", active)
    response = _get("/", host=SERVICE_HOST, forwarded=SHOP_HOST)
    assert response.json() == {"path": "/shop-site/", "query": "", "host": SHOP_HOST}
    verified.clear()
    response = _get("/", host=SERVICE_HOST, forwarded=SHOP_HOST)
    assert response.json()["path"] == "/" and response.json()["host"] == SERVICE_HOST


def test_another_merchants_host_answers_404_without_reaching_the_application(shops: set[str]) -> None:
    for path in ("/", "/robots.txt", "/products/x-0000000000", "/feeds/google.xml"):
        response = _get(path, host=OTHER_MERCHANT)
        assert response.status_code == 404, path
        assert response.json() == {"detail": "Not Found"}
        assert response.headers["x-agenticorg-shop-site"] == "not-listed"
        assert response.headers["cache-control"] == "no-store"
    # Agent paths on that host still reach the application.
    assert _get("/api/v1/thing", host=OTHER_MERCHANT).json()["path"] == "/api/v1/thing"


def test_a_listed_shop_answers_404_while_the_network_is_off(shops: set[str]) -> None:
    shops.clear()  # shop_for_host is None while the network is off
    response = _get("/", host=SHOP_HOST)
    assert response.status_code == 404 and response.headers["x-agenticorg-shop-site"] == "not-listed"


def test_a_verified_firebase_domain_that_is_not_listed_answers_404(
    shops: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_firebase_site", "merchant-domains-example")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    monkeypatch.setattr(store, "firebase_domain_active", AsyncMock(return_value=True))
    # Not indexed as a serving host either: the verified Firebase domain alone makes it a merchant host.
    response = _get("/robots.txt", host=SERVICE_HOST, forwarded="new.merchant.example")
    assert response.status_code == 404 and response.headers["x-agenticorg-shop-site"] == "not-listed"


def test_a_lookup_failure_answers_503_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", False)
    monkeypatch.setattr(net, "shop_for_host", AsyncMock(side_effect=RuntimeError("database unavailable")))
    response = _get("/", host=SHOP_HOST)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.headers["x-agenticorg-shop-site"] == "unavailable"
    assert response.headers["cache-control"] == "no-store"
    # Agent and API paths are not affected by the shop-site lookup.
    assert _get("/api/v1/thing", host=SHOP_HOST).json()["path"] == "/api/v1/thing"


def test_a_failed_firebase_domain_lookup_answers_503_on_site_paths(
    shops: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_firebase_site", "merchant-domains-example")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    monkeypatch.setattr(store, "firebase_domain_active", AsyncMock(side_effect=RuntimeError("database unavailable")))
    response = _get("/", host=SERVICE_HOST, forwarded=SHOP_HOST)
    assert response.status_code == 503 and response.headers["x-agenticorg-shop-site"] == "unavailable"


def test_the_full_app_never_answers_401_on_a_merchant_hosts_site_paths(shops: set[str]) -> None:
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app, base_url=f"https://{OTHER_MERCHANT}")
    response = client.get("/", headers={"host": OTHER_MERCHANT})
    assert response.status_code == 404 and response.headers["x-agenticorg-shop-site"] == "not-listed"


# --- routes -------------------------------------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, site: ShopSite | None) -> httpx.AsyncClient:
    async def load_site(host: str) -> ShopSite | None:
        return site if site is not None and host == site.host else None

    monkeypatch.setattr(routes, "load_site", load_site)
    app = FastAPI()
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{SHOP_HOST}")


async def _fetch(client: httpx.AsyncClient, path: str, host: str = SHOP_HOST) -> httpx.Response:
    async with client:
        return await client.get(path, headers={"host": host})


def test_routes_serve_the_shop_on_its_host(monkeypatch: pytest.MonkeyPatch) -> None:
    site = _site()
    handle = site.products[0].handle
    home = asyncio.run(_fetch(_client(monkeypatch, site), "/shop-site/"))
    assert home.status_code == 200 and "Masala chai" in home.text
    assert home.headers["cache-control"] == "private, max-age=300"
    assert "default-src 'none'" in home.headers["content-security-policy"]
    assert home.headers["x-content-type-options"] == "nosniff"
    page = asyncio.run(_fetch(_client(monkeypatch, site), f"/shop-site/products/{handle}"))
    assert page.status_code == 200 and "INR 449.00" in page.text
    feed = asyncio.run(_fetch(_client(monkeypatch, site), "/shop-site/feeds/google.xml"))
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("application/xml")
    assert "<g:price>449.00 INR</g:price>" in feed.text
    for path, kind in (("/shop-site/sitemap.xml", "application/xml"), ("/shop-site/llms.txt", "text/plain"),
                       ("/shop-site/robots.txt", "text/plain")):  # fmt: skip
        response = asyncio.run(_fetch(_client(monkeypatch, site), path))
        assert response.status_code == 200 and response.headers["content-type"].startswith(kind), path


def test_an_unknown_product_or_host_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    site = _site()
    assert asyncio.run(_fetch(_client(monkeypatch, site), "/shop-site/products/nope-0000000000")).status_code == 404
    assert asyncio.run(_fetch(_client(monkeypatch, site), "/shop-site/", host="api.agenticorg.ai")).status_code == 404
    assert asyncio.run(_fetch(_client(monkeypatch, None), "/shop-site/feeds/google.xml")).status_code == 404


def test_an_unverified_catalog_answers_503_except_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    site = _site(verified=False)
    for path in ("/shop-site/", "/shop-site/feeds/google.xml", "/shop-site/sitemap.xml", "/shop-site/llms.txt"):
        response = asyncio.run(_fetch(_client(monkeypatch, site), path))
        assert response.status_code == 503, path
        assert response.headers["retry-after"] == routes.RETRY_AFTER_SECONDS
        assert response.headers["cache-control"] == "no-store"
    assert asyncio.run(_fetch(_client(monkeypatch, site), "/shop-site/robots.txt")).status_code == 200


def test_the_full_app_serves_the_storefront_anonymously_on_the_shop_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from api.main import app

    site = _site()

    async def shop_for_host(host: str) -> net.ShopRef | None:
        return SHOP if host == SHOP_HOST else None

    async def load_site(host: str) -> ShopSite | None:
        return site if host == SHOP_HOST else None

    monkeypatch.setattr(net, "shop_for_host", shop_for_host)
    monkeypatch.setattr(routes, "load_site", load_site)
    monkeypatch.setattr("core.auth_state.check_window_rate", AsyncMock(return_value=False))
    client = TestClient(app, base_url=f"https://{SHOP_HOST}")
    response = client.get("/", headers={"host": SHOP_HOST})
    assert response.status_code == 200, response.text
    assert "Tea Corner" in response.text
    feed = client.get("/feeds/google.xml", headers={"host": SHOP_HOST})
    assert feed.status_code == 200 and "<g:id>CHAI-1</g:id>" in feed.text
    # On the API's own host the internal prefix finds no shop.
    assert client.get("/shop-site/", headers={"host": "api.agenticorg.ai"}).status_code == 404


# --- the network feed index ------------------------------------------------------------------------


def test_network_feed_index_lists_each_shops_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "network_enabled", True)
    directory = [
        {"slug": "tea-corner", "name": "Tea Corner", "host": SHOP_HOST},
        {"slug": "spice-hub", "name": "Spice Hub", "host": "spice-hub.agenticorg.ai"},
    ]
    monkeypatch.setattr(net, "directory", AsyncMock(return_value=directory))

    async def load_site(host: str) -> ShopSite | None:
        if host == SHOP_HOST:
            return _site()
        return ShopSite(slug="spice-hub", name="Spice Hub", host=host, catalog_verified=False)

    monkeypatch.setattr(shop_site_store, "load_site", load_site)
    network_api.reset_feed_index_cache()
    app = FastAPI()
    app.include_router(network_api.router, prefix="/api/v1")

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/v1/network/feeds")

    body: dict[str, Any] = asyncio.run(run()).json()
    network_api.reset_feed_index_cache()
    assert body["count"] == 2 and body["format"] == "rss-2.0-google-merchant"
    tea, spice = body["shops"]
    assert tea == {
        "slug": "tea-corner",
        "name": "Tea Corner",
        "website_url": f"https://{SHOP_HOST}/",
        "feed_url": f"https://{SHOP_HOST}/feeds/google.xml",
        "sitemap_url": f"https://{SHOP_HOST}/sitemap.xml",
        "catalog_verified": True,
        "eligible_items": 1,
    }
    assert (spice["catalog_verified"], spice["eligible_items"]) == (False, 0)


def test_network_feed_index_is_built_once_per_ttl_with_bounded_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "network_enabled", True)
    directory = [{"slug": f"shop-{i}", "name": f"Shop {i}", "host": f"shop-{i}.agenticorg.ai"} for i in range(10)]
    monkeypatch.setattr(net, "directory", AsyncMock(return_value=directory))
    loads: list[str] = []
    running = {"now": 0, "most": 0}

    async def load_site(host: str) -> ShopSite | None:
        loads.append(host)
        running["now"] += 1
        running["most"] = max(running["most"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return None

    monkeypatch.setattr(shop_site_store, "load_site", load_site)
    network_api.reset_feed_index_cache()

    async def run() -> list[dict[str, Any]]:
        # Five callers at once and one after: a single build serves them all.
        first = await asyncio.gather(*(network_api._feed_index() for _ in range(5)))
        return [*first, await network_api._feed_index()]

    results = asyncio.run(run())
    assert len(loads) == 10 and len(set(loads)) == 10
    assert running["most"] <= network_api.FEED_INDEX_CONCURRENCY
    assert all(result is results[0] for result in results) and results[0]["count"] == 10
    # After the TTL the index is built again.
    monkeypatch.setattr(network_api, "FEED_INDEX_TTL_SECONDS", -1.0)
    network_api.reset_feed_index_cache()
    asyncio.run(network_api._feed_index())
    assert len(loads) == 20
    network_api.reset_feed_index_cache()


def test_network_feed_index_is_404_while_the_network_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "network_enabled", False)
    app = FastAPI()
    app.include_router(network_api.router, prefix="/api/v1")

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/v1/network/feeds")

    assert asyncio.run(run()).status_code == 404


# --- shop_for_host ------------------------------------------------------------------------------------


def test_shop_for_host_is_none_while_the_network_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "network_enabled", False)
    finder = AsyncMock()
    monkeypatch.setattr(net, "find_serving_binding", finder)
    assert asyncio.run(net.shop_for_host(SHOP_HOST)) is None
    finder.assert_not_called()


def test_shop_for_host_needs_an_indexed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "network_enabled", True)
    net.reset_directory_cache()
    monkeypatch.setattr(net, "find_serving_binding", AsyncMock(return_value=None))
    assert asyncio.run(net.shop_for_host("unknown.shops.example")) is None
    net.reset_directory_cache()
