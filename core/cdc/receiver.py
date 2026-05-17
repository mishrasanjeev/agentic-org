"""Durable CDC webhook receiver.

The production source of truth is PostgreSQL. Redis/process memory must never
own CDC event history or deduplication; the in-memory store below exists only
as an explicit test seam.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import is_relaxed_env, settings

logger = structlog.get_logger()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _coerce_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


def _public_event(record: dict[str, Any]) -> dict[str, Any]:
    safe = _json_safe(record)
    safe["event_id"] = str(safe.get("id") or safe.get("event_id") or "")
    safe.setdefault("tenant_id", str(record.get("tenant_id", "")))
    return safe


def _provider_event_id(payload: dict[str, Any]) -> str | None:
    for key in ("provider_event_id", "event_id", "webhook_event_id", "delivery_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _payload_field(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _validate_payload_shape(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return None, {"status": "rejected", "reason": "invalid_payload", "http_status": 422}

    event_type = _payload_field(payload, "event_type", "type")
    resource_type = _payload_field(payload, "resource_type", "object")
    resource_id = _payload_field(payload, "resource_id", "object_id", "id")
    if not event_type or not resource_type or not resource_id:
        return None, {
            "status": "rejected",
            "reason": "invalid_payload",
            "message": "event_type, resource_type, and resource_id are required",
            "http_status": 422,
        }
    return {
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }, None


def _compute_fingerprint(
    *,
    connector: str,
    event_type: str,
    resource_type: str,
    resource_id: str,
    payload_hash: str,
) -> str:
    raw = json.dumps(
        {
            "connector": connector,
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "payload_hash": payload_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_signature(
    payload_bytes: bytes,
    signature: str,
    connector: str,
    *,
    alternate_payload_bytes: bytes | None = None,
) -> bool:
    """Validate HMAC-SHA256 signature using per-connector secret.

    Fails closed when no connector secret is configured.
    """
    secret = os.getenv(f"CDC_WEBHOOK_SECRET_{connector.upper()}", "")
    if not secret:
        logger.warning(
            "cdc_webhook_secret_missing",
            connector=connector,
            hint="Set CDC_WEBHOOK_SECRET_<CONNECTOR> env var",
        )
        return False
    candidates = [payload_bytes]
    if alternate_payload_bytes is not None and alternate_payload_bytes != payload_bytes:
        candidates.append(alternate_payload_bytes)
    for candidate in candidates:
        expected = hmac.new(secret.encode(), candidate, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature or ""):
            return True
    return False


class CDCEventStore(Protocol):
    async def insert_event(self, event: dict[str, Any]) -> dict[str, Any]:
        ...

    async def mark_processed(
        self,
        event_id: str,
        *,
        outcome: dict[str, Any],
        replay_status: str | None = None,
    ) -> None:
        ...

    async def mark_failed(
        self,
        event_id: str,
        *,
        failure_stage: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
        replay_status: str | None = None,
    ) -> None:
        ...

    async def list_events(
        self,
        *,
        tenant_id: str | None,
        connector: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        ...

    async def claim_replay(self, *, tenant_id: str, event_id: str, actor: str) -> dict[str, Any]:
        ...

    async def clear(self) -> None:
        ...


@dataclass
class InMemoryCDCEventRepository:
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    fingerprint_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    provider_index: dict[tuple[str, str, str], str] = field(default_factory=dict)
    dead_letters: list[dict[str, Any]] = field(default_factory=list)


class InMemoryCDCEventStore:
    """Explicit durable-store test double.

    Separate store instances can share the same repository to prove idempotency
    does not depend on receiver instance state.
    """

    def __init__(self, repository: InMemoryCDCEventRepository | None = None) -> None:
        self.repository = repository or InMemoryCDCEventRepository()

    async def insert_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event = copy.deepcopy(_json_safe(event))
        provider_event_id = event.get("provider_event_id")
        if provider_event_id:
            provider_key = (event["tenant_id"], event["connector"], str(provider_event_id))
            existing_id = self.repository.provider_index.get(provider_key)
            if existing_id:
                return {
                    "inserted": False,
                    "event": copy.deepcopy(self.repository.records[existing_id]),
                }

        fingerprint_key = (event["tenant_id"], event["connector"], event["fingerprint"])
        existing_id = self.repository.fingerprint_index.get(fingerprint_key)
        if existing_id:
            return {
                "inserted": False,
                "event": copy.deepcopy(self.repository.records[existing_id]),
            }

        event.setdefault("id", str(uuid.uuid4()))
        event.setdefault("processing_status", "received")
        event.setdefault("processing_outcome", {})
        event.setdefault("replay_status", "not_replayed")
        event.setdefault("processed", False)
        event.setdefault("replay_attempts", 0)
        now = datetime.now(UTC).isoformat()
        event.setdefault("created_at", now)
        event.setdefault("received_at", now)
        self.repository.records[event["id"]] = event
        self.repository.fingerprint_index[fingerprint_key] = event["id"]
        if provider_event_id:
            self.repository.provider_index[provider_key] = event["id"]
        return {"inserted": True, "event": copy.deepcopy(event)}

    async def mark_processed(
        self,
        event_id: str,
        *,
        outcome: dict[str, Any],
        replay_status: str | None = None,
    ) -> None:
        event = self.repository.records[str(event_id)]
        now = datetime.now(UTC).isoformat()
        event["processed"] = True
        event["processing_status"] = "processed"
        event["processing_outcome"] = copy.deepcopy(_json_safe(outcome))
        event["processed_at"] = now
        event["updated_at"] = now
        event["error_details"] = None
        if replay_status:
            event["replay_status"] = replay_status

    async def mark_failed(
        self,
        event_id: str,
        *,
        failure_stage: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
        replay_status: str | None = None,
    ) -> None:
        event = self.repository.records[str(event_id)]
        now = datetime.now(UTC).isoformat()
        details = copy.deepcopy(_json_safe(error_details or {}))
        event["processed"] = False
        event["processing_status"] = "dead_lettered"
        event["processing_outcome"] = {
            "failure_stage": failure_stage,
            "error_code": error_code,
            "error_message": error_message,
        }
        event["error_details"] = details
        event["updated_at"] = now
        if replay_status:
            event["replay_status"] = replay_status
        self.repository.dead_letters.append(
            {
                "id": str(uuid.uuid4()),
                "cdc_event_id": event_id,
                "tenant_id": event["tenant_id"],
                "connector": event["connector"],
                "event_hash": event["event_hash"],
                "failure_stage": failure_stage,
                "error_code": error_code,
                "error_message": error_message,
                "error_details": details,
                "created_at": now,
            }
        )

    async def list_events(
        self,
        *,
        tenant_id: str | None,
        connector: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        events = list(self.repository.records.values())
        if tenant_id is not None:
            events = [event for event in events if event.get("tenant_id") == tenant_id]
        if connector:
            events = [event for event in events if event.get("connector") == connector]
        if event_type:
            events = [event for event in events if event.get("event_type") == event_type]
        events.sort(key=lambda event: event.get("received_at") or event.get("created_at") or "", reverse=True)
        total = len(events)
        if limit is not None:
            events = events[offset : offset + limit]
        elif offset:
            events = events[offset:]
        return [copy.deepcopy(event) for event in events], total

    async def claim_replay(self, *, tenant_id: str, event_id: str, actor: str) -> dict[str, Any]:
        event = self.repository.records.get(str(event_id))
        if not event or event.get("tenant_id") != tenant_id:
            return {"status": "not_found"}
        if event.get("processing_status") == "processed" or event.get("replay_status") in {
            "replay_pending",
            "replayed",
            "failed",
        }:
            return {"status": "duplicate", "event": copy.deepcopy(event)}
        now = datetime.now(UTC).isoformat()
        event["replay_status"] = "replay_pending"
        event["replay_attempts"] = int(event.get("replay_attempts") or 0) + 1
        event["last_replayed_at"] = now
        event["last_replayed_by"] = actor
        event["updated_at"] = now
        return {"status": "claimed", "event": copy.deepcopy(event)}

    async def clear(self) -> None:
        self.repository.records.clear()
        self.repository.fingerprint_index.clear()
        self.repository.provider_index.clear()
        self.repository.dead_letters.clear()

    def list_events_sync(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        events = list(self.repository.records.values())
        if tenant_id is not None:
            events = [event for event in events if event.get("tenant_id") == tenant_id]
        return [copy.deepcopy(event) for event in events]

    def clear_sync(self) -> None:
        self.repository.records.clear()
        self.repository.fingerprint_index.clear()
        self.repository.provider_index.clear()
        self.repository.dead_letters.clear()


class SqlAlchemyCDCEventStore:
    """PostgreSQL implementation of CDC event ingestion and replay."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def insert_event(self, event: dict[str, Any]) -> dict[str, Any]:
        from core.models.cdc import CDCEvent

        tenant_uuid = _coerce_uuid(event["tenant_id"])
        event_id = uuid.uuid4()
        values = {
            "id": event_id,
            "tenant_id": tenant_uuid,
            "connector": event["connector"],
            "provider_event_id": event.get("provider_event_id"),
            "fingerprint": event["fingerprint"],
            "event_hash": event["event_hash"],
            "event_type": event["event_type"],
            "resource_type": event["resource_type"],
            "resource_id": event["resource_id"],
            "payload": _json_safe(event["payload"]),
            "raw_body_hash": event.get("raw_body_hash"),
            "signature_verification_status": event.get("signature_verification_status", "valid"),
            "processing_status": "received",
            "processing_outcome": {},
            "replay_status": "not_replayed",
            "processed": False,
            "received_at": event.get("received_at") or datetime.now(UTC),
        }
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    pg_insert(CDCEvent).values(**values).on_conflict_do_nothing()
                )
                if result.rowcount:
                    row = (
                        await session.execute(select(CDCEvent).where(CDCEvent.id == event_id))
                    ).scalar_one()
                    return {"inserted": True, "event": _cdc_model_to_dict(row)}

                row = await self._find_duplicate_row(
                    session,
                    tenant_uuid=tenant_uuid,
                    connector=event["connector"],
                    provider_event_id=event.get("provider_event_id"),
                    fingerprint=event["fingerprint"],
                )
                if row is None:
                    raise RuntimeError("cdc duplicate insert conflict was not queryable")
                return {"inserted": False, "event": _cdc_model_to_dict(row)}

    async def _find_duplicate_row(
        self,
        session: Any,
        *,
        tenant_uuid: uuid.UUID,
        connector: str,
        provider_event_id: str | None,
        fingerprint: str,
    ) -> Any:
        from core.models.cdc import CDCEvent

        clauses = [CDCEvent.fingerprint == fingerprint]
        if provider_event_id:
            clauses.append(CDCEvent.provider_event_id == provider_event_id)
        return (
            await session.execute(
                select(CDCEvent).where(
                    CDCEvent.tenant_id == tenant_uuid,
                    CDCEvent.connector == connector,
                    or_(*clauses),
                )
            )
        ).scalar_one_or_none()

    async def mark_processed(
        self,
        event_id: str,
        *,
        outcome: dict[str, Any],
        replay_status: str | None = None,
    ) -> None:
        from core.models.cdc import CDCEvent

        values: dict[str, Any] = {
            "processed": True,
            "processing_status": "processed",
            "processing_outcome": _json_safe(outcome),
            "processed_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "error_details": None,
        }
        if replay_status:
            values["replay_status"] = replay_status
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(CDCEvent).where(CDCEvent.id == uuid.UUID(str(event_id))).values(**values)
                )

    async def mark_failed(
        self,
        event_id: str,
        *,
        failure_stage: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
        replay_status: str | None = None,
    ) -> None:
        from core.models.cdc import CDCEvent, CDCEventDeadLetter

        event_uuid = uuid.UUID(str(event_id))
        now = datetime.now(UTC)
        details = _json_safe(error_details or {})
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(CDCEvent).where(CDCEvent.id == event_uuid).with_for_update()
                    )
                ).scalar_one()
                row.processed = False
                row.processing_status = "dead_lettered"
                row.processing_outcome = {
                    "failure_stage": failure_stage,
                    "error_code": error_code,
                    "error_message": error_message,
                }
                row.error_details = details
                row.updated_at = now
                if replay_status:
                    row.replay_status = replay_status
                session.add(
                    CDCEventDeadLetter(
                        cdc_event_id=row.id,
                        tenant_id=row.tenant_id,
                        connector=row.connector,
                        event_hash=row.event_hash,
                        failure_stage=failure_stage,
                        error_code=error_code,
                        error_message=error_message,
                        error_details=details,
                    )
                )

    async def list_events(
        self,
        *,
        tenant_id: str | None,
        connector: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        from core.models.cdc import CDCEvent

        async with self._session_factory() as session:
            query = select(CDCEvent)
            count_query = select(func.count()).select_from(CDCEvent)
            if tenant_id is not None:
                tenant_uuid = _coerce_uuid(tenant_id)
                query = query.where(CDCEvent.tenant_id == tenant_uuid)
                count_query = count_query.where(CDCEvent.tenant_id == tenant_uuid)
            if connector:
                query = query.where(CDCEvent.connector == connector)
                count_query = count_query.where(CDCEvent.connector == connector)
            if event_type:
                query = query.where(CDCEvent.event_type == event_type)
                count_query = count_query.where(CDCEvent.event_type == event_type)

            total = int((await session.execute(count_query)).scalar_one())
            query = query.order_by(CDCEvent.received_at.desc(), CDCEvent.created_at.desc()).offset(offset)
            if limit is not None:
                query = query.limit(limit)
            rows = (await session.execute(query)).scalars().all()
        return [_cdc_model_to_dict(row) for row in rows], total

    async def claim_replay(self, *, tenant_id: str, event_id: str, actor: str) -> dict[str, Any]:
        from core.models.cdc import CDCEvent

        tenant_uuid = _coerce_uuid(tenant_id)
        event_uuid = uuid.UUID(str(event_id))
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(CDCEvent)
                        .where(CDCEvent.id == event_uuid, CDCEvent.tenant_id == tenant_uuid)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return {"status": "not_found"}
                if row.processing_status == "processed" or row.replay_status in {
                    "replay_pending",
                    "replayed",
                    "failed",
                }:
                    return {"status": "duplicate", "event": _cdc_model_to_dict(row)}
                now = datetime.now(UTC)
                row.replay_status = "replay_pending"
                row.replay_attempts = int(row.replay_attempts or 0) + 1
                row.last_replayed_at = now
                row.last_replayed_by = actor
                row.updated_at = now
                return {"status": "claimed", "event": _cdc_model_to_dict(row)}

    async def clear(self) -> None:
        raise RuntimeError("clear_store is test-only and unavailable for PostgreSQL CDC store")


def _cdc_model_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "event_id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "connector": row.connector,
        "provider_event_id": row.provider_event_id,
        "fingerprint": row.fingerprint,
        "event_hash": row.event_hash,
        "event_type": row.event_type,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "payload": copy.deepcopy(row.payload),
        "raw_body_hash": row.raw_body_hash,
        "signature_verification_status": row.signature_verification_status,
        "processing_status": row.processing_status,
        "processing_outcome": copy.deepcopy(row.processing_outcome or {}),
        "replay_status": row.replay_status,
        "processed": bool(row.processed),
        "replay_attempts": int(row.replay_attempts or 0),
        "received_at": row.received_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "processed_at": row.processed_at,
        "last_replayed_at": row.last_replayed_at,
        "last_replayed_by": row.last_replayed_by,
        "error_details": copy.deepcopy(row.error_details),
    }


