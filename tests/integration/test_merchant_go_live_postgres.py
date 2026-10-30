# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant go-live on a real Postgres: setup steps, catalog upload, Sell everywhere, Shopify credentials and webhooks.

Reuses the custom-domain fixtures (disposable database migrated to head, fake DNS and a
fake Firebase Hosting API). Grantex and Shopify are never called: the enrolment client
and the Shopify product read are replaced per test.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from api.v1 import commerce_merchant_setup as setup_api
from api.v1 import commerce_runtime as runtime
from api.v1 import network as network_api
from core.commerce import grantex_enrolment
from core.commerce import network as net
from core.commerce.c6z_runtime_vertical import ShopifyAdminGraphQLClient
from core.config import settings
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import (
    C6ZConnectorEvidenceRow,
    C6ZMerchantCommerceConfigRow,
    C6ZSellerOnboardingPacketRow,
)
from core.models.tenant import Tenant
from tests.integration.test_custom_domains_postgres import _request, _tenant
from tests.integration.test_custom_domains_postgres import db as db
from tests.integration.test_custom_domains_postgres import firebase as firebase
from tests.integration.test_custom_domains_postgres import migrated_schema as migrated_schema
from tests.integration.test_custom_domains_postgres import world as world

pytestmark = pytest.mark.asyncio

CSV = (
    "sku,title,description,price,currency,quantity,image_url\n"
    "TEA-1,Nilgiri green tea,Loose leaf,499,INR,12,https://cdn.example/tea-1.png\n"
    "TEA-2,Masala chai,,350.50,INR,4,\n"
)
SHOP = "tea-corner.myshopify.com"
WEBHOOK_SECRET = "merchant-webhook-signing-secret-01"


@pytest.fixture()
def network(firebase, monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "platform_subdomain_apex", "shops.example")
    monkeypatch.setattr(settings, "platform_subdomain_cap", 1000)
    net.reset_directory_cache()
    yield firebase
    net.reset_directory_cache()


@pytest.fixture()
def enrolment(monkeypatch):
    """The Grantex enrolment client, answering what the test sets (an older Grantex by default)."""
    state: dict[str, Any] = {"answer": {"status": "unavailable", "reason": "grantex_http_404"}, "calls": []}

    async def enrol(tenant_id, merchant_id, source_system, **_kwargs):
        state["calls"].append((tenant_id, merchant_id, source_system))
        return dict(state["answer"])

    monkeypatch.setattr(grantex_enrolment, "enrol_service_tenant", enrol)
    return state


@pytest.fixture()
def shopify_reads(monkeypatch):
    """The read-only Shopify product read, answering one product (no network)."""
    calls: list[str] = []

    async def fetch(self, *, page_size: int = 50, max_pages: int = 10):
        calls.append(self.credentials.shop_domain)
        return [
            {
                "id": "gid://shopify/Product/1",
                "title": "Canvas Tote",
                "descriptionHtml": "Heavy canvas tote",
                "status": "ACTIVE",
                "variants": {
                    "nodes": [
                        {
                            "id": "gid://shopify/ProductVariant/11",
                            "sku": "TOTE-1",
                            "title": "Default",
                            "price": "1299.00",
                            "inventoryQuantity": 7,
                            "inventoryItem": {"id": "gid://shopify/InventoryItem/1"},
                        }
                    ]
                },
            }
        ][: page_size * max_pages]

    monkeypatch.setattr(ShopifyAdminGraphQLClient, "fetch_products", fetch)
    return calls


async def _create(tenant: str, name: str = "Tea Corner") -> dict[str, Any]:
    return await setup_api.create_merchant(
        setup_api.CreateMerchantRequest(display_name=name, categories=["tea"]), tenant_id=tenant
    )


async def _complete_setup(tenant: str, merchant_id: str) -> dict[str, Any]:
    await setup_api.upload_catalog(merchant_id, setup_api.CatalogUploadRequest(format="csv", content=CSV), tenant)
    await setup_api.put_policies(
        merchant_id,
        setup_api.PoliciesRequest(
            returns="Unused tea can be returned within 7 days.",
            shipping="Ships across India in 3 to 5 days.",
            shipping_fee="49.50",
            shipping_currency="inr",
            shipping_countries=["in", "IN"],
        ),
        tenant,
    )
    return await setup_api.put_order_limits(
        merchant_id,
        setup_api.OrderLimitsRequest(
            currency="inr", max_order_amount="5000", max_daily_amount="20000.50", max_quantity_per_order=5
        ),
        tenant,
    )


