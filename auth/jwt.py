"""JWT validation using RS256 with JWKS support."""
from __future__ import annotations

import time
from typing import Any

import httpx
from jose import JWTError, jwt
from jose.backends import RSAKey

from core.config import settings, external_keys
from core.schemas.errors import ErrorCode, make_error

_jwks_cache: dict[str, Any] = {}
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600


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


async def validate_token(token: str) -> dict[str, Any]:
    """Validate a JWT and return its claims. Raises on failure."""
    try:
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
            audience="agentflow-tool-gateway",
            issuer=settings.jwt_issuer,
            options={"verify_iss": True},
        )
        return payload
    except JWTError as e:
        raise ValueError(f"Token validation failed: {e}") from e


def extract_scopes(claims: dict[str, Any]) -> list[str]:
    return claims.get("grantex:scopes", [])


def extract_tenant_id(claims: dict[str, Any]) -> str:
    return claims.get("agentflow:tenant_id", "")


def extract_agent_id(claims: dict[str, Any]) -> str:
    return claims.get("agentflow:agent_id", "")
