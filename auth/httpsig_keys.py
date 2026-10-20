# SPDX-License-Identifier: Apache-2.0
"""Key resolution, replay claims and registry lookups for inbound agent request signatures (G47, G51).

Every network read here is async, over the DNS-pinning egress transport
(``core.security.egress.build_pinned_async_transport``) after the async URL
shape check, with no redirects, a 5 s timeout and a size cap, and is cached
per process: 300 s for a good answer, 60 s for a failed one (negative cache),
so a forged keyid cannot force a fetch per request.

Where keys come from:

- Visa Trusted Agent Protocol (``tag`` ``agent-browser-auth`` /
  ``agent-payer-auth``): the TAP key store. TAP-SPEC names a key-store
  ``GET /keys`` operation keyed by ``keyID`` but not its URL, so the operator
  pins the URL templates in ``AGENTICORG_HTTPSIG_TAP_KEY_STORE_URLS``
  (https only, each holding ``{keyid}``). The answer is a JWK or a JWK Set; a
  JWK Set member is used only when its ``kid`` is the keyid.
- Web Bot Auth (``tag="web-bot-auth"``): the Signature-Agent's key
  directory, ``<origin>/.well-known/http-message-signatures-directory``
  (draft-meunier-webbotauth-httpsig-directory-00 section 3), fetched only
  for an https origin listed in ``AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS``,
  200 only, no redirects (section 5.5), and only keys with a valid directory
  response signature are used (``verify_directory_response``, section 5.2).
  Keys are looked up by the (Signature-Agent URL, keyid) pair
  (draft-ietf-webbotauth-httpsig-protocol-00 section 5.4).
- The Grantex registry directory: the registry does not hold agents' private
  keys, so it signs its directory once with the platform key
  (``tag="grantex-registry-directory"``, ``"@authority";req`` and
  ``content-digest``). When the Signature-Agent origin is the Grantex origin
  (``GRANTEX_BASE_URL``) and is allow-listed, that countersignature is checked
  against the Grantex platform JWKS (the same pinned JWKS the OACP artifact
  verifier uses) and the listed keys are trusted through it.

Fetch budget: every fetch (key store, directory, registry lookup) also draws
on one per-process budget, a token bucket (``FETCH_BURST`` fetches, refilled at
``FETCH_RATE_PER_SECOND``) with at most ``MAX_CONCURRENT_FETCHES`` in flight.
A fetch over budget is not made and not cached: the key does not resolve
(``key_unknown``: refused in ``enforce`` mode, logged in ``log`` mode) and
``httpsig_key_fetch_budget_exceeded`` is logged. This bounds the outbound
traffic a stream of invented keyids can cause, on top of the negative cache.

Replay: ``claim_nonce`` is the shared, atomic nonce store, one Redis
``SET NX`` with an expiry of at least the profile's replay window.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from grantex_agent_httpsig import (
    SIGNATURE_DIRECTORY_MEDIA_TYPE,
    SIGNATURE_DIRECTORY_PATH,
    AgentHttpSigError,
    HttpRequest,
    HttpResponse,
    InnerList,
    Item,
    create_signature_base,
    jwk_thumbprint,
    parse_dictionary,
    public_jwk,
    verify_directory_response,
    verify_signature_value,
)

from core.async_redis import get_async_redis
from core.config import grantex_base_url_for_env, settings
from core.security.egress import (
    EgressValidationError,
    build_pinned_async_transport,
    egress_dns_validation_required,
    validate_public_url_async,
)

logger = structlog.get_logger()

KEY_TTL_SECONDS = 300.0
NEGATIVE_TTL_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 5.0
MAX_KEY_RESPONSE_BYTES = 64 * 1024
MAX_DIRECTORY_BYTES = 256 * 1024
MAX_LOOKUP_BYTES = 256 * 1024
REGISTRY_DIRECTORY_TAG = "grantex-registry-directory"
REGISTRY_DIRECTORY_MAX_WINDOW_SECONDS = 3600
REGISTRY_CLOCK_SKEW_SECONDS = 60
SCHEME_AGENT_ID_SCHEMES = ("visa_tap", "mastercard_agent_pay")
NONCE_KEY_PREFIX = "httpsig:nonce:"
FETCH_BURST = 20
FETCH_RATE_PER_SECOND = 2.0
MAX_CONCURRENT_FETCHES = 4

TransportFactory = Callable[[], Any]
# Test seam: a factory returning an httpx transport. Never set from request input.
_transport_factory: TransportFactory | None = None


class KeyFetchError(RuntimeError):
    """A key source could not be read or did not verify."""


class FetchBudgetExceededError(KeyFetchError):
    """The per-process fetch budget is spent: nothing was fetched, and nothing is cached."""


class _FetchBudget:
    """A token bucket plus an in-flight cap. Event-loop code only (no awaits inside), so no lock."""

    def __init__(self, burst: int, rate_per_second: float, max_in_flight: int) -> None:
        self.burst = burst
        self.rate = rate_per_second
        self.max_in_flight = max_in_flight
        self.tokens = float(burst)
        self.in_flight = 0
        self.updated = time.monotonic()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        self.tokens = min(float(self.burst), self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.in_flight >= self.max_in_flight or self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        self.in_flight += 1
        return True

    def release(self) -> None:
        self.in_flight = max(0, self.in_flight - 1)


@dataclass(frozen=True)
class ResolvedKey:
    jwk: Mapping[str, Any]
    #: tap_key_store | signature_agent_directory | grantex_registry_directory
    source: str


@dataclass(frozen=True)
class RegistryAgent:
    agent_did: str | None
    scheme_agent_ids: tuple[Mapping[str, str], ...]


class _Cache:
    """A per-process TTL cache with a negative entry (``None``) for failures."""

    def __init__(self) -> None:
        self._entries: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> tuple[bool, Any]:
        hit = self._entries.get(key)
        if hit is None or hit[0] <= time.monotonic():
            return False, None
        return True, hit[1]

    def put(self, key: Any, value: Any) -> None:
        ttl = NEGATIVE_TTL_SECONDS if value is None else KEY_TTL_SECONDS
        if len(self._entries) > 4096:
            self._entries.clear()
        self._entries[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        self._entries.clear()


_TAP_KEYS = _Cache()
_DIRECTORIES = _Cache()
_REGISTRY = _Cache()
_BUDGET = _FetchBudget(FETCH_BURST, FETCH_RATE_PER_SECOND, MAX_CONCURRENT_FETCHES)


def reset_httpsig_caches() -> None:  # test hook
    global _BUDGET
    _TAP_KEYS.clear()
    _DIRECTORIES.clear()
    _REGISTRY.clear()
    _BUDGET = _FetchBudget(FETCH_BURST, FETCH_RATE_PER_SECOND, MAX_CONCURRENT_FETCHES)


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def origin_of(url: str) -> str | None:
    """``https://host[:port]`` (default port dropped, lower case), or None for anything but https."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"


