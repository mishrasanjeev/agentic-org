from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.deps import get_current_tenant
from api.websocket import feed
from core.live_feed import (
    InMemoryFeedEventBroker,
    InMemoryFeedEventRepository,
    _RedisFeedSubscription,
    configure_live_feed_for_tests,
    reset_live_feed_for_tests,
)


@pytest.fixture
def feed_runtime() -> Generator[tuple[InMemoryFeedEventRepository, InMemoryFeedEventBroker]]:
    repository = InMemoryFeedEventRepository()
    broker = InMemoryFeedEventBroker()
    configure_live_feed_for_tests(repository=repository, broker=broker)
    feed._connections.clear()
    feed._subscriptions.clear()
    feed._subscription_tasks.clear()
    try:
        yield repository, broker
    finally:
        feed._connections.clear()
        feed._subscriptions.clear()
        feed._subscription_tasks.clear()
        reset_live_feed_for_tests()


# Session credentials travel in the cookie or the Authorization header;
# ``?token=`` query strings are rejected (audit 2026-09-13: JWTs in logs).
_AUTH = {"Authorization": "Bearer valid"}


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(feed.router, prefix="/api/v1")
    return app


def test_unauthenticated_websocket_handshake_is_rejected(feed_runtime) -> None:
    tenant_id = str(uuid.uuid4())
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}"):
            pass

    assert exc.value.code == 1008


def test_query_string_token_is_not_honoured(feed_runtime) -> None:
    tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": tenant_id, "grantex:scopes": []}
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}?token=valid"):
                pass

    assert exc.value.code == 1008


def test_authenticated_matching_tenant_connects(feed_runtime) -> None:
    tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": tenant_id, "grantex:scopes": []}
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with patch("api.websocket.feed.check_user_session_state", new_callable=AsyncMock, return_value=None):
            with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}", headers=_AUTH) as websocket:
                message = websocket.receive_json()

    assert message == {"type": "heartbeat", "tenant_id": tenant_id, "sequence": None}


def test_open_socket_closes_after_session_revocation(feed_runtime, monkeypatch) -> None:
    tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": tenant_id, "grantex:scopes": []}
    monkeypatch.setattr(feed, "FEED_AUTH_REVALIDATE_SECONDS", 0.02)
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with patch(
            "api.websocket.feed.check_user_session_state",
            new_callable=AsyncMock,
            side_effect=[None, "revoked"],
        ) as check:
            with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}", headers=_AUTH) as websocket:
                assert websocket.receive_json()["type"] == "heartbeat"
                with pytest.raises(WebSocketDisconnect) as exc:
                    for _ in range(3):
                        assert websocket.receive_json()["type"] == "heartbeat"

    assert exc.value.code == 1008
    assert check.await_count == 2
    assert tenant_id not in feed._connections


def test_auth_backend_unavailable_during_handshake_is_retryable(feed_runtime) -> None:
    tenant_id = str(uuid.uuid4())
    client = TestClient(_test_app())
    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, side_effect=RuntimeError("redis down")):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}", headers=_AUTH):
                pass
    assert exc.value.code == 1013


@pytest.mark.asyncio
async def test_api_key_recheck_uses_verified_record_without_bcrypt(feed_runtime, monkeypatch) -> None:
    tenant_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    key = SimpleNamespace(status="active", tenant_id=tenant_id, expires_at=None, scopes=["feed.read"])

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return SimpleNamespace(scalar_one_or_none=lambda: key)

    monkeypatch.setattr(feed, "async_session_factory", Session)
    original = {
        "credential_kind": "api_key",
        "claims": {"agenticorg:api_key_id": key_id},
        "scopes": ["feed.read"],
    }
    await feed.revalidate_websocket(AsyncMock(), tenant_id, original)
    key.status = "revoked"
    with pytest.raises(feed.WebSocketAuthError) as exc:
        await feed.revalidate_websocket(AsyncMock(), tenant_id, original)
    assert exc.value.code == "invalid_api_key"


@pytest.mark.asyncio
async def test_session_recheck_fails_closed_when_auth_store_is_unavailable(feed_runtime, monkeypatch) -> None:
    async def unavailable(_socket, _tenant_id):
        raise ConnectionError("auth store unavailable")

    monkeypatch.setattr(feed, "authenticate_websocket", unavailable)
    original = {"credential_kind": "session", "claims": {"sub": "user-1"}, "scopes": ["feed.read"]}
    with pytest.raises(feed.WebSocketAuthError) as exc:
        await feed.revalidate_websocket(AsyncMock(), "tenant-1", original)
    assert exc.value.code == "auth_unavailable"


def test_authenticated_tenant_mismatch_is_rejected(feed_runtime) -> None:
    path_tenant_id = str(uuid.uuid4())
    token_tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": token_tenant_id, "grantex:scopes": []}
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/v1/ws/feed/{path_tenant_id}", headers=_AUTH):
                pass

    assert exc.value.code == 1008


