# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP catalog search and lookup from verified OACP artifacts (G23) and the policy mapper (G26)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.commerce.ucp import v2026_04_08, v2026_08_25
from core.commerce.ucp.catalog import (
    catalog_unavailable_message,
    decode_cursor,
    find_catalog_variant,
    lookup_catalog,
    minor_units,
    not_found_message,
    paginate,
    search_catalog,
    ucp_product,
    ucp_variant_id,
    usable_artifact_ids,
    verified_catalog_source,
)
from core.commerce.ucp.policies import RETURN_POLICY_TYPE, WARRANTY_POLICY_TYPE, map_oacp_policy_to_ucp_policies
from tests.ucp_fixtures import cache_record, catalog_records, synced_products
from tests.ucp_schema_validation import (
    CATALOG_LOOKUP_SCHEMAS,
    CATALOG_SEARCH_SCHEMAS,
    R25_POLICY,
    assert_ucp_valid,
)

RENDERERS = {"2026-04-08": v2026_04_08, "2026-08-25": v2026_08_25}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    ("price", "currency", "expected"),
    [
        ("1299.00", "INR", 129_900),
        ("899", "INR", 89_900),
        ("0.105", "USD", 11),
        ("1500", "JPY", 1500),
        ("1.2345", "KWD", 1235),
        ("-1", "INR", None),
        ("abc", "INR", None),
        (None, "INR", None),
        ("NaN", "INR", None),
    ],
)
def test_minor_units_use_the_iso_4217_exponent_without_floats(price, currency, expected) -> None:
    assert minor_units(price, currency) == expected


def test_catalog_needs_verified_fresh_catalog_price_and_inventory_artifacts() -> None:
    products = synced_products()
    now_iso = _now_iso()
    assert (
        verified_catalog_source(cache_records=catalog_records(), products=products, now_iso=now_iso).unavailable_reason
        is None
    )
    for missing in ("catalog_snapshot", "price", "inventory"):
        records = [record for record in catalog_records() if record.artifact_type != missing]
        source = verified_catalog_source(cache_records=records, products=products, now_iso=now_iso)
        assert source.unavailable_reason == f"verified_artifacts_missing:{missing}"
        assert source.products == ()
    legacy = [cache_record(kind, ref="c6z:x:verified") for kind in ("catalog_snapshot", "price", "inventory")]
    unverified = [cache_record(kind, ref=None) for kind in ("catalog_snapshot", "price", "inventory")]
    expired = catalog_records(now=datetime.now(UTC) - timedelta(hours=1))
    buyer_scoped = catalog_records(buyer_agent_id="shopper-01")
    for records in (legacy, unverified, expired, buyer_scoped):
        assert verified_catalog_source(cache_records=records, products=products, now_iso=now_iso).unavailable_reason


def test_product_mapping_matches_the_ucp_item_schema() -> None:
    tote = ucp_product(synced_products()[0])
    assert tote["id"] == "shopify_product:aaaaaaaaaaaaaaaaaaaaaaaa:redacted"
    assert tote["description"] == {"plain": "Sturdy cotton tote bag."}
    assert tote["price_range"] == {
        "min": {"amount": 129_900, "currency": "INR"},
        "max": {"amount": 139_900, "currency": "INR"},
    }
    natural, black = tote["variants"]
    assert natural["id"] == ucp_variant_id({"variant_id": "gid://shopify/ProductVariant/1001"})
    assert natural["list_price"] == {"amount": 149_900, "currency": "INR"}
    assert natural["availability"] == {"available": True, "status": "in_stock"}
    assert black["availability"] == {"available": False, "status": "out_of_stock"}
    assert black["price"]["currency"] == "INR"  # falls back to the evidence currency
    assert tote["options"] == [{"name": "Colour", "values": [{"label": "Natural"}, {"label": "Black"}]}]
    assert tote["media"][0] == {"type": "image", "url": "https://cdn.merchant.example/tote.jpg", "alt_text": "Tote"}
    assert ucp_product(synced_products()[2]) is None  # draft products are not public
    found = find_catalog_variant(synced_products(), natural["id"])
    assert found is not None and found[1]["sku"] == "TOTE-NAT"


