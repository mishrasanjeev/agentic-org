"""Token bucket rate limiter backed by Redis."""

from __future__ import annotations

import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from core.config import settings

# Lua script implementing an atomic token bucket algorithm.
#
# KEYS[1] = bucket key  (e.g. "ratelimit:{tenant}:{connector}")
# ARGV[1] = capacity     (max tokens, equal to RPM)
# ARGV[2] = refill_rate  (tokens added per second, i.e. RPM / 60)
# ARGV[3] = now           (current timestamp as a float, seconds)
#
# The bucket state is stored as a Redis hash with two fields:
#   tokens     – current number of available tokens (float)
#   last_refill – timestamp of last refill (float)
#
# Returns: {allowed (0|1), remaining_tokens, retry_after_seconds}
_TOKEN_BUCKET_LUA = """
local key         = KEYS[1]
local capacity    = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now         = tonumber(ARGV[3])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens      = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    -- First request: initialise the bucket at full capacity minus this request.
    tokens      = capacity
    last_refill = now
end

-- Add tokens accrued since last refill, capped at capacity.
local elapsed = math.max(now - last_refill, 0)
tokens = math.min(capacity, tokens + elapsed * refill_rate)
last_refill = now

-- Attempt to consume one token.
if tokens >= 1 then
    tokens = tokens - 1
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(last_refill))
    -- Expire the key after 2x the full-bucket refill window to avoid leaking memory.
    local ttl = math.ceil(capacity / refill_rate) * 2
    if ttl < 120 then ttl = 120 end
    redis.call('EXPIRE', key, ttl)
    return {1, tostring(tokens), "0"}
else
    -- Denied.  Calculate how long until one token is available.
    local deficit = 1 - tokens
    local retry_after = deficit / refill_rate
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(last_refill))
    local ttl = math.ceil(capacity / refill_rate) * 2
    if ttl < 120 then ttl = 120 end
    redis.call('EXPIRE', key, ttl)
    return {0, tostring(tokens), tostring(retry_after)}
end
"""


@dataclass
class RateLimitResult:
    """Result of a rate-limit check."""

    allowed: bool
    remaining: float
    retry_after_seconds: float


class RateLimiter:
    """Per-connector token bucket rate limiter."""

    def __init__(self):
        self.redis: aioredis.Redis | None = None
        self._default_rpm = 60
        self._script_sha: str | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        # Pre-load the Lua script into Redis so subsequent calls use EVALSHA.
        self._script_sha = await self.redis.script_load(_TOKEN_BUCKET_LUA)

    async def check(
        self, tenant_id: str, connector_name: str, rpm: int | None = None
    ) -> RateLimitResult:
        """Check whether a request is allowed under the token bucket.

        Returns a ``RateLimitResult`` with ``allowed``, ``remaining``
        tokens, and ``retry_after_seconds`` (> 0 when denied).

        If Redis is unavailable the request is optimistically allowed so
        that transient infrastructure failures do not block all traffic.
        """
        if not self.redis:
            return RateLimitResult(allowed=True, remaining=0, retry_after_seconds=0)

        limit = rpm or self._default_rpm
        capacity = limit
        refill_rate = limit / 60.0  # tokens per second
        now = time.time()
        key = f"ratelimit:{tenant_id}:{connector_name}"

        try:
            result = await self.redis.evalsha(
                self._script_sha,
                1,  # number of keys
                key,
                str(capacity),
                str(refill_rate),
                str(now),
            )
        except aioredis.exceptions.NoScriptError:
            # Script was evicted; reload and retry once.
            self._script_sha = await self.redis.script_load(_TOKEN_BUCKET_LUA)
            result = await self.redis.evalsha(
                self._script_sha,
                1,
                key,
                str(capacity),
                str(refill_rate),
                str(now),
            )

        allowed = int(result[0]) == 1
        remaining = float(result[1])
        retry_after = float(result[2])

        return RateLimitResult(
            allowed=allowed,
            remaining=max(remaining, 0),
            retry_after_seconds=round(retry_after, 3) if not allowed else 0,
        )

    async def close(self):
        if self.redis:
            await self.redis.close()
