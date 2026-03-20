"""Redis-backed token pool for agent tokens."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis

from core.config import settings
from auth.grantex import grantex_client


class TokenPool:
    """Cache and manage agent tokens in Redis."""

    def __init__(self):
        self.redis: aioredis.Redis | None = None
        self._refresh_tasks: dict[str, asyncio.Task] = {}

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        # Subscribe to revocation channel
        pubsub = self.redis.pubsub()
        await pubsub.subscribe("agentflow:token:revoke")
        asyncio.create_task(self._listen_revocations(pubsub))

    async def get_token(self, agent_id: str) -> str | None:
        """Get cached token for an agent."""
        if not self.redis:
            return None
        data = await self.redis.get(f"agent:{agent_id}:token")
        if data:
            token_data = json.loads(data)
            return token_data.get("access_token")
        return None

    async def store_token(self, agent_id: str, token_data: dict[str, Any]) -> None:
        """Store token with TTL matching token expiry."""
        if not self.redis:
            return
        ttl = token_data.get("expires_in", 3600)
        await self.redis.setex(
            f"agent:{agent_id}:token",
            ttl,
            json.dumps(token_data),
        )
        # Schedule refresh at 50% TTL
        self._schedule_refresh(agent_id, ttl // 2)

    async def revoke_token(self, agent_id: str) -> None:
        """Revoke token and broadcast to all pool nodes."""
        if not self.redis:
            return
        await self.redis.delete(f"agent:{agent_id}:token")
        await self.redis.publish("agentflow:token:revoke", agent_id)
        if agent_id in self._refresh_tasks:
            self._refresh_tasks[agent_id].cancel()
            del self._refresh_tasks[agent_id]

    def _schedule_refresh(self, agent_id: str, delay: int) -> None:
        if agent_id in self._refresh_tasks:
            self._refresh_tasks[agent_id].cancel()
        self._refresh_tasks[agent_id] = asyncio.create_task(
            self._refresh_after(agent_id, delay)
        )

    async def _refresh_after(self, agent_id: str, delay: int) -> None:
        await asyncio.sleep(delay)
        # Fetch agent config and refresh token
        # (In production, load agent_type and scopes from DB)
        # For now, just delete expired token
        await self.redis.delete(f"agent:{agent_id}:token")

    async def _listen_revocations(self, pubsub) -> None:
        async for message in pubsub.listen():
            if message["type"] == "message":
                agent_id = message["data"]
                await self.redis.delete(f"agent:{agent_id}:token")

    async def close(self):
        for task in self._refresh_tasks.values():
            task.cancel()
        if self.redis:
            await self.redis.close()


token_pool = TokenPool()
