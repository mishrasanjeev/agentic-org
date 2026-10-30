# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Buyer payment hand-off on a real Postgres: decision sealing, links, Plural confirmation, orders, isolation.

The Plural API is an httpx MockTransport fake; webhooks are signed with a synthetic secret.
Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors, plural_hosted
from core.commerce.executors.plural import PluralWebhookError
from core.commerce.ucp import handlers
from core.commerce.ucp.catalog import ucp_variant_id
from core.commerce.ucp.merchant import merchant_ucp_from_config
from tests import ucp_txn_fixtures as uf

SHOP = "example-store.myshopify.com"
WEBHOOK_SECRET = base64.b64encode(b"synthetic-merchant-webhook-secret").decode()
ITEM_ID = ucp_variant_id({"variant_id": "gid://shopify/ProductVariant/1"})


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
    from core.config import settings

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr(settings, "public_api_base_url", "https://api.merchant.example")
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


@pytest.fixture()
def kicks(monkeypatch) -> list[tuple[str, str]]:
    """Order emails queued after commit (the Celery publish is replaced)."""
    from core.commerce import order_notifications
    from core.commerce.ucp import webhooks

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(order_notifications, "kick_order_notification", lambda t, o: sent.append((t, o)))
    monkeypatch.setattr(webhooks, "kick_dispatch", lambda _t: None)
    return sent


