"""Real-time agent activity feed + HITL notifications via WebSocket."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = structlog.get_logger()

router = APIRouter()

# Active WebSocket connections per tenant
_connections: dict[str, list[WebSocket]] = {}


async def broadcast_to_tenant(tenant_id: str, data: dict) -> int:
    """Broadcast a message to all WebSocket connections for a tenant.

    Used by the approval system to push real-time HITL notifications.
    Returns the number of clients that received the message.
    """
    connections = _connections.get(tenant_id, [])
    sent = 0
    stale = []
    for ws in connections:
        try:
            await ws.send_text(json.dumps(data))
            sent += 1
        except Exception:
            stale.append(ws)
    # Clean up stale connections
    for ws in stale:
        connections.remove(ws)
    return sent


@router.websocket("/ws/feed/{tenant_id}")
async def live_feed(websocket: WebSocket, tenant_id: str):
    await websocket.accept()

    # Register connection
    if tenant_id not in _connections:
        _connections[tenant_id] = []
    _connections[tenant_id].append(websocket)

    logger.info("ws_connected", tenant_id=tenant_id, total=len(_connections[tenant_id]))

    try:
        while True:
            # Send heartbeat every 15 seconds
            data = {"type": "heartbeat", "tenant_id": tenant_id}
            await websocket.send_text(json.dumps(data))
            # Also listen for client messages (ping/pong, etc.)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=15)
                # Client sent something — could be a ping
                client_data = json.loads(msg)
                if client_data.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except TimeoutError:
                pass  # Normal — just send next heartbeat
    except WebSocketDisconnect:
        pass
    finally:
        # Unregister connection
        if tenant_id in _connections:
            try:
                _connections[tenant_id].remove(websocket)
            except ValueError:
                pass
            if not _connections[tenant_id]:
                del _connections[tenant_id]
        logger.info("ws_disconnected", tenant_id=tenant_id)
