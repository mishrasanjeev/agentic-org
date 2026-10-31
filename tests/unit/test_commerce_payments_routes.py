# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The merchant payment routes, the orders list, the buyer's return page and the Plural webhook.

The database, the Plural credential store and the ledger are replaced; the
handlers, the request models, the settings parser and the page rendering are
the real ones.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.dialects import postgresql

from api.v1 import commerce_payments as cp
from core.commerce import plural_hosted
from core.commerce.oacp_merchant_config import merchant_config_id
from core.config import settings

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
MERCHANT = "merchant-1"
SELLER = "seller-1"
SCOPE = "commerce.merchant_config.write"
API_BASE = "https://api.shop.test"
WEBHOOK_KEY = "b" * 32
CREDENTIAL_REF = plural_hosted.credential_ref_for(MERCHANT)
SHOPIFY = [{"connector_type": "shopify", "shop_domain": "tea-corner.myshopify.com", "enabled": True}]
# Stand-in credential values; built at run time so they never read as real secrets.
CLIENT_SECRET = "client-secret-" + "x" * 8
WEBHOOK_SECRET = "webhook-secret-" + "y" * 8
BASE = "/api/v1/commerce/runtime/merchant-configs/merchant-1/payments"


class FakeSession:
    """Answers the handlers' queries from queues and keeps every statement it was given."""

    def __init__(self) -> None:
        self.scalar_results: list[list[object]] = []
        self.execute_results: list[object] = []
        self.statements: list[object] = []
        self.opened: list[uuid.UUID] = []

    async def scalars(self, statement):
        self.statements.append(statement)
        rows = list(self.scalar_results.pop(0)) if self.scalar_results else []
        return SimpleNamespace(all=lambda: rows, first=lambda: rows[0] if rows else None)

    async def execute(self, statement):
        self.statements.append(statement)
        row = self.execute_results.pop(0) if self.execute_results else None
        return SimpleNamespace(scalar_one_or_none=lambda: row)


def _sql(statement) -> tuple[str, dict]:
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


@pytest.fixture(autouse=True)
def _public_origin(monkeypatch):
    monkeypatch.setattr(settings, "public_api_base_url", API_BASE)
    monkeypatch.delenv("AGENTICORG_APP_URL", raising=False)


