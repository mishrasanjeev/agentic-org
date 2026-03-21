"""Circuit breaker pattern — Redis-backed, per-connector."""

from __future__ import annotations

import time
from enum import StrEnum

import redis.asyncio as aioredis

from core.config import settings


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-connector circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def can_execute(self, connector_name: str) -> bool:
        """Check if the circuit allows a request."""
        if not self.redis:
            return True
        state = await self._get_state(connector_name)
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            last_fail = float(await self.redis.get(f"cb:{connector_name}:last_fail") or 0)
            if time.time() - last_fail > self.recovery_timeout:
                await self._set_state(connector_name, CircuitState.HALF_OPEN)
                return True
            return False
        # Half open — allow limited requests
        return True

    async def record_success(self, connector_name: str) -> None:
        if not self.redis:
            return
        await self.redis.set(f"cb:{connector_name}:failures", 0)
        await self._set_state(connector_name, CircuitState.CLOSED)

    async def record_failure(self, connector_name: str) -> None:
        if not self.redis:
            return
        failures = await self.redis.incr(f"cb:{connector_name}:failures")
        await self.redis.set(f"cb:{connector_name}:last_fail", str(time.time()))
        if failures >= self.failure_threshold:
            await self._set_state(connector_name, CircuitState.OPEN)

    async def _get_state(self, name: str) -> CircuitState:
        if not self.redis:
            return CircuitState.CLOSED
        state = await self.redis.get(f"cb:{name}:state")
        return CircuitState(state) if state else CircuitState.CLOSED

    async def _set_state(self, name: str, state: CircuitState) -> None:
        if self.redis:
            await self.redis.set(f"cb:{name}:state", state.value)

    async def close(self):
        if self.redis:
            await self.redis.close()
