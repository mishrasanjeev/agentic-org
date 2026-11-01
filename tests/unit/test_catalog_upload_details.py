# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Catalog upload: optional named columns and shop-declared product details, served to buyers' AI assistants."""

from __future__ import annotations

import json

import pytest

from core.commerce import c6z_runtime_vertical as c6z
from core.commerce.c6z_runtime_vertical import contains_private_or_executable_value
from core.commerce.catalog_upload import (
    CatalogUploadError,
    build_catalog_upload_evidence,
    catalog_digest,
    detail_labels,
    parse_catalog_upload,
    rows_from_products,
    skipped_columns,
    upload_products,
)
from core.commerce.network_telegram import _product_summary
from core.commerce.ucp.catalog import _search_text, ucp_product

GHEE_CSV = (
    "sku,title,price,currency,quantity,url,Brand,Category,tags,MRP,barcode,"
    "Ingredients,Allergens,Shelf life,FSSAI licence no,Lab report,_cost\n"
    "GHEE-500,A2 cow ghee 500 ml,899,INR,40,https://shop.example.in/ghee,Kaveri Dairy,Ghee,ghee; a2 | bilona,999,"
    '8901234567890,"Cow milk fat. Contains milk. Store in a cool dry place",Milk,9 months,10019022008571,'
    "https://labreports.example-dairy.in/batch-2026,420\n"
    "GHEE-1L,A2 cow ghee 1 litre,1699,INR,12,,Kaveri Dairy,Ghee,,,,,Milk,,10019022008571,,\n"
)


