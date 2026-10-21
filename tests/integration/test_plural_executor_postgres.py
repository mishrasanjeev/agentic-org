# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""G55, G56, G58 on a real Postgres: UCP Complete through the Plural executor, RBI AFA, refunds and tracking.

The Plural API is a fake (httpx MockTransport) that counts every request, so
"no decision, no call" is checked against the wire. Requires the disposable
database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors, india_rails
from core.commerce.executors.plural import PluralExecutor, PluralWebhookError, apply_plural_event
from core.commerce.order_refunds import refund_order
from core.commerce.ucp import handlers
from tests import ucp_schema
from tests import ucp_txn_fixtures as uf
from tests.plural_fakes import WEBHOOK_SECRET, FakePlural, credentials, signed_headers

UPI_CAPABILITY = {
    "rails": ["card", "upi"],
    "max_amount_minor_units": 50000,
    "currency": "INR",
    "instrument_types": ["card", "upi_autopay"],
    "upi_autopay": {
        "amount_rule": "max",
        "max_amount_minor_units": 500000,
        "frequency": "monthly",
        "validity_days": 365,
        "purpose": "general",
        "authorization_url": "https://payments.example/upi/mandates/{mandate_reference}",
    },
}


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "scripts/alembic_migrate.py"], capture_output=True, text=True, env=env
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
def fake():
    plural = FakePlural()
    executor = PluralExecutor(credentials=credentials, transport_factory=plural.factory())
    executors.register_executor(executor)
    try:
        yield plural
    finally:
        executors.unregister_executor(executor.name)


async def _rows(engine, sql: str, *, tenant: str | None = None, **params) -> list[dict]:
    """Rows read inside the tenant's RLS context, so the module also runs as the NOBYPASSRLS app role."""
    async with engine.connect() as conn:
        if tenant is not None:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, false)"), {"t": tenant})
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


async def _events(engine, tenant: str) -> list[str]:
    rows = await _rows(
        engine,
        "SELECT event_type FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        tenant=tenant,
        t=tenant,
    )
    return [r["event_type"] for r in rows]


def _runtime(tenant: str, *, mandate=None, profile=None, config=None):
    overrides = {"executor": "plural_pine"}
    if mandate is not None:
        overrides["mandate_capability"] = mandate
    resolved = uf.merchant(tenant, **overrides)
    return uf.runtime(tenant, resolved=resolved, profile=profile, config=config)


async def _session(runtime, tenant: str) -> dict:
    created = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=uf.SHIPPING))
    )
    assert created.status == 201 and created.body["status"] == "ready_for_complete", created.body
    return created.body


async def _complete(runtime, tenant: str, session_id: str, key: str, **body):
    return await handlers.run(
        "complete_checkout", runtime, uf.request(tenant, uf.complete_body(**body), key=key), session_id
    )


