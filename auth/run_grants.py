# SPDX-License-Identifier: Apache-2.0
"""Resolve the grant an agent run carries (PRD F-1).

``resolve_run_grant`` decides the run's ``grants.enforce_closed`` mode and,
in ``warn`` and ``deny``, the grant token its tool calls are checked against:

1. a token the caller already holds (``supplied``), else
2. the agent's legacy ``config.grantex.grant_token`` (``agent_config``), else
3. a per-run grant from ``auth.token_pool`` — reused from the pool cache
   (``pool_cache``) or delegated from the root grant to the agent's
   registered Grantex agent (``minted``).

When none of these yields a token the run still starts, with an empty token
and a ``missing_sub_reason``; every tool call it makes is then recorded as
``grant_missing`` (warn) or refused (deny). Resolution never raises.

In ``off`` the supplied token is passed through untouched and nothing is
looked up or minted, so behaviour is exactly the legacy one.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

import structlog

from auth.grant_enforcement import (
    EnforcementMode,
    GrantCallContext,
    GrantCheck,
    check_tool_grant,
    resolve_enforcement_mode,
)

logger = structlog.get_logger()


# Token sources the legacy (``off``) path already enforced strictly.
LEGACY_ENFORCED_SOURCES = frozenset({"supplied", "agent_config"})
# Token sources the pool can refresh before they expire.
POOL_SOURCES = frozenset({"minted", "pool_cache"})

# Graph builders take ``run_grant`` as a required keyword so enforcement can
# never be left off by omission. Tests that exercise graph mechanics without
# enforcement pass this named sentinel; production code never does (a test in
# tests/unit/test_grant_enforcement_modes.py scans for it).
NO_RUN_GRANT_FOR_TESTS: Final[None] = None


@dataclass(frozen=True)
class RunGrant:
    mode: EnforcementMode
    token: str = field(default="", repr=False)
    source: str = ""
    grant_id: str = ""
    missing_sub_reason: str = ""
    # Pool-issued grants carry what is needed to obtain a fresh one before
    # this one expires (``refresh_run_grant``).
    expires_at: float | None = None
    ttl_seconds: int = 0
    tenant_id: str = ""
    agent_id: str = ""
    grantex_agent_id: str = ""
    scopes: tuple[str, ...] = ()

    @property
    def call_mode(self) -> EnforcementMode:
        """Mode to apply to each tool call.

        ``warn`` must never weaken a check that already applied: a token the
        caller supplied or the agent had configured was enforced strictly in
        ``off``, so a call it does not cover is still refused in ``warn``.
        """
        if self.mode is EnforcementMode.WARN and self.token and self.source in LEGACY_ENFORCED_SOURCES:
            return EnforcementMode.DENY
        return self.mode


async def _load_agent_grantex_config(tenant_id: str, agent_id: str) -> Mapping[str, Any]:
    from sqlalchemy import text

    from core.database import get_tenant_session

    tid = uuid.UUID(str(tenant_id))
    aid = uuid.UUID(str(agent_id))
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(
                text("SELECT config FROM agents WHERE id = :aid AND tenant_id = :tid"),
                {"aid": str(aid), "tid": str(tid)},
            )
        ).fetchone()
    if row is None:
        raise LookupError("agent not found for tenant")
    config = row[0] or {}
    grantex = config.get("grantex") if isinstance(config, dict) else None
    return grantex if isinstance(grantex, dict) else {}


async def resolve_run_grant(
    *,
    tenant_id: str | None,
    agent_id: str | None,
    supplied_token: str | None = "",
    grantex_config: Mapping[str, Any] | None = None,
    mode: EnforcementMode | None = None,
    runtime: str = "",
) -> RunGrant:
    """Resolve the enforcement mode and grant token for one agent run."""
    if mode is None:
        mode = await resolve_enforcement_mode(tenant_id)
    supplied = (supplied_token or "").strip()

    if mode is EnforcementMode.OFF:
        return RunGrant(mode=mode, token=supplied_token or "", source="supplied" if supplied else "")
    if supplied:
        return RunGrant(mode=mode, token=supplied, source="supplied")

    tenant = str(tenant_id or "")
    agent = str(agent_id or "")

    def _missing(sub_reason: str) -> RunGrant:
        logger.warning(
            "grant_resolution_failed",
            mode=mode.value,
            sub_reason=sub_reason,
            tenant_id=tenant,
            agent_id=agent,
            runtime=runtime,
        )
        return RunGrant(mode=mode, source="none", missing_sub_reason=sub_reason)

    if grantex_config is None:
        try:
            grantex_config = await _load_agent_grantex_config(tenant, agent)
        # enterprise-gate: broad-except-ok reason=agent-lookup-failure-resolves-to-grant-missing-never-an-allow
        except Exception as exc:
            logger.warning("grant_resolution_agent_lookup_failed", error_type=type(exc).__name__, agent_id=agent)
            return _missing("lookup_failed")

    legacy = grantex_config.get("grant_token")
    if isinstance(legacy, str) and legacy.strip():
        return RunGrant(mode=mode, token=legacy.strip(), source="agent_config")

    from auth.token_pool import GrantMintError, token_pool

    raw_scopes = grantex_config.get("grantex_scopes")
    scopes = [s for s in raw_scopes if isinstance(s, str) and s] if isinstance(raw_scopes, list) else []
    try:
        grant = await token_pool.get_run_grant_token(
            tenant_id=tenant,
            agent_id=agent,
            grantex_agent_id=str(grantex_config.get("grantex_agent_id") or ""),
            scopes=scopes,
        )
    except GrantMintError as exc:
        return _missing(exc.sub_reason)
    # enterprise-gate: broad-except-ok reason=unexpected-pool-failure-resolves-to-grant-missing-never-an-allow
    except Exception as exc:
        logger.error("grant_resolution_pool_error", error_type=type(exc).__name__, agent_id=agent)
        return _missing("mint_failed")
    return RunGrant(
        mode=mode,
        token=grant.token,
        source=grant.source,
        grant_id=grant.grant_id,
        expires_at=grant.expires_at,
        ttl_seconds=grant.ttl_seconds,
        tenant_id=tenant,
        agent_id=agent,
        grantex_agent_id=str(grantex_config.get("grantex_agent_id") or ""),
        scopes=tuple(scopes),
    )


async def refresh_run_grant(run_grant: RunGrant) -> RunGrant:
    """Return a pool-issued grant with enough lifetime left for the next call.

    Grants that did not come from the pool, or still have at least the
    minimum remaining lifetime, are returned unchanged. When a fresh grant
    cannot be obtained the current one is kept: if it has expired, Grantex
    verification refuses it (``token_invalid``), so this never widens access.
    """
    if run_grant.mode is EnforcementMode.OFF or run_grant.source not in POOL_SOURCES:
        return run_grant
    from auth.token_pool import GrantMintError, min_remaining_seconds, token_pool

    if run_grant.expires_at is not None and run_grant.expires_at - time.time() >= min_remaining_seconds(
        run_grant.ttl_seconds
    ):
        return run_grant
    try:
        fresh = await token_pool.get_run_grant_token(
            tenant_id=run_grant.tenant_id,
            agent_id=run_grant.agent_id,
            grantex_agent_id=run_grant.grantex_agent_id,
            scopes=list(run_grant.scopes),
            ttl_seconds=run_grant.ttl_seconds or None,
        )
    except GrantMintError as exc:
        logger.warning("run_grant_refresh_failed", sub_reason=exc.sub_reason, agent_id=run_grant.agent_id)
        return run_grant
    # enterprise-gate: broad-except-ok reason=refresh-failure-keeps-current-grant-which-verification-still-checks
    except Exception as exc:
        logger.error("run_grant_refresh_error", error_type=type(exc).__name__, agent_id=run_grant.agent_id)
        return run_grant
    logger.info("run_grant_refreshed", grant_id=fresh.grant_id, agent_id=run_grant.agent_id)
    return replace(
        run_grant,
        token=fresh.token,
        source=fresh.source,
        grant_id=fresh.grant_id,
        expires_at=fresh.expires_at,
        ttl_seconds=fresh.ttl_seconds,
    )


async def check_run_grant(
    run_grant: RunGrant,
    *,
    connector: str,
    tool: str,
    context: GrantCallContext,
    amount: float | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> GrantCheck:
    """Check one tool call against a run grant in ``warn`` or ``deny`` mode."""
    return await check_tool_grant(
        mode=run_grant.call_mode,
        grant_token=run_grant.token,
        connector=connector,
        tool=tool,
        context=context,
        amount=amount,
        missing_sub_reason=run_grant.missing_sub_reason,
        client_factory=client_factory,
    )


async def direct_tool_call_permitted(
    run_grant: RunGrant,
    *,
    connector: str,
    tool: str,
    tenant_id: str,
    agent_id: str,
    agent_type: str = "",
    runtime: str,
) -> bool:
    """Grant check for a tool the platform invokes directly, outside the graph.

    ``off`` always permits (legacy). ``warn`` records a would-deny and
    permits. ``deny`` refuses when the grant does not cover the call.
    """
    if run_grant.mode is EnforcementMode.OFF:
        return True
    check = await check_run_grant(
        run_grant,
        connector=connector,
        tool=tool,
        context=GrantCallContext(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_type=agent_type,
            runtime=runtime,
            grant_source=run_grant.source,
        ),
    )
    return check.dispatch_allowed
