# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""UCP checkout, cart and order on a real Postgres: state machine, arbiter decision, ledger, idempotency, webhooks.

Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors
from core.commerce.ucp import handlers, webhooks
from core.commerce.ucp.httpsig import verify_request_signature
from tests import ucp_txn_fixtures as uf


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.fixture()
async def db(monkeypatch):
    from core import database

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
def executor():
    fake = uf.FakeExecutor()
    executors.register_executor(fake)
    try:
        yield fake
    finally:
        executors.unregister_executor(fake.name)


async def _rows(engine, sql: str, **params) -> list[dict]:
    """Read evidence rows. Under the NOBYPASSRLS app role (``AGENTICORG_DB_URL``), assertions read through
    the owner DSN in ``AGENTICORG_TEST_OWNER_DB_URL`` when it is set, so the observer is not tenant-scoped."""
    owner_url = os.getenv("AGENTICORG_TEST_OWNER_DB_URL")
    observer = create_async_engine(owner_url, poolclass=NullPool) if owner_url else engine
    try:
        async with observer.connect() as conn:
            return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]
    finally:
        if owner_url:
            await observer.dispose()


async def _ledger_events(engine, tenant: str) -> list[str]:
    rows = await _rows(
        engine,
        "SELECT event_type FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        t=tenant,
    )
    return [r["event_type"] for r in rows]


async def _ready_session(runtime, tenant: str) -> dict:
    created = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=uf.SHIPPING))
    )
    assert created.status == 201, created.body
    assert created.body["status"] == "ready_for_complete", created.body.get("messages")
    return created.body


@pytest.mark.asyncio
async def test_create_complete_seals_decision_creates_order_and_queues_signed_webhook(db, executor):
    tenant = str(uuid.uuid4())
    kicks: list[uuid.UUID] = []
    signing = uf.signer()
    runtime = uf.runtime(tenant, kicks=kicks, signing=signing)
    session = await _ready_session(runtime, tenant)
    assert [t["type"] for t in session["totals"]] == ["subtotal", "fulfillment", "total"]
    assert session["totals"][-1]["amount"] == 2500 + 5000

    complete = uf.complete_body()
    done = await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, complete, key="complete-key-1"), session["id"]
    )
    assert done.status == 200, done.body
    assert done.body["status"] == "completed"
    order_id = done.body["order"]["id"]
    assert done.body["order"]["permalink_url"].startswith("https://")
    assert "credential" not in str(done.body["payment"])
    assert len(executor.calls) == 1
    checkout, decision = executor.calls[0]
    assert checkout.instruments[0].credential["token"] == "tok_example_1"
    assert checkout.token_binding == {"checkout_id": session["id"]}

    decision_row = (
        await _rows(db, "SELECT * FROM commerce_arbiter_decisions WHERE decision_id = :d", d=decision.decision_id)
    )[0]
    assert decision_row["action"] == "session_complete"
    assert decision_row["outcome"] == "allow" and decision_row["consumed_at"] is not None
    assert decision_row["payload_hash"] == decision.payload_hash

    events = await _ledger_events(db, tenant)
    for expected in (
        "commerce.ucp.checkout.create",
        "commerce.arbiter.decision",
        "commerce.arbiter.decision.consumed",
        "commerce.ucp.checkout.complete",
        "commerce.ucp.order.created",
        "commerce.ucp.checkout.execution",
    ):
        assert expected in events, (expected, events)

    order = await handlers.run("get_order", runtime, uf.request(tenant), order_id)
    assert order.status == 200
    assert order.body["checkout_id"] == session["id"]
    assert order.body["line_items"][0]["quantity"] == {"original": 1, "total": 1, "fulfilled": 0}
    assert order.body["line_items"][0]["status"] == "processing"

    deliveries = await _rows(db, "SELECT * FROM commerce_order_webhook_deliveries WHERE tenant_id = :t", t=tenant)
    assert len(deliveries) == 1 and deliveries[0]["status"] == "pending"
    assert kicks == [uuid.UUID(tenant)]

    received: list[httpx.Request] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        received.append(req)
        return httpx.Response(200, json={"ucp": {"version": "2026-04-08"}})

    dispatcher = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.MockTransport(_handler), signer=lambda: signing
    )
    report = await dispatcher.dispatch_tenant(uuid.UUID(tenant))
    assert report.delivered == 1
    sent = received[0]
    assert str(sent.url) == uf.WEBHOOK_URL
    assert sent.headers["webhook-id"] == str(deliveries[0]["webhook_id"])
    assert sent.headers["ucp-agent"] == f'profile="https://{uf.HOST}/.well-known/ucp"'
    assert verify_request_signature(
        method="POST",
        url=str(sent.url),
        headers=dict(sent.headers),
        body=sent.content,
        public_key=signing._key.public_key(),
    )
    assert '"ucp-agent"' in sent.headers["signature-input"]

    # Idempotent replay of complete returns the stored answer without executing again.
    replay = await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, complete, key="complete-key-1"), session["id"]
    )
    assert replay.status == 200 and replay.body["order"]["id"] == order_id
    assert len(executor.calls) == 1
    again = await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, complete, key="complete-key-2"), session["id"]
    )
    assert again.status == 409


