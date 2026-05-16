from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.cdc_webhooks import router as cdc_router
from core.cdc.receiver import (
    InMemoryCDCEventRepository,
    InMemoryCDCEventStore,
    clear_store,
    handle_cdc_webhook,
    list_stored_events,
    replay_cdc_event,
    set_cdc_event_store_for_tests,
)


def _sign(payload: dict, secret: str = "test-secret") -> str:  # noqa: S107
    return hmac.new(
        secret.encode(),
        json.dumps(payload, sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.fixture
def cdc_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryCDCEventStore:
    monkeypatch.setenv("CDC_WEBHOOK_SECRET_XERO", "test-secret")
    monkeypatch.setenv("CDC_WEBHOOK_SECRET_TESTCONN", "test-secret")
    store = InMemoryCDCEventStore()
    set_cdc_event_store_for_tests(store)
    yield store
    clear_store()
    set_cdc_event_store_for_tests(None)


@pytest.mark.asyncio
async def test_cdc_event_persists_to_durable_store(cdc_store: InMemoryCDCEventStore) -> None:
    payload = {
        "event_id": "evt-1",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-1",
    }

    result = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload))

    assert result["status"] == "accepted"
    events, total = await list_stored_events(tenant_id="tenant-a", store=cdc_store)
    assert total == 1
    assert events[0]["provider_event_id"] == "evt-1"
    assert events[0]["processing_status"] == "processed"
    assert events[0]["signature_verification_status"] == "valid"


@pytest.mark.asyncio
async def test_duplicate_delivery_dedupes_across_separate_store_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDC_WEBHOOK_SECRET_XERO", "test-secret")
    repo = InMemoryCDCEventRepository()
    pod_a = InMemoryCDCEventStore(repo)
    pod_b = InMemoryCDCEventStore(repo)
    payload = {
        "event_id": "evt-same",
        "event_type": "invoice.updated",
        "resource_type": "invoice",
        "resource_id": "inv-2",
    }

    first = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload), store=pod_a)
    second = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload), store=pod_b)

    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert len(repo.records) == 1


@pytest.mark.asyncio
async def test_invalid_signature_rejects_without_accepted_event(
    cdc_store: InMemoryCDCEventStore,
) -> None:
    payload = {
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-bad",
    }

    result = await handle_cdc_webhook("tenant-a", "xero", payload, "bad-signature")

    assert result == {"status": "rejected", "reason": "invalid_signature", "http_status": 403}
    events, total = await list_stored_events(tenant_id="tenant-a", store=cdc_store)
    assert events == []
    assert total == 0


@pytest.mark.asyncio
async def test_missing_tenant_returns_400(cdc_store: InMemoryCDCEventStore) -> None:
    payload = {
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-3",
    }

    result = await handle_cdc_webhook("", "xero", payload, _sign(payload))

    assert result["status"] == "rejected"
    assert result["reason"] == "missing_tenant"
    assert result["http_status"] == 400


@pytest.mark.asyncio
async def test_get_cdc_events_reads_durable_tenant_scoped_store(
    cdc_store: InMemoryCDCEventStore,
) -> None:
    payload_a = {
        "event_id": "evt-a",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-a",
    }
    payload_b = {
        "event_id": "evt-b",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-b",
    }
    await handle_cdc_webhook("tenant-a", "xero", payload_a, _sign(payload_a), store=cdc_store)
    await handle_cdc_webhook("tenant-b", "xero", payload_b, _sign(payload_b), store=cdc_store)

    events, total = await list_stored_events(tenant_id="tenant-a", store=cdc_store)

    assert total == 1
    assert events[0]["tenant_id"] == "tenant-a"
    assert events[0]["resource_id"] == "inv-a"


@pytest.mark.asyncio
async def test_cross_tenant_duplicate_fingerprints_are_allowed(
    cdc_store: InMemoryCDCEventStore,
) -> None:
    payload = {
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-cross",
    }

    first = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload), store=cdc_store)
    second = await handle_cdc_webhook("tenant-b", "xero", payload, _sign(payload), store=cdc_store)

    assert first["status"] == "accepted"
    assert second["status"] == "accepted"
    events_a, total_a = await list_stored_events(tenant_id="tenant-a", store=cdc_store)
    events_b, total_b = await list_stored_events(tenant_id="tenant-b", store=cdc_store)
    assert total_a == 1
    assert total_b == 1
    assert events_a[0]["fingerprint"] == events_b[0]["fingerprint"]


@pytest.mark.asyncio
async def test_trigger_evaluation_failure_records_dead_letter(
    cdc_store: InMemoryCDCEventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(event: dict, tenant_id: str) -> list[str]:
        raise RuntimeError("trigger db unavailable")

    monkeypatch.setattr("core.cdc.triggers.evaluate_triggers", _boom)
    payload = {
        "event_id": "evt-fail",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-fail",
    }

    result = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload), store=cdc_store)

    assert result["status"] == "accepted"
    assert result["processing_status"] == "dead_lettered"
    events, _total = await list_stored_events(tenant_id="tenant-a", store=cdc_store)
    assert events[0]["processing_status"] == "dead_lettered"
    assert events[0]["processing_outcome"]["error_code"] == "trigger_evaluation_failed"
    assert cdc_store.repository.dead_letters[0]["failure_stage"] == "trigger_evaluation"


