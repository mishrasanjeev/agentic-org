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
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass

import jwt
import redis.asyncio as aioredis
from jwt import PyJWTError

from core.config import is_strict_runtime_env, redis_socket_timeout_kwargs, redis_url_from_env, settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async Redis singleton
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def _strict() -> bool:
    """Is strict multi-replica auth-state enforcement enabled?"""
    env_override = os.getenv("AGENTICORG_AUTH_STATE_STRICT", "").lower() in ("1", "true", "yes")
    env = getattr(settings, "env", "development")
    runtime_env = env if isinstance(env, str) else "development"
    return env_override or is_strict_runtime_env(runtime_env)


async def _get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is not None:
        return _redis
    try:
        url = redis_url_from_env(default_db=0)
        _redis = aioredis.from_url(
            url,
            decode_responses=True,
            **redis_socket_timeout_kwargs(),
        )
        await _redis.ping()
        return _redis
    # enterprise-gate: broad-except-ok reason=auth-state-strict-runtime-refuses-memory-fallback
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
TOKEN_BLACKLIST_TTL = 3700  # floor: slightly > the default 60 min token TTL


# ---------------------------------------------------------------------------
# In-memory fallback state
# ---------------------------------------------------------------------------

_mem_failures: dict[str, list[float]] = defaultdict(list)
_mem_blocked: dict[str, float] = {}
_mem_blacklist: dict[str, float] = {}  # token_hash -> expiry
_mem_signup: dict[str, list[float]] = defaultdict(list)
_mem_user_state: dict[str, tuple[float, UserSessionState]] = {}  # key -> (expiry, state)


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
        # enterprise-gate: broad-except-ok reason=auth-failure-recording-fails-closed-in-strict-runtime
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
        # enterprise-gate: broad-except-ok reason=ip-block-check-fails-closed-in-strict-runtime
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
        # enterprise-gate: broad-except-ok reason=auth-failure-clear-fails-closed-in-strict-runtime
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


def _blacklist_ttl_for(token: str) -> int:
    """Remaining token lifetime (unverified ``exp``), floored at ``TOKEN_BLACKLIST_TTL``.

    ``token_ttl_minutes`` is configurable; a fixed TTL let long-lived tokens
    outlive their revocation. Non-JWT input falls back to the floor.
    """
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
    except (PyJWTError, ValueError, TypeError, AttributeError):
        return TOKEN_BLACKLIST_TTL
    if not isinstance(exp, int | float):
        return TOKEN_BLACKLIST_TTL
    return max(TOKEN_BLACKLIST_TTL, int(exp - time.time()) + 100)


async def blacklist_token(token: str) -> None:
    """Add a token to the blacklist for the rest of its lifetime."""
    h = _hash_token(token)
    ttl = _blacklist_ttl_for(token)
    _mem_blacklist[h] = time.time() + ttl
    r = await _get_redis()
    if r:
        try:
            await r.setex(f"auth:blacklist:{h}", ttl, "1")
            return
        # enterprise-gate: broad-except-ok reason=token-blacklist-write-fails-closed-in-strict-runtime
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
        # enterprise-gate: broad-except-ok reason=token-blacklist-read-fails-closed-in-strict-runtime
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
        # enterprise-gate: broad-except-ok reason=signup-rate-limit-fails-closed-in-strict-runtime
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


# ---------------------------------------------------------------------------
# Generic fixed-window counters (password reset, public demo requests, ...)
# ---------------------------------------------------------------------------

# enterprise-gate: process-local-ok reason=bounded-in-memory-rate-window-relaxed-runtime-only-when-redis-unavailable
_mem_window: dict[str, list[float]] = defaultdict(list)


async def check_window_rate(namespace: str, key: str, limit: int, window: int) -> bool:
    """Increment ``auth:<namespace>:<key>`` and return True if it should be BLOCKED.

    Redis-backed (cross-replica); in strict runtime env a Redis failure raises
    RuntimeError so callers fail closed. In relaxed env falls back to memory.
    """
    r = await _get_redis()
    if r:
        try:
            redis_key = f"auth:{namespace}:{key}"
            count = await r.incr(redis_key)
            if count == 1:
                await r.expire(redis_key, window)
            return count > limit
        # enterprise-gate: broad-except-ok reason=window-rate-limit-fails-closed-in-strict-runtime
        except Exception as exc:
            _raise_if_strict(f"check_window_rate:{namespace}", exc)
            logger.warning("auth_state: Redis window rate check failed, using memory (%s)", exc)
    else:
        _raise_if_strict(f"check_window_rate:{namespace}")
    # In-memory fallback (non-strict only)
    now = time.time()
    mem_key = f"{namespace}:{key}"
    _mem_window[mem_key] = [t for t in _mem_window[mem_key] if now - t < window]
    if len(_mem_window[mem_key]) >= limit:
        return True
    _mem_window[mem_key].append(now)
    return False


# ---------------------------------------------------------------------------
# Per-user session state (status + revocation watermark)
# ---------------------------------------------------------------------------

USER_SESSION_STATE_TTL = 30  # seconds — bounds the revocation lag across replicas

# Sentinel for "no users row" so a miss is cached too (bounded by the TTL).
_USER_STATE_MISSING = "missing"


