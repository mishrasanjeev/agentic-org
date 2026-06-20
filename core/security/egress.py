"""Outbound URL validation for tenant-influenced network destinations."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from core.config import is_strict_runtime_env


class EgressValidationError(ValueError):
    """Raised when a URL/host is unsafe for server-side egress."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    host: str


class PinnedDnsAsyncNetworkBackend:
    """httpcore network backend that connects to a prevalidated DNS answer.

    The normal httpx flow resolves DNS inside the transport after callers have
    validated a URL. For tenant-influenced destinations, that leaves a DNS
    rebinding window between validation and connect. This backend resolves and
    validates once, then passes the vetted IP address to the underlying network
    backend while httpcore still applies TLS/SNI to the original origin host.
    """

    def __init__(self, *, require_dns: bool = True, delegate: Any | None = None) -> None:
        if delegate is None:
            from httpcore._backends.auto import AutoBackend  # noqa: PLC0415

            delegate = AutoBackend()
        self._require_dns = require_dns
        self._delegate = delegate

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> Any:
        targets = resolve_public_hostname_addresses(host, require_dns=self._require_dns)
        last_exc: Exception | None = None
        for target in targets:
            try:
                return await self._delegate.connect_tcp(
                    target,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            # enterprise-gate: broad-except-ok reason=pinned-dns-connect-failure-continues-to-next-public-address
            except Exception as exc:  # pragma: no cover - exercised by httpcore integration
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise EgressValidationError("unresolvable", host)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> Any:
        raise EgressValidationError("unix_socket", "Unix sockets are not allowed for tenant egress")

    async def sleep(self, seconds: float) -> None:
        return await self._delegate.sleep(seconds)


class PinnedDnsAsyncHTTPTransport:
    """httpx async transport backed by a DNS-pinning httpcore pool."""

    def __init__(self, *, require_dns: bool = True) -> None:
        import httpcore  # noqa: PLC0415
        from httpx import AsyncBaseTransport  # noqa: PLC0415
        from httpx._config import DEFAULT_LIMITS, create_ssl_context  # noqa: PLC0415

        self._base_transport_type = AsyncBaseTransport
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=create_ssl_context(verify=True, cert=None, trust_env=True),
            max_connections=DEFAULT_LIMITS.max_connections,
            max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
            keepalive_expiry=DEFAULT_LIMITS.keepalive_expiry,
            http1=True,
            http2=False,
            network_backend=PinnedDnsAsyncNetworkBackend(require_dns=require_dns),
        )

    async def __aenter__(self) -> PinnedDnsAsyncHTTPTransport:
        await self._pool.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: Any | None = None,
    ) -> None:
        from httpx._transports.default import map_httpcore_exceptions  # noqa: PLC0415

        with map_httpcore_exceptions():
            await self._pool.__aexit__(exc_type, exc_value, traceback)

    async def handle_async_request(self, request: Any) -> Any:
        import httpcore  # noqa: PLC0415
        from httpx import Response  # noqa: PLC0415
        from httpx._transports.default import (  # noqa: PLC0415
            AsyncResponseStream,
            map_httpcore_exceptions,
        )
        from httpx._types import AsyncByteStream  # noqa: PLC0415

        assert isinstance(request.stream, AsyncByteStream)
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with map_httpcore_exceptions():
            resp = await self._pool.handle_async_request(req)
        return Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=AsyncResponseStream(resp.stream),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


def egress_dns_validation_required() -> bool:
    """Return True when DNS-resolved egress checks are required."""

    env = os.getenv("AGENTICORG_ENV") or os.getenv("ENV")
    if not env:
        return True
    return is_strict_runtime_env(env)


def host_matches_domain(host: str, domain: str) -> bool:
    normalized_host = host.rstrip(".").lower()
    normalized_domain = domain.rstrip(".").lower()
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def validate_public_url(
    url: str,
    *,
    allowed_schemes: Sequence[str] = ("https",),
    require_dns: bool | None = None,
    allowed_hosts: Iterable[str] | None = None,
    allowed_domains: Iterable[str] | None = None,
) -> ValidatedUrl:
    """Validate a tenant-influenced outbound URL before using credentials with it."""

    parsed = urlparse(str(url or "").strip())
    scheme = parsed.scheme.lower()
    allowed = {item.lower() for item in allowed_schemes}
    if scheme not in allowed:
        raise EgressValidationError("scheme", f"expected one of {sorted(allowed)}, got {scheme!r}")
    if parsed.username or parsed.password:
        raise EgressValidationError("userinfo", "URLs with embedded credentials are not allowed")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise EgressValidationError("missing_host", "URL is missing a hostname")
    validate_public_hostname(
        host,
        require_dns=egress_dns_validation_required() if require_dns is None else require_dns,
    )

    if allowed_hosts is not None:
        host_allowlist = {item.rstrip(".").lower() for item in allowed_hosts}
        if host not in host_allowlist:
            raise EgressValidationError("host_not_allowed", f"{host!r} is not in the allowed host set")
    if allowed_domains is not None:
        domains = tuple(item.rstrip(".").lower() for item in allowed_domains)
        if not any(host_matches_domain(host, domain) for domain in domains):
            raise EgressValidationError("domain_not_allowed", f"{host!r} is not under an allowed domain")
    return ValidatedUrl(url=url, scheme=scheme, host=host)


def validate_public_hostname(hostname: str, *, require_dns: bool | None = None) -> str:
    """Validate a hostname for public server-side egress."""

    host = str(hostname or "").strip().rstrip(".").lower()
    if not host:
        raise EgressValidationError("missing_host", "hostname is required")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise EgressValidationError("local_host", host)
    try:
        ip_literal = ipaddress.ip_address(host)
    except ValueError:
        ip_literal = None
    if ip_literal is not None:
        raise EgressValidationError("ip_literal", f"direct IP URLs are blocked ({host})")
    if require_dns is None:
        require_dns = egress_dns_validation_required()
    if require_dns:
        resolve_public_hostname_addresses(host, require_dns=True)
    return host


def resolve_public_hostname_addresses(
    hostname: str,
    *,
    require_dns: bool | None = None,
) -> tuple[str, ...]:
    """Return validated public DNS answers for a hostname, or the hostname in non-DNS mode."""

    host = validate_public_hostname(hostname, require_dns=False)
    if require_dns is None:
        require_dns = egress_dns_validation_required()
    if not require_dns:
        return (host,)
    addresses = _resolve_host(host)
    if not addresses:
        raise EgressValidationError("unresolvable", host)
    for address in addresses:
        if _is_blocked_ip(address):
            raise EgressValidationError("blocked_ip", f"{host} resolved to {address}")
    return tuple(str(address) for address in addresses)


def build_pinned_async_transport(*, require_dns: bool = True) -> Any:
    """Build an httpx transport that pins DNS to validated public answers."""

    return PinnedDnsAsyncHTTPTransport(require_dns=require_dns)


def _resolve_host(hostname: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return ()

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        ip_text = str(info[4][0])
        try:
            address = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def _is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return True
    if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.IPv4Network("100.64.0.0/10"):
        return True
    return False