@pytest.mark.asyncio
async def test_complete_charges_through_plural_with_decision_id_and_ledgers_every_call(db, fake):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    session = await _session(runtime, tenant)
    done = await _complete(runtime, tenant, session["id"], "complete-1")
    assert done.status == 200 and done.body["status"] == "completed", done.body
    assert [r.url.path for r in fake.api_calls] == [
        "/api/pay/v1/orders",
        "/api/pay/v1/orders/v1-ord-1/payments",
        "/api/pay/v1/orders/v1-ord-1/capture",
    ]
    sql = "SELECT * FROM commerce_checkout_sessions WHERE id = :s"
    session_row = (await _rows(db, sql, tenant=tenant, s=session["id"]))[0]
    decision_id = session_row["decision_id"]
    decision = (
        await _rows(db, "SELECT * FROM commerce_arbiter_decisions WHERE decision_id = :d", tenant=tenant, d=decision_id)
    )[0]
    assert decision["consumed_at"] is not None and decision["outcome"] == "allow"
    assert "afa_evidence_present" in decision["rules_evaluated"]
    assert set(decision["evidence"]["mandate_hashes"]) == {"intent", "cart", "payment"}
    metadata = json.loads(fake.api_calls[0].content)["purchase_details"]["merchant_metadata"]
    assert metadata["oacp_decision_id"] == decision_id
    assert metadata["oacp_payload_hash"] == decision["payload_hash"]
    order = (
        await _rows(db, "SELECT * FROM commerce_orders WHERE checkout_session_id = :s", tenant=tenant, s=session["id"])
    )[0]
    assert order["provider_order_ref"] == "v1-ord-1" and order["executor"] == "plural_pine"
    operations = await _rows(
        db,
        "SELECT operation, phase, decision_id FROM commerce_payment_operations WHERE tenant_id = :t",
        tenant=tenant,
        t=tenant,
    )
    assert {(o["operation"], o["phase"]) for o in operations} == {
        (op, phase) for op in ("create_order", "create_payment", "capture") for phase in ("requested", "succeeded")
    }
    assert {o["decision_id"] for o in operations} == {decision_id}
    events = await _events(db, tenant)
    for expected in (
        "commerce.arbiter.decision.consumed",
        "commerce.plural.create_order",
        "commerce.plural.create_payment",
        "commerce.plural.capture",
        "commerce.ucp.order.created",
    ):
        assert expected in events, (expected, events)
    assert events.index("commerce.arbiter.decision.consumed") < events.index("commerce.plural.create_order")


@pytest.mark.asyncio
async def test_refused_decision_makes_no_plural_call(db, fake):
    tenant = str(uuid.uuid4())
    config = uf.merchant_config(tenant)
    config["provider_policy"]["arbiter"]["max_order_amount_minor_units"] = 100
    runtime = _runtime(tenant, config=config)
    session = await _session(runtime, tenant)
    refused = await _complete(runtime, tenant, session["id"], "complete-r")
    assert refused.body["status"] == "ready_for_complete"
    assert refused.body["ai.agenticorg.commerce"]["decision"]["failing_rule"] == "merchant_limits"
    assert fake.requests == []
    sql = "SELECT 1 FROM commerce_payment_operations WHERE tenant_id = :t"
    assert await _rows(db, sql, tenant=tenant, t=tenant) == []


@pytest.mark.asyncio
async def test_decide_only_issues_no_call_and_a_replayed_decision_is_refused(db, fake):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    session = await _session(runtime, tenant)
    issued = await _complete(runtime, tenant, session["id"], "decide-1", decide_only=True)
    decision_id = issued.body["ai.agenticorg.commerce"]["decision"]["decision_id"]
    assert fake.requests == []
    # Somebody else consumes the decision first (a concurrent completion).
    from core.database import get_tenant_session

    payload_hash = issued.body["ai.agenticorg.commerce"]["decision"]["payload_hash"]
    async with get_tenant_session(uuid.UUID(tenant)) as s:
        await executors.consume_decision(
            s,
            tenant_id=tenant,
            merchant_id=uf.fx.MERCHANT,
            seller_agent_id=uf.fx.SELLER,
            decision_id=decision_id,
            payload_hash=payload_hash,
        )
    replay = await _complete(runtime, tenant, session["id"], "complete-2", decision_id=decision_id)
    assert replay.body["status"] == "ready_for_complete"
    assert replay.body["messages"][-1]["code"] == "decision_consumed"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_flag_off_leaves_complete_executor_unavailable(db):
    from core.commerce.executors import plural

    plural.sync_registration({})
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    session = await _session(runtime, tenant)
    blocked = await _complete(runtime, tenant, session["id"], "complete-off")
    assert blocked.body["status"] == "ready_for_complete"
    assert blocked.body["messages"][-1]["code"] == "executor_unavailable"


