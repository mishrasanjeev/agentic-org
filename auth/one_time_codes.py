"""Opaque one-time codes for invite / password-reset links.

SECURITY_AUDIT-2026-04-19 MEDIUM-10: JWT bearer tokens used to be
embedded in invite / reset URLs. Query-string tokens leak into browser
history, reverse-proxy logs, email-security scanners, analytics
referrers, and screenshots.

This module issues short random opaque codes and keeps the real JWT on
the server side, keyed by the code in Redis with a TTL. When the user
clicks the link, the backend exchanges the opaque code for the JWT
just long enough to run the action, then deletes the code (one-time
use).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from typing import Literal

import redis.asyncio as aioredis

CodeKind = Literal["invite", "reset", "mfa"]

_CODE_BYTES = 24  # 192-bit opaque code → 32-char urlsafe-base64

# A sign-in that passed its first factor waits this long for the
# authenticator code before the user has to start again.
MFA_CHALLENGE_TTL_SECONDS = 300
MFA_ACCOUNT_MAX_ATTEMPTS = 5

# Count and initial expiry are one Redis operation, including across replicas.
_ATTEMPT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
elseif redis.call('TTL', KEYS[1]) < 0 then
    return redis.error_reply('MFA counter has no expiry')
end
return count
"""


def _redis() -> aioredis.Redis:
    url = os.getenv("AGENTICORG_REDIS_URL") or os.getenv(
        "REDIS_URL", "redis://localhost:6379/0"
    )
    return aioredis.from_url(url, decode_responses=True)


def _key(kind: CodeKind, code: str) -> str:
    return f"onetime:{kind}:{code}"


async def issue(kind: CodeKind, payload: str, ttl_seconds: int) -> str:
    """Generate a fresh opaque code that maps to ``payload``."""
    code = secrets.token_urlsafe(_CODE_BYTES)
    r = _redis()
    try:
        await r.setex(_key(kind, code), ttl_seconds, payload)
    finally:
        await r.aclose()
    return code


async def peek(kind: CodeKind, code: str) -> str | None:
    """Return the payload for a code without consuming it.

    Used by GET endpoints that display metadata for the link target
    (e.g. ``/org/invite-info``) before the user submits the final
    action. Reads are non-destructive.
    """
    r = _redis()
    try:
        return await r.get(_key(kind, code))
    finally:
        await r.aclose()


async def consume(kind: CodeKind, code: str) -> str | None:
    """Atomically read and delete the code's payload.

    Returns the stored payload on success, or ``None`` if the code was
    already consumed, never existed, or expired.
    """
    r = _redis()
    try:
        payload = await r.getdel(_key(kind, code))
        return payload
    finally:
        await r.aclose()


async def record_attempt(kind: CodeKind, code: str, ttl_seconds: int) -> int:
    """Count one guess against a code and return the running total.

    Lets a caller cap wrong guesses per code (an MFA challenge allows five)
    across replicas; the counter expires with the code.
    """
    return await _record_attempt(f"onetime-attempts:{kind}:{code}", ttl_seconds)


async def _record_attempt(key: str, ttl_seconds: int) -> int:
    r = _redis()
    try:
        count = await r.eval(_ATTEMPT_SCRIPT, 1, key, ttl_seconds)
        if type(count) is not int or count < 1:
            raise aioredis.RedisError("Invalid MFA attempt counter")
        return count
    finally:
        await r.aclose()


def _mfa_account_key(tenant_id: str, user_id: str) -> str:
    scope = f"{uuid.UUID(tenant_id)}:{uuid.UUID(user_id)}"
    digest = hashlib.sha256(scope.encode("ascii")).hexdigest()
    return f"onetime-attempts:mfa-account:{digest}"


async def record_mfa_account_attempt(tenant_id: str, user_id: str) -> int:
    """Reserve a guess shared by all challenges and enrollment operations."""
    return await _record_attempt(_mfa_account_key(tenant_id, user_id), MFA_CHALLENGE_TTL_SECONDS)


async def clear_mfa_account_attempts(tenant_id: str, user_id: str) -> None:
    """Call only after a valid second factor, while holding that user's row lock."""
    r = _redis()
    try:
        await r.delete(_mfa_account_key(tenant_id, user_id))
    finally:
        await r.aclose()
