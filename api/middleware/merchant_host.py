# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains served through Firebase Hosting: the merchant host, caching on agent
paths, and a network shop's own storefront on its host.

Firebase Hosting forwards a merchant domain's agent paths to the API with
``Host`` set to the Cloud Run service's own name and the merchant domain in
``X-Forwarded-Host`` (verified by the spike in docs/oacp/merchant-custom-domains.md).
Every UCP and ACP surface selects the merchant from ``Host``, so on the agent
paths only (``/.well-known/ucp``, ``/.well-known/ucp/*``, ``/ucp/*``, ``/acp/*``
and the host check) and the shop-site paths below, and only while
``AGENTICORG_CUSTOM_DOMAIN_FIREBASE_ENABLED`` is on, this middleware puts the
forwarded host in ``Host`` when it names a custom domain AgenticOrg itself
connected through Firebase Hosting (``core.commerce.ucp.store.firebase_domain_active``).
Any other forwarded value is ignored and ``Host`` stands: a caller cannot name
an arbitrary merchant. The original ``Host`` is kept in
``scope["agenticorg.original_host"]``.

Shop site: on ``/``, ``/products/*``, ``/sitemap.xml``, ``/robots.txt``,
``/llms.txt`` and ``/feeds/*``, when the (resolved) ``Host`` is a listed,
servable network shop (``core.commerce.network.shop_for_host``), the path is
moved under ``/shop-site`` (``api/v1/shop_site.py``), which renders that shop's
storefront and feed. On every other host these paths reach the application
unchanged. A lookup failure leaves the path unchanged (fail closed).

Firebase Hosting caches a response that carries no ``Cache-Control`` (it adds
``max-age=600``), so on the same paths a response without one gets
``Cache-Control: no-store``. A response that sets its own policy (the UCP
profile's ``public, max-age=300``, the shop site's ``private, max-age=300``)
keeps it.
"""

from __future__ import annotations

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.commerce import custom_domains as cd
from core.commerce.ucp.authority import normalize_host

logger = structlog.get_logger()

SHOP_SITE_PREFIX = "/shop-site"
SHOP_SITE_FILES = frozenset({"/", "/sitemap.xml", "/robots.txt", "/llms.txt"})
SHOP_SITE_PREFIXES = ("/products/", "/feeds/")


def is_agent_path(path: str) -> bool:
    return (
        path == "/.well-known/ucp"
        or path.startswith("/.well-known/ucp/")
        or path.startswith("/ucp/")
        or path.startswith("/acp/")
        or path == cd.HOST_CHECK_PATH
    )


def is_shop_site_path(path: str) -> bool:
    return path in SHOP_SITE_FILES or path.startswith(SHOP_SITE_PREFIXES)


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class MerchantHostMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        agent, site = is_agent_path(path), is_shop_site_path(path)
        firebase = cd.firebase_enabled()
        if not site and not (agent and firebase):
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers") or [])
        if firebase:
            forwarded = _header(headers, b"x-forwarded-host")
            if forwarded:
                host = normalize_host(_header(headers, b"host"))
                merchant_host = normalize_host(forwarded.split(",")[0])
                if merchant_host is not None and merchant_host != host and await self._verified(merchant_host):
                    headers = [(k, v) for k, v in headers if k.lower() != b"host"]
                    headers.append((b"host", merchant_host.encode("latin-1")))
                    scope = {**scope, "headers": headers, "agenticorg.original_host": host}
        rewritten = False
        if site:
            host = normalize_host(_header(headers, b"host"))
            if host is not None and await self._shop_host(host):
                raw_path = scope.get("raw_path") or path.encode("latin-1")
                scope = {
                    **scope,
                    "path": SHOP_SITE_PREFIX + path,
                    "raw_path": SHOP_SITE_PREFIX.encode("latin-1") + raw_path,
                }
                rewritten = True
        if not firebase and not rewritten:
            await self.app(scope, receive, send)
            return

        async def send_with_cache_policy(message: Message) -> None:
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers") or [])
                if _header(response_headers, b"cache-control") is None:
                    response_headers.append((b"cache-control", b"no-store"))
                    message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, receive, send_with_cache_policy)

    @staticmethod
    async def _verified(host: str) -> bool:
        from core.commerce.ucp.store import firebase_domain_active  # noqa: PLC0415

        try:
            return await firebase_domain_active(host)
        # enterprise-gate: broad-except-ok reason=lookup-failure-fails-closed-host-header-stands
        except Exception as exc:
            logger.warning("merchant_host_lookup_failed", error_type=type(exc).__name__)
            return False

    @staticmethod
    async def _shop_host(host: str) -> bool:
        from core.commerce.network import shop_for_host  # noqa: PLC0415

        try:
            return await shop_for_host(host) is not None
        # enterprise-gate: broad-except-ok reason=lookup-failure-fails-closed-path-unchanged
        except Exception as exc:
            logger.warning("shop_site_host_lookup_failed", error_type=type(exc).__name__)
            return False
