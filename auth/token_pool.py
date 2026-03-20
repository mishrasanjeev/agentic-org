"""Redis-backed token pool for agent tokens."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

import structlog
import redis.asyncio as aioredis

from core.config import settings
from auth.grantex import grantex_client

logger = structlog.get_logger()

# Type alias for the callback that resolves agent config (agent_type, scopes)
# given an agent_id. Users must register a callback via set_agent_config_resolver().
AgentConfigResolver = Callable[[str], Awaitable[dict[str, Any]]]


class TokenPool:
    """Cache and manage agent tokens in Redis."""

    def __init__(self):
        self.redis: aioredis.Redis | None = None
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._agent_config_resolver: AgentConfigResolver | None = None

    def set_agent_config_resolver(self, resolver: AgentConfigResolver) -> None:
        """Register a callback to look up agent config (agent_type, scopes) by agent_id.

        The resolver must return a dict with at least:
            {"agent_type": str, "scopes": list[str]}
        It may also include "token_ttl": int (seconds).
        """
        self._agent_config_resolver = resolver

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
        """Wait for *delay* seconds, then proactively refresh the agent token.

        Loads agent config via the registered resolver, requests a new
        delegated token from Grantex, and stores it back in the pool.
        On any failure the error is logged and the stale token is removed
        so subsequent callers will obtain a fresh one on demand.
        """
        try:
            await asyncio.sleep(delay)

            # Resolve agent configuration (agent_type, scopes)
            if self._agent_config_resolver is None:
                logger.warning(
                    "token_refresh_skipped",
                    agent_id=agent_id,
                    reason="no agent_config_resolver registered",
                )
                # Fall back to deleting the stale token so the next caller
                # is forced to obtain a fresh one through the normal flow.
                if self.redis:
                    await self.redis.delete(f"agent:{agent_id}:token")
                return

            agent_cfg = await self._agent_config_resolver(agent_id)
            agent_type: str = agent_cfg["agent_type"]
            scopes: list[str] = agent_cfg["scopes"]
            ttl: int = agent_cfg.get("token_ttl", 3600)

            # Request a new delegated token from Grantex
            token_data = await grantex_client.delegate_agent_token(
                agent_id=agent_id,
                agent_type=agent_type,
                scopes=scopes,
                ttl=ttl,
            )

            # Store refreshed token (this also schedules the next refresh)
            await self.store_token(agent_id, token_data)

            logger.info(
                "token_refreshed",
                agent_id=agent_id,
                agent_type=agent_type,
                expires_in=token_data.get("expires_in"),
            )

        except asyncio.CancelledError:
            # Task was cancelled (e.g. token revoked or pool shutting down).
            # Re-raise so asyncio can clean up properly.
            raise

        except Exception:
            logger.exception(
                "token_refresh_failed",
                agent_id=agent_id,
            )
            # Remove the stale token so the next request triggers a fresh
            # acquisition rather than using an expired credential.
            try:
                if self.redis:
                    await self.redis.delete(f"agent:{agent_id}:token")
            except Exception:
                logger.exception(
                    "token_cleanup_after_refresh_failure_failed",
                    agent_id=agent_id,
                )

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
