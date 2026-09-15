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

Lookup failures:
  ``is_enabled`` treats a failed lookup as "no row" and returns the
  call-site default (and caches that for the TTL). Callers whose safety
  depends on the flag use ``is_enabled_strict``, which raises
  ``FeatureFlagLookupError`` instead and never trusts a cached failure.

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
# enterprise-gate: process-local-ok reason=bounded-ttl-db-backed-feature-flag-cache
_cache: dict[tuple[str, str], tuple[dict[str, Any] | None, float]] = {}
# Cached in place of a row when a lookup failed; only ``is_enabled`` reads it (as "no row").
_LOOKUP_FAILED: dict[str, Any] = {"lookup_failed": True}


class FeatureFlagLookupError(RuntimeError):
    """The flag could not be read, so its value is unknown (not "off")."""


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


async def _query_flag(tenant_id: uuid.UUID | None, flag_key: str) -> dict[str, Any] | None:
    """Read a flag from the DB: the tenant row, else the global row, else ``None``. Raises on failure."""
    row: dict[str, Any] | None = None
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
    return row


async def _load_flag(
    tenant_id: uuid.UUID | None, flag_key: str
) -> dict[str, Any] | None:
    """Fetch a flag from DB with a tiny in-process cache.

    Returns the row as a dict (enabled, rollout_percentage) or None.
    """
    cache_key = (str(tenant_id) if tenant_id else "_global", flag_key)
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return None if cached[0] is _LOOKUP_FAILED else cached[0]

    try:
        row = await _query_flag(tenant_id, flag_key)
    # enterprise-gate: broad-except-ok reason=feature-flag-lookup-failure-uses-callsite-default
    except Exception:
        logger.debug("feature_flag_lookup_failed", flag_key=flag_key)
        _store_cache(cache_key, _LOOKUP_FAILED, now + _CACHE_TTL_SECONDS)
        return None

    _store_cache(cache_key, row, now + _CACHE_TTL_SECONDS)
    return row


def _evaluate(
    row: dict[str, Any] | None,
    flag_key: str,
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


async def is_enabled(
    flag_key: str,
    *,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    default: bool = False,
) -> bool:
    """Return True if the flag is enabled for this subject."""
    row = await _load_flag(tenant_id, flag_key)
    return _evaluate(row, flag_key, tenant_id, user_id, default)


async def is_enabled_strict(
    flag_key: str,
    *,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Like ``is_enabled`` (off when no row exists), but a failed lookup raises ``FeatureFlagLookupError``.

    Successful reads share the TTL cache; a cached failure is never used and
    failures are not cached here, so the next call retries the database.
    """
    cache_key = (str(tenant_id) if tenant_id else "_global", flag_key)
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and cached[1] > now and cached[0] is not _LOOKUP_FAILED:
        row = cached[0]
    else:
        try:
            row = await _query_flag(tenant_id, flag_key)
        # enterprise-gate: broad-except-ok reason=strict-flag-lookup-failure-raises-feature-flag-lookup-error
        except Exception as exc:
            logger.warning("feature_flag_lookup_failed", flag_key=flag_key, strict=True, error_type=type(exc).__name__)
            raise FeatureFlagLookupError(f"feature flag {flag_key!r} could not be read") from exc
        _store_cache(cache_key, row, now + _CACHE_TTL_SECONDS)
    return _evaluate(row, flag_key, tenant_id, user_id, default=False)


def clear_cache() -> None:
    """Testing helper — reset the in-process flag cache."""
    _cache.clear()
