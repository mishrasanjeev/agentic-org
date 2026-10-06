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
from core.agent_registry import dependencies, lifecycle, reliability
from core.database import get_tenant_session
from core.models.agent import Agent
from core.ownership import Caller, caller_from_request, can_view_agent, require_agent_mutable, require_agent_visible
from core.schemas.api import AgentCardIn, AgentLifecycleIn, AgentRatingIn

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


@router.get("/agents/{agent_id}/dependencies")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.dependencies.read",
)
async def get_agent_dependencies(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """The agent's dependency graph: models, prompt, tools and connectors, knowledge, policies,
    datasets, related agents and teams, as nodes and edges without prompt text or rule reasons."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    effective = _effective_caller(caller, user_domains)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, effective)

        async def _load(related_id):
            row = (
                await session.execute(select(Agent).where(Agent.id == related_id, Agent.tenant_id == tid))
            ).scalar_one_or_none()
            # A related agent the caller may not see is named by its id only.
            return row if row is not None and can_view_agent(row, effective) else None

        result = await dependencies.graph(session, tid, agent, load_agent=_load)
        return {"id": str(agent_id), "kinds": list(dependencies.KINDS), **result}


@router.get("/agents/{agent_id}/reliability")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.reliability.read",
)
async def get_agent_reliability(
    agent_id: UUID,
    days: int | None = None,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """Reliability metrics over the window (30 days by default, at most 365) and the rating summary."""
    _require_enabled()
    try:
        window = reliability.validate_window(days)
    except reliability.RatingError as exc:
        raise HTTPException(422, str(exc)) from None
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
        return {
            "id": str(agent_id),
            "reliability": await reliability.metrics(session, tid, agent, days=window),
            "rating": await reliability.rating_summary(session, tid, agent_id),
        }


@router.post("/agents/{agent_id}/rating")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="idempotent-one-rating-per-user",
    audit_event="agents.rating.set",
)
async def rate_agent(
    agent_id: UUID,
    body: AgentRatingIn,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """Rate an agent 1 to 5 with a short comment; a new rating by the same person replaces the old.

    Anyone who may see the agent may rate it; a request without a local user (an API key) cannot.
    """
    _require_enabled()
    user_id = _user_uuid_from_claims(user)
    if user_id is None:
        raise HTTPException(403, "A rating needs a signed-in user")
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = await _agent(session, tid, agent_id)
        require_agent_visible(agent, _effective_caller(caller, user_domains))
        try:
            rating = await reliability.rate(session, tid, agent_id, user_id, body.score, body.comment)
        except reliability.RatingError as exc:
            raise HTTPException(422, str(exc)) from None
        await session.flush()
        return {
            "id": str(agent_id),
            "score": rating.score,
            "comment": rating.comment,
            "rating": await reliability.rating_summary(session, tid, agent_id),
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
    domain: str | None = None,
    use_case: str | None = None,
    channel: str | None = None,
    q: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict:
    """The catalogue: the registry entries the caller may see, filtered by state, risk tier, domain,
    use case, channel and a search term, with each agent's name, type, domain and runtime status."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    effective = _effective_caller(caller, user_domains)
    async with get_tenant_session(tid) as session:
        entries = await lifecycle.list_entries(
            session, tid, state=state, risk_tier=risk_tier, use_case=use_case, channel=channel
        )
        ids = [entry.agent_id for entry in entries]
        agents = {}
        if ids:
            rows = (
                (await session.execute(select(Agent).where(Agent.id.in_(ids), Agent.tenant_id == tid))).scalars().all()
            )
            agents = {agent.id: agent for agent in rows}
        listed = []
        for entry in entries:
            agent = agents.get(entry.agent_id)
            if agent is None or not can_view_agent(agent, effective):
                continue
            if domain and agent.domain != domain:
                continue
            if not lifecycle.matches_search(agent, entry, q):
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
        return {
            "entries": listed,
            "states": list(lifecycle.STATES),
            "risk_tiers": list(lifecycle.RISK_TIERS),
            "channels": list(lifecycle.CHANNELS),
        }


@router.get("/agent-registry/templates")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="agents.registry.templates",
)
async def list_agent_templates(pack: str | None = None, tenant_id: str = Depends(get_current_tenant)) -> dict:
    """The agent templates the industry packs offer, in the card's terms; install a pack to create them."""
    _require_enabled()
    rows = lifecycle.templates()
    if pack:
        rows = [row for row in rows if row["pack"] == pack]
    return {"templates": rows}