@pytest.fixture()
def db(monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def tenant_session(tenant):
        session.opened.append(tenant)
        yield session

    monkeypatch.setattr(cp, "get_tenant_session", tenant_session)
    return session


@pytest.fixture()
def ledger(monkeypatch):
    events: list[dict] = []

    async def record(session, **kwargs):
        events.append({"session": session, **kwargs})

    async def record_own(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(cp, "record_commerce_event", record)
    monkeypatch.setattr(cp, "record_commerce_event_in_own_transaction", record_own)
    return events


@pytest.fixture()
def stored(monkeypatch):
    """The merchant's saved Plural credentials row (``value`` is what ``credentials_row`` returns)."""
    holder = SimpleNamespace(value=None, calls=[])

    async def credentials_row(session, *, tenant_id, merchant_id):
        holder.calls.append({"tenant_id": tenant_id, "merchant_id": merchant_id})
        return holder.value

    monkeypatch.setattr(plural_hosted, "credentials_row", credentials_row)
    return holder


def _client(scopes=(SCOPE,), tenant: str = str(TENANT)) -> httpx.AsyncClient:
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.scopes = list(scopes)
        request.state.tenant_id = tenant
        return await call_next(request)

    app.include_router(cp.merchant_router, prefix="/api/v1")
    app.include_router(cp.public_router, prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _config(*, policy=None, connectors=None):
    return SimpleNamespace(
        config_id="mcc_1",
        merchant_id=MERCHANT,
        seller_agent_id=SELLER,
        provider_policy=policy,
        source_connectors=connectors,
        updated_at=None,
    )


def _credentials(*, webhook_secret_set: bool = True):
    return SimpleNamespace(
        connector_name=CREDENTIAL_REF,
        config={
            "merchant_id": MERCHANT,
            "environment": "sandbox",
            "webhook_key": WEBHOOK_KEY,
            "webhook_secret_set": webhook_secret_set,
            "configured_at": "2026-10-28T10:00:00Z",
        },
        health_status="unknown",
        last_health_check=None,
    )


# --- access -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_merchant_routes_need_the_config_scope_or_admin(db, stored):
    async with _client(scopes=["agents:read"]) as client:
        assert (await client.get(BASE)).status_code == 403
        assert (await client.get("/api/v1/commerce/runtime/orders")).status_code == 403
        assert (await client.put(BASE, json={"mode": "off"})).status_code == 403
    assert db.opened == []
    db.scalar_results = [[_config()]]
    async with _client(scopes=["agenticorg:admin"]) as client:
        assert (await client.get(BASE)).status_code == 200


@pytest.mark.asyncio
async def test_a_tenant_context_that_is_not_a_uuid_is_refused_before_any_read(db, stored):
    async with _client(tenant="tenant-one") as client:
        response = await client.get(BASE)
    assert response.status_code == 400 and response.json()["detail"] == "tenant context is not a UUID"
    assert db.opened == []


# --- GET payment settings ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_reports_the_saved_mode_and_what_each_provider_needs(db, stored):
    db.scalar_results = [[_config(policy={"payments": {"mode": "shopify_cart"}}, connectors=SHOPIFY)]]
    stored.value = _credentials()
    async with _client() as client:
        response = await client.get(BASE, params={"seller_agent_id": SELLER})
    assert response.status_code == 200
    body = response.json()
    assert body["merchant_id"] == MERCHANT and body["mode"] == "shopify_cart"
    assert body["shopify"] == {"shop_domain": "tea-corner.myshopify.com", "ready": True}
    assert body["plural"] == {
        "configured": True,
        "credential_ref": CREDENTIAL_REF,
        "environment": "sandbox",
        "webhook_secret_set": True,
        "webhook_url": f"{API_BASE}/api/v1/payments/plural/webhooks/{TENANT}/{WEBHOOK_KEY}",
        "configured_at": "2026-10-28T10:00:00Z",
        "last_test": None,
        "secret_values_redacted": True,
    }
    assert body["links_available"] is True and body["agenticorg_collects_payment"] is False
    assert db.opened == [TENANT]
    assert stored.calls == [{"tenant_id": str(TENANT), "merchant_id": MERCHANT}]
    sql, params = _sql(db.statements[0])
    # A read takes no row lock, and the seller agent narrows the lookup to its own config.
    assert "FOR UPDATE" not in sql
    assert params["merchant_id_1"] == MERCHANT
    assert params["config_id_1"] == merchant_config_id(str(TENANT), MERCHANT, SELLER)


@pytest.mark.asyncio
async def test_get_without_settings_credentials_or_public_origin_reports_off(db, stored, monkeypatch):
    monkeypatch.setattr(settings, "public_api_base_url", "")
    db.scalar_results = [[_config()]]
    async with _client() as client:
        body = (await client.get(BASE)).json()
    assert body["mode"] == "off"
    assert body["shopify"] == {"shop_domain": None, "ready": False}
    assert body["plural"] == {"configured": False}
    assert body["links_available"] is False
    assert "config_id_1" not in _sql(db.statements[0])[1]


@pytest.mark.asyncio
async def test_get_reads_a_malformed_payments_object_as_off(db, stored):
    # plural_hosted without a credential reference is unusable: fail closed.
    db.scalar_results = [[_config(policy={"payments": {"mode": "plural_hosted"}}, connectors=SHOPIFY)]]
    async with _client() as client:
        assert (await client.get(BASE)).json()["mode"] == "off"


@pytest.mark.asyncio
async def test_get_for_an_unknown_merchant_is_404(db, stored):
    db.scalar_results = [[]]
    async with _client() as client:
        response = await client.get(BASE)
    assert response.status_code == 404 and stored.calls == []


# --- PUT payment settings ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_shopify_cart_with_a_connected_store_saves_and_records(db, stored, ledger):
    row = _config(policy={"payments": {"mode": "plural_hosted"}, "refunds": "manual"}, connectors=SHOPIFY)
    db.scalar_results = [[row]]
    async with _client() as client:
        response = await client.put(BASE, json={"mode": "shopify_cart", "seller_agent_id": SELLER})
    assert response.status_code == 200 and response.json()["mode"] == "shopify_cart"
    assert row.provider_policy == {"payments": {"mode": "shopify_cart"}, "refunds": "manual"}
    assert isinstance(row.updated_at, datetime) and row.updated_at.tzinfo is UTC
    # A write locks the config row.
    assert "FOR UPDATE" in _sql(db.statements[0])[0]
    assert len(ledger) == 1
    event = ledger[0]
    assert event["session"] is db and event["event"] == cp.SETTINGS_EVENT and event["outcome"] == "success"
    assert event["tenant_id"] == str(TENANT) and event["merchant_id"] == MERCHANT
    assert event["seller_agent_id"] == SELLER and event["buyer_agent_id"] is None
    assert event["payload"] == {"merchant_id": MERCHANT, "mode": "shopify_cart"}
    assert event["resource_id"] == "mcc_1"


@pytest.mark.parametrize(
    "connectors",
    [
        None,
        [{**SHOPIFY[0], "enabled": False}],
        [{"connector_type": "shopify", "shop_domain": "tea-corner.example.com"}],
        [{"connector_type": "woocommerce", "shop_domain": "tea-corner.myshopify.com"}],
    ],
)
@pytest.mark.asyncio
async def test_put_shopify_cart_without_a_usable_store_is_refused(db, stored, ledger, connectors):
    row = _config(policy={"refunds": "manual"}, connectors=connectors)
    db.scalar_results = [[row]]
    async with _client() as client:
        response = await client.put(BASE, json={"mode": "shopify_cart"})
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "shopify_store_not_connected",
        "message": "Connect your Shopify store first; buyers pay in its checkout.",
    }
    assert row.provider_policy == {"refunds": "manual"} and row.updated_at is None
    assert ledger == []


@pytest.mark.parametrize(
    ("credentials", "code"),
    [
        (None, "plural_credentials_missing"),
        (_credentials(webhook_secret_set=False), "plural_webhook_secret_missing"),
    ],
)
@pytest.mark.asyncio
async def test_put_plural_hosted_needs_saved_credentials_and_a_webhook_secret(db, stored, ledger, credentials, code):
    row = _config(policy={"payments": {"mode": "shopify_cart"}}, connectors=SHOPIFY)
    db.scalar_results = [[row]]
    stored.value = credentials
    async with _client() as client:
        response = await client.put(BASE, json={"mode": "plural_hosted"})
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": code, "message": cp._SETUP_MESSAGES[code]}
    assert row.provider_policy == {"payments": {"mode": "shopify_cart"}}
    assert ledger == []


@pytest.mark.asyncio
async def test_put_plural_hosted_saves_a_reference_the_settings_parser_accepts(db, stored, ledger):
    row = _config(policy=None)
    db.scalar_results = [[row]]
    stored.value = _credentials()
    async with _client() as client:
        response = await client.put(BASE, json={"mode": "plural_hosted"})
    assert response.status_code == 200
    assert row.provider_policy == {"payments": {"mode": "plural_hosted", "plural": {"credential_ref": CREDENTIAL_REF}}}
    body = response.json()
    # The saved object reads back as plural_hosted (it is not discarded as malformed).
    assert body["mode"] == "plural_hosted" and body["plural"]["webhook_secret_set"] is True
    assert stored.calls == [{"tenant_id": str(TENANT), "merchant_id": MERCHANT}] * 2
    assert [e["payload"]["mode"] for e in ledger] == ["plural_hosted"]


@pytest.mark.asyncio
async def test_put_off_removes_the_payments_object_and_keeps_the_rest_of_the_policy(db, stored, ledger):
    row = _config(policy={"payments": {"mode": "shopify_cart"}, "refunds": "manual"}, connectors=SHOPIFY)
    db.scalar_results = [[row]]
    async with _client() as client:
        response = await client.put(BASE, json={"mode": "off"})
    assert response.status_code == 200 and response.json()["mode"] == "off"
    assert row.provider_policy == {"refunds": "manual"}
    assert [e["payload"] for e in ledger] == [{"merchant_id": MERCHANT, "mode": "off"}]


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "stripe"},
        {},
        {"mode": "off", "collect_payment": True},
        {"mode": "off", "seller_agent_id": ""},
    ],
)
@pytest.mark.asyncio
async def test_put_refuses_a_malformed_request_without_reading_anything(db, stored, ledger, body):
    async with _client() as client:
        assert (await client.put(BASE, json=body)).status_code == 422
    assert db.opened == [] and ledger == []


