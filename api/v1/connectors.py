"""Connector endpoints."""
from __future__ import annotations

import uuid as _uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.connector import Connector
from core.schemas.api import ConnectorCreate

router = APIRouter()


def _connector_to_dict(conn: Connector) -> dict:
    return {
        "connector_id": str(conn.id),
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
        await session.flush()

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
        "health_check_at": connector.health_check_at.isoformat() if connector.health_check_at else None,
        "healthy": connector.status == "active",
    }
