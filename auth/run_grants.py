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
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
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

# Graph builders, ``core.langgraph.tool_adapter.execute_agent_tool`` and
# ``ToolGateway.execute`` take ``run_grant`` as a required keyword so
# enforcement can never be left off by omission. Tests that exercise graph
# mechanics or the legacy tool checks without enforcement pass this named
# sentinel (the legacy path); production code never does (a test in
# tests/unit/test_run_grant_lifecycle.py scans for it).
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
    # A Grantex token the caller authenticated with that belongs to a
    # different agent than the one this run executes. Every tool call must be
    # allowed by BOTH this token and the run agent's grant (``check_run_grant``),
    # so a caller can never borrow another agent's tools or grant.
    caller_token: str = field(default="", repr=False)
    caller_agent_id: str = ""
    # The run was started by a caller Grantex token that is no longer
    # available (a workflow or approval resumed later, in another request).
    # Every tool call is refused rather than run without the caller's check.
    caller_token_unavailable: bool = False

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


@dataclass(frozen=True)
class CallerGrant:
    """The Grantex token a request authenticated with, and the agent it was issued to.

    ``required`` without a ``token`` means the work belongs to a run a caller
    token started, but the token is not available here: tool calls are refused.
    """

    token: str = field(default="", repr=False)
    agent_id: str = ""
    required: bool = False

    @property
    def bound(self) -> bool:
        return bool(self.token) or self.required

    def resolve_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``resolve_run_grant``."""
        return {"caller_token": self.token, "caller_agent_id": self.agent_id, "caller_required": self.required}

    def marker(self) -> dict[str, str] | None:
        """What a run persists to remember it was started by a caller token (never the token)."""
        return {"agent_id": self.agent_id} if self.bound else None


NO_CALLER: Final = CallerGrant()

# Key under which workflow state and approval context record a caller binding.
CALLER_GRANT_KEY: Final = "grant_caller"

_ACTIVE_CALLER: ContextVar[CallerGrant] = ContextVar("run_grant_active_caller", default=NO_CALLER)


def caller_grant_from_request(request: Any) -> CallerGrant:
    """The caller grant of an API request: its Grantex token, if it authenticated with one.

    Only the Grantex middleware sets ``request.state.grant_token``; its agent id
    is taken only alongside that token, never from a legacy session.
    """
    state = getattr(request, "state", None)
    token = getattr(state, "grant_token", None) if state is not None else None
    if not isinstance(token, str) or not token.strip():
        return NO_CALLER
    agent_id = getattr(state, "agent_id", None)
    return CallerGrant(token=token.strip(), agent_id=str(agent_id) if isinstance(agent_id, str) else "")


@contextmanager
def bind_caller_grant(caller: CallerGrant) -> Iterator[None]:
    """Make ``caller`` available to the work started in this context (workflow steps)."""
    reset = _ACTIVE_CALLER.set(caller)
    try:
        yield
    finally:
        _ACTIVE_CALLER.reset(reset)


def caller_grant_for_run(marker: Any) -> CallerGrant:
    """Caller grant for work belonging to a run that recorded ``marker`` (``CallerGrant.marker``).

    A run with no marker was not started by a caller token; the active caller
    (if any) still applies. A run with a marker gets the active caller when it
    is that same caller, else the binding is required but unavailable.
    """
    active = _ACTIVE_CALLER.get()
    if not isinstance(marker, Mapping):
        return active
    agent_id = str(marker.get("agent_id") or "")
    if active.token and active.agent_id == agent_id:
        return active
    return CallerGrant(agent_id=agent_id, required=True)


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
    caller_token: str | None = "",
    caller_agent_id: str | None = "",
    caller_required: bool = False,
) -> RunGrant:
    """Resolve the enforcement mode and grant token for one agent run.

    ``supplied_token`` belongs to the run agent. ``caller_token`` is a Grantex
    token the request authenticated with, issued to ``caller_agent_id``: when
    that is the run agent it is used as the run grant; otherwise the run
    agent's own grant is resolved as usual and the caller token is kept
    alongside it, so both must allow every tool call. ``caller_required``
    without a ``caller_token`` (a run a caller token started, resumed without
    it) makes every tool call a denial.
    """
    if mode is None:
        mode = await resolve_enforcement_mode(tenant_id)
    supplied = (supplied_token or "").strip()
    caller = (caller_token or "").strip()

    if mode is EnforcementMode.OFF:
        legacy_token = supplied_token or caller_token or ""
        return RunGrant(mode=mode, token=legacy_token, source="supplied" if legacy_token.strip() else "")
    if caller and not supplied and caller_agent_id and str(caller_agent_id) == str(agent_id or ""):
        supplied, caller = caller, ""
    grant = await _resolve_run_agent_grant(
        mode=mode,
        tenant_id=tenant_id,
        agent_id=agent_id,
        supplied=supplied,
        grantex_config=grantex_config,
        runtime=runtime,
    )
    if caller:
        logger.info(
            "grant_resolution_caller_token_bound",
            mode=mode.value,
            agent_id=str(agent_id or ""),
            caller_agent_id=str(caller_agent_id or ""),
            runtime=runtime,
        )
        grant = replace(grant, caller_token=caller, caller_agent_id=str(caller_agent_id or ""))
    elif caller_required and not (caller_token or "").strip():
        logger.warning(
            "grant_resolution_caller_token_unavailable",
            mode=mode.value,
            agent_id=str(agent_id or ""),
            caller_agent_id=str(caller_agent_id or ""),
            runtime=runtime,
        )
        grant = replace(grant, caller_agent_id=str(caller_agent_id or ""), caller_token_unavailable=True)
    return grant


async def _resolve_run_agent_grant(
    *,
    mode: EnforcementMode,
    tenant_id: str | None,
    agent_id: str | None,
    supplied: str,
    grantex_config: Mapping[str, Any] | None,
    runtime: str,
) -> RunGrant:
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

    if not agent:
        # Nothing to resolve a grant for: the call is not made by an agent
        # with a Grantex registration (for example a workflow connector step).
        return _missing("no_agent")

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
    """Check one tool call against a run grant in ``warn`` or ``deny`` mode.

    With a ``caller_token`` the call must be allowed by the caller's token
    first - always strictly, because the legacy path enforced caller tokens
    strictly - and then by the run agent's grant in the run's mode. A run
    whose caller token is required but unavailable is refused
    (``grant_missing``/``caller_token_unavailable``).
    """
    if run_grant.caller_token_unavailable and not run_grant.caller_token:
        return await check_tool_grant(
            mode=EnforcementMode.DENY,
            grant_token="",
            connector=connector,
            tool=tool,
            context=replace(context, grant_source="caller"),
            amount=amount,
            missing_sub_reason="caller_token_unavailable",
            client_factory=client_factory,
        )
    if run_grant.caller_token:
        caller_check = await check_tool_grant(
            mode=EnforcementMode.DENY,
            grant_token=run_grant.caller_token,
            connector=connector,
            tool=tool,
            context=replace(context, grant_source="caller"),
            amount=amount,
            client_factory=client_factory,
        )
        if not caller_check.dispatch_allowed:
            return caller_check
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
