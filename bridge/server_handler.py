"""Cloud-side WebSocket handler for bridge connections."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from api.route_metadata import route_meta
from bridge.state import (
    BridgeRouteError,
    BridgeSessionRecord,
    BrokerSubscription,
    connection_owner,
    get_bridge_broker,
    get_bridge_state_repository,
    payload_hash,
)
from core.database import async_session_factory
from core.models.bridge import BridgeRegistration

logger = structlog.get_logger()

router = APIRouter()

# Local socket registry only. PostgreSQL is authoritative for status.
_active_bridges: dict[str, BridgeConnection] = {}

# Local waiter futures only. PostgreSQL is authoritative for request state.
_pending_requests: dict[str, asyncio.Future] = {}


class BridgeConnection:
    """Represents an active bridge agent connection on this process."""

    def __init__(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        websocket: WebSocket,
        request_subscription: BrokerSubscription,
    ) -> None:
        self.bridge_id = bridge_id
        self.tenant_id = tenant_id
        self.connector_type = connector_type
        self.websocket = websocket
        self.request_subscription = request_subscription
        self.connected_at = datetime.now(UTC)
        self.last_heartbeat = datetime.now(UTC)
        self.tally_healthy = False

    @property
    def status(self) -> dict[str, Any]:
        return {
            "bridge_id": self.bridge_id,
            "tenant_id": self.tenant_id,
            "connector_type": self.connector_type,
            "connected": True,
            "local_connected": True,
            "connected_at": self.connected_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "tally_healthy": self.tally_healthy,
        }


async def _get_bridge_registration(bridge_id: str) -> BridgeRegistration | None:
    """Load the bridge registration row used for bridge-token auth."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BridgeRegistration).where(BridgeRegistration.bridge_id == bridge_id)
        )
        return result.scalar_one_or_none()


async def _register_local_connection(
    *,
    bridge_id: str,
    tenant_id: str,
    connector_type: str,
    websocket: WebSocket,
) -> BridgeConnection:
    async def _broker_handler(message: dict[str, Any]) -> None:
        await _send_brokered_request_to_bridge(bridge_id, message)

    request_subscription = await get_bridge_broker().subscribe_requests(
        bridge_id,
        _broker_handler,
    )
    conn = BridgeConnection(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
        connector_type=connector_type,
        websocket=websocket,
        request_subscription=request_subscription,
    )
    _active_bridges[bridge_id] = conn
    await get_bridge_state_repository().connect_session(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
        connector_type=connector_type,
        tally_healthy=False,
        owner=connection_owner(),
        process_id=os.getpid(),
        metadata={"source": "websocket_connect"},
    )
    return conn


@router.websocket("/ws/bridge/{bridge_id}")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="bridge.websocket.token_protected.sensitive.write",
    rate_limit="bridge-websocket-connect",
    idempotency="connection-session-upsert-by-bridge-id",
    audit_event="bridge.websocket.connect",
    public_reason="bridge-token-handshake-validates-registration",
)
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

    conn: BridgeConnection | None = None
    try:
        conn = await _register_local_connection(
            bridge_id=bridge_id,
            tenant_id=str(registration.tenant_id),
            connector_type=registration.bridge_type,
            websocket=websocket,
        )
        logger.info("bridge_connected", bridge_id=bridge_id, tenant_id=str(registration.tenant_id))

        async for raw in websocket.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("bridge_socket_malformed_json", bridge_id=bridge_id, error=str(exc))
                continue

            msg_type = msg.get("type", "")

            if msg_type == "heartbeat":
                await handle_bridge_heartbeat(
                    bridge_id=bridge_id,
                    tenant_id=conn.tenant_id,
                    tally_healthy=bool(msg.get("tally_healthy", False)),
                )
                conn.last_heartbeat = datetime.now(UTC)
                conn.tally_healthy = bool(msg.get("tally_healthy", False))
                await websocket.send_text(json.dumps({"type": "heartbeat_ack"}))

            elif msg_type == "response":
                await handle_bridge_response(msg, tenant_id=conn.tenant_id)

            elif msg_type == "pong":
                pass

            else:
                logger.warning("bridge_unknown_msg", bridge_id=bridge_id, type=msg_type)

    except WebSocketDisconnect:
        logger.info("bridge_disconnected", bridge_id=bridge_id)
    finally:
        if conn is not None:
            await handle_bridge_disconnect(
                bridge_id=bridge_id,
                tenant_id=conn.tenant_id,
                reason="Bridge disconnected",
            )


async def handle_bridge_heartbeat(
    *,
    bridge_id: str,
    tenant_id: str,
    tally_healthy: bool,
) -> BridgeSessionRecord:
    """Persist bridge heartbeat state."""
    return await get_bridge_state_repository().heartbeat(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
        tally_healthy=tally_healthy,
    )


