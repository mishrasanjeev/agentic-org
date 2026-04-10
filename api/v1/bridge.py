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
    connected: bool
    tally_healthy: bool
    last_heartbeat: str | None


@router.post("/register", response_model=BridgeRegisterResponse)
async def register_bridge(
    req: BridgeRegisterRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> BridgeRegisterResponse:
    """Register a new bridge agent for a tenant."""
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

    live = get_bridge_status(bridge_id)

    return BridgeStatus(
        bridge_id=bridge_id,
        connector_type=reg.bridge_type,
        registered_at=reg.created_at.isoformat(),
        connected=live.get("connected", False),
        tally_healthy=live.get("tally_healthy", False),
        last_heartbeat=live.get("last_heartbeat"),
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
        live = get_bridge_status(reg.bridge_id)
        bridges.append({
            "bridge_id": reg.bridge_id,
            "bridge_token": meta.get("bridge_token", ""),
            "tenant_id": str(reg.tenant_id),
            "connector_type": reg.bridge_type,
            "tally_port": meta.get("tally_port", 9000),
            "label": meta.get("label", ""),
            "registered_at": reg.created_at.isoformat(),
            "connected": live.get("connected", False),
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
        )
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