@pytest.mark.asyncio
async def test_put_for_an_unknown_merchant_is_404(db, stored, ledger):
    db.scalar_results = [[]]
    async with _client() as client:
        assert (await client.put(BASE, json={"mode": "off"})).status_code == 404
    assert ledger == []


# --- PUT plural-credentials -------------------------------------------------------------------------


@pytest.fixture()
def saved(monkeypatch):
    calls: list[dict] = []
    outcome = SimpleNamespace(error=None)

    async def save_credentials(session, **kwargs):
        calls.append({"session": session, **kwargs})
        if outcome.error is not None:
            raise outcome.error
        return SimpleNamespace(connector_name=CREDENTIAL_REF)

    monkeypatch.setattr(plural_hosted, "save_credentials", save_credentials)
    outcome.calls = calls
    return outcome


@pytest.mark.asyncio
async def test_put_credentials_trims_the_values_and_never_echoes_a_secret(db, stored, ledger, saved):
    db.scalar_results = [[_config()]]
    stored.value = _credentials()
    body = {"client_id": "  client-1  ", "client_secret": f"  {CLIENT_SECRET}  ", "webhook_secret": "   "}
    async with _client() as client:
        response = await client.put(f"{BASE}/plural-credentials", json=body)
    assert response.status_code == 200
    assert saved.calls == [
        {
            "session": db,
            "tenant_id": str(TENANT),
            "merchant_id": MERCHANT,
            "environment": "sandbox",
            "client_id": "client-1",
            "client_secret": CLIENT_SECRET,
            # A blank secret keeps the stored one.
            "webhook_secret": None,
        }
    ]
    assert response.json()["plural"]["configured"] is True
    assert CLIENT_SECRET not in response.text
    # Reading the config does not lock it.
    assert "FOR UPDATE" not in _sql(db.statements[0])[0]
    assert len(ledger) == 1
    event = ledger[0]
    assert event["event"] == cp.CREDENTIALS_EVENT and event["outcome"] == "success"
    assert event["resource_id"] == CREDENTIAL_REF and event["seller_agent_id"] is None
    assert event["payload"] == {
        "merchant_id": MERCHANT,
        "credential_ref": CREDENTIAL_REF,
        "environment": "sandbox",
        "credential_values_redacted": True,
    }


