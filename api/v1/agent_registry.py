# SPDX-License-Identifier: Apache-2.0
"""Agent registry: an agent's card and its governance lifecycle."""

from __future__ import annotations

import uuid as _uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.deps import get_current_tenant, get_current_user, get_user_domains
from api.route_metadata import route_meta
from api.v1.agents import _effective_caller, _user_uuid_from_claims
from core.agent_registry import lifecycle
from core.database import get_tenant_session
from core.models.agent import Agent
from core.ownership import Caller, caller_from_request, require_agent_mutable, require_agent_visible
from core.schemas.api import AgentCardIn, AgentLifecycleIn

logger = structlog.get_logger()

router = APIRouter()


def _require_enabled() -> None:
    if not lifecycle.enabled():
        raise HTTPException(409, "The agent registry is off in this deployment")


def _refused(exc: lifecycle.RegistryError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


async def _agent(session, tenant_id: _uuid.UUID, agent_id: UUID, *, lock: bool = False) -> Agent:
    statement = select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    if lock:
        statement = statement.with_for_update()
    agent = (await session.execute(statement)).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.get("/agents/{agent_id}/card")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.card.read",
)
async def get_agent_card(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """The agent's card: identity, purpose and risk, models, tools, permissions, schemas, prompt summary,
    evaluation gate and lifecycle state. Never the prompt's text."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
        return await lifecycle.card(session, tid, agent)


@router.put("/agents/{agent_id}/card")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.write",
    rate_limit="agent-write",
    idempotency="idempotent-partial-update",
    audit_event="agents.card.set",
)
async def set_agent_card(
    agent_id: UUID,
    body: AgentCardIn,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """Set the card fields an administrator writes: purpose, risk tier, use case and channels.

    Only the fields sent are changed. The entry is created as ``draft`` on first use.
    """
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    try:
        fields = lifecycle.parse_card_fields(body.model_dump(exclude_unset=True))
    except lifecycle.RegistryError as exc:
        raise _refused(exc) from None
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_mutable(agent, _effective_caller(caller, user_domains))
        await lifecycle.set_card_fields(session, tid, agent_id, fields)
        return await lifecycle.card(session, tid, agent)


@router.post("/agents/{agent_id}/lifecycle")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.write",
    rate_limit="agent-write",
    idempotency="not-idempotent-state-transition",
    audit_event="agents.lifecycle.transition",
)
async def transition_agent_lifecycle(
    agent_id: UUID,
    body: AgentLifecycleIn,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """Move the agent to the next lifecycle state under the transition table, with a note."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id, lock=True)
        require_agent_mutable(agent, _effective_caller(caller, user_domains))
        try:
            entry, event = await lifecycle.transition(
                session, tid, agent, body.to, actor=_user_uuid_from_claims(user), note=body.note
            )
        except lifecycle.RegistryError as exc:
            raise _refused(exc) from None
        return {"id": str(agent_id), "registry": lifecycle.entry_dict(entry), "event": lifecycle.event_dict(event)}


@router.get("/agents/{agent_id}/lifecycle")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.lifecycle.read",
)
async def get_agent_lifecycle(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """The agent's lifecycle state and its transitions, newest first."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
        entry = await lifecycle.get_entry(session, tid, agent_id)
        history = await lifecycle.events(session, tid, agent_id)
        return {
            "id": str(agent_id),
            "registry": lifecycle.entry_dict(entry),
            "events": [lifecycle.event_dict(event) for event in history],
            "states": list(lifecycle.STATES),
            "transitions": {state: list(nexts) for state, nexts in lifecycle.TRANSITIONS.items()},
        }


@router.get("/agent-registry")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.registry.list",
)
async def list_agent_registry(
    state: str | None = None,
    risk_tier: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """The registry entries the caller may see, with each agent's name, type, domain and status."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    effective = _effective_caller(caller, user_domains)
    async with get_tenant_session(tid) as session:
        entries = await lifecycle.list_entries(session, tid, state=state, risk_tier=risk_tier)
        ids = [entry.agent_id for entry in entries]
        agents = {}
        if ids:
            rows = (
                (await session.execute(select(Agent).where(Agent.id.in_(ids), Agent.tenant_id == tid))).scalars().all()
            )
            agents = {agent.id: agent for agent in rows}
        from core.ownership import can_view_agent

        listed = []
        for entry in entries:
            agent = agents.get(entry.agent_id)
            if agent is None or not can_view_agent(agent, effective):
                continue
            listed.append(
                {
                    "agent_id": str(agent.id),
                    "name": agent.name,
                    "agent_type": agent.agent_type,
                    "domain": agent.domain,
                    "status": agent.status,
                    "owner_user_id": str(agent.owner_user_id) if getattr(agent, "owner_user_id", None) else None,
                    **lifecycle.entry_dict(entry),
                }
            )
        return {"entries": listed, "states": list(lifecycle.STATES), "risk_tiers": list(lifecycle.RISK_TIERS)}