def allowed_signature_agent_origins() -> frozenset[str]:
    return frozenset(o for o in (origin_of(v) for v in _csv(settings.httpsig_signature_agent_origins)) if o)


def tap_key_store_templates() -> list[str]:
    out = []
    for template in _csv(settings.httpsig_tap_key_store_urls):
        if "{keyid}" not in template or origin_of(template.replace("{keyid}", "k")) is None:
            logger.warning("httpsig_tap_key_store_url_ignored", reason="https URL with {keyid} required")
            continue
        out.append(template)
    return out


def grantex_origin() -> str | None:
    return origin_of(grantex_base_url_for_env())


async def _fetch(url: str, *, accept: str, max_bytes: int) -> httpx.Response:
    try:
        # Scheme, userinfo, IP-literal and local-name checks; DNS answers are
        # checked by the pinned transport on the event loop.
        await validate_public_url_async(url, require_dns=False)
    except EgressValidationError as exc:
        raise KeyFetchError(f"unsafe key source: {exc.reason}") from exc
    budget = _BUDGET
    if not budget.try_acquire():
        logger.warning("httpsig_key_fetch_budget_exceeded", in_flight=budget.in_flight)
        raise FetchBudgetExceededError("key fetch budget exceeded")
    transport = (
        _transport_factory()
        if _transport_factory is not None
        else build_pinned_async_transport(require_dns=egress_dns_validation_required())
    )
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS, transport=transport, follow_redirects=False
        ) as client:
            response = await client.get(url, headers={"Accept": accept})
    except (httpx.HTTPError, EgressValidationError, OSError) as exc:
        raise KeyFetchError(f"fetch failed: {type(exc).__name__}") from exc
    finally:
        budget.release()
    if len(response.content) > max_bytes:
        raise KeyFetchError("response exceeds the size limit")
    return response


