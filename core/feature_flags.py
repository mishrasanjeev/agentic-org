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
from dataclasses import dataclass
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
    tenant_id: uuid.UUID | None, flag_key: str
) -> dict[str, Any] | None:
    """Fetch a flag from DB with a tiny in-process cache.

    Returns the row as a dict (enabled, rollout_percentage) or None.
    """
    cache_key = (str(tenant_id) if tenant_id else "_global", flag_key)
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return cached[0]

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
    # enterprise-gate: broad-except-ok reason=feature-flag-lookup-failure-uses-callsite-default
    except Exception:
        logger.debug("feature_flag_lookup_failed", flag_key=flag_key)
        row = None

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


# Flags that decide what agents are allowed to do. Tenant admins must not be
# able to set, change or delete them through the tenant feature-flag API: a
# tenant row could otherwise switch an operator's enforcement off. Platform
# operators manage them with ``scripts/authority_flags.py``. A key is reserved
# when it equals one of these or starts with one of them followed by ".".
RESERVED_FLAG_KEYS: tuple[str, ...] = (
    "grants.enforce_closed",
    "pseudonymisation.pre_model",
    "approvals.resume_agent_runs",
    "decisions.required",
    "caps.enforce",
)


def is_reserved_flag_key(flag_key: str) -> bool:
    """True for authority flags only platform operators may manage."""
    key = (flag_key or "").strip().lower()
    return any(key == reserved or key.startswith(reserved + ".") for reserved in RESERVED_FLAG_KEYS)


class FeatureFlagLookupError(RuntimeError):
    """The flag store could not be read, so the flag's value is unknown."""


@dataclass(frozen=True)
class FlagRows:
    """The global and tenant rows for one flag key, read independently."""

    global_row: dict[str, Any] | None
    tenant_row: dict[str, Any] | None


def row_enabled(flag_key: str, row: dict[str, Any] | None, *, subject_id: str) -> bool:
    """Evaluate one flag row (``enabled`` and rollout) for a stable subject."""
    if row is None or not row["enabled"]:
        return False
    pct = int(row.get("rollout_percentage", 100))
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    return _bucket(flag_key, subject_id or "__anon__") < pct


async def load_flag_rows_strict(flag_key: str, *, tenant_id: uuid.UUID) -> FlagRows:
    """Read a flag's global row and the tenant's row separately.

    For authority decisions: unlike ``is_enabled`` a tenant row does not hide
    the global row, and a store that cannot be read raises
    ``FeatureFlagLookupError`` instead of reading as "absent". Only successful
    reads are cached.
    """
    cache_key = (f"rows:{tenant_id}", flag_key)
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and cached[1] > now and cached[0] is not None:
        return FlagRows(global_row=cached[0]["global"], tenant_row=cached[0]["tenant"])

    def _as_row(flag: FeatureFlag | None) -> dict[str, Any] | None:
        if flag is None:
            return None
        return {"enabled": flag.enabled, "rollout_percentage": flag.rollout_percentage}

    try:
        async with get_tenant_session(tenant_id) as session:
            tenant_flag = (
                await session.execute(
                    select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.flag_key == flag_key)
                )
            ).scalar_one_or_none()
            global_flag = (
                await session.execute(
                    select(FeatureFlag).where(FeatureFlag.tenant_id.is_(None), FeatureFlag.flag_key == flag_key)
                )
            ).scalar_one_or_none()
            rows = FlagRows(global_row=_as_row(global_flag), tenant_row=_as_row(tenant_flag))
    # enterprise-gate: broad-except-ok reason=unreadable-flag-store-raises-lookup-error-never-reads-as-absent
    except Exception as exc:
        logger.warning("feature_flag_strict_lookup_failed", flag_key=flag_key, error_type=type(exc).__name__)
        raise FeatureFlagLookupError(f"feature flag {flag_key!r} could not be read") from exc

    _store_cache(cache_key, {"global": rows.global_row, "tenant": rows.tenant_row}, now + _CACHE_TTL_SECONDS)
    return rows


def clear_cache() -> None:
    """Testing helper — reset the in-process flag cache."""
    _cache.clear()
