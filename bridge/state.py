"""Durable bridge session/request state and cross-pod routing broker."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select, text

from core.config import redis_socket_timeout_kwargs, settings

logger = structlog.get_logger()

BridgeRequestHandler = Callable[[dict[str, Any]], Awaitable[None]]
BridgeResponseHandler = Callable[[dict[str, Any]], Awaitable[None]]

ACTIVE_REQUEST_STATUSES = {"pending", "sent"}
TERMINAL_REQUEST_STATUSES = {
    "responded",
    "timed_out",
    "failed",
    "cancelled",
    "orphaned",
}
BRIDGE_HEARTBEAT_STALE_SECONDS = 120


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _now() -> datetime:
    return datetime.now(UTC)


def payload_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def connection_owner() -> str:
    pod = os.getenv("K_REVISION") or os.getenv("HOSTNAME") or "local"
    return f"{pod}:{os.getpid()}"


@dataclass(slots=True)
class BridgeSessionRecord:
    bridge_id: str
    tenant_id: str
    connector_type: str = "tally"
    status: str = "connected"
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    last_heartbeat: datetime | None = None
    tally_healthy: bool = False
    connection_owner: str | None = None
    process_id: int | None = None
    reconnect_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def stale(self) -> bool:
        if not self.last_heartbeat:
            return False
        return (_now() - self.last_heartbeat) > timedelta(seconds=BRIDGE_HEARTBEAT_STALE_SECONDS)

    @property
    def connected(self) -> bool:
        return self.status in {"active", "connected"} and not self.stale

    def to_status(self, *, local_connected: bool = False) -> dict[str, Any]:
        status = "unhealthy" if self.status in {"active", "connected"} and self.stale else self.status
        return {
            "bridge_id": self.bridge_id,
            "tenant_id": self.tenant_id,
            "connector_type": self.connector_type,
            "status": status,
            "connected": self.connected,
            "local_connected": local_connected,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "disconnected_at": (
                self.disconnected_at.isoformat() if self.disconnected_at else None
            ),
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "tally_healthy": self.tally_healthy,
            "connection_owner": self.connection_owner,
            "reconnect_count": self.reconnect_count,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(slots=True)
class BridgeRequestRecord:
    request_id: str
    bridge_id: str
    tenant_id: str
    connector_type: str
    method: str
    payload_hash: str
    status: str = "pending"
    idempotency_key: str | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None
    responded_at: datetime | None = None
    expires_at: datetime | None = None
    id: str | None = None
    updated_at: datetime | None = None


class BridgeRouteError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        request_id: str | None = None,
        bridge_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.bridge_id = bridge_id

    def to_detail(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "error_category": self.code,
            "request_id": self.request_id,
            "bridge_id": self.bridge_id,
        }


class BridgeStateRepository(Protocol):
    async def connect_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        tally_healthy: bool,
        owner: str,
        process_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> BridgeSessionRecord:
        ...

    async def heartbeat(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        tally_healthy: bool,
    ) -> BridgeSessionRecord:
        ...

    async def disconnect_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        reason: str,
    ) -> BridgeSessionRecord | None:
        ...

    async def get_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str | None = None,
    ) -> BridgeSessionRecord | None:
        ...

    async def list_sessions(self, *, tenant_id: str) -> list[BridgeSessionRecord]:
        ...

    async def create_request(
        self,
        *,
        request_id: str,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        method: str,
        payload_hash_value: str,
        timeout_seconds: float,
        idempotency_key: str | None = None,
    ) -> BridgeRequestRecord:
        ...

    async def get_request(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        ...

    async def mark_sent(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        ...

    async def mark_responded(
        self,
        *,
        request_id: str,
        tenant_id: str | None = None,
        result: dict[str, Any],
        response_metadata: dict[str, Any] | None = None,
    ) -> tuple[BridgeRequestRecord | None, bool]:
        ...

    async def mark_failed(
        self,
        *,
        request_id: str,
        tenant_id: str | None = None,
        code: str,
        message: str,
        response_metadata: dict[str, Any] | None = None,
    ) -> tuple[BridgeRequestRecord | None, bool]:
        ...

    async def mark_timed_out(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        ...

    async def mark_bridge_requests_orphaned(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        reason: str,
    ) -> list[BridgeRequestRecord]:
        ...


class InMemoryBridgeStateRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, BridgeSessionRecord] = {}
        self.requests: dict[str, BridgeRequestRecord] = {}

    async def connect_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        tally_healthy: bool,
        owner: str,
        process_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> BridgeSessionRecord:
        now = _now()
        existing = self.sessions.get(bridge_id)
        reconnect_count = (existing.reconnect_count + 1) if existing else 0
        created_at = existing.created_at if existing else now
        record = BridgeSessionRecord(
            id=existing.id if existing else str(uuid.uuid4()),
            bridge_id=bridge_id,
            tenant_id=tenant_id,
            connector_type=connector_type,
            status="connected",
            connected_at=now,
            disconnected_at=None,
            last_heartbeat=now,
            tally_healthy=tally_healthy,
            connection_owner=owner,
            process_id=process_id,
            reconnect_count=reconnect_count,
            metadata=copy.deepcopy(_json_safe(metadata or (existing.metadata if existing else {}))),
            created_at=created_at,
            updated_at=now,
        )
        self.sessions[bridge_id] = record
        return copy.deepcopy(record)

    async def heartbeat(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        tally_healthy: bool,
    ) -> BridgeSessionRecord:
        record = self.sessions[bridge_id]
        if record.tenant_id != tenant_id:
            raise BridgeRouteError(
                "Tenant mismatch for bridge heartbeat",
                code="tenant_mismatch",
                bridge_id=bridge_id,
            )
        record.status = "connected"
        record.last_heartbeat = _now()
        record.tally_healthy = tally_healthy
        record.updated_at = record.last_heartbeat
        return copy.deepcopy(record)

    async def disconnect_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        reason: str,
    ) -> BridgeSessionRecord | None:
        record = self.sessions.get(bridge_id)
        if not record or record.tenant_id != tenant_id:
            return None
        now = _now()
        record.status = "disconnected"
        record.disconnected_at = now
        record.updated_at = now
        record.metadata = {**copy.deepcopy(record.metadata), "disconnect_reason": reason}
        return copy.deepcopy(record)

    async def get_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str | None = None,
    ) -> BridgeSessionRecord | None:
        record = self.sessions.get(bridge_id)
        if record is None:
            return None
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None
        return copy.deepcopy(record)

    async def list_sessions(self, *, tenant_id: str) -> list[BridgeSessionRecord]:
        return [
            copy.deepcopy(record)
            for record in self.sessions.values()
            if record.tenant_id == tenant_id
        ]

    async def create_request(
        self,
        *,
        request_id: str,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        method: str,
        payload_hash_value: str,
        timeout_seconds: float,
        idempotency_key: str | None = None,
    ) -> BridgeRequestRecord:
        if idempotency_key:
            for existing in self.requests.values():
                if (
                    existing.tenant_id == tenant_id
                    and existing.bridge_id == bridge_id
                    and existing.idempotency_key == idempotency_key
                ):
                    return copy.deepcopy(existing)
        now = _now()
        record = BridgeRequestRecord(
            id=str(uuid.uuid4()),
            request_id=request_id,
            bridge_id=bridge_id,
            tenant_id=tenant_id,
            connector_type=connector_type,
            method=method,
            payload_hash=payload_hash_value,
            status="pending",
            idempotency_key=idempotency_key,
            created_at=now,
            expires_at=now + timedelta(seconds=timeout_seconds),
            updated_at=now,
        )
        self.requests[request_id] = record
        return copy.deepcopy(record)

    async def get_request(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        record = self.requests.get(request_id)
        if record is not None and tenant_id is not None and record.tenant_id != tenant_id:
            return None
        return copy.deepcopy(record) if record else None

    async def mark_sent(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        record = self.requests.get(request_id)
        if not record:
            return None
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None
        if record.status == "pending":
            now = _now()
            record.status = "sent"
            record.sent_at = now
            record.updated_at = now
        return copy.deepcopy(record)

    async def mark_responded(
        self,
        *,
        request_id: str,
        tenant_id: str | None = None,
        result: dict[str, Any],
        response_metadata: dict[str, Any] | None = None,
    ) -> tuple[BridgeRequestRecord | None, bool]:
        record = self.requests.get(request_id)
        if not record:
            return None, False
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None, False
        if record.status in TERMINAL_REQUEST_STATUSES:
            self._record_duplicate(record, response_metadata)
            return copy.deepcopy(record), False
        now = _now()
        record.status = "responded"
        record.responded_at = now
        record.updated_at = now
        record.result = copy.deepcopy(_json_safe(result))
        record.response_metadata = copy.deepcopy(_json_safe(response_metadata or {}))
        return copy.deepcopy(record), True

    async def mark_failed(
        self,
        *,
        request_id: str,
        tenant_id: str | None = None,
        code: str,
        message: str,
        response_metadata: dict[str, Any] | None = None,
    ) -> tuple[BridgeRequestRecord | None, bool]:
        record = self.requests.get(request_id)
        if not record:
            return None, False
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None, False
        if record.status in TERMINAL_REQUEST_STATUSES:
            self._record_duplicate(record, response_metadata)
            return copy.deepcopy(record), False
        now = _now()
        record.status = "failed"
        record.responded_at = now
        record.updated_at = now
        record.error = {"code": code, "message": message}
        record.response_metadata = copy.deepcopy(_json_safe(response_metadata or {}))
        return copy.deepcopy(record), True

    async def mark_timed_out(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        record = self.requests.get(request_id)
        if not record:
            return None
        if tenant_id is not None and record.tenant_id != tenant_id:
            return None
        if record.status in ACTIVE_REQUEST_STATUSES:
            now = _now()
            record.status = "timed_out"
            record.updated_at = now
            record.error = {"code": "request_timed_out", "message": "Bridge request timed out"}
        return copy.deepcopy(record)

    async def mark_bridge_requests_orphaned(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        reason: str,
    ) -> list[BridgeRequestRecord]:
        updated: list[BridgeRequestRecord] = []
        now = _now()
        for record in self.requests.values():
            if (
                record.bridge_id != bridge_id
                or record.tenant_id != tenant_id
                or record.status not in ACTIVE_REQUEST_STATUSES
            ):
                continue
            record.status = "orphaned"
            record.updated_at = now
            record.error = {"code": "bridge_disconnected", "message": reason}
            updated.append(copy.deepcopy(record))
        return updated

    @staticmethod
    def _record_duplicate(
        record: BridgeRequestRecord,
        response_metadata: dict[str, Any] | None,
    ) -> None:
        metadata = copy.deepcopy(record.response_metadata or {})
        metadata["duplicate_response_count"] = int(metadata.get("duplicate_response_count") or 0) + 1
        metadata["last_duplicate_at"] = _now().isoformat()
        if response_metadata:
            metadata["last_duplicate_metadata"] = copy.deepcopy(_json_safe(response_metadata))
        record.response_metadata = metadata
        record.updated_at = _now()


class SqlAlchemyBridgeStateRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def connect_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        tally_healthy: bool,
        owner: str,
        process_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> BridgeSessionRecord:
        from core.models.bridge import BridgeSession

        now = _now()
        async with self._session_factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                row = (
                    await session.execute(
                        select(BridgeSession)
                        .where(BridgeSession.bridge_id == bridge_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = BridgeSession(
                        bridge_id=bridge_id,
                        tenant_id=uuid.UUID(tenant_id),
                        connector_type=connector_type,
                        status="connected",
                        connected_at=now,
                        disconnected_at=None,
                        last_heartbeat=now,
                        tally_healthy=tally_healthy,
                        connection_owner=owner,
                        process_id=process_id,
                        reconnect_count=0,
                        session_metadata=_json_safe(metadata or {}),
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.connector_type = connector_type
                    row.status = "connected"
                    row.connected_at = now
                    row.disconnected_at = None
                    row.last_heartbeat = now
                    row.tally_healthy = tally_healthy
                    row.connection_owner = owner
                    row.process_id = process_id
                    row.reconnect_count = int(row.reconnect_count or 0) + 1
                    if metadata:
                        row.session_metadata = _json_safe(metadata)
                    row.updated_at = now
                return self._session_from_model(row)

    async def heartbeat(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        tally_healthy: bool,
    ) -> BridgeSessionRecord:
        from core.models.bridge import BridgeSession

        async with self._session_factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                row = (
                    await session.execute(
                        select(BridgeSession)
                        .where(
                            BridgeSession.bridge_id == bridge_id,
                            BridgeSession.tenant_id == uuid.UUID(tenant_id),
                        )
                        .with_for_update()
                    )
                ).scalar_one()
                now = _now()
                row.status = "connected"
                row.last_heartbeat = now
                row.tally_healthy = tally_healthy
                row.updated_at = now
                return self._session_from_model(row)

    async def disconnect_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        reason: str,
    ) -> BridgeSessionRecord | None:
        from core.models.bridge import BridgeSession

        async with self._session_factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                row = (
                    await session.execute(
                        select(BridgeSession)
                        .where(
                            BridgeSession.bridge_id == bridge_id,
                            BridgeSession.tenant_id == uuid.UUID(tenant_id),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                now = _now()
                row.status = "disconnected"
                row.disconnected_at = now
                row.updated_at = now
                row.session_metadata = {
                    **(row.session_metadata or {}),
                    "disconnect_reason": reason,
                }
                return self._session_from_model(row)

    async def get_session(
        self,
        *,
        bridge_id: str,
        tenant_id: str | None = None,
    ) -> BridgeSessionRecord | None:
        from core.models.bridge import BridgeSession

        async with self._session_factory() as session:
            if tenant_id:
                await _set_tenant(session, tenant_id)
            query = select(BridgeSession).where(BridgeSession.bridge_id == bridge_id)
            if tenant_id:
                query = query.where(BridgeSession.tenant_id == uuid.UUID(tenant_id))
            row = (await session.execute(query)).scalar_one_or_none()
            return self._session_from_model(row) if row else None

    async def list_sessions(self, *, tenant_id: str) -> list[BridgeSessionRecord]:
        from core.models.bridge import BridgeSession

        async with self._session_factory() as session:
            await _set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    select(BridgeSession)
                    .where(BridgeSession.tenant_id == uuid.UUID(tenant_id))
                    .order_by(BridgeSession.created_at.desc())
                )
            ).scalars().all()
            return [self._session_from_model(row) for row in rows]

    async def create_request(
        self,
        *,
        request_id: str,
        bridge_id: str,
        tenant_id: str,
        connector_type: str,
        method: str,
        payload_hash_value: str,
        timeout_seconds: float,
        idempotency_key: str | None = None,
    ) -> BridgeRequestRecord:
        from core.models.bridge import BridgeRequest

        async with self._session_factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                if idempotency_key:
                    existing = (
                        await session.execute(
                            select(BridgeRequest).where(
                                BridgeRequest.tenant_id == uuid.UUID(tenant_id),
                                BridgeRequest.bridge_id == bridge_id,
                                BridgeRequest.idempotency_key == idempotency_key,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        return self._request_from_model(existing)
                now = _now()
                row = BridgeRequest(
                    request_id=request_id,
                    bridge_id=bridge_id,
                    tenant_id=uuid.UUID(tenant_id),
                    connector_type=connector_type,
                    method=method,
                    payload_hash=payload_hash_value,
                    status="pending",
                    idempotency_key=idempotency_key,
                    expires_at=now + timedelta(seconds=timeout_seconds),
                )
                session.add(row)
                await session.flush()
                return self._request_from_model(row)

    async def get_request(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        from core.models.bridge import BridgeRequest

        async with self._session_factory() as session:
            if tenant_id:
                await _set_tenant(session, tenant_id)
            query = select(BridgeRequest).where(BridgeRequest.request_id == request_id)
            if tenant_id:
                query = query.where(BridgeRequest.tenant_id == uuid.UUID(tenant_id))
            row = (await session.execute(query)).scalar_one_or_none()
            return self._request_from_model(row) if row else None

    async def mark_sent(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_request(session, request_id, tenant_id=tenant_id)
                if row is None:
                    return None
                if row.status == "pending":
                    now = _now()
                    row.status = "sent"
                    row.sent_at = now
                    row.updated_at = now
                return self._request_from_model(row)

    async def mark_responded(
        self,
        *,
        request_id: str,
        tenant_id: str | None = None,
        result: dict[str, Any],
        response_metadata: dict[str, Any] | None = None,
    ) -> tuple[BridgeRequestRecord | None, bool]:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_request(session, request_id, tenant_id=tenant_id)
                if row is None:
                    return None, False
                if row.status in TERMINAL_REQUEST_STATUSES:
                    self._record_duplicate(row, response_metadata)
                    return self._request_from_model(row), False
                now = _now()
                row.status = "responded"
                row.responded_at = now
                row.updated_at = now
                row.result = _json_safe(result)
                row.response_metadata = _json_safe(response_metadata or {})
                return self._request_from_model(row), True

    async def mark_failed(
        self,
        *,
        request_id: str,
        tenant_id: str | None = None,
        code: str,
        message: str,
        response_metadata: dict[str, Any] | None = None,
    ) -> tuple[BridgeRequestRecord | None, bool]:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_request(session, request_id, tenant_id=tenant_id)
                if row is None:
                    return None, False
                if row.status in TERMINAL_REQUEST_STATUSES:
                    self._record_duplicate(row, response_metadata)
                    return self._request_from_model(row), False
                now = _now()
                row.status = "failed"
                row.responded_at = now
                row.updated_at = now
                row.error = {"code": code, "message": message}
                row.response_metadata = _json_safe(response_metadata or {})
                return self._request_from_model(row), True

    async def mark_timed_out(
        self,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> BridgeRequestRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._locked_request(session, request_id, tenant_id=tenant_id)
                if row is None:
                    return None
                if row.status in ACTIVE_REQUEST_STATUSES:
                    now = _now()
                    row.status = "timed_out"
                    row.updated_at = now
                    row.error = {
                        "code": "request_timed_out",
                        "message": "Bridge request timed out",
                    }
                return self._request_from_model(row)

    async def mark_bridge_requests_orphaned(
        self,
        *,
        bridge_id: str,
        tenant_id: str,
        reason: str,
    ) -> list[BridgeRequestRecord]:
        from core.models.bridge import BridgeRequest

        async with self._session_factory() as session:
            async with session.begin():
                await _set_tenant(session, tenant_id)
                rows = (
                    await session.execute(
                        select(BridgeRequest)
                        .where(
                            BridgeRequest.bridge_id == bridge_id,
                            BridgeRequest.tenant_id == uuid.UUID(tenant_id),
                            BridgeRequest.status.in_(list(ACTIVE_REQUEST_STATUSES)),
                        )
                        .with_for_update()
                    )
                ).scalars().all()
                now = _now()
                for row in rows:
                    row.status = "orphaned"
                    row.updated_at = now
                    row.error = {"code": "bridge_disconnected", "message": reason}
                return [self._request_from_model(row) for row in rows]

    async def _locked_request(
        self,
        session: Any,
        request_id: str,
        *,
        tenant_id: str | None = None,
    ) -> Any | None:
        from core.models.bridge import BridgeRequest

        if tenant_id:
            await _set_tenant(session, tenant_id)
        query = select(BridgeRequest).where(BridgeRequest.request_id == request_id)
        if tenant_id:
            query = query.where(BridgeRequest.tenant_id == uuid.UUID(tenant_id))
        return (
            await session.execute(
                query.with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    def _record_duplicate(row: Any, response_metadata: dict[str, Any] | None) -> None:
        metadata = dict(row.response_metadata or {})
        metadata["duplicate_response_count"] = int(metadata.get("duplicate_response_count") or 0) + 1
        metadata["last_duplicate_at"] = _now().isoformat()
        if response_metadata:
            metadata["last_duplicate_metadata"] = _json_safe(response_metadata)
        row.response_metadata = metadata
        row.updated_at = _now()

    @staticmethod
    def _session_from_model(row: Any) -> BridgeSessionRecord:
        return BridgeSessionRecord(
            id=str(row.id),
            bridge_id=row.bridge_id,
            tenant_id=str(row.tenant_id),
            connector_type=row.connector_type,
            status=row.status,
            connected_at=row.connected_at,
            disconnected_at=row.disconnected_at,
            last_heartbeat=row.last_heartbeat,
            tally_healthy=bool(row.tally_healthy),
            connection_owner=row.connection_owner,
            process_id=row.process_id,
            reconnect_count=int(row.reconnect_count or 0),
            metadata=copy.deepcopy(row.session_metadata or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _request_from_model(row: Any) -> BridgeRequestRecord:
        return BridgeRequestRecord(
            id=str(row.id),
            request_id=row.request_id,
            bridge_id=row.bridge_id,
            tenant_id=str(row.tenant_id),
            connector_type=row.connector_type,
            method=row.method,
            payload_hash=row.payload_hash,
            status=row.status,
            idempotency_key=row.idempotency_key,
            response_metadata=copy.deepcopy(row.response_metadata or {}),
            result=copy.deepcopy(row.result),
            error=copy.deepcopy(row.error),
            created_at=row.created_at,
            sent_at=row.sent_at,
            responded_at=row.responded_at,
            expires_at=row.expires_at,
            updated_at=row.updated_at,
        )


class BrokerSubscription(Protocol):
    async def close(self) -> None:
        ...


class BridgeBroker(Protocol):
    async def publish_request(self, bridge_id: str, message: dict[str, Any]) -> None:
        ...

    async def subscribe_requests(
        self,
        bridge_id: str,
        handler: BridgeRequestHandler,
    ) -> BrokerSubscription:
        ...

    async def publish_response(self, request_id: str, message: dict[str, Any]) -> None:
        ...

    async def subscribe_response(
        self,
        request_id: str,
        handler: BridgeResponseHandler,
    ) -> BrokerSubscription:
        ...


class _InMemorySubscription:
    def __init__(self, handlers: set[Any], handler: Any) -> None:
        self._handlers = handlers
        self._handler = handler

    async def close(self) -> None:
        self._handlers.discard(self._handler)


class InMemoryBridgeBroker:
    def __init__(self) -> None:
        self.request_handlers: dict[str, set[BridgeRequestHandler]] = {}
        self.response_handlers: dict[str, set[BridgeResponseHandler]] = {}
        self.published_requests: list[dict[str, Any]] = []
        self.published_responses: list[dict[str, Any]] = []

    async def publish_request(self, bridge_id: str, message: dict[str, Any]) -> None:
        safe_message = copy.deepcopy(_json_safe(message))
        self.published_requests.append(safe_message)
        for handler in list(self.request_handlers.get(bridge_id, set())):
            await handler(copy.deepcopy(safe_message))

    async def subscribe_requests(
        self,
        bridge_id: str,
        handler: BridgeRequestHandler,
    ) -> BrokerSubscription:
        handlers = self.request_handlers.setdefault(bridge_id, set())
        handlers.add(handler)
        return _InMemorySubscription(handlers, handler)

    async def publish_response(self, request_id: str, message: dict[str, Any]) -> None:
        safe_message = copy.deepcopy(_json_safe(message))
        self.published_responses.append(safe_message)
        for handler in list(self.response_handlers.get(request_id, set())):
            await handler(copy.deepcopy(safe_message))

    async def subscribe_response(
        self,
        request_id: str,
        handler: BridgeResponseHandler,
    ) -> BrokerSubscription:
        handlers = self.response_handlers.setdefault(request_id, set())
        handlers.add(handler)
        return _InMemorySubscription(handlers, handler)


class _RedisSubscription:
    def __init__(
        self,
        redis_url: str,
        channel: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._redis_url = redis_url
        self._channel = channel
        self._handler = handler
        self._redis: aioredis.Redis | None = None
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            **redis_socket_timeout_kwargs(),
        )
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message.get("data") or "{}")
                except json.JSONDecodeError:
                    logger.warning("bridge_broker_invalid_json", channel=self._channel)
                    continue
                await self._handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - broker subscription should not crash process.
            logger.warning("bridge_broker_subscription_failed", channel=self._channel, error=str(exc))

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            with suppress(Exception):
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.close()
        if self._redis is not None:
            await _close_redis(self._redis)


class RedisBridgeBroker:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: aioredis.Redis | None = None

    async def publish_request(self, bridge_id: str, message: dict[str, Any]) -> None:
        await self._publish(_request_channel(bridge_id), message)

    async def subscribe_requests(
        self,
        bridge_id: str,
        handler: BridgeRequestHandler,
    ) -> BrokerSubscription:
        subscription = _RedisSubscription(self._redis_url, _request_channel(bridge_id), handler)
        await subscription.start()
        return subscription

    async def publish_response(self, request_id: str, message: dict[str, Any]) -> None:
        await self._publish(_response_channel(request_id), message)

    async def subscribe_response(
        self,
        request_id: str,
        handler: BridgeResponseHandler,
    ) -> BrokerSubscription:
        subscription = _RedisSubscription(self._redis_url, _response_channel(request_id), handler)
        await subscription.start()
        return subscription

    async def _publish(self, channel: str, message: dict[str, Any]) -> None:
        redis = await self._publisher()
        await redis.publish(channel, json.dumps(_json_safe(message)))

    async def _publisher(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                **redis_socket_timeout_kwargs(),
            )
        return self._redis


async def _close_redis(redis: aioredis.Redis) -> None:
    close_fn = getattr(redis, "aclose", None) or getattr(redis, "close", None)
    if close_fn is None:
        return
    result = close_fn()
    if inspect.isawaitable(result):
        await result


async def _set_tenant(session: Any, tenant_id: str) -> None:
    await session.execute(
        text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(uuid.UUID(str(tenant_id)))},
    )


def _request_channel(bridge_id: str) -> str:
    return f"bridge:req:{bridge_id}"


def _response_channel(request_id: str) -> str:
    return f"bridge:resp:{request_id}"


_repository_override: BridgeStateRepository | None = None
_broker_override: BridgeBroker | None = None
_default_repository: BridgeStateRepository | None = None
_default_broker: BridgeBroker | None = None


def get_bridge_state_repository() -> BridgeStateRepository:
    global _default_repository
    if _repository_override is not None:
        return _repository_override
    if _default_repository is None:
        from core.database import async_session_factory

        _default_repository = SqlAlchemyBridgeStateRepository(async_session_factory)
    return _default_repository


def get_bridge_broker() -> BridgeBroker:
    global _default_broker
    if _broker_override is not None:
        return _broker_override
    if _default_broker is None:
        _default_broker = RedisBridgeBroker()
    return _default_broker


def configure_bridge_state_for_tests(
    *,
    repository: BridgeStateRepository | None = None,
    broker: BridgeBroker | None = None,
) -> None:
    global _broker_override, _repository_override
    _repository_override = repository
    _broker_override = broker


def reset_bridge_state_for_tests() -> None:
    configure_bridge_state_for_tests(repository=None, broker=None)