@pytest.mark.asyncio
async def test_put_credentials_passes_production_and_both_secrets(db, stored, ledger, saved):
    db.scalar_results = [[_config()]]
    body = {
        "environment": "production",
        "client_id": "client-1",
        "client_secret": CLIENT_SECRET,
        "webhook_secret": WEBHOOK_SECRET,
    }
    async with _client() as client:
        response = await client.put(f"{BASE}/plural-credentials", json=body)
    assert response.status_code == 200
    call = saved.calls[0]
    assert call["environment"] == "production" and call["webhook_secret"] == WEBHOOK_SECRET
    assert WEBHOOK_SECRET not in response.text and CLIENT_SECRET not in response.text
    assert ledger[0]["payload"]["environment"] == "production"


@pytest.mark.asyncio
async def test_put_credentials_turns_a_store_refusal_into_422_without_a_ledger_row(db, stored, ledger, saved):
    db.scalar_results = [[_config()]]
    saved.error = ValueError("client secret is required for new credentials")
    async with _client() as client:
        response = await client.put(f"{BASE}/plural-credentials", json={"client_id": "client-1"})
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "plural_credentials_invalid",
        "message": "client secret is required for new credentials",
    }
    assert ledger == []


@pytest.mark.parametrize(
    "body",
    [
        {"environment": "staging", "client_id": "client-1"},
        {"client_id": ""},
        {"client_id": "client-1", "api_key": "k"},
        {"client_id": "c" * 201},
    ],
)
@pytest.mark.asyncio
async def test_put_credentials_refuses_a_malformed_request(db, stored, ledger, saved, body):
    async with _client() as client:
        assert (await client.put(f"{BASE}/plural-credentials", json=body)).status_code == 422
    assert db.opened == [] and saved.calls == [] and ledger == []


# --- POST plural-credentials/test -------------------------------------------------------------------


@pytest.fixture()
def plural_check(monkeypatch):
    state = SimpleNamespace(stored=None, result=(True, "token_issued"), loads=[], checks=[])

    async def load_credentials(session, *, tenant_id, credential_ref, merchant_id):
        state.loads.append({"tenant_id": tenant_id, "credential_ref": credential_ref, "merchant_id": merchant_id})
        return state.stored

    async def check_credentials(credentials):
        state.checks.append(credentials)
        return state.result

    monkeypatch.setattr(plural_hosted, "load_credentials", load_credentials)
    monkeypatch.setattr(plural_hosted, "check_credentials", check_credentials)
    return state


def _stored_credentials(environment: str = "sandbox"):
    return SimpleNamespace(credentials=object(), environment=environment, credential_ref=CREDENTIAL_REF)


@pytest.mark.asyncio
async def test_the_credential_test_without_saved_credentials_is_404_and_calls_nothing(db, stored, ledger, plural_check):
    db.scalar_results = [[_config()]]
    async with _client() as client:
        response = await client.post(f"{BASE}/plural-credentials/test")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "plural_credentials_missing"
    assert plural_check.loads == [{"tenant_id": str(TENANT), "credential_ref": CREDENTIAL_REF, "merchant_id": MERCHANT}]
    assert plural_check.checks == [] and ledger == []


@pytest.mark.asyncio
async def test_a_passing_credential_test_marks_the_row_healthy_and_records_it(db, stored, ledger, plural_check):
    db.scalar_results = [[_config()]]
    plural_check.stored = _stored_credentials()
    row = _credentials()
    stored.value = row
    async with _client() as client:
        response = await client.post(f"{BASE}/plural-credentials/test")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["code"] == "token_issued" and body["environment"] == "sandbox"
    assert body["tested_at"].endswith("Z") and "+00:00" not in body["tested_at"]
    assert plural_check.checks == [plural_check.stored.credentials]
    assert row.config["last_test"] == {"ok": True, "code": "token_issued", "at": body["tested_at"]}
    # The rest of the stored config is kept.
    assert row.config["webhook_key"] == WEBHOOK_KEY and row.config["webhook_secret_set"] is True
    assert row.health_status == "healthy" and isinstance(row.last_health_check, datetime)
    # One session to read the credentials, another (after the provider call) to record the result.
    assert db.opened == [TENANT, TENANT]
    assert len(ledger) == 1
    event = ledger[0]
    assert event["event"] == cp.TEST_EVENT and event["outcome"] == "success"
    assert event["payload"] == {"merchant_id": MERCHANT, "environment": "sandbox", "result": "token_issued"}
    assert event["resource_id"] == CREDENTIAL_REF