async def _config(tenant: str, merchant_id: str) -> C6ZMerchantCommerceConfigRow:
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        return (
            await session.scalars(
                select(C6ZMerchantCommerceConfigRow).where(
                    C6ZMerchantCommerceConfigRow.tenant_id == tenant,
                    C6ZMerchantCommerceConfigRow.merchant_id == merchant_id,
                )
            )
        ).one()


async def _opt_in(tenant: str, merchant_id: str, seller: str, slug: str) -> dict[str, Any]:
    return await network_api.put_sell_everywhere(
        merchant_id,
        network_api.SellEverywhereRequest(listed=True, accept_terms=True, slug=slug, seller_agent_id=seller),
        _request(),
        tenant,
    )


async def test_a_new_merchant_goes_from_upload_to_published_without_any_env_edit(db, network, enrolment):
    tenant = await _tenant(db)
    created = await _create(tenant)
    merchant_id = created["merchant"]["merchant_id"]
    seller = created["merchant"]["seller_agent_id"]
    assert re.fullmatch(r"tea-corner-[0-9a-f]{6}", merchant_id)
    assert created["missing"] == ["catalog", "policies", "shipping_rate", "order_limits"]
    listed = await setup_api.list_merchant_configs(tenant_id=tenant)
    assert [item["merchant_id"] for item in listed["merchants"]] == [merchant_id]

    slug = f"tea-corner-{secrets.token_hex(3)}"
    with pytest.raises(HTTPException) as incomplete:
        await _opt_in(tenant, merchant_id, seller, slug)
    assert incomplete.value.status_code == 409 and incomplete.value.detail["code"] == "setup_incomplete"
    assert incomplete.value.detail["missing"] == ["catalog", "policies", "shipping_rate", "order_limits"]

    with pytest.raises(HTTPException) as bad_file:
        await setup_api.upload_catalog(
            merchant_id,
            setup_api.CatalogUploadRequest(format="csv", content="sku,title,price,currency,quantity\nA,,0,INR,1\n"),
            tenant,
        )
    assert bad_file.value.status_code == 422
    assert {item["column"] for item in bad_file.value.detail["errors"]} == {"title", "price"}

    ready = await _complete_setup(tenant, merchant_id)
    assert ready["ready_to_turn_on"] is True and ready["missing"] == []
    assert ready["policies"]["shipping_rate"] == {"fee": "49.50", "currency": "INR", "countries": ["IN"]}
    assert ready["catalog"] | {"last_synced_at": None} == {
        "status": "ready",
        "source": "catalog_upload",
        "shop_domain": None,
        "product_count": 2,
        "last_synced_at": None,
        "currency": "INR",
        "last_error": None,
        "webhook_status": None,
        "webhook_path": None,
    }
    assert ready["order_limits"] == {
        "status": "set",
        "currency": "INR",
        "max_order_amount": "5000.00",
        "max_daily_amount": "20000.50",
        "max_quantity_per_order": 5,
    }
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        evidence = (
            await session.scalars(select(C6ZConnectorEvidenceRow).where(C6ZConnectorEvidenceRow.tenant_id == tenant))
        ).one()
        packet = (
            await session.scalars(
                select(C6ZSellerOnboardingPacketRow).where(C6ZSellerOnboardingPacketRow.tenant_id == tenant)
            )
        ).one()
    assert evidence.source_system == "catalog_upload" and evidence.product_count == 2
    assert packet.connector_choice == "catalog_upload" and packet.status == "synced"
    assert packet.public_brand_profile["return_policy_summary"] == "Unused tea can be returned within 7 days."
    assert packet.public_brand_profile["fulfillment_policy_summary"].endswith("No store pickup.")
    config = await _config(tenant, merchant_id)
    assert config.provider_policy["arbiter"] == {
        "max_order_amount_minor_units": 500_000,
        "max_daily_amount_minor_units": 2_000_050,
        "max_quantity_per_order": 5,
        "currency_allowlist": ["INR"],
    }

    view = await _opt_in(tenant, merchant_id, seller, slug)
    assert view["listing"]["status"] == "listed"
    assert enrolment["calls"] == [(tenant, merchant_id, "catalog_upload")]
    assert view["grantex_enrolment"]["status"] == "unavailable" and view["grantex_enrolment"]["waiting"] is True
    # Platform subdomain attested, signing key provisioned, profile published: no operator step.
    assert view["publication"] == {"published": True, "host": f"{slug}.shops.example", "refusal": None}
    assert view["go_live"]["publication"] == "published"
    # No Grantex settings in this run: the artifacts wait, the page says so.
    assert view["go_live"]["artifacts"] == "not_refreshable"
    assert view["artifacts"]["status"] == "missing"
    assert view["setup"] == {"missing": [], "ready_to_turn_on": True}

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        query = select(Tenant.settings).where(Tenant.id == uuid.UUID(tenant))
        tenant_settings = (await session.execute(query)).scalar()
    assert tenant_settings["grantex_service_enrolment"]["status"] == "unavailable"
    assert "ucp_serving_hosts" in tenant_settings  # jsonb_set left the host index alone

    # Grantex now has the route: Publish / Retry enrols, once.
    enrolment["answer"] = {
        "status": "active",
        "merchant_ids": [merchant_id],
        "source_systems": ["catalog_upload"],
        "enrolled_at": "2026-10-29T08:00:00Z",
    }
    retried = await network_api.publish_sell_everywhere(
        merchant_id, network_api.SellEverywherePublishRequest(seller_agent_id=seller), _request(), tenant
    )
    assert retried["grantex_enrolment"]["status"] == "active"
    assert retried["grantex_enrolment"]["merchant_enrolled"] is True
    await network_api.publish_sell_everywhere(
        merchant_id, network_api.SellEverywherePublishRequest(seller_agent_id=seller), _request(), tenant
    )
    assert len(enrolment["calls"]) == 2
    got = await network_api.get_sell_everywhere(merchant_id, seller, tenant)
    assert got["grantex_enrolment"]["status"] == "active" and got["publication"]["published"] is True