def _test_jwt() -> str:
    """An invented token in JWS compact form, built at run time so the file holds no token literal."""
    import base64
    import json

    def part(value: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return ".".join([part({"alg": "HS256", "typ": "JWT"}), part({"sub": "test-only"}), "c2lnbmF0dXJlLXRlc3Qtb25seQ"])


JWT = _test_jwt()


def _products(rows: list[dict]) -> list[dict]:
    return upload_products(
        rows=rows,
        tenant_id="t",
        merchant_id="m",
        seller_agent_id="s",
        synced_at="2026-11-01T00:00:00Z",
        uploaded_at="2026-11-01T00:00:00Z",
    )


def _packet() -> dict:
    return c6z.build_seller_onboarding_packet(
        tenant_id="11111111-1111-4111-8111-111111111111",
        merchant_id="ghee-shop-1a2b3c",
        seller_agent_id="seller_agent_ghee_shop_1a2b3c",
        merchant_display_name="Kaveri Dairy",
        public_brand_profile={"display_name": "Kaveri Dairy"},
        commerce_categories=["food"],
        requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
        artifact_cache_scope={"merchant_id": "ghee-shop-1a2b3c"},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={},
        connector_choice=c6z.CATALOG_UPLOAD_SOURCE_SYSTEM,
    )


def test_extra_columns_become_product_details_in_column_order_and_empty_cells_are_left_out() -> None:
    rows = parse_catalog_upload(GHEE_CSV, "csv")
    first, second = rows
    assert first["brand"] == "Kaveri Dairy" and first["category"] == "Ghee"
    assert first["tags"] == ["ghee", "a2", "bilona"]
    assert first["compare_at_price"] == "999.00" and first["gtin"] == "8901234567890"
    assert [item["label"] for item in first["attributes"]] == [
        "Ingredients",
        "Allergens",
        "Shelf life",
        "FSSAI licence no",
        "Lab report",
    ]
    assert first["attributes"][3] == {
        "name": "fssai_licence_no",
        "label": "FSSAI licence no",
        "value": "10019022008571",
    }
    assert [item["name"] for item in second["attributes"]] == ["allergens", "fssai_licence_no"]
    assert "tags" not in second and "gtin" not in second and "compare_at_price" not in second
    assert detail_labels(rows) == ["Ingredients", "Allergens", "Shelf life", "FSSAI licence no", "Lab report"]
    assert skipped_columns(GHEE_CSV, "csv") == ["_cost"]
    assert all("_cost" not in json.dumps(row) and "420" not in json.dumps(row.get("attributes")) for row in rows)


def test_a_reread_rebuilds_exactly_the_uploaded_rows_so_the_evidence_id_is_stable() -> None:
    rows = parse_catalog_upload(GHEE_CSV, "csv")
    back = rows_from_products(_products(rows))
    assert back == rows
    assert catalog_digest(back) == catalog_digest(rows)
    first = build_catalog_upload_evidence(
        packet=_packet(), rows=rows, uploaded_at="2026-11-01T00:00:00Z", observed_at="2026-11-01T00:00:00Z"
    )
    again = build_catalog_upload_evidence(
        packet=_packet(), rows=back, uploaded_at="2026-11-01T00:00:00Z", observed_at="2026-11-01T01:00:00Z"
    )
    assert first["evidence_id"] == again["evidence_id"]
    assert first["products"][0]["attributes"] == rows[0]["attributes"]


def test_a_catalog_with_only_the_original_columns_keeps_its_rows_and_digest() -> None:
    rows = parse_catalog_upload("sku,title,price,currency,quantity\nCHAI-1,Masala chai,250,INR,5\n", "csv")
    assert sorted(rows[0]) == ["currency", "description", "image_url", "price", "quantity", "sku", "title", "url"]
    assert rows_from_products(_products(rows)) == rows


def test_json_products_take_extra_keys_and_an_attributes_object() -> None:
    content = json.dumps(
        {
            "products": [
                {
                    "sku": "GHEE-250",
                    "title": "Ghee 250 ml",
                    "price": "499",
                    "currency": "INR",
                    "quantity": 3,
                    "tags": ["ghee", "small pack"],
                    "certifications": ["FSSAI", "Organic India"],
                    "weight_grams": 230,
                    "attributes": {"Country of origin": "India", "Vegetarian": True},
                    "_supplier_note": "never shown",
                }
            ]
        }
    )
    row = parse_catalog_upload(content, "json")[0]
    assert row["tags"] == ["ghee", "small pack"]
    assert {item["name"]: item["value"] for item in row["attributes"]} == {
        "certifications": "FSSAI; Organic India",
        "weight_grams": "230",
        "country_of_origin": "India",
        "vegetarian": "yes",
    }
    assert skipped_columns(content, "json") == ["_supplier_note"]


@pytest.mark.parametrize(
    ("column", "message"),
    [
        ("cost", "private business data"),
        ("Supplier name", "private business data"),
        ("profit_margin", "private business data"),
        ("descripton", "misspelled description"),
        ("brands", "misspelled brand"),
        ("api token", "reserved for credentials"),
        ("fssai_authorization", "reserved for credentials"),
        ("9 months", "starting with a letter"),
    ],
)
def test_detail_columns_that_would_leak_or_mislead_are_refused_by_name(column: str, message: str) -> None:
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(f"sku,title,price,currency,quantity,{column}\nA-1,Ghee,1,INR,1,x\n", "csv")
    assert refused.value.errors[0]["column"] == column
    assert message in refused.value.errors[0]["message"]


def test_detail_values_are_checked_and_named_by_row_and_column() -> None:
    content = (
        "sku,title,price,currency,quantity,Lab report,Notes,barcode,tags\n"
        "A-1,Ghee,1,INR,1,http://labs.example.in/r1,shpat_0123456789abcdef,8901234567891,ok\n"
    )
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(content, "csv")
    problems = {(item["row"], item["column"]) for item in refused.value.errors}
    assert {(1, "Lab report"), (1, "Notes"), (1, "gtin")} <= problems


def test_two_columns_that_read_the_same_and_too_many_details_are_refused() -> None:
    with pytest.raises(CatalogUploadError, match="problem"):
        parse_catalog_upload("sku,title,price,currency,quantity,Shelf life,shelf_life\nA,B,1,INR,1,x,y\n", "csv")
    many = ",".join(f"detail {index}" for index in range(31))
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(f"sku,title,price,currency,quantity,{many}\nA,B,1,INR,1{',x' * 31}\n", "csv")
    assert any("more than 30" in item["message"] for item in refused.value.errors)


def test_barcode_check_digits() -> None:
    for valid in ("4006381333931", "9780306406157", "036000291452", "96385074"):
        rows = parse_catalog_upload(f"sku,title,price,currency,quantity,gtin\nA,B,1,INR,1,{valid}\n", "csv")
        assert rows[0]["gtin"] == valid
    with pytest.raises(CatalogUploadError):
        parse_catalog_upload("sku,title,price,currency,quantity,gtin\nA,B,1,INR,1,4006381333932\n", "csv")


def test_food_prose_and_report_links_are_not_mistaken_for_credentials() -> None:
    assert not contains_private_or_executable_value("Cow milk fat. Contains milk. Store in a cool dry place")
    assert not contains_private_or_executable_value("A basic ghee for daily cooking")
    assert not contains_private_or_executable_value("https://labreports.mybrandname.com/report-2026")
    # Real credentials are still caught.
    assert contains_private_or_executable_value(JWT)
    assert contains_private_or_executable_value("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==")
    assert contains_private_or_executable_value("Bearer abc")


def test_buyers_ai_assistants_read_the_details_brand_tags_link_mrp_and_barcode() -> None:
    rows = parse_catalog_upload(GHEE_CSV, "csv")
    product = dict(_products(rows)[0], _evidence_currency="INR")
    served = ucp_product(product)
    assert served is not None
    assert served["url"] == "https://shop.example.in/ghee"
    assert served["tags"] == ["ghee", "a2", "bilona"]
    assert served["categories"] == [{"value": "Ghee"}]
    assert served["metadata"]["brand"] == "Kaveri Dairy"
    assert served["metadata"]["attributes_declared_by"] == "shop"
    assert {"name": "Lab report", "value": "https://labreports.example-dairy.in/batch-2026"} in served["metadata"][
        "attributes"
    ]
    variant = served["variants"][0]
    assert variant["barcodes"] == [{"type": "GTIN", "value": "8901234567890"}]
    assert variant["list_price"]["amount"] == 99900
    assert "fssai licence no 10019022008571" in _search_text(product)
    summary = _product_summary("kaveri", served)
    assert summary["brand"] == "Kaveri Dairy"
    assert summary["details_declared_by_shop"][0].startswith("Ingredients: Cow milk fat")


def test_a_product_without_details_or_brand_has_no_metadata() -> None:
    rows = parse_catalog_upload("sku,title,price,currency,quantity\nCHAI-1,Masala chai,250,INR,5\n", "csv")
    served = ucp_product(dict(_products(rows)[0], _evidence_currency="INR"))
    assert served is not None
    assert "metadata" not in served and "tags" not in served and "url" not in served


def test_the_example_file_on_the_setup_page_uploads_cleanly() -> None:
    from pathlib import Path

    content = Path("ui/public/media/catalog-example.csv").read_text(encoding="utf-8")
    rows = parse_catalog_upload(content, "csv")
    assert len(rows) == 2
    assert skipped_columns(content, "csv") == ["_cost_price"]
    assert "Lab report" in detail_labels(rows)


# --- review fixes ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        f"token {JWT}",
        f"jwt={JWT}",
        f"{JWT}\nnext line",
        f"{JWT}=",
        "Basic dXNlcjpwYXNz",
        "BASIC dXNlcjpwYXNz",
        "Authorization: Basic dXNlcjpwYXNz",
        "Basic YWI6Y2Q=",
        "Basic dTpw",
    ],
)
def test_tokens_inside_text_and_short_basic_credentials_are_still_caught(value: str) -> None:
    assert contains_private_or_executable_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "basic pack of 6",
        "Wheat. Sugar. Salt",
        "v1.2.3 build 2026.11.01",
        "See https://shop.example.com/a.b.c for details",
    ],
)
def test_ordinary_text_is_not_a_credential(value: str) -> None:
    assert not contains_private_or_executable_value(value)


