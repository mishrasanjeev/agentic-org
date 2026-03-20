"""Token bucket rate limiter backed by Redis."""
from __future__ import annotations

import redis.asyncio as aioredis

from core.config import settings


class RateLimiter:
    """Per-connector token bucket rate limiter."""

    def __init__(self):
        self.redis: aioredis.Redis | None = None
        self._default_rpm = 60

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def check(self, tenant_id: str, connector_name: str, rpm: int | None = None) -> bool:
        """Return True if request is allowed, False if rate limited."""
        if not self.redis:
            return True

        limit = rpm or self._default_rpm
        key = f"ratelimit:{tenant_id}:{connector_name}"
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        results = await pipe.execute()
        count, ttl = results

        if ttl == -1:
            await self.redis.expire(key, 60)

        return count <= limit

    async def close(self):
        if self.redis:
            await self.redis.close()