def _usable_public_jwk(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, Mapping) or "d" in candidate:
        return None
    try:
        jwk = dict(public_jwk(candidate))
    except (AgentHttpSigError, KeyError, TypeError, ValueError):
        return None
    if isinstance(candidate.get("kid"), str):
        jwk["kid"] = candidate["kid"]
    return jwk


def _jwk_from_key_store(document: Any, keyid: str) -> dict[str, Any] | None:
    if isinstance(document, Mapping) and isinstance(document.get("keys"), list):
        for member in document["keys"]:
            if isinstance(member, Mapping) and member.get("kid") == keyid:
                return _usable_public_jwk(member)
        return None
    if isinstance(document, Mapping) and document.get("kid") in (None, keyid):
        return _usable_public_jwk(document)
    return None


async def resolve_tap_key(keyid: str) -> ResolvedKey | None:
    """The TAP key-store key for ``keyid`` (cached), or None."""
    templates = tap_key_store_templates()
    if not templates:
        return None
    found, cached = _TAP_KEYS.get(keyid)
    if found:
        return cached
    resolved: ResolvedKey | None = None
    for template in templates:
        url = template.replace("{keyid}", quote(keyid, safe=""))
        try:
            response = await _fetch(url, accept="application/json", max_bytes=MAX_KEY_RESPONSE_BYTES)
            if response.status_code != 200:
                continue
            jwk = _jwk_from_key_store(response.json(), keyid)
        except FetchBudgetExceededError:
            return None  # not cached: the key may resolve once the budget refills
        except (KeyFetchError, ValueError) as exc:
            logger.warning("httpsig_tap_key_fetch_failed", reason=str(exc)[:120])
            continue
        if jwk is not None:
            resolved = ResolvedKey(jwk, "tap_key_store")
            break
    _TAP_KEYS.put(keyid, resolved)
    return resolved


def _jwk_by_kid(jwks: Mapping[str, Any], kid: str) -> Mapping[str, Any] | None:
    for key in jwks.get("keys") or []:
        if isinstance(key, Mapping) and key.get("kid") == kid:
            return key
    return None


