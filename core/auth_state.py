"""Redis-backed auth state — cross-pod consistent throttling and token blacklist.

Provides atomic rate limiting, IP blocking, and token blacklisting that
survives pod restarts and works correctly across multiple replicas.

Fallback behavior:
- Default (strict mode OFF): degrades to in-memory state if Redis is
  unavailable. Acceptable for single-replica dev/local environments.
- Strict mode (AGENTICORG_AUTH_STATE_STRICT=1): refuses to degrade —
  raises RuntimeError on Redis failure so the caller can return 503.
  Enterprise multi-replica deploys MUST enable strict mode; otherwise
  a token revoked on replica A still validates on replica B, and an
  IP blocked on replica A is free to retry on replica B.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import defaultdict

import redis.asyncio as aioredis

from core.config import redis_url_from_env

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async Redis singleton
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def _strict() -> bool:
    """Is strict multi-replica auth-state enforcement enabled?"""
    return os.getenv("AGENTICORG_AUTH_STATE_STRICT", "").lower() in ("1", "true", "yes")


async def _get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is not None:
        return _redis
    try:
        url = redis_url_from_env(default_db=0)
        _redis = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        await _redis.ping()
        return _redis
    except Exception as exc:
        if _strict():
            logger.error(
                "auth_state: Redis unavailable in strict mode — refusing to "
                "degrade to in-memory fallback (%s)",
                exc,
            )
            raise
        logger.warning(
            "auth_state: Redis unavailable, using in-memory fallback "
            "(single-replica only; set AGENTICORG_AUTH_STATE_STRICT=1 "
            "for multi-replica enforcement): %s",
            exc,
        )
        _redis = None
        return None


def _raise_if_strict(op: str, exc: Exception | None = None) -> None:
    """If strict mode is on, reject degraded operation."""
    if _strict():
        msg = f"auth_state: {op} requires Redis in strict mode"
        if exc is not None:
            raise RuntimeError(f"{msg}: {exc}") from exc
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_FAILURE_WINDOW = 60  # seconds
AUTH_MAX_FAILURES = 10
AUTH_BLOCK_DURATION = 900  # 15 minutes
SIGNUP_MAX_PER_HOUR = 5
SIGNUP_WINDOW = 3600
TOKEN_BLACKLIST_TTL = 3700  # slightly > token TTL (60 min)


# ---------------------------------------------------------------------------
# In-memory fallback state
# ---------------------------------------------------------------------------

_mem_failures: dict[str, list[float]] = defaultdict(list)
_mem_blocked: dict[str, float] = {}
_mem_blacklist: dict[str, float] = {}  # token_hash -> expiry
_mem_signup: dict[str, list[float]] = defaultdict(list)


# ---------------------------------------------------------------------------
# IP-based auth failure tracking
# ---------------------------------------------------------------------------


async def record_auth_failure(ip: str) -> bool:
    """Record a failed auth attempt. Returns True if IP is now blocked."""
    r = await _get_redis()
    if r:
        try:
            key = f"auth:failures:{ip}"
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, AUTH_FAILURE_WINDOW)
            if count >= AUTH_MAX_FAILURES:
                await r.setex(f"auth:blocked:{ip}", AUTH_BLOCK_DURATION, "1")
                return True
            return False
        except Exception as exc:
            _raise_if_strict("record_auth_failure", exc)
            logger.warning("auth_state: Redis failure tracking failed, using memory (%s)", exc)
    else:
        _raise_if_strict("record_auth_failure")
    # In-memory fallback (non-strict only)
    now = time.time()
    _mem_failures[ip] = [t for t in _mem_failures[ip] if now - t < AUTH_FAILURE_WINDOW]
    _mem_failures[ip].append(now)
    if len(_mem_failures[ip]) >= AUTH_MAX_FAILURES:
        _mem_blocked[ip] = now + AUTH_BLOCK_DURATION
        return True
    return False


async def is_ip_blocked(ip: str) -> bool:
    """Check if an IP is currently blocked."""
    r = await _get_redis()
    if r:
        try:
            val = await r.get(f"auth:blocked:{ip}")
            if val:
                return True
            return False
        except Exception as exc:
            _raise_if_strict("is_ip_blocked", exc)
            logger.warning("auth_state: Redis block check failed, using memory (%s)", exc)
    else:
        _raise_if_strict("is_ip_blocked")
    # In-memory fallback (non-strict only)
    if ip in _mem_blocked:
        if time.time() < _mem_blocked[ip]:
            return True
        del _mem_blocked[ip]
        _mem_failures.pop(ip, None)
    return False


async def clear_auth_failures(ip: str) -> None:
    """Clear failure history after successful auth."""
    r = await _get_redis()
    if r:
        try:
            await r.delete(f"auth:failures:{ip}", f"auth:blocked:{ip}")
        except Exception as exc:
            _raise_if_strict("clear_auth_failures", exc)
            logger.warning("auth_state: Redis clear_failures failed (%s)", exc)
    else:
        _raise_if_strict("clear_auth_failures")
    _mem_failures.pop(ip, None)
    _mem_blocked.pop(ip, None)


# ---------------------------------------------------------------------------
# Token blacklist
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    """SHA-256 hash of the token — never store raw JWTs in Redis.

    Requires ``AGENTICORG_SECRET_KEY`` in any non-local environment.
    SECURITY_AUDIT-2026-04-19 LOW-15: pre-fix this fell back to a
    predictable hard-coded default which made blacklist keys guessable.
    """
    secret = os.getenv("AGENTICORG_SECRET_KEY", "")
    if not secret:
        env = os.getenv("AGENTICORG_ENV", "development").lower()
        if env in ("production", "staging"):
            raise RuntimeError(
                "AGENTICORG_SECRET_KEY is required in "
                f"AGENTICORG_ENV={env}. Aborting token blacklist "
                "hash to avoid predictable default (LOW-15)."
            )
        # Local/dev only — still unique-per-process so tests don't
        # collide with any real environment.
        secret = "agenticorg-dev-only-do-not-use-in-production"
    return hashlib.sha256(f"{secret}:{token}".encode()).hexdigest()


async def blacklist_token(token: str) -> None:
    """Add a token to the blacklist."""
    h = _hash_token(token)
    _mem_blacklist[h] = time.time() + TOKEN_BLACKLIST_TTL
    r = await _get_redis()
    if r:
        try:
            await r.setex(f"auth:blacklist:{h}", TOKEN_BLACKLIST_TTL, "1")
            return
        except Exception as exc:
            _raise_if_strict("blacklist_token", exc)
            logger.warning("auth_state: Redis blacklist write failed (%s)", exc)
    else:
        _raise_if_strict("blacklist_token")


async def is_token_blacklisted(token: str) -> bool:
    """Check if a token is blacklisted."""
    h = _hash_token(token)
    # Memory check first (L1 cache)
    if h in _mem_blacklist:
        if time.time() <= _mem_blacklist[h]:
            return True
        del _mem_blacklist[h]
    # Redis check
    r = await _get_redis()
    if r:
        try:
            val = await r.get(f"auth:blacklist:{h}")
            if val:
                _mem_blacklist[h] = time.time() + TOKEN_BLACKLIST_TTL
                return True
            return False
        except Exception as exc:
            _raise_if_strict("is_token_blacklisted", exc)
            logger.warning("auth_state: Redis blacklist read failed (%s)", exc)
            return False
    _raise_if_strict("is_token_blacklisted")
    return False


# ---------------------------------------------------------------------------
# Signup rate limiting
# ---------------------------------------------------------------------------


async def check_signup_rate(ip: str) -> bool:
    """Returns True if the signup should be BLOCKED (rate exceeded)."""
    r = await _get_redis()
    if r:
        try:
            key = f"auth:signup:{ip}"
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, SIGNUP_WINDOW)
            return count > SIGNUP_MAX_PER_HOUR
        except Exception as exc:
            _raise_if_strict("check_signup_rate", exc)
            logger.warning("auth_state: Redis signup rate check failed, using memory (%s)", exc)
    else:
        _raise_if_strict("check_signup_rate")
    # In-memory fallback (non-strict only)
    now = time.time()
    _mem_signup[ip] = [t for t in _mem_signup[ip] if now - t < SIGNUP_WINDOW]
    if len(_mem_signup[ip]) >= SIGNUP_MAX_PER_HOUR:
        return True
    _mem_signup[ip].append(now)
    return False
