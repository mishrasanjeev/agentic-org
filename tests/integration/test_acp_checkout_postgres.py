# SPDX-License-Identifier: Apache-2.0
"""ACP checkout sessions on a real Postgres over the UCP session table (G34, G35, G37, G38, G40).

Every response is validated against the ACP 2026-04-17 ``CheckoutSession`` / ``CheckoutSessionWithOrder`` schema.
Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors
from core.commerce.acp import capabilities as caps
from core.commerce.acp import checkout as acp
from core.commerce.acp import headers as hdr
from core.commerce.acp.orders import verify_merchant_signature
from core.commerce.grantex_service import GrantexServiceClient
from core.commerce.ucp import handlers, idempotency, webhooks
from tests import acp_fixtures as af
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
    async with engine.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


async def _ledger(engine, tenant: str) -> list[str]:
    rows = await _rows(
        engine,
        "SELECT event_type FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        t=tenant,
    )
    return [r["event_type"] for r in rows]


def _grantex(*, max_amount: int = 100000, fail: bool = False, calls: list | None = None) -> GrantexServiceClient:
    def _handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        if calls is not None:
            calls.append(sent)
        if fail:
            raise httpx.ConnectError("grantex down")
        return httpx.Response(200, json=af.allowance_answer(sent["checkout_session_id"], max_amount=max_amount))

    return GrantexServiceClient(
        base_url="https://grantex.example",
        token="service-token-placeholder",
        verifier=object(),
        transport_factory=lambda: httpx.MockTransport(_handler),
        allow_local_endpoints=True,
    )


def _vendor(**extra) -> dict:
    return {"buyer_authority": uf.authority(), "passport_jwt": af.PASSPORT, **extra}


async def _ready(runtime, tenant: str, *, vendor: dict | None = None) -> dict:
    created = await acp.run(af.request("create", tenant, af.create_body(vendor=vendor or _vendor())), runtime)
    assert created.status == 201, created.body
    af.assert_session(created.body)
    updated = await acp.run(af.request("update", tenant, af.select_shipping(), session_id=created.body["id"]), runtime)
    assert updated.status == 200, updated.body
    af.assert_session(updated.body)
    return updated.body


@pytest.fixture()
def plain_webhook_keys(monkeypatch):
    """The subscription keys stored as reversible test JSON instead of the tenant KEK envelope (no tenant row)."""
    from core.cases.push import SigningKey

    async def _encrypt(_tenant, keys):
        return {"_encrypted": "test:" + json.dumps([[k.key_id, k.secret, k.created_at] for k in keys])}

    async def _decrypt(row):
        return [SigningKey(*item) for item in json.loads(row.signing_keys_encrypted["_encrypted"][5:])]

    monkeypatch.setattr(webhooks, "_encrypt_keys", _encrypt)
    monkeypatch.setattr(webhooks, "decrypt_keys", _decrypt)


@pytest.mark.asyncio
async def test_create_update_complete_on_the_shared_session_table_with_signed_acp_order_event(
    db, executor, plain_webhook_keys
):
    tenant = str(uuid.uuid4())
    calls: list[dict] = []
    kicks: list[uuid.UUID] = []
    runtime = af.runtime(tenant, grantex=_grantex(calls=calls), kicks=kicks)
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        _sub, key = await webhooks.create_subscription(
            session,
            tenant_id=uuid.UUID(tenant),
            merchant_id=runtime.ucp.resolver.merchant.merchant_id,
            url="https://agent.example/agentic_checkout/webhooks/order_events",
            scheme="acp_hmac",
            now=datetime.now(UTC),
        )
    session_body = await _ready(runtime, tenant)
    assert session_body["status"] == "ready_for_payment", session_body["messages"]
    assert session_body["capabilities"]["extensions"] == [caps.SERVED_EXTENSIONS[caps.VENDOR_EXTENSION]]
    handler = session_body["capabilities"]["payment"]["handlers"][0]
    assert handler["config"]["delegate_payment"]["allowance"]["checkout_session_id"] == session_body["id"]
    assert calls and calls[0]["checkout_session_id"] == session_body["id"] and "passport_jwt" in calls[0]

    row = (await _rows(db, "SELECT * FROM commerce_checkout_sessions WHERE id = :i", i=session_body["id"]))[0]
    assert row["state"]["protocol"] == "acp" and row["platform_profile_url"] == af.PLATFORM
    assert af.PASSPORT not in json.dumps(row["state"])

    done = await acp.run(
        af.request("complete", tenant, af.complete_body(vendor=_vendor()), session_id=session_body["id"]), runtime
    )
    assert done.status == 200, done.body
    af.assert_session(done.body)
    assert done.body["status"] == "completed"
    order_id = done.body["order"]["id"]
    assert done.body["order"]["checkout_session_id"] == session_body["id"]
    assert len(executor.calls) == 1
    checkout, decision = executor.calls[0]
    assert checkout.instruments[0].credential == {"type": "plural_pine_token", "token": "tok_example_1"}
    decision_row = (
        await _rows(db, "SELECT * FROM commerce_arbiter_decisions WHERE decision_id = :d", d=decision.decision_id)
    )[0]
    assert decision_row["action"] == "session_complete" and decision_row["consumed_at"] is not None

    events = await _ledger(db, tenant)
    for expected in (
        "commerce.acp.delegate_payment.allowance",
        "commerce.acp.checkout.create",
        "commerce.acp.checkout.update",
        "commerce.arbiter.decision",
        "commerce.acp.payment.risk_signals",
        "commerce.acp.checkout.complete",
        "commerce.acp.order.created",
        "commerce.acp.checkout.execution",
    ):
        assert expected in events, (expected, events)
    assert not [e for e in events if e.startswith("commerce.ucp.checkout")]

    # The order event goes out through the UCP webhook outbox, as ACP order_create with Merchant-Signature.
    received: list[httpx.Request] = []

    def _receiver(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json={"received": True})

    dispatcher = webhooks.OrderWebhookDispatcher(transport_factory=lambda: httpx.MockTransport(_receiver))
    report = await dispatcher.dispatch_tenant(uuid.UUID(tenant))
    assert report.delivered == 1
    sent = received[0]
    assert verify_merchant_signature(
        sent.headers["merchant-signature"], sent.content, key.secret, now=int(datetime.now(UTC).timestamp())
    )
    event = json.loads(sent.content)
    assert event["type"] == "order_create" and event["data"]["id"] == order_id
    assert event["data"]["checkout_session_id"] == session_body["id"]
    assert kicks == [uuid.UUID(tenant)]

    got = await acp.run(af.request("get", tenant, session_id=session_body["id"]), runtime)
    assert got.status == 200 and got.body["status"] == "completed"
    af.assert_session(got.body)
    again = await acp.run(
        af.request("complete", tenant, af.complete_body(vendor=_vendor()), session_id=session_body["id"]), runtime
    )
    assert again.status == 405 and again.body["code"] == "session_not_modifiable"


@pytest.mark.asyncio
async def test_idempotency_key_header_semantics(db):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant)
    body = af.create_body()
    first = await acp.run(af.request("create", tenant, body, key="k-1"), runtime)
    assert first.status == 201 and first.headers["Idempotency-Key"] == "k-1"
    reordered = dict(reversed(list(body.items())))
    replay = await acp.run(af.request("create", tenant, reordered, key="k-1"), runtime)
    assert replay.status == 201 and replay.body == first.body
    assert replay.headers["Idempotent-Replayed"] == "true"
    conflict = await acp.run(af.request("create", tenant, af.create_body(af.TOTE, af.TOTE), key="k-1"), runtime)
    assert conflict.status == 422 and conflict.body["code"] == "idempotency_conflict"
    assert af.errors("Error", conflict.body) == []
    request = af.request("create", tenant, body)
    request.headers.pop("idempotency-key")
    missing = await acp.run(request, runtime)
    assert missing.status == 400 and missing.body["code"] == "idempotency_key_required"
    claim = await idempotency.claim(
        tenant_id=tenant,
        merchant_id=runtime.ucp.resolver.merchant.merchant_id,
        key="k-busy",
        operation="acp:create:",
        raw_body=hdr.canonical_body(json.dumps(body).encode()),
    )
    assert claim.claimed
    busy = await acp.run(af.request("create", tenant, body, key="k-busy"), runtime)
    assert busy.status == 409 and busy.body["code"] == "idempotency_in_flight"
    assert busy.headers["Retry-After"] == "1"
    sessions = await _rows(db, "SELECT id FROM commerce_checkout_sessions WHERE tenant_id = :t", t=tenant)
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_api_version_is_required_and_pinned(db):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant)
    request = af.request("create", tenant, af.create_body())
    request.headers.pop("api-version")
    missing = await acp.run(request, runtime)
    assert missing.status == 400 and missing.body["code"] == "missing_api_version"
    assert missing.body["supported_versions"] == ["2026-04-17"]
    old = await acp.run(af.request("create", tenant, af.create_body(), **{"API-Version": "2026-01-30"}), runtime)
    assert old.status == 400 and old.body["code"] == "unsupported_api_version"
    assert await _rows(db, "SELECT id FROM commerce_checkout_sessions WHERE tenant_id = :t", t=tenant) == []


@pytest.mark.asyncio
async def test_sessions_belong_to_their_protocol_and_platform(db):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant)
    created = await acp.run(af.request("create", tenant, af.create_body()), runtime)
    session_id = created.body["id"]
    other = await acp.run(af.request("get", tenant, session_id=session_id, platform="acp:apikey:other"), runtime)
    assert other.status == 404 and other.body == {
        "type": "invalid_request",
        "code": "not_found",
        "message": other.body["message"],
    }
    ucp_view = await handlers.run(
        "get_checkout", uf.runtime(tenant, resolved=af.merchant(tenant)), uf.request(tenant), session_id
    )
    assert ucp_view.status == 404
    ucp_session = await handlers.run(
        "create_checkout", uf.runtime(tenant, resolved=af.merchant(tenant)), uf.request(tenant, uf.create_body())
    )
    assert ucp_session.status == 201
    acp_view = await acp.run(af.request("get", tenant, session_id=ucp_session.body["id"]), runtime)
    assert acp_view.status == 404
    other_tenant = str(uuid.uuid4())
    cross = await acp.run(af.request("get", other_tenant, session_id=session_id), af.runtime(other_tenant))
    assert cross.status == 404


@pytest.mark.asyncio
async def test_cancel_then_cancel_again_is_405(db):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant)
    created = await acp.run(af.request("create", tenant, af.create_body()), runtime)
    canceled = await acp.run(
        af.request(
            "cancel", tenant, {"intent_trace": {"reason_code": "timing_deferred"}}, session_id=created.body["id"]
        ),
        runtime,
    )
    assert canceled.status == 200 and canceled.body["status"] == "canceled"
    af.assert_session(canceled.body)
    again = await acp.run(af.request("cancel", tenant, {}, session_id=created.body["id"]), runtime)
    assert again.status == 405 and again.body["code"] == "session_not_modifiable"
    update = await acp.run(af.request("update", tenant, af.select_shipping(), session_id=created.body["id"]), runtime)
    assert update.status == 405


@pytest.mark.asyncio
async def test_grantex_unreachable_is_a_business_error_and_completion_is_blocked(db, executor):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant, grantex=_grantex(fail=True))
    session_body = await _ready(runtime, tenant)
    assert session_body["status"] == "not_ready_for_payment"
    message = next(m for m in session_body["messages"] if m["type"] == "error")
    assert message["code"] == "missing" and message["resolution"] == "recoverable"
    assert "delegate_payment" not in session_body["capabilities"]["payment"]["handlers"][0]["config"]
    done = await acp.run(
        af.request("complete", tenant, af.complete_body(vendor=_vendor()), session_id=session_body["id"]), runtime
    )
    assert done.status == 200 and done.body["status"] == "not_ready_for_payment"
    af.assert_session(done.body)
    assert executor.calls == []
    assert not await _rows(db, "SELECT * FROM commerce_arbiter_decisions WHERE tenant_id = :t", t=tenant)
    allowance_rows = await _rows(
        db,
        "SELECT outcome FROM audit_log WHERE tenant_id = :t AND event_type = 'commerce.acp.delegate_payment.allowance'",
        t=tenant,
    )
    assert allowance_rows and all(r["outcome"] == "refused" for r in allowance_rows)


@pytest.mark.asyncio
async def test_total_above_the_allowance_is_maximum_exceeded(db, executor):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant, grantex=_grantex(max_amount=1000))
    session_body = await _ready(runtime, tenant)
    done = await acp.run(
        af.request("complete", tenant, af.complete_body(vendor=_vendor()), session_id=session_body["id"]), runtime
    )
    assert done.status == 200 and done.body["status"] == "ready_for_payment"
    assert {"code": "maximum_exceeded", "param": "$.totals"}.items() <= next(
        m for m in done.body["messages"] if m["type"] == "error"
    ).items()
    assert executor.calls == []


@pytest.mark.asyncio
async def test_no_executor_and_arbiter_refusal_are_acp_message_errors(db, executor):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant, grantex=_grantex())
    session_body = await _ready(runtime, tenant)
    vendor = _vendor()
    vendor["buyer_authority"].pop("mandate_ref")
    vendor["buyer_authority"].pop("mandate_hash")
    refused = await acp.run(
        af.request("complete", tenant, af.complete_body(vendor=vendor), session_id=session_body["id"]), runtime
    )
    assert refused.status == 200 and refused.body["status"] == "ready_for_payment"
    af.assert_session(refused.body)
    assert refused.body[caps.VENDOR_FIELD]["decision"]["failing_rule"] == "mandate_present_unexpired"
    assert any(m.get("code") == "payment_declined" for m in refused.body["messages"])
    assert "blocker" not in json.dumps(refused.body) and "ucp" not in refused.body
    executors.unregister_executor(executor.name)
    unavailable = await acp.run(
        af.request("complete", tenant, af.complete_body(vendor=_vendor()), session_id=session_body["id"]), runtime
    )
    assert any(m.get("code") == "unsupported" for m in unavailable.body["messages"])
    af.assert_session(unavailable.body)


@pytest.mark.asyncio
async def test_unknown_host_unopted_merchant_and_bad_bodies_are_acp_errors(db):
    tenant = str(uuid.uuid4())
    runtime = af.runtime(tenant)
    unknown = await acp.run(af.request("create", tenant, af.create_body(), host="unknown.example"), runtime)
    assert unknown.status == 404 and unknown.body["code"] == "not_found"
    closed = af.runtime(tenant, resolved=af.merchant(tenant, acp_enabled=False))
    assert (await acp.run(af.request("create", tenant, af.create_body()), closed)).status == 404
    bad = await acp.run(af.request("create", tenant, {"items": [{"id": af.TOTE, "quantity": 2}]}), runtime)
    assert bad.status == 400 and bad.body["type"] == "invalid_request" and af.errors("Error", bad.body) == []
    request = af.request("create", tenant, af.create_body())
    request.raw_body = b"{not json"
    assert (await acp.run(request, runtime)).status == 400
    enforced = af.runtime(tenant, mode="enforce")
    refused = await acp.run(af.request("create", tenant, af.create_body()), enforced)
    assert refused.status == 401 and refused.body["code"] == "invalid_signature"
