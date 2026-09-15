"""Redis-backed token pool for agent tokens.

Besides caching and refreshing agent tokens, the pool obtains the *first*
grant token for an agent run (``get_run_grant_token``): it delegates a
short-lived grant to the agent's registered Grantex agent from the platform
root grant, scoped to the agent's registered scopes, and caches it per tenant,
agent and scope set until shortly before it expires.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
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

# A cached run grant is reused only while it has at least this long to live.
RUN_GRANT_MIN_REMAINING_SECONDS = 60


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

    def __repr__(self) -> str:  # never print the token
        return f"RunGrantToken(grant_id={self.grant_id!r}, expires_at={self.expires_at!r}, source={self.source!r})"


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
        self.redis: aioredis.Redis | None = None
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._agent_config_resolver: AgentConfigResolver | None = None
        self._grantex_client_factory = grantex_client_factory or _default_grantex_client

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
        await pubsub.subscribe("agenticorg:token:revoke")
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

    async def get_run_grant_token(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        grantex_agent_id: str,
        scopes: list[str],
        ttl_seconds: int | None = None,
    ) -> RunGrantToken:
        """Return a grant token for one agent run, minting the first one if needed.

        Raises ``GrantMintError`` when no token can be obtained; callers decide
        what that means for the run (it is never a silent allow).
        """
        if not tenant_id or not agent_id:
            raise GrantMintError("lookup_failed", "tenant and agent are required to obtain a run grant")
        if not grantex_agent_id or not scopes:
            raise GrantMintError("agent_not_registered", "agent has no registered Grantex agent id or scopes")

        cache_key = self._run_grant_cache_key(tenant_id, agent_id, grantex_agent_id, scopes)
        cached = await self._read_run_grant(cache_key)
        if cached is not None:
            return cached

        minted = await self._mint_run_grant(
            grantex_agent_id=grantex_agent_id,
            scopes=scopes,
            ttl_seconds=ttl_seconds or settings.grants_run_token_ttl_seconds,
        )
        await self._store_run_grant(cache_key, minted)
        return minted

    @staticmethod
    def _run_grant_cache_key(tenant_id: str, agent_id: str, grantex_agent_id: str, scopes: list[str]) -> str:
        digest = hashlib.sha256(
            json.dumps([grantex_agent_id, sorted(scopes)], separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return f"grant:run:{tenant_id}:{agent_id}:{digest}"

    async def _read_run_grant(self, cache_key: str) -> RunGrantToken | None:
        if not self.redis:
            return None
        try:
            raw = await self.redis.get(cache_key)
        except (aioredis.RedisError, OSError) as exc:
            # A cache miss only means minting a fresh grant.
            logger.warning("run_grant_cache_read_failed", error_type=type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            token = str(data["token"])
            expires_at = float(data["expires_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if not token or expires_at - time.time() < RUN_GRANT_MIN_REMAINING_SECONDS:
            return None
        return RunGrantToken(
            token=token,
            grant_id=str(data.get("grant_id") or ""),
            expires_at=expires_at,
            source="pool_cache",
        )

    async def _store_run_grant(self, cache_key: str, grant: RunGrantToken) -> None:
        if not self.redis or grant.expires_at is None:
            return
        ttl = int(grant.expires_at - time.time()) - RUN_GRANT_MIN_REMAINING_SECONDS
        if ttl <= 0:
            return
        payload = json.dumps({"token": grant.token, "grant_id": grant.grant_id, "expires_at": grant.expires_at})
        try:
            await self.redis.setex(cache_key, ttl, payload)
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
        logger.info(
            "run_grant_minted",
            grantex_agent_id=grantex_agent_id,
            grant_id=grant_id,
            scopes_count=len(scopes),
        )
        return RunGrantToken(
            token=token,
            grant_id=grant_id,
            expires_at=_parse_expiry(data.get("expiresAt") or data.get("expires_at")),
            source="minted",
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
        if self.redis:
            await self.redis.close()


token_pool = TokenPool()
