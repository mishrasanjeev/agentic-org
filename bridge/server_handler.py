"""Cloud-side WebSocket handler for bridge connections."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from core.database import async_session_factory
from core.models.bridge import BridgeRegistration

logger = structlog.get_logger()

router = APIRouter()

# Active bridge connections: bridge_id -> BridgeConnection
_active_bridges: dict[str, BridgeConnection] = {}

# Pending request futures: request_id -> (bridge_id, asyncio.Future)
_pending_requests: dict[str, tuple[str, asyncio.Future]] = {}


class BridgeConnection:
    """Represents an active bridge agent connection."""

    def __init__(self, bridge_id: str, websocket: WebSocket):
        self.bridge_id = bridge_id
        self.websocket = websocket
        self.connected_at = datetime.now(UTC)
        self.last_heartbeat = datetime.now(UTC)
        self.tally_healthy = False

    @property
    def status(self) -> dict[str, Any]:
        return {
            "bridge_id": self.bridge_id,
            "connected": True,
            "connected_at": self.connected_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "tally_healthy": self.tally_healthy,
        }


async def _get_bridge_registration(bridge_id: str) -> BridgeRegistration | None:
    """Load the bridge registration row used for auth."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BridgeRegistration).where(BridgeRegistration.bridge_id == bridge_id)
        )
        return result.scalar_one_or_none()


@router.websocket("/ws/bridge/{bridge_id}")
async def bridge_ws(websocket: WebSocket, bridge_id: str) -> None:
    """WebSocket endpoint for bridge agent connections."""
    await websocket.accept()

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        auth_msg = json.loads(raw)
        if auth_msg.get("type") != "auth" or auth_msg.get("bridge_id") != bridge_id:
            await websocket.send_text(json.dumps({
                "type": "auth_error",
                "error": "Invalid auth handshake",
            }))
            await websocket.close()
            return
    except (TimeoutError, json.JSONDecodeError):
        await websocket.close()
        return

    token = auth_msg.get("token", "")
    if not token:
        await websocket.send_text(json.dumps({
            "type": "auth_error",
            "error": "Missing bridge token",
        }))
        await websocket.close()
        return

    registration = await _get_bridge_registration(bridge_id)
    expected_token = ((registration.metadata_ or {}) if registration else {}).get("bridge_token", "")
    if (
        registration is None
        or registration.status != "active"
        or not expected_token
        or not secrets.compare_digest(token, expected_token)
    ):
        await websocket.send_text(json.dumps({
            "type": "auth_error",
            "error": "Invalid bridge token",
        }))
        await websocket.close()
        return

    await websocket.send_text(json.dumps({"type": "auth_ok"}))

    conn = BridgeConnection(bridge_id, websocket)
    _active_bridges[bridge_id] = conn
    logger.info("bridge_connected", bridge_id=bridge_id)

    try:
        async for raw in websocket.iter_text():
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "heartbeat":
                conn.last_heartbeat = datetime.now(UTC)
                conn.tally_healthy = msg.get("tally_healthy", False)
                await websocket.send_text(json.dumps({"type": "heartbeat_ack"}))

            elif msg_type == "response":
                request_id = msg.get("request_id")
                pending = _pending_requests.pop(request_id, None)
                if pending:
                    _, future = pending
                    if not future.done():
                        future.set_result(msg)

            elif msg_type == "pong":
                pass

            else:
                logger.warning("bridge_unknown_msg", bridge_id=bridge_id, type=msg_type)

    except WebSocketDisconnect:
        logger.info("bridge_disconnected", bridge_id=bridge_id)
    finally:
        _active_bridges.pop(bridge_id, None)
        for request_id, (pending_bridge_id, future) in list(_pending_requests.items()):
            if pending_bridge_id != bridge_id:
                continue
            _pending_requests.pop(request_id, None)
            if not future.done():
                future.set_exception(RuntimeError("Bridge disconnected"))


async def route_to_bridge(
    bridge_id: str,
    xml_body: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Route an XML request through a bridge to the CA's local Tally."""
    conn = _active_bridges.get(bridge_id)
    if not conn:
        raise RuntimeError(f"Bridge {bridge_id} is not connected")

    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_requests[request_id] = (bridge_id, future)

    await conn.websocket.send_text(json.dumps({
        "type": "post_xml",
        "request_id": request_id,
        "method": "post_xml",
        "xml_body": xml_body,
    }))

    logger.info("bridge_request_sent", bridge_id=bridge_id, request_id=request_id)

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except TimeoutError as exc:
        _pending_requests.pop(request_id, None)
        raise RuntimeError(f"Bridge request timed out after {timeout}s") from exc


def get_bridge_status(bridge_id: str) -> dict[str, Any]:
    """Get the status of a bridge connection."""
    conn = _active_bridges.get(bridge_id)
    if conn:
        return conn.status
    return {"bridge_id": bridge_id, "connected": False}


def list_active_bridges() -> list[dict[str, Any]]:
    """List all active bridge connections."""
    return [conn.status for conn in _active_bridges.values()]
