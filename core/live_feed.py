"""Durable, brokered live feed events."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import redis.asyncio as aioredis
import structlog
from sqlalchemy import func, select, text

from core.config import redis_socket_timeout_kwargs, settings

logger = structlog.get_logger()

FeedHandler = Callable[[dict[str, Any]], Awaitable[None]]


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _coerce_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


@dataclass(slots=True)
class FeedEventRecord:
    tenant_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    correlation_id: str | None = None
    id: str | None = None
    created_at: datetime | None = None

    def to_message(self) -> dict[str, Any]:
        message = copy.deepcopy(_json_safe(self.payload or {}))
        message["id"] = self.id
        message["tenant_id"] = self.tenant_id
        message["sequence"] = self.sequence
        message["type"] = self.event_type
        message["source"] = self.source
        message["correlation_id"] = self.correlation_id
        message["created_at"] = self.created_at.isoformat() if self.created_at else None
        return message


class FeedEventRepository(Protocol):
    async def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> FeedEventRecord:
        ...

    async def list_after(
        self,
        *,
        tenant_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> list[FeedEventRecord]:
        ...


class BrokerSubscription(Protocol):
    async def close(self) -> None:
        ...


class FeedEventBroker(Protocol):
    async def publish(self, event: FeedEventRecord) -> None:
        ...

    async def subscribe(self, tenant_id: str, handler: FeedHandler) -> BrokerSubscription:
        ...


class InMemoryFeedEventRepository:
    """Durable feed event test double."""

    def __init__(self) -> None:
        self.events: dict[str, list[FeedEventRecord]] = {}

    async def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> FeedEventRecord:
        rows = self.events.setdefault(str(tenant_id), [])
        record = FeedEventRecord(
            id=str(uuid.uuid4()),
            tenant_id=str(tenant_id),
            sequence=len(rows) + 1,
            event_type=str(event_type),
            payload=copy.deepcopy(_json_safe(payload)),
            source=source,
            correlation_id=correlation_id,
            created_at=datetime.now(UTC),
        )
        rows.append(record)
        return copy.deepcopy(record)

    async def list_after(
        self,
        *,
        tenant_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> list[FeedEventRecord]:
        capped_limit = min(max(int(limit), 1), 500)
        return [
            copy.deepcopy(event)
            for event in self.events.get(str(tenant_id), [])
            if event.sequence > int(after)
        ][:capped_limit]


class SqlAlchemyFeedEventRepository:
    """PostgreSQL-backed feed event repository."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> FeedEventRecord:
        from core.models.feed import FeedEvent

        tenant_uuid = _coerce_uuid(tenant_id)
        safe_payload = _json_safe(payload)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_uuid)},
                )
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id)::bigint)"),
                    {"tenant_id": str(tenant_uuid)},
                )
                next_sequence = (
                    await session.execute(
                        select(func.coalesce(func.max(FeedEvent.sequence), 0) + 1).where(
                            FeedEvent.tenant_id == tenant_uuid
                        )
                    )
                ).scalar_one()
                row = FeedEvent(
                    tenant_id=tenant_uuid,
                    sequence=int(next_sequence),
                    event_type=str(event_type),
                    payload=safe_payload,
                    source=source,
                    correlation_id=correlation_id,
                )
                session.add(row)
                await session.flush()
                return self._from_model(row)

    async def list_after(
        self,
        *,
        tenant_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> list[FeedEventRecord]:
        from core.models.feed import FeedEvent

        tenant_uuid = _coerce_uuid(tenant_id)
        capped_limit = min(max(int(limit), 1), 500)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_uuid)},
                )
                rows = (
                    await session.execute(
                        select(FeedEvent)
                        .where(
                            FeedEvent.tenant_id == tenant_uuid,
                            FeedEvent.sequence > int(after),
                        )
                        .order_by(FeedEvent.sequence.asc())
                        .limit(capped_limit)
                    )
                ).scalars().all()
                return [self._from_model(row) for row in rows]

    @staticmethod
    def _from_model(row: Any) -> FeedEventRecord:
        return FeedEventRecord(
            id=str(row.id),
            tenant_id=str(row.tenant_id),
            sequence=int(row.sequence),
            event_type=row.event_type,
            payload=copy.deepcopy(row.payload or {}),
            source=row.source,
            correlation_id=row.correlation_id,
            created_at=row.created_at,
        )