_default_store: CDCEventStore | None = None


def get_cdc_event_store() -> CDCEventStore:
    global _default_store
    if _default_store is not None:
        return _default_store

    if is_relaxed_env(settings.env):
        _default_store = InMemoryCDCEventStore()
        return _default_store

    from core.database import async_session_factory

    _default_store = SqlAlchemyCDCEventStore(async_session_factory)
    return _default_store


def set_cdc_event_store_for_tests(store: CDCEventStore | None) -> None:
    """Install an explicit CDC store test seam."""
    global _default_store
    _default_store = store


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Use the async CDC store APIs from an active event loop")


async def handle_cdc_webhook(
    tenant_id: str,
    connector: str,
    payload: dict[str, Any],
    signature: str,
    *,
    raw_body: bytes | None = None,
    store: CDCEventStore | None = None,
) -> dict[str, Any]:
    """Process an incoming CDC webhook for a tenant."""
    if not tenant_id or not str(tenant_id).strip():
        return {"status": "rejected", "reason": "missing_tenant", "http_status": 400}
    if not connector or not str(connector).strip():
        return {"status": "rejected", "reason": "missing_connector", "http_status": 422}

    shape, shape_error = _validate_payload_shape(payload)
    if shape_error:
        return shape_error
    assert shape is not None

    canonical_bytes = _canonical_payload_bytes(payload)
    if not _validate_signature(
        canonical_bytes,
        signature,
        connector,
        alternate_payload_bytes=raw_body,
    ):
        return {"status": "rejected", "reason": "invalid_signature", "http_status": 403}

    payload_hash = _sha256_bytes(canonical_bytes)
    raw_body_hash = _sha256_bytes(raw_body if raw_body is not None else canonical_bytes)
    fingerprint = _compute_fingerprint(
        connector=connector,
        event_type=shape["event_type"],
        resource_type=shape["resource_type"],
        resource_id=shape["resource_id"],
        payload_hash=payload_hash,
    )
    received_at = datetime.now(UTC)
    event = {
        "tenant_id": str(tenant_id),
        "connector": connector,
        "provider_event_id": _provider_event_id(payload),
        "fingerprint": fingerprint,
        "event_hash": fingerprint,
        "event_type": shape["event_type"],
        "resource_type": shape["resource_type"],
        "resource_id": shape["resource_id"],
        "payload": copy.deepcopy(_json_safe(payload)),
        "raw_body_hash": raw_body_hash,
        "signature_verification_status": "valid",
        "processing_status": "received",
        "processing_outcome": {},
        "replay_status": "not_replayed",
        "processed": False,
        "received_at": received_at,
    }

    event_store = store or get_cdc_event_store()
    try:
        stored = await event_store.insert_event(event)
    except ValueError as exc:
        logger.warning("cdc_webhook_invalid_tenant", tenant_id=tenant_id, error=str(exc))
        return {"status": "rejected", "reason": "invalid_tenant", "http_status": 422}
    # enterprise-gate: broad-except-ok reason=cdc-store-failure-returns-retryable-503-without-accepting-event
    # enterprise-gate: broad-except-ok reason=cdc-trigger-failure-dead-letters-durable-event
    # enterprise-gate: broad-except-ok reason=cdc-replay-trigger-failure-marks-replay-failed
    except Exception as exc:  # noqa: BLE001
        logger.exception("cdc_webhook_store_failed", connector=connector, error=str(exc))
        return {"status": "error", "reason": "store_unavailable", "http_status": 503}

    stored_event = stored["event"]
    if not stored["inserted"]:
        return {
            "status": "duplicate",
            "event_id": str(stored_event["id"]),
            "fingerprint": stored_event["fingerprint"],
            "processing_status": stored_event.get("processing_status", "received"),
            "http_status": 200,
        }

    try:
        from core.cdc.triggers import evaluate_triggers

        matched_workflows = evaluate_triggers(stored_event, tenant_id=str(tenant_id))
        await event_store.mark_processed(
            str(stored_event["id"]),
            outcome={"matched_workflows": matched_workflows, "workflow_count": len(matched_workflows)},
        )
        stored_event["processed"] = True
        stored_event["processing_status"] = "processed"
        stored_event["processing_outcome"] = {
            "matched_workflows": matched_workflows,
            "workflow_count": len(matched_workflows),
        }
    # enterprise-gate: broad-except-ok reason=cdc-replay-trigger-failure-marks-replay-failed
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "cdc_trigger_evaluation_failed",
            event_id=str(stored_event["id"]),
            tenant_id=tenant_id,
            connector=connector,
            error=str(exc),
        )
        await event_store.mark_failed(
            str(stored_event["id"]),
            failure_stage="trigger_evaluation",
            error_code="trigger_evaluation_failed",
            error_message=str(exc),
            error_details={"exception_type": type(exc).__name__},
        )
        stored_event["processing_status"] = "dead_lettered"
        stored_event["processing_outcome"] = {
            "failure_stage": "trigger_evaluation",
            "error_code": "trigger_evaluation_failed",
            "error_message": str(exc),
        }

    return {
        "status": "accepted",
        "event_id": str(stored_event["id"]),
        "fingerprint": fingerprint,
        "processing_status": stored_event.get("processing_status", "processed"),
        "event": _public_event(stored_event),
        "http_status": 202,
    }