@pytest.mark.asyncio
async def test_refunds_need_a_fresh_refund_decision_and_stay_within_the_capture(db, fake):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    session = await _session(runtime, tenant)
    done = await _complete(runtime, tenant, session["id"], "complete-rf")
    order_id = done.body["order"]["id"]
    now = datetime.now(UTC)
    first = await refund_order(tenant_id=tenant, order_id=order_id, amount_minor_units=3000, reason="Damaged", now=now)
    assert first.status == "succeeded", first
    assert first.decision["action"] == "refund" and first.decision["outcome"] == "allow"
    calls = len(fake.requests)
    later = now + timedelta(seconds=1)
    over = await refund_order(tenant_id=tenant, order_id=order_id, amount_minor_units=5000, reason=None, now=later)
    assert over.status == "refused" and over.reason == "refund_within_captured"
    assert len(fake.requests) == calls
    last = now + timedelta(seconds=2)
    rest = await refund_order(tenant_id=tenant, order_id=order_id, amount_minor_units=4500, reason=None, now=last)
    assert rest.status == "succeeded"
    decisions = await _rows(
        db,
        "SELECT outcome, consumed_at FROM commerce_arbiter_decisions WHERE tenant_id = :t AND action = 'refund' "
        "ORDER BY decided_at",
        tenant=tenant,
        t=tenant,
    )
    assert [d["outcome"] for d in decisions] == ["allow", "refuse", "allow"]
    assert all(d["consumed_at"] is not None for d in decisions if d["outcome"] == "allow")
    order = await handlers.run("get_order", runtime, uf.request(tenant), order_id)
    refunds = [a for a in order.body["adjustments"] if a["type"] == "refund"]
    assert [r["totals"][0]["amount"] for r in refunds] == [-3000, -4500]
    ucp_schema.assert_valid("2026-04-08", "shopping/order.json", order.body)
    assert "commerce.plural.refund" in await _events(db, tenant)


@pytest.mark.asyncio
async def test_upi_autopay_needs_afa_evidence_before_the_session_can_complete(db, fake):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant, mandate=UPI_CAPABILITY)
    session = await _session(runtime, tenant)
    payment = uf.token_payment()
    payment["instruments"][0]["type"] = "upi_autopay"
    blocked = await _complete(runtime, tenant, session["id"], "afa-1", payment=payment)
    assert blocked.body["status"] == "requires_escalation", blocked.body
    ucp_schema.assert_valid_checkout("2026-04-08", blocked.body)
    assert blocked.body["continue_url"].startswith("https://payments.example/upi/mandates/aom-")
    assert blocked.body["messages"][-1]["code"] == india_rails.AFA_ERROR_CODE
    assert blocked.body["ai.agenticorg.commerce"]["decision"]["failing_rule"] == "afa_evidence_present"
    assert fake.requests == []
    reference = blocked.body["continue_url"].rsplit("/", 1)[-1]

    # A forged event (bad signature) is refused and records nothing.
    body = json.dumps(
        {"data": {"mandate": {"merchant_mandate_reference": reference, "mandate_id": "md-9", "status": "ACTIVE"}}}
    ).encode()
    now = int(time.time())
    with pytest.raises(PluralWebhookError):
        await apply_plural_event(
            tenant_id=tenant,
            raw_body=body,
            headers={**signed_headers(body, timestamp=now), "webhook-signature": "v1,AAAA"},
            secret=WEBHOOK_SECRET,
        )
    rows = await _rows(db, "SELECT phase FROM commerce_afa_evidence WHERE tenant_id = :t", tenant=tenant, t=tenant)
    assert [r["phase"] for r in rows] == ["requested"]
    still = await _complete(runtime, tenant, session["id"], "afa-2", payment=payment)
    assert still.status == 200 and still.body["status"] != "completed"

    applied = await apply_plural_event(
        tenant_id=tenant, raw_body=body, headers=signed_headers(body, timestamp=now), secret=WEBHOOK_SECRET
    )
    assert applied["afa"] == "authorized"
    ready = await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])
    assert ready.body["status"] == "ready_for_complete" and "continue_url" not in ready.body
    done = await _complete(runtime, tenant, session["id"], "afa-3", payment=payment)
    assert done.body["status"] == "completed", done.body
    option = json.loads(fake.api_calls[1].content)["payments"][0]["payment_option"]
    assert option["upi_details"]["mandate_reference"] == reference
    decisions = await _rows(
        db,
        "SELECT outcome, failing_rule FROM commerce_arbiter_decisions WHERE tenant_id = :t AND action = "
        "'session_complete' ORDER BY decided_at",
        tenant=tenant,
        t=tenant,
    )
    assert decisions[0]["failing_rule"] == "afa_evidence_present" and decisions[-1]["outcome"] == "allow"


