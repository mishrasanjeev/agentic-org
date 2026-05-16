"""REST API for bridge management — register, status, list, deregister.

Persisted to PostgreSQL via the BridgeRegistration ORM model.
"""

from __future__ import annotations

import secrets
import uuid as _uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.bridge import BridgeRegistration

logger = structlog.get_logger()

router = APIRouter(prefix="/bridge", tags=["Bridge"])


class BridgeRegisterRequest(BaseModel):
    tenant_id: str
    connector_type: str = "tally"
    tally_port: int = 9000
    label: str = ""


class BridgeRegisterResponse(BaseModel):
    bridge_id: str
    bridge_token: str
    connector_type: str
    ws_url: str


class BridgeStatus(BaseModel):
    bridge_id: str
    connector_type: str
    registered_at: str
    status: str = "disconnected"
    connected: bool
    local_connected: bool = False
    tally_healthy: bool
    last_heartbeat: str | None
    disconnected_at: str | None = None
    connection_owner: str | None = None
    reconnect_count: int = 0


@router.post("/register", response_model=BridgeRegisterResponse)
async def register_bridge(
    req: BridgeRegisterRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> BridgeRegisterResponse:
    """Register a new bridge agent for a tenant."""
    if req.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    bridge_id = str(_uuid.uuid4())
    bridge_token = secrets.token_urlsafe(48)
    ws_url = f"wss://app.agenticorg.ai/api/v1/ws/bridge/{bridge_id}"
    tid = _uuid.UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        registration = BridgeRegistration(
            tenant_id=tid,
            bridge_id=bridge_id,
            bridge_type=req.connector_type,
            url=ws_url,
            status="active",
            metadata_={
                "bridge_token": bridge_token,
                "tally_port": req.tally_port,
                "label": req.label,
            },
        )
        session.add(registration)

    logger.info(
        "bridge_registered",
        bridge_id=bridge_id,
        tenant_id=tenant_id,
        connector_type=req.connector_type,
    )

    return BridgeRegisterResponse(
        bridge_id=bridge_id,
        bridge_token=bridge_token,
        connector_type=req.connector_type,
        ws_url=ws_url,
    )


@router.get("/{bridge_id}/status", response_model=BridgeStatus)
async def bridge_status(
    bridge_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> BridgeStatus:
    """Check the status of a registered bridge."""
    from bridge.server_handler import get_bridge_status

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(BridgeRegistration).where(
                BridgeRegistration.bridge_id == bridge_id,
                BridgeRegistration.tenant_id == tid,
            )
        )
        reg = result.scalar_one_or_none()

    if not reg:
        raise HTTPException(status_code=404, detail="Bridge not found")

    live = await get_bridge_status(bridge_id, tenant_id=tenant_id)

    return BridgeStatus(
        bridge_id=bridge_id,
        connector_type=reg.bridge_type,
        registered_at=reg.created_at.isoformat(),
        status=live.get("status", "disconnected"),
        connected=live.get("connected", False),
        local_connected=live.get("local_connected", False),
        tally_healthy=live.get("tally_healthy", False),
        last_heartbeat=live.get("last_heartbeat"),
        disconnected_at=live.get("disconnected_at"),
        connection_owner=live.get("connection_owner"),
        reconnect_count=int(live.get("reconnect_count") or 0),
    )


@router.get("/list")
async def list_bridges(
    tenant_id: str = Depends(get_current_tenant),
) -> list[dict[str, Any]]:
    """List all registered bridges for the tenant."""
    from bridge.server_handler import get_bridge_status

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(BridgeRegistration).where(
                BridgeRegistration.tenant_id == tid,
                BridgeRegistration.status == "active",
            )
        )
        rows = result.scalars().all()

    bridges = []
    for reg in rows:
        meta = reg.metadata_ or {}
        live = await get_bridge_status(reg.bridge_id, tenant_id=tenant_id)
        bridges.append({
            "bridge_id": reg.bridge_id,
            "tenant_id": str(reg.tenant_id),
            "connector_type": reg.bridge_type,
            "tally_port": meta.get("tally_port", 9000),
            "label": meta.get("label", ""),
            "registered_at": reg.created_at.isoformat(),
            "status": live.get("status", "disconnected"),
            "connected": live.get("connected", False),
            "local_connected": live.get("local_connected", False),
            "tally_healthy": live.get("tally_healthy", False),
            "last_heartbeat": live.get("last_heartbeat"),
            "disconnected_at": live.get("disconnected_at"),
            "connection_owner": live.get("connection_owner"),
            "reconnect_count": int(live.get("reconnect_count") or 0),
        })
    return bridges


@router.delete("/{bridge_id}")
async def deregister_bridge(
    bridge_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, str]:
    """Deregister a bridge."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(BridgeRegistration).where(
                BridgeRegistration.bridge_id == bridge_id,
                BridgeRegistration.tenant_id == tid,
            )
        )
        reg = result.scalar_one_or_none()
        if not reg:
            raise HTTPException(status_code=404, detail="Bridge not found")
        await session.delete(reg)

    logger.info("bridge_deregistered", bridge_id=bridge_id)
    return {"status": "deregistered", "bridge_id": bridge_id}


@router.post("/route/{connector_type}")
async def route_through_bridge(
    connector_type: str,
    payload: dict[str, Any],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Route a request through a bridge to a local connector.

    Used by TallyConnector in bridge mode -- sends XML through the
    WebSocket tunnel to the CA's local Tally instance.
    """
    from bridge.server_handler import route_to_bridge
    from bridge.state import BridgeRouteError

    bridge_id = payload.get("bridge_id", "")
    if not bridge_id:
        raise HTTPException(status_code=400, detail="bridge_id required")

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(BridgeRegistration).where(
                BridgeRegistration.bridge_id == bridge_id,
                BridgeRegistration.tenant_id == tid,
            )
        )
        reg = result.scalar_one_or_none()

    if not reg:
        raise HTTPException(status_code=404, detail="Bridge not found")

    try:
        result = await route_to_bridge(
            bridge_id=bridge_id,
            xml_body=payload.get("xml_body", ""),
            timeout=30.0,
            tenant_id=tenant_id,
            connector_type=connector_type,
            request_id=payload.get("request_id"),
            idempotency_key=payload.get("idempotency_key") or payload.get("request_id"),
        )
        return result
    except BridgeRouteError as exc:
        status_by_code = {
            "bridge_not_registered": 404,
            "tenant_mismatch": 403,
            "bridge_disconnected": 503,
            "bridge_broker_unavailable": 503,
            "bridge_publish_failed": 503,
            "request_timed_out": 504,
            "malformed_response": 502,
            "bridge_error": 502,
        }
        raise HTTPException(
            status_code=status_by_code.get(exc.code, 502),
            detail=exc.to_detail(),
        ) from exc