@dataclass(frozen=True)
class UserSessionState:
    """What the auth middleware needs to decide whether a JWT is still honoured."""

    found: bool
    status: str = ""
    sessions_invalid_before: float | None = None  # POSIX seconds, UTC
    # True only when the users row could not be read in a relaxed runtime
    # (strict raises instead). Distinguishes a degraded lookup from a row
    # that is definitively absent.
    lookup_failed: bool = False

    def rejects_token(self, issued_at: float | None) -> str | None:
        """Return a rejection reason or ``None`` if the token is still valid."""
        if not self.found:
            # Only legacy session tokens reach this check (middleware +
            # websocket legacy paths). A session whose users row is gone
            # must not be honoured; a degraded relaxed-runtime lookup keeps
            # the pre-existing best-effort behaviour (logged by the loader).
            return None if self.lookup_failed else "user_missing"
        if self.status != "active":
            return "user_inactive"
        if self.sessions_invalid_before is None:
            return None
        # JWT ``iat`` has second granularity while the watermark carries
        # microseconds: compare against the whole-second floor so a token
        # minted in the same second as a reset / logout-all is honoured.
        if issued_at is None or issued_at < math.floor(self.sessions_invalid_before):
            return "session_revoked"
        return None


def _user_state_key(tenant_id: str, email: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{email.lower()}".encode()).hexdigest()
    return f"auth:user_state:{digest}"


def _encode_user_state(state: UserSessionState) -> str:
    if not state.found:
        return _USER_STATE_MISSING
    watermark = "" if state.sessions_invalid_before is None else repr(state.sessions_invalid_before)
    return f"{state.status}|{watermark}"


def _decode_user_state(raw: str) -> UserSessionState:
    if raw == _USER_STATE_MISSING:
        return UserSessionState(found=False)
    status, _, watermark = raw.partition("|")
    return UserSessionState(
        found=True,
        status=status,
        sessions_invalid_before=float(watermark) if watermark else None,
    )


async def _load_user_state_from_db(tenant_id: str, email: str) -> UserSessionState:
    import uuid

    from sqlalchemy import select

    from core.database import async_session_factory
    from core.models.user import User

    tid = uuid.UUID(str(tenant_id))
    async with async_session_factory() as session:
        result = await session.execute(
            select(User.status, User.sessions_invalid_before).where(
                User.tenant_id == tid, User.email == email
            )
        )
        row = result.first()
    if row is None:
        return UserSessionState(found=False)
    status, watermark = row
    return UserSessionState(
        found=True,
        status=str(status or ""),
        sessions_invalid_before=watermark.timestamp() if watermark is not None else None,
    )


async def get_user_session_state(tenant_id: str, email: str) -> UserSessionState:
    """Return the user's status + revocation watermark, cached for a short TTL.

    Redis is consulted first (cross-replica, ``USER_SESSION_STATE_TTL``), then
    the ``users`` row. In strict runtime env a combined cache + DB failure
    raises ``RuntimeError`` so the middleware fails closed; relaxed runtimes
    degrade to an in-memory cache with the same TTL.
    """
    key = _user_state_key(tenant_id, email)
    now = time.time()
    cached = _mem_user_state.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    # The users row is authoritative; Redis is only a cross-replica cache.
    # A Redis outage therefore falls through to the DB even in strict mode
    # (``_get_redis`` raises there) — only cache AND DB failing is fatal.
    r = None
    try:
        r = await _get_redis()
        if r:
            raw = await r.get(key)
            if raw:
                state = _decode_user_state(raw)
                _mem_user_state[key] = (now + USER_SESSION_STATE_TTL, state)
                return state
    # enterprise-gate: broad-except-ok reason=user-state-cache-read-falls-through-to-authoritative-db-read
    except Exception as exc:
        logger.warning("auth_state: Redis user-state read failed (%s)", exc)

    try:
        state = await _load_user_state_from_db(tenant_id, email)
    # enterprise-gate: broad-except-ok reason=user-state-db-read-fails-closed-in-strict-runtime
    except Exception as exc:
        _raise_if_strict("get_user_session_state", exc)
        logger.warning("auth_state: users lookup failed, treating as unknown user (%s)", exc)
        return UserSessionState(found=False, lookup_failed=True)

    _mem_user_state[key] = (now + USER_SESSION_STATE_TTL, state)
    if r:
        try:
            await r.setex(key, USER_SESSION_STATE_TTL, _encode_user_state(state))
        # enterprise-gate: broad-except-ok reason=user-state-cache-write-is-best-effort-after-authoritative-db-read
        except Exception as exc:
            logger.warning("auth_state: Redis user-state write failed (%s)", exc)
    return state


async def invalidate_user_session_state(tenant_id: str, email: str) -> None:
    """Drop the cached state after a revocation so the new watermark is seen promptly.

    Best-effort: the watermark is already committed in ``users`` (the
    authoritative source), so a cache-bust failure only delays enforcement
    on other replicas by at most ``USER_SESSION_STATE_TTL`` seconds.
    """
    key = _user_state_key(tenant_id, email)
    _mem_user_state.pop(key, None)
    try:
        r = await _get_redis()
        if r:
            await r.delete(key)
    # enterprise-gate: broad-except-ok reason=revocation-cache-bust-is-best-effort-and-ttl-bounded
    except Exception as exc:
        logger.warning("auth_state: Redis user-state delete failed (%s)", exc)