@pytest.mark.asyncio
async def test_upi_afa_is_an_actions_step_when_the_extension_is_negotiated(db, fake):
    tenant = str(uuid.uuid4())
    names = [*uf.ALL_CAPABILITIES, *uf.R25_EXTRA, india_rails.UCP_UPI_MANDATE_EXTENSION]
    profile = uf.platform_profile("2026-08-25", capabilities=names)
    runtime = _runtime(tenant, mandate=UPI_CAPABILITY, profile=profile)
    session = await _session(runtime, tenant)
    payment = uf.token_payment()
    payment["instruments"][0]["type"] = "upi_autopay"
    blocked = await _complete(runtime, tenant, session["id"], "afa-r25", payment=payment)
    assert blocked.body["status"] == "incomplete", blocked.body
    ucp_schema.assert_valid_checkout("2026-08-25", blocked.body)
    action = blocked.body["actions"][india_rails.UCP_AFA_ACTION][0]
    assert action["config"]["payment_instrument_id"] == "instr_1"
    assert action["config"]["mandate"]["frequency"] == "monthly"
    assert blocked.body["messages"][-1]["path"] == f"$.actions['{india_rails.UCP_AFA_ACTION}'][0]"
    assert india_rails.UCP_UPI_MANDATE_EXTENSION in blocked.body["ucp"]["capabilities"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_payment_records_are_append_only_and_tenant_scoped(db, fake):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    session = await _session(runtime, tenant)
    await _complete(runtime, tenant, session["id"], "complete-ao")
    async with db.connect() as conn:
        # The trigger refuses as the owner; the app role does not even hold UPDATE.
        with pytest.raises(Exception, match="append-only|permission denied"):
            await conn.execute(
                text("UPDATE commerce_payment_operations SET phase = 'failed' WHERE tenant_id = :t"), {"t": tenant}
            )
    sql = "SELECT id FROM commerce_payment_operations WHERE tenant_id = :t"
    assert await _rows(db, sql, tenant=tenant, t=tenant)
    if "agenticorg_app" in os.environ["AGENTICORG_DB_URL"]:
        # Under the NOBYPASSRLS role another tenant's context sees none of these rows.
        assert await _rows(db, sql, tenant=str(uuid.uuid4()), t=tenant) == []
    flags = await _rows(
        db,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('commerce_payment_operations', 'commerce_afa_evidence')",
    )
    assert len(flags) == 2 and all(f["relrowsecurity"] and f["relforcerowsecurity"] for f in flags)


@pytest.mark.asyncio
async def test_a_redelivered_shopify_fulfillment_is_recorded_once(db, fake):
    from core.commerce.fulfillment_tracking import normalise_shopify_fulfillment

    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    session = await _session(runtime, tenant)
    done = await _complete(runtime, tenant, session["id"], "complete-tr")
    order_id = done.body["order"]["id"]
    sql = "SELECT snapshot FROM commerce_orders WHERE id = :o"
    snapshot = (await _rows(db, sql, tenant=tenant, o=order_id))[0]["snapshot"]
    payload = {"id": 9001, "status": "success", "tracking_company": "Acme Express", "tracking_numbers": ["AX1"]}
    normalised = normalise_shopify_fulfillment(payload, snapshot)
    now = datetime.now(UTC)
    revision, updated, _v = await handlers.apply_order_change(
        tenant_id=tenant, order_id=order_id, fulfillment_event=normalised.event, adjustment=None, now=now,
        event_id=normalised.event_id,
    )
    assert updated["line_items"][0]["status"] == "fulfilled"
    with pytest.raises(handlers.OrderChangeError) as dup:
        await handlers.apply_order_change(
            tenant_id=tenant, order_id=order_id, fulfillment_event=normalised.event, adjustment=None, now=now,
            event_id=normalised.event_id,
        )
    assert dup.value.status == 409
