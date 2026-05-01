"""RPA / browser-automation egress guard.

SEC-2026-05-P1-006 (docs/BRUTAL_SECURITY_SCAN_2026-05-01.md).

Generic-portal RPA accepted any caller-supplied URL and let server-side
Chromium navigate to it. In multi-tenant SaaS, a tenant admin is NOT
infrastructure-trusted — the same admin could pivot the browser to
probe internal admin panels, RFC1918 ranges, the GCE metadata
endpoint (``169.254.169.254``), or bypass an initial check via DNS
rebinding (resolver returns public IP first, then private IP on
follow-up).

This module is the first line of defense:

1. **Scheme allowlist** — only ``http`` / ``https``. Blocks ``file:``,
   ``ftp:``, ``data:``, browser-extension schemes, etc.
2. **No IP literals** — direct ``http://10.0.0.5/`` is rejected.
   Forces DNS so we can validate the destination.
3. **DNS-resolved IP check** — resolve hostname, walk every A/AAAA,
   reject if ANY result is in a blocked range:
   - Loopback (``127.0.0.0/8``, ``::1``)
   - RFC1918 (``10/8``, ``172.16/12``, ``192.168/16``)
   - Link-local (``169.254/16``, ``fe80::/10``) — covers cloud
     metadata services
   - Carrier-grade NAT (``100.64/10``)
   - Multicast / reserved / unspecified (``0.0.0.0/8``, ``224/4``,
     ``240/4``, ``::/128``, ``fc00::/7``)
4. **Playwright route hook** — re-validate every request mid-flight
   so DNS rebinding (DNS changes the resolved IP between the initial
   check and the actual fetch) is caught.

The audit's longer-term ask — a tenant-approved domain registry —
is a follow-up. This PR ships the SSRF block which is the urgent
risk; the allowlist UI + DB schema is separate work.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Final
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger()

# Allowed URL schemes. Anything else is rejected with reason ``scheme``.
ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# DNS resolution timeout. Aggressive on purpose — the alternative is
# a slow attack (resolver delay) bypassing the gate by timing out.
_DNS_TIMEOUT_SECONDS: Final[float] = 3.0


class EgressBlocked(Exception):  # noqa: N818 — domain term; "Error" suffix would be redundant
    """Raised when the egress guard refuses a destination.

    The ``reason`` attribute is one of:
    - ``"scheme"``: non-http(s) scheme.
    - ``"ip_literal"``: caller passed an IP-literal URL.
    - ``"unresolvable"``: DNS lookup failed (or was empty).
    - ``"blocked_ip:<address>"``: resolved IP is in a blocked range.
    - ``"missing_host"``: URL had no hostname.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _is_blocked_ip(addr: ipaddress._BaseAddress) -> bool:
    """Return True for any address Python or operators consider unsafe.

    Standard library does most of the heavy lifting via the
    ``is_private`` / ``is_loopback`` / ``is_link_local`` /
    ``is_multicast`` / ``is_reserved`` properties. We add explicit
    blocks for cloud-metadata (``169.254.169.254``) — already covered
    by ``is_link_local`` but worth flagging — and carrier-grade NAT
    (``100.64.0.0/10``) which Python doesn't tag as ``is_private``.
    """
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return True
    # Carrier-grade NAT — RFC 6598. Used by ISPs internally; no
    # legitimate public service lives here.
    if isinstance(addr, ipaddress.IPv4Address):
        if addr in ipaddress.IPv4Network("100.64.0.0/10"):
            return True
    return False