async def handle_bridge_disconnect(*, bridge_id: str, tenant_id: str, reason: str) -> None:
    """Mark session disconnected and active requests orphaned."""
    conn = _active_bridges.pop(bridge_id, None)
    if conn is not None:
        await conn.request_subscription.close()

    await get_bridge_state_repository().disconnect_session(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
        reason=reason,
    )
    orphaned = await get_bridge_state_repository().mark_bridge_requests_orphaned(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
        reason=reason,
    )
    for request in orphaned:
        future = _pending_requests.pop(request.request_id, None)
        if future and not future.done():
            future.set_exception(
                BridgeRouteError(
                    reason,
                    code="bridge_disconnected",
                    request_id=request.request_id,
                    bridge_id=bridge_id,
                )
            )


async def _send_brokered_request_to_bridge(bridge_id: str, message: dict[str, Any]) -> None:
    conn = _active_bridges.get(bridge_id)
    request_id = str(message.get("request_id") or "")
    tenant_id = str(message.get("tenant_id") or (conn.tenant_id if conn else ""))
    if not conn:
        if request_id:
            await get_bridge_state_repository().mark_failed(
                request_id=request_id,
                tenant_id=tenant_id or None,
                code="bridge_not_local",
                message="Bridge is not connected to this process",
            )
            await get_bridge_broker().publish_response(
                request_id,
                {
                    "type": "response",
                    "request_id": request_id,
                    "status": "error",
                    "error_category": "bridge_disconnected",
                    "error": "Bridge is not connected to this process",
                },
            )
        return

    sent_record = await get_bridge_state_repository().mark_sent(
        request_id,
        tenant_id=tenant_id or None,
    )
    if sent_record is None:
        await get_bridge_broker().publish_response(
            request_id,
            {
                "type": "response",
                "request_id": request_id,
                "status": "error",
                "error_category": "bridge_request_not_found",
                "error": "Bridge request was not durably registered",
            },
        )
        return

    try:
        await conn.websocket.send_text(json.dumps({
            "type": "post_xml",
            "request_id": request_id,
            "method": message.get("method", "post_xml"),
            "xml_body": message.get("xml_body", ""),
        }))
    except Exception as exc:
        await get_bridge_state_repository().mark_failed(
            request_id=request_id,
            tenant_id=tenant_id or None,
            code="bridge_send_failed",
            message=str(exc),
        )
        await get_bridge_broker().publish_response(
            request_id,
            {
                "type": "response",
                "request_id": request_id,
                "status": "error",
                "error_category": "bridge_send_failed",
                "error": "Failed to send request to bridge socket",
            },
        )
        return
    logger.info("bridge_request_sent", bridge_id=bridge_id, request_id=request_id)


async def handle_bridge_response(
    msg: dict[str, Any],
    *,
    tenant_id: str | None = None,
) -> None:
    """Persist a bridge response and fan it back to the waiting API pod."""
    request_id = str(msg.get("request_id") or "")
    if not request_id:
        logger.warning("bridge_response_missing_request_id")
        return

    status = msg.get("status")
    metadata = {
        "bridge_status": status,
        "received_at": datetime.now(UTC).isoformat(),
    }
    if status == "ok" and isinstance(msg.get("xml_response"), str):
        result = {
            "type": "response",
            "request_id": request_id,
            "status": "ok",
            "xml_response": msg["xml_response"],
        }
        _record, transitioned = await get_bridge_state_repository().mark_responded(
            request_id=request_id,
            tenant_id=tenant_id,
            result=result,
            response_metadata=metadata,
        )
        if transitioned:
            await get_bridge_broker().publish_response(request_id, result)
            _complete_local_waiter(request_id, result)
        elif _record is None:
            logger.warning("bridge_response_without_request", request_id=request_id)
        return

    if status == "error" and isinstance(msg.get("error"), str):
        error_message = str(msg.get("error"))
        result = {
            "type": "response",
            "request_id": request_id,
            "status": "error",
            "error_category": "bridge_error",
            "error": error_message,
        }
        _record, transitioned = await get_bridge_state_repository().mark_failed(
            request_id=request_id,
            tenant_id=tenant_id,
            code="bridge_error",
            message=error_message,
            response_metadata=metadata,
        )
        if transitioned:
            await get_bridge_broker().publish_response(request_id, result)
            _complete_local_waiter(request_id, result)
        elif _record is None:
            logger.warning("bridge_response_without_request", request_id=request_id)
        return

    error_result = {
        "type": "response",
        "request_id": request_id,
        "status": "error",
        "error_category": "malformed_response",
        "error": "Bridge returned a malformed response",
    }
    _record, transitioned = await get_bridge_state_repository().mark_failed(
        request_id=request_id,
        tenant_id=tenant_id,
        code="malformed_response",
        message="Bridge returned a malformed response",
        response_metadata={**metadata, "raw_keys": sorted(msg.keys())},
    )
    if transitioned:
        await get_bridge_broker().publish_response(request_id, error_result)
        _complete_local_waiter(request_id, error_result)
    elif _record is None:
        logger.warning("bridge_response_without_request", request_id=request_id)


def _complete_local_waiter(request_id: str, message: dict[str, Any]) -> None:
    future = _pending_requests.pop(request_id, None)
    if future and not future.done():
        future.set_result(message)


