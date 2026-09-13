"""Idempotency enforcement via Redis."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from core.config import settings

IDEMPOTENCY_TTL = 86400  # 24 hours
# A reservation only has to outlive one in-flight tool call. If the worker
# dies mid-call the key expires and the next retry can re-run the tool.
RESERVATION_TTL = 300  # 5 minutes
_PENDING_MARKER = {"__idempotency_pending__": True}


class IdempotencyStore:
    """Store and retrieve idempotent results."""

    def __init__(self):
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    @staticmethod
    def _key(tenant_id: str, key: str) -> str:
        return f"idempotency:{tenant_id}:{key}"

    async def get(self, tenant_id: str, key: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        data = await self.redis.get(self._key(tenant_id, key))
        if data:
            return json.loads(data)
        return None

    async def reserve(self, tenant_id: str, key: str) -> tuple[bool, dict[str, Any] | None]:
        """Atomically claim ``key`` for one in-flight execution (``SET NX``).

        Returns ``(acquired, cached)``:

        * ``(True, None)`` — the caller owns the key and must ``store`` a
          result or ``release`` it.
        * ``(False, result)`` — a completed result already exists; return it.
        * ``(False, None)`` — another call is in flight for the same key.

        A plain get-then-store check let two concurrent calls with the same
        key both pass the "not cached yet" test and execute the side effect
        twice. The reservation closes that window.
        """
        if not self.redis:
            return True, None
        redis_key = self._key(tenant_id, key)
        acquired = await self.redis.set(
            redis_key,
            json.dumps(_PENDING_MARKER),
            ex=RESERVATION_TTL,
            nx=True,
        )
        if acquired:
            return True, None
        data = await self.redis.get(redis_key)
        if not data:
            # Reservation expired between SET NX and GET; treat as in flight
            # rather than executing twice.
            return False, None
        cached = json.loads(data)
        if isinstance(cached, dict) and cached.get("__idempotency_pending__"):
            return False, None
        return False, cached

    async def release(self, tenant_id: str, key: str) -> None:
        """Drop a reservation whose execution did not produce a result."""
        if not self.redis:
            return
        await self.redis.delete(self._key(tenant_id, key))

    async def store(self, tenant_id: str, key: str, result: dict[str, Any]) -> None:
        if not self.redis:
            return
        await self.redis.setex(
            self._key(tenant_id, key),
            IDEMPOTENCY_TTL,
            json.dumps(result, default=str),
        )

    async def close(self):
        if self.redis:
            await self.redis.close()
