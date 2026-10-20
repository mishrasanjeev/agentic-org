# SPDX-License-Identifier: Apache-2.0
"""Verify Grantex-issued OACP artifacts before AgenticOrg caches them.

Trust is pinned in configuration, never taken from an artifact:

- the issuer is ``grantex_issuer_for_env()`` (``AGENTICORG_GRANTEX_ISSUER`` or
  the issuer derived from ``GRANTEX_BASE_URL``), and an envelope naming any
  other issuer is refused;
- the JWKS is fetched from ``grantex_jwks_uri_for_env()`` and cached per
  process: 300 s for a good answer, 60 s negative cache for a failed fetch
  or an unknown kid, 5 s fetch timeout (the policy of
  ``auth/grantex_middleware.py``), fetched with httpx's async client over the
  DNS-pinning egress transport so no request handler blocks;
- the Token Status List named by ``envelope.status.status_list`` (signed into
  the envelope) is fetched only when its origin is the issuer origin or the
  Grantex API origin, verified as a ``statuslist+jwt`` against the same JWKS,
  cached no longer than its ``ttl`` and ``exp``, and an unreachable or invalid
  list refuses the artifact (``revocation_status_unavailable``).

Verification order follows the cross-repo OACP contract: steps 1-7 in
``core.commerce.oacp_artifacts.verify_oacp_artifact`` (detached JWS first),
step 8 (status list) here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from core.commerce.oacp_artifacts import (
    OacpArtifactScope,
    OacpArtifactVerificationInput,
    verify_oacp_artifact,
)
from core.commerce.oacp_jws import (
    OACP_JWS_PROFILE,
    STATUS_LIST_JWT_TYP,
    OacpJwsError,
    token_status_at,
    verify_compact_jwt,
)
from core.config import grantex_base_url_for_env, grantex_issuer_for_env, grantex_jwks_uri_for_env
from core.security.egress import (
    EgressValidationError,
    build_pinned_async_transport,
    validate_public_url,
)

logger = structlog.get_logger()

JWKS_TTL_SECONDS = 300.0
JWKS_NEGATIVE_TTL_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 5.0
STATUS_LIST_MAX_CACHE_SECONDS = 600.0
STATUS_LIST_NEGATIVE_TTL_SECONDS = 30.0
STATUS_LIST_CLOCK_SKEW_SECONDS = 60
_MAX_JWKS_BYTES = 256 * 1024
_MAX_STATUS_LIST_BYTES = 4 * 1024 * 1024

REVOCATION_STATUS_UNAVAILABLE = "revocation_status_unavailable"
ARTIFACT_REVOKED = "artifact_revoked"
ARTIFACT_SUSPENDED = "artifact_suspended"
ISSUER_KEY_UNTRUSTED = "issuer_key_untrusted"

# draft-ietf-oauth-status-list section 7.1 status types.
_STATUS_VALID = 0
_STATUS_INVALID = 1
_STATUS_SUSPENDED = 2

TransportFactory = Callable[[], httpx.AsyncBaseTransport | Any]


class OacpTrustUnavailableError(RuntimeError):
    """A pinned trust input (JWKS or status list) could not be obtained."""


def _origin(url: str) -> str | None:
    parts = urlsplit(str(url or "").strip())
    if not parts.scheme or not parts.hostname:
        return None
    host = parts.hostname.lower()
    default_port = {"https": 443, "http": 80}.get(parts.scheme.lower())
    port = parts.port
    netloc = host if port is None or port == default_port else f"{host}:{port}"
    return f"{parts.scheme.lower()}://{netloc}"


@dataclass(frozen=True)
class OacpGrantexTrustConfig:
    issuer: str
    jwks_uri: str
    status_list_origins: frozenset[str]
    # Local conformance runs only: allow http and loopback hosts for the
    # pinned issuer. Never set from request input.
    allow_local_endpoints: bool = False


def oacp_grantex_trust_config_from_settings() -> OacpGrantexTrustConfig:
    issuer = grantex_issuer_for_env()
    jwks_uri = grantex_jwks_uri_for_env()
    origins = {origin for origin in (_origin(issuer), _origin(grantex_base_url_for_env())) if origin}
    return OacpGrantexTrustConfig(issuer=issuer, jwks_uri=jwks_uri, status_list_origins=frozenset(origins))


@dataclass
class _TrustCaches:
    jwks: dict[str, tuple[float, Mapping[str, Any]]] = field(default_factory=dict)
    jwks_negative: dict[str, float] = field(default_factory=dict)
    kid_negative: dict[tuple[str, str], float] = field(default_factory=dict)
    status_lists: dict[str, tuple[float, Mapping[str, Any]]] = field(default_factory=dict)
    status_negative: dict[str, float] = field(default_factory=dict)

    def clear(self) -> None:
        self.jwks.clear()
        self.jwks_negative.clear()
        self.kid_negative.clear()
        self.status_lists.clear()
        self.status_negative.clear()


# Process-level caches shared by every request. Plain dict operations on the
# event loop thread need no lock; two concurrent misses fetch twice.
_CACHES = _TrustCaches()


def reset_oacp_trust_caches() -> None:  # test hook
    _CACHES.clear()


def _epoch(now_iso: str) -> float | None:
    value = now_iso[:-1] + "+00:00" if now_iso.endswith("Z") else now_iso
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class OacpGrantexArtifactVerifier:
    """Verifies Grantex OACP artifacts against the pinned issuer, JWKS and status lists."""

    def __init__(
        self,
        config: OacpGrantexTrustConfig,
        *,
        transport_factory: TransportFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._transport_factory = transport_factory
        self._clock = clock

    @property
    def config(self) -> OacpGrantexTrustConfig:
        return self._config

    async def _get(self, url: str, *, accept: str, max_bytes: int) -> httpx.Response:
        if not self._config.allow_local_endpoints:
            try:
                # Scheme, userinfo, IP-literal and local-name checks. DNS answers
                # are validated asynchronously by the pinned transport.
                validate_public_url(url, require_dns=False)
            except EgressValidationError as exc:
                raise OacpTrustUnavailableError(f"unsafe trust endpoint: {exc}") from exc
        transport = (
            self._transport_factory()
            if self._transport_factory is not None
            else build_pinned_async_transport(require_dns=True)
        )
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT_SECONDS,
                transport=transport,
                follow_redirects=False,
            ) as client:
                response = await client.get(url, headers={"Accept": accept})
        except (httpx.HTTPError, EgressValidationError, OSError) as exc:
            raise OacpTrustUnavailableError(f"fetch failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise OacpTrustUnavailableError(f"fetch returned HTTP {response.status_code}")
        if len(response.content) > max_bytes:
            raise OacpTrustUnavailableError("response exceeds the size limit")
        return response

    async def jwks(self, *, force_refresh: bool = False) -> Mapping[str, Any]:
        uri = self._config.jwks_uri
        if not uri:
            raise OacpTrustUnavailableError("Grantex JWKS URI is not configured")
        now = self._clock()
        if not force_refresh:
            hit = _CACHES.jwks.get(uri)
            if hit is not None and hit[0] > now:
                return hit[1]
            negative_until = _CACHES.jwks_negative.get(uri)
            if negative_until is not None and negative_until > now:
                raise OacpTrustUnavailableError("Grantex JWKS unavailable (negative cache)")
        try:
            response = await self._get(uri, accept="application/json", max_bytes=_MAX_JWKS_BYTES)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
                raise OacpTrustUnavailableError("JWKS is not a key set")
        except (OacpTrustUnavailableError, ValueError) as exc:
            _CACHES.jwks_negative[uri] = self._clock() + JWKS_NEGATIVE_TTL_SECONDS
            raise OacpTrustUnavailableError(str(exc)) from exc
        _CACHES.jwks[uri] = (self._clock() + JWKS_TTL_SECONDS, body)
        _CACHES.jwks_negative.pop(uri, None)
        return body

    async def verify(
        self,
        envelope: Mapping[str, Any],
        payload: Any,
        *,
        expected_scope: OacpArtifactScope,
        now_iso: str,
    ) -> dict[str, Any]:
        """Run steps 1-8 for one artifact and return a valid result or a refusal."""
        artifact_id = envelope.get("artifact_id") if isinstance(envelope, Mapping) else None
        try:
            jwks = await self.jwks()
        except OacpTrustUnavailableError as exc:
            return _refusal(artifact_id, ISSUER_KEY_UNTRUSTED, "Grantex JWKS is unavailable.", detail=str(exc))

        result = self._verify_steps_1_to_7(envelope, payload, jwks, expected_scope, now_iso)
        kid = result.get("issuer_key_id")
        if result.get("kid_not_found") is True and isinstance(kid, str):
            # A key rotated in after the JWKS was cached: refresh once, then
            # negatively cache the kid so a forged kid cannot force fetches.
            negative_key = (self._config.jwks_uri, kid)
            negative_until = _CACHES.kid_negative.get(negative_key)
            if negative_until is None or negative_until <= self._clock():
                try:
                    jwks = await self.jwks(force_refresh=True)
                except OacpTrustUnavailableError as exc:
                    return _refusal(artifact_id, ISSUER_KEY_UNTRUSTED, "Grantex JWKS is unavailable.", detail=str(exc))
                result = self._verify_steps_1_to_7(envelope, payload, jwks, expected_scope, now_iso)
                if result.get("kid_not_found") is True:
                    _CACHES.kid_negative[negative_key] = self._clock() + JWKS_NEGATIVE_TTL_SECONDS
        if result.get("valid") is not True:
            result.pop("kid_not_found", None)
            return result

        status_refusal = await self._check_status(envelope, jwks, now_iso)
        if status_refusal is not None:
            return {**_refusal(artifact_id, status_refusal[0], status_refusal[1]), "issuer_key_id": kid}
        result["status_checked"] = True
        result["verifier_result_ref"] = build_verifier_result_ref(
            issuer_key_id=str(kid),
            payload_hash=str(result["payload_hash"]),
            verified_at=now_iso,
        )
        return result

    def _verify_steps_1_to_7(
        self,
        envelope: Mapping[str, Any],
        payload: Any,
        jwks: Mapping[str, Any],
        expected_scope: OacpArtifactScope,
        now_iso: str,
    ) -> dict[str, Any]:
        return verify_oacp_artifact(
            OacpArtifactVerificationInput(
                envelope=dict(envelope) if isinstance(envelope, Mapping) else {},
                payload=payload,
                jwks=jwks,
                expected_issuer=self._config.issuer,
                now_iso=now_iso,
                expected_scope=expected_scope,
            )
        )

    async def _check_status(
        self,
        envelope: Mapping[str, Any],
        jwks: Mapping[str, Any],
        now_iso: str,
    ) -> tuple[str, str] | None:
        status = envelope.get("status")
        status_list = status.get("status_list") if isinstance(status, Mapping) else None
        if not isinstance(status_list, Mapping):
            return REVOCATION_STATUS_UNAVAILABLE, "Artifact carries no status list reference."
        uri = status_list.get("uri")
        idx = status_list.get("idx")
        if not isinstance(uri, str) or isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            return REVOCATION_STATUS_UNAVAILABLE, "Artifact status list reference is malformed."
        if envelope.get("revocation_status_url") != uri:
            return REVOCATION_STATUS_UNAVAILABLE, "revocation_status_url does not match the status list."
        origin = _origin(uri)
        if origin is None or origin not in self._config.status_list_origins:
            return REVOCATION_STATUS_UNAVAILABLE, "Status list is not served by the pinned Grantex issuer."
        if not self._config.allow_local_endpoints and not uri.startswith("https://"):
            return REVOCATION_STATUS_UNAVAILABLE, "Status list URI is not https."
        now_epoch = _epoch(now_iso)
        if now_epoch is None:
            return REVOCATION_STATUS_UNAVAILABLE, "Verification time is invalid."
        try:
            claims = await self._status_list_claims(uri, jwks, now_epoch)
            value = token_status_at(claims["status_list"], idx)
        except (OacpTrustUnavailableError, OacpJwsError, KeyError, TypeError) as exc:
            logger.warning("oacp_status_list_unavailable", uri=uri, reason=str(exc))
            return REVOCATION_STATUS_UNAVAILABLE, "Status list is unavailable or invalid."
        if value == _STATUS_VALID:
            return None
        if value == _STATUS_INVALID:
            return ARTIFACT_REVOKED, "Artifact is revoked on the issuer status list."
        if value == _STATUS_SUSPENDED:
            return ARTIFACT_SUSPENDED, "Artifact is suspended on the issuer status list."
        return REVOCATION_STATUS_UNAVAILABLE, "Status list holds an unknown status value."

    async def _status_list_claims(self, uri: str, jwks: Mapping[str, Any], now_epoch: float) -> Mapping[str, Any]:
        now = self._clock()
        hit = _CACHES.status_lists.get(uri)
        if hit is not None and hit[0] > now:
            claims = hit[1]
            exp = claims.get("exp")
            if isinstance(exp, int | float) and exp > now_epoch:
                return claims
        negative_until = _CACHES.status_negative.get(uri)
        if negative_until is not None and negative_until > now:
            raise OacpTrustUnavailableError("status list unavailable (negative cache)")
        try:
            response = await self._get(
                uri,
                accept="application/statuslist+jwt",
                max_bytes=_MAX_STATUS_LIST_BYTES,
            )
            claims = verify_compact_jwt(response.text, jwks, expected_typ=STATUS_LIST_JWT_TYP)
            _validate_status_list_claims(claims, uri, now_epoch)
        except (OacpTrustUnavailableError, OacpJwsError) as exc:
            _CACHES.status_negative[uri] = self._clock() + STATUS_LIST_NEGATIVE_TTL_SECONDS
            raise OacpTrustUnavailableError(str(exc)) from exc
        lifetime = min(float(claims["exp"]) - now_epoch, STATUS_LIST_MAX_CACHE_SECONDS)
        ttl = claims.get("ttl")
        if isinstance(ttl, int | float) and not isinstance(ttl, bool) and ttl > 0:
            lifetime = min(lifetime, float(ttl))
        _CACHES.status_lists[uri] = (self._clock() + max(lifetime, 0.0), claims)
        _CACHES.status_negative.pop(uri, None)
        return claims


def _validate_status_list_claims(claims: Mapping[str, Any], uri: str, now_epoch: float) -> None:
    """draft-ietf-oauth-status-list section 5.1 claims of a referenced status list."""
    if claims.get("sub") != uri:
        raise OacpJwsError("status list sub does not match the referenced uri")
    exp = claims.get("exp")
    iat = claims.get("iat")
    if isinstance(exp, bool) or not isinstance(exp, int | float) or exp <= now_epoch:
        raise OacpJwsError("status list is expired or has no exp")
    if isinstance(iat, bool) or not isinstance(iat, int | float) or iat > now_epoch + STATUS_LIST_CLOCK_SKEW_SECONDS:
        raise OacpJwsError("status list iat is missing or in the future")
    if not isinstance(claims.get("status_list"), Mapping):
        raise OacpJwsError("status list claim is missing")


def build_verifier_result_ref(*, issuer_key_id: str, payload_hash: str, verified_at: str) -> str:
    """The cache record's ``verifier_result_ref``: what was verified, with which key, when."""
    return f"{OACP_JWS_PROFILE};kid={issuer_key_id};payload_hash={payload_hash};status=valid;verified_at={verified_at}"


def _refusal(artifact_id: Any, code: str, message: str, *, detail: str | None = None) -> dict[str, Any]:
    refusal: dict[str, Any] = {
        "valid": False,
        "status": "refused",
        "artifact_id": artifact_id if isinstance(artifact_id, str) else None,
        "refusal_code": code,
        "message": message,
    }
    if detail:
        refusal["detail"] = detail
    return refusal


def split_grantex_artifact(artifact: Mapping[str, Any]) -> tuple[Mapping[str, Any], Any]:
    """Return ``(envelope, payload)`` from a Grantex issuance item ``{envelope, payload}``."""
    envelope = artifact.get("envelope")
    if not isinstance(envelope, Mapping):
        envelope = artifact
    payload = envelope.get("artifact_payload") or artifact.get("artifact_payload") or artifact.get("payload") or {}
    return envelope, payload


def default_oacp_grantex_verifier() -> OacpGrantexArtifactVerifier:
    return OacpGrantexArtifactVerifier(oacp_grantex_trust_config_from_settings())
