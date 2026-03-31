"""Connector endpoints."""

from __future__ import annotations

import uuid as _uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.connector import Connector
from core.schemas.api import ConnectorCreate, ConnectorUpdate

router = APIRouter()


def _connector_to_dict(conn: Connector) -> dict:
    return {
        "id": str(conn.id),
        "connector_id": str(conn.id),  # kept for backward compat
        "name": conn.name,
        "category": conn.category,
        "description": conn.description,
        "base_url": conn.base_url,
        "auth_type": conn.auth_type,
        "tool_functions": conn.tool_functions,
        "data_schema_ref": conn.data_schema_ref,
        "rate_limit_rpm": conn.rate_limit_rpm,
        "timeout_ms": conn.timeout_ms,
        "status": conn.status,
        "health_check_at": conn.health_check_at.isoformat() if conn.health_check_at else None,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
    }


# ── GET /connectors ─────────────────────────────────────────────────────────
@router.get("/connectors")
async def list_connectors(
    category: str | None = None,
    page: int = 1,
    per_page: int = 50,
    tenant_id: str = Depends(get_current_tenant),
):
    from sqlalchemy import func

    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(Connector).where(Connector.tenant_id == tid)
        count_query = select(func.count()).select_from(Connector).where(Connector.tenant_id == tid)
        if category:
            query = query.where(Connector.category == category)
            count_query = count_query.where(Connector.category == category)
        total = (await session.execute(count_query)).scalar() or 0
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        connectors = result.scalars().all()
    return {
        "items": [_connector_to_dict(c) for c in connectors],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── POST /connectors ────────────────────────────────────────────────────────
@router.post("/connectors", status_code=201)
async def register_connector(
    body: ConnectorCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        connector = Connector(
            tenant_id=tid,
            name=body.name,
            category=body.category,
            base_url=body.base_url,
            auth_type=body.auth_type,
            auth_config=body.auth_config,
            secret_ref=body.secret_ref,
            tool_functions=body.tool_functions,
            data_schema_ref=body.data_schema_ref,
            rate_limit_rpm=body.rate_limit_rpm,
            status="active",
        )
        session.add(connector)
        try:
            await session.flush()
        except IntegrityError:
            raise HTTPException(409, f"Connector '{body.name}' already exists") from None

    return _connector_to_dict(connector)


# ── GET /connectors/{conn_id} ────────────────────────────────────────────────
@router.get("/connectors/{conn_id}")
async def get_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")
    return _connector_to_dict(connector)


# ── PUT /connectors/{conn_id} ──────────────────────────────────────────────
@router.put("/connectors/{conn_id}")
async def update_connector(
    conn_id: UUID,
    body: ConnectorUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            raise HTTPException(404, "Connector not found")

        for field, value in body.model_dump(exclude_none=True).items():
            setattr(connector, field, value)

    return _connector_to_dict(connector)


# ── GET /connectors/{conn_id}/health ─────────────────────────────────────────
@router.get("/connectors/{conn_id}/health")
async def connector_health(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
        )
        connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(404, "Connector not found")

    return {
        "connector_id": str(connector.id),
        "name": connector.name,
        "status": connector.status,
        "health_check_at": connector.health_check_at.isoformat()
        if connector.health_check_at
        else None,
        "healthy": connector.status == "active",
    }
