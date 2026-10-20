# SPDX-License-Identifier: Apache-2.0
"""G21: the /ucp REST adapter and its production wiring.

Every route resolves the merchant from ``Host`` before any business work (an unknown host is 404, and a write's
refusal is ledgered in its own transaction while a read records nothing), ``Request-Id`` is echoed, and the
production runtime reads the merchant's runtime scope, Shopify credentials (fail closed to no Shopify reads),
delivery rates (a failure degrades to no rates) and the verified policy snapshot only while its artifact is usable.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from api import route_enforcement
from api.route_enforcement import enforce_route_metadata
from api.v1 import commerce_runtime, commerce_ucp
from core.commerce.ledger import current_request_scope
from core.commerce.ucp import handlers
from core.commerce.ucp.items import ProductEvidenceCatalog
from core.commerce.ucp.merchant import PublishedHostResolver
from core.commerce.ucp.shopify_reads import ShopifyAdminReads
from core.config import settings
from tests import ucp_fixtures
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000b7"))
ADMIN_ACCESS = "-".join(("shpat", "placeholder", "value"))


# --- REST adapter ---------------------------------------------------------------------------------


def _app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.tenant_id = TENANT
        request.state.scopes = ["ucp:read", "ucp:write"]
        request.state.auth_mode = "api_key"
        request.state.api_key_name = "platform-key"
        return await call_next(request)

    app.include_router(commerce_ucp.router)
    return app


@pytest.fixture()
def client(monkeypatch):
    async def _no_rate_limit(_request, _meta):
        return None

    monkeypatch.setattr(route_enforcement, "_check_rate_limit", _no_rate_limit)
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(commerce_ucp, "runtime_factory", lambda: uf.runtime(TENANT))
    events: list[dict] = []

    async def _record_own(**kwargs):
        scope = current_request_scope()
        if scope is not None:
            scope.recorded.add(kwargs["event"])
        events.append(kwargs)

    monkeypatch.setattr(commerce_ucp, "record_commerce_event_in_own_transaction", _record_own)
    test_client = TestClient(_app())
    test_client.events = events  # type: ignore[attr-defined]
    return test_client


UNKNOWN = {"Host": "unknown.merchant.example", "UCP-Agent": f'profile="{uf.PLATFORM_PROFILE}"'}


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        ("PUT", "/ucp/checkout-sessions/chk_1", "update_checkout"),
        ("POST", "/ucp/checkout-sessions/chk_1/complete", "complete_checkout"),
        ("POST", "/ucp/checkout-sessions/chk_1/cancel", "cancel_checkout"),
        ("POST", "/ucp/carts", "create_cart"),
        ("PUT", "/ucp/carts/cart_1", "update_cart"),
        ("POST", "/ucp/carts/cart_1/cancel", "cancel_cart"),
    ],
)
def test_write_routes_refuse_an_unknown_host_and_ledger_the_refusal(client, method, path, operation) -> None:
    response = client.request(method, path, json=uf.create_body(), headers=UNKNOWN)
    assert response.status_code == 404
    assert [e["event"] for e in client.events] == [handlers.EVENTS[operation]]
    event = client.events[0]
    assert event["outcome"] == "refused" and event["details"] == {"http_status": 404, "operation": operation}
    assert event["payload"]["operation"] == operation and len(event["payload"]["body_sha256"]) == 64


@pytest.mark.parametrize("path", ["/ucp/checkout-sessions/chk_1", "/ucp/carts/cart_1", "/ucp/orders/ord_1"])
def test_read_routes_refuse_an_unknown_host_without_a_ledger_event(client, path) -> None:
    response = client.get(path, headers=UNKNOWN)
    assert response.status_code == 404
    assert client.events == []


def test_request_id_is_echoed_and_bounded(client) -> None:
    long_id = "r" * 300
    response = client.get("/ucp/orders/ord_1", headers={**UNKNOWN, "Request-Id": long_id})
    assert response.headers["request-id"] == "r" * 128
    assert "request-id" not in {k.lower() for k in client.get("/ucp/orders/ord_1", headers=UNKNOWN).headers}


# --- production wiring ------------------------------------------------------------------------------


def test_build_runtime_wires_the_production_dependencies() -> None:
    runtime = commerce_ucp.build_runtime()
    assert isinstance(runtime.resolver, PublishedHostResolver)
    assert isinstance(runtime.catalog_for(uf.merchant(TENANT)), ProductEvidenceCatalog)
    assert runtime.rates_for is commerce_ucp._rates_for
    assert runtime.shopify_reads_for is commerce_ucp._shopify_reads
    assert runtime.arbiter_scope is commerce_ucp._arbiter_scope
    assert runtime.signer is commerce_ucp.merchant_signer
    assert runtime.after_commit is commerce_ucp.kick_dispatch
    assert runtime.published is commerce_ucp._published_capabilities
    assert runtime.policies_for is commerce_ucp._policies


@pytest.mark.asyncio
async def test_runtime_scope_products_and_arbiter_scope_read_the_merchant_scope(monkeypatch) -> None:
    merchant = uf.merchant(TENANT)
    calls: list[dict] = []

    async def _scope(**kwargs):
        calls.append(kwargs)
        return ["record"], [{"product_ref": "p1"}], [{"capability": "c"}]

    async def _config(tenant_id, merchant_id, seller_agent_id):
        calls.append({"config": (tenant_id, merchant_id, seller_agent_id)})
        return {"limits": True}

    monkeypatch.setattr(commerce_runtime, "_load_runtime_scope", _scope)
    monkeypatch.setattr(commerce_runtime, "_merchant_config_for_arbiter", _config)
    assert await commerce_ucp._products(merchant) == [{"product_ref": "p1"}]
    assert await commerce_ucp._arbiter_scope(merchant) == (["record"], {"limits": True})
    expected = {
        "tenant_id": TENANT,
        "merchant_id": merchant.merchant_id,
        "seller_agent_id": merchant.seller_agent_id,
        "buyer_agent_id": None,
    }
    assert calls == [expected, expected, {"config": (TENANT, merchant.merchant_id, merchant.seller_agent_id)}]


@pytest.fixture()
def vault(monkeypatch):
    """The Shopify connector row and the tenant vault, faked: ``state`` decides what each read returns."""
    from core.crypto import tenant_secrets

    state: dict = {"row": None, "plaintext": None, "fail": False}

    @asynccontextmanager
    async def _session(_tenant):
        yield object()

    async def _row(_session, tenant, merchant_id):
        state["asked"] = (tenant, merchant_id)
        return state["row"]

    def _decrypt(stored: str) -> str:
        assert stored == "ciphertext"
        if state["fail"]:
            raise ValueError("bad token")
        return json.dumps(state["plaintext"])

    monkeypatch.setattr(commerce_ucp, "get_tenant_session", _session)
    monkeypatch.setattr(commerce_runtime, "_load_shopify_connector_config_row", _row)
    monkeypatch.setattr(tenant_secrets, "decrypt_for_tenant", _decrypt)
    return state


def _row(config: dict | None = None, stored: object = "ciphertext") -> SimpleNamespace:
    return SimpleNamespace(credentials_encrypted={"_encrypted": stored}, config=config)


@pytest.mark.asyncio
async def test_shopify_credentials_come_from_the_tenant_vault(vault) -> None:
    merchant = uf.merchant(TENANT)
    vault["row"] = _row({"api_version": "2026-07"})
    vault["plaintext"] = {"admin_access_token": f" {ADMIN_ACCESS} ", "shop_domain": "Example-Store.MyShopify.com"}
    credentials = await commerce_ucp._shopify_credentials(merchant)
    assert credentials is not None
    assert credentials.shop_domain == "example-store.myshopify.com"
    assert credentials.admin_access_token == ADMIN_ACCESS
    assert credentials.api_version == "2026-07"
    assert vault["asked"] == (uuid.UUID(TENANT), merchant.merchant_id)
    reads = await commerce_ucp._shopify_reads(merchant)
    assert isinstance(reads, ShopifyAdminReads)
    assert reads.graphql_url == "https://example-store.myshopify.com/admin/api/2026-07/graphql.json"


@pytest.mark.asyncio
async def test_shopify_domain_and_version_fall_back_to_the_connector_config(vault) -> None:
    vault["row"] = _row({"shop_domain": "fallback-store.myshopify.com"})
    vault["plaintext"] = {"access_token": ADMIN_ACCESS}
    credentials = await commerce_ucp._shopify_credentials(uf.merchant(TENANT))
    assert credentials is not None
    assert (credentials.shop_domain, credentials.api_version) == ("fallback-store.myshopify.com", "2026-04")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "plaintext", "fail"),
    [
        (None, None, False),
        (_row(stored=""), None, False),
        (_row(stored=None), None, False),
        (_row(), None, True),
        (_row(), {"shop_domain": "example-store.myshopify.com"}, False),
        (_row(), {"admin_access_token": ADMIN_ACCESS, "shop_domain": "shop.merchant.example"}, False),
        (_row(), {"admin_access_token": ADMIN_ACCESS}, False),
    ],
    ids=["no-connector", "empty-vault", "no-vault", "unreadable", "no-token", "not-shopify", "no-domain"],
)
async def test_missing_or_unusable_shopify_credentials_mean_no_shopify_reads(vault, row, plaintext, fail) -> None:
    vault.update(row=row, plaintext=plaintext, fail=fail)
    merchant = uf.merchant(TENANT)
    assert await commerce_ucp._shopify_credentials(merchant) is None
    assert await commerce_ucp._shopify_reads(merchant) is None


@pytest.mark.asyncio
async def test_delivery_rates_degrade_to_none_when_shopify_is_absent_or_failing(monkeypatch) -> None:
    merchant = uf.merchant(TENANT)
    rates = commerce_ucp._rates_for(merchant)

    async def _none(_m):
        return None

    monkeypatch.setattr(commerce_ucp, "_shopify_reads", _none)
    assert await rates(merchant) == []

    async def _working(_m):
        return uf.FakeShopifyReads()

    monkeypatch.setattr(commerce_ucp, "_shopify_reads", _working)
    assert [r.id for r in await rates(merchant)] == ["ship-std", "ship-exp"]

    class _Failing:
        async def delivery_rates(self):
            raise TimeoutError("shopify slow")

    async def _failing(_m):
        return _Failing()

    monkeypatch.setattr(commerce_ucp, "_shopify_reads", _failing)
    assert await rates(merchant) == []


POLICY = {
    "return_policy_summary": "Returns within 30 days.",
    "return_policy_url": "https://merchant.example/returns",
    "accepts_returns": True,
    "return_window_days": 30,
}


def _merchant_with_policy(snapshot: object):
    merchant = uf.merchant(TENANT)
    return dataclasses.replace(
        merchant,
        tenant_id=ucp_fixtures.TENANT_ID,
        merchant_id=ucp_fixtures.MERCHANT_ID,
        seller_agent_id=ucp_fixtures.SELLER_AGENT_ID,
        raw_config={**merchant.raw_config, "public_publishing": {"ucp": {"verified_policy": snapshot}}},
    )


@pytest.fixture()
def shared_records(monkeypatch):
    from core.commerce.ucp import store

    state: dict = {"records": [], "calls": []}

    async def _load(**kwargs):
        state["calls"].append(kwargs)
        return state["records"]

    monkeypatch.setattr(store, "load_shared_cache_records", _load)
    return state


@pytest.mark.asyncio
async def test_policies_come_from_the_verified_snapshot_while_its_artifact_is_usable(shared_records) -> None:
    shared_records["records"] = [ucp_fixtures.cache_record("policy")]
    merchant = _merchant_with_policy({"artifact_id": "oacp_art_policy", "policy": POLICY})
    policies = await commerce_ucp._policies(merchant)
    assert policies and policies[0]["type"] == "dev.ucp.shopping.policy.return"
    assert shared_records["calls"] == [
        {
            "tenant_id": ucp_fixtures.TENANT_ID,
            "merchant_id": ucp_fixtures.MERCHANT_ID,
            "seller_agent_id": ucp_fixtures.SELLER_AGENT_ID,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "records"),
    [
        (None, []),
        ({"artifact_id": "oacp_art_policy"}, []),
        ({"artifact_id": "oacp_art_policy", "policy": POLICY}, []),
        ({"artifact_id": "oacp_art_policy", "policy": POLICY}, "unverified"),
        ({"artifact_id": "oacp_art_other", "policy": POLICY}, "verified"),
    ],
    ids=["no-snapshot", "no-policy", "no-records", "unverified-record", "other-artifact"],
)
async def test_no_policies_without_a_usable_verified_artifact(shared_records, snapshot, records) -> None:
    if records == "unverified":
        records = [ucp_fixtures.cache_record("policy", ref=None)]
    elif records == "verified":
        records = [ucp_fixtures.cache_record("policy")]
    shared_records["records"] = records
    assert await commerce_ucp._policies(_merchant_with_policy(snapshot)) == []
    if snapshot is None or "policy" not in snapshot:
        assert shared_records["calls"] == []


# --- runtime router: refusals answered before the database ----------------------------------------


@pytest.mark.asyncio
async def test_runtime_routes_refuse_a_tenant_context_that_is_not_a_uuid() -> None:
    with pytest.raises(HTTPException) as caught:
        await commerce_ucp.list_order_webhook_subscriptions(merchant_id=None, tenant_id="not-a-uuid")
    assert caught.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        commerce_ucp.OrderEventRequest(),
        commerce_ucp.OrderEventRequest(fulfillment_event={"type": "shipped"}, adjustment={"type": "refund"}),
    ],
)
async def test_order_event_needs_exactly_one_change(monkeypatch, body) -> None:
    kicked: list = []
    monkeypatch.setattr(commerce_ucp, "kick_dispatch", kicked.append)
    with pytest.raises(HTTPException) as caught:
        await commerce_ucp.record_order_event("ord_1", body, tenant_id=TENANT)
    assert caught.value.status_code == 422
    assert kicked == []