def _verify_rsa_pkcs1_sha256(jwk: Mapping[str, Any], base: str, signature: bytes) -> bool:
    """RFC 9421 section 3.3.2 (rsa-v1_5-sha256), the Grantex platform key's RS256 form."""
    try:
        n = int.from_bytes(base64.urlsafe_b64decode(str(jwk["n"]) + "=" * (-len(str(jwk["n"])) % 4)), "big")
        e = int.from_bytes(base64.urlsafe_b64decode(str(jwk["e"]) + "=" * (-len(str(jwk["e"])) % 4)), "big")
        key = rsa.RSAPublicNumbers(e, n).public_key()
    except (KeyError, TypeError, ValueError):
        return False
    if key.key_size < 2048:
        return False
    try:
        key.verify(signature, base.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        return False
    return True


def verify_registry_countersignature(
    response: HttpResponse, *, authority: str, jwks: Mapping[str, Any], now: int
) -> bool:
    """True when the Grantex registry's directory signature verifies against the platform JWKS.

    The registry covers ``"@authority";req`` and ``content-digest`` (RFC 9421
    sections 2.4 and RFC 9530), ``tag="grantex-registry-directory"``, with the
    platform kid and ``alg`` ``ecdsa-p256-sha256`` or ``rsa-v1_5-sha256``.
    """
    headers = response.headers
    pairs = list(headers.items()) if isinstance(headers, Mapping) else list(headers)

    def field(name: str) -> str | None:
        values = [str(v) for k, v in pairs if str(k).lower() == name]
        return ", ".join(values) if values else None

    body = response.body if isinstance(response.body, bytes) else str(response.body or "").encode("utf-8")
    digest_field = field("content-digest")
    input_field, signature_field = field("signature-input"), field("signature")
    if digest_field is None or input_field is None or signature_field is None:
        return False
    try:
        digest = parse_dictionary(digest_field).get("sha-256")
        inputs = parse_dictionary(input_field)
        signatures = parse_dictionary(signature_field)
    except AgentHttpSigError:
        return False
    if not isinstance(digest, Item) or not isinstance(digest.value, bytes):
        return False
    if not hmac.compare_digest(digest.value, hashlib.sha256(body).digest()):
        return False
    request = HttpRequest("GET", "https://" + authority + SIGNATURE_DIRECTORY_PATH)
    for label, member in inputs.items():
        if not isinstance(member, InnerList) or member.params.get("tag") != REGISTRY_DIRECTORY_TAG:
            continue
        covered = [str(c.value) + (";req" if "req" in c.params else "") for c in member.items]
        if covered != ["@authority;req", "content-digest"]:
            continue
        p = member.params
        created, expires, kid, alg = p.get("created"), p.get("expires"), p.get("keyid"), p.get("alg")
        if type(created) is not int or type(expires) is not int or not isinstance(kid, str):
            continue
        if expires <= created or expires - created > REGISTRY_DIRECTORY_MAX_WINDOW_SECONDS:
            continue
        if created > now + REGISTRY_CLOCK_SKEW_SECONDS or now >= expires + REGISTRY_CLOCK_SKEW_SECONDS:
            continue
        signature = signatures.get(label)
        jwk = _jwk_by_kid(jwks, kid)
        if jwk is None or not isinstance(signature, Item) or not isinstance(signature.value, bytes):
            continue
        try:
            base = create_signature_base(response, member, None, request)
        except AgentHttpSigError:
            continue
        if alg == "ecdsa-p256-sha256" and jwk.get("kty") == "EC":
            public = {k: jwk[k] for k in ("kty", "crv", "x", "y") if k in jwk}
            if verify_signature_value(public, base, signature.value):
                return True
        elif alg == "rsa-v1_5-sha256" and jwk.get("kty") == "RSA":
            if _verify_rsa_pkcs1_sha256(jwk, base, signature.value):
                return True
    return False


async def _grantex_platform_jwks() -> Mapping[str, Any]:
    from core.commerce.oacp_grantex_trust import OacpTrustUnavailableError, default_oacp_grantex_verifier

    try:
        return await default_oacp_grantex_verifier().jwks()
    except OacpTrustUnavailableError as exc:
        raise KeyFetchError(f"Grantex JWKS unavailable: {exc}") from exc


def _directory_keys(document: Any) -> dict[str, dict[str, Any]]:
    keys: dict[str, dict[str, Any]] = {}
    candidates = document.get("keys") if isinstance(document, Mapping) else None
    for candidate in candidates if isinstance(candidates, list) else []:
        jwk = _usable_public_jwk(candidate)
        if jwk is None:
            continue
        try:
            thumbprint = jwk_thumbprint(jwk)
        except (AgentHttpSigError, TypeError, ValueError):
            continue
        # The directory draft: a kid, when present, is the thumbprint.
        if jwk.get("kid") not in (None, thumbprint):
            continue
        jwk["kid"] = thumbprint
        keys[thumbprint] = jwk
    return keys


async def _load_directory(origin: str, now: int) -> tuple[dict[str, dict[str, Any]], str] | None:
    url = origin + SIGNATURE_DIRECTORY_PATH
    response = await _fetch(url, accept=SIGNATURE_DIRECTORY_MEDIA_TYPE, max_bytes=MAX_DIRECTORY_BYTES)
    if response.status_code != 200:
        raise KeyFetchError(f"directory answered HTTP {response.status_code}")
    message = HttpResponse(response.status_code, list(response.headers.multi_items()), response.content)
    authority = origin.removeprefix("https://")
    if origin == grantex_origin():
        jwks = await _grantex_platform_jwks()
        if verify_registry_countersignature(message, authority=authority, jwks=jwks, now=now):
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type != SIGNATURE_DIRECTORY_MEDIA_TYPE:
                raise KeyFetchError("the directory media type is " + SIGNATURE_DIRECTORY_MEDIA_TYPE)
            try:
                document = json.loads(response.content.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise KeyFetchError("the directory is not JSON") from exc
            return _directory_keys(document), "grantex_registry_directory"
    try:
        verified = verify_directory_response(message, authority=authority, now=now)
    except AgentHttpSigError as exc:
        raise KeyFetchError(str(exc)) from exc
    return {key["kid"]: dict(key) for key in verified.keys}, "signature_agent_directory"


async def resolve_signature_agent_key(
    signature_agent_url: str, agent_type: str, keyid: str, *, now: int
) -> ResolvedKey | None:
    """The key named by (Signature-Agent URL, keyid), from an allow-listed https directory, or None."""
    if agent_type != "directory":
        # jwks_uri and cimd members are recognised by the library but no key
        # source is configured for them here: such a request is key_unknown.
        return None
    origin = origin_of(signature_agent_url)
    if origin is None or origin not in allowed_signature_agent_origins():
        return None
    found, cached = _DIRECTORIES.get(origin)
    if not found:
        try:
            cached = await _load_directory(origin, now)
        except FetchBudgetExceededError:
            return None  # not cached
        except KeyFetchError as exc:
            logger.warning("httpsig_directory_fetch_failed", origin=origin, reason=str(exc)[:120])
            cached = None
        _DIRECTORIES.put(origin, cached)
    if cached is None:
        return None
    keys, source = cached
    jwk = keys.get(keyid)
    return ResolvedKey(jwk, source) if jwk is not None else None


async def claim_nonce(scope: str, nonce: str, until: int, *, now: int) -> bool | None:
    """Atomically record (scope, nonce) until ``until``: True first time, False replay, None store unavailable."""
    from redis.exceptions import RedisError

    client = await get_async_redis()
    if client is None:
        return None
    key = NONCE_KEY_PREFIX + hashlib.sha256(f"{scope}\n{nonce}".encode()).hexdigest()
    try:
        stored = await client.set(key, "1", nx=True, ex=max(1, int(until) - int(now)))
    except (RedisError, OSError, TimeoutError):
        return None
    return bool(stored)


def _scheme_ids(answer: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    seen: list[Mapping[str, str]] = []
    for attestation in answer.get("attestations") or []:
        if not isinstance(attestation, Mapping):
            continue
        for entry in attestation.get("scheme_agent_ids") or []:
            if (
                isinstance(entry, Mapping)
                and entry.get("scheme") in SCHEME_AGENT_ID_SCHEMES
                and isinstance(entry.get("agent_id"), str)
                and 0 < len(entry["agent_id"]) <= 256
            ):
                item = {"scheme": str(entry["scheme"]), "agent_id": str(entry["agent_id"])}
                if item not in seen:
                    seen.append(item)
    return tuple(seen)


async def lookup_registry_agent(query: Sequence[tuple[str, str]]) -> RegistryAgent | None:
    """Grantex ``GET /v1/registry/agents`` (by key thumbprint, or by scheme and scheme agent id), cached.

    The unauthenticated, minimised form (the registry answers it while
    ``REGISTRY_PUBLIC_ENDPOINTS_ENABLED`` is on); every failure is None.
    """
    base = origin_of(grantex_base_url_for_env())
    if base is None:
        return None
    cache_key = tuple(query)
    found, cached = _REGISTRY.get(cache_key)
    if found:
        return cached
    url = base + "/v1/registry/agents?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in query)
    agent: RegistryAgent | None = None
    try:
        response = await _fetch(url, accept="application/json", max_bytes=MAX_LOOKUP_BYTES)
        if response.status_code == 200:
            answer = response.json()
            if isinstance(answer, Mapping):
                did = answer.get("agent_did")
                agent = RegistryAgent(did if isinstance(did, str) else None, _scheme_ids(answer))
    except FetchBudgetExceededError:
        return None  # not cached
    except (KeyFetchError, ValueError) as exc:
        logger.warning("httpsig_registry_lookup_failed", reason=str(exc)[:120])
    _REGISTRY.put(cache_key, agent)
    return agent
