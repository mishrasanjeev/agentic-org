"""Durable workflow state store.

PostgreSQL is the source of truth for workflow run state. Redis is only a
best-effort cache plus a legacy fallback for pre-durability rows.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select

from core.config import settings

logger = structlog.get_logger()


def _json_safe(value: Any) -> Any:
    """Return a JSON-compatible deep copy."""
    return json.loads(json.dumps(value, default=str))


def _state_hash(state: dict[str, Any]) -> str:
    payload = json.dumps(
        _json_safe(state),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _extract_tenant_id(state: dict[str, Any], override: Any = None) -> uuid.UUID | None:
    if override:
        return _coerce_uuid(override)
    trigger_payload = state.get("trigger_payload")
    if not isinstance(trigger_payload, dict):
        trigger_payload = {}
    return _coerce_uuid(
        state.get("tenant_id")
        or trigger_payload.get("tenant_id")
        or trigger_payload.get("agenticorg:tenant_id")
    )


def _extract_workflow_run_id(state: dict[str, Any], override: Any = None) -> uuid.UUID | None:
    if override:
        return _coerce_uuid(override)
    return _coerce_uuid(
        state.get("workflow_run_id")
        or state.get("db_workflow_run_id")
        or state.get("database_workflow_run_id")
    )


class WorkflowStateRepository(Protocol):
    """Persistence boundary for workflow state.

    Unit tests can inject an in-memory implementation. Production ``init()``
    swaps the default in-memory repository for the SQLAlchemy-backed one.
    """

    async def save(
        self,
        state: dict[str, Any],
        *,
        state_hash: str,
        tenant_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        actor: str,
        step_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ...

    async def load(self, run_id: str) -> dict[str, Any] | None:
        ...


class InMemoryWorkflowStateRepository:
    """Durable-store test double with transition capture."""

    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self.transitions: list[dict[str, Any]] = []

    async def save(
        self,
        state: dict[str, Any],
        *,
        state_hash: str,
        tenant_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        actor: str,
        step_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        run_id = str(state["id"])
        if idempotency_key and any(
            t["run_id"] == run_id and t.get("idempotency_key") == idempotency_key
            for t in self.transitions
        ):
            return

        previous = self.states.get(run_id)
        previous_hash = previous["state_hash"] if previous else None
        version = int(previous["version"]) + 1 if previous else 1
        now = datetime.now(UTC).isoformat()

        self.states[run_id] = {
            "run_id": run_id,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "workflow_run_id": str(workflow_run_id) if workflow_run_id else None,
            "status": state.get("status", "unknown"),
            "waiting_step_id": state.get("waiting_step_id"),
            "state": copy.deepcopy(_json_safe(state)),
            "state_hash": state_hash,
            "version": version,
            "updated_at": now,
        }
        self.transitions.append(
            {
                "run_id": run_id,
                "step_id": step_id,
                "previous_state_hash": previous_hash,
                "new_state_hash": state_hash,
                "actor": actor,
                "idempotency_key": idempotency_key,
                "metadata": copy.deepcopy(metadata or {}),
                "created_at": now,
            }
        )

    async def load(self, run_id: str) -> dict[str, Any] | None:
        record = self.states.get(str(run_id))
        if not record:
            return None
        return copy.deepcopy(record["state"])


class SqlAlchemyWorkflowStateRepository:
    """PostgreSQL implementation using the app's async SQLAlchemy session factory."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def save(
        self,
        state: dict[str, Any],
        *,
        state_hash: str,
        tenant_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        actor: str,
        step_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from core.models.workflow import WorkflowRunState, WorkflowStateTransition

        run_id = str(state["id"])
        safe_state = _json_safe(state)
        async with self._session_factory() as session:
            async with session.begin():
                if idempotency_key:
                    existing_transition = (
                        await session.execute(
                            select(WorkflowStateTransition.id).where(
                                WorkflowStateTransition.run_id == run_id,
                                WorkflowStateTransition.idempotency_key == idempotency_key,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing_transition:
                        return

                row = (
                    await session.execute(
                        select(WorkflowRunState)
                        .where(WorkflowRunState.run_id == run_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()

                previous_hash = row.state_hash if row else None
                if row is None:
                    row = WorkflowRunState(
                        run_id=run_id,
                        tenant_id=tenant_id,
                        workflow_run_id=workflow_run_id,
                        status=safe_state.get("status", "unknown"),
                        waiting_step_id=safe_state.get("waiting_step_id"),
                        state=safe_state,
                        state_hash=state_hash,
                        version=1,
                    )
                    session.add(row)
                else:
                    row.tenant_id = tenant_id or row.tenant_id
                    row.workflow_run_id = workflow_run_id or row.workflow_run_id
                    row.status = safe_state.get("status", "unknown")
                    row.waiting_step_id = safe_state.get("waiting_step_id")
                    row.state = safe_state
                    row.state_hash = state_hash
                    row.version = int(row.version or 0) + 1
                    row.updated_at = datetime.now(UTC)

                session.add(
                    WorkflowStateTransition(
                        run_id=run_id,
                        step_id=step_id,
                        previous_state_hash=previous_hash,
                        new_state_hash=state_hash,
                        actor=actor,
                        idempotency_key=idempotency_key,
                        transition_metadata=_json_safe(metadata or {}),
                    )
                )

    async def load(self, run_id: str) -> dict[str, Any] | None:
        from core.models.workflow import WorkflowRunState

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(WorkflowRunState).where(WorkflowRunState.run_id == str(run_id))
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return copy.deepcopy(row.state)


class WorkflowStateStore:
    def __init__(
        self,
        repository: WorkflowStateRepository | None = None,
        redis: aioredis.Redis | None = None,
    ) -> None:
        self.repository: WorkflowStateRepository = repository or InMemoryWorkflowStateRepository()
        self._repository_explicit = repository is not None
        self.redis = redis

    async def init(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        if not self._repository_explicit:
            from core.database import async_session_factory

            self.repository = SqlAlchemyWorkflowStateRepository(async_session_factory)

    async def save(
        self,
        state: dict[str, Any],
        *,
        actor: str = "workflow_engine",
        step_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: uuid.UUID | str | None = None,
        workflow_run_id: uuid.UUID | str | None = None,
    ) -> None:
        run_id = state.get("id")
        if not run_id:
            raise ValueError("workflow state must include an 'id'")

        safe_hash = _state_hash(state)
        effective_tenant_id = _extract_tenant_id(state, tenant_id)
        effective_workflow_run_id = _extract_workflow_run_id(state, workflow_run_id)

        await self.repository.save(
            state,
            state_hash=safe_hash,
            tenant_id=effective_tenant_id,
            workflow_run_id=effective_workflow_run_id,
            actor=actor,
            step_id=step_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        await self._write_redis_cache(state)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        state = await self.repository.load(run_id)
        if state is not None:
            return state

        legacy_state = await self._load_redis_legacy(run_id)
        if legacy_state is None:
            return None

        await self.save(
            legacy_state,
            actor="redis_legacy_backfill",
            metadata={"source": "redis_fallback"},
        )
        return legacy_state

    async def close(self) -> None:
        if not self.redis:
            return
        close_fn = getattr(self.redis, "close", None) or getattr(self.redis, "aclose", None)
        if close_fn is None:
            return
        result = close_fn()
        if inspect.isawaitable(result):
            await result

    async def _write_redis_cache(self, state: dict[str, Any]) -> None:
        if not self.redis:
            return
        try:
            await self.redis.set(f"wfstate:{state['id']}", json.dumps(_json_safe(state)))
        except Exception as exc:  # noqa: BLE001 - Redis cache must not gate correctness.
            logger.warning(
                "workflow_state_cache_write_failed",
                run_id=state.get("id"),
                error=str(exc),
            )

    async def _load_redis_legacy(self, run_id: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        try:
            data = await self.redis.get(f"wfstate:{run_id}")
        except Exception as exc:  # noqa: BLE001 - Redis fallback is best effort.
            logger.warning("workflow_state_cache_read_failed", run_id=run_id, error=str(exc))
            return None
        return json.loads(data) if data else None