@pytest.mark.asyncio
async def test_replay_path_is_tenant_scoped_and_idempotent(
    cdc_store: InMemoryCDCEventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(event: dict, tenant_id: str) -> list[str]:
        raise RuntimeError("first pass failed")

    monkeypatch.setattr("core.cdc.triggers.evaluate_triggers", _boom)
    payload = {
        "event_id": "evt-replay",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-replay",
    }
    accepted = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload), store=cdc_store)
    event_id = accepted["event_id"]

    wrong_tenant = await replay_cdc_event(
        tenant_id="tenant-b",
        event_id=event_id,
        actor="admin-a",
        store=cdc_store,
    )
    assert wrong_tenant["status"] == "not_found"

    monkeypatch.setattr("core.cdc.triggers.evaluate_triggers", lambda event, tenant_id: ["wf-1"])
    replayed = await replay_cdc_event(
        tenant_id="tenant-a",
        event_id=event_id,
        actor="admin-a",
        store=cdc_store,
    )
    duplicate = await replay_cdc_event(
        tenant_id="tenant-a",
        event_id=event_id,
        actor="admin-a",
        store=cdc_store,
    )

    assert replayed["status"] == "replayed"
    assert replayed["matched_workflows"] == ["wf-1"]
    assert duplicate["status"] == "duplicate"
    events, _total = await list_stored_events(tenant_id="tenant-a", store=cdc_store)
    assert events[0]["replay_status"] == "replayed"
    assert events[0]["replay_attempts"] == 1


@pytest.mark.asyncio
async def test_failed_replay_is_idempotent(
    cdc_store: InMemoryCDCEventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(event: dict, tenant_id: str) -> list[str]:
        raise RuntimeError("still failing")

    monkeypatch.setattr("core.cdc.triggers.evaluate_triggers", _boom)
    payload = {
        "event_id": "evt-replay-fail",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-replay-fail",
    }
    accepted = await handle_cdc_webhook("tenant-a", "xero", payload, _sign(payload), store=cdc_store)
    event_id = accepted["event_id"]

    first = await replay_cdc_event(
        tenant_id="tenant-a",
        event_id=event_id,
        actor="admin-a",
        store=cdc_store,
    )
    second = await replay_cdc_event(
        tenant_id="tenant-a",
        event_id=event_id,
        actor="admin-a",
        store=cdc_store,
    )

    assert first["status"] == "failed"
    assert second["status"] == "duplicate"
    assert len(cdc_store.repository.dead_letters) == 2


def test_cdc_webhook_api_invalid_signature_returns_403(
    cdc_store: InMemoryCDCEventStore,
) -> None:
    app = FastAPI()
    app.include_router(cdc_router)
    client = TestClient(app)
    payload = {
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-http",
    }

    response = client.post(
        "/webhooks/cdc/tenant-a/xero",
        json=payload,
        headers={"X-CDC-Signature": "bad-signature"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "invalid_signature"


def test_cdc_webhook_api_duplicate_returns_200(cdc_store: InMemoryCDCEventStore) -> None:
    app = FastAPI()
    app.include_router(cdc_router)
    client = TestClient(app)
    payload = {
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-http-dup",
    }
    signature = _sign(payload)

    first = client.post(
        "/webhooks/cdc/tenant-a/xero",
        json=payload,
        headers={"X-CDC-Signature": signature},
    )
    second = client.post(
        "/webhooks/cdc/tenant-a/xero",
        json=payload,
        headers={"X-CDC-Signature": signature},
    )

    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


def test_cdc_webhook_api_accepts_raw_body_signature(cdc_store: InMemoryCDCEventStore) -> None:
    app = FastAPI()
    app.include_router(cdc_router)
    client = TestClient(app)
    raw_body = b'{"resource_id":"inv-raw","resource_type":"invoice","event_type":"invoice.created"}'
    signature = hmac.new(b"test-secret", raw_body, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/cdc/tenant-a/xero",
        content=raw_body,
        headers={"X-CDC-Signature": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_cdc_webhook_api_invalid_json_returns_400(cdc_store: InMemoryCDCEventStore) -> None:
    app = FastAPI()
    app.include_router(cdc_router)
    client = TestClient(app)

    response = client.post(
        "/webhooks/cdc/tenant-a/xero",
        content=b"not-json",
        headers={"X-CDC-Signature": "irrelevant"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "invalid_json"


def test_cdc_webhook_api_invalid_shape_returns_422(cdc_store: InMemoryCDCEventStore) -> None:
    app = FastAPI()
    app.include_router(cdc_router)
    client = TestClient(app)
    payload = {"event_type": "invoice.created"}

    response = client.post(
        "/webhooks/cdc/tenant-a/xero",
        json=payload,
        headers={"X-CDC-Signature": _sign(payload)},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "invalid_payload"