@pytest.mark.asyncio
async def test_a_refused_credential_test_marks_the_row_unhealthy(db, stored, ledger, plural_check):
    db.scalar_results = [[_config()]]
    plural_check.stored = _stored_credentials("production")
    plural_check.result = (False, "plural_refused:invalid_client")
    row = _credentials()
    stored.value = row
    async with _client() as client:
        body = (await client.post(f"{BASE}/plural-credentials/test")).json()
    assert body["ok"] is False and body["code"] == "plural_refused:invalid_client"
    assert body["environment"] == "production"
    assert row.health_status == "unhealthy" and row.config["last_test"]["ok"] is False
    assert ledger[0]["outcome"] == "refused" and ledger[0]["payload"]["result"] == "plural_refused:invalid_client"


@pytest.mark.asyncio
async def test_a_credential_test_still_records_when_the_row_is_gone(db, stored, ledger, plural_check):
    db.scalar_results = [[_config()]]
    plural_check.stored = _stored_credentials()
    stored.value = None
    async with _client() as client:
        response = await client.post(f"{BASE}/plural-credentials/test")
    assert response.status_code == 200 and response.json()["ok"] is True
    assert [e["event"] for e in ledger] == [cp.TEST_EVENT]


# --- GET orders -------------------------------------------------------------------------------------


ORDERS = "/api/v1/commerce/runtime/orders"


def _order(order_id: str, checkout_id: str, **overrides):
    values = {
        "id": order_id,
        "merchant_id": MERCHANT,
        "checkout_session_id": checkout_id,
        "created_at": datetime(2026, 10, 28, 9, 30, tzinfo=UTC),
        "currency": "INR",
        "total_minor_units": 44900,
        "snapshot": {
            "line_items": [
                {
                    "item": {"title": "Assam tea", "price": 22450},
                    "quantity": {"total": 2},
                    "status": "processing",
                }
            ],
            "permalink_url": f"https://tea-corner.shops.example/orders/{order_id}",
        },
        "executor": "plural_hosted",
        "provider_order_ref": "v1-pl-1",
        "platform_profile_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _checkout(checkout_id: str, state: dict):
    return SimpleNamespace(id=checkout_id, state=state)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 100_001}])
@pytest.mark.asyncio
async def test_orders_refuse_a_page_out_of_range_without_reading(db, params):
    async with _client() as client:
        response = await client.get(ORDERS, params=params)
    assert response.status_code == 422 and db.opened == []


@pytest.mark.asyncio
async def test_orders_page_newest_first_within_the_tenant_and_merchant(db):
    orders = [_order("ord_3", "chk_3"), _order("ord_2", "chk_2"), _order("ord_1", "chk_1")]
    db.scalar_results = [orders, [_checkout("chk_3", {}), _checkout("chk_2", {})]]
    async with _client() as client:
        response = await client.get(ORDERS, params={"merchant_id": MERCHANT, "limit": 2, "offset": 4})
    assert response.status_code == 200
    body = response.json()
    assert [o["id"] for o in body["orders"]] == ["ord_3", "ord_2"]
    assert body["limit"] == 2 and body["offset"] == 4 and body["next_offset"] == 6
    assert db.opened == [TENANT]
    sql, params = _sql(db.statements[0])
    assert "ORDER BY commerce_orders.created_at DESC, commerce_orders.id DESC" in sql
    assert params["tenant_id_1"] == TENANT and params["merchant_id_1"] == MERCHANT
    # One extra row is read to know whether another page exists.
    assert params["param_1"] == 3 and params["param_2"] == 4
    sql, params = _sql(db.statements[1])
    assert "commerce_checkout_sessions" in sql
    assert params["tenant_id_1"] == TENANT and params["id_1"] == ["chk_3", "chk_2"]


@pytest.mark.asyncio
async def test_the_last_page_has_no_next_offset_and_every_merchant_is_listed(db):
    db.scalar_results = [[_order("ord_1", "chk_1")], [_checkout("chk_1", {})]]
    async with _client() as client:
        body = (await client.get(ORDERS)).json()
    assert body["limit"] == 25 and body["offset"] == 0 and body["next_offset"] is None
    assert [o["id"] for o in body["orders"]] == ["ord_1"]
    sql, params = _sql(db.statements[0])
    assert "commerce_orders.merchant_id =" not in sql and params["tenant_id_1"] == TENANT
    assert params["param_1"] == 26


