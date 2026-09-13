"""Redis-based usage counters per tenant (async, on the shared pool).

Keys:
  usage:{tenant_id}:runs    — agent run count (monthly)
  usage:{tenant_id}:storage — storage bytes used

Monthly reset is achieved via TTL on the :runs key (set to end-of-month).
Storage is absolute, not TTL-based. The active agent count is not a
counter: ``/billing/usage`` computes it from the ``agents`` table so it can
never drift from reality.

All counter functions use ``core.async_redis.get_async_redis`` (one pool
per process). The only synchronous client left in this module is
``sync_redis_client`` — a cached singleton for code that is synchronous by
design and runs under ``asyncio.to_thread`` (the Plural order map, the
Stripe customer-id cache, the public status page probe).
"""

from __future__ import annotations

import calendar
import threading
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# Guard Redis import
try:
    import redis as _redis
except ImportError:  # pragma: no cover
    _redis = None  # type: ignore[assignment]

_sync_lock = threading.Lock()
_sync_client: Any = None


def sync_redis_client():
    """Return the process-wide synchronous Redis client (thread-run code only)."""
    global _sync_client
    if _redis is None:
        raise RuntimeError("redis package is not installed — run: pip install redis")
    with _sync_lock:
        if _sync_client is None:
            from core.config import redis_socket_timeout_kwargs, redis_url_from_env

            _sync_client = _redis.from_url(
                redis_url_from_env(default_db=0),
                decode_responses=True,
                **redis_socket_timeout_kwargs(),
            )
        return _sync_client


# Backwards-compatible alias for out-of-tree callers (api/v1/status.py).
_get_redis = sync_redis_client


def _key(tenant_id: str, metric: str) -> str:
    return f"usage:{tenant_id}:{metric}"


def _seconds_until_month_end() -> int:
    """Return seconds remaining until 00:00 UTC on the 1st of next month."""
    now = datetime.now(UTC)
    _, days_in_month = calendar.monthrange(now.year, now.month)
    eom = now.replace(day=days_in_month, hour=23, minute=59, second=59)
    diff = int((eom - now).total_seconds())
    return max(diff, 1)


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        value = value.decode()
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ── Increment / set ──────────────────────────────────────────────────


async def increment_agent_runs(tenant_id: str, count: int = 1) -> int:
    """Increment agent run counter for the current month.

    Sets a TTL so the key auto-expires at month end. Returns the new total,
    or ``-1`` when Redis is unavailable (metering is best-effort; the
    caller must never fail a completed run because of it).
    """
    from core.async_redis import get_async_redis

    r = await get_async_redis()
    if r is None:
        logger.warning("usage_metering_unavailable", metric="runs")
        return -1
    key = _key(tenant_id, "runs")
    new_val = await r.incrby(key, count)
    # Set TTL only if it's a fresh key (no TTL yet)
    if await r.ttl(key) == -1:
        await r.expire(key, _seconds_until_month_end())
    return int(new_val)


async def increment_storage(tenant_id: str, bytes_delta: int) -> int:
    """Increment storage usage counter. Returns new total bytes (or -1)."""
    from core.async_redis import get_async_redis

    r = await get_async_redis()
    if r is None:
        logger.warning("usage_metering_unavailable", metric="storage")
        return -1
    return int(await r.incrby(_key(tenant_id, "storage"), bytes_delta))


# ── Query ────────────────────────────────────────────────────────────


async def count_active_agents(tenant_id: str) -> int:
    """Count the tenant's live agents from the database (never a cached counter)."""
    import uuid

    from sqlalchemy import text

    from core.database import get_tenant_session

    try:
        tid = uuid.UUID(str(tenant_id))
    except ValueError:
        return 0
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM agents "
                "WHERE tenant_id = CAST(:tid AS uuid) AND status <> 'retired'"
            ),
            {"tid": str(tid)},
        )
        return int(result.scalar_one() or 0)


async def get_usage(tenant_id: str, *, include_agent_count: bool = True) -> dict[str, Any]:
    """Return current usage for a tenant.

    Returns
    -------
    dict with agent_runs, agent_count, storage_bytes (all ints).
    """
    from core.async_redis import get_async_redis

    runs = storage = None
    r = await get_async_redis()
    if r is not None:
        runs = await r.get(_key(tenant_id, "runs"))
        storage = await r.get(_key(tenant_id, "storage"))

    agent_count = 0
    if include_agent_count:
        try:
            agent_count = await count_active_agents(tenant_id)
        # enterprise-gate: broad-except-ok reason=usage-read-degrades-to-zero-agents-never-raises-to-dashboard
        except Exception:
            logger.warning("usage_agent_count_unavailable", tenant_id=tenant_id)

    return {
        "agent_runs": _to_int(runs),
        "agent_count": agent_count,
        "storage_bytes": _to_int(storage),
    }


# ── Monthly reset (explicit) ────────────────────────────────────────


async def reset_monthly(tenant_id: str) -> None:
    """Explicitly reset monthly counters (runs)."""
    from core.async_redis import get_async_redis

    r = await get_async_redis()
    if r is None:
        return
    await r.delete(_key(tenant_id, "runs"))
    logger.info("usage_monthly_reset", tenant_id=tenant_id)