class FakeHosted:
    """Plural hosted checkout and order status, recording every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.status = "CREATED"
        self.order_http_status = 200

    def __call__(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def bodies(self, suffix: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(suffix) and r.content]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/auth/v1/token"):
            return httpx.Response(200, json={"access_token": "access-example"})
        if path.endswith("/checkout/v1/orders") and request.method == "POST":
            if self.order_http_status >= 400:
                return httpx.Response(self.order_http_status, json={"code": "OPERATION_NOT_ALLOWED"})
            body = json.loads(request.content)
            order_id = f"v1-hosted-{len(self.orders) + 1}"
            self.orders[order_id] = body
            return httpx.Response(
                200,
                json={
                    "data": {
                        "order_id": order_id,
                        "redirect_url": f"https://pay.plural.example/checkout?token={order_id}",
                        "token": "token-example",
                        "response_code": 200,
                    }
                },
            )
        if "/pay/v1/orders/" in path and request.method == "GET":
            order_id = path.rsplit("/", 1)[-1]
            body = self.orders.get(order_id)
            if body is None:
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "order_id": order_id,
                        "merchant_order_reference": body["merchant_order_reference"],
                        "status": self.status,
                        "order_amount": body["order_amount"],
                    }
                },
            )
        return httpx.Response(404, json={"code": "NOT_FOUND"})


@pytest.fixture()
def hosted(monkeypatch) -> FakeHosted:
    fake = FakeHosted()
    monkeypatch.setattr(plural_hosted, "transport_override", fake)
    return fake


async def _exec(engine, sql: str, **params) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def _rows(engine, sql: str, **params) -> list[dict]:
    owner_url = os.getenv("AGENTICORG_TEST_OWNER_DB_URL")
    observer = create_async_engine(owner_url, poolclass=NullPool) if owner_url else engine
    try:
        async with observer.connect() as conn:
            return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]
    finally:
        if owner_url:
            await observer.dispose()


async def _tenant(engine) -> str:
    tenant = uuid.uuid4()
    await _exec(
        engine,
        "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
        "VALUES (:id, :name, :slug, 'enterprise', 'IN', CAST('{}' AS jsonb))",
        id=tenant,
        name=f"handoff-{tenant.hex}",
        slug=f"handoff-{tenant.hex}",
    )
    return str(tenant)


def _config(tenant: str, payments: dict[str, Any] | None) -> dict[str, Any]:
    config = uf.merchant_config(tenant)
    config["source_connectors"] = [{"connector_type": "shopify", "shop_domain": SHOP, "enabled": True}]
    if payments is not None:
        config["provider_policy"]["payments"] = payments
    return config


def _runtime(monkeypatch, tenant: str, payments: dict[str, Any] | None) -> handlers.UcpRuntime:
    from core.commerce.ucp import store

    config = _config(tenant, payments)

    async def _load(*, tenant_id: str, merchant_id: str, seller_agent_id: str):
        assert tenant_id == tenant
        return SimpleNamespace(
            tenant_id=tenant_id, public_publishing={}, **{k: v for k, v in config.items() if k != "tenant_id"}
        )

    monkeypatch.setattr(store, "load_merchant_config_row", _load)
    resolved = merchant_ucp_from_config(config, tenant_id=tenant, serving_host=uf.HOST)
    assert resolved is not None
    return uf.runtime(tenant, resolved=resolved, config=config)


async def _ready(runtime, tenant: str, **body: Any) -> dict:
    created = await handlers.run(
        "create_checkout",
        runtime,
        uf.request(tenant, uf.create_body(item_id=ITEM_ID, fulfillment=uf.SHIPPING, **body)),
    )
    assert created.status == 201 and created.body["status"] == "ready_for_complete", created.body
    return created.body


async def _complete(runtime, tenant: str, session_id: str, body: Any = None, **flags: Any) -> handlers.UcpResponse:
    req = uf.request(tenant, {} if body is None else body)
    for name, value in flags.items():
        setattr(req, name, value)
    return await handlers.run("complete_checkout", runtime, req, session_id)


async def _save_plural(tenant: str, merchant_id: str = uf.fx.MERCHANT) -> plural_hosted.MerchantPluralCredentials:
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        await plural_hosted.save_credentials(
            session,
            tenant_id=tenant,
            merchant_id=merchant_id,
            environment="sandbox",
            client_id="client-example",
            client_secret="secret-example-value",
            webhook_secret=WEBHOOK_SECRET,
        )
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        stored = await plural_hosted.load_credentials(
            session,
            tenant_id=tenant,
            credential_ref=plural_hosted.credential_ref_for(merchant_id),
            merchant_id=merchant_id,
        )
    assert stored is not None
    return stored


def _plural_payments() -> dict[str, Any]:
    return {"mode": "plural_hosted", "plural": {"credential_ref": plural_hosted.credential_ref_for(uf.fx.MERCHANT)}}


def _signed(body: bytes, *, webhook_id: str, timestamp: int | None = None, secret: str = WEBHOOK_SECRET) -> dict:
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(base64.b64decode(secret), f"{webhook_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": "v1," + base64.b64encode(mac).decode(),
    }


def _event(order_id: str, reference: str, status: str, amount: int | None) -> bytes:
    data: dict[str, Any] = {"order_id": order_id, "merchant_order_reference": reference, "status": status}
    if amount is not None:
        data["order_amount"] = {"value": amount, "currency": "INR"}
    return json.dumps({"event_type": f"ORDER_{status}", "data": data}).encode()


async def _decisions(engine, tenant: str) -> list[dict]:
    return await _rows(
        engine,
        "SELECT decision_id, action, outcome, consumed_at, rules_evaluated FROM commerce_arbiter_decisions "
        "WHERE tenant_id = :t ORDER BY decided_at",
        t=tenant,
    )


async def _ledger(engine, tenant: str) -> list[str]:
    rows = await _rows(
        engine,
        "SELECT event_type FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        t=tenant,
    )
    return [r["event_type"] for r in rows]


# --- Shopify cart ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shopify_handoff_seals_a_decision_and_returns_the_cart_permalink_idempotently(
    db, executor, kicks, monkeypatch
):
    from core import config as core_config

    # Strict runtime posture: caller-asserted buyer authority is refused for executed payments.
    monkeypatch.setattr(core_config, "arbiter_require_verified_authority_enabled", lambda: True)
    tenant = await _tenant(db)
    runtime = _runtime(monkeypatch, tenant, {"mode": "shopify_cart"})
    session = await _ready(runtime, tenant)
    assert session["line_items"][0]["item"]["id"] == ITEM_ID

    done = await _complete(runtime, tenant, session["id"], body=uf.complete_body())
    assert done.status == 200, done.body
    assert done.body["status"] == "requires_escalation"
    assert done.body["continue_url"] == f"https://{SHOP}/cart/1:1"
    assert [m["code"] for m in done.body["messages"] if m["type"] == "error"] == ["payment_handoff"]
    assert executor.calls == []
    vendor = done.body["ai.agenticorg.commerce"]
    assert vendor["decision"]["action"] == "payment_handoff" and vendor["decision"]["outcome"] == "allow"

    # The numeric variant id stays server-side: only the buyer's link carries it.
    public = json.dumps({k: v for k, v in done.body.items() if k != "continue_url"})
    assert "gid://shopify" not in public and "storefront_variant" not in public and "ProductVariant" not in public

    decisions = await _decisions(db, tenant)
    assert len(decisions) == 1
    assert decisions[0]["action"] == "payment_handoff" and decisions[0]["consumed_at"] is not None
    assert list(decisions[0]["rules_evaluated"]) == ["oacp_artifacts_valid", "handoff_merchant_limits"]

    again = await _complete(runtime, tenant, session["id"])
    assert again.body["continue_url"] == done.body["continue_url"]
    assert len(await _decisions(db, tenant)) == 1
    read = await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])
    assert read.body["continue_url"] == done.body["continue_url"]

    events = await _ledger(db, tenant)
    for expected in (
        "commerce.arbiter.decision",
        "commerce.arbiter.decision.consumed",
        "commerce.ucp.checkout.complete",
    ):
        assert expected in events, (expected, events)
    assert kicks == []



@pytest.mark.asyncio
async def test_the_daily_cap_counts_the_days_open_and_paid_handoffs(db, executor, kicks, monkeypatch):
    tenant = await _tenant(db)
    runtime = _runtime(monkeypatch, tenant, {"mode": "shopify_cart"})
    first = await _ready(runtime, tenant)
    total = int(next(t["amount"] for t in first["totals"] if t["type"] == "total"))
    # Room for one checkout today, not two.
    runtime_config = (await runtime.arbiter_scope(None))[1]
    runtime_config["provider_policy"]["arbiter"]["max_daily_amount_minor_units"] = total * 2 - 1

    done = await _complete(runtime, tenant, first["id"], body=uf.complete_body())
    assert done.status == 200 and done.body["status"] == "requires_escalation", done.body

    second = await _ready(runtime, tenant)
    refused = await _complete(runtime, tenant, second["id"], body=uf.complete_body())
    assert refused.body.get("status") != "requires_escalation", refused.body
    assert "daily_amount_cap_exceeded" in json.dumps(refused.body)
    assert executor.calls == []

    # Re-opening the first link is not a new commitment.
    again = await _complete(runtime, tenant, first["id"])
    assert again.body["continue_url"] == done.body["continue_url"]

    # A lapsed link counts for nothing, even on the day it was issued: the second checkout now fits.
    now = datetime.now(UTC)
    await _move_handoff(db, first["id"], expires_at=now - timedelta(minutes=1))
    allowed = await _complete(runtime, tenant, second["id"], body=uf.complete_body())
    assert allowed.body["status"] == "requires_escalation", allowed.body

    # A link issued before UTC midnight that is still open counts: no room for a third checkout.
    before_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=1)
    for session_id in (first["id"], second["id"]):
        await _move_handoff(db, session_id, issued_at=before_midnight)
    third = await _ready(runtime, tenant)
    held = await _complete(runtime, tenant, third["id"], body=uf.complete_body())
    assert held.body.get("status") != "requires_escalation", held.body
    assert "daily_amount_cap_exceeded" in json.dumps(held.body)


async def _move_handoff(engine, session_id: str, *, issued_at=None, expires_at=None) -> None:
    """Move a hand-off in time as the clock would: when its link was issued (the row's last update), or when
    the link lapses."""
    from core.commerce.payment_handoff import iso

    if issued_at is not None:
        await _exec(
            engine,
            "UPDATE commerce_checkout_sessions SET updated_at = :at, "
            "state = jsonb_set(state, '{handoff,issued_at}', to_jsonb(CAST(:iso AS text))) WHERE id = :i",
            at=issued_at,
            iso=iso(issued_at),
            i=session_id,
        )
    if expires_at is not None:
        await _exec(
            engine,
            "UPDATE commerce_checkout_sessions "
            "SET state = jsonb_set(state, '{handoff,expires_at}', to_jsonb(CAST(:iso AS text))) WHERE id = :i",
            iso=iso(expires_at),
            i=session_id,
        )


@pytest.mark.asyncio
async def test_the_daily_total_is_todays_payments_plus_the_links_still_open(db):
    from core.commerce.payment_handoff import iso
    from core.database import get_tenant_session

    tenant = await _tenant(db)
    merchant = uf.merchant(tenant)
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    url = "https://example-store.myshopify.com/cart/1:1"
    open_link = {"status": "issued", "continue_url": url, "expires_at": iso(now + timedelta(minutes=20))}
    lapsed_link = {**open_link, "expires_at": iso(now - timedelta(seconds=1))}
    retired_link = {"status": "failed", "expires_at": open_link["expires_at"]}
    escalated = "requires_escalation"
    # name: (status, amount, hand-off, completed_at, currency)
    sessions: dict[str, tuple[str, int, dict[str, Any] | None, datetime | None, str]] = {
        "paid_today": ("completed", 100, None, midnight + timedelta(seconds=1), "INR"),
        "paid_yesterday": ("completed", 200, None, midnight - timedelta(seconds=1), "INR"),
        "open": (escalated, 400, open_link, None, "INR"),
        "lapsed": (escalated, 800, lapsed_link, None, "INR"),
        "retired": (escalated, 1600, retired_link, None, "INR"),
        "no_link": (escalated, 3200, None, None, "INR"),
        "usd": (escalated, 6400, open_link, None, "USD"),
        "deciding": (escalated, 12800, open_link, None, "INR"),
    }
    # The open link was issued before UTC midnight; the lapsed, retired and link-less ones were touched today.
    touched_today = {"lapsed", "retired", "no_link"}
    ids = {name: f"chk_{name}_{uuid.uuid4().hex[:8]}" for name in sessions}
    for name, (status, amount, handoff, completed_at, currency) in sessions.items():
        state: dict[str, Any] = {"totals": [{"type": "total", "amount": amount}]}
        if handoff is not None:
            state["handoff"] = handoff
        await _exec(
            db,
            "INSERT INTO commerce_checkout_sessions (id, tenant_id, merchant_id, seller_agent_id, serving_host, "
            "ucp_version, status, currency, state, revision, order_id, expires_at, created_at, updated_at, "
            "completed_at) VALUES (:id, :t, :m, :s, 'h.example', '2026-04-08', :status, :currency, "
            "CAST(:state AS jsonb), 1, :order_id, :exp, :created, :updated, :completed_at)",
            id=ids[name],
            t=uuid.UUID(tenant),
            m=merchant.merchant_id,
            s=merchant.seller_agent_id,
            status=status,
            currency=currency,
            state=json.dumps(state),
            order_id=f"ord_{name}" if status == "completed" else None,
            exp=now + timedelta(hours=1),
            created=midnight - timedelta(hours=2),
            updated=now if name in touched_today else completed_at or midnight - timedelta(hours=2),
            completed_at=completed_at,
        )
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        committed = await handlers.daily_committed_minor_units(
            session,
            tenant_id=uuid.UUID(tenant),
            merchant=merchant,
            currency="INR",
            exclude_session_id=ids["deciding"],
            now=now,
        )
    # Paid today plus the one link still open (issued yesterday); nothing else.
    assert committed == 100 + 400


@pytest.mark.asyncio
async def test_shopify_handoff_refuses_without_a_connected_store_or_artifacts(db, executor, monkeypatch):
    tenant = await _tenant(db)
    runtime = _runtime(monkeypatch, tenant, {"mode": "shopify_cart"})
    from core.commerce.ucp import store

    original = store.load_merchant_config_row

    async def _no_store(**kwargs):
        row = await original(**kwargs)
        row.source_connectors = []
        return row

    monkeypatch.setattr(store, "load_merchant_config_row", _no_store)
    session = await _ready(runtime, tenant)
    refused = await _complete(runtime, tenant, session["id"])
    assert refused.body["status"] == "ready_for_complete"
    assert refused.body["messages"][-1]["code"] == "payment_setup_incomplete"
    assert "continue_url" not in refused.body
    monkeypatch.setattr(store, "load_merchant_config_row", original)

    async def _no_artifacts(_merchant):
        return [], _config(tenant, {"mode": "shopify_cart"})

    runtime.arbiter_scope = _no_artifacts
    refused = await _complete(runtime, tenant, session["id"])
    assert refused.body["messages"][-1]["code"] == "arbiter_refused"
    assert (await _decisions(db, tenant))[0]["outcome"] == "refuse"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_handoff_only_binding_never_runs_the_executor_for_a_merchant_without_a_handoff(db, executor, monkeypatch):
    tenant = await _tenant(db)
    runtime = _runtime(monkeypatch, tenant, None)
    session = await _ready(runtime, tenant)
    refused = await _complete(runtime, tenant, session["id"], body=uf.complete_body(), handoff_only=True)
    assert refused.status == 200
    assert refused.body["status"] == "ready_for_complete"
    assert refused.body["messages"][-1]["code"] == "payment_handoff_unavailable"
    assert executor.calls == [] and await _decisions(db, tenant) == []


# --- Plural hosted -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plural_credentials_are_encrypted_and_tenant_and_merchant_scoped(db):
    from core.database import get_tenant_session

    tenant, other = await _tenant(db), await _tenant(db)
    stored = await _save_plural(tenant)
    assert stored.environment == "sandbox" and stored.credentials.base_url.startswith("https://")
    assert len(stored.webhook_key) == 32
    raw = await _rows(db, "SELECT credentials_encrypted, config FROM connector_configs WHERE tenant_id = :t", t=tenant)
    dumped = json.dumps(raw[0]["credentials_encrypted"]) + json.dumps(raw[0]["config"])
    for secret in ("secret-example-value", WEBHOOK_SECRET, "client-example"):
        assert secret not in dumped
    ref = plural_hosted.credential_ref_for(uf.fx.MERCHANT)
    async with get_tenant_session(uuid.UUID(other)) as session:
        assert (
            await plural_hosted.load_credentials(
                session, tenant_id=other, credential_ref=ref, merchant_id=uf.fx.MERCHANT
            )
            is None
        )
        assert await plural_hosted.load_by_webhook_key(session, tenant_id=other, webhook_key=stored.webhook_key) is None
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        assert (
            await plural_hosted.load_credentials(
                session, tenant_id=tenant, credential_ref=ref, merchant_id="another.example"
            )
            is None
        )
        again = await plural_hosted.save_credentials(
            session,
            tenant_id=tenant,
            merchant_id=uf.fx.MERCHANT,
            environment="sandbox",
            client_id="client-example-2",
            client_secret=None,
            webhook_secret=None,
        )
        assert again.config["webhook_key"] == stored.webhook_key
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        kept = await plural_hosted.load_credentials(
            session, tenant_id=tenant, credential_ref=ref, merchant_id=uf.fx.MERCHANT
        )
    assert kept is not None and kept.credentials.client_id == "client-example-2"
    assert (
        kept.credentials.client_secret == "secret-example-value" and kept.credentials.webhook_secret == WEBHOOK_SECRET
    )


@pytest.mark.asyncio
async def test_plural_handoff_creates_one_hosted_order_and_the_signed_webhook_creates_the_order(
    db, executor, hosted, kicks, monkeypatch
):
    tenant = await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant, buyer={"email": "buyer@merchant.example", "first_name": "Asha"})
    total = session["totals"][-1]["amount"]

    done = await _complete(runtime, tenant, session["id"])
    assert done.body["status"] == "requires_escalation", done.body
    assert done.body["continue_url"] == "https://pay.plural.example/checkout?token=v1-hosted-1"
    body = hosted.bodies("/checkout/v1/orders")[0]
    assert body["pre_auth"] is False and body["order_amount"] == {"value": total, "currency": "INR"}
    assert (
        body["callback_url"] == f"https://api.merchant.example/api/v1/payments/plural/return/{tenant}/{session['id']}"
    )
    assert body["purchase_details"]["customer"] == {"email_id": "buyer@merchant.example", "first_name": "Asha"}
    reference = body["merchant_order_reference"]
    assert reference.startswith("aoh-") and executor.calls == []

    # Idempotent: the same link, no second Plural order.
    again = await _complete(runtime, tenant, session["id"])
    assert again.body["continue_url"] == done.body["continue_url"] and len(hosted.orders) == 1
    # The checkout cannot change under an issued Plural order.
    changed = await handlers.run(
        "update_checkout",
        runtime,
        uf.request(tenant, uf.create_body(2, item_id=ITEM_ID, fulfillment=uf.SHIPPING)),
        session["id"],
    )
    assert changed.status == 409

    paid = _event("v1-hosted-1", reference, "PROCESSED", total)
    forged = _signed(paid, webhook_id="wh_forged", secret=base64.b64encode(b"not-the-merchant").decode())
    with pytest.raises(PluralWebhookError) as exc:
        await plural_hosted.apply_webhook(
            tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=forged
        )
    assert exc.value.code == "signature_invalid"
    stale = _signed(paid, webhook_id="wh_old", timestamp=int(time.time()) - 3600)
    with pytest.raises(PluralWebhookError) as exc:
        await plural_hosted.apply_webhook(
            tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=stale
        )
    assert exc.value.code == "timestamp_out_of_window"
    with pytest.raises(PluralWebhookError) as exc:
        await plural_hosted.apply_webhook(
            tenant_id=tenant, webhook_key="0" * 32, raw_body=paid, headers=_signed(paid, webhook_id="x")
        )
    assert exc.value.code == "unknown_endpoint"
    assert (await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])).body[
        "status"
    ] == "requires_escalation"

    applied = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=_signed(paid, webhook_id="wh_paid_1")
    )
    assert applied["result"] == "completed" and applied["applied"] is True
    replay = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=_signed(paid, webhook_id="wh_paid_1")
    )
    assert replay["result"] == "duplicate_event"
    redelivered = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=_signed(paid, webhook_id="wh_paid_2")
    )
    assert redelivered["result"] == "already_completed"

    orders = await _rows(
        db,
        "SELECT id, executor, provider_order_ref, total_minor_units FROM commerce_orders WHERE tenant_id = :t",
        t=tenant,
    )
    assert len(orders) == 1
    assert orders[0]["executor"] == "plural_hosted" and orders[0]["provider_order_ref"] == "v1-hosted-1"
    assert orders[0]["total_minor_units"] == total
    final = await handlers.run("get_checkout", runtime, uf.request(tenant), session["id"])
    assert final.body["status"] == "completed" and final.body["order"]["id"] == orders[0]["id"]
    assert "continue_url" not in final.body
    deliveries = await _rows(
        db, "SELECT order_id FROM commerce_order_webhook_deliveries WHERE tenant_id = :t", t=tenant
    )
    assert [d["order_id"] for d in deliveries] == [orders[0]["id"]]
    assert kicks == [(tenant, orders[0]["id"])]
    events = await _ledger(db, tenant)
    assert "commerce.ucp.order.created" in events and "commerce.payments.handoff.confirm" in events


@pytest.mark.asyncio
async def test_a_payment_for_another_amount_never_becomes_an_order(db, executor, hosted, kicks, monkeypatch):
    tenant = await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, session["id"])
    reference = hosted.bodies("/checkout/v1/orders")[0]["merchant_order_reference"]
    short = _event("v1-hosted-1", reference, "PROCESSED", 100)
    result = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=short, headers=_signed(short, webhook_id="wh_short")
    )
    assert result["result"] == "amount_mismatch" and result["applied"] is False
    assert await _rows(db, "SELECT id FROM commerce_orders WHERE tenant_id = :t", t=tenant) == []
    assert kicks == []


@pytest.mark.asyncio
async def test_the_poll_confirms_a_payment_whose_webhook_never_came_and_retires_a_failed_link(
    db, executor, hosted, kicks, monkeypatch
):
    tenant = await _tenant(db)
    await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    paid_session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, paid_session["id"])

    hosted.status = "PENDING"
    assert await plural_hosted.poll_open_handoffs(tenant_ids=[tenant]) == {"pending": 1}
    hosted.status = "PROCESSED"
    assert await plural_hosted.poll_open_handoffs(tenant_ids=[tenant]) == {"completed": 1}
    assert await plural_hosted.poll_open_handoffs(tenant_ids=[tenant]) == {}
    assert len(await _rows(db, "SELECT id FROM commerce_orders WHERE tenant_id = :t", t=tenant)) == 1

    failed_session = await _ready(runtime, tenant)
    first = await _complete(runtime, tenant, failed_session["id"])
    hosted.status = "FAILED"
    assert await plural_hosted.poll_open_handoffs(tenant_ids=[tenant]) == {"link_retired": 1}
    retired = await handlers.run("get_checkout", runtime, uf.request(tenant), failed_session["id"])
    assert retired.body["status"] == "ready_for_complete" and "continue_url" not in retired.body
    # The next Complete issues a fresh link under a fresh decision.
    hosted.status = "CREATED"
    second = await _complete(runtime, tenant, failed_session["id"])
    assert second.body["continue_url"] != first.body["continue_url"]
    assert len(hosted.orders) == 3


@pytest.mark.asyncio
async def test_plural_refusal_returns_the_session_to_ready_with_a_message(db, executor, hosted, monkeypatch):
    tenant = await _tenant(db)
    await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant)
    hosted.order_http_status = 422
    refused = await _complete(runtime, tenant, session["id"])
    assert refused.body["status"] == "ready_for_complete"
    assert refused.body["messages"][-1]["code"] == "payment_link_refused"
    assert "continue_url" not in refused.body


@pytest.mark.asyncio
async def test_stale_reservation_is_released(db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    tenant = await _tenant(db)
    session_id = f"chk_{uuid.uuid4().hex}"
    state = {
        "handoff": {"mode": "plural_hosted", "status": "creating", "reference": "aoh-x"},
        "handoff_refs": ["aoh-x"],
    }
    old = datetime.now(UTC) - timedelta(minutes=30)
    await _exec(
        db,
        "INSERT INTO commerce_checkout_sessions (id, tenant_id, merchant_id, seller_agent_id, serving_host, "
        "ucp_version, status, currency, state, revision, expires_at, created_at, updated_at) "
        "VALUES (:id, :t, 'm', 's', 'h.example', '2026-04-08', 'complete_in_progress', 'INR', "
        "CAST(:state AS jsonb), 1, :exp, :old, :old)",
        id=session_id,
        t=uuid.UUID(tenant),
        state=json.dumps(state),
        exp=datetime.now(UTC) + timedelta(hours=1),
        old=old,
    )
    assert await plural_hosted.release_stale_reservations(tenant) == 1
    row = (await _rows(db, "SELECT status, state FROM commerce_checkout_sessions WHERE id = :i", i=session_id))[0]
    assert row["status"] == "ready_for_complete" and row["state"]["handoff"]["status"] == "failed"
    assert row["state"]["handoff_refs"] == ["aoh-x"]


# --- the merchant gets the order -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_email_goes_to_this_tenants_active_admins_only_and_orders_list_is_tenant_scoped(
    db, executor, hosted, kicks, monkeypatch
):
    from api.v1 import commerce_payments
    from core import email as core_email
    from core.commerce.order_notifications import notify_merchant_of_order

    tenant, other = await _tenant(db), await _tenant(db)
    for tid, email, role, status in (
        (tenant, "owner@merchant.example", "admin", "active"),
        (tenant, "former@merchant.example", "admin", "suspended"),
        (tenant, "analyst@merchant.example", "analyst", "active"),
        (other, "other-owner@merchant.example", "admin", "active"),
    ):
        await _exec(
            db,
            "INSERT INTO users (id, tenant_id, email, name, role, status, mfa_enabled) "
            "VALUES (:id, :t, :e, 'Synthetic', :r, :s, false)",
            id=uuid.uuid4(),
            t=uuid.UUID(tid),
            e=email,
            r=role,
            s=status,
        )
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant, buyer={"email": "buyer@merchant.example", "phone_number": "+919800000000"})
    await _complete(runtime, tenant, session["id"])
    body = hosted.bodies("/checkout/v1/orders")[0]
    paid = _event("v1-hosted-1", body["merchant_order_reference"], "PROCESSED", body["order_amount"]["value"])
    result = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=_signed(paid, webhook_id="wh_1")
    )
    order_id = result["order_id"]

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        core_email, "send_email", lambda to, subject, html, **_: sent.append((to, subject, html)) or True
    )
    assert await notify_merchant_of_order(tenant, order_id) == 1
    assert [s[0] for s in sent] == ["owner@merchant.example"]
    assert order_id in sent[0][1] and "buyer@merchant.example" in sent[0][2] and "+919800000000" in sent[0][2]
    # The heading is the method and option; the order's shipping fee is shown once, on its own line.
    assert "<li>Ship - Standard<br>1 MG Road<br>Pune, 411001<br>IN</li>" in sent[0][2]
    assert "<p>Shipping fee: Rs 50.00</p>" in sent[0][2] and sent[0][2].count("Rs 50.00") == 1
    assert "commerce.order.notification" in await _ledger(db, tenant)

    mine = await commerce_payments.list_orders(merchant_id=None, limit=25, offset=0, tenant_id=tenant)
    assert [o["id"] for o in mine["orders"]] == [order_id]
    view = mine["orders"][0]
    assert view["buyer"] == {"email": "buyer@merchant.example", "phone_number": "+919800000000"}
    assert view["payment"] == {"status": "paid", "provider": "plural", "provider_order_ref_present": True}
    assert view["items"][0]["quantity"] == 1 and view["total"].startswith("Rs ")
    assert [d["address"] for d in view["delivery"]] == [["1 MG Road", "Pune, 411001", "IN"]]
    assert view["delivery"] == [
        {"method": "shipping", "option": "Standard", "address": ["1 MG Road", "Pune, 411001", "IN"]}
    ]
    assert view["shipping"] == "Rs 50.00"
    assert mine["next_offset"] is None
    theirs = await commerce_payments.list_orders(merchant_id=None, limit=25, offset=0, tenant_id=other)
    assert theirs["orders"] == []
    filtered = await commerce_payments.list_orders(merchant_id="another.example", limit=25, offset=0, tenant_id=tenant)
    assert filtered["orders"] == []
