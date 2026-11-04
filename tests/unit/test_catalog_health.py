# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Catalog health (core.commerce.catalog_health): the merchant's checklist, and what is never offered to buyers."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.commerce.c6z_runtime_vertical import normalize_shopify_product_node
from core.commerce.catalog_health import ambiguous_skus, catalog_health_report, without_ambiguous_skus
from core.commerce.catalog_upload import parse_catalog_upload, upload_warnings
from core.commerce.ucp.catalog import lookup_catalog, search_catalog
from core.commerce.ucp.items import ProductEvidenceCatalog
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000b7"))
GTIN = "8901234567890"


def _variant(vid: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "variant_id": f"gid://shopify/ProductVariant/{vid}",
        "sku": f"SKU-{vid}",
        "title": "Default",
        "price": "100.00",
        "currency": "INR",
        "inventory_quantity_snapshot": 5,
        "gtin": GTIN,
    }
    base.update(fields)
    return base


def _product(ref: str, title: str, *variants: dict[str, Any], **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_ref": f"agenticorg:shopify_product:{ref}",
        "title": title,
        "description": "A good product.",
        "status": "ACTIVE",
        "images": [{"url": f"https://cdn.example/{ref}.jpg"}],
        "variants": list(variants),
        "source_system": "shopify",
    }
    base.update(fields)
    return base


def test_a_clean_catalog_has_no_problems_or_warnings() -> None:
    report = catalog_health_report([_product("a", "Masala Chai 250 g", _variant("1"))], source="shopify")
    assert report == {
        "source": "shopify",
        "product_count": 1,
        "inactive_product_count": 0,
        "variant_count": 1,
        "offered_variant_count": 1,
        "status": "ok",
        "problems": [],
        "warnings": [],
    }


def test_problems_say_the_variant_is_not_offered_and_warnings_say_what_buyers_miss() -> None:
    products = [
        _product(
            "chai",
            "Masala Chai",
            _variant("1", title="250 g", sku="CHAI"),
            _variant("2", title="500 g", sku="CHAI"),
            _variant("3", title="1 kg", sku="CHAI-1KG", price=None),
        ),
        _product("tea", "Ginger Tea Leaves", _variant("4", price="abc"), images=[], description=""),
        _product("pot", "Pot", _variant("5", inventory_quantity_snapshot=-2, sku="", gtin=None)),
        _product("mug", "Steel Mug", _variant("6", inventory_quantity_snapshot=None)),
        _product("cup", "Clay Cup Set", _variant("7", inventory_quantity_snapshot=0, inventory_tracked=False)),
        _product("jar", "Glass Jar 1 L", _variant("8", inventory_quantity_snapshot=0)),
        _product("old", "Old Product", _variant("9", price=None), status="ARCHIVED"),
    ]
    report = catalog_health_report(products, source="shopify")
    assert report["status"] == "problems"
    assert (report["product_count"], report["inactive_product_count"], report["variant_count"]) == (6, 1, 8)
    problems = {item["code"]: item for item in report["problems"]}
    warnings = {item["code"]: item for item in report["warnings"]}
    assert {code: item["count"] for code, item in problems.items()} == {
        "no_price": 2,
        "duplicate_sku": 2,
        "negative_stock": 1,
    }
    assert problems["no_price"]["examples"] == ["Masala Chai (1 kg)", "Ginger Tea Leaves"]
    assert problems["duplicate_sku"]["examples"] == ["Masala Chai (250 g)", "Masala Chai (500 g)"]
    assert problems["duplicate_sku"]["effect"].startswith("Not offered to buyers")
    assert problems["no_price"]["effect"] == "Not offered to buyers."
    assert problems["duplicate_sku"]["fix"].startswith("In Shopify, open each product")
    # Not offered: the two shared-SKU variants, two without a price and the one below zero.
    assert report["offered_variant_count"] == 3
    assert {code: item["count"] for code, item in warnings.items()} == {
        "no_sku": 1,
        "stock_unknown": 2,
        "zero_stock": 1,
        "no_image": 1,
        "no_description": 1,
        "no_barcode": 1,
        "short_title": 1,
    }
    assert warnings["no_image"]["counts"] == "products" and warnings["no_sku"]["counts"] == "variants"
    assert warnings["stock_unknown"]["examples"] == ["Steel Mug", "Clay Cup Set"]
    assert warnings["stock_unknown"]["fix"].startswith("In Shopify, open the product, tick Track quantity")
    assert warnings["short_title"]["examples"] == ["Pot"]


def test_examples_are_capped_and_an_upload_gets_upload_fixes() -> None:
    products = [
        _product(f"p{i}", f"Product number {i}", _variant(str(i)), images=[], source_system="catalog_upload")
        for i in range(6)
    ]
    report = catalog_health_report(products, source="catalog_upload")
    [warning] = report["warnings"]
    assert warning["count"] == 6 and len(warning["examples"]) == 3
    assert warning["fix"] == "Add an https:// link in the image_url column."
    assert report["status"] == "warnings"


