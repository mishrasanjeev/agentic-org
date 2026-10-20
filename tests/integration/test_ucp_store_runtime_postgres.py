# SPDX-License-Identifier: Apache-2.0
"""UCP on a real Postgres: the serving-host index, the merchant scope reads, Idempotency-Key records, the merchant
runtime API for order webhook subscriptions and deliveries, and the dispatcher's dead-letter paths (G21, G27).

Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors
from core.commerce.oacp_merchant_config import merchant_config_id
from core.commerce.ucp import handlers, idempotency, store, webhooks
from tests import ucp_fixtures
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
    store.reset_ucp_host_cache()
    try:
        yield engine
    finally:
        store.reset_ucp_host_cache()
        await engine.dispose()


async def _rows(engine, sql: str, **params) -> list[dict]:
    owner_url = os.getenv("AGENTICORG_TEST_OWNER_DB_URL")
    observer = create_async_engine(owner_url, poolclass=NullPool) if owner_url else engine
    try:
        async with observer.connect() as conn:
            return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]
    finally:
        if owner_url:
            await observer.dispose()


async def _exec(engine, sql: str, **params) -> None:
    owner_url = os.getenv("AGENTICORG_TEST_OWNER_DB_URL")
    writer = create_async_engine(owner_url, poolclass=NullPool) if owner_url else engine
    try:
        async with writer.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        if owner_url:
            await writer.dispose()


async def _tenant(engine, settings_value: dict | None = None, *, with_admin: bool = False) -> tuple[str, str | None]:
    tenant = uuid.uuid4()
    await _exec(
        engine,
        "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
        "VALUES (:id, :name, :slug, 'enterprise', 'IN', CAST(:settings AS jsonb))",
        id=tenant,
        name=f"ucp-store-{tenant.hex}",
        slug=f"ucp-store-{tenant.hex}",
        settings=json.dumps(settings_value or {}),
    )
    user = None
    if with_admin:
        user = uuid.uuid4()
        await _exec(
            engine,
            "INSERT INTO users (id, tenant_id, email, name, role, status, mfa_enabled) "
            "VALUES (:id, :tenant_id, :email, 'Store Admin', 'admin', 'active', false)",
            id=user,
            tenant_id=tenant,
            email=f"admin-{tenant.hex[:8]}@example.com",
        )
    return str(tenant), (str(user) if user else None)


def _host() -> str:
    return f"shop-{uuid.uuid4().hex[:10]}.merchant.example"


# --- serving-host index ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serving_host_index_resolves_one_tenant_and_refuses_a_shared_host(db):
    host, shared, malformed = _host(), _host(), _host()
    owner, _ = await _tenant(
        db,
        {
            store.HOST_INDEX_KEY: {
                host: {"merchant_id": "merchant-a", "seller_agent_id": "seller-a"},
                shared: {"merchant_id": "merchant-a"},
                malformed: {"merchant_id": 42},
            }
        },
    )
    await _tenant(db, {store.HOST_INDEX_KEY: {shared: {"merchant_id": "merchant-b"}}})

    binding = await store.find_serving_binding(host)
    assert binding == store.UcpServingBinding(
        host=host, tenant_id=owner, merchant_id="merchant-a", seller_agent_id="seller-a"
    )
    assert await store.find_serving_binding(shared) is None  # claimed by two tenants: fail closed
    assert await store.find_serving_binding(malformed) is None
    assert await store.find_serving_binding(_host()) is None

    assert await store.host_claimed_by_other_tenant(shared, owner) is True
    assert await store.host_claimed_by_other_tenant(host, owner) is False
    assert await store.host_claimed_by_other_tenant(host, str(uuid.uuid4())) is True

    # The answer is cached until the TTL (or a reset); the database stays the source of truth.
    await _exec(db, "UPDATE tenants SET settings = '{}'::jsonb WHERE id = :id", id=uuid.UUID(owner))
    assert await store.find_serving_binding(host) == binding
    store.reset_ucp_host_cache()
    assert await store.find_serving_binding(host) is None


@pytest.mark.asyncio
async def test_write_host_index_points_moves_and_removes_a_host(db):
    from core.database import get_platform_session

    tenant, _ = await _tenant(db)
    first, second = _host(), _host()

    async with get_platform_session() as session:
        await store.write_host_index(
            session, tenant_id=tenant, host=first, merchant_id="merchant-a", seller_agent_id="seller-a"
        )
    assert (await store.find_serving_binding(first)).merchant_id == "merchant-a"

    # Moving to a new host drops the previous one and invalidates both cache entries.
    async with get_platform_session() as session:
        await store.write_host_index(
            session,
            tenant_id=tenant,
            host=second,
            merchant_id="merchant-a",
            seller_agent_id="seller-a",
            previous_host=first,
        )
    assert await store.find_serving_binding(first) is None
    assert (await store.find_serving_binding(second)).tenant_id == tenant
    settings_value = (await _rows(db, "SELECT settings FROM tenants WHERE id = :id", id=uuid.UUID(tenant)))[0]
    assert settings_value["settings"][store.HOST_INDEX_KEY] == {
        second: {"merchant_id": "merchant-a", "seller_agent_id": "seller-a"}
    }

    async with get_platform_session() as session:
        await store.write_host_index(session, tenant_id=tenant, host=second, merchant_id=None, seller_agent_id=None)
    assert await store.find_serving_binding(second) is None

    with pytest.raises(LookupError):
        async with get_platform_session() as session:
            await store.write_host_index(
                session, tenant_id=str(uuid.uuid4()), host=first, merchant_id="m", seller_agent_id=None
            )


# --- merchant scope reads ----------------------------------------------------------------------------


async def _config_row(tenant: str, merchant_id: str, seller_agent_id: str = "default") -> None:
    from core.database import get_tenant_session
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        session.add(
            C6ZMerchantCommerceConfigRow(
                config_id=merchant_config_id(tenant, merchant_id, seller_agent_id),
                tenant_id=tenant,
                merchant_id=merchant_id,
                seller_agent_id=seller_agent_id,
                merchant_display_name="Example Store",
                provider_policy={"ucp": {"currency": "INR"}},
            )
        )


@pytest.mark.asyncio
async def test_merchant_config_and_catalog_inputs_are_read_inside_the_tenant(db):
    from core.commerce.oacp_artifacts import DurableOacpArtifactCacheRepository
    from core.database import get_tenant_session
    from core.models.commerce_c6z_runtime import C6ZConnectorEvidenceRow

    tenant, _ = await _tenant(db)
    merchant_id, seller = "merchant-scope", "seller-scope"
    await _config_row(tenant, merchant_id, seller)
    row = await store.load_merchant_config_row(tenant_id=tenant, merchant_id=merchant_id, seller_agent_id=seller)
    assert row is not None and row.merchant_display_name == "Example Store"
    assert await store.load_merchant_config_row(tenant_id=tenant, merchant_id="other", seller_agent_id=seller) is None

    now = datetime.now(UTC)
    shared = dataclasses.replace(
        ucp_fixtures.cache_record("policy"),
        cache_record_id=f"cache_policy_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant,
        merchant_id=merchant_id,
        seller_agent_id=seller,
    )
    buyer_bound = dataclasses.replace(
        shared, cache_record_id=f"cache_policy_{uuid.uuid4().hex[:8]}", buyer_agent_id="buyer-1"
    )
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        repo = DurableOacpArtifactCacheRepository(session)
        await repo.upsert(shared)
        await repo.upsert(buyer_bound)

        def _evidence(suffix: str, synced: datetime, currency: str, products: list) -> C6ZConnectorEvidenceRow:
            return C6ZConnectorEvidenceRow(
                evidence_id=f"ev_{suffix}_{uuid.uuid4().hex[:8]}",
                packet_id=f"pkt_{suffix}",
                tenant_id=tenant,
                merchant_id=merchant_id,
                seller_agent_id=seller,
                source_evidence_ref=f"agenticorg:shopify:evidence:{suffix}:redacted",
                source_observed_at=synced,
                synced_at=synced,
                currency=currency,
                products=products,
                product_count=len(products),
            )

        session.add(
            _evidence(
                "new",
                now,
                "INR",
                [{"product_ref": "p1", "title": "Tote (new)"}, {"title": "no ref"}, "not-a-product"],
            )
        )
        session.add(
            _evidence(
                "old",
                now - timedelta(hours=1),
                "USD",
                [{"product_ref": "p1", "title": "Tote (old)"}, {"product_ref": "p2"}],
            )
        )

    records = await store.load_shared_cache_records(tenant_id=tenant, merchant_id=merchant_id, seller_agent_id=seller)
    assert [r.cache_record_id for r in records] == [shared.cache_record_id]  # buyer-bound records excluded

    records, products = await store.load_catalog_inputs(
        tenant_id=tenant, merchant_id=merchant_id, seller_agent_id=seller
    )
    assert [r.cache_record_id for r in records] == [shared.cache_record_id]
    # Newest evidence wins for a product; each product carries its evidence row's currency.
    assert products == [
        {"product_ref": "p1", "title": "Tote (new)", "_evidence_currency": "INR"},
        {"product_ref": "p2", "_evidence_currency": "USD"},
    ]
    other_scope = await store.load_catalog_inputs(tenant_id=tenant, merchant_id="other", seller_agent_id=seller)
    assert other_scope == ([], [])


# --- Idempotency-Key records -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_claim_replay_conflict_release_and_expiry(db):
    tenant = str(uuid.uuid4())
    scope = {"tenant_id": tenant, "merchant_id": "m-idem"}
    body = b'{"line_items":[]}'

    assert await idempotency.claim(**scope, key="k1", operation="create_checkout", raw_body=body) == idempotency.Claim(
        claimed=True
    )
    in_progress = await idempotency.claim(**scope, key="k1", operation="create_checkout", raw_body=body)
    assert in_progress.conflict == "idempotency_key_in_progress"

    await idempotency.store(**scope, key="k1", status=201, body={"id": "chk_1"})
    replay = await idempotency.claim(**scope, key="k1", operation="create_checkout", raw_body=body)
    assert replay == idempotency.Claim(claimed=False, replay=(201, {"id": "chk_1"}))
    other_body = await idempotency.claim(**scope, key="k1", operation="create_checkout", raw_body=b"{}")
    assert other_body.conflict == "idempotency_key_reused"
    other_operation = await idempotency.claim(**scope, key="k1", operation="create_cart", raw_body=body)
    assert other_operation.conflict == "idempotency_key_reused"

    # A 5xx answer is not stored: the claim is released so the client can retry.
    assert (await idempotency.claim(**scope, key="k2", operation="create_checkout", raw_body=body)).claimed
    await idempotency.store(**scope, key="k2", status=503, body={"messages": []})
    assert not await _rows(
        db,
        "SELECT 1 FROM commerce_ucp_idempotency WHERE tenant_id = :t AND idempotency_key = 'k2'",
        t=uuid.UUID(tenant),
    )
    assert (await idempotency.claim(**scope, key="k2", operation="create_checkout", raw_body=body)).claimed

    # An expired record is replaced by a fresh claim, whatever it held.
    await _exec(
        db,
        "UPDATE commerce_ucp_idempotency SET created_at = now() - interval '3 days', "
        "expires_at = now() - interval '1 day' WHERE tenant_id = :t AND idempotency_key = 'k1'",
        t=uuid.UUID(tenant),
    )
    assert (await idempotency.claim(**scope, key="k1", operation="create_cart", raw_body=b"{}")).claimed
    row = (
        await _rows(
            db,
            "SELECT operation, status, expires_at > now() AS live FROM commerce_ucp_idempotency "
            "WHERE tenant_id = :t AND idempotency_key = 'k1'",
            t=uuid.UUID(tenant),
        )
    )[0]
    assert row == {"operation": "create_cart", "status": "pending", "live": True}


@pytest.mark.asyncio
async def test_prune_expired_removes_only_expired_records(db):
    tenant, _ = await _tenant(db)
    scope = {"tenant_id": tenant, "merchant_id": "m-prune"}
    for key in ("old", "fresh"):
        assert (await idempotency.claim(**scope, key=key, operation="create_cart", raw_body=b"{}")).claimed
    await _exec(
        db,
        "UPDATE commerce_ucp_idempotency SET created_at = now() - interval '3 days', "
        "expires_at = now() - interval '1 day' WHERE tenant_id = :t AND idempotency_key = 'old'",
        t=uuid.UUID(tenant),
    )
    assert await idempotency.prune_expired() >= 1
    keys = await _rows(
        db, "SELECT idempotency_key FROM commerce_ucp_idempotency WHERE tenant_id = :t", t=uuid.UUID(tenant)
    )
    assert keys == [{"idempotency_key": "fresh"}]


@pytest.mark.asyncio
async def test_idempotency_store_failures(monkeypatch):
    from core import database

    @asynccontextmanager
    async def _broken(_tenant):
        raise ConnectionError("database unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr(database, "get_tenant_session", _broken)
    tenant = str(uuid.uuid4())
    with pytest.raises(idempotency.IdempotencyUnavailableError, match="ConnectionError"):
        await idempotency.claim(tenant_id=tenant, merchant_id="m", key="k", operation="create_cart", raw_body=b"{}")
    # Storing the answer after the durable write must not fail the request: the key just cannot replay.
    await idempotency.store(tenant_id=tenant, merchant_id="m", key="k", status=201, body={})


def test_idempotency_key_shape() -> None:
    assert idempotency.valid_key("k-1")
    assert not idempotency.valid_key(None) and not idempotency.valid_key("")
    assert not idempotency.valid_key("k" * 256) and not idempotency.valid_key("bad\nkey")
    assert idempotency.body_sha256(b"{}") == hashlib.sha256(b"{}").hexdigest()


# --- merchant runtime API: subscriptions, deliveries, replay --------------------------------------------


@pytest.fixture()
def executor():
    fake = uf.FakeExecutor()
    executors.register_executor(fake)
    try:
        yield fake
    finally:
        executors.unregister_executor(fake.name)


async def _completed_order(tenant: str) -> dict:
    runtime = uf.runtime(tenant, signing=uf.signer())
    created = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=uf.SHIPPING))
    )
    assert created.status == 201 and created.body["status"] == "ready_for_complete", created.body
    done = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), created.body["id"])
    assert done.body["status"] == "completed", done.body.get("messages")
    return done.body


async def _api_key(tenant: str, user: str) -> uuid.UUID:
    from core.database import get_tenant_session
    from core.models.api_key import APIKey

    key_id = uuid.uuid4()
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        session.add(
            APIKey(
                id=key_id,
                tenant_id=uuid.UUID(tenant),
                user_id=uuid.UUID(user),
                name="agent-platform",
                prefix="ao_sk_test",
                key_hash=hashlib.sha256(key_id.bytes).hexdigest(),
                scopes=["acp:read", "acp:write"],
                status="active",
            )
        )
    return key_id


async def _ledger(engine, tenant: str) -> list[str]:
    rows = await _rows(
        engine,
        "SELECT event_type FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        t=uuid.UUID(tenant),
    )
    return [r["event_type"] for r in rows]


@pytest.mark.asyncio
async def test_subscription_lifecycle_through_the_runtime_api(db, monkeypatch):
    from api.v1 import commerce_ucp as api

    tenant, user = await _tenant(db, with_admin=True)
    merchant_id = uf.merchant(tenant).merchant_id
    create = api.SubscriptionCreate

    with pytest.raises(HTTPException) as missing_merchant:
        await api.create_order_webhook_subscription(
            create(merchant_id=merchant_id, url="https://receiver.example/orders"), tenant_id=tenant
        )
    assert missing_merchant.value.status_code == 404

    await _config_row(tenant, merchant_id)
    ucp_view = await api.create_order_webhook_subscription(
        create(merchant_id=merchant_id, url="https://receiver.example/orders"), tenant_id=tenant
    )
    assert ucp_view["scheme"] == "ucp" and ucp_view["source"] == "merchant" and "signing_key" not in ucp_view
    with pytest.raises(HTTPException) as duplicate:
        await api.create_order_webhook_subscription(
            create(merchant_id=merchant_id, url="https://receiver.example/orders"), tenant_id=tenant
        )
    assert (duplicate.value.status_code, duplicate.value.detail) == (409, "subscription_exists")

    with pytest.raises(HTTPException) as unbound:
        await api.create_order_webhook_subscription(
            create(merchant_id=merchant_id, url="https://agent.example/orders", scheme="acp_hmac"), tenant_id=tenant
        )
    assert (unbound.value.status_code, unbound.value.detail) == (422, "platform_required")
    with pytest.raises(HTTPException) as unknown_key:
        await api.create_order_webhook_subscription(
            create(
                merchant_id=merchant_id,
                url="https://agent.example/orders",
                scheme="acp_hmac",
                platform_api_key_id=uuid.uuid4(),
            ),
            tenant_id=tenant,
        )
    assert unknown_key.value.status_code == 404

    key_id = await _api_key(tenant, user)
    acp_view = await api.create_order_webhook_subscription(
        create(
            merchant_id=merchant_id, url="https://agent.example/orders", scheme="acp_hmac", platform_api_key_id=key_id
        ),
        tenant_id=tenant,
    )
    assert acp_view["platform_profile_url"] == webhooks.acp_platform_id(key_id)
    first_key = acp_view["signing_key"]
    assert first_key["key_id"] == acp_view["active_key_id"] and len(first_key["secret"]) >= 32
    stored = await _rows(
        db,
        "SELECT signing_keys_encrypted FROM commerce_order_webhook_subscriptions WHERE id = :i",
        i=uuid.UUID(acp_view["id"]),
    )
    assert first_key["secret"] not in json.dumps(stored[0]["signing_keys_encrypted"])

    listed = await api.list_order_webhook_subscriptions(merchant_id=None, tenant_id=tenant)
    assert [s["id"] for s in listed["subscriptions"]] == [ucp_view["id"], acp_view["id"]]
    assert (await api.list_order_webhook_subscriptions(merchant_id="nobody", tenant_id=tenant)) == {"subscriptions": []}

    disabled = await api.update_order_webhook_subscription(
        ucp_view["id"], api.SubscriptionUpdate(enabled=False), tenant_id=tenant
    )
    assert disabled["enabled"] is False
    for bad in ("not-a-uuid", str(uuid.uuid4())):
        with pytest.raises(HTTPException) as gone:
            await api.update_order_webhook_subscription(bad, api.SubscriptionUpdate(enabled=True), tenant_id=tenant)
        assert gone.value.status_code == 404

    with pytest.raises(HTTPException) as not_hmac:
        await api.rotate_order_webhook_subscription_key(ucp_view["id"], tenant_id=tenant)
    assert (not_hmac.value.status_code, not_hmac.value.detail) == (422, "scheme_invalid")
    rotated = await api.rotate_order_webhook_subscription_key(acp_view["id"], tenant_id=tenant)
    assert rotated["active_key_id"] == rotated["signing_key"]["key_id"] != first_key["key_id"]

    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderWebhookSubscriptionRow

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        row = await session.get(CommerceOrderWebhookSubscriptionRow, uuid.UUID(acp_view["id"]))
        keys = await webhooks.decrypt_keys(row)
    # The previous key is kept so the receiver can switch before it is retired.
    assert [k.key_id for k in keys] == [rotated["signing_key"]["key_id"], first_key["key_id"]]

    events = await _ledger(db, tenant)
    assert events.count("commerce.ucp.order_webhook_subscription.create") == 2
    assert "commerce.ucp.order_webhook_subscription.update" in events
    assert "commerce.ucp.order_webhook_subscription.rotate_key" in events


@pytest.mark.asyncio
async def test_rejected_delivery_dead_letters_and_replays_through_the_runtime_api(db, executor, monkeypatch):
    from api.v1 import commerce_ucp as api

    tenant, _ = await _tenant(db)
    done = await _completed_order(tenant)
    order_id = done["order"]["id"]
    kicks: list[uuid.UUID] = []
    monkeypatch.setattr(api, "kick_dispatch", kicks.append)

    listed = await api.list_order_webhook_deliveries(order_id, tenant_id=tenant)
    assert [(d["event_type"], d["status"], d["attempts"]) for d in listed["deliveries"]] == [
        ("order.created", "pending", 0)
    ]
    delivery_id = listed["deliveries"][0]["id"]
    with pytest.raises(HTTPException) as pending:
        await api.replay_order_webhook_delivery(delivery_id, tenant_id=tenant)
    assert pending.value.status_code == 409

    signing = uf.signer()
    rejecting = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.MockTransport(lambda _r: httpx.Response(400, json={"error": "bad"})),
        signer=lambda: signing,
    )
    report = await rejecting.dispatch_tenant(uuid.UUID(tenant))
    assert (report.delivered, report.retried, report.dead_lettered) == (0, 0, 1)
    dead = (await api.list_order_webhook_deliveries(order_id, tenant_id=tenant))["deliveries"][0]
    assert dead["status"] == "dead_lettered" and dead["last_status_code"] == 400
    assert dead["last_error"] == "endpoint_rejected:http_400"
    # Nothing is due any more: an empty pass.
    assert (await rejecting.dispatch_tenant(uuid.UUID(tenant))).dead_lettered == 0

    replayed = await api.replay_order_webhook_delivery(delivery_id, tenant_id=tenant)
    assert replayed == {"id": delivery_id, "status": "pending"}
    assert kicks == [uuid.UUID(tenant)]
    again = (await api.list_order_webhook_deliveries(order_id, tenant_id=tenant))["deliveries"][0]
    assert (again["status"], again["attempts"], again["last_error"]) == ("pending", 0, None)
    assert "commerce.ucp.order_webhook_delivery.replay" in await _ledger(db, tenant)

    for bad in ("not-a-uuid", str(uuid.uuid4())):
        with pytest.raises(HTTPException) as missing:
            await api.replay_order_webhook_delivery(bad, tenant_id=tenant)
        assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as unknown_order:
        await api.record_order_event(
            "ord_missing", api.OrderEventRequest(adjustment={"type": "refund"}), tenant_id=tenant
        )
    assert unknown_order.value.status_code == 404
    assert kicks == [uuid.UUID(tenant)]


@pytest.mark.asyncio
async def test_delivery_to_a_disabled_subscription_is_dead_lettered_without_sending(db, executor):
    tenant, _ = await _tenant(db)
    await _completed_order(tenant)
    await _exec(
        db, "UPDATE commerce_order_webhook_subscriptions SET enabled = false WHERE tenant_id = :t", t=uuid.UUID(tenant)
    )
    sent: list[httpx.Request] = []
    dispatcher = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
        signer=uf.signer,
    )
    report = await dispatcher.dispatch_tenant(uuid.UUID(tenant))
    assert report.dead_lettered == 1 and sent == []
    rows = await _rows(
        db, "SELECT status, last_error FROM commerce_order_webhook_deliveries WHERE tenant_id = :t", t=uuid.UUID(tenant)
    )
    assert rows == [{"status": "dead_lettered", "last_error": "subscription_disabled"}]


@pytest.mark.asyncio
async def test_platform_subscription_is_reused_only_by_its_own_platform(db, executor):
    from core.database import get_tenant_session

    tenant, _ = await _tenant(db)
    await _completed_order(tenant)
    merchant_id = uf.merchant(tenant).merchant_id
    now = datetime.now(UTC)
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        same = await webhooks.ensure_platform_subscription(
            session,
            tenant_id=uuid.UUID(tenant),
            merchant_id=merchant_id,
            webhook_url=uf.WEBHOOK_URL,
            platform_profile_url=uf.PLATFORM_PROFILE,
            now=now,
        )
        other = await webhooks.ensure_platform_subscription(
            session,
            tenant_id=uuid.UUID(tenant),
            merchant_id=merchant_id,
            webhook_url=uf.WEBHOOK_URL,
            platform_profile_url="https://other-platform.example/profile.json",
            now=now,
        )
    rows = await _rows(
        db,
        "SELECT id, platform_profile_url FROM commerce_order_webhook_subscriptions WHERE tenant_id = :t",
        t=uuid.UUID(tenant),
    )
    assert len(rows) == 1 and same is not None and same.id == rows[0]["id"]
    assert rows[0]["platform_profile_url"] == uf.PLATFORM_PROFILE
    assert other is None


@pytest.mark.asyncio
async def test_sweep_lists_every_tenant(db):
    tenant, _ = await _tenant(db)
    assert uuid.UUID(tenant) in await webhooks._tenants_with_pending()
