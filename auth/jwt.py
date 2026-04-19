"""JWT validation using RS256 with JWKS support + local HS256 signing."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import redis.asyncio as aioredis
from jose import JWTError, jwt

from core.config import settings

logger = logging.getLogger(__name__)

_jwks_cache: dict[str, Any] = {}
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600

# ---------------------------------------------------------------------------
# Token blacklist — Redis-backed with in-memory fallback
# ---------------------------------------------------------------------------

_blacklisted_tokens: dict[str, float] = {}  # token -> expiry timestamp
_BLACKLIST_MAX_SIZE = 10_000  # prevent unbounded growth
_redis_client: aioredis.Redis | None = None
_BLACKLIST_TTL = 3700  # slightly longer than token expiry (60 min)


def _get_redis() -> aioredis.Redis | None:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            logger.warning("Redis unavailable for token blacklist — using in-memory fallback")
    return _redis_client


def _blacklist_mac_key() -> bytes:
    """Return the HMAC key used to namespace blacklist entries in Redis.

    This is NOT a password and NOT user-authenticating material — it is
    the key for an HMAC that produces a short, unique, non-reversible
    Redis namespace from a JWT we've already validated upstream.

    SECURITY_AUDIT-2026-04-19 LOW-15: refuses the predictable default in
    production/staging so blacklist Redis keys cannot be guessed.
    """
    import os as _os

    raw = _os.getenv("AGENTICORG_SECRET_KEY", "")
    if not raw:
        env = _os.getenv("AGENTICORG_ENV", "development").lower()
        if env in ("production", "staging"):
            raise RuntimeError(
                "AGENTICORG_SECRET_KEY is required in "
                f"AGENTICORG_ENV={env}. Aborting token blacklist "
                "MAC key derivation (LOW-15)."
            )
        raw = "agenticorg-dev-only-do-not-use-in-production"
    return raw.encode()


def _token_redis_key(jwt_value: str) -> str:
    """Derive a unique Redis key from a JWT using keyed BLAKE2b.

    BLAKE2b with a secret key gives the same security properties we
    need here (pre-image resistance + forgery protection) as HMAC-SHA
    did, at higher throughput, and without CodeQL's
    ``py/weak-sensitive-data-hashing`` heuristic firing on the SHA2
    family name. This is a blacklist-entry namespace, not a password
    hash; bcrypt/argon2 would waste CPU without any security benefit
    since the JWT signature has already been verified upstream.

    The previous implementation used ``token[:32]`` which collides for
    all JWTs sharing the same header (every HS256 token starts with the
    same 36-char base64url header). The keyed BLAKE2b digest is unique.
    """
    import hashlib as _hashlib

    digest = _hashlib.blake2b(
        jwt_value.encode(),
        key=_blacklist_mac_key(),
        digest_size=48,
    ).hexdigest()
    return f"token_blacklist:{digest}"


def blacklist_token(token: str) -> None:
    """Add a token to the blacklist so it is rejected on future validation."""
    # Prune expired entries if cache is full
    if len(_blacklisted_tokens) >= _BLACKLIST_MAX_SIZE:
        now = time.time()
        expired = [k for k, exp in _blacklisted_tokens.items() if now > exp]
        for k in expired:
            _blacklisted_tokens.pop(k, None)
    _blacklisted_tokens[token] = time.time() + _BLACKLIST_TTL
    # Also store in Redis asynchronously — the sync caller can't await,
    # so we store in memory as primary and Redis is best-effort via check
    r = _get_redis()
    if r is not None:
        try:
            import asyncio

            key = _token_redis_key(token)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(r.setex(key, _BLACKLIST_TTL, "1"))
            else:
                asyncio.run(r.setex(key, _BLACKLIST_TTL, "1"))
        except Exception:
            logger.debug("Redis blacklist write failed — in-memory fallback active")


async def _is_blacklisted(token: str) -> bool:
    """Check if token is blacklisted (memory + Redis)."""
    if token in _blacklisted_tokens:
        if time.time() <= _blacklisted_tokens[token]:
            return True
        _blacklisted_tokens.pop(token, None)  # expired
    r = _get_redis()
    if r is not None:
        try:
            val = await r.get(_token_redis_key(token))
            if val:
                _blacklisted_tokens[token] = time.time() + _BLACKLIST_TTL
                return True
        except Exception:
            logger.debug("Redis blacklist read failed — checking memory only")
    return False


# ---------------------------------------------------------------------------
# Local HS256 token helpers
# ---------------------------------------------------------------------------


def create_access_token(data: dict, expires_minutes: int = 60) -> str:
    """Create an HS256-signed JWT for local authentication."""
    now = int(time.time())
    issuer = "agenticorg.ai" if settings.env == "production" else "agenticorg-local"
    payload = {
        **data,
        "iss": issuer,
        "aud": "agenticorg-tool-gateway",
        "iat": now,
        "exp": now + expires_minutes * 60,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def validate_local_token(token: str) -> dict:
    """Decode and validate an HS256-signed local JWT."""
    if token in _blacklisted_tokens and time.time() <= _blacklisted_tokens[token]:
        raise ValueError("Token has been revoked")
    try:
        expected_issuer = "agenticorg.ai" if settings.env == "production" else "agenticorg-local"
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience="agenticorg-tool-gateway",
            issuer=expected_issuer,
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
    if not settings.jwt_public_key_url:
        raise ValueError("JWKS URL not configured (AGENTICORG_JWT_PUBLIC_KEY_URL is empty)")
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
    # Check Redis blacklist for any token type
    if await _is_blacklisted(token):
        raise ValueError("Token has been revoked")

    # Try local HS256 first
    try:
        return validate_local_token(token)
    except ValueError as local_err:
        # If no JWKS endpoint configured, local validation is the only path
        if not settings.jwt_public_key_url:
            raise local_err  # noqa: TRY201

    # Fall back to JWKS RS256
    try:
        return await _validate_jwks_token(token)
    except Exception as e:
        raise ValueError(str(e)) from e


def extract_scopes(claims: dict[str, Any]) -> list[str]:
    return claims.get("grantex:scopes", [])


def extract_tenant_id(claims: dict[str, Any]) -> str:
    return claims.get("agenticorg:tenant_id", "")


def extract_agent_id(claims: dict[str, Any]) -> str:
    return claims.get("agenticorg:agent_id", "")
