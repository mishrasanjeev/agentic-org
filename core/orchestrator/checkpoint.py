"""Checkpoint manager — save/restore workflow state."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from core.config import settings


class CheckpointManager:
    def __init__(self):
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def save(self, workflow_run_id: str, state: dict[str, Any]) -> None:
        if self.redis:
            await self.redis.set(
                f"checkpoint:{workflow_run_id}",
                json.dumps(state, default=str),
                ex=86400,
            )

    async def load(self, workflow_run_id: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        data = await self.redis.get(f"checkpoint:{workflow_run_id}")
        return json.loads(data) if data else None

    async def close(self):
        if self.redis:
            await self.redis.close()
