"""JWT validation using RS256 with JWKS support + local HS256 signing."""

from __future__ import annotations

import time
from typing import Any

import httpx
from jose import JWTError, jwt

from core.config import settings

_jwks_cache: dict[str, Any] = {}
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600


# ---------------------------------------------------------------------------
# Local HS256 token helpers
# ---------------------------------------------------------------------------


def create_access_token(data: dict, expires_minutes: int = 60) -> str:
    """Create an HS256-signed JWT for local authentication."""
    now = int(time.time())
    payload = {
        **data,
        "iss": "agenticorg-local",
        "aud": "agenticorg-tool-gateway",
        "iat": now,
        "exp": now + expires_minutes * 60,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def validate_local_token(token: str) -> dict:
    """Decode and validate an HS256-signed local JWT."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience="agenticorg-tool-gateway",
            issuer="agenticorg-local",
            options={"verify_iss": True},
        )
        return payload
    except JWTError as e:
        raise ValueError(f"Local token validation failed: {e}") from e


# ---------------------------------------------------------------------------
# JWKS helpers
# ---------------------------------------------------------------------------


async def _fetch_jwks() -> dict[str, Any]:
    global _jwks_cache, _jwks_cache_time
    if _jwks_cache and (time.time() - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(settings.jwt_public_key_url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_time = time.time()
    return _jwks_cache


async def _validate_jwks_token(token: str) -> dict[str, Any]:
    """Validate a JWT against the remote JWKS endpoint (RS256)."""
    jwks = await _fetch_jwks()
    unverified_header = jwt.get_unverified_header(token)

    # Reject alg:none attacks (SEC-AUTH-004)
    if unverified_header.get("alg", "").lower() == "none":
        raise ValueError("Algorithm none is not permitted")

    # Find matching key
    kid = unverified_header.get("kid")
    rsa_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            rsa_key = key
            break
    if not rsa_key:
        raise ValueError(f"No matching key found for kid={kid}")

    payload = jwt.decode(
        token,
        rsa_key,
        algorithms=["RS256"],
        audience="agenticorg-tool-gateway",
        issuer=settings.jwt_issuer,
        options={"verify_iss": True},
    )
    return payload


async def validate_token(token: str) -> dict[str, Any]:
    """Validate a JWT and return its claims. Raises on failure.

    Tries local HS256 first (for self-issued tokens), then falls back
    to JWKS RS256 validation (for external auth or test-patched JWKS).
    """
    # Try local HS256 first
    try:
        return validate_local_token(token)
    except ValueError:
        pass

    # Fall back to JWKS RS256
    try:
        return await _validate_jwks_token(token)
    except Exception as e:
        raise ValueError(f"Token validation failed: {e}") from e


def extract_scopes(claims: dict[str, Any]) -> list[str]:
    return claims.get("grantex:scopes", [])


def extract_tenant_id(claims: dict[str, Any]) -> str:
    return claims.get("agenticorg:tenant_id", "")


def extract_agent_id(claims: dict[str, Any]) -> str:
    return claims.get("agenticorg:agent_id", "")