async def test_a_config_save_keeps_the_order_limits_and_policies(db, network, enrolment):
    tenant = await _tenant(db)
    created = await _create(tenant)
    merchant_id = created["merchant"]["merchant_id"]
    seller = created["merchant"]["seller_agent_id"]
    await _complete_setup(tenant, merchant_id)
    # What the operator console sends on every save: a fixed policy without the arbiter keys.
    await runtime.upsert_merchant_commerce_config(
        merchant_id,
        runtime.MerchantCommerceConfigUpsert(
            seller_agent_id=seller,
            merchant_display_name="Tea Corner",
            commerce_categories=["tea"],
            source_connectors=[setup_api._upload_source(enabled=True)],
            provider_policy={
                "provider_owned_execution": True,
                "grantex_executes_payment": False,
                "agenticorg_executes_payment": False,
            },
            sync_onboarding_packet=False,
        ),
        tenant,
    )
    config = await _config(tenant, merchant_id)
    assert config.provider_policy["arbiter"]["max_daily_amount_minor_units"] == 2_000_050
    assert config.provider_policy["merchant_policies"]["returns"].startswith("Unused tea")
    assert config.provider_policy["provider_owned_execution"] is True
    view = await setup_api.get_merchant_setup(merchant_id, tenant)
    assert view["order_limits"]["status"] == "set" and view["ready_to_turn_on"] is True

    with pytest.raises(HTTPException) as refused:
        await setup_api.put_order_limits(
            merchant_id,
            setup_api.OrderLimitsRequest(
                currency="INR", max_order_amount="5000", max_daily_amount="100", max_quantity_per_order=5
            ),
            tenant,
        )
    assert refused.value.status_code == 422


async def test_merchant_setup_is_tenant_scoped(db, network):
    tenant_a, tenant_b = await _tenant(db), await _tenant(db)
    created = await _create(tenant_a)
    merchant_id = created["merchant"]["merchant_id"]
    assert (await setup_api.list_merchant_configs(tenant_id=tenant_b))["merchants"] == []
    for call in (
        lambda: setup_api.get_merchant_setup(merchant_id, tenant_b),
        lambda: setup_api.upload_catalog(
            merchant_id, setup_api.CatalogUploadRequest(format="csv", content=CSV), tenant_b
        ),
        lambda: setup_api.put_policies(
            merchant_id,
            setup_api.PoliciesRequest(returns="Returns in 7 days please.", shipping="Ships in 3 to 5 days."),
            tenant_b,
        ),
    ):
        with pytest.raises(HTTPException) as hidden:
            await call()
        assert hidden.value.status_code == 404