@pytest.mark.asyncio
async def test_idempotency_same_key_different_body_is_409_and_same_body_replays(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    first = await handlers.run("create_checkout", runtime, uf.request(tenant, uf.create_body(), key="k-create"))
    replay = await handlers.run("create_checkout", runtime, uf.request(tenant, uf.create_body(), key="k-create"))
    assert first.status == replay.status == 201
    assert replay.body["id"] == first.body["id"]
    conflict = await handlers.run("create_checkout", runtime, uf.request(tenant, uf.create_body(2), key="k-create"))
    assert conflict.status == 409
    assert conflict.body["messages"][0]["code"] == "idempotency_key_reused"
    rows = await _rows(db, "SELECT * FROM commerce_ucp_idempotency WHERE tenant_id = :t", t=tenant)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "complete" and len(row["body_sha256"]) == 64
    assert (row["expires_at"] - row["created_at"]).total_seconds() >= 24 * 3600


@pytest.mark.asyncio
async def test_no_executor_keeps_ready_for_complete_and_consumes_nothing(db):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    result = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), session["id"])
    assert result.status == 200
    assert result.body["status"] == "ready_for_complete"
    assert any(m["code"] == "executor_unavailable" for m in result.body["messages"])
    assert not await _rows(db, "SELECT * FROM commerce_arbiter_decisions WHERE tenant_id = :t", t=tenant)


@pytest.mark.asyncio
async def test_arbiter_refusal_is_sealed_and_session_stays_ready(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    body = uf.complete_body()
    body["ai.agenticorg.commerce"]["buyer_authority"].pop("mandate_ref")
    body["ai.agenticorg.commerce"]["buyer_authority"].pop("mandate_hash")
    result = await handlers.run("complete_checkout", runtime, uf.request(tenant, body), session["id"])
    assert result.body["status"] == "ready_for_complete"
    assert result.body["ai.agenticorg.commerce"]["decision"]["failing_rule"] == "mandate_present_unexpired"
    rows = await _rows(db, "SELECT outcome, consumed_at FROM commerce_arbiter_decisions WHERE tenant_id = :t", t=tenant)
    assert rows == [{"outcome": "refuse", "consumed_at": None}]
    assert executor.calls == []


@pytest.mark.asyncio
async def test_two_step_decide_then_complete_with_decision_id_and_reuse_refused(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    decided = await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, uf.complete_body(decide_only=True)), session["id"]
    )
    decision = decided.body["ai.agenticorg.commerce"]["decision"]
    assert decision["outcome"] == "allow" and decided.body["status"] == "ready_for_complete"
    done = await handlers.run(
        "complete_checkout",
        runtime,
        uf.request(tenant, uf.complete_body(decision_id=decision["decision_id"])),
        session["id"],
    )
    assert done.body["status"] == "completed"
    assert executor.calls[0][1].decision_id == decision["decision_id"]


