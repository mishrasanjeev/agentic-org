# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Plural 3-D Secure and pending payments reconciled from verified webhooks, on a real Postgres (G55).

The Plural API is the MockTransport fake that counts every request; events are
signed with the synthetic webhook secret. Each outcome must reach the payment
journal and the checkout exactly once, however often Plural redelivers it.
Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors
from core.commerce.executors.plural import PluralExecutor, apply_plural_event
from core.commerce.ucp import handlers
from tests import ucp_txn_fixtures as uf
from tests.plural_fakes import WEBHOOK_SECRET, FakePlural, credentials, signed_headers


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


@pytest.fixture(autouse=True)
def merchant_config_rows(monkeypatch):
    """The merchant config row reconciliation loads (production: the stored commerce config)."""
    from types import SimpleNamespace

    from core.commerce.ucp import store

    async def _load(*, tenant_id: str, merchant_id: str, seller_agent_id: str):
        config = uf.merchant_config(tenant_id, executor="plural_pine")
        assert (merchant_id, seller_agent_id) == (config["merchant_id"], config["seller_agent_id"])
        return SimpleNamespace(
            tenant_id=tenant_id, public_publishing={}, **{k: v for k, v in config.items() if k != "tenant_id"}
        )

    monkeypatch.setattr(store, "load_merchant_config_row", _load)


@pytest.fixture()
def plural():
    fake = FakePlural()
    executor = PluralExecutor(credentials=credentials, transport_factory=fake.factory())
    executors.register_executor(executor)
    try:
        yield fake
    finally:
        executors.unregister_executor(executor.name)


async def _rows(engine, sql: str, *, tenant: str | None = None, **params) -> list[dict]:
    async with engine.connect() as conn:
        if tenant is not None:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, false)"), {"t": tenant})
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


async def _ledger(engine, tenant: str) -> list[dict]:
    return await _rows(
        engine,
        "SELECT event_type, details FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        tenant=tenant,
        t=tenant,
    )


async def _session(runtime, tenant: str) -> dict:
    created = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=uf.SHIPPING))
    )
    assert created.status == 201 and created.body["status"] == "ready_for_complete", created.body
    return created.body


def _order_event(order_id: str, reference: str, status: str, *, webhook_id: str) -> tuple[bytes, dict]:
    body = json.dumps(
        {
            "event_type": f"ORDER_{status}",
            "data": {"order_id": order_id, "merchant_order_reference": reference, "status": status},
        }
    ).encode()
    return body, signed_headers(body, webhook_id=webhook_id, timestamp=int(time.time()))


async def _operations(engine, tenant: str) -> list[tuple[str, str]]:
    rows = await _rows(
        engine,
        "SELECT operation, phase FROM commerce_payment_operations WHERE tenant_id = :t ORDER BY created_at, id",
        tenant=tenant,
        t=tenant,
    )
    return [(r["operation"], r["phase"]) for r in rows]


@pytest.mark.asyncio
async def test_capture_after_3ds_is_reconciled_from_the_verified_webhook_once(db, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "arbiter_grantex_post_enabled", lambda env=None: False)
    fake = FakePlural(challenge_url="https://acs.payments.example/3ds/1")
    executor = PluralExecutor(credentials=credentials, transport_factory=fake.factory())
    executors.register_executor(executor)
    try:
        tenant = str(uuid.uuid4())
        runtime = uf.runtime(tenant, resolved=uf.merchant(tenant, executor="plural_pine"))
        session = await _session(runtime, tenant)
        challenged = await handlers.run(
            "complete_checkout", runtime, uf.request(tenant, uf.complete_body(), key="3ds-1"), session["id"]
        )
        assert challenged.body["status"] == "requires_escalation", challenged.body
        assert [r.url.path.rsplit("/", 1)[-1] for r in fake.api_calls] == ["orders", "payments"]
        reference = fake.bodies("/pay/v1/orders")[0]["merchant_order_reference"]

        body, headers = _order_event("v1-ord-1", reference, "AUTHORIZED", webhook_id="wh_3ds_1")
        applied = await apply_plural_event(tenant_id=tenant, raw_body=body, headers=headers, secret=WEBHOOK_SECRET)
        assert applied["applied"] is True and applied["payment"] == {
            "journal": "capture_succeeded",
            "session": "completed",
        }
        assert fake.api_calls[-1].url.path.endswith("/v1-ord-1/capture")
        got = await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])
        assert got.body["status"] == "completed" and got.body["order"]["id"]
        orders = await _rows(
            db, "SELECT provider_order_ref FROM commerce_orders WHERE tenant_id = :t", tenant=tenant, t=tenant
        )
        assert [o["provider_order_ref"] for o in orders] == ["v1-ord-1"]

        calls = len(fake.api_calls)
        replay_body, replay_headers = _order_event("v1-ord-1", reference, "AUTHORIZED", webhook_id="wh_3ds_2")
        replay = await apply_plural_event(
            tenant_id=tenant, raw_body=replay_body, headers=replay_headers, secret=WEBHOOK_SECRET
        )
        assert replay["applied"] is False and replay["payment"]["session"] == "already_completed"
        processed_body, processed_headers = _order_event("v1-ord-1", reference, "PROCESSED", webhook_id="wh_3ds_3")
        processed = await apply_plural_event(
            tenant_id=tenant, raw_body=processed_body, headers=processed_headers, secret=WEBHOOK_SECRET
        )
        assert processed["payment"]["journal"] == "unchanged"
        assert len(fake.api_calls) == calls
        assert (await _operations(db, tenant)).count(("capture", "succeeded")) == 1
        assert len(await _rows(db, "SELECT id FROM commerce_orders WHERE tenant_id = :t", tenant=tenant, t=tenant)) == 1
        events = [r["event_type"] for r in await _ledger(db, tenant)]
        assert "commerce.ucp.checkout.reconciled" in events and "commerce.plural.capture" in events
    finally:
        executors.unregister_executor(executor.name)


