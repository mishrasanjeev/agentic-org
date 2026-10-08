# SPDX-License-Identifier: Apache-2.0
"""The debugging console: an agent's breakpoints, its paused runs, and step-through of a run's checkpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import get_current_tenant, get_user_domains, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import (
    _assert_connectors_ready_for_dispatch,
    _effective_caller,
    _monthly_budget_refusal,
    _parse_company_id,
    _record_cost_ledger,
    _resolve_connector_configs,
    require_agent_mutable,
    require_agent_visible,
)
from core.billing.metering import gate_agent_run
from core.database import get_tenant_session
from core.finops import attribution as cost_attribution
from core.finops import thresholds as finops_thresholds
from core.langgraph import debugger
from core.models.agent import Agent
from core.models.hitl import HITLQueue
from core.ownership import AGENT_VISIBILITY_TENANT, Caller, caller_from_request

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


def _step_refused(error: str, message: str) -> HTTPException:
    return HTTPException(409, detail={"error": error, "message": message})


async def _step_gate(tid: uuid.UUID, tenant_id: str, agent: Any) -> None:
    """The billing gates a run passes before it runs, applied to a debug step before it is claimed."""
    blocked = await gate_agent_run(tenant_id)
    if blocked is not None:
        raise _step_refused("limit_exceeded", str(blocked.get("error") or "The monthly agent-run limit is reached."))
    budget = await _monthly_budget_refusal(tid, agent.id, getattr(agent, "cost_controls", None) or {})
    if budget is not None:
        raise _step_refused("budget_exceeded", str(budget["error"]["message"]))
    if finops_thresholds.enabled():
        async with get_tenant_session(tid) as session:
            decision = await finops_thresholds.check_run(session, tid, cost_attribution.current())
        if decision.action == "suspend":
            refusal = finops_thresholds.refusal(decision, agent_id=str(agent.id))
            raise _step_refused("threshold_suspended", str(refusal["error"]["message"]))
        if decision.action == "throttle":
            await asyncio.sleep(decision.delay_seconds)


async def _step_connectors(
    tid: uuid.UUID, tenant_id: str, agent: Any, spec: dict[str, Any]
) -> tuple[dict[str, Any], list[str] | None]:
    """The run's connector credentials, resolved again for this step from the connector ids the run used.

    The session keeps only the ids; decrypted credentials live for the step.
    """
    connector_ids = [str(cid) for cid in (spec.get("connector_ids") or [])]
    if not connector_ids:
        return {}, spec.get("connector_names")
    company_uuid = _parse_company_id(spec.get("company_id"))
    async with get_tenant_session(tid, company_uuid) as session:
        # A personal connector stays usable only by its owner's personal agent.
        await _assert_connectors_ready_for_dispatch(
            session,
            tid,
            [],
            company_uuid,
            agent_visibility=str(getattr(agent, "visibility", None) or AGENT_VISIBILITY_TENANT),
            agent_owner_user_id=getattr(agent, "owner_user_id", None),
            linked_connector_ids=connector_ids,
        )
    config, names = await _resolve_connector_configs(
        tenant_id=tenant_id,
        connector_ids=connector_ids,
        agent_level_config=getattr(agent, "config", None),
        company_id=spec.get("company_id"),
    )
    # The resolved names are the allow-list: a connector that no longer resolves offers no tools.
    return config, names


async def _open_approval(
    tid: uuid.UUID, agent: Any, thread_id: str, spec: dict[str, Any], result: dict[str, Any], caller: Caller
) -> str:
    """Open the approval a debug step reached, as a run reaching it does; a decision resumes the thread."""
    from core.approvals.agent_run_resume import RESUME_SPEC_KEY
    from core.push.sender import notify_approval_created

    trigger = str(result.get("hitl_trigger") or "approval required")
    confidence = float(result.get("confidence") or 0.0)
    agent_type = str(getattr(agent, "agent_type", "") or "")
    item_id = uuid.uuid4()
    async with get_tenant_session(tid) as session:
        session.add(
            HITLQueue(
                id=item_id,
                tenant_id=tid,
                agent_id=agent.id,
                workflow_run_id=None,
                requested_by_user_id=caller.user_id,
                title=f"HITL: {agent_type} — {trigger}",
                trigger_type="confidence_below_floor" if trigger.startswith("confidence ") else "policy_condition",
                priority="high" if confidence < 0.7 else "normal",
                assignee_role=str(getattr(agent, "domain", None) or "admin"),
                decision_options={"options": ["approve", "reject", "override"]},
                context={
                    "agent_type": agent_type,
                    "agent_status": getattr(agent, "status", None),
                    "confidence": confidence,
                    "reasoning_trace": list(result.get("reasoning_trace") or []),
                    "trigger": trigger,
                    "output": result.get("output", {}),
                    "debug_session": True,
                    **(
                        {"output_schema_errors": result["output_schema_errors"]}
                        if result.get("output_schema_errors")
                        else {}
                    ),
                    # Server-only, as for a run: the graph parameters the approval resume re-enters with.
                    RESUME_SPEC_KEY: dict(spec),
                },
                expires_at=datetime.now(UTC) + timedelta(hours=4),
                checkpoint_thread_id=thread_id,
            )
        )
    owner = getattr(agent, "owner_user_id", None)
    await notify_approval_created(
        str(tid),
        item_id=str(item_id),
        agent_name=str(getattr(agent, "name", "") or agent_type),
        action=trigger,
        agent_visibility=getattr(agent, "visibility", None),
        agent_owner_user_id=str(owner) if owner else None,
    )
    return str(item_id)


async def _advance(agent_id: uuid.UUID, thread_id: str, tenant_id: str, caller: Caller | None, mode: str) -> dict:
    """Re-enter a paused run: one node (``step``) or up to the next breakpoint (``continue``).

    A step passes the billing gates a run passes, its usage (what the step
    spent, not the thread's total) is added to the agent's cost ledger, the
    run's connector credentials are resolved again, and an approval the step
    reaches opens the normal approval flow.
    """
    if not debugger.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    effective = _effective_caller(caller)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_mutable(agent, effective)
        attribution = (
            await cost_attribution.resolve_for_agent(session, agent, application="agents")
            if cost_attribution.enabled()
            else None
        )
    attribution_token = cost_attribution.bind(attribution) if attribution is not None else None
    try:
        return await _advance_claimed(agent, thread_id, tenant_id, effective, mode)
    finally:
        if attribution_token is not None:
            cost_attribution.reset(attribution_token)


async def _advance_claimed(agent: Any, thread_id: str, tenant_id: str, caller: Caller, mode: str) -> dict:
    from auth.run_grants import CALLER_GRANT_KEY, caller_grant_for_run, resolve_run_grant
    from core.langgraph import runner
    from core.langgraph.checkpointer import CheckpointerUnavailableError

    tid = uuid.UUID(tenant_id)
    agent_id = agent.id
    # Refused before the session is claimed, so a refused step leaves it paused.
    await _step_gate(tid, tenant_id, agent)
    claim = await debugger.claim_session(tid, agent_id, thread_id)
    if claim.refusal:
        raise HTTPException(claim.status, detail={"error": claim.refusal, "message": "The session cannot be stepped"})
    spec = claim.spec
    try:
        connector_config, connector_names = await _step_connectors(tid, tenant_id, agent, spec)
    except HTTPException:
        await debugger.release_session(tid, agent_id, thread_id)
        raise
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
            connector_config=connector_config,
            connector_names=connector_names,
            tenant_id=tenant_id,
            company_id=spec.get("company_id"),
            domain=spec.get("domain"),
            llm_provider=spec.get("llm_provider"),
            require_paused=True,
            debug={"mode": mode, "breakpoints": claim.breakpoints},
            output_schema=spec.get("output_schema"),
            output_schema_json=spec.get("output_schema_json"),
            limits=spec.get("limits"),
            **bound_grant,
        )
    except CheckpointerUnavailableError as exc:
        result = {"status": "failed", "error": str(exc), "reason": exc.reason}
    # enterprise-gate: broad-except-ok reason=debug-step-failure-is-recorded-on-the-session
    except Exception as exc:
        logger.error("agent_debug_step_error", agent_id=str(agent_id), error_type=type(exc).__name__)
        result = {"status": "failed", "error": type(exc).__name__, "reason": "step_failed"}
    # What the step spent joins the run's usage: no new task, the run was counted when it started.
    usage_recorded = await _record_cost_ledger(tid, agent_id, result.get("performance") or {}, count_task=False)
    approval_id = None
    if result.get("status") == "hitl_triggered":
        approval_id = await _open_approval(tid, agent, thread_id, spec, result, caller)
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
        "hitl_trigger": result.get("hitl_trigger") or None,
        "approval_id": approval_id,
        "usage_recorded": usage_recorded,
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