def test_excel_trailing_empty_columns_are_ignored() -> None:
    rows = parse_catalog_upload("sku,title,price,currency,quantity,,\r\nA1,Ghee,499,INR,3,,\r\n", "csv")
    assert rows[0]["sku"] == "A1" and "attributes" not in rows[0]


@pytest.mark.parametrize(
    "header",
    ["category,product_type", "compare_at_price,mrp", "gtin,barcode", "brand,brand"],
)
def test_a_column_and_its_alias_or_a_repeated_column_are_refused(header: str) -> None:
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(f"sku,title,price,currency,quantity,{header}\nA1,Ghee,499,INR,3,x,\n", "csv")
    assert any("not both" in item["message"] or "twice" in item["message"] for item in refused.value.errors)


def test_json_alias_conflicts_and_non_text_brand_are_refused() -> None:
    base = {"sku": "G", "title": "Ghee", "price": "1", "currency": "INR", "quantity": 1}
    with pytest.raises(CatalogUploadError, match="problem"):
        parse_catalog_upload(json.dumps([{**base, "mrp": "5", "compare_at_price": None}]), "json")
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(json.dumps([{**base, "brand": ["Amul"]}]), "json")
    assert refused.value.errors[0]["column"] == "brand"


@pytest.mark.parametrize(
    "column", ["CostPrice", "buying price", "COGS", "Costs", "dealer price", "landed", "Profit margin %"]
)
def test_one_word_and_plural_private_business_names_are_refused(column: str) -> None:
    with pytest.raises(CatalogUploadError, match="problem"):
        parse_catalog_upload(f"sku,title,price,currency,quantity,{column}\nA,B,1,INR,1,2\n", "csv")


