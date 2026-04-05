"""Redis-based usage counters per tenant.

Keys:
  usage:{tenant_id}:runs    — agent run count (monthly)
  usage:{tenant_id}:agents  — active agent count
  usage:{tenant_id}:storage — storage bytes used

Monthly reset is achieved via TTL on the :runs key (set to end-of-month).
Agent and storage counters are absolute, not TTL-based.
"""

from __future__ import annotations

import calendar
import os
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard Redis import
try:
    import redis as _redis
except ImportError:  # pragma: no cover
    _redis = None  # type: ignore[assignment]

_REDIS_URL = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")


def _get_redis():
    """Return a synchronous Redis client."""
    if _redis is None:
        raise RuntimeError("redis package is not installed — run: pip install redis")
    return _redis.from_url(_REDIS_URL, decode_responses=True)


def _key(tenant_id: str, metric: str) -> str:
    return f"usage:{tenant_id}:{metric}"


def _seconds_until_month_end() -> int:
    """Return seconds remaining until 00:00 UTC on the 1st of next month."""
    now = datetime.now(UTC)
    _, days_in_month = calendar.monthrange(now.year, now.month)
    eom = now.replace(day=days_in_month, hour=23, minute=59, second=59)
    diff = int((eom - now).total_seconds())
    return max(diff, 1)


# ── Increment / set ──────────────────────────────────────────────────


def increment_agent_runs(tenant_id: str, count: int = 1) -> int:
    """Increment agent run counter for the current month.

    Sets a TTL so the key auto-expires at month end.
    Returns the new total.
    """
    r = _get_redis()
    key = _key(tenant_id, "runs")
    new_val = r.incrby(key, count)
    # Set TTL only if it's a fresh key (no TTL yet)
    if r.ttl(key) == -1:
        r.expire(key, _seconds_until_month_end())
    return new_val


def set_agent_count(tenant_id: str, count: int) -> None:
    """Set the active agent count for a tenant (absolute value)."""
    r = _get_redis()
    r.set(_key(tenant_id, "agents"), count)


def increment_storage(tenant_id: str, bytes_delta: int) -> int:
    """Increment storage usage counter. Returns new total bytes."""
    r = _get_redis()
    return r.incrby(_key(tenant_id, "storage"), bytes_delta)


# ── Query ────────────────────────────────────────────────────────────


def get_usage(tenant_id: str) -> dict[str, Any]:
    """Return current usage for a tenant.

    Returns
    -------
    dict with agent_runs, agent_count, storage_bytes (all ints).
    """
    r = _get_redis()
    runs = r.get(_key(tenant_id, "runs"))
    agents = r.get(_key(tenant_id, "agents"))
    storage = r.get(_key(tenant_id, "storage"))
    return {
        "agent_runs": int(runs) if runs else 0,
        "agent_count": int(agents) if agents else 0,
        "storage_bytes": int(storage) if storage else 0,
    }


# ── Monthly reset (explicit) ────────────────────────────────────────


def reset_monthly(tenant_id: str) -> None:
    """Explicitly reset monthly counters (runs)."""
    r = _get_redis()
    r.delete(_key(tenant_id, "runs"))
    logger.info("usage_monthly_reset", tenant_id=tenant_id)