@pytest.mark.asyncio
async def test_decision_for_another_credential_is_refused(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    decided = await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, uf.complete_body(decide_only=True)), session["id"]
    )
    decision_id = decided.body["ai.agenticorg.commerce"]["decision"]["decision_id"]
    other = uf.complete_body(decision_id=decision_id, payment=uf.token_payment(token="tok_other"))
    result = await handlers.run("complete_checkout", runtime, uf.request(tenant, other), session["id"])
    assert result.body["status"] == "ready_for_complete"
    assert any(m["code"] == "decision_payload_mismatch" for m in result.body["messages"])
    assert executor.calls == []


@pytest.mark.asyncio
async def test_cart_create_update_and_single_call_conversion(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    cart = await handlers.run("create_cart", runtime, uf.request(tenant, uf.create_body(2)))
    assert cart.status == 201
    assert cart.body["continue_url"] == "https://merchant.example/buy/TOTE-1:2"
    updated = await handlers.run("update_cart", runtime, uf.request(tenant, uf.create_body(3)), cart.body["id"])
    assert updated.status == 200 and updated.body["line_items"][0]["quantity"] == 3
    first = await handlers.run(
        "create_checkout",
        runtime,
        uf.request(tenant, {"cart_id": cart.body["id"], "line_items": []}),
    )
    assert first.status == 201 and first.body["line_items"][0]["quantity"] == 3
    second = await handlers.run(
        "create_checkout",
        runtime,
        uf.request(tenant, {"cart_id": cart.body["id"], "line_items": []}),
    )
    assert second.status == 200 and second.body["id"] == first.body["id"]
    canceled = await handlers.run("cancel_cart", runtime, uf.request(tenant, {}), cart.body["id"])
    assert canceled.status == 200
    gone = await handlers.run("get_cart", runtime, uf.request(tenant), cart.body["id"])
    assert gone.status == 404


@pytest.mark.asyncio
async def test_order_event_updates_snapshot_and_queues_update_webhook(db, executor, monkeypatch):
    from api.v1 import commerce_ucp as api

    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    done = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), session["id"])
    order_id = done.body["order"]["id"]
    monkeypatch.setattr(api, "kick_dispatch", lambda _tenant: None)
    line_id = done.body["line_items"][0]["id"]
    result = await api.record_order_event(
        order_id,
        api.OrderEventRequest(
            fulfillment_event={
                "type": "shipped",
                "line_items": [{"id": line_id, "quantity": 1}],
                "tracking_number": "TRK1",
            }
        ),
        tenant_id=tenant,
    )
    assert result["revision"] == 2
    assert result["order"]["line_items"][0]["status"] == "fulfilled"
    rows = await _rows(
        db,
        "SELECT event_type, order_revision FROM commerce_order_webhook_deliveries WHERE tenant_id = :t "
        "ORDER BY order_revision",
        t=tenant,
    )
    assert [(r["event_type"], r["order_revision"]) for r in rows] == [("order.created", 1), ("order.updated", 2)]


@pytest.mark.parametrize("version", ["2026-04-08", "2026-08-25"])
@pytest.mark.asyncio
async def test_every_handler_response_validates_against_the_published_schema(db, executor, version):
    from tests import ucp_schema

    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant, profile=uf.platform_profile(version))
    body = uf.create_body(
        fulfillment=uf.SHIPPING,
        buyer={"email": "buyer@example.com"},
        context={"eligibility": ["com.merchant.rewards"]},
        attribution={"campaign_id": "x"},
    )
    created = await handlers.run("create_checkout", runtime, uf.request(tenant, body))
    ucp_schema.assert_valid_checkout(version, created.body)
    got = await handlers.run("get_checkout", runtime, uf.request(tenant), created.body["id"])
    ucp_schema.assert_valid_checkout(version, got.body)
    updated = await handlers.run("update_checkout", runtime, uf.request(tenant, body), created.body["id"])
    ucp_schema.assert_valid_checkout(version, updated.body)
    done = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), created.body["id"])
    assert done.body["status"] == "completed", done.body.get("messages")
    ucp_schema.assert_valid_checkout(version, done.body)
    order = await handlers.run("get_order", runtime, uf.request(tenant), done.body["order"]["id"])
    ucp_schema.assert_valid(version, "shopping/order.json", order.body)
    cart = await handlers.run("create_cart", runtime, uf.request(tenant, uf.create_body()))
    ucp_schema.assert_valid_cart(version, cart.body)
    other = await handlers.run("create_checkout", runtime, uf.request(tenant, uf.create_body()))
    canceled = await handlers.run("cancel_checkout", runtime, uf.request(tenant, {}), other.body["id"])
    ucp_schema.assert_valid_checkout(version, canceled.body)
    assert canceled.body["status"] == "canceled"
    error_schema = (
        "shopping/types/error_response.json" if version == "2026-04-08" else "common/types/error_response.json"
    )
    for frozen_op in ("update_checkout", "complete_checkout", "cancel_checkout"):
        refused = await handlers.run(frozen_op, runtime, uf.request(tenant, body), other.body["id"])
        assert refused.status == 409
        ucp_schema.assert_valid(version, error_schema, refused.body)


