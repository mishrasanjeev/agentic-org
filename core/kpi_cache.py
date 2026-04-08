"""KPI caching layer -- Redis (hot) + PostgreSQL (historical).

Redis is the primary cache for sub-second dashboard reads.
When Redis is unavailable the layer falls back to the kpi_cache
PostgreSQL table so dashboards never fully break.

Every set() writes to *both* Redis and PostgreSQL so we always
have a historical record for trend analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from core.config import settings
from core.database import async_session_factory

logger = logging.getLogger(__name__)

# Redis key pattern: kpi:{tenant_id}:{role}:{metric_name}
_KEY_PREFIX = "kpi"


def _redis_key(tenant_id: str, role: str, metric_name: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{role}:{metric_name}"


def _role_pattern(tenant_id: str, role: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{role}:*"


async def _get_redis():
    """Lazily connect to Redis; returns None if unavailable."""
    try:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception:
        logger.debug("Redis unavailable, falling back to PostgreSQL")
        return None


class KPICache:
    """Redis-backed KPI cache with PostgreSQL fallback."""

    async def get(
        self, tenant_id: str, role: str, metric_name: str
    ) -> dict | None:
        """Get a single cached KPI metric.

        Tries Redis first, then falls back to the kpi_cache table.
        """
        redis = await _get_redis()
        if redis:
            try:
                raw = await redis.get(_redis_key(tenant_id, role, metric_name))
                if raw:
                    return json.loads(raw)
            except Exception:
                logger.debug("Redis GET failed, falling back to PG")
            finally:
                await redis.aclose()

        # PostgreSQL fallback
        return await self._pg_get(tenant_id, role, metric_name)

    async def set(
        self,
        tenant_id: str,
        role: str,
        metric_name: str,
        value: dict,
        ttl: int = 3600,
        source: str = "agent",
    ) -> None:
        """Store a KPI metric in Redis + PostgreSQL.

        Args:
            tenant_id: Tenant UUID string.
            role: CxO role (ceo, cfo, cmo, chro, coo, cbo).
            metric_name: Metric identifier.
            value: Metric payload (dict).
            ttl: Time-to-live in seconds (Redis only).
            source: Where the metric originated (agent, connector, manual).
        """
        now = datetime.now(datetime.UTC).isoformat()
        envelope = {
            "value": value,
            "source": source,
            "computed_at": now,
            "ttl_seconds": ttl,
            "stale": False,
        }

        # Write to Redis (best-effort)
        redis = await _get_redis()
        if redis:
            try:
                await redis.set(
                    _redis_key(tenant_id, role, metric_name),
                    json.dumps(envelope),
                    ex=ttl,
                )
            except Exception:
                logger.debug("Redis SET failed")
            finally:
                await redis.aclose()

        # Always write to PostgreSQL for historical record
        await self._pg_upsert(tenant_id, role, metric_name, value, ttl, source)

    async def get_all_for_role(
        self, tenant_id: str, role: str
    ) -> dict[str, dict]:
        """Get all cached KPI metrics for a given role.

        Returns a dict keyed by metric_name.
        """
        result: dict[str, dict] = {}

        redis = await _get_redis()
        if redis:
            try:
                keys = []
                async for key in redis.scan_iter(
                    match=_role_pattern(tenant_id, role), count=100
                ):
                    keys.append(key)
                if keys:
                    values = await redis.mget(keys)
                    for key, raw in zip(keys, values, strict=False):
                        if raw:
                            # Extract metric_name from key pattern
                            metric_name = key.split(":")[-1]
                            result[metric_name] = json.loads(raw)
                    if result:
                        return result
            except Exception:
                logger.debug("Redis SCAN failed, falling back to PG")
            finally:
                await redis.aclose()

        # PostgreSQL fallback
        return await self._pg_get_all_for_role(tenant_id, role)

    async def invalidate(
        self, tenant_id: str, role: str, metric_name: str | None = None
    ) -> None:
        """Invalidate cached KPI metrics.

        If metric_name is None, invalidates all metrics for the role.
        """
        redis = await _get_redis()
        if redis:
            try:
                if metric_name:
                    await redis.delete(
                        _redis_key(tenant_id, role, metric_name)
                    )
                else:
                    keys = []
                    async for key in redis.scan_iter(
                        match=_role_pattern(tenant_id, role), count=100
                    ):
                        keys.append(key)
                    if keys:
                        await redis.delete(*keys)
            except Exception:
                logger.debug("Redis invalidate failed")
            finally:
                await redis.aclose()

        # Mark as stale in PostgreSQL
        await self._pg_mark_stale(tenant_id, role, metric_name)

    async def is_stale(
        self, tenant_id: str, role: str, metric_name: str
    ) -> bool:
        """Check if a KPI metric is stale (expired TTL or marked stale)."""
        redis = await _get_redis()
        if redis:
            try:
                ttl_remaining = await redis.ttl(
                    _redis_key(tenant_id, role, metric_name)
                )
                if ttl_remaining > 0:
                    return False
                if ttl_remaining == -2:
                    # Key does not exist
                    return True
            except Exception:
                logger.debug("Redis TTL check failed")
            finally:
                await redis.aclose()

        # Check PostgreSQL
        return await self._pg_is_stale(tenant_id, role, metric_name)

    # ── PostgreSQL helpers ─────────────────────────────────────────────

    async def _pg_get(
        self, tenant_id: str, role: str, metric_name: str
    ) -> dict | None:
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT metric_value, source, computed_at, ttl_seconds, stale "
                        "FROM kpi_cache "
                        "WHERE tenant_id = :tid AND role = :role "
                        "  AND metric_name = :metric "
                        "ORDER BY computed_at DESC LIMIT 1"
                    ),
                    {
                        "tid": tenant_id,
                        "role": role,
                        "metric": metric_name,
                    },
                )
            ).first()
            if not row:
                return None
            computed_at = row.computed_at
            if hasattr(computed_at, "isoformat"):
                computed_at = computed_at.isoformat()
            return {
                "value": row.metric_value,
                "source": row.source,
                "computed_at": computed_at,
                "ttl_seconds": row.ttl_seconds,
                "stale": row.stale,
            }

    async def _pg_get_all_for_role(
        self, tenant_id: str, role: str
    ) -> dict[str, dict]:
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT ON (metric_name) "
                        "  metric_name, metric_value, source, computed_at, "
                        "  ttl_seconds, stale "
                        "FROM kpi_cache "
                        "WHERE tenant_id = :tid AND role = :role "
                        "ORDER BY metric_name, computed_at DESC"
                    ),
                    {"tid": tenant_id, "role": role},
                )
            ).all()
            result: dict[str, dict] = {}
            for row in rows:
                computed_at = row.computed_at
                if hasattr(computed_at, "isoformat"):
                    computed_at = computed_at.isoformat()
                result[row.metric_name] = {
                    "value": row.metric_value,
                    "source": row.source,
                    "computed_at": computed_at,
                    "ttl_seconds": row.ttl_seconds,
                    "stale": row.stale,
                }
            return result

    async def _pg_upsert(
        self,
        tenant_id: str,
        role: str,
        metric_name: str,
        value: dict,
        ttl: int,
        source: str,
    ) -> None:
        async with async_session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO kpi_cache "
                    "  (tenant_id, role, metric_name, metric_value, source, "
                    "   ttl_seconds, stale, computed_at) "
                    "VALUES (:tid, :role, :metric, :val::jsonb, :source, "
                    "        :ttl, FALSE, NOW())"
                ),
                {
                    "tid": tenant_id,
                    "role": role,
                    "metric": metric_name,
                    "val": json.dumps(value),
                    "source": source,
                    "ttl": ttl,
                },
            )
            await session.commit()

    async def _pg_mark_stale(
        self, tenant_id: str, role: str, metric_name: str | None
    ) -> None:
        async with async_session_factory() as session:
            if metric_name:
                await session.execute(
                    text(
                        "UPDATE kpi_cache SET stale = TRUE "
                        "WHERE tenant_id = :tid AND role = :role "
                        "  AND metric_name = :metric"
                    ),
                    {
                        "tid": tenant_id,
                        "role": role,
                        "metric": metric_name,
                    },
                )
            else:
                await session.execute(
                    text(
                        "UPDATE kpi_cache SET stale = TRUE "
                        "WHERE tenant_id = :tid AND role = :role"
                    ),
                    {"tid": tenant_id, "role": role},
                )
            await session.commit()

    async def _pg_is_stale(
        self, tenant_id: str, role: str, metric_name: str
    ) -> bool:
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT stale, computed_at, ttl_seconds "
                        "FROM kpi_cache "
                        "WHERE tenant_id = :tid AND role = :role "
                        "  AND metric_name = :metric "
                        "ORDER BY computed_at DESC LIMIT 1"
                    ),
                    {
                        "tid": tenant_id,
                        "role": role,
                        "metric": metric_name,
                    },
                )
            ).first()
            if not row:
                return True
            if row.stale:
                return True
            # Check if TTL has expired
            now = datetime.now(datetime.UTC)
            computed = row.computed_at
            if computed.tzinfo is None:
                computed = computed.replace(tzinfo=datetime.UTC)
            return now > computed + timedelta(seconds=row.ttl_seconds)
