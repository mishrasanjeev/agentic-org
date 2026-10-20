# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G61: a precedence table per fact family decides which source wins; config-only adapters are never called."""

from __future__ import annotations

import copy

import pytest

from core.commerce import source_precedence as sp
from core.commerce.oacp_merchant_config import MerchantCommerceConfigError, normalize_merchant_commerce_config
from core.commerce.ucp.items import ProductEvidenceCatalog
from tests import ucp_txn_fixtures as uf

TENANT = "11111111-1111-4111-8111-111111111111"


def test_defaults_keep_shopify_authoritative_for_commercial_facts_and_config_for_places() -> None:
    assert sp.precedence(None, "price") == ("shopify", "merchant_config")
    assert sp.precedence(None, "inventory")[0] == "shopify"
    assert sp.precedence(None, "catalog")[0] == "shopify"
    assert sp.precedence(None, "policy")[0] == "merchant_config"
    assert sp.precedence(None, "location")[0] == "merchant_config"
    assert set(sp.FAMILIES) == set(sp.DEFAULT_PRECEDENCE)


def test_first_source_with_a_value_wins_and_disagreements_are_named() -> None:
    policy = {
        "source_precedence": {"price": ["erp", "shopify"]},
        "source_facts": {"erp": {"price": {"TOTE-1": "24.00"}}, "pim": {"price": {"TOTE-1": "26.00"}}},
    }
    result = sp.resolve("price", "TOTE-1", {"shopify": "25.00"}, provider_policy=policy)
    assert (result.source, result.value) == ("erp", "24.00")
    # pim is not in the price list, so it never wins; it still disagrees and is reported.
    assert result.disagreeing_sources == ("pim", "shopify")
    # Equal decimal prices do not disagree.
    same = sp.resolve("price", "TOTE-1", {"shopify": "24.0"}, provider_policy=policy)
    assert "shopify" not in same.disagreeing_sources


def test_a_source_without_a_value_falls_through_and_no_value_is_no_fact() -> None:
    policy = {"source_precedence": {"inventory": ["wms", "shopify"]}}
    assert sp.resolve("inventory", "TOTE-1", {"shopify": 4}, provider_policy=policy).source == "shopify"
    nothing = sp.resolve("inventory", "TOTE-1", {"shopify": None}, provider_policy=policy)
    assert not nothing.found and nothing.value is None


@pytest.mark.parametrize(
    "policy",
    [
        {"source_precedence": {"shipping": ["shopify"]}},
        {"source_precedence": {"price": []}},
        {"source_precedence": {"price": ["shopify", "shopify"]}},
        {"source_precedence": {"price": ["marketplace"]}},
        {"source_facts": {"shopify": {"price": {"A": "1.00"}}}},
        {"source_facts": {"erp": {"price": {"A": "one"}}}},
        {"source_facts": {"wms": {"inventory": {"A": -1}}}},
        {"source_facts": {"pim": {"catalog": {"A": "title"}}}},
    ],
)
def test_invalid_tables_and_facts_are_refused_when_the_config_is_saved(policy) -> None:
    with pytest.raises(sp.SourcePrecedenceError):
        sp.validate_provider_policy(policy)


def _normalize(provider_policy: dict) -> dict:
    return normalize_merchant_commerce_config(
        tenant_id=TENANT,
        merchant_id="merchant-1",
        seller_agent_id="seller-1",
        merchant_display_name="Example Store",
        public_brand_profile={},
        commerce_categories=["bags"],
        source_connectors=[{"connector_type": "shopify", "mode": "read_only"}, {"connector_type": "erp"}],
        buyer_channels={},
        payment_providers=[],
        offline_pos_stores=[],
        public_publishing={},
        source_freshness_policy=None,
        provider_policy=provider_policy,
    )


def test_merchant_config_save_validates_the_table_and_non_shopify_adapters_stay_config_only() -> None:
    saved = _normalize({"source_precedence": {"price": ["erp", "shopify"]}})
    assert saved["provider_policy"]["source_precedence"]["price"] == ["erp", "shopify"]
    erp = next(c for c in saved["source_connectors"] if c["connector_type"] == "erp")
    assert erp["adapter_status"] == "pending_adapter" and erp["allowed_to_execute"] is False
    with pytest.raises(MerchantCommerceConfigError):
        _normalize({"source_precedence": {"price": ["nowhere"]}})


def test_products_unchanged_without_a_table() -> None:
    products = uf.products(TENANT)
    assert sp.products_with_precedence(products, {}) == products


@pytest.mark.asyncio
async def test_checkout_items_use_the_winning_price_stock_and_title() -> None:
    products = uf.products(TENANT)
    policy = {
        "source_precedence": {"price": ["erp", "shopify"], "inventory": ["wms", "shopify"], "catalog": ["pim"]},
        "source_facts": {
            "erp": {"price": {"TOTE-1": "30.00"}},
            "wms": {"inventory": {"TOTE-1": 3}},
            "pim": {"catalog": {"TOTE-1": {"title": "ERP name"}}},
        },
    }
    config = uf.merchant_config(TENANT)
    config["provider_policy"].update(policy)
    from core.commerce.ucp.merchant import merchant_ucp_from_config

    merchant = merchant_ucp_from_config(config, tenant_id=TENANT, serving_host=uf.HOST)

    async def _loader(_merchant):
        return copy.deepcopy(products)

    item = (await ProductEvidenceCatalog(_loader).lookup(merchant, ["TOTE-1"]))["TOTE-1"]
    assert item.price_minor_units == 3000
    assert item.inventory == 3
    assert item.title.endswith("(ERP name)")
    # Without the table the Shopify evidence stands.
    plain = (await ProductEvidenceCatalog(_loader).lookup(uf.merchant(TENANT), ["TOTE-1"]))["TOTE-1"]
    assert plain.price_minor_units != 3000


def test_catalog_rendering_and_checkout_see_the_same_winner() -> None:
    from core.commerce.ucp.catalog import ucp_product

    policy = {
        "source_precedence": {"price": ["erp", "shopify"]},
        "source_facts": {"erp": {"price": {"TOTE-1": "30.00"}}},
    }
    rendered = ucp_product(sp.products_with_precedence(uf.products(TENANT), policy)[0])
    assert rendered["variants"][0]["price"]["amount"] == 3000
    assert rendered["price_range"]["min"]["amount"] == 3000


def test_config_only_sources_are_never_fetched() -> None:
    import inspect

    source = inspect.getsource(sp)
    for forbidden in ("httpx", "requests", "aiohttp", "urlopen"):
        assert forbidden not in source
