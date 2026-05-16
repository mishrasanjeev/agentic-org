"""Regression tests for durable bridge session/request state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bridge.state import (
    BRIDGE_HEARTBEAT_STALE_SECONDS,
    BridgeRouteError,
    InMemoryBridgeBroker,
    InMemoryBridgeStateRepository,
    configure_bridge_state_for_tests,
    reset_bridge_state_for_tests,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def bridge_state() -> tuple[InMemoryBridgeStateRepository, InMemoryBridgeBroker]:
    from bridge import server_handler

    repo = InMemoryBridgeStateRepository()
    broker = InMemoryBridgeBroker()
    configure_bridge_state_for_tests(repository=repo, broker=broker)
    server_handler._active_bridges.clear()
    server_handler._pending_requests.clear()
    try:
        yield repo, broker
    finally:
        server_handler._active_bridges.clear()
        server_handler._pending_requests.clear()
        reset_bridge_state_for_tests()


@pytest.mark.asyncio
async def test_bridge_heartbeat_persists_status_after_local_registry_clear(bridge_state) -> None:
    from bridge import server_handler
    from bridge.server_handler import get_bridge_status, handle_bridge_heartbeat

    repo, _broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-1",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=False,
        owner="pod-a",
        process_id=1,
    )

    heartbeat = await handle_bridge_heartbeat(
        bridge_id="bridge-1",
        tenant_id=TENANT_A,
        tally_healthy=True,
    )
    server_handler._active_bridges.clear()

    status = await get_bridge_status("bridge-1", tenant_id=TENANT_A)
    assert heartbeat.tally_healthy is True
    assert status["connected"] is True
    assert status["local_connected"] is False
    assert status["tally_healthy"] is True
    assert status["last_heartbeat"] is not None


@pytest.mark.asyncio
async def test_route_to_bridge_persists_pending_request_before_publish(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge

    repo, broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-1",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )

    saw_persisted_before_publish = False

    async def respond_after_persist(message: dict) -> None:
        nonlocal saw_persisted_before_publish
        record = await repo.get_request(message["request_id"])
        saw_persisted_before_publish = record is not None and record.status == "pending"
        result = {
            "type": "response",
            "request_id": message["request_id"],
            "status": "ok",
            "xml_response": "<ENVELOPE/>",
        }
        await repo.mark_responded(
            request_id=message["request_id"],
            result=result,
            response_metadata={"source": "test"},
        )
        await broker.publish_response(message["request_id"], result)

    await broker.subscribe_requests("bridge-1", respond_after_persist)

    result = await route_to_bridge(
        "bridge-1",
        "<ENVELOPE/>",
        timeout=1,
        tenant_id=TENANT_A,
        connector_type="tally",
        request_id="req-route",
    )

    record = await repo.get_request("req-route")
    assert saw_persisted_before_publish is True
    assert result["status"] == "ok"
    assert record is not None
    assert record.status == "responded"
    assert record.payload_hash


@pytest.mark.asyncio
async def test_timeout_marks_durable_request_timed_out(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge

    repo, _broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-timeout",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )

    with pytest.raises(BridgeRouteError) as exc:
        await route_to_bridge(
            "bridge-timeout",
            "<ENVELOPE/>",
            timeout=0.01,
            tenant_id=TENANT_A,
            request_id="req-timeout",
        )

    record = await repo.get_request("req-timeout")
    assert exc.value.code == "request_timed_out"
    assert record is not None
    assert record.status == "timed_out"
    assert record.error == {
        "code": "request_timed_out",
        "message": "Bridge request timed out",
    }


@pytest.mark.asyncio
async def test_stale_heartbeat_status_is_unhealthy_and_route_fails(bridge_state) -> None:
    from bridge.server_handler import get_bridge_status, route_to_bridge

    repo, _broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-stale",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )
    repo.sessions["bridge-stale"].last_heartbeat = datetime.now(UTC) - timedelta(
        seconds=BRIDGE_HEARTBEAT_STALE_SECONDS + 1,
    )

    status = await get_bridge_status("bridge-stale", tenant_id=TENANT_A)
    with pytest.raises(BridgeRouteError) as exc:
        await route_to_bridge(
            "bridge-stale",
            "<ENVELOPE/>",
            timeout=1,
            tenant_id=TENANT_A,
            request_id="req-stale",
        )

    record = await repo.get_request("req-stale")
    assert status["status"] == "unhealthy"
    assert status["connected"] is False
    assert exc.value.code == "bridge_disconnected"
    assert record is not None
    assert record.status == "failed"
    assert record.error == {
        "code": "bridge_disconnected",
        "message": "Bridge bridge-stale is disconnected",
    }


@pytest.mark.asyncio
async def test_broker_unavailable_marks_durable_request_failed(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge

    repo, _broker = bridge_state

    class FailingBroker(InMemoryBridgeBroker):
        async def subscribe_response(self, request_id: str, handler):
            raise RuntimeError("redis unavailable")

    configure_bridge_state_for_tests(repository=repo, broker=FailingBroker())
    await repo.connect_session(
        bridge_id="bridge-broker-down",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )

    with pytest.raises(BridgeRouteError) as exc:
        await route_to_bridge(
            "bridge-broker-down",
            "<ENVELOPE/>",
            timeout=1,
            tenant_id=TENANT_A,
            request_id="req-broker-down",
        )

    record = await repo.get_request("req-broker-down")
    assert exc.value.code == "bridge_broker_unavailable"
    assert record is not None
    assert record.status == "failed"
    assert record.error == {
        "code": "bridge_broker_unavailable",
        "message": "redis unavailable",
    }


@pytest.mark.asyncio
async def test_disconnect_marks_outstanding_requests_orphaned(bridge_state) -> None:
    from bridge.server_handler import handle_bridge_disconnect

    repo, _broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-disconnect",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )
    await repo.create_request(
        request_id="req-orphan",
        bridge_id="bridge-disconnect",
        tenant_id=TENANT_A,
        connector_type="tally",
        method="post_xml",
        payload_hash_value="abc123",
        timeout_seconds=30,
    )
    await repo.mark_sent("req-orphan")

    await handle_bridge_disconnect(
        bridge_id="bridge-disconnect",
        tenant_id=TENANT_A,
        reason="socket closed",
    )

    session = await repo.get_session(bridge_id="bridge-disconnect", tenant_id=TENANT_A)
    record = await repo.get_request("req-orphan")
    assert session is not None
    assert session.status == "disconnected"
    assert record is not None
    assert record.status == "orphaned"
    assert record.error == {"code": "bridge_disconnected", "message": "socket closed"}


@pytest.mark.asyncio
async def test_duplicate_bridge_response_does_not_double_complete_request(bridge_state) -> None:
    from bridge.server_handler import handle_bridge_response

    repo, broker = bridge_state
    await repo.create_request(
        request_id="req-dup",
        bridge_id="bridge-dup",
        tenant_id=TENANT_A,
        connector_type="tally",
        method="post_xml",
        payload_hash_value="abc123",
        timeout_seconds=30,
    )
    await repo.mark_sent("req-dup")

    response = {"type": "response", "request_id": "req-dup", "status": "ok", "xml_response": "<ENVELOPE/>"}
    await handle_bridge_response(response)
    await handle_bridge_response(response)

    record = await repo.get_request("req-dup")
    assert record is not None
    assert record.status == "responded"
    assert record.response_metadata["duplicate_response_count"] == 1
    assert len(broker.published_responses) == 1


@pytest.mark.asyncio
async def test_malformed_bridge_response_is_recorded_failed(bridge_state) -> None:
    from bridge.server_handler import handle_bridge_response

    repo, _broker = bridge_state
    await repo.create_request(
        request_id="req-bad",
        bridge_id="bridge-bad",
        tenant_id=TENANT_A,
        connector_type="tally",
        method="post_xml",
        payload_hash_value="abc123",
        timeout_seconds=30,
    )
    await repo.mark_sent("req-bad")

    await handle_bridge_response({"type": "response", "request_id": "req-bad", "status": "ok"})

    record = await repo.get_request("req-bad")
    assert record is not None
    assert record.status == "failed"
    assert record.error == {
        "code": "malformed_response",
        "message": "Bridge returned a malformed response",
    }


@pytest.mark.asyncio
async def test_reconnect_updates_same_session(bridge_state) -> None:
    repo, _broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-reconnect",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=False,
        owner="pod-a",
        process_id=1,
    )
    await repo.disconnect_session(
        bridge_id="bridge-reconnect",
        tenant_id=TENANT_A,
        reason="socket closed",
    )

    session = await repo.connect_session(
        bridge_id="bridge-reconnect",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-b",
        process_id=2,
    )

    assert len(repo.sessions) == 1
    assert session.status == "connected"
    assert session.reconnect_count == 1
    assert session.connection_owner == "pod-b"


@pytest.mark.asyncio
async def test_tenant_mismatch_cannot_route_other_tenant_bridge(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge

    repo, _broker = bridge_state
    await repo.connect_session(
        bridge_id="bridge-tenant",
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )

    with pytest.raises(BridgeRouteError) as exc:
        await route_to_bridge(
            "bridge-tenant",
            "<ENVELOPE/>",
            timeout=0.01,
            tenant_id=TENANT_B,
            request_id="req-cross-tenant",
        )

    assert exc.value.code == "tenant_mismatch"
    assert await repo.get_request("req-cross-tenant") is None