async def _resolve_host(hostname: str) -> list[ipaddress._BaseAddress]:
    """DNS-resolve ``hostname`` to all A/AAAA addresses.

    Runs in the default executor so we don't block the event loop on
    slow resolvers. Returns ``[]`` on lookup failure — the caller
    treats empty as ``unresolvable``.
    """
    loop = asyncio.get_event_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(hostname, None),
            timeout=_DNS_TIMEOUT_SECONDS,
        )
    except (TimeoutError, socket.gaierror, OSError) as exc:
        logger.warning("egress_guard_dns_failed", host=hostname, error=str(exc))
        return []

    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            addresses.append(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
    return addresses


async def validate_egress_url(url: str) -> str:
    """Validate ``url`` for outbound RPA navigation. Returns the URL
    unchanged on success, raises ``EgressBlocked`` on rejection.

    Pre-flight checks:
    - Non-empty + parses as URL.
    - Scheme is ``http`` or ``https``.
    - Hostname is present and is NOT an IP literal.
    - Hostname's DNS resolution returns at least one IP, and EVERY
      resolved IP is public (not loopback / RFC1918 / link-local /
      carrier-grade NAT / multicast / reserved).

    The caller is responsible for additional defenses (Playwright
    route interception for DNS rebinding mid-navigation, see
    ``apply_playwright_route_guard``).
    """
    if not url or not isinstance(url, str):
        raise EgressBlocked("scheme", "URL must be a non-empty string")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise EgressBlocked(
            "scheme",
            f"only http/https allowed, got {parsed.scheme!r}",
        )

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise EgressBlocked("missing_host", url)

    # Reject IP literals — force DNS so we can validate.
    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None
    if ip_literal is not None:
        raise EgressBlocked(
            "ip_literal",
            f"direct IP URLs are blocked ({hostname}); use a hostname",
        )

    addresses = await _resolve_host(hostname)
    if not addresses:
        raise EgressBlocked("unresolvable", hostname)

    for addr in addresses:
        if _is_blocked_ip(addr):
            logger.warning(
                "egress_guard_blocked_resolved_ip",
                host=hostname,
                resolved_to=str(addr),
            )
            raise EgressBlocked(
                f"blocked_ip:{addr}",
                f"{hostname} resolved to {addr} which is in a private/reserved range",
            )

    return url


def apply_playwright_route_guard(page, allowed_origins: set[str] | None = None):  # noqa: ANN001
    """Install a Playwright route interceptor that re-validates every
    request mid-navigation.

    Defends against DNS rebinding: an attacker's domain can resolve
    to a public IP for the initial check, then to ``169.254.169.254``
    for subsequent requests once the browser is parked on the page.
    Each in-flight request gets re-resolved + re-validated.

    Pass ``allowed_origins`` (set of ``"https://example.com"`` strings)
    to also enforce same-origin or domain-allowlist semantics. When
    ``None``, the IP-block defense is the only check — typical for
    "let admin log into any public portal" UX.
    """
    async def _route_handler(route):
        request_url = route.request.url
        try:
            await validate_egress_url(request_url)
        except EgressBlocked as blocked:
            logger.warning(
                "rpa_route_blocked",
                url=request_url,
                reason=blocked.reason,
                detail=blocked.detail,
            )
            await route.abort(error_code="addressunreachable")
            return

        if allowed_origins is not None:
            parsed = urlparse(request_url)
            origin = f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}"
            if origin not in allowed_origins:
                logger.warning(
                    "rpa_route_blocked",
                    url=request_url,
                    reason="not_in_allowed_origins",
                    origin=origin,
                )
                await route.abort(error_code="addressunreachable")
                return

        await route.continue_()

    return page.route("**/*", _route_handler)


def egress_guard_strict_mode_required() -> bool:
    """Whether the calling environment requires a domain allowlist.

    Returns True in production / staging — operators MUST provide
    ``AGENTICORG_RPA_ALLOWED_DOMAINS`` (comma-separated) for any RPA
    job in those environments. In local / dev / test, the IP-block
    defense alone is acceptable for development workflows.
    """
    env = (os.getenv("AGENTICORG_ENV") or "").strip().lower()
    return env not in {"local", "dev", "development", "test", "testing"}


__all__ = [
    "ALLOWED_SCHEMES",
    "EgressBlocked",
    "apply_playwright_route_guard",
    "egress_guard_strict_mode_required",
    "validate_egress_url",
]
