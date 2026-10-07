# SPDX-License-Identifier: Apache-2.0
"""The debugging console: an agent's breakpoints, its paused runs, and step-through of a run's checkpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import get_current_tenant, get_user_domains, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _effective_caller, require_agent_mutable, require_agent_visible
from core.database import get_tenant_session
from core.langgraph import debugger
from core.models.agent import Agent
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter(prefix="/agents/{agent_id}/debug", dependencies=[require_tenant_admin])

_THREAD = Annotated[str, Path(min_length=1, max_length=160)]
_CHECKPOINT = Annotated[str, Path(min_length=1, max_length=64)]


class BreakpointsIn(BaseModel):
    """The nodes an agent's runs pause before, or null to remove them."""

    model_config = {"extra": "forbid"}

    break_before: list[str] | None = None


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "debug_console_disabled",
            "message": "The debugging console is off for this deployment (AGENTICORG_RUNTIME_DEBUG_CONSOLE_ENABLED).",
        },
    )


def _refused(exc: debugger.DebugError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


async def _agent(session: Any, tid: uuid.UUID, agent_id: uuid.UUID, *, lock: bool = False) -> Agent:
    query = select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
    if lock:
        query = query.with_for_update()
    agent = (await session.execute(query)).scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.debug.read",
)
async def get_breakpoints(
    agent_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """The nodes the agent's runs pause before, the graph's nodes, and whether breakpoints apply."""
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
        nodes = debugger.declared(agent)
    return {"id": str(agent_id), "break_before": nodes, "nodes": list(debugger.NODES), "enforced": debugger.enabled()}


@router.put("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.write",
    rate_limit="agent-write",
    idempotency="idempotent-full-replace",
    audit_event="agents.debug.set",
)
async def set_breakpoints(
    agent_id: uuid.UUID,
    body: BreakpointsIn,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Give an agent its breakpoints (nodes its runs pause before), or remove them.

    They apply while ``AGENTICORG_RUNTIME_DEBUG_CONSOLE_ENABLED`` is on.
    """
    tid = uuid.UUID(tenant_id)
    nodes: list[str] | None = None
    if body.break_before is not None:
        try:
            nodes = debugger.parse_breakpoints(body.break_before)
        except debugger.DebugError as exc:
            raise _refused(exc) from None
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id, lock=True)
        require_agent_mutable(agent, _effective_caller(caller, user_domains))
        config = dict(agent.config or {})
        if not nodes:
            config.pop(debugger.DEBUG_KEY, None)
        else:
            config[debugger.DEBUG_KEY] = {"break_before": nodes}
        agent.config = config
    return {
        "id": str(agent_id),
        "break_before": nodes or [],
        "nodes": list(debugger.NODES),
        "enforced": debugger.enabled(),
    }


@router.get("/sessions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.debug.sessions.list",
)
async def list_sessions(
    agent_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """The agent's debug sessions, newest first: runs paused at a breakpoint and where each one stands."""
    if not debugger.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
    sessions = await debugger.list_sessions(tid, agent_id, limit=limit)
    return {"id": str(agent_id), "sessions": sessions, "total": len(sessions)}


@router.get("/threads/{thread_id:path}/steps/{checkpoint_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.debug.inspect",
)
async def inspect_step(
    agent_id: uuid.UUID,
    thread_id: _THREAD,
    checkpoint_id: _CHECKPOINT,
    path: Annotated[str, Query(max_length=200)] = "",
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """One value of the state at one step, by its dotted path (``output.summary``, ``messages.2.content``)."""
    if not debugger.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
    try:
        return await debugger.inspect(tid, thread_id, checkpoint_id, path)
    except debugger.DebugError as exc:
        raise _refused(exc) from None


@router.get("/threads/{thread_id:path}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.debug.steps",
)
async def thread_steps(
    agent_id: uuid.UUID,
    thread_id: _THREAD,
    limit: Annotated[int, Query(ge=1, le=debugger.MAX_STEPS)] = debugger.MAX_STEPS,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Every checkpoint of a run's thread as a step: the node that ran, what it changed, the state, what is next."""
    if not debugger.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
    try:
        return await debugger.steps(tid, thread_id, limit=limit)
    except debugger.DebugError as exc:
        raise _refused(exc) from None


async def _advance(agent_id: uuid.UUID, thread_id: str, tenant_id: str, caller: Caller | None, mode: str) -> dict:
    """Re-enter a paused run: one node (``step``) or up to the next breakpoint (``continue``)."""
    from auth.run_grants import CALLER_GRANT_KEY, caller_grant_for_run, resolve_run_grant
    from core.langgraph import runner
    from core.langgraph.checkpointer import CheckpointerUnavailableError

    if not debugger.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_mutable(agent, _effective_caller(caller))
    claim = await debugger.claim_session(tid, agent_id, thread_id)
    if claim.refusal:
        raise HTTPException(claim.status, detail={"error": claim.refusal, "message": "The session cannot be stepped"})
    spec = claim.spec
    bound_grant: dict[str, Any] = {}
    if isinstance(spec.get(CALLER_GRANT_KEY), dict):
        bound_grant["run_grant"] = await resolve_run_grant(
            tenant_id=tenant_id,
            agent_id=str(agent_id),
            runtime="langgraph_resume",
            **caller_grant_for_run(spec[CALLER_GRANT_KEY]).resolve_kwargs(),
        )
    try:
        result = await runner.resume_agent(
            agent_id=str(agent_id),
            thread_id=thread_id,
            decision={},
            system_prompt=claim.system_prompt,
            authorized_tools=list(spec.get("authorized_tools") or []),
            llm_model=str(spec.get("llm_model") or ""),
            confidence_floor=float(spec.get("confidence_floor") or 0.88),
            hitl_condition=str(spec.get("hitl_condition") or ""),
            connector_config={},
            connector_names=spec.get("connector_names"),
            tenant_id=tenant_id,
            company_id=spec.get("company_id"),
            domain=spec.get("domain"),
            llm_provider=spec.get("llm_provider"),
            require_paused=True,
            debug={"mode": mode, "breakpoints": claim.breakpoints},
            **bound_grant,
        )
    except CheckpointerUnavailableError as exc:
        result = {"status": "failed", "error": str(exc), "reason": exc.reason}
    # enterprise-gate: broad-except-ok reason=debug-step-failure-is-recorded-on-the-session
    except Exception as exc:
        logger.error("agent_debug_step_error", agent_id=str(agent_id), error_type=type(exc).__name__)
        result = {"status": "failed", "error": type(exc).__name__, "reason": "step_failed"}
    state = await debugger.finish_session(tid, agent_id, thread_id, result)
    return {
        "thread_id": thread_id,
        "mode": mode,
        "status": result.get("status"),
        "paused_before": list(result.get("paused_before") or []),
        "session": state,
        "output": result.get("output", {}),
        "confidence": result.get("confidence", 0.0),
        "reasoning_trace": result.get("reasoning_trace", []),
        "error": result.get("error") or None,
        "reason": result.get("reason") or None,
        "performance": result.get("performance", {}),
    }


@router.post("/threads/{thread_id:path}/step")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.write",
    rate_limit="agent-control",
    idempotency="not-idempotent-create",
    audit_event="agents.debug.step",
)
async def step_thread(
    agent_id: uuid.UUID,
    thread_id: _THREAD,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Run the next node of a paused run and pause again."""
    return await _advance(agent_id, thread_id, tenant_id, caller, "step")


@router.post("/threads/{thread_id:path}/continue")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.debug.sensitive.write",
    rate_limit="agent-control",
    idempotency="not-idempotent-create",
    audit_event="agents.debug.continue",
)
async def continue_thread(
    agent_id: uuid.UUID,
    thread_id: _THREAD,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Run a paused run on to its next breakpoint, or to the end."""
    return await _advance(agent_id, thread_id, tenant_id, caller, "continue")
