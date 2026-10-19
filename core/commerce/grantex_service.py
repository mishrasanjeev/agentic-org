# SPDX-License-Identifier: Apache-2.0
"""The AgenticOrg service client for the Grantex routes that countersign AgenticOrg evidence.

Two callers: the commerce arbiter (``core.commerce.arbiter_grantex``, ``POST
/v1/commerce/oacp/arbiter-decisions``) and the audit chain witness
(``core.governance.audit_witness``, ``POST /v1/audit/witness-checkpoints``).
Both authenticate with the AgenticOrg service token Grantex already accepts for
the C6Z authority route (``GRANTEX_COMMERCE_BASE_URL`` and
``GRANTEX_COMMERCE_INTERNAL_TOKEN``; the token is never logged).

Every call is async over the DNS-pinning egress transport, validated with the
async egress validator, bounded by a 5 s timeout, never retried and never
follows a redirect: a caller that cannot reach Grantex gets
``GrantexServiceUnavailableError`` at once and decides what that means.
Answers are verified against the Grantex JWKS pinned in configuration (the
Phase 1 OACP trust fetcher and its caches, ``core.commerce.oacp_grantex_trust``).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from core.commerce.oacp_grantex_trust import (
    OacpGrantexArtifactVerifier,
    OacpTrustUnavailableError,
    default_oacp_grantex_verifier,
)
from core.commerce.oacp_jws import OacpJwsError, verify_compact_jwt
from core.security.egress import EgressValidationError, build_pinned_async_transport, validate_public_url_async

TIMEOUT_SECONDS = 5.0
_MAX_RESPONSE_BYTES = 256 * 1024
BASE_URL_ENV = "GRANTEX_COMMERCE_BASE_URL"
TOKEN_ENV = "GRANTEX_COMMERCE_INTERNAL_TOKEN"

TransportFactory = Callable[[], httpx.AsyncBaseTransport | Any]


class GrantexServiceUnavailableError(RuntimeError):
    """Grantex could not be reached or answered with something unusable."""


class GrantexTokenError(ValueError):
    """A Grantex token or countersignature does not verify against the pinned JWKS."""


@dataclass(frozen=True)
class GrantexServiceResponse:
    status_code: int
    body: dict[str, Any]


class GrantexServiceClient:
    """Posts AgenticOrg evidence to Grantex and verifies what comes back."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verifier: OacpGrantexArtifactVerifier,
        transport_factory: TransportFactory | None = None,
        allow_local_endpoints: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._verifier = verifier
        self._transport_factory = transport_factory
        # Local integration runs only (a Grantex on localhost); never set from request input.
        self._allow_local_endpoints = allow_local_endpoints

    @property
    def issuer(self) -> str:
        return self._verifier.config.issuer

    async def post_json(self, path: str, body: Mapping[str, Any]) -> GrantexServiceResponse:
        url = f"{self._base_url}{path}"
        if not self._allow_local_endpoints:
            try:
                await validate_public_url_async(url, require_dns=False)
            except EgressValidationError as exc:
                raise GrantexServiceUnavailableError("unsafe Grantex endpoint") from exc
        transport = (
            self._transport_factory()
            if self._transport_factory is not None
            else build_pinned_async_transport(require_dns=not self._allow_local_endpoints)
        )
        try:
            client_options = {"timeout": TIMEOUT_SECONDS, "transport": transport, "follow_redirects": False}
            async with httpx.AsyncClient(**client_options) as client:
                response = await client.post(
                    url,
                    json=dict(body),
                    headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                )
        except (httpx.HTTPError, EgressValidationError, OSError) as exc:
            raise GrantexServiceUnavailableError(f"Grantex unreachable: {type(exc).__name__}") from exc
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise GrantexServiceUnavailableError("Grantex response exceeds the size limit")
        try:
            parsed = response.json()
        except ValueError as exc:
            raise GrantexServiceUnavailableError(f"Grantex answered HTTP {response.status_code} without JSON") from exc
        if not isinstance(parsed, dict):
            raise GrantexServiceUnavailableError(f"Grantex answered HTTP {response.status_code} with a non-object")
        return GrantexServiceResponse(status_code=response.status_code, body=parsed)

    async def verify_token(self, token: Any, *, typ: str) -> dict[str, Any]:
        """Verify a compact JWS from Grantex against the pinned JWKS (one refresh for an unknown kid)."""
        if not isinstance(token, str) or not token or len(token) > 16_384:
            raise GrantexTokenError("token missing or oversized")
        try:
            jwks = await self._verifier.jwks()
        except OacpTrustUnavailableError as exc:
            raise GrantexServiceUnavailableError("Grantex JWKS unavailable") from exc
        try:
            return verify_compact_jwt(token, jwks, expected_typ=typ)
        except OacpJwsError as exc:
            if "kid" not in str(exc):
                raise GrantexTokenError(str(exc)) from exc
        try:
            jwks = await self._verifier.jwks(force_refresh=True)
        except OacpTrustUnavailableError as exc:
            raise GrantexServiceUnavailableError("Grantex JWKS unavailable") from exc
        try:
            return verify_compact_jwt(token, jwks, expected_typ=typ)
        except OacpJwsError as exc:
            raise GrantexTokenError(str(exc)) from exc

    async def jwks(self) -> Mapping[str, Any] | None:
        """The pinned JWKS if it can be had from cache or one fetch, else None (for export bundles)."""
        try:
            return await self._verifier.jwks()
        except OacpTrustUnavailableError:
            return None


def grantex_service_client_from_env(env: Mapping[str, str] | None = None) -> GrantexServiceClient | None:
    """The client configured by ``GRANTEX_COMMERCE_BASE_URL`` and ``GRANTEX_COMMERCE_INTERNAL_TOKEN``, or None."""
    source = os.environ if env is None else env
    base_url = str(source.get(BASE_URL_ENV) or "").strip()
    token = str(source.get(TOKEN_ENV) or "").strip()
    if not base_url or not token:
        return None
    return GrantexServiceClient(base_url=base_url, token=token, verifier=default_oacp_grantex_verifier())
