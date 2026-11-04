# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A network shop's own storefront host on a real Postgres: host-to-shop resolution, the storefront load,
the registration rows on the Sell everywhere page, and the network feed index.

Reuses the network fixtures (disposable database migrated to head, fake DNS and Firebase Hosting). The
test database holds no Grantex-verified catalog artifacts, so the storefront loads as not verified: the
pages answer 503 and every program shows the shop as not eligible for that reason.
"""

from __future__ import annotations

import json
import secrets

import pytest

from api.v1 import network as api
from core.commerce import network as net
from core.commerce import network_registrations as reg
from core.commerce import shop_site_store
from core.config import settings
from tests.integration.test_custom_domains_postgres import MERCHANT, SELLER, _merchant, _tenant
from tests.integration.test_custom_domains_postgres import db as db
from tests.integration.test_custom_domains_postgres import firebase as firebase
from tests.integration.test_custom_domains_postgres import migrated_schema as migrated_schema
from tests.integration.test_custom_domains_postgres import world as world
from tests.integration.test_network_postgres import _opt_in, _web_address_live
from tests.integration.test_network_postgres import network as network

pytestmark = pytest.mark.asyncio


async def test_a_live_listed_shop_host_resolves_to_its_storefront(db, network, monkeypatch):
    monkeypatch.setattr(settings, "network_registrations", json.dumps({"google": "submitted"}))
    shop_site_store.reset_site_cache()
    tenant = await _tenant(db)
    await _merchant(tenant)
    slug = f"site-shop-{secrets.token_hex(3)}"
    host = f"{slug}.shops.example"
    await _opt_in(tenant, slug=slug)

    # While the address is being mapped the host is not a shop host yet.
    assert await net.shop_for_host(host) is None
    view = await api.get_sell_everywhere(MERCHANT, SELLER, tenant)
    google = view["surfaces"]["google"]
    assert google == {"status": "not_eligible", "reason": reg.WEB_ADDRESS_NOT_LIVE, "feed_url": None}

    await _web_address_live(tenant, host)
    shop = await net.shop_for_host(host)
    assert shop is not None
    assert (shop.slug, shop.tenant_id, shop.merchant_id, shop.host) == (slug, tenant, MERCHANT, host)
    assert await net.shop_for_host(f"other-{slug}.shops.example") is None

    site = await shop_site_store.load_site(host)
    assert site is not None and site.slug == slug and site.host == host
    # No Grantex-verified catalog artifacts in this database: the storefront holds no products.
    assert site.catalog_verified is False and site.products == ()
    # The shop's own policies and flat shipping fee are read from its config.
    assert site.returns_policy == "Returns within 7 days." and site.shipping_policy == "Ships in 3 to 5 days."
    assert [(rate.amount_minor_units, rate.currency) for rate in site.rates] == [(4000, "INR")]

    view = await api.get_sell_everywhere(MERCHANT, SELLER, tenant)
    google = view["surfaces"]["google"]
    assert google == {"status": "not_eligible", "reason": reg.CATALOG_NOT_VERIFIED, "feed_url": None}
    assert view["surfaces"]["chatgpt"]["reason"] == reg.CATALOG_NOT_VERIFIED

    monkeypatch.setattr(settings, "network_enabled", True)
    index = json.loads((await api.list_network_feeds()).body)
    entry = next(item for item in index["shops"] if item["slug"] == slug)
    assert entry["feed_url"] == f"https://{host}/feeds/google.xml"
    assert (entry["catalog_verified"], entry["eligible_items"]) == (False, 0)

    # Pausing takes the host off the storefront.
    await _opt_in(tenant, listed=False)
    net.reset_directory_cache()
    assert await net.shop_for_host(host) is None
    assert await shop_site_store.load_site(host) is None
    shop_site_store.reset_site_cache()