async def route_to_bridge(
    bridge_id: str,
    xml_body: str,
    timeout: float = 30.0,
    *,
    tenant_id: str | None = None,
    connector_type: str = "tally",
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Route an XML request through a durable bridge request lifecycle."""
    session = await get_bridge_state_repository().get_session(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
    )
    if not session:
        if tenant_id is not None:
            visible_session = await get_bridge_state_repository().get_session(
                bridge_id=bridge_id,
                tenant_id=None,
            )
            if visible_session is not None and visible_session.tenant_id != tenant_id:
                raise BridgeRouteError(
                    "Tenant mismatch for bridge route",
                    code="tenant_mismatch",
                    bridge_id=bridge_id,
                )
        raise BridgeRouteError(
            f"Bridge {bridge_id} is not registered or not visible to this tenant",
            code="bridge_not_registered",
            bridge_id=bridge_id,
        )
    if tenant_id is not None and session.tenant_id != tenant_id:
        raise BridgeRouteError(
            "Tenant mismatch for bridge route",
            code="tenant_mismatch",
            bridge_id=bridge_id,
        )

    effective_request_id = request_id or str(uuid.uuid4())
    record = await get_bridge_state_repository().create_request(
        request_id=effective_request_id,
        bridge_id=bridge_id,
        tenant_id=session.tenant_id,
        connector_type=connector_type or session.connector_type,
        method="post_xml",
        payload_hash_value=payload_hash(xml_body),
        timeout_seconds=timeout,
        idempotency_key=idempotency_key,
    )
    effective_request_id = record.request_id

    if not session.connected:
        await get_bridge_state_repository().mark_failed(
            request_id=effective_request_id,
            tenant_id=session.tenant_id,
            code="bridge_disconnected",
            message=f"Bridge {bridge_id} is disconnected",
        )
        raise BridgeRouteError(
            f"Bridge {bridge_id} is disconnected",
            code="bridge_disconnected",
            request_id=effective_request_id,
            bridge_id=bridge_id,
        )

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_requests[effective_request_id] = future

    async def _response_handler(message: dict[str, Any]) -> None:
        _complete_local_waiter(effective_request_id, message)

    try:
        response_subscription = await get_bridge_broker().subscribe_response(
            effective_request_id,
            _response_handler,
        )
    except Exception as exc:
        _pending_requests.pop(effective_request_id, None)
        await get_bridge_state_repository().mark_failed(
            request_id=effective_request_id,
            tenant_id=session.tenant_id,
            code="bridge_broker_unavailable",
            message=str(exc),
        )
        raise BridgeRouteError(
            "Bridge broker unavailable",
            code="bridge_broker_unavailable",
            request_id=effective_request_id,
            bridge_id=bridge_id,
        ) from exc

    try:
        await get_bridge_broker().publish_request(
            bridge_id,
            {
                "type": "post_xml",
                "request_id": effective_request_id,
                "bridge_id": bridge_id,
                "tenant_id": session.tenant_id,
                "method": "post_xml",
                "xml_body": xml_body,
            },
        )
    except Exception as exc:
        _pending_requests.pop(effective_request_id, None)
        await response_subscription.close()
        await get_bridge_state_repository().mark_failed(
            request_id=effective_request_id,
            tenant_id=session.tenant_id,
            code="bridge_publish_failed",
            message=str(exc),
        )
        raise BridgeRouteError(
            "Bridge broker publish failed",
            code="bridge_publish_failed",
            request_id=effective_request_id,
            bridge_id=bridge_id,
        ) from exc

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except TimeoutError as exc:
        _pending_requests.pop(effective_request_id, None)
        await get_bridge_state_repository().mark_timed_out(
            effective_request_id,
            tenant_id=session.tenant_id,
        )
        raise BridgeRouteError(
            f"Bridge request timed out after {timeout}s",
            code="request_timed_out",
            request_id=effective_request_id,
            bridge_id=bridge_id,
        ) from exc
    finally:
        _pending_requests.pop(effective_request_id, None)
        await response_subscription.close()


async def get_bridge_status(
    bridge_id: str,
    *,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Get durable bridge connection status, enriched with local socket state."""
    session = await get_bridge_state_repository().get_session(
        bridge_id=bridge_id,
        tenant_id=tenant_id,
    )
    local_connected = bridge_id in _active_bridges
    if session:
        return session.to_status(local_connected=local_connected)
    return {
        "bridge_id": bridge_id,
        "connected": False,
        "local_connected": local_connected,
        "status": "unknown",
    }


async def list_active_bridges(*, tenant_id: str | None = None) -> list[dict[str, Any]]:
    """List durable bridge sessions. Without tenant_id, list local sockets only."""
    if tenant_id is None:
        return [conn.status for conn in _active_bridges.values()]
    sessions = await get_bridge_state_repository().list_sessions(tenant_id=tenant_id)
    return [
        session.to_status(local_connected=session.bridge_id in _active_bridges)
        for session in sessions
    ]