async def list_stored_events(
    *,
    tenant_id: str | None = None,
    connector: str | None = None,
    event_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    store: CDCEventStore | None = None,
) -> tuple[list[dict[str, Any]], int]:
    event_store = store or get_cdc_event_store()
    events, total = await event_store.list_events(
        tenant_id=tenant_id,
        connector=connector,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return [_public_event(event) for event in events], total


def get_stored_events(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Return stored CDC events for tests/diagnostics.

    Production API paths use ``list_stored_events`` so they can read
    PostgreSQL asynchronously. The sync wrapper remains for legacy tests and
    refuses to run inside an active event loop for SQL-backed stores.
    """
    store = get_cdc_event_store()
    if isinstance(store, InMemoryCDCEventStore):
        return [_public_event(event) for event in store.list_events_sync(tenant_id=tenant_id)]
    events, _total = _run_sync(list_stored_events(tenant_id=tenant_id, store=store))
    return events


def clear_store() -> None:
    """Clear the configured CDC test store."""
    store = get_cdc_event_store()
    if isinstance(store, InMemoryCDCEventStore):
        store.clear_sync()
        return
    _run_sync(store.clear())


async def replay_cdc_event(
    *,
    tenant_id: str,
    event_id: str,
    actor: str = "admin",
    store: CDCEventStore | None = None,
) -> dict[str, Any]:
    """Replay a failed CDC event once, scoped to a tenant."""
    event_store = store or get_cdc_event_store()
    claim = await event_store.claim_replay(tenant_id=tenant_id, event_id=event_id, actor=actor)
    if claim["status"] == "not_found":
        return {"status": "not_found", "http_status": 404}
    if claim["status"] == "duplicate":
        return {
            "status": "duplicate",
            "reason": "already_replayed_or_processed",
            "event_id": event_id,
            "http_status": 200,
        }

    event = claim["event"]
    try:
        from core.cdc.triggers import evaluate_triggers

        matched_workflows = evaluate_triggers(event, tenant_id=tenant_id)
        await event_store.mark_processed(
            event_id,
            outcome={
                "matched_workflows": matched_workflows,
                "workflow_count": len(matched_workflows),
                "replayed_by": actor,
            },
            replay_status="replayed",
        )
        logger.info("cdc_event_replayed", tenant_id=tenant_id, event_id=event_id, actor=actor)
        return {
            "status": "replayed",
            "event_id": event_id,
            "matched_workflows": matched_workflows,
            "http_status": 202,
        }
    # enterprise-gate: broad-except-ok reason=cdc-replay-trigger-failure-marks-replay-failed
    except Exception as exc:  # noqa: BLE001
        await event_store.mark_failed(
            event_id,
            failure_stage="replay_trigger_evaluation",
            error_code="replay_trigger_evaluation_failed",
            error_message=str(exc),
            error_details={"exception_type": type(exc).__name__, "replayed_by": actor},
            replay_status="failed",
        )
        logger.exception(
            "cdc_event_replay_failed",
            tenant_id=tenant_id,
            event_id=event_id,
            actor=actor,
            error=str(exc),
        )
        return {
            "status": "failed",
            "event_id": event_id,
            "reason": "replay_trigger_evaluation_failed",
            "http_status": 500,
        }
