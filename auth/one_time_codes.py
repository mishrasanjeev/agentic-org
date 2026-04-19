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

import os
import secrets
from typing import Literal

import redis.asyncio as aioredis

CodeKind = Literal["invite", "reset"]

_CODE_BYTES = 24  # 192-bit opaque code → 32-char urlsafe-base64


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
