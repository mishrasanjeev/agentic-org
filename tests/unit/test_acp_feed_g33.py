# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G33: the ACP product feed validates against the vendored ACP 2026-04-17 feed schema; CSV per the column rules."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from core.commerce.acp import feed as acp_feed
from tests.ucp_fixtures import HOST, MERCHANT_ID, SELLER_AGENT_ID, TENANT_ID, catalog_records, synced_products

SCHEMA_FILE = (
    Path(__file__).resolve().parents[2] / "core" / "commerce" / "acp" / "schemas" / "2026-04-17" / "schema.feed.json"
)


def _validator(definition: str) -> Draft202012Validator:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _errors(definition: str, document: object) -> list[str]:
    return [f"{list(e.absolute_path)}: {e.message}" for e in _validator(definition).iter_errors(document)]


def _config(**acp: object) -> dict:
    return {
        "merchant_id": MERCHANT_ID,
        "merchant_display_name": "Example Store",
        "provider_policy": {
            "ucp": {
                "links": [
                    {"type": "privacy_policy", "url": "https://merchant.example/privacy"},
                    {"type": "terms_of_service", "url": "https://merchant.example/terms"},
                    {"type": "shipping_policy", "url": "https://merchant.example/shipping"},
                ],
                "permalink_endpoint": "https://merchant.example/buy",
            },
            "acp": {"checkout_enabled": True, **acp},
        },
    }


POLICY = {
    "return_policy_summary": "Returns within 30 days.",
    "return_policy_url": "https://merchant.example/returns",
    "accepts_returns": True,
    "return_window_days": 30,
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _feed(*, checkout_flag: bool = True, policy: dict | None = POLICY, **acp: object) -> acp_feed.Feed:
    seller = acp_feed.seller_from_config(
        _config(**acp), serving_host=HOST, verified_policy=policy, checkout_flag=checkout_flag
    )
    return acp_feed.build_feed(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        cache_records=catalog_records(),
        products=synced_products(),
        seller=seller,
        now_iso=_now_iso(),
    )


def test_vendored_feed_schema_is_the_2026_04_17_bundle() -> None:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "Feed - Schema Bundle"
    assert {"Product", "Variant", "ProductsResponse", "FeedMetadata"} <= set(schema["$defs"])
    Draft202012Validator.check_schema(schema)


def test_feed_json_validates_against_the_acp_2026_04_17_feed_schema() -> None:
    feed = _feed()
    assert [p["title"] for p in feed.products] == ["Canvas Tote", "Linen Apron"]  # the draft product is not listed
    assert _errors("ProductsResponse", feed.products_response()) == []
    for product in feed.products:
        assert _errors("Product", product) == []
    for line in feed.products_jsonl().splitlines():
        assert _errors("Product", json.loads(line)) == []
    metadata = feed.feed_metadata()
    assert _errors("FeedMetadata", metadata) == []
    assert metadata["id"].startswith("feed_")


def test_feed_variants_carry_availability_seller_returns_and_checkout_item_ids() -> None:
    from core.commerce.ucp.catalog import ucp_variant_id

    tote = _feed().products[0]
    natural, black = tote["variants"]
    assert natural["id"] == ucp_variant_id({"variant_id": "gid://shopify/ProductVariant/1001"})
    assert natural["price"] == {"amount": 129_900, "currency": "INR"}
    assert natural["list_price"] == {"amount": 149_900, "currency": "INR"}
    assert natural["availability"] == {"available": True, "status": "in_stock"}
    assert black["availability"] == {"available": False, "status": "out_of_stock"}
    assert natural["variant_options"] == [{"name": "Colour", "value": "Natural"}]
    links = {link["type"]: link["url"] for link in natural["seller"]["links"]}
    assert natural["seller"]["name"] == "Example Store"
    assert links["refund_policy"] == "https://merchant.example/returns"
    assert links["privacy_policy"] == "https://merchant.example/privacy"
    assert natural["url"].startswith("https://merchant.example/buy/")
    assert "enable_checkout" not in json.dumps(tote)  # not a member of the ACP feed schema


def test_feed_is_refused_without_verified_fresh_artifacts() -> None:
    seller = acp_feed.seller_from_config(_config(), serving_host=HOST, verified_policy=None, checkout_flag=True)
    for records in (catalog_records()[:-1], catalog_records(ref=None), catalog_records(ref="status=invalid")):
        with pytest.raises(acp_feed.FeedUnavailableError):
            acp_feed.build_feed(
                tenant_id=TENANT_ID,
                merchant_id=MERCHANT_ID,
                seller_agent_id=SELLER_AGENT_ID,
                cache_records=records,
                products=synced_products(),
                seller=seller,
                now_iso=_now_iso(),
            )


def _rows(feed: acp_feed.Feed) -> list[dict[str, str]]:
    text = feed.csv_text()
    assert text.splitlines()[0] == ",".join(acp_feed.CSV_COLUMNS)
    return list(csv.DictReader(io.StringIO(text)))


def test_csv_follows_the_column_rules() -> None:
    feed = _feed()
    rows = _rows(feed)
    # The apron has no image (image_url is required): left out and counted.
    assert [r["title"] for r in rows] == ["Natural", "Black"]
    assert feed.skipped_rows == 1
    natural = rows[0]
    for column in acp_feed.REQUIRED_CSV_COLUMNS:
        assert natural[column]
    assert natural["price"] == "1499.00 INR" and natural["sale_price"] == "1299.00 INR"
    assert natural["availability"] == "in_stock" and rows[1]["availability"] == "out_of_stock"
    assert natural["brand"] == "Merchant Example" and natural["seller_name"] == "Example Store"
    assert natural["seller_url"] == f"https://{HOST}"
    assert natural["accepts_returns"] == "true" and natural["return_deadline_in_days"] == "30"
    assert natural["return_policy"] == "https://merchant.example/returns"
    assert natural["enable_search"] == "true" and natural["enable_checkout"] == "true"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"checkout_flag": False}, "false"),
        ({"checkout_enabled": False}, "false"),
        ({"feed": {"enable_search": False}}, "false"),
    ],
)
def test_enable_checkout_needs_search_the_flag_and_the_merchant_opt_in(kwargs, expected) -> None:
    rows = _rows(_feed(**kwargs))
    assert {r["enable_checkout"] for r in rows} == {expected}