async def test_shopify_connects_with_the_merchants_own_token_and_webhooks_verify_per_merchant(
    db, network, shopify_reads, monkeypatch
):
    async def validated(_credentials):
        return None

    monkeypatch.setattr(runtime, "_validate_shopify_read_credentials", validated)
    tenant, other = await _tenant(db), await _tenant(db)
    created = await _create(tenant)
    merchant_id = created["merchant"]["merchant_id"]

    connected = await setup_api.connect_shopify(
        merchant_id,
        setup_api.ShopifyConnectRequest(
            shop_domain=SHOP, admin_access_token="fixture-admin-token-0001", webhook_secret=WEBHOOK_SECRET
        ),
        tenant,
    )
    assert connected["sync_error"] is None and shopify_reads == [SHOP]
    catalog = connected["setup"]["catalog"]
    assert catalog["status"] == "ready" and catalog["source"] == "shopify" and catalog["shop_domain"] == SHOP
    assert catalog["product_count"] == 1 and catalog["webhook_status"] == "configured"
    token = runtime.shopify_webhook_path_token(tenant, merchant_id)
    assert catalog["webhook_path"] == f"/api/v1/webhooks/shopify/{tenant}/{token}"
    assert WEBHOOK_SECRET not in json.dumps(connected) and "fixture-admin-token-0001" not in json.dumps(connected)

    # Saving the token again without the secret keeps the stored secret for the same store.
    await setup_api.connect_shopify(
        merchant_id,
        setup_api.ShopifyConnectRequest(shop_domain=SHOP, admin_access_token="fixture-admin-token-0002"),
        tenant,
    )
    assert (await setup_api.get_merchant_setup(merchant_id, tenant))["catalog"]["webhook_status"] == "configured"

    body = json.dumps({"id": 1, "title": "Canvas Tote"}).encode()

    async def deliver(tenant_id: str, path_token: str, secret: str, shop: str = SHOP) -> Any:
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        return await setup_api.receive_shopify_webhook(
            uuid.UUID(tenant_id),
            _webhook_request(body, {"x-shopify-hmac-sha256": signature, "x-shopify-shop-domain": shop}),
            path_token=path_token,
        )

    accepted = await deliver(tenant, token, WEBHOOK_SECRET)
    assert accepted.status_code == 200 and json.loads(accepted.body)["stale_packets_marked"] == 1
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        statuses = (
            await session.scalars(
                select(C6ZSellerOnboardingPacketRow.status).where(C6ZSellerOnboardingPacketRow.tenant_id == tenant)
            )
        ).all()
    assert list(statuses) == ["cache_refresh_needed"]
    assert (await setup_api.get_merchant_setup(merchant_id, tenant))["catalog"]["status"] == "changed_in_store"

    for tenant_id, path_token, secret, shop in (
        (tenant, token, "a-deployment-wide-secret-value", SHOP),
        (tenant, token, WEBHOOK_SECRET, "another-store.myshopify.com"),
        (other, token, WEBHOOK_SECRET, SHOP),
        (tenant, runtime.shopify_webhook_path_token(tenant, "another-merchant"), WEBHOOK_SECRET, SHOP),
    ):
        refused = await deliver(tenant_id, path_token, secret, shop)
        assert refused.status_code == 401 and json.loads(refused.body) == {"status": "not_verified"}

    # A sync reads the store with the merchant's own token, never the deployment's.
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "operator-store.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "fixture-operator-token")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-04")
    synced = await setup_api.sync_catalog(merchant_id, tenant_id=tenant)
    assert synced["sync"]["credential_source"] == "tenant_connector_config"
    assert shopify_reads[-1] == SHOP


