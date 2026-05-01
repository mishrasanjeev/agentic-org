"""SEC-2026-05-P1-006 PR-E: RPA / browser-automation egress guard pins.

Pre-fix: ``rpa/scripts/generic_portal.py`` accepted any caller-supplied
``portal_url`` / ``target_url`` and let server-side Chromium navigate
to it. A tenant admin could:

- Probe internal admin panels via RFC1918 / loopback URLs.
- Fetch GCE/AWS metadata via ``169.254.169.254``.
- Use ``file:`` to read host filesystem.
- Use DNS rebinding (resolver returns public IP first, private IP later)
  to bypass an initial check.

PR-E adds ``rpa/egress_guard.py`` with three layers (scheme allowlist,
IP-literal block, DNS-resolution check) plus a Playwright route hook
for DNS rebinding defense. These pins ensure the SSRF surface stays
closed.

Hermetic by design — patches ``socket.getaddrinfo`` and
``loop.getaddrinfo`` so tests don't depend on the test machine's DNS
resolver returning known values.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rpa.egress_guard import (
    ALLOWED_SCHEMES,
    EgressBlocked,
    _is_blocked_ip,
    apply_playwright_route_guard,
    validate_egress_url,
)

# ─────────────────────────────────────────────────────────────────
# Helpers — fake DNS resolution
# ─────────────────────────────────────────────────────────────────


def _patch_resolver(addresses: list[str]):
    """Patch ``rpa.egress_guard._resolve_host`` to return the given
    addresses. Using the helper-level patch (not socket.getaddrinfo)
    so the test doesn't depend on Python event-loop internals."""
    parsed = [ipaddress.ip_address(a) for a in addresses]
    return patch(
        "rpa.egress_guard._resolve_host",
        AsyncMock(return_value=parsed),
    )


# ─────────────────────────────────────────────────────────────────
# Scheme allowlist
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocks_file_scheme() -> None:
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("file:///etc/passwd")
    assert exc.value.reason == "scheme"


@pytest.mark.asyncio
async def test_blocks_ftp_scheme() -> None:
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("ftp://example.com/")
    assert exc.value.reason == "scheme"


@pytest.mark.asyncio
async def test_blocks_data_uri() -> None:
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("data:text/html,<script>alert(1)</script>")
    assert exc.value.reason == "scheme"


@pytest.mark.asyncio
async def test_allowed_schemes_are_just_http_https() -> None:
    """Pin the allowed-scheme set so a future contributor can't widen
    it without explicitly removing this test."""
    assert ALLOWED_SCHEMES == frozenset({"http", "https"})


# ─────────────────────────────────────────────────────────────────
# IP-literal block — direct IP URLs are rejected
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocks_ipv4_literal_url() -> None:
    """Direct IP URLs bypass DNS — reject so we always validate via
    hostname resolution."""
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("http://1.2.3.4/admin")
    assert exc.value.reason == "ip_literal"


@pytest.mark.asyncio
async def test_blocks_ipv4_literal_metadata_endpoint() -> None:
    """The classic SSRF target — even via IP literal."""
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("http://169.254.169.254/latest/meta-data/")
    assert exc.value.reason == "ip_literal"


@pytest.mark.asyncio
async def test_blocks_ipv6_literal_url() -> None:
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("http://[::1]/")
    assert exc.value.reason == "ip_literal"


# ─────────────────────────────────────────────────────────────────
# DNS-resolution check — every resolved IP must be public
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocks_hostname_resolving_to_loopback() -> None:
    """Common DNS rebinding shape: attacker domain resolves to 127.x."""
    with _patch_resolver(["127.0.0.1"]):
        with pytest.raises(EgressBlocked) as exc:
            await validate_egress_url("http://attacker.example/")
    assert exc.value.reason.startswith("blocked_ip:")
    assert "127.0.0.1" in exc.value.reason


@pytest.mark.asyncio
async def test_blocks_hostname_resolving_to_metadata_link_local() -> None:
    """``169.254.169.254`` is the GCE/AWS metadata IP — the canonical
    SSRF target."""
    with _patch_resolver(["169.254.169.254"]):
        with pytest.raises(EgressBlocked) as exc:
            await validate_egress_url("http://metadata-rebind.example/")
    assert "169.254.169.254" in exc.value.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addr",
    ["10.0.0.5", "172.16.0.5", "192.168.1.5", "100.64.1.5"],
    ids=["rfc1918_10", "rfc1918_172", "rfc1918_192", "cgnat_100_64"],
)
async def test_blocks_hostname_resolving_to_private_range(addr: str) -> None:
    """RFC1918 + carrier-grade NAT — every private range a tenant
    admin might want to probe is blocked."""
    with _patch_resolver([addr]):
        with pytest.raises(EgressBlocked) as exc:
            await validate_egress_url("http://attacker.example/")
    assert addr in exc.value.reason


@pytest.mark.asyncio
async def test_blocks_when_any_resolved_ip_is_private() -> None:
    """DNS rebinding defense: even if SOME resolved IPs are public,
    a SINGLE private one in the response is enough to reject. An
    attacker who returns ``[8.8.8.8, 169.254.169.254]`` can't bypass
    the gate by ordering the response."""
    with _patch_resolver(["8.8.8.8", "169.254.169.254"]):
        with pytest.raises(EgressBlocked) as exc:
            await validate_egress_url("http://attacker.example/")
    assert "169.254.169.254" in exc.value.reason


