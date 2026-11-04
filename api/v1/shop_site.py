# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A listed network shop's storefront and product feed, served on the shop's own web address.

Buyers and crawlers ask for ``/``, ``/products/<handle>``, ``/sitemap.xml``, ``/robots.txt``,
``/llms.txt`` and ``/feeds/google.xml`` on the shop's host. ``api/middleware/merchant_host.py``
sends exactly those paths to the routes here, under the internal ``/shop-site`` prefix, and only
when the request's host is a listed, servable network shop (``core.commerce.network.shop_for_host``).
The routes choose the shop by ``Host`` alone and answer 404 on any other host, so asking for
``/shop-site/...`` directly on the API host finds nothing.

While the shop's verified catalog is unavailable, every route but ``robots.txt`` answers 503 with
``Retry-After``: a crawler keeps the items it already holds instead of dropping the shop.
Pages are ``Cache-Control: private``: the Firebase Hosting front serves many shops from one site,
so its shared cache must never hold a shop's page.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from api.route_metadata import route_meta
from core.commerce.shop_feed import render_google_feed
from core.commerce.shop_site import (
    PAGE_CSP,
    ShopSite,
    render_home,
    render_llms,
    render_product,
    render_robots,
    render_sitemap,
    render_unavailable,
)
from core.commerce.shop_site_store import load_site
from core.commerce.ucp.authority import normalize_host

SHOP_SITE_PREFIX = "/shop-site"
router = APIRouter(prefix=SHOP_SITE_PREFIX, tags=["Shop site"])

CACHE_CONTROL = "private, max-age=300"
RETRY_AFTER_SECONDS = "600"
_HEADERS = {"Cache-Control": CACHE_CONTROL, "X-Content-Type-Options": "nosniff"}
_PAGE_HEADERS = {**_HEADERS, "Content-Security-Policy": PAGE_CSP, "Referrer-Policy": "strict-origin-when-cross-origin"}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


async def _site(request: Request) -> ShopSite:
    host = normalize_host(request.headers.get("host"))
    site = await load_site(host) if host else None
    if site is None:
        raise _not_found()
    return site


def _unavailable(site: ShopSite) -> HTMLResponse:
    return HTMLResponse(
        render_unavailable(site.name),
        status_code=503,
        headers={"Retry-After": RETRY_AFTER_SECONDS, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/", response_class=HTMLResponse)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.storefront.read",
    rate_limit="shop-site-read",
    idempotency="read-only",
    audit_event="none-public-shop-site-home",
    public_reason="shop-storefront-public-host-bound-listed-shops-verified-catalog-only",
)
async def shop_site_home(request: Request) -> Response:
    site = await _site(request)
    if not site.catalog_verified:
        return _unavailable(site)
    return HTMLResponse(render_home(site), headers=_PAGE_HEADERS)


@router.get("/products/{handle}", response_class=HTMLResponse)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.storefront.read",
    rate_limit="shop-site-read",
    idempotency="read-only",
    audit_event="none-public-shop-site-product",
    public_reason="shop-storefront-public-host-bound-listed-shops-verified-catalog-only",
)
async def shop_site_product(handle: str, request: Request, variant: str | None = None) -> Response:
    site = await _site(request)
    if not site.catalog_verified:
        return _unavailable(site)
    product = site.product(handle)
    if product is None:
        raise _not_found()
    return HTMLResponse(render_product(site, product, (variant or "")[:32] or None), headers=_PAGE_HEADERS)


@router.get("/sitemap.xml")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.storefront.read",
    rate_limit="shop-site-read",
    idempotency="read-only",
    audit_event="none-public-shop-site-sitemap",
    public_reason="shop-storefront-public-host-bound-listed-shops-verified-catalog-only",
)
async def shop_site_sitemap(request: Request) -> Response:
    site = await _site(request)
    if not site.catalog_verified:
        return _unavailable(site)
    return Response(render_sitemap(site), media_type="application/xml", headers=_HEADERS)


@router.get("/robots.txt")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.storefront.read",
    rate_limit="shop-site-read",
    idempotency="read-only",
    audit_event="none-public-shop-site-robots",
    public_reason="shop-storefront-public-host-bound-listed-shops-verified-catalog-only",
)
async def shop_site_robots(request: Request) -> Response:
    site = await _site(request)
    return PlainTextResponse(render_robots(site.host), headers=_HEADERS)


@router.get("/llms.txt")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.storefront.read",
    rate_limit="shop-site-read",
    idempotency="read-only",
    audit_event="none-public-shop-site-llms",
    public_reason="shop-storefront-public-host-bound-listed-shops-verified-catalog-only",
)
async def shop_site_llms(request: Request) -> Response:
    site = await _site(request)
    if not site.catalog_verified:
        return _unavailable(site)
    return PlainTextResponse(render_llms(site), media_type="text/plain; charset=utf-8", headers=_HEADERS)


@router.get("/feeds/google.xml")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.storefront.read",
    rate_limit="shop-site-read",
    idempotency="read-only",
    audit_event="none-public-shop-site-feed",
    public_reason="shop-product-feed-public-host-bound-listed-shops-verified-catalog-only",
)
async def shop_site_google_feed(request: Request) -> Response:
    site = await _site(request)
    if not site.catalog_verified:
        return _unavailable(site)
    return Response(render_google_feed(site), media_type="application/xml; charset=utf-8", headers=_HEADERS)