async def test_the_webhook_secret_is_added_later_without_the_token_for_the_same_store_only(
    db, network, shopify_reads, monkeypatch
):
    validated_with: list[str] = []

    async def validated(credentials):
        validated_with.append(credentials.admin_access_token)

    monkeypatch.setattr(runtime, "_validate_shopify_read_credentials", validated)
    tenant = await _tenant(db)
    merchant_id = (await _create(tenant))["merchant"]["merchant_id"]
    second_id = (await _create(tenant, "Second Shop"))["merchant"]["merchant_id"]

    async def vault(mid: str) -> dict[str, Any]:
        async with get_tenant_session(uuid.UUID(tenant)) as session:
            row = await runtime._load_shopify_connector_config_row(session, uuid.UUID(tenant), mid)
        return runtime._stored_shopify_credential_data(row)

    async def connect(mid: str, **fields: Any) -> dict[str, Any]:
        return await setup_api.connect_shopify(mid, setup_api.ShopifyConnectRequest(**fields), tenant)

    first = await connect(merchant_id, shop_domain=SHOP, admin_access_token="fixture-admin-token-0001")
    assert first["setup"]["catalog"]["webhook_status"] == "blocked_missing_webhook_secret"

    # Shopify showed the token once: the signing secret is added with the token left blank.
    added = await connect(merchant_id, shop_domain=SHOP, webhook_secret=WEBHOOK_SECRET)
    assert added["sync_error"] is None and added["setup"]["catalog"]["webhook_status"] == "configured"
    assert validated_with == ["fixture-admin-token-0001", "fixture-admin-token-0001"]
    assert "fixture-admin-token-0001" not in json.dumps(added) and WEBHOOK_SECRET not in json.dumps(added)
    stored = await vault(merchant_id)
    assert stored["admin_access_token"] == "fixture-admin-token-0001" and stored["webhook_secret"] == WEBHOOK_SECRET
    assert stored["shop_domain"] == SHOP and stored["credential_source"] == "admin_access_token"

    body = json.dumps({"id": 1}).encode()
    signature = base64.b64encode(hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()).decode()
    accepted = await setup_api.receive_shopify_webhook(
        uuid.UUID(tenant),
        _webhook_request(body, {"x-shopify-hmac-sha256": signature, "x-shopify-shop-domain": SHOP}),
        path_token=runtime.shopify_webhook_path_token(tenant, merchant_id),
    )
    assert accepted.status_code == 200

    # Another store, or a merchant with no saved connection of its own (even in the same tenant), needs a token.
    for mid, shop in (
        (merchant_id, "another-store.myshopify.com"),
        (second_id, SHOP),
    ):
        with pytest.raises(HTTPException) as required:
            await connect(mid, shop_domain=shop, webhook_secret="another-signing-secret-value-01")
        assert required.value.status_code == 400
        assert required.value.detail["error"] == "shopify_token_required"
    assert len(validated_with) == 2
    # The refused saves changed nothing.
    assert await vault(merchant_id) == stored
    assert await vault(second_id) == {}
    setup = await setup_api.get_merchant_setup(merchant_id, tenant)
    assert setup["catalog"]["shop_domain"] == SHOP and setup["catalog"]["webhook_status"] == "configured"


async def test_a_tenant_without_a_connector_is_blocked_even_with_shopify_env(db, network, shopify_reads, monkeypatch):
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "operator-store.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "fixture-operator-token")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-04")
    monkeypatch.setattr(settings, "shopify_env_operator_tenant_id", "")
    tenant = await _tenant(db)
    merchant_id = f"shop-{secrets.token_hex(3)}"
    saved = await runtime.upsert_merchant_commerce_config(
        merchant_id,
        runtime.MerchantCommerceConfigUpsert(
            merchant_display_name="Operator lookalike",
            commerce_categories=["general"],
            source_connectors=[
                {
                    "connector_type": "shopify",
                    "shop_domain": "operator-store.myshopify.com",
                    "store_id": "operator-store.myshopify.com",
                    "mode": "read_only",
                    "credential_custody": "agenticorg_vault",
                    "credential_ref": "tenant_connector_config",
                }
            ],
        ),
        tenant,
    )
    with pytest.raises(HTTPException) as blocked:
        await runtime._sync_shopify_packet(
            tenant_id=tenant, packet_id=saved["packet_id"], page_size=1, max_pages=1, currency=None
        )
    assert blocked.value.status_code == 424
    assert blocked.value.detail["status"] == "blocked_missing_shopify_credentials"
    assert shopify_reads == []

    # The named operator tenant reads its own store from the environment, and only that store.
    monkeypatch.setattr(settings, "shopify_env_operator_tenant_id", tenant)
    synced = await runtime._sync_shopify_packet(
        tenant_id=tenant, packet_id=saved["packet_id"], page_size=1, max_pages=1, currency=None
    )
    assert synced["credential_source"] == "environment_operator"
    assert shopify_reads == ["operator-store.myshopify.com"]


def _webhook_request(body: bytes, headers: dict[str, str]) -> Request:
    sent = {"done": False}

    async def receive() -> dict[str, Any]:
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/webhooks/shopify/x/y",
        "headers": [
            (key.encode(), value.encode()) for key, value in {**headers, "content-length": str(len(body))}.items()
        ],
        "query_string": b"",
    }
    return Request(scope, receive)
