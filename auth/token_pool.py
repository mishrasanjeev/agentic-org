"""Redis-backed token pool for agent tokens.

Besides caching and refreshing agent tokens, the pool obtains the *first*
grant token for an agent run (``get_run_grant_token``): it delegates a
short-lived grant to the agent's registered Grantex agent from the platform
root grant, scoped to the agent's registered scopes, and caches it per tenant,
agent and scope set.

Lifetime and bounds of delegated run grants:

* A cached grant is handed out only while it has at least
  ``min_remaining_seconds(ttl)`` (the larger of 120 s and 10% of the requested
  lifetime) left; long runs call ``get_run_grant_token`` again before that.
* Grants are not revoked by the pool; they expire (default 15 minutes).
  Revoking the root grant on Grantex cascades to every delegated grant.
* At most one grant is minted per (tenant, agent, scope set) per process while
  a usable one exists: Redis is shared across processes, a bounded in-process
  cache covers Redis being unavailable, and a per-key lock stops concurrent
  runs from minting in parallel.
* Redis is reached through one lazily created client per event loop (the API
  loop, or a Celery worker's persistent loop), so nothing connects at import
  or worker start.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
import structlog

from auth.grantex import grantex_client
from core.config import external_keys, settings

logger = structlog.get_logger()

# Type alias for the callback that resolves agent config (agent_type, scopes)
# given an agent_id. Users must register a callback via set_agent_config_resolver().
AgentConfigResolver = Callable[[str], Awaitable[dict[str, Any]]]

# A run grant is handed out only while it has at least this long to live.
RUN_GRANT_MIN_REMAINING_FLOOR_SECONDS = 120
RUN_GRANT_MIN_REMAINING_FRACTION = 0.1
_LOCAL_CACHE_MAX = 1_024
_LOCKS_MAX = 4_096


def min_remaining_seconds(ttl_seconds: int) -> int:
    """Minimum lifetime a run grant must have left to be handed out."""
    return max(RUN_GRANT_MIN_REMAINING_FLOOR_SECONDS, math.ceil(RUN_GRANT_MIN_REMAINING_FRACTION * ttl_seconds))


class GrantMintError(RuntimeError):
    """No run grant could be obtained. ``sub_reason`` is a fixed short code."""

    def __init__(self, sub_reason: str, message: str) -> None:
        super().__init__(message)
        self.sub_reason = sub_reason


@dataclass(frozen=True)
class RunGrantToken:
    token: str
    grant_id: str
    expires_at: float | None  # epoch seconds; None when Grantex did not say
    source: str  # "pool_cache" | "minted"
    ttl_seconds: int = 0  # lifetime that was requested when it was minted

    def __repr__(self) -> str:  # never print the token
        return f"RunGrantToken(grant_id={self.grant_id!r}, expires_at={self.expires_at!r}, source={self.source!r})"

    def usable(self, now: float | None = None) -> bool:
        """True while the grant has at least the minimum remaining lifetime."""
        if self.expires_at is None:
            return False
        remaining = self.expires_at - (time.time() if now is None else now)
        return remaining >= min_remaining_seconds(self.ttl_seconds)


def _default_grantex_client() -> Any:
    from core.langgraph.grantex_auth import get_grantex_client

    return get_grantex_client()


def _parse_expiry(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class TokenPool:
    """Cache and manage agent tokens in Redis."""

    def __init__(self, grantex_client_factory: Callable[[], Any] | None = None):
        # Set explicitly (tests) or by ``init``; otherwise ``_redis_client``
        # creates one client per event loop on first use.
        self.redis: aioredis.Redis | None = None
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._agent_config_resolver: AgentConfigResolver | None = None
        self._grantex_client_factory = grantex_client_factory or _default_grantex_client
        self.lazy_redis = True
        # enterprise-gate: process-local-ok reason=one-lazy-redis-client-per-event-loop-and-pid
        self._loop_clients: dict[tuple[int, int], aioredis.Redis] = {}
        # enterprise-gate: process-local-ok reason=bounded-in-process-run-grant-cache-used-when-redis-unavailable
        self._local_grants: OrderedDict[str, RunGrantToken] = OrderedDict()
        # enterprise-gate: process-local-ok reason=bounded-per-key-mint-locks-prevent-parallel-minting
        self._mint_locks: OrderedDict[tuple[int, str], asyncio.Lock] = OrderedDict()
        self._revocation_task: asyncio.Task | None = None

    def set_agent_config_resolver(self, resolver: AgentConfigResolver) -> None:
        """Register a callback to look up agent config (agent_type, scopes) by agent_id.

        The resolver must return a dict with at least:
            {"agent_type": str, "scopes": list[str]}
        It may also include "token_ttl": int (seconds).
        """
        self._agent_config_resolver = resolver

    async def init(self) -> None:
        """Create the pool's Redis client for this loop and start the revocation listener.

        Never blocks on or fails because of Redis: the client connects on first
        use and the listener runs in the background, logging if it stops.
        """
        try:
            self.redis = self._new_redis_client()
        except (aioredis.RedisError, OSError, ValueError) as exc:
            # Stay lazy: run grants are still minted (and cached in process)
            # without Redis, and a later call creates the client again.
            logger.warning("token_pool_redis_unavailable_at_start", error_type=type(exc).__name__)
            return
        if self._revocation_task is None or self._revocation_task.done():
            self._revocation_task = asyncio.create_task(self._supervise_revocations())

    @staticmethod
    def _new_redis_client() -> aioredis.Redis:
        from core.config import redis_socket_timeout_kwargs

        return aioredis.from_url(settings.redis_url, decode_responses=True, **redis_socket_timeout_kwargs())

    def _redis_client(self) -> aioredis.Redis | None:
        if self.redis is not None:
            return self.redis
        if not self.lazy_redis:
            return None
        key = (os.getpid(), id(asyncio.get_running_loop()))
        client = self._loop_clients.get(key)
        if client is None:
            client = self._new_redis_client()
            self._loop_clients[key] = client
        return client

    async def _supervise_revocations(self) -> None:
        try:
            if self.redis is None:
                return
            pubsub = self.redis.pubsub()
            await pubsub.subscribe("agenticorg:token:revoke")
            await self._listen_revocations(pubsub)
        except asyncio.CancelledError:
            raise
        except (aioredis.RedisError, OSError) as exc:
            logger.warning("token_pool_revocation_listener_stopped", error_type=type(exc).__name__)

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

    async def get_run_grant_token(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        grantex_agent_id: str,
        scopes: list[str],
        ttl_seconds: int | None = None,
    ) -> RunGrantToken:
        """Return a usable grant token for one agent run, minting one if needed.

        Raises ``GrantMintError`` when no token can be obtained; callers decide
        what that means for the run (it is never a silent allow).
        """
        if not tenant_id or not agent_id:
            raise GrantMintError("lookup_failed", "tenant and agent are required to obtain a run grant")
        if not grantex_agent_id or not scopes:
            raise GrantMintError("agent_not_registered", "agent has no registered Grantex agent id or scopes")
        ttl = int(ttl_seconds or settings.grants_run_token_ttl_seconds)

        cache_key = self._run_grant_cache_key(tenant_id, agent_id, grantex_agent_id, scopes)
        cached = await self._cached_run_grant(cache_key)
        if cached is not None:
            return cached

        async with self._mint_lock(cache_key):
            # Another run may have minted while this one waited for the lock.
            cached = await self._cached_run_grant(cache_key)
            if cached is not None:
                return cached
            minted = await self._mint_run_grant(grantex_agent_id=grantex_agent_id, scopes=scopes, ttl_seconds=ttl)
            await self._store_run_grant(cache_key, minted)
            return minted

    @staticmethod
    def _run_grant_cache_key(tenant_id: str, agent_id: str, grantex_agent_id: str, scopes: list[str]) -> str:
        digest = hashlib.sha256(
            json.dumps([grantex_agent_id, sorted(scopes)], separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return f"grant:run:{tenant_id}:{agent_id}:{digest}"

    def _mint_lock(self, cache_key: str) -> asyncio.Lock:
        key = (id(asyncio.get_running_loop()), cache_key)
        lock = self._mint_locks.get(key)
        if lock is None:
            while len(self._mint_locks) >= _LOCKS_MAX:
                oldest, oldest_lock = next(iter(self._mint_locks.items()))
                if oldest_lock.locked():
                    break
                self._mint_locks.pop(oldest)
            lock = asyncio.Lock()
            self._mint_locks[key] = lock
        return lock

    async def _cached_run_grant(self, cache_key: str) -> RunGrantToken | None:
        grant = await self._read_run_grant(cache_key)
        if grant is None:
            local = self._local_grants.get(cache_key)
            if local is not None and local.usable():
                grant = RunGrantToken(
                    token=local.token,
                    grant_id=local.grant_id,
                    expires_at=local.expires_at,
                    source="pool_cache",
                    ttl_seconds=local.ttl_seconds,
                )
            elif local is not None:
                self._local_grants.pop(cache_key, None)
        return grant

    async def _read_run_grant(self, cache_key: str) -> RunGrantToken | None:
        client = self._redis_client()
        if client is None:
            return None
        try:
            raw = await client.get(cache_key)
        except (aioredis.RedisError, OSError) as exc:
            # A miss only means the in-process cache or a fresh mint is used.
            logger.warning("run_grant_cache_read_failed", error_type=type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            grant = RunGrantToken(
                token=str(data["token"]),
                grant_id=str(data.get("grant_id") or ""),
                expires_at=float(data["expires_at"]),
                source="pool_cache",
                ttl_seconds=int(data.get("ttl_seconds") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return grant if grant.token and grant.usable() else None

    async def _store_run_grant(self, cache_key: str, grant: RunGrantToken) -> None:
        if grant.expires_at is None or not grant.usable():
            # Too short-lived to share; the run that minted it still uses it.
            logger.info("run_grant_not_cached", grant_id=grant.grant_id, reason="short_lived")
            return
        self._local_grants[cache_key] = grant
        self._local_grants.move_to_end(cache_key)
        while len(self._local_grants) > _LOCAL_CACHE_MAX:
            self._local_grants.popitem(last=False)
        client = self._redis_client()
        if client is None:
            return
        ttl = int(grant.expires_at - time.time()) - min_remaining_seconds(grant.ttl_seconds)
        if ttl <= 0:
            return
        payload = json.dumps(
            {
                "token": grant.token,
                "grant_id": grant.grant_id,
                "expires_at": grant.expires_at,
                "ttl_seconds": grant.ttl_seconds,
            }
        )
        try:
            await client.setex(cache_key, ttl, payload)
        except (aioredis.RedisError, OSError) as exc:
            logger.warning("run_grant_cache_write_failed", error_type=type(exc).__name__)

    async def _mint_run_grant(self, *, grantex_agent_id: str, scopes: list[str], ttl_seconds: int) -> RunGrantToken:
        root_grant = external_keys.grantex_root_grant_token.strip()
        if not root_grant:
            raise GrantMintError("minting_unconfigured", "GRANTEX_ROOT_GRANT_TOKEN is not configured")
        try:
            client = self._grantex_client_factory()
        except (ImportError, RuntimeError, ValueError) as exc:
            raise GrantMintError("minting_unconfigured", f"Grantex client unavailable: {type(exc).__name__}") from exc
        try:
            response = await asyncio.to_thread(
                client.grants.delegate,
                parent_grant_token=root_grant,
                sub_agent_id=grantex_agent_id,
                scopes=list(scopes),
                expires_in=f"{max(1, ttl_seconds // 60)}m",
            )
        # enterprise-gate: broad-except-ok reason=delegation-failure-raises-grant-mint-error-never-an-allow
        except Exception as exc:
            raise GrantMintError("mint_failed", f"grant delegation failed: {type(exc).__name__}") from exc

        data = response if isinstance(response, dict) else {}
        token = data.get("grantToken") or data.get("grant_token")
        if not isinstance(token, str) or not token:
            raise GrantMintError("mint_failed", "grant delegation returned no grant token")
        grant_id = str(data.get("grantId") or data.get("grant_id") or "")
        expires_at = _parse_expiry(data.get("expiresAt") or data.get("expires_at"))
        if expires_at is None:
            # Without an expiry the grant could never be cached or refreshed on
            # time, so every call would mint again. Refuse it instead.
            raise GrantMintError("mint_failed", "grant delegation returned no usable expiry")
        logger.info(
            "run_grant_minted",
            grantex_agent_id=grantex_agent_id,
            grant_id=grant_id,
            scopes_count=len(scopes),
        )
        return RunGrantToken(
            token=token,
            grant_id=grant_id,
            expires_at=expires_at,
            source="minted",
            ttl_seconds=ttl_seconds,
        )

    async def revoke_token(self, agent_id: str) -> None:
        """Revoke token and broadcast to all pool nodes."""
        if not self.redis:
            return
        await self.redis.delete(f"agent:{agent_id}:token")
        await self.redis.publish("agenticorg:token:revoke", agent_id)
        if agent_id in self._refresh_tasks:
            self._refresh_tasks[agent_id].cancel()
            del self._refresh_tasks[agent_id]

    def _schedule_refresh(self, agent_id: str, delay: int) -> None:
        if agent_id in self._refresh_tasks:
            self._refresh_tasks[agent_id].cancel()
        self._refresh_tasks[agent_id] = asyncio.create_task(self._refresh_after(agent_id, delay))

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

        # enterprise-gate: broad-except-ok reason=token-refresh-boundary-removes-stale-token-on-failure
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
            # enterprise-gate: broad-except-ok reason=best-effort-token-cleanup-failure-is-logged
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
        if self._revocation_task is not None:
            self._revocation_task.cancel()
        if self.redis:
            await self.redis.close()
        for client in list(self._loop_clients.values()):
            try:
                await client.aclose()
            except (aioredis.RedisError, OSError, RuntimeError) as exc:
                logger.debug("token_pool_client_close_failed", error_type=type(exc).__name__)
        self._loop_clients.clear()


token_pool = TokenPool()
