"""OpenID Connect provider implementation.

Uses httpx for HTTP (already a dependency) and authlib for JWT/JWS
verification. Supports the Authorization Code flow with PKCE, which is
the modern recommendation for web apps.

Flow:
  1. /api/v1/auth/sso/{provider_key}/login
     → We build an authorization URL, set state+nonce+PKCE verifier in
       a short-lived Redis key, and redirect the user's browser to the IdP.
  2. IdP authenticates the user and redirects back to our callback.
  3. /api/v1/auth/sso/{provider_key}/callback?code=...&state=...
     → We verify state, exchange the code for tokens, verify the ID token,
       JIT-provision the user if needed, and issue our own session JWT.

Only metadata discovery endpoints are fetched at runtime — no IdP SDK.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from authlib.jose import JsonWebKey, jwt

from core.http_retry import retry_http_async
from core.security.egress import (
    EgressValidationError,
    build_pinned_async_transport,
    validate_public_url,
)

logger = structlog.get_logger()


@dataclass
class OIDCTokens:
    access_token: str
    id_token: str
    refresh_token: str | None
    claims: dict[str, Any]


class OIDCProvider:
    """A single tenant's OIDC provider configuration.

    Constructed from an SSOConfig row's ``config`` JSON:
        {
          "issuer": "https://login.microsoftonline.com/<tid>/v2.0",
          "client_id": "...",
          "client_secret_ref": "secret://path/to/client_secret",
          "scopes": ["openid", "profile", "email"],
          "redirect_uri": "https://app.agenticorg.ai/api/v1/auth/sso/azure/callback"
        }
    """

    def __init__(self, provider_key: str, config: dict[str, Any]) -> None:
        self.provider_key = provider_key
        self.issuer = self._validate_issuer(config["issuer"])
        self._issuer_host = urlparse(self.issuer).hostname or ""
        self.client_id = config["client_id"]
        self.client_secret = config.get("client_secret", "")
        self.redirect_uri = config["redirect_uri"]
        self.scopes = config.get("scopes") or ["openid", "profile", "email"]
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0

    @retry_http_async(max_attempts=3)
    async def _discover(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        url = f"{self.issuer}/.well-known/openid-configuration"
        self._validate_provider_endpoint(url, "discovery_url")
        async with httpx.AsyncClient(
            timeout=10,
            transport=build_pinned_async_transport(require_dns=True),
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            self._discovery = resp.json()
        self._validate_discovery_document(self._discovery)
        return self._discovery

    @retry_http_async(max_attempts=3)
    async def _get_jwks(self) -> dict[str, Any]:
        # Cache JWKS for 10 minutes to avoid hammering the IdP.
        if self._jwks is not None and time.time() - self._jwks_fetched_at < 600:
            return self._jwks
        disc = await self._discover()
        self._validate_provider_endpoint(str(disc["jwks_uri"]), "jwks_uri")
        async with httpx.AsyncClient(
            timeout=10,
            transport=build_pinned_async_transport(require_dns=True),
        ) as client:
            resp = await client.get(disc["jwks_uri"])
            resp.raise_for_status()
            self._jwks = resp.json()
            self._jwks_fetched_at = time.time()
        return self._jwks

    def build_authorize_url(
        self, state: str, nonce: str, code_challenge: str
    ) -> str:
        """Return the IdP URL the user's browser should redirect to."""
        # We use the issuer's discovery in practice; at authorize time we
        # need the authorization_endpoint synchronously. Callers should
        # await .prepare() first.
        disc = self._discovery or {}
        base = disc.get("authorization_endpoint") or f"{self.issuer}/authorize"
        self._validate_provider_endpoint(str(base), "authorization_endpoint", require_dns=False)
        import urllib.parse

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{base}?{urllib.parse.urlencode(params)}"

    async def prepare(self) -> None:
        """Force discovery + JWKS fetch so build_authorize_url is sync."""
        await self._discover()
        await self._get_jwks()

    @retry_http_async(max_attempts=3)
    async def exchange_code(
        self, code: str, code_verifier: str, expected_nonce: str
    ) -> OIDCTokens:
        """Exchange the authorization code for tokens + verify ID token."""
        disc = await self._discover()
        token_endpoint = disc["token_endpoint"]
        self._validate_provider_endpoint(str(token_endpoint), "token_endpoint")

        async with httpx.AsyncClient(
            timeout=15,
            transport=build_pinned_async_transport(require_dns=True),
        ) as client:
            resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            body = resp.json()

        id_token = body.get("id_token", "")
        if not id_token:
            raise ValueError("OIDC response missing id_token")

        claims = await self._verify_id_token(id_token, expected_nonce)

        return OIDCTokens(
            access_token=body.get("access_token", ""),
            id_token=id_token,
            refresh_token=body.get("refresh_token"),
            claims=claims,
        )

    async def _verify_id_token(
        self, id_token: str, expected_nonce: str
    ) -> dict[str, Any]:
        """Verify ID token signature, issuer, audience, nonce, expiry."""
        jwks = await self._get_jwks()
        key = JsonWebKey.import_key_set(jwks)

        claims_options = {
            "iss": {"essential": True, "values": [self.issuer]},
            "aud": {"essential": True, "values": [self.client_id]},
            "exp": {"essential": True},
        }
        claims = jwt.decode(id_token, key, claims_options=claims_options)
        claims.validate()

        # Check nonce to prevent replay
        if claims.get("nonce") != expected_nonce:
            raise ValueError("OIDC nonce mismatch — possible replay attack")

        return dict(claims)

    def _validate_issuer(self, issuer: str) -> str:
        value = str(issuer or "").strip().rstrip("/")
        try:
            validate_public_url(value, allowed_schemes=("https",), require_dns=False)
        except EgressValidationError as exc:
            raise ValueError("OIDC issuer must be an HTTPS public URL") from exc
        return value

    def _validate_provider_endpoint(
        self,
        url: str,
        field: str,
        *,
        require_dns: bool | None = None,
    ) -> str:
        try:
            validate_public_url(
                str(url or "").strip(),
                allowed_schemes=("https",),
                require_dns=require_dns,
                allowed_hosts=(self._issuer_host,),
            )
        except EgressValidationError as exc:
            raise ValueError(f"OIDC {field} must stay on the configured issuer host") from exc
        return str(url)

    def _validate_discovery_document(self, discovery: dict[str, Any]) -> None:
        discovered_issuer = str(discovery.get("issuer") or "").rstrip("/")
        if discovered_issuer != self.issuer:
            raise ValueError("OIDC discovery issuer mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            self._validate_provider_endpoint(str(discovery.get(field) or ""), field, require_dns=False)


# ── PKCE helpers ──────────────────────────────────────────────────


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    return secrets.token_urlsafe(32)
