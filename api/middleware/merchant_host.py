# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains served through Firebase Hosting: the merchant host and caching on agent paths.

Firebase Hosting forwards a merchant domain's agent paths to the API with
``Host`` set to the Cloud Run service's own name and the merchant domain in
``X-Forwarded-Host`` (verified by the spike in docs/oacp/merchant-custom-domains.md).
Every UCP and ACP surface selects the merchant from ``Host``, so on the agent
paths only (``/.well-known/ucp``, ``/.well-known/ucp/*``, ``/ucp/*``, ``/acp/*``
and the host check), and only while ``AGENTICORG_CUSTOM_DOMAIN_FIREBASE_ENABLED``
is on, this middleware puts the forwarded host in ``Host`` when it names a
custom domain AgenticOrg itself connected through Firebase Hosting
(``core.commerce.ucp.store.firebase_domain_active``). Any other forwarded value
is ignored and ``Host`` stands: a caller cannot name an arbitrary merchant.
The original ``Host`` is kept in ``scope["agenticorg.original_host"]``.

Firebase Hosting caches a response that carries no ``Cache-Control`` (it adds
``max-age=600``), so on the same paths a response without one gets
``Cache-Control: no-store``. A response that sets its own policy (the UCP
profile's ``public, max-age=300``) keeps it.
"""

from __future__ import annotations

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.commerce import custom_domains as cd
from core.commerce.ucp.authority import normalize_host

logger = structlog.get_logger()


def is_agent_path(path: str) -> bool:
    return (
        path == "/.well-known/ucp"
        or path.startswith("/.well-known/ucp/")
        or path.startswith("/ucp/")
        or path.startswith("/acp/")
        or path == cd.HOST_CHECK_PATH
    )


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class MerchantHostMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_agent_path(str(scope.get("path") or "")) or not cd.firebase_enabled():
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers") or [])
        forwarded = _header(headers, b"x-forwarded-host")
        if forwarded:
            host = normalize_host(_header(headers, b"host"))
            merchant_host = normalize_host(forwarded.split(",")[0])
            if merchant_host is not None and merchant_host != host and await self._verified(merchant_host):
                headers = [(k, v) for k, v in headers if k.lower() != b"host"]
                headers.append((b"host", merchant_host.encode("latin-1")))
                scope = {**scope, "headers": headers, "agenticorg.original_host": host}

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
