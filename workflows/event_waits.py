"""Durable workflow event-wait listener store.

PostgreSQL is authoritative for wait_for_event listener registration and
matching. Redis is used only as a rebuildable cache/index for older paths.
"""

from __future__ import annotations

import copy
import inspect
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select

from core.config import settings

logger = structlog.get_logger()

ACTIVE_STATUS = "waiting"
TERMINAL_STATUSES = {"matched", "timed_out", "cancelled", "expired"}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _event_matches_criteria(
    event_fields: dict[str, Any],
    match_criteria: dict[str, Any],
) -> bool:
    if not match_criteria:
        return True
    for key, expected in match_criteria.items():
        actual = event_fields.get(key)
        if actual is None:
            return False
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False
    return True


@dataclass(slots=True)
class EventWaitRecord:
    engine_run_id: str
    step_id: str
    event_type: str
    match_criteria: dict[str, Any] = field(default_factory=dict)
    tenant_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    connector: str | None = None
    provider: str | None = None
    status: str = ACTIVE_STATUS
    timeout_at: datetime | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    matched_at: datetime | None = None
    matched_event_id: str | None = None
    matched_event: dict[str, Any] | None = None


class WorkflowEventWaitRepository(Protocol):
    async def register(self, record: EventWaitRecord) -> EventWaitRecord:
        ...

    async def list_waiting(
        self,
        *,
        event_types: Sequence[str] | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> list[EventWaitRecord]:
        ...

    async def claim_matched(
        self,
        *,
        engine_run_id: str,
        step_id: str,
        matched_event_id: str | None,
        event_data: dict[str, Any],
    ) -> EventWaitRecord | None:
        ...

    async def mark_timed_out(
        self,
        *,
        engine_run_id: str,
        step_id: str,
    ) -> EventWaitRecord | None:
        ...


class InMemoryWorkflowEventWaitRepository:
    """Durable event-wait test double."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], EventWaitRecord] = {}

    async def register(self, record: EventWaitRecord) -> EventWaitRecord:
        now = datetime.now(UTC)
        key = (record.engine_run_id, record.step_id)
        existing = self.records.get(key)
        if existing and existing.status == ACTIVE_STATUS:
            existing.tenant_id = record.tenant_id
            existing.workflow_run_id = record.workflow_run_id
            existing.event_type = record.event_type
            existing.connector = record.connector
            existing.provider = record.provider
            existing.match_criteria = copy.deepcopy(_json_safe(record.match_criteria))
            existing.timeout_at = record.timeout_at
            existing.updated_at = now
            return copy.deepcopy(existing)

        stored = copy.deepcopy(record)
        stored.id = stored.id or uuid.uuid4()
        stored.match_criteria = copy.deepcopy(_json_safe(stored.match_criteria))
        stored.status = ACTIVE_STATUS
        stored.created_at = now
        stored.updated_at = now
        self.records[key] = stored
        return copy.deepcopy(stored)

    async def list_waiting(
        self,
        *,
        event_types: Sequence[str] | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> list[EventWaitRecord]:
        event_type_set = set(event_types or [])
        now = datetime.now(UTC)
        records: list[EventWaitRecord] = []
        for record in self.records.values():
            if record.status != ACTIVE_STATUS:
                continue
            if event_type_set and record.event_type not in event_type_set:
                continue
            if record.tenant_id != tenant_id:
                continue
            if record.timeout_at and record.timeout_at <= now:
                continue
            records.append(copy.deepcopy(record))
        return records

    async def claim_matched(
        self,
        *,
        engine_run_id: str,
        step_id: str,
        matched_event_id: str | None,
        event_data: dict[str, Any],
    ) -> EventWaitRecord | None:
        record = self.records.get((engine_run_id, step_id))
        if not record or record.status != ACTIVE_STATUS:
            return None
        now = datetime.now(UTC)
        record.status = "matched"
        record.matched_at = now
        record.updated_at = now
        record.matched_event_id = matched_event_id
        record.matched_event = copy.deepcopy(_json_safe(event_data))
        return copy.deepcopy(record)

    async def mark_timed_out(
        self,
        *,
        engine_run_id: str,
        step_id: str,
    ) -> EventWaitRecord | None:
        record = self.records.get((engine_run_id, step_id))
        if not record:
            return None
        if record.status == ACTIVE_STATUS:
            now = datetime.now(UTC)
            record.status = "timed_out"
            record.updated_at = now
        return copy.deepcopy(record)


class SqlAlchemyWorkflowEventWaitRepository:
    """PostgreSQL implementation using the app async SQLAlchemy session factory."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def register(self, record: EventWaitRecord) -> EventWaitRecord:
        from core.models.workflow import WorkflowEventWait

        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(WorkflowEventWait)
                        .where(
                            WorkflowEventWait.engine_run_id == record.engine_run_id,
                            WorkflowEventWait.step_id == record.step_id,
                            WorkflowEventWait.status == ACTIVE_STATUS,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()

                if row is None:
                    row = WorkflowEventWait(
                        tenant_id=record.tenant_id,
                        engine_run_id=record.engine_run_id,
                        workflow_run_id=record.workflow_run_id,
                        step_id=record.step_id,
                        event_type=record.event_type,
                        connector=record.connector,
                        provider=record.provider,
                        match_criteria=_json_safe(record.match_criteria or {}),
                        status=ACTIVE_STATUS,
                        timeout_at=record.timeout_at,
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.tenant_id = record.tenant_id or row.tenant_id
                    row.workflow_run_id = record.workflow_run_id or row.workflow_run_id
                    row.event_type = record.event_type
                    row.connector = record.connector
                    row.provider = record.provider
                    row.match_criteria = _json_safe(record.match_criteria or {})
                    row.timeout_at = record.timeout_at
                    row.updated_at = datetime.now(UTC)

                return self._from_model(row)

    async def list_waiting(
        self,
        *,
        event_types: Sequence[str] | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> list[EventWaitRecord]:
        from core.models.workflow import WorkflowEventWait

        conditions = [
            WorkflowEventWait.status == ACTIVE_STATUS,
            (WorkflowEventWait.timeout_at.is_(None))
            | (WorkflowEventWait.timeout_at > datetime.now(UTC)),
        ]
        if event_types:
            conditions.append(WorkflowEventWait.event_type.in_(list(event_types)))
        if tenant_id is None:
            conditions.append(WorkflowEventWait.tenant_id.is_(None))
        else:
            conditions.append(WorkflowEventWait.tenant_id == tenant_id)

        async with self._session_factory() as session:
            rows = (
                await session.execute(select(WorkflowEventWait).where(*conditions))
            ).scalars().all()
            return [self._from_model(row) for row in rows]

    async def claim_matched(
        self,
        *,
        engine_run_id: str,
        step_id: str,
        matched_event_id: str | None,
        event_data: dict[str, Any],
    ) -> EventWaitRecord | None:
        from core.models.workflow import WorkflowEventWait

        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(WorkflowEventWait)
                        .where(
                            WorkflowEventWait.engine_run_id == engine_run_id,
                            WorkflowEventWait.step_id == step_id,
                            WorkflowEventWait.status == ACTIVE_STATUS,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None or row.status != ACTIVE_STATUS:
                    return None

                now = datetime.now(UTC)
                row.status = "matched"
                row.matched_at = now
                row.updated_at = now
                row.matched_event_id = matched_event_id
                row.matched_event = _json_safe(event_data)
                return self._from_model(row)

    async def mark_timed_out(
        self,
        *,
        engine_run_id: str,
        step_id: str,
    ) -> EventWaitRecord | None:
        from core.models.workflow import WorkflowEventWait

        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(WorkflowEventWait)
                        .where(
                            WorkflowEventWait.engine_run_id == engine_run_id,
                            WorkflowEventWait.step_id == step_id,
                        )
                        .order_by(WorkflowEventWait.updated_at.desc().nullslast())
                        .limit(1)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                if row.status == ACTIVE_STATUS:
                    row.status = "timed_out"
                    row.updated_at = datetime.now(UTC)
                return self._from_model(row)

    @staticmethod
    def _from_model(row: Any) -> EventWaitRecord:
        return EventWaitRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            engine_run_id=row.engine_run_id,
            workflow_run_id=row.workflow_run_id,
            step_id=row.step_id,
            event_type=row.event_type,
            connector=row.connector,
            provider=row.provider,
            match_criteria=copy.deepcopy(row.match_criteria or {}),
            status=row.status,
            timeout_at=row.timeout_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            matched_at=row.matched_at,
            matched_event_id=row.matched_event_id,
            matched_event=copy.deepcopy(row.matched_event),
        )


class WorkflowEventWaitStore:
    def __init__(
        self,
        repository: WorkflowEventWaitRepository | None = None,
        redis: aioredis.Redis | None = None,
    ) -> None:
        self.repository: WorkflowEventWaitRepository = (
            repository or InMemoryWorkflowEventWaitRepository()
        )
        self._repository_explicit = repository is not None
        self.redis = redis
        self._redis_explicit = redis is not None

    async def init(self) -> None:
        if not self._redis_explicit:
            self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        if not self._repository_explicit:
            from core.database import async_session_factory

            self.repository = SqlAlchemyWorkflowEventWaitRepository(async_session_factory)

    async def close(self) -> None:
        if not self.redis or self._redis_explicit:
            return
        close_fn = getattr(self.redis, "aclose", None) or getattr(self.redis, "close", None)
        if close_fn is None:
            return
        result = close_fn()
        if inspect.isawaitable(result):
            await result

    async def register(
        self,
        *,
        engine_run_id: str,
        step_id: str,
        event_type: str,
        match_criteria: dict[str, Any] | None = None,
        timeout_at: datetime | None = None,
        tenant_id: uuid.UUID | str | None = None,
        workflow_run_id: uuid.UUID | str | None = None,
        connector: str | None = None,
        provider: str | None = None,
    ) -> EventWaitRecord:
        record = EventWaitRecord(
            tenant_id=_coerce_uuid(tenant_id),
            engine_run_id=str(engine_run_id),
            workflow_run_id=_coerce_uuid(workflow_run_id),
            step_id=str(step_id),
            event_type=str(event_type),
            connector=connector,
            provider=provider,
            match_criteria=copy.deepcopy(_json_safe(match_criteria or {})),
            timeout_at=timeout_at,
        )
        stored = await self.repository.register(record)
        await self._write_redis_index(stored)
        return stored

    async def claim_matching_waits(
        self,
        *,
        event_types: Sequence[str],
        event_fields: dict[str, Any],
        tenant_id: uuid.UUID | str | None = None,
        matched_event_id: str | None = None,
    ) -> list[EventWaitRecord]:
        effective_tenant_id = _coerce_uuid(tenant_id)
        waiting = await self.repository.list_waiting(
            event_types=event_types,
            tenant_id=effective_tenant_id,
        )
        await self._write_redis_indexes(waiting)

        claimed: list[EventWaitRecord] = []
        for record in waiting:
            if not _event_matches_criteria(event_fields, record.match_criteria):
                logger.debug(
                    "workflow_event_criteria_mismatch",
                    event_types=list(event_types),
                    match=record.match_criteria,
                    run_id=record.engine_run_id,
                    step_id=record.step_id,
                )
                continue
            matched = await self.repository.claim_matched(
                engine_run_id=record.engine_run_id,
                step_id=record.step_id,
                matched_event_id=matched_event_id,
                event_data=event_fields,
            )
            if matched is not None:
                claimed.append(matched)
                await self._delete_redis_index(matched)
        return claimed

    async def mark_timed_out(self, *, engine_run_id: str, step_id: str) -> EventWaitRecord | None:
        record = await self.repository.mark_timed_out(
            engine_run_id=str(engine_run_id),
            step_id=str(step_id),
        )
        if record and record.status in TERMINAL_STATUSES:
            await self._delete_redis_index(record)
        return record

    async def rebuild_redis_indexes(
        self,
        *,
        event_types: Sequence[str] | None = None,
        tenant_id: uuid.UUID | str | None = None,
    ) -> int:
        waiting = await self.repository.list_waiting(
            event_types=event_types,
            tenant_id=_coerce_uuid(tenant_id),
        )
        await self._write_redis_indexes(waiting)
        return len(waiting)

    async def _write_redis_indexes(self, records: Sequence[EventWaitRecord]) -> None:
        for record in records:
            await self._write_redis_index(record)

    async def _write_redis_index(self, record: EventWaitRecord) -> None:
        if not self.redis:
            return
        payload = {
            "run_id": record.engine_run_id,
            "step_id": record.step_id,
            "tenant_id": str(record.tenant_id) if record.tenant_id else None,
            "workflow_run_id": str(record.workflow_run_id) if record.workflow_run_id else None,
            "event_type": record.event_type,
            "connector": record.connector,
            "provider": record.provider,
            "match": record.match_criteria,
            "timeout_at": record.timeout_at.isoformat() if record.timeout_at else None,
        }
        ttl_seconds: int | None = None
        if record.timeout_at:
            ttl_seconds = max(int((record.timeout_at - datetime.now(UTC)).total_seconds()) + 60, 60)
        try:
            kwargs = {"ex": ttl_seconds} if ttl_seconds else {}
            await self.redis.set(self._redis_key(record), json.dumps(_json_safe(payload)), **kwargs)
        # enterprise-gate: broad-except-ok reason=postgres-event-wait-registration-is-authoritative
        except Exception as exc:  # noqa: BLE001 - Redis index is best-effort only.
            logger.warning(
                "workflow_event_wait_cache_write_failed",
                run_id=record.engine_run_id,
                step_id=record.step_id,
                error=str(exc),
            )

    async def _delete_redis_index(self, record: EventWaitRecord) -> None:
        if not self.redis:
            return
        try:
            await self.redis.delete(self._redis_key(record))
        # enterprise-gate: broad-except-ok reason=postgres-event-wait-status-is-authoritative
        except Exception as exc:  # noqa: BLE001 - Redis cleanup is best-effort only.
            logger.warning(
                "workflow_event_wait_cache_delete_failed",
                run_id=record.engine_run_id,
                step_id=record.step_id,
                error=str(exc),
            )

    @staticmethod
    def _redis_key(record: EventWaitRecord) -> str:
        return f"wfwait_event:{record.event_type}:{record.engine_run_id}:{record.step_id}"
