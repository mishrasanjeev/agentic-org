"""Shared async Redis client — connection pool reused across requests.

Usage in async handlers:
    from core.async_redis import get_async_redis
    r = await get_async_redis()
    if r:
        await r.setex("key", 60, "value")
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from core.config import redis_url_from_env

logger = logging.getLogger(__name__)

_pool: aioredis.Redis | None = None


async def get_async_redis() -> aioredis.Redis | None:
    """Return a shared async Redis client (lazy init, single pool per process)."""
    global _pool
    if _pool is not None:
        return _pool
    try:
        url = redis_url_from_env(default_db=0)
        _pool = aioredis.from_url(
            url,
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        await _pool.ping()
        return _pool
    except Exception:
        logger.warning("async_redis: Redis unavailable")
        _pool = None
        return None
