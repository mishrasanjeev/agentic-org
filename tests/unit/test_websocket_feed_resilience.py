from __future__ import annotations

import uuid
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.deps import get_current_tenant
from api.websocket import feed
from core.live_feed import (
    InMemoryFeedEventBroker,
    InMemoryFeedEventRepository,
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
    try:
        yield repository, broker
    finally:
        feed._connections.clear()
        feed._subscriptions.clear()
        reset_live_feed_for_tests()


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


def test_authenticated_matching_tenant_connects(feed_runtime) -> None:
    tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": tenant_id, "grantex:scopes": []}
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}?token=valid") as websocket:
            message = websocket.receive_json()

    assert message == {"type": "heartbeat", "tenant_id": tenant_id, "sequence": None}


def test_authenticated_tenant_mismatch_is_rejected(feed_runtime) -> None:
    path_tenant_id = str(uuid.uuid4())
    token_tenant_id = str(uuid.uuid4())
    claims = {"sub": "user-1", "agenticorg:tenant_id": token_tenant_id, "grantex:scopes": []}
    client = TestClient(_test_app())

    with patch("api.websocket.feed.validate_token", new_callable=AsyncMock, return_value=claims):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/v1/ws/feed/{path_tenant_id}?token=valid"):
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
        with client.websocket_connect(f"/api/v1/ws/feed/{tenant_id}?token=valid") as websocket:
            assert websocket.receive_json()["type"] == "heartbeat"
            websocket.send_text("{bad-json")
            assert websocket.receive_json()["code"] == "invalid_json"
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json()["type"] == "pong"