class _InMemorySubscription:
    def __init__(self, broker: InMemoryFeedEventBroker, tenant_id: str, handler: FeedHandler) -> None:
        self._broker = broker
        self._tenant_id = tenant_id
        self._handler = handler

    async def close(self) -> None:
        self._broker._handlers.get(self._tenant_id, set()).discard(self._handler)


class InMemoryFeedEventBroker:
    """Broker test double that fanouts to local subscribers."""

    def __init__(self) -> None:
        self._handlers: dict[str, set[FeedHandler]] = {}
        self.published: list[dict[str, Any]] = []

    async def publish(self, event: FeedEventRecord) -> None:
        message = event.to_message()
        self.published.append(copy.deepcopy(message))
        for handler in list(self._handlers.get(event.tenant_id, set())):
            await handler(copy.deepcopy(message))

    async def subscribe(self, tenant_id: str, handler: FeedHandler) -> BrokerSubscription:
        self._handlers.setdefault(str(tenant_id), set()).add(handler)
        return _InMemorySubscription(self, str(tenant_id), handler)


class _RedisFeedSubscription:
    def __init__(self, redis_url: str, tenant_id: str, handler: FeedHandler) -> None:
        self._redis_url = redis_url
        self._tenant_id = str(tenant_id)
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
        await self._pubsub.subscribe(_channel(self._tenant_id))
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
                    logger.warning("live_feed_broker_invalid_json", tenant_id=self._tenant_id)
                    continue
                await self._handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - subscription failure should not crash the app.
            logger.warning(
                "live_feed_broker_subscription_failed",
                tenant_id=self._tenant_id,
                error=str(exc),
            )

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            with suppress(Exception):
                await self._pubsub.unsubscribe(_channel(self._tenant_id))
                await self._pubsub.close()
        if self._redis is not None:
            await _close_redis(self._redis)


class RedisFeedEventBroker:
    """Redis Pub/Sub broker for cross-pod live feed fanout."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: aioredis.Redis | None = None

    async def publish(self, event: FeedEventRecord) -> None:
        redis = await self._publisher()
        await redis.publish(_channel(event.tenant_id), json.dumps(event.to_message()))

    async def subscribe(self, tenant_id: str, handler: FeedHandler) -> BrokerSubscription:
        subscription = _RedisFeedSubscription(self._redis_url, str(tenant_id), handler)
        await subscription.start()
        return subscription

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


def _channel(tenant_id: str) -> str:
    return f"feed:{tenant_id}"


_repository_override: FeedEventRepository | None = None
_broker_override: FeedEventBroker | None = None
_default_repository: FeedEventRepository | None = None
_default_broker: FeedEventBroker | None = None


def get_feed_event_repository() -> FeedEventRepository:
    global _default_repository
    if _repository_override is not None:
        return _repository_override
    if _default_repository is None:
        from core.database import async_session_factory

        _default_repository = SqlAlchemyFeedEventRepository(async_session_factory)
    return _default_repository


def get_feed_event_broker() -> FeedEventBroker:
    global _default_broker
    if _broker_override is not None:
        return _broker_override
    if _default_broker is None:
        _default_broker = RedisFeedEventBroker()
    return _default_broker


def configure_live_feed_for_tests(
    *,
    repository: FeedEventRepository | None = None,
    broker: FeedEventBroker | None = None,
) -> None:
    global _broker_override, _repository_override
    _repository_override = repository
    _broker_override = broker


def reset_live_feed_for_tests() -> None:
    configure_live_feed_for_tests(repository=None, broker=None)