@pytest.mark.asyncio
async def test_broadcast_to_tenant_persists_sequence_and_brokers_to_subscribers(feed_runtime) -> None:
    repository, broker = feed_runtime
    tenant_id = str(uuid.uuid4())
    delivered: list[dict] = []

    async def _handler(message: dict) -> None:
        delivered.append(message)

    await broker.subscribe(tenant_id, _handler)

    connected_count = await feed.broadcast_to_tenant(
        tenant_id,
        {
            "type": "hitl.approval.created",
            "payload": {"approval_id": "hitl-1"},
            "source": "approvals",
            "correlation_id": "corr-1",
        },
    )

    stored = await repository.list_after(tenant_id=tenant_id, after=0)
    assert connected_count == 0
    assert len(stored) == 1
    assert stored[0].sequence == 1
    assert stored[0].event_type == "hitl.approval.created"
    assert broker.published[0]["sequence"] == 1
    assert delivered[0]["sequence"] == 1
    assert delivered[0]["type"] == "hitl.approval.created"


def test_catch_up_endpoint_returns_only_caller_tenant_events_after_sequence(feed_runtime) -> None:
    repository, _broker = feed_runtime
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    async def _seed() -> None:
        await repository.append(tenant_id=tenant_a, event_type="first", payload={"type": "first"})
        await repository.append(tenant_id=tenant_a, event_type="second", payload={"type": "second"})
        await repository.append(tenant_id=tenant_b, event_type="other", payload={"type": "other"})

    import anyio

    anyio.run(_seed)

    app = _test_app()
    app.dependency_overrides[get_current_tenant] = lambda: tenant_a
    client = TestClient(app)

    response = client.get("/api/v1/feed/events?after=1")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == tenant_a
    assert [item["type"] for item in body["items"]] == ["second"]
    assert [item["tenant_id"] for item in body["items"]] == [tenant_a]


def test_bad_json_socket_message_is_isolated(feed_runtime) -> None:
    tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": tenant_id, "grantex:scopes": []}
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with patch("api.websocket.feed.check_user_session_state", new_callable=AsyncMock, return_value=None):
            with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}", headers=_AUTH) as websocket:
                assert websocket.receive_json()["type"] == "heartbeat"
                websocket.send_text("{bad-json")
                assert websocket.receive_json()["code"] == "invalid_json"
                websocket.send_json({"type": "ping"})
                assert websocket.receive_json()["type"] == "pong"


@pytest.mark.asyncio
async def test_slow_socket_does_not_block_peer_and_is_removed(feed_runtime, monkeypatch) -> None:
    tenant_id = str(uuid.uuid4())
    slow = AsyncMock()
    fast = AsyncMock()
    async def never_send(_message: dict) -> None:
        await asyncio.Event().wait()

    slow.send_json.side_effect = never_send
    monkeypatch.setattr(feed, "FEED_SOCKET_SEND_TIMEOUT_SECONDS", 0.01)
    feed._connections[tenant_id] = {slow, fast}

    delivered = await asyncio.wait_for(
        feed._fanout_local({"tenant_id": tenant_id, "type": "update"}), timeout=0.2
    )

    assert delivered == 1
    fast.send_json.assert_awaited_once()
    assert slow not in feed._connections[tenant_id]
    assert fast in feed._connections[tenant_id]


@pytest.mark.asyncio
async def test_fanout_caps_parallel_sends_for_large_tenant(feed_runtime, monkeypatch) -> None:
    tenant_id = str(uuid.uuid4())
    active = 0
    peak = 0

    async def send(_message: dict) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
        finally:
            active -= 1

    sockets = {AsyncMock() for _ in range(100)}
    for socket in sockets:
        socket.send_json.side_effect = send
    monkeypatch.setattr(feed, "FEED_SOCKET_SEND_TIMEOUT_SECONDS", 0.2)
    feed._connections[tenant_id] = sockets

    assert await feed._fanout_local({"tenant_id": tenant_id, "type": "update"}) == 100
    assert 1 < peak <= 32


