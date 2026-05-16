"""Authenticated, durable live feed WebSocket."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import bcrypt
import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from api.deps import get_current_tenant
from auth.grantex_middleware import _is_grantex_token
from auth.jwt import extract_scopes, extract_tenant_id, validate_token
from core.database import async_session_factory
from core.live_feed import (
    BrokerSubscription,
    get_feed_event_broker,
    get_feed_event_repository,
)
from core.models.api_key import APIKey

router = APIRouter()
logger = structlog.get_logger()

_connections: dict[str, set[WebSocket]] = {}
_subscriptions: dict[str, BrokerSubscription] = {}
_connections_lock = asyncio.Lock()


class WebSocketAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _extract_token(websocket: WebSocket) -> str:
    cookie_token = websocket.cookies.get("agenticorg_session") or ""
    if cookie_token:
        return cookie_token

    authorization = websocket.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:]

    return (
        websocket.query_params.get("token")
        or websocket.query_params.get("access_token")
        or websocket.query_params.get("ws_ticket")
        or ""
    )


async def _claims_from_api_key(token: str) -> dict[str, Any]:
    prefix = f"ao_sk_{token[6:12]}"
    async with async_session_factory() as session:
        result = await session.execute(
            select(APIKey).where(
                APIKey.prefix == prefix,
                APIKey.status == "active",
            )
        )
        candidates = result.scalars().all()

    matched_key = None
    for candidate in candidates:
        if bcrypt.checkpw(token.encode(), candidate.key_hash.encode()):
            matched_key = candidate
            break

    if matched_key is None:
        raise WebSocketAuthError("invalid_api_key", "Invalid API key")
    if matched_key.expires_at and matched_key.expires_at < datetime.now(UTC):
        raise WebSocketAuthError("expired_api_key", "API key expired")

    async with async_session_factory() as session:
        await session.execute(
            update(APIKey)
            .where(APIKey.id == matched_key.id)
            .values(last_used_at=datetime.now(UTC))
        )
        await session.commit()

    return {
        "sub": f"apikey:{matched_key.prefix}",
        "agenticorg:tenant_id": str(matched_key.tenant_id),
        "grantex:scopes": matched_key.scopes or [],
    }


async def _claims_from_grantex_token(token: str) -> dict[str, Any]:
    try:
        import os

        from grantex._verify import VerifyGrantTokenOptions, verify_grant_token

        grantex_url = os.getenv("GRANTEX_BASE_URL", "https://api.grantex.dev")
        verified = verify_grant_token(
            token,
            VerifyGrantTokenOptions(jwks_uri=f"{grantex_url}/.well-known/jwks.json"),
        )
    except Exception as exc:  # noqa: BLE001 - auth failure maps to policy violation.
        raise WebSocketAuthError("invalid_grant_token", "Invalid or expired grant token") from exc

    return {
        "sub": getattr(verified, "principal_id", ""),
        "agenticorg:tenant_id": getattr(verified, "developer_id", ""),
        "grantex:scopes": getattr(verified, "scopes", []),
        "agenticorg:agent_id": getattr(verified, "agent_did", ""),
        "grantex:grant_id": getattr(verified, "grant_id", ""),
        "grantex:delegation_depth": getattr(verified, "delegation_depth", 0),
    }


async def authenticate_websocket(websocket: WebSocket, path_tenant_id: str) -> dict[str, Any]:
    token = _extract_token(websocket)
    if not token:
        raise WebSocketAuthError("missing_auth", "Missing session cookie or bearer token")

    if token.startswith("ao_sk_"):
        claims = await _claims_from_api_key(token)
    elif _is_grantex_token(token):
        claims = await _claims_from_grantex_token(token)
    else:
        try:
            claims = await validate_token(token)
        except ValueError as exc:
            raise WebSocketAuthError("invalid_token", "Invalid or expired token") from exc

    tenant_id = extract_tenant_id(claims)
    if not tenant_id:
        raise WebSocketAuthError("missing_tenant", "Token is missing tenant claim")
    if str(tenant_id) != str(path_tenant_id):
        raise WebSocketAuthError("tenant_mismatch", "Tenant mismatch")

    return {
        "claims": claims,
        "tenant_id": tenant_id,
        "scopes": extract_scopes(claims),
    }


async def _fanout_local(message: dict[str, Any]) -> int:
    tenant_id = str(message.get("tenant_id") or "")
    async with _connections_lock:
        sockets = list(_connections.get(tenant_id, set()))

    failed: list[WebSocket] = []
    sent = 0
    for socket in sockets:
        try:
            await socket.send_json(message)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - stale sockets are removed below.
            logger.debug("live_feed_socket_send_failed", tenant_id=tenant_id, error=str(exc))
            failed.append(socket)

    if failed:
        async with _connections_lock:
            bucket = _connections.get(tenant_id)
            if bucket is not None:
                for socket in failed:
                    bucket.discard(socket)
    return sent


async def _ensure_subscription_locked(tenant_id: str) -> None:
    if tenant_id in _subscriptions:
        return
    _subscriptions[tenant_id] = await get_feed_event_broker().subscribe(tenant_id, _fanout_local)


async def _add_connection(tenant_id: str, websocket: WebSocket) -> None:
    async with _connections_lock:
        _connections.setdefault(tenant_id, set()).add(websocket)
        await _ensure_subscription_locked(tenant_id)


async def _remove_connection(tenant_id: str, websocket: WebSocket) -> None:
    subscription: BrokerSubscription | None = None
    async with _connections_lock:
        bucket = _connections.get(tenant_id)
        if bucket is not None:
            bucket.discard(websocket)
            if not bucket:
                _connections.pop(tenant_id, None)
                subscription = _subscriptions.pop(tenant_id, None)
    if subscription is not None:
        await subscription.close()


async def broadcast_to_tenant(tenant_id: str, data: dict) -> int:
    """Persist and broker a tenant feed event.

    The return value is the number of currently connected local sockets; cross-pod
    delivery is asynchronous through the broker.
    """

    event_type = str(data.get("type") or data.get("event_type") or "message")
    event = await get_feed_event_repository().append(
        tenant_id=str(tenant_id),
        event_type=event_type,
        payload=data,
        source=data.get("source") if isinstance(data.get("source"), str) else None,
        correlation_id=(
            data.get("correlation_id") if isinstance(data.get("correlation_id"), str) else None
        ),
    )
    message = event.to_message()
    try:
        await get_feed_event_broker().publish(event)
    except Exception as exc:  # noqa: BLE001 - persisted feed event remains replayable.
        logger.warning("live_feed_broker_publish_failed", tenant_id=tenant_id, error=str(exc))
        await _fanout_local(message)

    async with _connections_lock:
        return len(_connections.get(str(tenant_id), set()))


@router.get("/feed/events")
async def list_feed_events(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    events = await get_feed_event_repository().list_after(
        tenant_id=tenant_id,
        after=after,
        limit=limit,
    )
    return {
        "tenant_id": tenant_id,
        "after": after,
        "items": [event.to_message() for event in events],
    }


@router.websocket("/ws/feed/{tenant_id}")
async def live_feed(websocket: WebSocket, tenant_id: str) -> None:
    try:
        await authenticate_websocket(websocket, tenant_id)
    except WebSocketAuthError as exc:
        logger.warning("live_feed_auth_rejected", tenant_id=tenant_id, code=exc.code)
        await websocket.close(code=1008, reason=exc.message[:123])
        return

    await websocket.accept()
    try:
        await _add_connection(tenant_id, websocket)
        await websocket.send_json({"type": "heartbeat", "tenant_id": tenant_id, "sequence": None})
        while True:
            try:
                raw_message = await asyncio.wait_for(websocket.receive_text(), timeout=15)
            except TimeoutError:
                await websocket.send_json(
                    {"type": "heartbeat", "tenant_id": tenant_id, "sequence": None}
                )
                continue
            except WebSocketDisconnect:
                break

            try:
                inbound = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_json",
                        "tenant_id": tenant_id,
                        "sequence": None,
                    }
                )
                continue
            if isinstance(inbound, dict) and inbound.get("type") == "ping":
                await websocket.send_json({"type": "pong", "tenant_id": tenant_id, "sequence": None})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - connection-level failure should not crash the app.
        logger.warning("live_feed_connection_failed", tenant_id=tenant_id, error=str(exc))
        with suppress(RuntimeError):
            await websocket.close(code=1011, reason="Feed connection failed")
    finally:
        await _remove_connection(tenant_id, websocket)
