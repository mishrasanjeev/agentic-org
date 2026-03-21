"""Idempotency enforcement via Redis."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from core.config import settings

IDEMPOTENCY_TTL = 86400  # 24 hours


class IdempotencyStore:
    """Store and retrieve idempotent results."""

    def __init__(self):
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def get(self, tenant_id: str, key: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        data = await self.redis.get(f"idempotency:{tenant_id}:{key}")
        if data:
            return json.loads(data)
        return None

    async def store(self, tenant_id: str, key: str, result: dict[str, Any]) -> None:
        if not self.redis:
            return
        await self.redis.setex(
            f"idempotency:{tenant_id}:{key}",
            IDEMPOTENCY_TTL,
            json.dumps(result, default=str),
        )

    async def close(self):
        if self.redis:
            await self.redis.close()