@pytest.mark.asyncio
async def test_pending_payment_captured_at_the_provider_completes_the_session(db, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "arbiter_grantex_post_enabled", lambda env=None: False)
    fake = FakePlural(payment_status="PENDING")
    executor = PluralExecutor(credentials=credentials, transport_factory=fake.factory())
    executors.register_executor(executor)
    try:
        tenant = str(uuid.uuid4())
        runtime = uf.runtime(tenant, resolved=uf.merchant(tenant, executor="plural_pine"))
        session = await _session(runtime, tenant)
        pending = await handlers.run(
            "complete_checkout", runtime, uf.request(tenant, uf.complete_body(), key="pend-1"), session["id"]
        )
        assert pending.body["status"] == "ready_for_complete"
        assert pending.body["messages"][-1]["code"] == "payment_pending"
        reference = fake.bodies("/pay/v1/orders")[0]["merchant_order_reference"]
        calls = len(fake.api_calls)
        body, headers = _order_event("v1-ord-1", reference, "PROCESSED", webhook_id="wh_pend_1")
        applied = await apply_plural_event(tenant_id=tenant, raw_body=body, headers=headers, secret=WEBHOOK_SECRET)
        assert applied["payment"] == {"journal": "capture_recorded", "session": "completed"}
        assert len(fake.api_calls) == calls, "a capture the provider reports is recorded, never repeated"
        assert ("capture", "succeeded") in await _operations(db, tenant)
        got = await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])
        assert got.body["status"] == "completed"
    finally:
        executors.unregister_executor(executor.name)


@pytest.mark.asyncio
async def test_failed_3ds_returns_the_session_to_ready_and_journals_the_failure(db, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "arbiter_grantex_post_enabled", lambda env=None: False)
    fake = FakePlural(challenge_url="https://acs.payments.example/3ds/2")
    executor = PluralExecutor(credentials=credentials, transport_factory=fake.factory())
    executors.register_executor(executor)
    try:
        tenant = str(uuid.uuid4())
        runtime = uf.runtime(tenant, resolved=uf.merchant(tenant, executor="plural_pine"))
        session = await _session(runtime, tenant)
        await handlers.run(
            "complete_checkout", runtime, uf.request(tenant, uf.complete_body(), key="3ds-f"), session["id"]
        )
        reference = fake.bodies("/pay/v1/orders")[0]["merchant_order_reference"]
        for webhook_id in ("wh_f_1", "wh_f_2"):
            body, headers = _order_event("v1-ord-1", reference, "FAILED", webhook_id=webhook_id)
            await apply_plural_event(tenant_id=tenant, raw_body=body, headers=headers, secret=WEBHOOK_SECRET)
        assert (await _operations(db, tenant)).count(("create_payment", "failed")) == 1
        got = await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])
        assert got.body["status"] == "ready_for_complete"
        assert not [r for r in fake.api_calls if r.url.path.endswith("/capture")]
    finally:
        executors.unregister_executor(executor.name)


@pytest.mark.asyncio
async def test_an_event_for_an_order_of_another_tenant_changes_nothing(db, plural):
    tenant = str(uuid.uuid4())
    body, headers = _order_event("v1-ord-9", "aoc-" + "0" * 44, "PROCESSED", webhook_id="wh_x")
    result = await apply_plural_event(tenant_id=tenant, raw_body=body, headers=headers, secret=WEBHOOK_SECRET)
    assert result["applied"] is False and result["payment"]["journal"] == "unknown_order"
    assert await _operations(db, tenant) == []