@pytest.mark.asyncio
async def test_fanout_closes_subscription_after_last_socket_fails(feed_runtime, monkeypatch) -> None:
    tenant_id = str(uuid.uuid4())
    socket = AsyncMock()

    async def never_send(_message: dict) -> None:
        await asyncio.Event().wait()

    socket.send_json.side_effect = never_send
    subscription = AsyncMock()
    monkeypatch.setattr(feed, "FEED_SOCKET_SEND_TIMEOUT_SECONDS", 0.01)
    feed._connections[tenant_id] = {socket}
    feed._subscriptions[tenant_id] = subscription

    assert await feed._fanout_local({"tenant_id": tenant_id, "type": "update"}) == 0
    assert tenant_id not in feed._connections
    assert tenant_id not in feed._subscriptions
    subscription.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stalled_subscription_does_not_block_other_tenants(feed_runtime, monkeypatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    original = feed.get_feed_event_broker().subscribe

    async def subscribe(tenant_id: str, handler):
        if tenant_id == "stalled":
            entered.set()
            await release.wait()
        return await original(tenant_id, handler)

    monkeypatch.setattr(feed.get_feed_event_broker(), "subscribe", subscribe)
    stalled = AsyncMock()
    healthy = AsyncMock()
    stalled_task = asyncio.create_task(feed._add_connection("stalled", stalled))
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        await asyncio.wait_for(feed._add_connection("healthy", healthy), timeout=0.2)
        assert healthy in feed._connections["healthy"]
        assert "healthy" in feed._subscriptions
    finally:
        release.set()
        await stalled_task
        await feed._remove_connection("stalled", stalled)
        await feed._remove_connection("healthy", healthy)


@pytest.mark.asyncio
async def test_same_tenant_subscription_is_single_flight(feed_runtime, monkeypatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original = feed.get_feed_event_broker().subscribe

    async def subscribe(tenant_id: str, handler):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(tenant_id, handler)

    monkeypatch.setattr(feed.get_feed_event_broker(), "subscribe", subscribe)
    first, second = AsyncMock(), AsyncMock()
    one = asyncio.create_task(feed._add_connection("same", first))
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        two = asyncio.create_task(feed._add_connection("same", second))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(one, two)
        assert calls == 1
        assert feed._connections["same"] == {first, second}
    finally:
        release.set()
        await feed._remove_connection("same", first)
        await feed._remove_connection("same", second)


@pytest.mark.asyncio
async def test_failed_subscription_does_not_leave_socket_or_task(feed_runtime, monkeypatch) -> None:
    async def fail(_tenant_id: str, _handler):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(feed.get_feed_event_broker(), "subscribe", fail)
    socket = AsyncMock()
    with pytest.raises(ConnectionError):
        await feed._add_connection("failed", socket)
    assert "failed" not in feed._connections
    assert "failed" not in feed._subscription_tasks


@pytest.mark.asyncio
async def test_redis_feed_subscription_recovers_after_listener_disconnect(monkeypatch) -> None:
    delivered = asyncio.Event()
    calls: list[dict] = []

    async def handler(message: dict) -> None:
        calls.append(message)
        delivered.set()

    class FakePubSub:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.subscribe = AsyncMock()
            self.unsubscribe = AsyncMock()
            self.close = AsyncMock()

        async def listen(self):
            if self.fail:
                raise ConnectionError("temporary disconnect")
            yield {"type": "message", "data": json.dumps({"sequence": 1})}
            await asyncio.Event().wait()

    class FakeRedis:
        def __init__(self, fail: bool) -> None:
            self.stream = FakePubSub(fail)
            self.aclose = AsyncMock()

        def pubsub(self) -> FakePubSub:
            return self.stream

    instances = [FakeRedis(True), FakeRedis(False)]
    with patch("core.live_feed.aioredis.from_url", side_effect=instances) as from_url:
        subscription = _RedisFeedSubscription("redis://test", "tenant-1", handler)
        try:
            await subscription.start()
            await asyncio.wait_for(delivered.wait(), timeout=1.0)
        finally:
            await subscription.close()

    assert from_url.call_count == 2
    assert calls == [{"sequence": 1}]
    assert instances[0].aclose.await_count == 1
    assert instances[1].aclose.await_count == 1


@pytest.mark.asyncio
async def test_redis_feed_start_releases_connection_after_subscribe_failure() -> None:
    redis = MagicMock()
    redis.aclose = AsyncMock()
    pubsub = AsyncMock()
    pubsub.subscribe.side_effect = ConnectionError("unavailable")
    redis.pubsub.return_value = pubsub

    with patch("core.live_feed.aioredis.from_url", return_value=redis):
        subscription = _RedisFeedSubscription("redis://test", "tenant-1", AsyncMock())
        with pytest.raises(ConnectionError):
            await subscription.start()

    pubsub.close.assert_awaited_once()
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_redis_cleanup_can_be_retried() -> None:
    started = asyncio.Event()
    attempts = 0

    async def unsubscribe(_channel: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            await asyncio.Event().wait()

    pubsub = MagicMock()
    pubsub.unsubscribe = AsyncMock(side_effect=unsubscribe)
    pubsub.close = AsyncMock()
    redis = MagicMock()
    redis.aclose = AsyncMock()
    subscription = _RedisFeedSubscription("redis://test", "tenant-1", AsyncMock())
    subscription._pubsub = pubsub
    subscription._redis = redis

    cleanup = asyncio.create_task(subscription._close_current())
    await started.wait()
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup
    assert subscription._pubsub is pubsub
    await subscription.close()

    assert attempts == 2
    pubsub.close.assert_awaited_once()
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscription_can_close_from_its_own_handler() -> None:
    pubsub = MagicMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.close = AsyncMock()
    redis = MagicMock()
    redis.aclose = AsyncMock()
    subscription = _RedisFeedSubscription("redis://test", "tenant-1", AsyncMock())
    subscription._task = asyncio.current_task()
    subscription._pubsub = pubsub
    subscription._redis = redis

    await subscription.close()

    assert subscription._closed is True
    pubsub.close.assert_awaited_once()
    redis.aclose.assert_awaited_once()
