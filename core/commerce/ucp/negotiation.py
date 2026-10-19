# SPDX-License-Identifier: Apache-2.0
"""``UCP-Agent`` parsing, platform profile fetching and capability negotiation.

UCP 2026-08-25 overview, Negotiation Protocol: every request carries
``UCP-Agent: profile="<platform profile URL>"`` (an RFC 8941 Dictionary). The
business fetches that profile (HTTPS only, no redirects, SSRF guard, a body
cap and a cache with a 60 second floor), validates its ``ucp.version`` against
the releases it serves (``version_unsupported`` otherwise), and intersects
capabilities: names present in both profiles, at a version both declare (the
release, for ``dev.ucp.*``), then extensions whose parents are all absent are
pruned until nothing changes (Intersection Algorithm). Every response echoes
``ucp.version`` and the active capabilities relevant to the operation.

A ``version`` parameter on the ``profile`` member (``profile="..."; version=
"2026-04-08"``) is read as the platform's declared release and refused early
when this server does not speak it; the fetched profile stays authoritative.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from core.commerce.ucp import versions as v
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.registry import CapabilityEntry, CapabilityRegistry, RegistryContext

logger = structlog.get_logger()

PROFILE_BODY_LIMIT = 256 * 1024
PROFILE_TIMEOUT_SECONDS = 5.0
PROFILE_CACHE_MIN_SECONDS = 60
PROFILE_CACHE_MAX_SECONDS = 3600
_PROFILE_CACHE_SIZE = 256

ROOT_CAPABILITY = {"checkout": v.CHECKOUT, "cart": v.CART, "order": v.ORDER}


class NegotiationError(Exception):
    """A discovery or version error (UCP overview, Error Codes); ``status`` is the REST status."""

    def __init__(self, code: str, status: int, content: str) -> None:
        super().__init__(content)
        self.code = code
        self.status = status
        self.content = content


# ---------------------------------------------------------------------------
# RFC 8941 Dictionary (the subset UCP-Agent uses: strings, tokens, integers, booleans, parameters)


@dataclass(frozen=True)
class SfMember:
    value: Any
    params: dict[str, Any] = field(default_factory=dict)


def parse_sf_dictionary(text: str) -> dict[str, SfMember]:
    """Parse an RFC 8941 Dictionary (section 4.2.2); raises ValueError on malformed input."""
    parser = _SfParser(text)
    out = parser.dictionary()
    return out


_KEY = "_-.*"


class _SfParser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def _ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def _peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def dictionary(self) -> dict[str, SfMember]:
        out: dict[str, SfMember] = {}
        self._ws()
        while self.i < len(self.s):
            key = self._key()
            if self._peek() == "=":
                self.i += 1
                value = self._bare_item()
            else:
                value = True
            out[key] = SfMember(value, self._params())
            self._ws()
            if self.i >= len(self.s):
                break
            if self._peek() != ",":
                raise ValueError("expected ',' between dictionary members")
            self.i += 1
            self._ws()
            if self.i >= len(self.s):
                raise ValueError("trailing comma")
        return out

    def _key(self) -> str:
        start = self.i
        if not (self._peek().islower() or self._peek() == "*"):
            raise ValueError("invalid key")
        while self.i < len(self.s) and (self.s[self.i].islower() or self.s[self.i].isdigit() or self.s[self.i] in _KEY):
            self.i += 1
        return self.s[start : self.i]

    def _params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        while self._peek() == ";":
            self.i += 1
            self._ws()
            key = self._key()
            if self._peek() == "=":
                self.i += 1
                params[key] = self._bare_item()
            else:
                params[key] = True
        return params

    def _bare_item(self) -> Any:
        ch = self._peek()
        if ch == '"':
            return self._string()
        if ch == "?":
            self.i += 1
            flag = self._peek()
            if flag not in "01" or not flag:
                raise ValueError("invalid boolean")
            self.i += 1
            return flag == "1"
        if ch == "-" or ch.isdigit():
            start = self.i
            self.i += 1
            while self.i < len(self.s) and (self.s[self.i].isdigit() or self.s[self.i] == "."):
                self.i += 1
            text = self.s[start : self.i]
            return float(text) if "." in text else int(text)
        if ch.isalpha() or ch == "*":
            start = self.i
            while self.i < len(self.s) and (self.s[self.i].isalnum() or self.s[self.i] in "!#$%&'*+-.^_`|~:/"):
                self.i += 1
            return self.s[start : self.i]
        raise ValueError("invalid item")

    def _string(self) -> str:
        self.i += 1
        out: list[str] = []
        while self.i < len(self.s):
            ch = self.s[self.i]
            self.i += 1
            if ch == "\\":
                if self.i >= len(self.s) or self.s[self.i] not in '"\\':
                    raise ValueError("invalid escape")
                out.append(self.s[self.i])
                self.i += 1
            elif ch == '"':
                return "".join(out)
            elif ord(ch) < 0x20 or ord(ch) > 0x7E:
                raise ValueError("invalid string character")
            else:
                out.append(ch)
        raise ValueError("unterminated string")


@dataclass(frozen=True)
class UcpAgent:
    profile_url: str
    declared_version: str | None


def parse_ucp_agent(header: str | None) -> UcpAgent:
    """The platform profile URL (and declared release, if any) from ``UCP-Agent``."""
    if not header or not header.strip():
        raise NegotiationError("invalid_profile_url", 400, "The UCP-Agent header with a profile URL is required.")
    try:
        members = parse_sf_dictionary(header.strip())
    except ValueError as exc:
        raise NegotiationError("invalid_profile_url", 400, "UCP-Agent is not an RFC 8941 dictionary.") from exc
    profile = members.get("profile")
    if profile is None or not isinstance(profile.value, str) or not profile.value:
        raise NegotiationError("invalid_profile_url", 400, 'UCP-Agent must carry profile="<url>".')
    declared = profile.params.get("version")
    if declared is None and "version" in members:
        declared = members["version"].value
    url = profile.value
    parsed = httpx.URL(url) if _looks_absolute(url) else None
    if parsed is None or parsed.scheme not in {"https", "http"} or not parsed.host or parsed.userinfo:
        raise NegotiationError("invalid_profile_url", 400, "The UCP-Agent profile must be an absolute https URL.")
    return UcpAgent(profile_url=url, declared_version=declared if isinstance(declared, str) else None)


def _looks_absolute(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError, TypeError):
        return False
    return bool(parsed.scheme and parsed.host)


# ---------------------------------------------------------------------------
# Platform profile


@dataclass(frozen=True)
class PlatformProfile:
    url: str
    version: str
    capabilities: dict[str, tuple[dict[str, Any], ...]]

    def versions_of(self, name: str) -> set[str]:
        return {str(entry.get("version")) for entry in self.capabilities.get(name, ()) if entry.get("version")}

    def order_webhook_url(self, version: str) -> str | None:
        for entry in self.capabilities.get(v.ORDER, ()):
            if entry.get("version") != version:
                continue
            config = entry.get("config") if isinstance(entry.get("config"), Mapping) else {}
            url = config.get("webhook_url")
            if isinstance(url, str) and url:
                return url
        return None


def platform_profile_from_document(url: str, document: Any) -> PlatformProfile:
    if not isinstance(document, Mapping) or not isinstance(document.get("ucp"), Mapping):
        raise NegotiationError("profile_malformed", 422, "The platform profile has no ucp object.")
    ucp = document["ucp"]
    version = ucp.get("version")
    if not v.is_version(version):
        raise NegotiationError("profile_malformed", 422, "The platform profile has no valid ucp.version.")
    raw = ucp.get("capabilities") or {}
    if not isinstance(raw, Mapping):
        raise NegotiationError("profile_malformed", 422, "The platform profile capabilities must be a map.")
    capabilities: dict[str, tuple[dict[str, Any], ...]] = {}
    for name, entries in raw.items():
        items = entries if isinstance(entries, list) else [entries]
        capabilities[str(name)] = tuple(dict(item) for item in items if isinstance(item, Mapping))
    return PlatformProfile(url=url, version=str(version), capabilities=capabilities)


ProfileFetcher = Callable[[str], Awaitable[Any]]
# enterprise-gate: process-local-ok reason=per-replica-cache-of-public-platform-profiles-with-ttl-not-security-state
_cache: dict[str, tuple[float, Any]] = {}


def clear_profile_cache() -> None:
    _cache.clear()


async def default_profile_fetcher(url: str) -> Any:
    """GET the platform profile: https only, public address, no redirects, bounded body, cached >= 60 s."""
    from core.security.egress import (
        EgressValidationError,
        build_pinned_async_transport,
        egress_dns_validation_required,
        validate_public_url_async,
    )

    now = time.monotonic()
    cached = _cache.get(url)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        await validate_public_url_async(url, allowed_schemes=("https",))
    except EgressValidationError as exc:
        raise NegotiationError("invalid_profile_url", 400, "The profile URL is not a public https URL.") from exc
    try:
        async with (
            httpx.AsyncClient(
                transport=build_pinned_async_transport(require_dns=egress_dns_validation_required()),
                timeout=PROFILE_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client,
            client.stream("GET", url, headers={"Accept": "application/json"}) as response,
        ):
            if response.status_code != 200:
                raise NegotiationError("profile_unreachable", 424, "The platform profile could not be fetched.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > PROFILE_BODY_LIMIT:
                    raise NegotiationError("profile_malformed", 422, "The platform profile is too large.")
            ttl = _cache_ttl(response.headers.get("cache-control"))
    except httpx.HTTPError as exc:
        raise NegotiationError("profile_unreachable", 424, "The platform profile could not be fetched.") from exc
    try:
        document = json.loads(bytes(body))
    except ValueError as exc:
        raise NegotiationError("profile_malformed", 422, "The platform profile is not JSON.") from exc
    if len(_cache) >= _PROFILE_CACHE_SIZE:
        _cache.pop(next(iter(_cache)))
    _cache[url] = (now + ttl, document)
    return document


def _cache_ttl(cache_control: str | None) -> float:
    ttl = PROFILE_CACHE_MIN_SECONDS
    for part in (cache_control or "").split(","):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age" and value.isdigit():
            ttl = int(value)
    return float(min(max(ttl, PROFILE_CACHE_MIN_SECONDS), PROFILE_CACHE_MAX_SECONDS))


# ---------------------------------------------------------------------------
# Intersection


@dataclass(frozen=True)
class Negotiated:
    version: str
    active: dict[str, CapabilityEntry]
    platform: PlatformProfile

    def has(self, name: str) -> bool:
        return name in self.active

    def response_capabilities(self, resource: str) -> dict[str, list[dict[str, Any]]]:
        """Active capabilities relevant to ``resource`` (UCP overview, Response Capability Selection)."""
        return {
            name: [entry.response_entry(self.version)]
            for name, entry in sorted(self.active.items())
            if resource in entry.resources
        }

    def capability_versions(self) -> dict[str, str]:
        return dict.fromkeys(sorted(self.active), self.version)


def intersect(business: list[CapabilityEntry], platform: PlatformProfile, version: str) -> dict[str, CapabilityEntry]:
    """UCP Intersection Algorithm: shared names at the shared release, then prune orphaned extensions."""
    active = {entry.name: entry for entry in business if version in platform.versions_of(entry.name)}
    changed = True
    while changed:
        changed = False
        for name, entry in list(active.items()):
            parents = entry.extends_for(version)
            if parents and not any(parent in active for parent in parents):
                del active[name]
                changed = True
    return active


async def negotiate(
    *,
    ucp_agent: str | None,
    merchant: MerchantUcp,
    registry: CapabilityRegistry,
    context: RegistryContext,
    resource: str,
    fetcher: ProfileFetcher | None = None,
    supported_versions: tuple[str, ...] | None = None,
) -> Negotiated:
    """Negotiate one request. Raises ``NegotiationError``; ``capabilities_incompatible`` is a 200 business outcome."""
    agent = parse_ucp_agent(ucp_agent)
    supported = supported_versions or v.supported_ucp_versions()
    if agent.declared_version is not None and agent.declared_version not in supported:
        raise NegotiationError(
            "version_unsupported", 422, f"UCP version {agent.declared_version} is not supported by this business."
        )
    try:
        document = await (fetcher or default_profile_fetcher)(agent.profile_url)
    except NegotiationError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise NegotiationError("profile_unreachable", 424, "The platform profile could not be fetched.") from exc
    platform = platform_profile_from_document(agent.profile_url, document)
    if platform.version not in supported:
        raise NegotiationError("version_unsupported", 422, f"UCP version {platform.version} is not supported.")
    active = intersect(registry.capabilities(merchant, platform.version, context), platform, platform.version)
    root = ROOT_CAPABILITY[resource]
    if root not in active:
        raise NegotiationError(
            "capabilities_incompatible",
            200,
            f"The platform profile does not declare {root} at version {platform.version}.",
        )
    return Negotiated(version=platform.version, active=active, platform=platform)