def test_an_action_name_with_dots_is_refused() -> None:
    with pytest.raises(CatalogUploadError, match="problem"):
        parse_catalog_upload("sku,title,price,currency,quantity,order.create\nA,B,1,INR,1,yes\n", "csv")


def test_underscore_keys_inside_a_json_attributes_object_are_skipped_and_reported() -> None:
    content = json.dumps(
        [{"sku": "G", "title": "Ghee", "price": "1", "currency": "INR", "quantity": 1,
          "attributes": {"Ingredients": "milk", "_notes": "batch 7 had complaints"}}]
    )  # fmt: skip
    row = parse_catalog_upload(content, "json")[0]
    assert row["attributes"] == [{"name": "ingredients", "label": "Ingredients", "value": "milk"}]
    assert skipped_columns(content, "json") == ["attributes._notes"]


def test_links_with_credentials_are_refused_and_upper_case_links_are_served() -> None:
    with pytest.raises(CatalogUploadError, match="problem"):
        parse_catalog_upload(
            "sku,title,price,currency,quantity,Lab report\nA,B,1,INR,1,Report: https://user:pass@lab.example.com/r\n",
            "csv",
        )
    rows = parse_catalog_upload(
        "sku,title,price,currency,quantity,url,Lab report,Protocol\n"
        "A,B,1,INR,1,HTTPS://Shop.Example.in/g,HTTPS://labs.example.in/r1,HTTP/2 and HTTP/3\n",
        "csv",
    )
    served = ucp_product(dict(_products(rows)[0], _evidence_currency="INR"))
    assert served is not None
    assert served["url"] == "https://shop.example.in/g"
    assert {"name": "Lab report", "value": "https://labs.example.in/r1"} in served["metadata"]["attributes"]
    assert {"name": "Protocol", "value": "HTTP/2 and HTTP/3"} in served["metadata"]["attributes"]


def test_column_names_in_any_script_are_details() -> None:
    rows = parse_catalog_upload(
        "sku,title,price,currency,quantity,सामग्री,Région d'origine\nA1,Ghee,1,INR,1,दूध,France\n", "csv"
    )
    assert [item["label"] for item in rows[0]["attributes"]] == ["सामग्री", "Région d'origine"]
    assert rows_from_products(_products(rows)) == rows


def test_telegram_search_results_fit_the_tool_cap_and_take_shops_in_turn() -> None:
    from core.commerce.network_telegram import SEARCH_RESULT_BUDGET, _fit_search_results

    items = [
        {"shop": f"s{shop}", "title": "T" * 50, "description": "d" * 300, "details_declared_by_shop": ["x" * 100] * 4}
        for shop in range(10)
        for _ in range(5)
    ]
    body = _fit_search_results(items)
    assert len(json.dumps(body)) <= SEARCH_RESULT_BUDGET
    assert body["more_results_left_out"] is True
    assert [item["shop"] for item in body["products"][:10]] == [f"s{shop}" for shop in range(10)]