def test_enable_checkout_needs_privacy_and_terms_links() -> None:
    config = _config()
    config["provider_policy"]["ucp"]["links"] = []
    seller = acp_feed.seller_from_config(config, serving_host=HOST, verified_policy=None, checkout_flag=True)
    assert seller.checkout_available is True
    feed = acp_feed.build_feed(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        cache_records=catalog_records(),
        products=synced_products(),
        seller=seller,
        now_iso=_now_iso(),
    )
    rows = _rows(feed)
    assert {r["enable_checkout"] for r in rows} == {"false"}
    assert {r["accepts_returns"] for r in rows} == {""}  # no verified return terms


def test_csv_neutralises_formula_cells_and_quotes_per_rfc_4180() -> None:
    products = synced_products()
    products[0]["title"] = '=HYPERLINK("https://attacker.example")'
    products[0]["variants"][0]["title"] = 'Natural, "large"'
    seller = acp_feed.seller_from_config(_config(), serving_host=HOST, verified_policy=None, checkout_flag=True)
    feed = acp_feed.build_feed(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        cache_records=catalog_records(),
        products=products,
        seller=seller,
        now_iso=_now_iso(),
    )
    assert '"Natural, ""large"""' in feed.csv_text()
    rows = _rows(feed)
    assert rows[0]["title"] == 'Natural, "large"'
    assert rows[1]["title"] == "Black"
    products[0]["variants"][1]["title"] = "-1 cm"
    feed = acp_feed.build_feed(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        cache_records=catalog_records(),
        products=products,
        seller=seller,
        now_iso=_now_iso(),
    )
    assert _rows(feed)[1]["title"] == "'-1 cm"


def test_product_url_template_wins_over_the_permalink() -> None:
    feed = _feed(feed={"product_url_template": "https://merchant.example/p/{item_id}"})
    url = feed.products[0]["variants"][0]["url"]
    assert url.startswith("https://merchant.example/p/") and "{item_id}" not in url
