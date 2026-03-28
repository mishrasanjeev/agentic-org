"""HITL approval endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_user_domains
from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.hitl import HITLQueue
from core.schemas.api import HITLDecision, PaginatedResponse

router = APIRouter()


def _hitl_to_dict(item: HITLQueue) -> dict:
    return {
        "id": str(item.id),
        "workflow_run_id": str(item.workflow_run_id),
        "agent_id": str(item.agent_id),
        "title": item.title,
        "trigger_type": item.trigger_type,
        "priority": item.priority,
        "status": item.status,
        "assignee_role": item.assignee_role,
        "decision_options": item.decision_options,
        "context": item.context,
        "decision": item.decision,
        "decision_by": str(item.decision_by) if item.decision_by else None,
        "decision_at": item.decision_at.isoformat() if item.decision_at else None,
        "decision_notes": item.decision_notes,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ── GET /approvals ───────────────────────────────────────────────────────────
@router.get("/approvals", response_model=PaginatedResponse)
async def list_approvals(
    domain: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        base = select(HITLQueue).where(HITLQueue.tenant_id == tid)
        count_base = select(func.count()).select_from(HITLQueue).where(HITLQueue.tenant_id == tid)

        # RBAC domain filtering via Agent subquery
        if user_domains is not None:
            domain_agent_ids = (
                select(Agent.id).where(Agent.domain.in_(user_domains)).scalar_subquery()
            )
            base = base.where(HITLQueue.agent_id.in_(domain_agent_ids))
            count_base = count_base.where(HITLQueue.agent_id.in_(domain_agent_ids))

        if priority:
            base = base.where(HITLQueue.priority == priority)
            count_base = count_base.where(HITLQueue.priority == priority)
        if status:
            base = base.where(HITLQueue.status == status)
            count_base = count_base.where(HITLQueue.status == status)

        # Default: show only pending items
        if not status:
            base = base.where(HITLQueue.status == "pending")
            count_base = count_base.where(HITLQueue.status == "pending")

        total = (await session.execute(count_base)).scalar() or 0

        query = (
            base.order_by(HITLQueue.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
        result = await session.execute(query)
        items = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_hitl_to_dict(i) for i in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ── POST /approvals/{id}/decide ─────────────────────────────────────────────
@router.post("/approvals/{hitl_id}/decide")
async def decide(
    hitl_id: UUID,
    body: HITLDecision,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(HITLQueue).where(HITLQueue.id == hitl_id, HITLQueue.tenant_id == tid)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(404, "HITL item not found")
        if item.status != "pending":
            raise HTTPException(409, f"HITL item already resolved with status '{item.status}'")

        # Check expiry
        if item.expires_at and datetime.now(UTC) > item.expires_at:
            raise HTTPException(410, "HITL item has expired")

        item.decision = body.decision
        item.decision_notes = body.notes if body.notes else None
        item.decision_at = datetime.now(UTC)
        item.status = "decided"

    return {
        "hitl_id": str(hitl_id),
        "decision": body.decision,
        "status": "decided",
        "decided_at": item.decision_at.isoformat(),
    }
