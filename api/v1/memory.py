# SPDX-License-Identifier: Apache-2.0
"""Long-term memory: recall, remember, erase per subject, the retention policy and pruning."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user, get_user_domains, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _effective_caller, _user_uuid_from_claims
from core.database import get_tenant_session
from core.memory import long_term
from core.ownership import Caller, caller_from_request, require_agent_visible

logger = structlog.get_logger()
router = APIRouter()


class MemoryIn(BaseModel):
    model_config = {"extra": "forbid"}

    subject: str = Field(..., min_length=1, max_length=long_term.MAX_SUBJECT)
    content: str = Field(..., min_length=1, max_length=long_term.MAX_CONTENT)
    kind: str = Field("fact", max_length=16)
    agent_id: str | None = Field(None, max_length=64)
    importance: int = Field(3, ge=1, le=5)
    retention_days: int | None = Field(None, ge=1, le=long_term.MAX_RETENTION_DAYS)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "runtime_memory_disabled",
            "message": "Long-term memory is off for this deployment (AGENTICORG_RUNTIME_MEMORY_ENABLED).",
        },
    )


def _refused(exc: long_term.MemoryStoreError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _agent_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(422, detail={"error": "agent_id", "message": "agent_id is an agent id"}) from None


async def _authorised_agent(session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID | None, caller: Caller) -> None:
    """The named agent is the tenant's and the caller may see it; 404 otherwise, as for the agent itself."""
    if agent_id is None:
        return
    from sqlalchemy import select

    from core.models.agent import Agent

    agent = (
        await session.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")
    require_agent_visible(agent, caller)


@router.get("/memory/policy")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="memory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="memory.policy.read",
)
async def memory_policy(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The kinds, their default retention, the bounds and how erasure works."""
    return long_term.policy()


@router.get("/memory")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="memory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="memory.recall",
)
async def recall_memory(
    subject: str = Query(..., min_length=1, max_length=long_term.MAX_SUBJECT),
    agent_id: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(long_term.DEFAULT_RECALL, ge=1, le=long_term.MAX_RECALL),
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """What is remembered about a subject: the agent's own and the shared entries, unexpired, most important first."""
    if not long_term.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    agent_uuid = _agent_uuid(agent_id)
    async with get_tenant_session(tid) as session:
        await _authorised_agent(session, tid, agent_uuid, _effective_caller(caller, user_domains))
        rows = await long_term.recall(session, tid, subject=subject, agent_id=agent_uuid, query=q, limit=limit)
        entries = [long_term.entry_dict(r) for r in rows]
    return {"subject": long_term.normalise_subject(subject), "entries": entries, "total": len(entries)}


@router.post("/memory", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="memory.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-same-content-refreshes-expiry",
    audit_event="memory.remember",
)
async def remember_memory(
    body: MemoryIn,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
    user_domains: list[str] | None = Depends(get_user_domains),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Store one memory about a subject; the same content refreshes its expiry instead of duplicating."""
    if not long_term.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    agent_uuid = _agent_uuid(body.agent_id)
    try:
        async with get_tenant_session(tid) as session:
            await _authorised_agent(session, tid, agent_uuid, _effective_caller(caller, user_domains))
            row = await long_term.remember(
                session,
                tid,
                subject=body.subject,
                content=body.content,
                kind=body.kind,
                agent_id=agent_uuid,
                importance=body.importance,
                retention_days=body.retention_days,
                source="api",
                actor=_user_uuid_from_claims(user),
            )
            return long_term.entry_dict(row)
    except long_term.MemoryStoreError as exc:
        raise _refused(exc) from None


@router.delete("/memory", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="memory.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-erasure",
    audit_event="memory.erase",
)
async def erase_memory(
    subject: str = Query(..., min_length=1, max_length=long_term.MAX_SUBJECT),
    agent_id: str | None = Query(None, max_length=64),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Erase every entry about a subject, for one agent or for all; the count answers the request."""
    if not long_term.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            erased = await long_term.erase(session, tid, subject=subject, agent_id=_agent_uuid(agent_id))
    except long_term.MemoryStoreError as exc:
        raise _refused(exc) from None
    return {"subject": long_term.normalise_subject(subject), "erased": erased}


@router.post("/memory/prune", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="memory.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-prune",
    audit_event="memory.prune",
)
async def prune_memory(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Remove the tenant's expired entries now (the scheduled task does this nightly)."""
    if not long_term.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        pruned = await long_term.prune(session, tid)
    return {"pruned": pruned}
