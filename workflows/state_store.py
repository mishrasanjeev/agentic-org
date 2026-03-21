"""Persist workflow state to Redis (and PostgreSQL)."""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from core.config import settings


class WorkflowStateStore:
    def __init__(self):
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def save(self, state: dict[str, Any]) -> None:
        if self.redis:
            await self.redis.set(f"wfstate:{state['id']}", json.dumps(state, default=str), ex=172800)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        data = await self.redis.get(f"wfstate:{run_id}")
        return json.loads(data) if data else None

    async def close(self):
        if self.redis:
            await self.redis.close()