@pytest.mark.asyncio
async def test_payment_authentication_action_keeps_complete_in_progress_2026_08_25(db):
    tenant = str(uuid.uuid4())
    action = {
        "dev.ucp.common.payment.three_ds_challenge": [
            {"id": "3ds-1", "config": {"payment_instrument_id": "instr_1", "url": "https://payments.example/3ds/1"}}
        ],
        "dev.ucp.common.payment.device_data_collection": [
            {"id": "ddc-1", "config": {"payment_instrument_id": "instr_1", "url": "https://evil.example/ddc"}}
        ],
    }
    fake = uf.FakeExecutor(executors.ExecutionResult(status="action_required", actions=action))
    executors.register_executor(fake)
    try:
        runtime = uf.runtime(tenant, profile=uf.platform_profile("2026-08-25"))
        session = await _ready_session(runtime, tenant)
        result = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), session["id"])
    finally:
        executors.unregister_executor(fake.name)
    assert result.body["status"] == "complete_in_progress"
    assert "order" not in result.body
    # Only the action on an allowed origin survives.
    assert list(result.body["actions"]) == ["dev.ucp.common.payment.three_ds_challenge"]
    frozen = await handlers.run("update_checkout", runtime, uf.request(tenant, uf.create_body()), session["id"])
    assert frozen.status == 409


@pytest.mark.asyncio
async def test_out_of_band_step_is_an_escalation_with_continue_url_2026_04_08(db):
    tenant = str(uuid.uuid4())
    fake = uf.FakeExecutor(
        executors.ExecutionResult(status="action_required", escalation_url="https://payments.example/3ds/session-1")
    )
    executors.register_executor(fake)
    try:
        runtime = uf.runtime(tenant)
        session = await _ready_session(runtime, tenant)
        result = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), session["id"])
    finally:
        executors.unregister_executor(fake.name)
    assert result.body["status"] == "requires_escalation"
    assert result.body["continue_url"] == "https://payments.example/3ds/session-1"
    assert any(
        m.get("severity") == "requires_buyer_input" and m["code"] == "requires_3ds" for m in result.body["messages"]
    )


@pytest.mark.asyncio
async def test_failed_execution_returns_session_to_ready(db):
    tenant = str(uuid.uuid4())
    fake = uf.FakeExecutor(
        executors.ExecutionResult(status="failed", error_code="payment_failed", error_message="Declined")
    )
    executors.register_executor(fake)
    try:
        runtime = uf.runtime(tenant)
        session = await _ready_session(runtime, tenant)
        result = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), session["id"])
    finally:
        executors.unregister_executor(fake.name)
    assert result.body["status"] == "ready_for_complete"
    assert any(m["code"] == "payment_failed" for m in result.body["messages"])
    events = await _ledger_events(db, tenant)
    assert "commerce.ucp.checkout.execution" in events and "commerce.ucp.order.created" not in events


