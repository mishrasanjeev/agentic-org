"""Audit log endpoint."""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.audit import AuditLog
from core.schemas.api import PaginatedResponse

router = APIRouter()


def _audit_to_dict(entry: AuditLog) -> dict:
    return {
        "id": str(entry.id),
        "event_type": entry.event_type,
        "actor_type": entry.actor_type,
        "actor_id": entry.actor_id,
        "agent_id": str(entry.agent_id) if entry.agent_id else None,
        "workflow_run_id": str(entry.workflow_run_id) if entry.workflow_run_id else None,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "action": entry.action,
        "outcome": entry.outcome,
        "details": entry.details,
        "trace_id": entry.trace_id,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


# ── GET /audit ───────────────────────────────────────────────────────────────
@router.get("/audit", response_model=PaginatedResponse)
async def query_audit(
    event_type: str | None = None,
    agent_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    per_page: int = 50,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        base = select(AuditLog).where(AuditLog.tenant_id == tid)
        count_base = select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tid)

        if event_type:
            base = base.where(AuditLog.event_type == event_type)
            count_base = count_base.where(AuditLog.event_type == event_type)

        if agent_id:
            agent_uuid = _uuid.UUID(agent_id)
            base = base.where(AuditLog.agent_id == agent_uuid)
            count_base = count_base.where(AuditLog.agent_id == agent_uuid)

        if date_from:
            dt_from = datetime.fromisoformat(date_from)
            base = base.where(AuditLog.created_at >= dt_from)
            count_base = count_base.where(AuditLog.created_at >= dt_from)

        if date_to:
            dt_to = datetime.fromisoformat(date_to)
            base = base.where(AuditLog.created_at <= dt_to)
            count_base = count_base.where(AuditLog.created_at <= dt_to)

        total = (await session.execute(count_base)).scalar() or 0

        query = (
            base.order_by(AuditLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
        result = await session.execute(query)
        entries = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_audit_to_dict(e) for e in entries],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
