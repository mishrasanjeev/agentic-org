"""Real-time agent activity feed via WebSocket."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/feed/{tenant_id}")
async def live_feed(websocket: WebSocket, tenant_id: str):
    await websocket.accept()
    try:
        while True:
            data = {"type": "heartbeat", "tenant_id": tenant_id}
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