@pytest.mark.asyncio
async def test_no_orders_skips_the_checkout_lookup(db):
    db.scalar_results = [[]]
    async with _client() as client:
        body = (await client.get(ORDERS)).json()
    assert body == {"orders": [], "limit": 25, "offset": 0, "next_offset": None}
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_an_order_shows_the_buyer_contact_delivery_items_total_and_surface(db):
    state = {
        "buyer": {
            "first_name": "Asha",
            "last_name": "Rao",
            "email": "asha@example.test",
            "phone_number": "+910000000000",
            "shipping_address": {"line1": "1 Tea Street"},
            "payment_token": "tok_1",
        },
        "platform": {"surface": "telegram"},
    }
    snapshot = {
        **_order("ord_1", "chk_1").snapshot,
        "currency": "INR",
        "totals": [{"type": "subtotal", "amount": 44900}, {"type": "fulfillment", "amount": 4900}],
        "fulfillment": {
            "expectations": [
                {
                    "method_type": "shipping",
                    "description": "Standard delivery",
                    "destination": {
                        "first_name": "Asha",
                        "last_name": "Rao",
                        "street_address": "12 MG Road",
                        "address_locality": "Bengaluru",
                        "address_region": "KA",
                        "postal_code": "560001",
                        "address_country": "IN",
                    },
                }
            ],
            "events": [],
        },
    }
    db.scalar_results = [[_order("ord_1", "chk_1", snapshot=snapshot)], [_checkout("chk_1", state)]]
    async with _client() as client:
        order = (await client.get(ORDERS)).json()["orders"][0]
    assert order == {
        "id": "ord_1",
        "merchant_id": MERCHANT,
        "checkout_id": "chk_1",
        "created_at": "2026-10-28T09:30:00Z",
        "currency": "INR",
        "total_minor_units": 44900,
        "total": "Rs 449.00",
        "items": [{"title": "Assam tea", "quantity": 2, "unit_price": "Rs 224.50", "status": "processing"}],
        # Only the contact fields the buyer gave; nothing else from the checkout state.
        "buyer": {
            "first_name": "Asha",
            "last_name": "Rao",
            "email": "asha@example.test",
            "phone_number": "+910000000000",
        },
        # Where the order goes, from the order itself: the merchant ships from this.
        "delivery": [
            {
                "method": "shipping",
                "option": "Standard delivery",
                "address": ["Asha Rao", "12 MG Road", "Bengaluru, KA, 560001", "IN"],
            }
        ],
        "shipping": "Rs 49.00",
        "payment": {"status": "paid", "provider": "plural", "provider_order_ref_present": True},
        "surface": "Telegram",
        "permalink_url": "https://tea-corner.shops.example/orders/ord_1",
    }


@pytest.mark.asyncio
async def test_an_order_shipped_twice_gives_its_one_shipping_fee_at_order_level(db):
    def shipped(option: str, street: str) -> dict:
        return {
            "method_type": "shipping",
            "description": option,
            "destination": {"first_name": "Asha", "street_address": street, "address_country": "IN"},
        }

    snapshot = {
        **_order("ord_2", "chk_2").snapshot,
        "currency": "INR",
        # Two shipping destinations at Rs 49 each: one fulfillment total for the order.
        "totals": [{"type": "subtotal", "amount": 44900}, {"type": "fulfillment", "amount": 9800}],
        "fulfillment": {"expectations": [shipped("Standard", "12 MG Road"), shipped("Express", "4 Park Street")]},
    }
    db.scalar_results = [[_order("ord_2", "chk_2", snapshot=snapshot)], [_checkout("chk_2", {})]]
    async with _client() as client:
        order = (await client.get(ORDERS)).json()["orders"][0]
    assert order["shipping"] == "Rs 98.00"
    # Each delivery is its method, option and address: never a fee of its own.
    assert order["delivery"] == [
        {"method": "shipping", "option": "Standard", "address": ["Asha", "12 MG Road", "IN"]},
        {"method": "shipping", "option": "Express", "address": ["Asha", "4 Park Street", "IN"]},
    ]


@pytest.mark.asyncio
async def test_an_order_without_its_checkout_still_lists(db):
    order = _order(
        "ord_9",
        "chk_gone",
        created_at=None,
        executor="manual_invoice",
        provider_order_ref=None,
        currency="USD",
        total_minor_units=1250,
        snapshot={"line_items": [{"item": {"title": "Mug"}, "quantity": {"total": 1}}]},
    )
    pine = _order("ord_8", "chk_8", executor="plural_pine")
    db.scalar_results = [[order, pine], []]
    async with _client() as client:
        listed = (await client.get(ORDERS)).json()["orders"]
    first = listed[0]
    assert first["created_at"] is None and first["buyer"] == {} and first["surface"] == "UCP platform"
    assert first["delivery"] == [] and first["shipping"] is None
    assert first["total"] == "USD 12.50" and first["permalink_url"] is None
    assert first["items"] == [{"title": "Mug", "quantity": 1, "unit_price": "", "status": None}]
    assert first["payment"] == {"status": "paid", "provider": "manual_invoice", "provider_order_ref_present": False}
    assert listed[1]["payment"]["provider"] == "plural"


