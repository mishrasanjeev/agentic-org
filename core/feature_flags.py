"""Feature flag evaluator — reads from DB, caches in-process.

Why not LaunchDarkly / GrowthBook / Unleash?
  - Open-source only (user requirement)
  - This is a single-binary deployment; an in-process evaluator backed by
    the existing Postgres cuts the moving parts.

Usage:
    from core.feature_flags import is_enabled

    if await is_enabled("new_workflow_builder", tenant_id=tid, user_id=uid):
        # new code
    else:
        # old code

Resolution order for a single flag key:
  1. Tenant-specific row (tenant_id = <tid>) if present
  2. Global default row (tenant_id IS NULL)
  3. Disabled (fail closed)

Rollout percentage:
  - rollout_percentage is evaluated with a deterministic hash of
    (flag_key, user_id).  A given user either sees the flag or doesn't —
    no flicker between requests.
  - If user_id is None, we fall back to tenant_id.
  - 0% = off for everyone (even if enabled=True).
  - 100% = on for everyone (when enabled=True).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from core.database import get_tenant_session
from core.models.feature_flag import FeatureFlag

logger = structlog.get_logger()

# Simple TTL cache.  Entries are (value, expires_at).
# Key: (tenant_id_str, flag_key)
_CACHE_TTL_SECONDS = 30
_CACHE_MAX_SIZE = 2_048
# Cached in place of a row when the flag store could not be read. ``is_enabled``
# treats it like an absent row; ``is_enabled_strict`` raises for it until the
# entry expires, so an outage is not re-probed on every call.
_LOOKUP_FAILED: Any = object()
# enterprise-gate: process-local-ok reason=bounded-ttl-db-backed-feature-flag-cache
_cache: dict[tuple[str, str], tuple[dict[str, Any] | None, float]] = {}


class FeatureFlagLookupError(RuntimeError):
    """The flag store could not be read, so the flag's value is unknown."""


def _bucket(flag_key: str, subject_id: str) -> int:
    """Return 0-99 deterministic bucket for a (flag, subject) pair."""
    h = hashlib.sha256(f"{flag_key}:{subject_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") % 100


def _store_cache(
    cache_key: tuple[str, str],
    row: dict[str, Any] | None,
    expires_at: float,
) -> None:
    now = time.monotonic()
    if cache_key not in _cache and len(_cache) >= _CACHE_MAX_SIZE:
        expired = [
            key
            for key, (_, cached_expires_at) in _cache.items()
            if cached_expires_at <= now
        ]
        for key in expired:
            _cache.pop(key, None)
        while len(_cache) >= _CACHE_MAX_SIZE:
            oldest_key = min(_cache, key=lambda item: _cache[item][1])
            _cache.pop(oldest_key, None)
    _cache[cache_key] = (row, expires_at)


async def _load_flag(
    tenant_id: uuid.UUID | None, flag_key: str, *, strict: bool = False
) -> dict[str, Any] | None:
    """Fetch a flag from DB with a tiny in-process cache.

    Returns the row as a dict (enabled, rollout_percentage) or None.
    With ``strict`` a failed lookup raises ``FeatureFlagLookupError``
    instead of reading as an absent row.
    """
    cache_key = (str(tenant_id) if tenant_id else "_global", flag_key)
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        if cached[0] is not _LOOKUP_FAILED:
            return cached[0]
        if strict:
            raise FeatureFlagLookupError(f"feature flag {flag_key!r} could not be read")
        return None

    row: dict[str, Any] | None = None
    try:
        # Look up a tenant-scoped flag first, then fall back to global.
        lookup_tenant = tenant_id or uuid.UUID(int=0)
        async with get_tenant_session(lookup_tenant) as session:
            if tenant_id is not None:
                result = await session.execute(
                    select(FeatureFlag).where(
                        FeatureFlag.tenant_id == tenant_id,
                        FeatureFlag.flag_key == flag_key,
                    )
                )
                flag = result.scalar_one_or_none()
                if flag is not None:
                    row = {
                        "enabled": flag.enabled,
                        "rollout_percentage": flag.rollout_percentage,
                    }

            if row is None:
                result = await session.execute(
                    select(FeatureFlag).where(
                        FeatureFlag.tenant_id.is_(None),
                        FeatureFlag.flag_key == flag_key,
                    )
                )
                flag = result.scalar_one_or_none()
                if flag is not None:
                    row = {
                        "enabled": flag.enabled,
                        "rollout_percentage": flag.rollout_percentage,
                    }
    # enterprise-gate: broad-except-ok reason=feature-flag-lookup-failure-uses-callsite-default-or-raises-when-strict
    except Exception as exc:
        logger.debug("feature_flag_lookup_failed", flag_key=flag_key)
        _store_cache(cache_key, _LOOKUP_FAILED, now + _CACHE_TTL_SECONDS)
        if strict:
            raise FeatureFlagLookupError(f"feature flag {flag_key!r} could not be read") from exc
        return None

    _store_cache(cache_key, row, now + _CACHE_TTL_SECONDS)
    return row


async def is_enabled(
    flag_key: str,
    *,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    default: bool = False,
) -> bool:
    """Return True if the flag is enabled for this subject."""
    row = await _load_flag(tenant_id, flag_key)
    return _evaluate(flag_key, row, tenant_id=tenant_id, user_id=user_id, default=default)


async def is_enabled_strict(
    flag_key: str,
    *,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    default: bool = False,
) -> bool:
    """Like ``is_enabled`` but raises ``FeatureFlagLookupError`` when the flag
    store cannot be read.

    For authority decisions, where "the flag is off" and "we could not tell"
    must not look the same.
    """
    row = await _load_flag(tenant_id, flag_key, strict=True)
    return _evaluate(flag_key, row, tenant_id=tenant_id, user_id=user_id, default=default)


def _evaluate(
    flag_key: str,
    row: dict[str, Any] | None,
    *,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    default: bool,
) -> bool:
    if row is None:
        return default
    if not row["enabled"]:
        return False

    pct = int(row.get("rollout_percentage", 100))
    if pct >= 100:
        return True
    if pct <= 0:
        return False

    subject = str(user_id or tenant_id or "")
    if not subject:
        # No stable subject — treat as a coin flip at flag-key level.
        subject = "__anon__"
    return _bucket(flag_key, subject) < pct


def clear_cache() -> None:
    """Testing helper — reset the in-process flag cache."""
    _cache.clear()