def test_the_evidence_currency_is_the_fallback_for_a_variant_without_one() -> None:
    products = [_product("a", "Masala Chai 250 g", _variant("1", currency=None))]
    assert catalog_health_report(products, source="shopify", currency="INR")["status"] == "ok"
    no_currency = catalog_health_report(products, source="shopify")
    assert [item["code"] for item in no_currency["problems"]] == ["no_price"]


def test_a_sku_is_ambiguous_only_within_one_source() -> None:
    shopify = _product("a", "Masala Chai", _variant("1", sku="CHAI"), _variant("2", sku="CHAI"), _variant("3"))
    upload = _product("u", "Masala Chai", _variant("9", sku="CHAI-UP"), source_system="catalog_upload")
    same_sku_other_source = _product("v", "Chai", _variant("8", sku="SKU-3"), source_system="catalog_upload")
    assert ambiguous_skus([shopify, upload, same_sku_other_source]) == {("shopify", "CHAI")}
    kept = without_ambiguous_skus([shopify, upload, same_sku_other_source])
    assert [v["sku"] for v in kept[0]["variants"]] == ["SKU-3"]
    assert kept[1] is upload and kept[2] is same_sku_other_source
    assert without_ambiguous_skus([upload]) == [upload]


def test_the_catalog_does_not_list_a_shared_sku_or_a_variant_without_a_price() -> None:
    products = without_ambiguous_skus(
        [
            _product("a", "Masala Chai", _variant("1", sku="CHAI"), _variant("2", sku="CHAI"), _variant("3")),
            _product("b", "Ginger Tea", _variant("4", price=None)),
        ]
    )
    [listed] = search_catalog(products, query="chai", categories=None, price_min=None, price_max=None)
    assert [v["sku"] for v in listed["variants"]] == ["SKU-3"]
    assert search_catalog(products, query="ginger", categories=None, price_min=None, price_max=None) == []
    found, missing = lookup_catalog(products, ["CHAI", "SKU-3"])
    assert missing == [0] and [v["sku"] for v in found[0]["variants"]] == ["SKU-3"]


@pytest.mark.asyncio
async def test_the_checkout_never_sells_a_shared_sku_or_a_variant_without_a_price() -> None:
    products = [
        _product("a", "Masala Chai", _variant("1", sku="CHAI"), _variant("2", sku="CHAI"), _variant("3")),
        _product("b", "Ginger Tea", _variant("4", price="")),
    ]

    async def loader(_merchant: Any) -> list[dict[str, Any]]:
        return products

    found = await ProductEvidenceCatalog(loader).lookup(
        uf.merchant(TENANT), ["CHAI", "1", "gid://shopify/ProductVariant/2", "SKU-4", "SKU-3"]
    )
    assert set(found) == {"SKU-3"}
    assert found["SKU-3"].price_minor_units == 10000


def test_a_shopify_variant_whose_stock_is_not_tracked_is_marked() -> None:
    node = {
        "id": "gid://shopify/Product/1",
        "title": "Clay Cup",
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "price": "10.00",
                    "inventoryItem": {"id": "i1", "tracked": False},
                },
                {
                    "id": "gid://shopify/ProductVariant/2",
                    "price": "10.00",
                    "inventoryItem": {"id": "i2", "tracked": True},
                },
                {"id": "gid://shopify/ProductVariant/3", "price": "10.00", "inventoryItem": {"id": "i3"}},
            ]
        },
    }
    product = normalize_shopify_product_node(
        node,
        tenant_id=TENANT,
        merchant_id="merchant-1",
        seller_agent_id="seller-1",
        synced_at="2026-11-04T00:00:00Z",
        default_currency="INR",
    )
    assert [v.get("inventory_tracked") for v in product["variants"]] == [False, None, None]


def test_an_upload_reports_missing_images_descriptions_and_barcodes_as_warnings_not_errors() -> None:
    content = (
        "sku,title,description,price,currency,quantity,image_url,barcode\n"
        "A-1,Masala Chai 250 g,Strong blend,100,INR,5,https://cdn.example/a.jpg,8901234567890\n"
        "A-2,Ginger Tea 100 g,,120,INR,3,,\n"
        "A-3,Green Tea 100 g,,90,INR,0,https://cdn.example/c.jpg,\n"
    )
    rows = parse_catalog_upload(content, "csv")
    assert len(rows) == 3
    assert upload_warnings(rows) == [
        {
            "column": "image_url",
            "count": 1,
            "rows": [2],
            "message": "1 product has no image, so buyers' AI assistants show no photo.",
        },
        {
            "column": "description",
            "count": 2,
            "rows": [2, 3],
            "message": "2 products have no description, so buyers' AI assistants have only the title.",
        },
        {
            "column": "barcode",
            "count": 2,
            "rows": [2, 3],
            "message": "2 products have no barcode (GTIN), so they cannot be matched to the same product at "
            "other shops.",
        },
    ]
    assert upload_warnings(rows[:1]) == []