# --- the buyer's return page ------------------------------------------------------------------------


RETURN = f"/api/v1/payments/plural/return/{TENANT}/chk_1"
HANDOFF_STATE = {
    "handoff": {"mode": "plural_hosted", "status": "issued", "reference": "ref_1"},
    "buyer": {"email": "asha@example.test"},
}


@pytest.fixture()
def refresh(monkeypatch):
    calls: list[dict] = []

    async def refresh_handoff(**kwargs):
        calls.append(kwargs)
        return "completed"

    monkeypatch.setattr(plural_hosted, "refresh_handoff", refresh_handoff)
    return calls


def _session_row(status: str, *, state=None, order_id: str | None = None):
    return SimpleNamespace(
        id="chk_1",
        status=status,
        order_id=order_id,
        merchant_id=MERCHANT,
        state=HANDOFF_STATE if state is None else state,
    )


def _assert_private_page(response):
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex"
    assert "asha@example.test" not in response.text


@pytest.mark.parametrize(
    ("status", "title", "line"),
    [
        ("completed", "Payment received", "Your order ord_1 is confirmed with the shop."),
        ("canceled", "Checkout canceled", "This checkout was canceled."),
        ("ready_for_complete", "Payment not completed", "ask for a new payment link"),
        ("incomplete", "Confirming your payment", "Refresh this page in a minute"),
    ],
)
@pytest.mark.asyncio
async def test_the_return_page_shows_the_checkout_outcome(db, refresh, status, title, line):
    db.execute_results = [_session_row(status, order_id="ord_1")]
    async with _client() as client:
        response = await client.get(RETURN)
    assert response.status_code == 200
    assert f"<title>{title}</title>" in response.text and line in response.text
    _assert_private_page(response)
    assert refresh == []
    sql, params = _sql(db.statements[0])
    assert "commerce_checkout_sessions" in sql
    assert params["tenant_id_1"] == TENANT and params["id_1"] == "chk_1"


@pytest.mark.asyncio
async def test_a_waiting_checkout_asks_plural_and_shows_the_fresh_status(db, refresh):
    waiting = _session_row("requires_escalation")
    db.execute_results = [waiting, _session_row("completed", order_id="ord_7")]
    async with _client() as client:
        response = await client.get(RETURN)
    assert refresh == [{"tenant_id": str(TENANT), "session_row": waiting, "source": "return", "report_untouched": True}]
    assert "Payment received" in response.text and "ord_7" in response.text
    assert db.opened == [TENANT, TENANT]


@pytest.mark.parametrize(
    ("current_link", "title"),
    [
        ("link_retired", "Payment not completed"),
        ("not_attempted", "Payment not completed"),
        ("pending", "Confirming your payment"),
        ("status_unavailable", "Confirming your payment"),
    ],
)
@pytest.mark.asyncio
async def test_a_declined_payment_on_the_newest_link_is_shown_even_while_an_earlier_link_waits(
    db, monkeypatch, current_link, title
):
    async def refresh_handoff(**kwargs):
        return current_link

    monkeypatch.setattr(plural_hosted, "refresh_handoff", refresh_handoff)
    # The buyer was declined on link B; link A, issued earlier and still payable, is the checkout's link again.
    state = {
        "handoff": {"mode": "plural_hosted", "status": "issued", "reference": "ref_a"},
        "handoff_refs": ["ref_a", "ref_b"],
        "handoff_orders": {
            "ref_a": {"status": "issued", "issued_at": "2026-10-30T09:00:00Z"},
            "ref_b": {"status": "failed", "issued_at": "2026-10-30T10:00:00Z"},
        },
    }
    db.execute_results = [_session_row("requires_escalation", state=state)] * 2
    async with _client() as client:
        response = await client.get(RETURN)
    assert f"<title>{title}</title>" in response.text
    _assert_private_page(response)


@pytest.mark.asyncio
async def test_a_waiting_checkout_that_cannot_be_reread_keeps_asking_the_buyer_to_wait(db, refresh):
    db.execute_results = [_session_row("requires_escalation"), None]
    async with _client() as client:
        response = await client.get(RETURN)
    assert response.status_code == 200 and "Confirming your payment" in response.text
    assert len(refresh) == 1


@pytest.mark.asyncio
async def test_a_checkout_known_only_by_its_old_references_still_shows_its_outcome(db, refresh):
    db.execute_results = [_session_row("completed", state={"handoff_refs": {"ref_1": {}}}, order_id="ord_1")]
    async with _client() as client:
        response = await client.get(RETURN)
    assert response.status_code == 200 and "Payment received" in response.text


@pytest.mark.parametrize("row", [None, _session_row("completed", state={"buyer": {"email": "asha@example.test"}})])
@pytest.mark.asyncio
async def test_the_return_page_is_404_for_a_checkout_without_a_handoff(db, refresh, row):
    db.execute_results = [row]
    async with _client() as client:
        response = await client.get(RETURN)
    assert response.status_code == 404 and "could not find this checkout" in response.text
    _assert_private_page(response)
    assert refresh == []