@pytest.mark.asyncio
async def test_allows_hostname_resolving_to_only_public_ips() -> None:
    """Happy path — public IPs pass through."""
    with _patch_resolver(["8.8.8.8", "1.1.1.1"]):
        result = await validate_egress_url("https://api.example.com/v1/login")
    assert result == "https://api.example.com/v1/login"


@pytest.mark.asyncio
async def test_blocks_unresolvable_hostname() -> None:
    """DNS lookup failure → reject, don't fail open. An attacker
    could otherwise cause the gate to pass by killing their DNS."""
    with _patch_resolver([]):
        with pytest.raises(EgressBlocked) as exc:
            await validate_egress_url("https://nx.example.com/")
    assert exc.value.reason == "unresolvable"


# ─────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_url_blocks() -> None:
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("")
    assert exc.value.reason == "scheme"


@pytest.mark.asyncio
async def test_non_string_url_blocks() -> None:
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url(None)  # type: ignore[arg-type]
    assert exc.value.reason == "scheme"


@pytest.mark.asyncio
async def test_url_with_no_hostname_blocks() -> None:
    """``http:///path`` parses but has no host. Reject."""
    with pytest.raises(EgressBlocked) as exc:
        await validate_egress_url("http:///path")
    assert exc.value.reason == "missing_host"


# ─────────────────────────────────────────────────────────────────
# _is_blocked_ip — direct unit test of the predicate
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr,expected",
    [
        ("127.0.0.1", True),
        ("10.0.0.1", True),
        ("172.16.0.1", True),
        ("192.168.0.1", True),
        ("169.254.169.254", True),
        ("100.64.0.1", True),
        ("224.0.0.1", True),  # multicast
        ("0.0.0.0", True),  # unspecified
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("142.250.190.46", False),  # google.com
        ("::1", True),
        ("fe80::1", True),  # link-local v6
        ("fc00::1", True),  # ULA v6
        ("2606:4700::1", False),  # cloudflare v6
    ],
)
def test_is_blocked_ip_matrix(addr: str, expected: bool) -> None:
    """Direct predicate test — pin every blocked / allowed range
    deterministically. A future contributor who adds an exception
    must update this matrix."""
    assert _is_blocked_ip(ipaddress.ip_address(addr)) is expected


# ─────────────────────────────────────────────────────────────────
# Playwright route guard — DNS rebinding mid-navigation
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_playwright_route_guard_aborts_blocked_request() -> None:
    """The route hook calls ``route.abort`` when the in-flight URL
    fails ``validate_egress_url``. Pin the abort call so a future
    refactor can't accidentally fall through to ``continue_``."""
    routed_handler: list[Any] = []

    class _FakePage:
        async def route(self, _pattern, handler):
            routed_handler.append(handler)

    page = _FakePage()
    await apply_playwright_route_guard(page)
    assert len(routed_handler) == 1
    handler = routed_handler[0]

    aborted: list[str] = []

    class _FakeRoute:
        class _FakeRequest:
            url = "http://169.254.169.254/latest/meta-data/"

        request = _FakeRequest()

        async def abort(self, error_code=""):
            aborted.append(error_code)

        async def continue_(self):
            raise AssertionError("must not continue on blocked URL")

    await handler(_FakeRoute())
    assert aborted == ["addressunreachable"]


@pytest.mark.asyncio
async def test_playwright_route_guard_continues_allowed_request() -> None:
    """Public URL → route.continue_() called, no abort."""
    routed_handler: list[Any] = []

    class _FakePage:
        async def route(self, _pattern, handler):
            routed_handler.append(handler)

    page = _FakePage()
    await apply_playwright_route_guard(page)
    handler = routed_handler[0]

    continued: list[bool] = []

    class _FakeRoute:
        class _FakeRequest:
            url = "https://api.example.com/v1/data"

        request = _FakeRequest()

        async def abort(self, error_code=""):
            raise AssertionError("must not abort allowed URL")

        async def continue_(self):
            continued.append(True)

    with _patch_resolver(["8.8.8.8"]):
        await handler(_FakeRoute())
    assert continued == [True]


# ─────────────────────────────────────────────────────────────────
# Source pin — generic_portal calls the guard before navigation
# ─────────────────────────────────────────────────────────────────


def test_generic_portal_calls_egress_guard_before_navigation() -> None:
    """SEC-2026-05-P1-006 contract: the SSRF defense MUST run before
    ``page.goto``. Source-grep pin so a future refactor can't
    accidentally remove the guard call.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "rpa"
        / "scripts"
        / "generic_portal.py"
    ).read_text(encoding="utf-8")
    # The validate_egress_url call must precede the first page.goto.
    guard_idx = src.find("validate_egress_url(portal_url)")
    goto_idx = src.find("await page.goto(portal_url")
    assert guard_idx >= 0, (
        "generic_portal.py is missing validate_egress_url(portal_url) — "
        "the SSRF guard MUST run before page.goto. SEC-2026-05-P1-006."
    )
    assert goto_idx >= 0, "page.goto(portal_url) lookup failed"
    assert guard_idx < goto_idx, (
        "validate_egress_url must run BEFORE page.goto — caught a "
        "regression that would let SSRF land before the guard fires."
    )
    # Same check for the route guard.
    assert "apply_playwright_route_guard(page)" in src, (
        "generic_portal.py is missing apply_playwright_route_guard — "
        "DNS rebinding defense is gone."
    )