@pytest.mark.asyncio
async def test_two_concurrent_completes_execute_once(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    body = uf.complete_body()
    results = await asyncio.gather(
        handlers.run("complete_checkout", runtime, uf.request(tenant, body), session["id"]),
        handlers.run("complete_checkout", runtime, uf.request(tenant, body), session["id"]),
    )
    assert len(executor.calls) == 1
    assert sorted(r.status for r in results) == [200, 409]


@pytest.mark.asyncio
async def test_split_payments_and_terms_2026_08_25(db, executor):
    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant, profile=uf.platform_profile("2026-08-25"))
    body = uf.create_body(fulfillment=uf.SHIPPING, payment={"selected_term_id": "pay_in_two", "instruments": []})
    created = await handlers.run("create_checkout", runtime, uf.request(tenant, body))
    assert created.body["payment"]["selected_term_id"] == "pay_in_two"
    assert created.body["payment"]["terms"][0]["schedules"][0]["amount"] == 3750
    split = uf.token_payment()
    second = dict(split["instruments"][0], id="instr_2")
    split["instruments"][0]["amount"] = 5000
    second["amount"] = 2500
    split["instruments"].append(second)
    done = await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, uf.complete_body(payment=split)), created.body["id"]
    )
    assert done.body["status"] == "completed", done.body.get("messages")
    order = await handlers.run("get_order", runtime, uf.request(tenant), done.body["order"]["id"])
    assert order.body["payment"]["accepted_term"]["id"] == "pay_in_two"
    assert len(executor.calls[0][0].instruments) == 2


@pytest.mark.asyncio
async def test_webhook_retries_keep_identity_and_unsigned_is_never_sent(db, executor):
    from datetime import UTC, datetime, timedelta

    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), session["id"])
    sent: list[httpx.Request] = []

    def _flaky(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(500 if len(sent) == 1 else 200)

    unsigned = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.MockTransport(_flaky), signer=lambda: None
    )
    report = await unsigned.dispatch_tenant(uuid.UUID(tenant))
    assert report.retried == 1 and sent == []
    rows = await _rows(
        db, "SELECT last_error, attempts FROM commerce_order_webhook_deliveries WHERE tenant_id = :t", t=tenant
    )
    assert rows == [{"last_error": "signing_key_unavailable", "attempts": 1}]

    signing = uf.signer()
    later = [datetime.now(UTC) + timedelta(hours=1)]
    dispatcher = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.MockTransport(_flaky), signer=lambda: signing, clock=lambda: later[0]
    )
    first = await dispatcher.dispatch_tenant(uuid.UUID(tenant))
    later[0] = later[0] + timedelta(hours=1)
    second = await dispatcher.dispatch_tenant(uuid.UUID(tenant))
    assert (first.retried, second.delivered) == (1, 1)
    assert sent[0].headers["webhook-id"] == sent[1].headers["webhook-id"]


@pytest.mark.asyncio
async def test_expired_session_reads_as_canceled_and_refuses_writes(db, executor):
    from datetime import UTC, datetime, timedelta

    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    session = await _ready_session(runtime, tenant)
    later = uf.runtime(tenant, now=datetime.now(UTC) + timedelta(hours=7))
    got = await handlers.run("get_checkout", later, uf.request(tenant), session["id"])
    assert got.body["status"] == "canceled"
    refused = await handlers.run("complete_checkout", later, uf.request(tenant, uf.complete_body()), session["id"])
    assert refused.status == 409 and executor.calls == []


@pytest.mark.asyncio
async def test_expired_cart_session_is_canceled_and_the_cart_converts_again(db, executor):
    from datetime import UTC, datetime, timedelta

    tenant = str(uuid.uuid4())
    runtime = uf.runtime(tenant)
    cart = await handlers.run("create_cart", runtime, uf.request(tenant, uf.create_body()))
    first = await handlers.run("create_checkout", runtime, uf.request(tenant, {"cart_id": cart.body["id"]}))
    later = uf.runtime(tenant, now=datetime.now(UTC) + timedelta(hours=7))
    second = await handlers.run("create_checkout", later, uf.request(tenant, {"cart_id": cart.body["id"]}))
    assert second.status == 201 and second.body["id"] != first.body["id"]
    rows = await _rows(db, "SELECT status FROM commerce_checkout_sessions WHERE id = :i", i=first.body["id"])
    assert rows == [{"status": "canceled"}]