@pytest.mark.asyncio
async def test_the_return_page_escapes_what_it_shows(db, refresh):
    db.execute_results = [_session_row("completed", order_id="ord_<b>1</b>")]
    async with _client() as client:
        response = await client.get(RETURN)
    assert "ord_&lt;b&gt;1&lt;/b&gt;" in response.text and "<b>1</b>" not in response.text


@pytest.mark.asyncio
async def test_a_posted_return_ignores_what_the_form_claims(db, refresh):
    db.execute_results = [_session_row("ready_for_complete")]
    async with _client() as client:
        response = await client.post(RETURN, data={"status": "PROCESSED", "order_id": "v1-pl-1"})
    assert response.status_code == 200 and "Payment not completed" in response.text
    assert refresh == []


# --- Plural's webhook -------------------------------------------------------------------------------


WEBHOOK = f"/api/v1/payments/plural/webhooks/{TENANT}/{WEBHOOK_KEY}"


@pytest.fixture()
def applied(monkeypatch):
    state = SimpleNamespace(calls=[], result=None)

    async def apply_webhook(**kwargs):
        state.calls.append(kwargs)
        return state.result

    monkeypatch.setattr(plural_hosted, "apply_webhook", apply_webhook)
    return state


@pytest.mark.asyncio
async def test_a_verified_webhook_is_applied_and_recorded(ledger, applied):
    applied.result = {
        "applied": True,
        "result": "completed",
        "order_id": "ord_1",
        "event_type": "ORDER_PROCESSED",
        "body_sha256": "c" * 64,
    }
    raw = b'{"event_type":"ORDER_PROCESSED"}'
    headers = {"webhook-id": "msg_1", "webhook-timestamp": "1761700000", "webhook-signature": "v1,sig"}
    url = f"/api/v1/payments/plural/webhooks/{str(TENANT).upper()}/{WEBHOOK_KEY}"
    async with _client(scopes=[]) as client:
        response = await client.post(url, content=raw, headers=headers)
    assert response.status_code == 200
    # The acknowledgement carries no order data.
    assert response.json() == {"received": True, "applied": True, "result": "completed"}
    assert applied.calls == [
        {"tenant_id": str(TENANT), "webhook_key": WEBHOOK_KEY, "raw_body": raw, "headers": headers}
    ]
    assert ledger == [
        {
            "tenant_id": str(TENANT),
            "event": cp.WEBHOOK_EVENT,
            "outcome": "success",
            "merchant_id": None,
            "seller_agent_id": None,
            "buyer_agent_id": None,
            "payload": {"body_sha256": "c" * 64, "event_type": "ORDER_PROCESSED", "result": "completed"},
            "details": {"applied": True, "result": "completed"},
        }
    ]


@pytest.mark.asyncio
async def test_a_verified_event_for_another_order_is_acknowledged_and_not_applied(ledger, applied):
    applied.result = {"applied": False, "result": "not_a_handoff", "event_type": "ORDER_PROCESSED", "body_sha256": "d"}
    async with _client(scopes=[]) as client:
        response = await client.post(WEBHOOK, content=b"{}")
    assert response.json() == {"received": True, "applied": False, "result": "not_a_handoff"}
    # Missing signature headers reach the verifier as empty strings.
    assert applied.calls[0]["headers"] == {"webhook-id": "", "webhook-timestamp": "", "webhook-signature": ""}
    assert ledger[0]["outcome"] == "success" and ledger[0]["details"] == {"applied": False, "result": "not_a_handoff"}


@pytest.mark.asyncio
async def test_an_oversized_webhook_is_refused_before_verification(ledger, applied):
    async with _client(scopes=[]) as client:
        response = await client.post(WEBHOOK, content=b"x" * (cp.MAX_WEBHOOK_BYTES + 1))
    assert response.status_code == 413 and applied.calls == [] and ledger == []


@pytest.mark.asyncio
async def test_a_refused_webhook_records_the_reason(ledger, monkeypatch):
    async def apply_webhook(**_kwargs):
        raise plural_hosted.PluralWebhookError("timestamp_out_of_window")

    monkeypatch.setattr(plural_hosted, "apply_webhook", apply_webhook)
    async with _client(scopes=[]) as client:
        response = await client.post(WEBHOOK, content=b"{}")
    assert response.status_code == 400 and response.json()["detail"] == "timestamp_out_of_window"
    assert len(ledger) == 1
    assert ledger[0]["outcome"] == "refused" and ledger[0]["tenant_id"] == str(TENANT)
    assert ledger[0]["payload"] == {"reason": "timestamp_out_of_window"}
    assert ledger[0]["details"] == {"reason": "timestamp_out_of_window"}
