# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The AgenticOrg shopping network on a real Postgres: opt-in, slugs, the directory, platform subdomains, RLS.

Reuses the custom-domain fixtures (disposable database migrated to head, fake
DNS and a fake Firebase Hosting API). Publication is simulated by recording the
``published`` UCP state the publish route would write.
"""

from __future__ import annotations

import re
import secrets
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from api.v1 import network as api
from core.commerce import network as net
from core.commerce.ucp import store
from core.commerce.ucp.state import ucp_state, with_ucp_state
from core.config import settings
from tests.integration.test_custom_domains_postgres import MERCHANT, SELLER, _merchant, _request, _tenant
from tests.integration.test_custom_domains_postgres import db as db
from tests.integration.test_custom_domains_postgres import firebase as firebase
from tests.integration.test_custom_domains_postgres import migrated_schema as migrated_schema
from tests.integration.test_custom_domains_postgres import world as world

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def network(firebase, monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "platform_subdomain_apex", "shops.example")
    monkeypatch.setattr(settings, "platform_subdomain_cap", 20)
    monkeypatch.setattr(settings, "network_telegram_bot_username", "ExampleShopBot")
    net.reset_directory_cache()
    yield firebase
    net.reset_directory_cache()


CATALOG_CSV = "sku,title,price,currency,quantity\nTOTE-1,Canvas tote,499,INR,3\n"


async def _ready(tenant: str) -> None:
    """The setup Sell everywhere needs: a catalog, policies with a shipping fee, and order limits."""
    from api.v1 import commerce_merchant_setup as setup_api

    await setup_api.upload_catalog(MERCHANT, setup_api.CatalogUploadRequest(format="csv", content=CATALOG_CSV), tenant)
    await setup_api.put_policies(
        MERCHANT,
        setup_api.PoliciesRequest(
            returns="Returns within 7 days.",
            shipping="Ships in 3 to 5 days.",
            shipping_fee="40",
            shipping_currency="INR",
        ),
        tenant,
    )
    await setup_api.put_order_limits(
        MERCHANT,
        setup_api.OrderLimitsRequest(
            currency="INR", max_order_amount="5000", max_daily_amount="20000", max_quantity_per_order=5
        ),
        tenant,
    )


async def _opt_in(tenant: str, *, slug: str | None = None, listed: bool = True, claim: bool = True) -> dict:
    if listed:
        await _ready(tenant)
    return await api.put_sell_everywhere(
        MERCHANT,
        api.SellEverywhereRequest(
            listed=listed, accept_terms=listed, slug=slug, seller_agent_id=SELLER, claim_subdomain=claim
        ),
        _request(),
        tenant,
    )


async def _unpublish(tenant: str) -> None:
    from core.database import get_tenant_session
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        row = (
            await session.execute(
                C6ZMerchantCommerceConfigRow.__table__.select().where(
                    C6ZMerchantCommerceConfigRow.tenant_id == tenant,
                    C6ZMerchantCommerceConfigRow.merchant_id == MERCHANT,
                )
            )
        ).first()
        config = await session.get(C6ZMerchantCommerceConfigRow, row.config_id)
        state = ucp_state(config.public_publishing)
        state["published"] = None
        config.public_publishing = with_ucp_state(config.public_publishing, state)
    store.reset_ucp_host_cache()
    net.reset_directory_cache()


async def test_opt_in_claims_a_platform_subdomain_publishes_and_lists_the_shop(db, network):
    tenant = await _tenant(db)
    await _merchant(tenant)
    slug = f"example-store-{secrets.token_hex(3)}"
    host = f"{slug}.shops.example"

    view = await _opt_in(tenant, slug=slug)
    assert view["listing"]["slug"] == slug and view["listing"]["status"] == "listed"
    assert view["listing"]["terms_version"] == settings.network_terms_version
    assert view["subdomain_note"] is None
    assert view["surfaces"]["web"] == {"status": "pending_mapping", "host": host}
    # Going live published the profile on the attested platform subdomain (no operator step).
    assert view["publication"] == {"published": True, "host": host, "refusal": None}
    assert view["surfaces"]["mcp"]["status"] == "live"
    assert view["platform_subdomains"]["apex"] == "shops.example" and view["platform_subdomains"]["in_use"] >= 1

    binding = await store.find_serving_binding(host)
    assert binding is not None and binding.tenant_id == tenant and binding.merchant_id == MERCHANT

    # Without publication the shop is not in the directory.
    await _unpublish(tenant)
    assert slug not in [shop["slug"] for shop in await net.directory()]
    with pytest.raises(net.NetworkError) as hidden:
        await net.resolve_shop(slug)
    assert hidden.value.code == "shop_unavailable"
    assert (await api.get_sell_everywhere(MERCHANT, SELLER, tenant))["publication"]["refusal"] is None

    retried = await api.publish_sell_everywhere(
        MERCHANT, api.SellEverywherePublishRequest(seller_agent_id=SELLER), _request(), tenant
    )
    assert retried["publication"]["published"] is True
    net.reset_directory_cache()
    shops = [shop for shop in await net.directory() if shop["slug"] == slug]
    assert shops == [
        {
            "slug": slug,
            "name": "Example Store",
            "categories": ["general"],
            "host": host,
            "profile_url": f"https://{host}/.well-known/ucp",
            "telegram_link": f"https://t.me/ExampleShopBot?start={slug}",
        }
    ]
    shop = await net.resolve_shop(slug)
    assert (shop.tenant_id, shop.merchant_id, shop.host) == (tenant, MERCHANT, host)

    # The custom-domain status task takes the row from here to the Firebase domain.
    assert network["hosting"]["created"] == []

    # Pausing hides the shop; the slug stays the shop's.
    paused = await _opt_in(tenant, listed=False)
    assert paused["listing"]["status"] == "paused"
    net.reset_directory_cache()
    assert slug not in [shop["slug"] for shop in await net.directory()]
    with pytest.raises(net.NetworkError) as gone:
        await net.resolve_shop(slug)
    assert gone.value.code == "unknown_shop"


async def test_slugs_are_unique_across_tenants_reserved_names_refused_and_terms_required(db, network):
    first, second = await _tenant(db), await _tenant(db)
    await _merchant(first)
    await _merchant(second)
    slug = f"tea-corner-{secrets.token_hex(3)}"
    await _opt_in(first, slug=slug, claim=False)

    with pytest.raises(HTTPException) as taken:
        await _opt_in(second, slug=slug, claim=False)
    assert taken.value.status_code == 409 and taken.value.detail["code"] == "slug_taken"
    for bad, code in (("www", "reserved_slug"), ("Bad_Slug", "invalid_slug"), ("a--b", "invalid_slug")):
        with pytest.raises(HTTPException) as refused:
            await _opt_in(second, slug=bad, claim=False)
        assert refused.value.detail["code"] == code
    with pytest.raises(HTTPException) as terms:
        await api.put_sell_everywhere(
            MERCHANT, api.SellEverywhereRequest(listed=True, seller_agent_id=SELLER), _request(), second
        )
    assert terms.value.detail["code"] == "terms_not_accepted"
    with pytest.raises(HTTPException) as fixed:
        await _opt_in(first, slug="other-name", claim=False)
    assert fixed.value.detail["code"] == "slug_fixed"


async def test_the_platform_subdomain_cap_is_enforced_across_tenants(db, network, monkeypatch):
    # The disposable database is shared across tests: leave room for exactly one more.
    monkeypatch.setattr(settings, "platform_subdomain_cap", await net.platform_subdomains_in_use() + 1)
    first, second = await _tenant(db), await _tenant(db)
    await _merchant(first)
    await _merchant(second)
    assert (await _opt_in(first, slug=f"cap-a-{secrets.token_hex(3)}"))["subdomain_note"] is None
    second_view = await _opt_in(second, slug=f"cap-b-{secrets.token_hex(3)}")
    # The listing stands; only the platform address is refused.
    assert second_view["listing"]["status"] == "listed"
    assert second_view["subdomain_note"]["code"] == "platform_subdomain_cap_reached"
    assert second_view["surfaces"]["web"]["status"] == "not_started"


async def test_without_the_firebase_front_no_subdomain_is_claimed(db, world, monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "platform_subdomain_apex", "shops.example")
    tenant = await _tenant(db)
    await _merchant(tenant)
    view = await _opt_in(tenant, slug=f"no-front-{secrets.token_hex(3)}")
    assert view["subdomain_note"]["code"] == "platform_subdomains_unavailable"
    assert view["listing"]["subdomain_host"] is None


async def test_listings_are_isolated_by_rls_and_readable_only_in_platform_context(db, network):
    tenant_a, tenant_b = await _tenant(db), await _tenant(db)
    await _merchant(tenant_a)
    await _opt_in(tenant_a, slug=f"rls-{secrets.token_hex(4)}", claim=False)

    role = f"net_rls_{secrets.token_hex(4)}"
    async with db.begin() as conn:
        schema = str((await conn.execute(text("SELECT current_schema()"))).scalar_one())
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", schema), schema
        await conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOBYPASSRLS"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        await conn.execute(text(f"GRANT SELECT, INSERT ON {schema}.commerce_network_listings TO {role}"))

    async def as_probe(conn, **ctx: str) -> None:
        await conn.execute(text(f"SET LOCAL ROLE {role}"))
        await conn.execute(text("SELECT set_config('search_path', :s, true)"), {"s": schema})
        for key, value in ctx.items():
            await conn.execute(text("SELECT set_config(:k, :v, true)"), {"k": f"agenticorg.{key}", "v": value})

    async def count(**ctx: str) -> int:
        async with db.begin() as conn:
            await as_probe(conn, **ctx)
            result = await conn.execute(
                text("SELECT count(*) FROM commerce_network_listings WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": tenant_a},
            )
            return int(result.scalar_one())

    async def insert_as_probe(**ctx: str) -> None:
        async with db.begin() as conn:
            await as_probe(conn, **ctx)
            await conn.execute(
                text(
                    "INSERT INTO commerce_network_listings (id, tenant_id, merchant_id, slug, display_name, "
                    "terms_version, opted_in_by, opted_in_at) VALUES (:id, CAST(:t AS uuid), 'm', :slug, 'x', "
                    "'v', 'test', now())"
                ),
                {"id": uuid.uuid4(), "t": tenant_a, "slug": f"probe-{secrets.token_hex(4)}"},
            )

    try:
        assert await count(tenant_id=tenant_a) == 1
        assert await count(tenant_id=tenant_b) == 0
        assert await count() == 0
        assert await count(platform_ctx="1") == 1
        with pytest.raises(Exception, match="row-level security"):
            await insert_as_probe(tenant_id=tenant_b)
        with pytest.raises(Exception, match="row-level security"):
            await insert_as_probe(platform_ctx="1")
    finally:
        async with db.begin() as conn:
            await conn.execute(text(f"REVOKE ALL ON {schema}.commerce_network_listings FROM {role}"))
            await conn.execute(text(f"REVOKE USAGE ON SCHEMA {schema} FROM {role}"))
            await conn.execute(text(f"DROP ROLE {role}"))


async def test_the_network_flag_hides_every_route(db, network, monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", False)
    tenant = await _tenant(db)
    await _merchant(tenant)
    with pytest.raises(HTTPException) as hidden:
        await api.get_sell_everywhere(MERCHANT, SELLER, tenant)
    assert hidden.value.status_code == 404
    with pytest.raises(HTTPException):
        await api.list_network_merchants()
