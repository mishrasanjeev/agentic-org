"""REST API for bridge management — register, status, list, deregister."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from api.deps import get_current_tenant
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = structlog.get_logger()

router = APIRouter(prefix="/bridge", tags=["Bridge"])

# In-memory bridge registry (DB-backed in production)
_bridge_registry: dict[str, dict[str, Any]] = {}


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
    tenant: dict = Depends(get_current_tenant),
) -> BridgeRegisterResponse:
    """Register a new bridge agent for a tenant."""
    bridge_id = str(uuid.uuid4())
    bridge_token = secrets.token_urlsafe(48)

    _bridge_registry[bridge_id] = {
        "bridge_id": bridge_id,
        "bridge_token": bridge_token,
        "tenant_id": tenant.get("tenant_id", req.tenant_id),
        "connector_type": req.connector_type,
        "tally_port": req.tally_port,
        "label": req.label,
        "registered_at": datetime.now(UTC).isoformat(),
    }

    logger.info(
        "bridge_registered",
        bridge_id=bridge_id,
        tenant_id=tenant.get("tenant_id"),
        connector_type=req.connector_type,
    )

    return BridgeRegisterResponse(
        bridge_id=bridge_id,
        bridge_token=bridge_token,
        connector_type=req.connector_type,
        ws_url=f"wss://app.agenticorg.ai/api/v1/ws/bridge/{bridge_id}",
    )


@router.get("/{bridge_id}/status", response_model=BridgeStatus)
async def bridge_status(
    bridge_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> BridgeStatus:
    """Check the status of a registered bridge."""
    from bridge.server_handler import get_bridge_status

    reg = _bridge_registry.get(bridge_id)
    if not reg:
        raise HTTPException(status_code=404, detail="Bridge not found")

    live = get_bridge_status(bridge_id)

    return BridgeStatus(
        bridge_id=bridge_id,
        connector_type=reg["connector_type"],
        registered_at=reg["registered_at"],
        connected=live.get("connected", False),
        tally_healthy=live.get("tally_healthy", False),
        last_heartbeat=live.get("last_heartbeat"),
    )


@router.get("/list")
async def list_bridges(
    tenant: dict = Depends(get_current_tenant),
) -> list[dict[str, Any]]:
    """List all registered bridges for the tenant."""
    from bridge.server_handler import get_bridge_status

    tenant_id = tenant.get("tenant_id", "")
    bridges = []
    for reg in _bridge_registry.values():
        if reg["tenant_id"] == tenant_id:
            live = get_bridge_status(reg["bridge_id"])
            bridges.append({**reg, "connected": live.get("connected", False)})
    return bridges


@router.delete("/{bridge_id}")
async def deregister_bridge(
    bridge_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> dict[str, str]:
    """Deregister a bridge."""
    reg = _bridge_registry.pop(bridge_id, None)
    if not reg:
        raise HTTPException(status_code=404, detail="Bridge not found")

    logger.info("bridge_deregistered", bridge_id=bridge_id)
    return {"status": "deregistered", "bridge_id": bridge_id}


@router.post("/route/{connector_type}")
async def route_through_bridge(
    connector_type: str,
    payload: dict[str, Any],
    tenant: dict = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Route a request through a bridge to a local connector.

    Used by TallyConnector in bridge mode — sends XML through the
    WebSocket tunnel to the CA's local Tally instance.
    """
    from bridge.server_handler import route_to_bridge

    bridge_id = payload.get("bridge_id", "")
    if not bridge_id:
        raise HTTPException(status_code=400, detail="bridge_id required")

    reg = _bridge_registry.get(bridge_id)
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