@pytest.mark.parametrize("version", ["2026-04-08", "2026-08-25"])
def test_search_and_lookup_responses_validate_against_the_release_schema(version: str) -> None:
    products = synced_products()
    results = search_catalog(products, query="tote", categories=None, price_min=None, price_max=None)
    page, pagination = paginate(results, offset=0, limit=10)
    policies = map_oacp_policy_to_ucp_policies({"return_policy_summary": "7 day returns."})
    body = RENDERERS[version].render_catalog_response(
        capability="dev.ucp.shopping.catalog.search",
        products=page,
        messages=[],
        pagination=pagination,
        policies=policies,
    )
    assert_ucp_valid(body, CATALOG_SEARCH_SCHEMAS[version])
    assert [item["title"] for item in body["products"]] == ["Canvas Tote"]
    assert ("policies" in body) is (version == "2026-08-25")

    natural_id = ucp_variant_id({"variant_id": "gid://shopify/ProductVariant/1001"})
    found, missing = lookup_catalog(
        products,
        ["shopify_product:bbbbbbbbbbbbbbbbbbbbbbbb:redacted", natural_id, "TOTE-BLK", "unknown-id"],
    )
    body = RENDERERS[version].render_catalog_response(
        capability="dev.ucp.shopping.catalog.lookup",
        products=found,
        messages=[not_found_message(index) for index in missing],
        pagination=None,
        policies=[],
    )
    assert_ucp_valid(body, CATALOG_LOOKUP_SCHEMAS[version])
    assert missing == [3]
    tote = next(item for item in found if item["title"] == "Canvas Tote")
    assert [(variant["sku"], variant["inputs"]) for variant in tote["variants"]] == [
        ("TOTE-NAT", [{"id": natural_id, "match": "exact"}]),
        ("TOTE-BLK", [{"id": "TOTE-BLK", "match": "exact"}]),
    ]
    apron = next(item for item in found if item["title"] == "Linen Apron")
    assert apron["variants"][0]["inputs"] == [
        {"id": "shopify_product:bbbbbbbbbbbbbbbbbbbbbbbb:redacted", "match": "featured"}
    ]

    unavailable = RENDERERS[version].render_catalog_response(
        capability="dev.ucp.shopping.catalog.search",
        products=[],
        messages=[catalog_unavailable_message()],
        pagination={"has_next_page": False},
        policies=[],
    )
    assert_ucp_valid(unavailable, CATALOG_SEARCH_SCHEMAS[version])


def test_search_filters_and_pagination() -> None:
    products = synced_products()
    assert [
        p["title"] for p in search_catalog(products, query=None, categories=["kitchen"], price_min=None, price_max=None)
    ] == ["Linen Apron"]
    cheap = search_catalog(
        products, query=None, categories=None, price_min=None, price_max=100_000, price_currency="INR"
    )
    assert [p["title"] for p in cheap] == ["Linen Apron"]
    ignored = search_catalog(
        products, query=None, categories=None, price_min=None, price_max=100_000, price_currency="USD"
    )
    assert len(ignored) == 2
    assert search_catalog(products, query="tote bag natural", categories=None, price_min=None, price_max=None)
    assert not search_catalog(products, query="tote mug", categories=None, price_min=None, price_max=None)
    page, pagination = paginate(list(range(25)), offset=0, limit=10)
    assert page == list(range(10)) and pagination["has_next_page"] is True
    second, last = paginate(list(range(25)), offset=decode_cursor(pagination["cursor"]), limit=10)
    assert second == list(range(10, 20))
    final, done = paginate(list(range(25)), offset=decode_cursor(last["cursor"]), limit=500)
    assert final == list(range(20, 25)) and done["has_next_page"] is False and "cursor" not in done
    assert decode_cursor("not-a-cursor!") is None
    assert decode_cursor(None) == 0


def test_policy_mapper_maps_well_known_types_and_namespaces_the_rest() -> None:
    payload = {
        "return_policy_summary": "Synthetic <b>7 day</b> return window.",
        "return_policy_url": "https://shop.merchant.example/returns",
        "warranty_policy_summary": "Synthetic limited warranty.",
        "warranty_policy_url": "http://insecure.example/warranty",
        "fulfillment_policy_summary": "Dispatch in 2 days.",
        "cancellation_policy_summary": "",
        "support_policy_summary": None,
        "jurisdiction_scope": ["IN"],
    }
    plain = map_oacp_policy_to_ucp_policies(payload)
    assert plain == [
        {
            "type": RETURN_POLICY_TYPE,
            "description": {"plain": "Synthetic 7 day return window."},
            "url": "https://shop.merchant.example/returns",
        },
        {"type": WARRANTY_POLICY_TYPE, "description": {"plain": "Synthetic limited warranty."}},
    ]
    namespaced = map_oacp_policy_to_ucp_policies(
        payload, vendor_namespace="example.merchant.shop", applies_to=["$.products[0]"]
    )
    assert [policy["type"] for policy in namespaced] == [
        RETURN_POLICY_TYPE,
        WARRANTY_POLICY_TYPE,
        "example.merchant.shop.policy.fulfillment",
    ]
    assert all(policy["applies_to"] == ["$.products[0]"] for policy in namespaced)
    for policy in namespaced:
        assert_ucp_valid(policy, R25_POLICY)
    assert map_oacp_policy_to_ucp_policies(payload, vendor_namespace="dev.ucp.shopping") == plain
    assert map_oacp_policy_to_ucp_policies(None) == []
    assert map_oacp_policy_to_ucp_policies({"return_policy_summary": 7}) == []


def test_policy_snapshot_is_used_only_while_its_artifact_is_verified_and_fresh() -> None:
    now_iso = _now_iso()
    assert usable_artifact_ids(cache_records=[cache_record("policy")], now_iso=now_iso, artifact_type="policy") == {
        "oacp_art_policy"
    }
    assert (
        usable_artifact_ids(cache_records=[cache_record("policy", ref=None)], now_iso=now_iso, artifact_type="policy")
        == set()
    )
