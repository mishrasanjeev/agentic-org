"""Durable, tenant-scoped RPA execution history store (SEC-015).

Replaces the previous module-level ``_execution_history`` dict in
``api/v1/rpa.py`` which lost state on restart and produced inconsistent
behavior across multiple replicas.

Backing store: Redis LIST keyed by tenant id. The list is bounded to
``MAX_ENTRIES`` items (LTRIM after every append) and carries a TTL of
``RETENTION_DAYS`` so audit data eventually rolls off.

Resilience: when Redis is unavailable AND the environment is relaxed
(``local``, ``dev``, ``development``, ``test``, ``ci``), the store
falls back to a process-local dict so unit tests + dev workflows keep
working. In strict envs (``production``, ``staging``, ``preview``)
Redis unavailability is logged but does not silently degrade — the
append path returns ``False`` so callers can surface the gap.

Tenant isolation: every read + write is keyed by ``tenant_id`` at the
storage layer. The store has no operation that returns rows across
tenants. Tested by ``test_security_pr_h_rpa_history_durability.py``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Tunable via env so ops can change retention without redeploying.
RETENTION_DAYS = int(os.environ.get("AGENTICORG_RPA_HISTORY_RETENTION_DAYS", "90"))
MAX_ENTRIES = int(os.environ.get("AGENTICORG_RPA_HISTORY_MAX_ENTRIES", "1000"))

# Process-local fallback used only in relaxed envs (local/dev/test/ci).
# Keyed by tenant_id → list[execution_dict] in newest-first order.
_FALLBACK: dict[str, list[dict[str, Any]]] = {}

_RELAXED_ENVS = frozenset({"local", "dev", "development", "test", "ci"})


def _is_relaxed_env() -> bool:
    return os.environ.get("AGENTICORG_ENV", "development").lower() in _RELAXED_ENVS


def _key(tenant_id: str) -> str:
    return f"rpa:history:{tenant_id}"


async def append(
    tenant_id: str,
    execution: dict[str, Any],
    *,
    retention_days: int | None = None,
    max_entries: int | None = None,
) -> bool:
    """Persist one execution record for ``tenant_id``.

    Returns True when the record was durably stored (Redis), False
    when only the process-local fallback was used. Strict envs treat
    a False return as a degradation that callers should log.
    """
    retention = retention_days if retention_days is not None else RETENTION_DAYS
    cap = max_entries if max_entries is not None else MAX_ENTRIES

    payload = json.dumps(execution, default=str, sort_keys=True)
    key = _key(tenant_id)

    redis = await _get_redis()
    if redis is not None:
        try:
            # LPUSH so newest entries are at the head; reads use
            # LRANGE 0 N which returns newest-first.
            await redis.lpush(key, payload)
            # Trim to the most recent cap entries to bound memory.
            await redis.ltrim(key, 0, cap - 1)
            # Renew TTL on every append so an active tenant's history
            # doesn't roll off mid-use; idle tenants do roll off
            # after retention_days of no activity.
            await redis.expire(key, retention * 24 * 3600)
            return True
        except Exception as exc:  # noqa: BLE001 — best effort; log and fall through
            logger.warning(
                "rpa_history_redis_append_failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )

    # Fallback path. Strict envs warn so ops sees the gap.
    if not _is_relaxed_env():
        logger.warning(
            "rpa_history_persistence_degraded",
            extra={
                "tenant_id": tenant_id,
                "reason": (
                    "Redis unavailable in strict env; using process-local "
                    "fallback. Audit history will not survive restart."
                ),
            },
        )
    bucket = _FALLBACK.setdefault(tenant_id, [])
    bucket.insert(0, execution)
    if len(bucket) > cap:
        del bucket[cap:]
    return False


async def list_history(
    tenant_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return newest-first execution history for ``tenant_id``.

    Always tenant-scoped: there is no path that crosses tenants. When
    Redis is unavailable, falls through to the process-local fallback.
    """
    if limit <= 0:
        return []
    cap = min(limit, MAX_ENTRIES)
    key = _key(tenant_id)

    redis = await _get_redis()
    if redis is not None:
        try:
            raw_entries = await redis.lrange(key, 0, cap - 1)
            entries: list[dict[str, Any]] = []
            for raw in raw_entries:
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    # A malformed entry shouldn't blank the rest of the
                    # tenant's history.
                    logger.warning(
                        "rpa_history_redis_decode_failed",
                        extra={"tenant_id": tenant_id, "raw": raw[:200]},
                    )
            return entries
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "rpa_history_redis_read_failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )

    return list(_FALLBACK.get(tenant_id, []))[:cap]


async def _get_redis():
    """Lazy-import the shared async-Redis client.

    Imported at call time so unit tests can patch
    ``core.async_redis.get_async_redis`` per-test.
    """
    try:
        from core.async_redis import get_async_redis  # noqa: PLC0415

        return await get_async_redis()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rpa_history_redis_import_failed", extra={"error": str(exc)})
        return None


def _reset_fallback_for_tests() -> None:
    """Test-only helper: clear the process-local fallback bucket."""
    _FALLBACK.clear()
