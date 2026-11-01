# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Shop-wide details (set once on the setup page) and product details read from a Shopify store."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from core.commerce import c6z_runtime_vertical as c6z
from core.commerce.catalog_upload import (
    MAX_SHOP_DETAILS,
    CatalogUploadError,
    declared_detail,
    gtin_or_none,
    parse_catalog_upload,
    public_tags,
    shop_details_from_entries,
    shop_details_of,
    upload_products,
)
from core.commerce.network_telegram import _fit_search_results, _product_summary
from core.commerce.shopify_product_details import metafield_details, metafield_text, product_extras
from core.commerce.ucp.catalog import lookup_catalog, search_catalog, ucp_product

LICENCE = {"label": "FSSAI licence number", "value": "10019022008571"}
CERTIFIED = {"label": "Certified organic", "value": "India Organic, certificate ORG-2026-114"}
_CREDENTIAL_DETAIL_NAMES = (
    "API key", "APIKey", "api.key", "API-key", "Access key", "AccessKey", "ClientSecret", "PrivateKey",
    "CheckoutCreate", "paymentCreate", "\uff21\uff30\uff29 \uff4b\uff45\uff59", "Client Secret",
)


def _upload_product() -> dict[str, Any]:
    return upload_products(
        rows=[
            {
                "sku": "GHEE-500",
                "title": "A2 cow ghee 500 ml",
                "description": "Slow-cooked ghee",
                "price": "899.00",
                "currency": "INR",
                "quantity": 4,
                "attributes": [{"name": "ingredients", "label": "Ingredients", "value": "Cow milk fat"}],
            }
        ],
        tenant_id="t",
        merchant_id="m",
        seller_agent_id="s",
        synced_at="2026-11-01T00:00:00Z",
        uploaded_at="2026-11-01T00:00:00Z",
    )[0]


# --- shop details ---------------------------------------------------------------------------------


def test_shop_details_are_checked_like_product_details_and_blank_rows_are_ignored() -> None:
    details = shop_details_from_entries([LICENCE, {"label": "", "value": "  "}, CERTIFIED])
    assert details == [
        {"name": "fssai_licence_number", "label": "FSSAI licence number", "value": "10019022008571"},
        {"name": "certified_organic", "label": "Certified organic", "value": CERTIFIED["value"]},
    ]
    link = shop_details_from_entries([{"label": "Lab reports", "value": "HTTPS://Labs.Example.in/ghee"}])
    assert link[0]["value"] == "https://labs.example.in/ghee"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"label": "Supplier", "value": "Nandini"}, "private business data"),
        ({"label": "Cost price", "value": "420"}, "private business data"),
        ({"label": "API token", "value": "abc"}, "reserved for credentials"),
        ({"label": "Lab report", "value": "http://labs.example.in/ghee"}, "https://"),
        ({"label": "Login", "value": "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw=="}, "credential"),
        ({"label": "Lab report", "value": "https://user:pw@labs.example.in/x"}, "user name or password"),
        ({"label": "", "value": "10019022008571"}, "needs a name"),
        ({"label": "Licence", "value": ""}, "needs a value"),
        ({"label": "9 to 5", "value": "open"}, "starting with a letter"),
    ],
)
def test_shop_details_refuse_what_should_not_be_published(entry: dict[str, str], message: str) -> None:
    with pytest.raises(CatalogUploadError) as caught:
        shop_details_from_entries([LICENCE, entry])
    assert caught.value.errors[0]["row"] == 2
    assert message in caught.value.errors[0]["message"]


@pytest.mark.parametrize("label", _CREDENTIAL_DETAIL_NAMES)
def test_credential_detail_names_are_refused_and_hidden_when_already_stored(label: str) -> None:
    entry = {"label": label, "value": "synthetic-placeholder"}
    with pytest.raises(CatalogUploadError) as caught:
        shop_details_from_entries([entry])
    assert caught.value.errors[0]["row"] == 1
    assert "reserved for credentials" in caught.value.errors[0]["message"]
    assert shop_details_of({"shop_details": [entry]}) == []
    assert public_tags([label]) == []


@pytest.mark.parametrize("label", _CREDENTIAL_DETAIL_NAMES)
@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_uploaded_product_details_refuse_the_same_credential_names(label: str, fmt: str) -> None:
    product = {"sku": "SYNTHETIC-1", "title": "Synthetic item", "price": "1.00", "currency": "INR", "quantity": 1}
    if fmt == "json":
        content = json.dumps([{**product, "attributes": {label: "synthetic-placeholder"}}])
    else:
        content = "sku,title,price,currency,quantity," + label + "\n"
        content += "SYNTHETIC-1,Synthetic item,1.00,INR,1,synthetic-placeholder\n"
    with pytest.raises(CatalogUploadError):
        parse_catalog_upload(content, fmt)


@pytest.mark.parametrize("label", ["Key features", "Key ingredients", "Product access instructions"])
def test_public_details_without_a_credential_or_action_name_remain_available(label: str) -> None:
    details = shop_details_from_entries([{"label": label, "value": "Synthetic public statement"}])
    assert details[0]["label"] == label


def test_shop_details_refuse_repeats_and_more_than_the_limit() -> None:
    with pytest.raises(CatalogUploadError, match="1 problem"):
        shop_details_from_entries([LICENCE, {"label": "FSSAI licence-number", "value": "x"}])
    many = [{"label": f"Detail {chr(97 + index)}", "value": "yes"} for index in range(MAX_SHOP_DETAILS + 1)]
    with pytest.raises(CatalogUploadError) as caught:
        shop_details_from_entries(many)
    assert caught.value.errors[-1]["message"] == f"at most {MAX_SHOP_DETAILS} shop details"


def test_shop_detail_names_are_not_matched_against_catalog_columns() -> None:
    # "Description" and "Price list" would be refused as a misspelled column in an upload, not here.
    assert shop_details_from_entries([{"label": "Description", "value": "Family dairy since 1952"}])
    detail, problem = declared_detail("Price list", "Price list", "https://shop.example.in/prices")
    assert problem is None and detail is not None


def test_stored_shop_details_are_read_defensively() -> None:
    assert shop_details_of(None) == [] and shop_details_of({"shop_details": "x"}) == []
    stored = {"shop_details": [{"name": "a", "label": "A", "value": "1"}, {"name": "b"}, "junk"]}
    assert shop_details_of(stored) == [{"name": "a", "label": "A", "value": "1"}]


def test_every_served_product_carries_the_shop_details_but_search_does_not_match_them() -> None:
    product = _upload_product()
    shop = shop_details_from_entries([LICENCE])
    served = ucp_product(product, shop)
    assert served is not None
    assert served["metadata"]["shop_details"] == [{"name": "FSSAI licence number", "value": "10019022008571"}]
    assert served["metadata"]["shop_details_declared_by"] == "shop"
    assert served["metadata"]["attributes"] == [{"name": "Ingredients", "value": "Cow milk fat"}]
    assert ucp_product(product)["metadata"].get("shop_details") is None
    assert (
        search_catalog([product], query="fssai", categories=None, price_min=None, price_max=None, shop_details=shop)
        == []
    )
    found = search_catalog([product], query="ghee", categories=None, price_min=None, price_max=None, shop_details=shop)
    assert found[0]["metadata"]["shop_details"]
    looked_up, missing = lookup_catalog([product], [found[0]["id"]], shop_details=shop)
    assert not missing and looked_up[0]["metadata"]["shop_details"]


def test_served_shop_details_skip_stored_values_that_became_unsafe() -> None:
    stored = [
        {"name": "a", "label": "Plain", "value": "yes"},
        {"name": "b", "label": "Old link", "value": "http://x.example/a"},
        {"name": "c", "label": "Login", "value": "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw=="},
    ]
    served = ucp_product(_upload_product(), stored)
    assert served is not None and served["metadata"]["shop_details"] == [{"name": "Plain", "value": "yes"}]


def test_merchant_config_keeps_shop_details_with_links() -> None:
    from core.commerce.oacp_merchant_config import _contains_unsafe_config_value, _safe_mapping

    details = shop_details_from_entries([LICENCE, {"label": "Lab reports", "value": "https://labs.example.in/ghee"}])
    policy = _safe_mapping({"shop_details": details})
    assert policy["shop_details"] == details and not _contains_unsafe_config_value(policy)


class _Ctx:
    async def __aenter__(self) -> Any:
        return object()

    async def __aexit__(self, *_exc: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_the_setup_route_replaces_the_list_and_an_empty_list_removes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from api.v1 import commerce_merchant_setup as setup_api

    row = SimpleNamespace(provider_policy={"arbiter": {"max_quantity_per_order": 5}, "shop_details": [{"x": 1}]})
    saved: list[dict[str, Any]] = []

    async def config_row(_session: Any, _tenant: str, _merchant: str) -> Any:
        return row

    async def save(_session: Any, **kwargs: Any) -> dict[str, Any]:
        saved.append(kwargs)
        row.provider_policy = kwargs["changes"]["provider_policy"]
        return {}

    async def view(_tenant: str, _merchant: str) -> dict[str, Any]:
        return {"shop_details": shop_details_of(row.provider_policy)}

    monkeypatch.setattr(setup_api, "get_tenant_session", lambda _tid: _Ctx())
    monkeypatch.setattr(setup_api, "_config_row", config_row)
    monkeypatch.setattr(setup_api, "_save_config", save)
    monkeypatch.setattr(setup_api, "setup_view", view)
    tenant = "11111111-1111-4111-8111-111111111111"

    body = setup_api.ShopDetailsRequest(details=[setup_api.ShopDetail(**LICENCE)])
    result = await setup_api.put_shop_details("ghee-shop", body, tenant)
    assert result["shop_details"][0]["value"] == "10019022008571"
    assert row.provider_policy["arbiter"] == {"max_quantity_per_order": 5}
    assert saved[0]["payload"] == {"shop_detail_count": 1}
    assert saved[0]["event"] == "commerce.runtime.merchant_setup.shop_details"

    await setup_api.put_shop_details("ghee-shop", setup_api.ShopDetailsRequest(details=[]), tenant)
    assert "shop_details" not in row.provider_policy

    bad = setup_api.ShopDetailsRequest(details=[setup_api.ShopDetail(label="Margin", value="30%")])
    with pytest.raises(HTTPException) as caught:
        await setup_api.put_shop_details("ghee-shop", bad, tenant)
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "shop_details_invalid"
    assert caught.value.detail["errors"][0]["column"] == "Margin"


# --- Shopify product details ----------------------------------------------------------------------


def _metafield(key: str, kind: str, value: Any, *, name: str | None = None, access: str | None = "PUBLIC_READ") -> dict:
    definition = None if name is None and access is None else {"name": name or key, "access": {"storefront": access}}
    return {
        "namespace": "custom",
        "key": key,
        "type": kind,
        "value": value if isinstance(value, str) else json.dumps(value),
        "definition": definition,
    }


def test_only_metafields_the_storefront_can_read_become_details() -> None:
    nodes = [
        _metafield("ingredients", "multi_line_text_field", "Cow milk fat", name="Ingredients"),
        _metafield("batch_notes", "single_line_text_field", "QA hold", name="Batch notes", access="NONE"),
        {**_metafield("no_definition", "single_line_text_field", "x"), "definition": None},
        _metafield("supplier", "single_line_text_field", "Nandini", name="Dairy partner"),
        _metafield("dairy_partner", "single_line_text_field", "Nandini", name="Cost note"),
        _metafield("login", "single_line_text_field", "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw=="),
        _metafield("lab_report", "url", "http://labs.example.in/x", name="Lab report"),
        _metafield("lab_report_2", "url", "https://labs.example.in/x", name="Lab report link"),
        _metafield("hero", "file_reference", "gid://shopify/MediaImage/1", name="Hero image"),
        _metafield("specs", "json", {"a": 1}, name="Specs"),
        {**_metafield("ingredients", "single_line_text_field", "dup", name="Ingredients"), "namespace": "other"},
    ]
    details = metafield_details(nodes)
    assert details == [
        {"name": "ingredients", "label": "Ingredients", "value": "Cow milk fat"},
        {"name": "lab_report_link", "label": "Lab report link", "value": "https://labs.example.in/x"},
    ]


@pytest.mark.parametrize(
    ("kind", "value", "text"),
    [
        ("boolean", "true", "yes"),
        ("boolean", "false", "no"),
        ("number_decimal", "4.50", "4.50"),
        ("date", "2027-06-30", "2027-06-30"),
        ("rating", {"value": "4.50", "scale_min": "1.0", "scale_max": "5.0"}, "4.5 out of 5"),
        ("weight", {"value": 0.5, "unit": "KILOGRAMS"}, "0.5 kilograms"),
        ("volume", {"value": 500, "unit": "MILLILITERS"}, "500 milliliters"),
        ("money", {"amount": "999.00", "currency_code": "inr"}, "999.00 INR"),
        ("list.single_line_text_field", ["Milk", "Butter"], "Milk; Butter"),
        (
            "list.weight",
            [{"value": 1, "unit": "KILOGRAMS"}, {"value": 2, "unit": "KILOGRAMS"}],
            "1 kilograms; 2 kilograms",
        ),
        (
            "rich_text_field",
            {
                "type": "root",
                "children": [{"type": "paragraph", "children": [{"type": "text", "value": "Store cool."}]}],
            },
            "Store cool.",
        ),
        ("list.product_reference", ["gid://shopify/Product/1"], None),
        ("json", {"a": 1}, None),
        ("link", {"text": "x", "url": "https://x.example"}, None),
    ],
)
def test_metafield_values_become_plain_text(kind: str, value: Any, text: str | None) -> None:
    assert metafield_text(kind, value if isinstance(value, str) else json.dumps(value)) == text


def test_shopify_tags_barcodes_and_store_link() -> None:
    assert public_tags(["Ghee", "ghee", "wholesale", "internal only", "A2", None, "x" * 60]) == ["Ghee", "A2"]
    assert public_tags("Ghee, A2") == []
    assert gtin_or_none("8901234567890") == "8901234567890"
    assert gtin_or_none("8901234567891") is None and gtin_or_none("ISBN-1") is None and gtin_or_none(None) is None
    extras = product_extras({"tags": ["Ghee"], "onlineStoreUrl": "https://shop.example.in/products/ghee"})
    assert extras == {"tags": ("Ghee",), "url": "https://shop.example.in/products/ghee"}
    assert product_extras({"onlineStoreUrl": None, "tags": [], "metafields": {"nodes": []}}) == {}


def _shopify_node(**extra: Any) -> dict[str, Any]:
    return {
        "id": "gid://shopify/Product/1",
        "title": "A2 cow ghee 500 ml",
        "descriptionHtml": "<p>Slow-cooked</p>",
        "vendor": "Kaveri Dairy",
        "productType": "Ghee",
        "status": "ACTIVE",
        "updatedAt": "2026-11-01T00:00:00Z",
        "media": {"nodes": []},
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/11",
                    "sku": "GHEE-500",
                    "title": "Default Title",
                    "price": "899.00",
                    "compareAtPrice": None,
                    "inventoryQuantity": 4,
                    "updatedAt": "2026-11-01T00:00:00Z",
                    "selectedOptions": [],
                    "inventoryItem": {"id": "gid://shopify/InventoryItem/111"},
                    **extra.pop("variant", {}),
                }
            ]
        },
        **extra,
    }


def _normalize(node: dict[str, Any]) -> dict[str, Any]:
    return c6z.normalize_shopify_product_node(
        node,
        tenant_id="t",
        merchant_id="m",
        seller_agent_id="s",
        synced_at="2026-11-01T00:00:00Z",
        default_currency="INR",
    )


def test_a_shopify_product_with_details_is_served_like_an_uploaded_one() -> None:
    node = _shopify_node(
        tags=["Ghee", "wholesale"],
        onlineStoreUrl="https://shop.example.in/products/ghee",
        metafields={"nodes": [_metafield("shelf_life", "single_line_text_field", "9 months", name="Shelf life")]},
        variant={"barcode": "8901234567890"},
    )
    product = _normalize(node)
    assert product["tags"] == ("Ghee",)
    assert product["attributes"] == ({"name": "shelf_life", "label": "Shelf life", "value": "9 months"},)
    assert product["variants"][0]["gtin"] == "8901234567890"
    served = ucp_product(product, shop_details_from_entries([LICENCE]))
    assert served is not None
    assert served["url"] == "https://shop.example.in/products/ghee" and served["tags"] == ["Ghee"]
    assert served["metadata"]["attributes"] == [{"name": "Shelf life", "value": "9 months"}]
    assert served["metadata"]["shop_details"][0]["name"] == "FSSAI licence number"
    assert served["variants"][0]["barcodes"] == [{"type": "GTIN", "value": "8901234567890"}]


def test_a_product_read_without_the_detail_fields_is_normalized_as_before() -> None:
    product = _normalize(_shopify_node(variant={"barcode": "store-code-7"}))
    assert not {"tags", "url", "attributes"} & set(product)
    assert "gtin" not in product["variants"][0]


# --- Shopify reads: the details query, its fallback, cost limits and throttling ----------------------


def _page(nodes: list[dict[str, Any]], *, next_cursor: str | None = None) -> dict[str, Any]:
    return {
        "data": {
            "products": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
            }
        }
    }


def _client(handler: Any) -> c6z.ShopifyAdminGraphQLClient:
    return c6z.ShopifyAdminGraphQLClient(
        c6z.ShopifyCredentials(
            shop_domain="ghee-shop.myshopify.com", admin_access_token="shpat_redacted_fixture", api_version="2026-04"
        ),
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(c6z.asyncio, "sleep", sleep)
    return slept


@pytest.mark.asyncio
async def test_a_store_that_refuses_the_detail_fields_is_read_without_them(no_sleep: list[float]) -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        queries.append(query)
        if "metafields" in query:
            return httpx.Response(
                200,
                json={
                    "errors": [{"message": "Field 'access' doesn't exist", "extensions": {"code": "undefinedField"}}]
                },
            )
        return httpx.Response(200, json=_page([{"id": "gid://shopify/Product/1"}]))

    products = await _client(handler).fetch_products(page_size=5, max_pages=2)
    assert products == [{"id": "gid://shopify/Product/1"}]
    assert "metafields" in queries[0] and "metafields" not in queries[1] and len(queries) == 2


@pytest.mark.asyncio
async def test_a_query_over_the_cost_limit_is_asked_again_in_smaller_pages(no_sleep: list[float]) -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.content)["variables"]
        sizes.append(variables["first"])
        if variables["first"] > 12:
            return httpx.Response(
                200, json={"errors": [{"message": "Query cost is 4300", "extensions": {"code": "MAX_COST_EXCEEDED"}}]}
            )
        if variables["after"] is None:
            return httpx.Response(200, json=_page([{"id": "1"}], next_cursor="c1"))
        return httpx.Response(200, json=_page([{"id": "2"}]))

    products = await _client(handler).fetch_products(page_size=50, max_pages=5)
    assert [item["id"] for item in products] == ["1", "2"]
    assert sizes == [50, 25, 12, 12]


@pytest.mark.asyncio
async def test_a_throttled_read_waits_and_is_retried_then_gives_up(no_sleep: list[float]) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]})
        return httpx.Response(200, json=_page([{"id": "1"}]))

    assert await _client(handler).fetch_products(page_size=5, max_pages=1) == [{"id": "1"}]
    assert no_sleep == [1.0]

    def always(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]})

    with pytest.raises(c6z.C6ZRuntimeValidationError, match="returned errors"):
        await _client(always).fetch_products(page_size=5, max_pages=1)


@pytest.mark.asyncio
async def test_an_error_in_the_query_without_details_still_fails_the_read(no_sleep: list[float]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": [{"message": "Access denied", "extensions": {"code": "ACCESS_DENIED"}}]}
        )

    with pytest.raises(c6z.C6ZRuntimeValidationError, match="returned errors"):
        await _client(handler).fetch_products(page_size=5, max_pages=1)


def _cost_store(products: int, per_product: int) -> tuple[Any, list[int]]:
    """A store that refuses queries over 1000 points, as Shopify reports it, and pages by cursor."""
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.content)["variables"]
        first, start = variables["first"], int(variables["after"] or 0)
        sizes.append(first)
        cost = 2 + first * per_product
        if cost > 1000:
            error = {
                "message": "too costly",
                "extensions": {"code": "MAX_COST_EXCEEDED", "cost": cost, "maxCost": 1000},
            }
            return httpx.Response(200, json={"errors": [error]})
        nodes = [{"id": str(index)} for index in range(start, min(products, start + first))]
        end = start + len(nodes)
        return httpx.Response(200, json=_page(nodes, next_cursor=str(end) if end < products else None))

    return handler, sizes


@pytest.mark.asyncio
async def test_smaller_pages_follow_the_reported_cost_and_never_shorten_the_catalog(no_sleep: list[float]) -> None:
    handler, sizes = _cost_store(products=100, per_product=247)
    products = await _client(handler).fetch_products(page_size=50, max_pages=10)
    assert [item["id"] for item in products] == [str(index) for index in range(100)]
    assert sizes[:2] == [50, 3] and set(sizes[1:]) == {3}


@pytest.mark.asyncio
async def test_a_store_larger_than_one_sync_is_read_up_to_the_limit_and_says_so(
    no_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(c6z.logger, "warning", lambda event, **kw: warnings.append((event, kw)))
    handler, _sizes = _cost_store(products=40, per_product=10)
    products = await _client(handler).fetch_products(page_size=10, max_pages=3)
    assert len(products) == 30
    assert warnings == [("shopify_catalog_read_limit_reached", {"products_read": 30, "limit": 30})]


@pytest.mark.asyncio
async def test_only_a_refused_field_falls_back_and_a_passing_error_still_fails(no_sleep: list[float]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": [{"message": "boom", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}]}
        )

    with pytest.raises(c6z.C6ZRuntimeValidationError, match="returned errors"):
        await _client(handler).fetch_products(page_size=5, max_pages=1)

    mixed = [
        {"message": "x", "extensions": {"code": "ACCESS_DENIED"}},
        {"message": "y", "extensions": {"code": "THROTTLED"}},
    ]

    def mixed_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": mixed})

    with pytest.raises(c6z.C6ZRuntimeValidationError, match="returned errors"):
        await _client(mixed_handler).fetch_products(page_size=5, max_pages=1)
    assert no_sleep == []


@pytest.mark.asyncio
async def test_a_throttled_read_waits_for_the_bucket_to_refill(no_sleep: list[float]) -> None:
    calls = {"n": 0}
    throttled = {
        "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
        "extensions": {
            "cost": {
                "requestedQueryCost": 900,
                "throttleStatus": {"maximumAvailable": 1000, "currentlyAvailable": 300, "restoreRate": 50},
            }
        },
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=throttled if calls["n"] == 1 else _page([{"id": "1"}]))

    assert await _client(handler).fetch_products(page_size=5, max_pages=1) == [{"id": "1"}]
    assert no_sleep == [12.0]


def test_a_metafield_name_that_reads_as_a_credential_is_left_out_not_the_sync() -> None:
    nodes = [
        _metafield("notes", "single_line_text_field", "hello", name="xoxb-team notes"),
        _metafield("notes_2", "single_line_text_field", "hello", name="-----BEGIN notes"),
        _metafield("shelf_life", "single_line_text_field", "9 months", name="Shelf life"),
    ]
    product = _normalize(_shopify_node(metafields={"nodes": nodes}))
    assert product["attributes"] == ({"name": "shelf_life", "label": "Shelf life", "value": "9 months"},)


@pytest.mark.parametrize("label", _CREDENTIAL_DETAIL_NAMES)
def test_shopify_details_skip_credential_names_in_keys_labels_and_namespaces(label: str) -> None:
    for field in ("key", "namespace", "name"):
        node = _metafield("public_detail", "single_line_text_field", "synthetic-placeholder", name="Public detail")
        if field == "name":
            node["definition"]["name"] = label
        else:
            node[field] = label
        assert metafield_details([node]) == []


def test_shop_details_saved_without_the_setup_page_are_checked_when_read() -> None:
    stored = {
        "shop_details": [
            {"name": "supplier", "label": "Supplier", "value": "Acme Wholesale, cost 40/kg"},
            {"name": "x", "label": "FSSAI licence number", "value": "10019022008571"},
            {"name": "y", "label": "Old link", "value": "http://labs.example.in/x"},
        ]
    }
    assert shop_details_of(stored) == [
        {"name": "fssai_licence_number", "label": "FSSAI licence number", "value": "10019022008571"}
    ]


def test_store_links_with_a_user_name_and_unusable_numbers_are_left_out() -> None:
    assert product_extras({"onlineStoreUrl": "https://u:p@shop.example/products/x"}) == {}
    assert product_extras({"onlineStoreUrl": "https://Shop.Example/products/x"}) == {
        "url": "https://shop.example/products/x"
    }
    assert metafield_text("rating", json.dumps({"value": "NaN", "scale_max": "5"})) is None
    assert metafield_text("weight", json.dumps({"value": None, "unit": "GRAMS"})) is None


# --- the Telegram shopper -------------------------------------------------------------------------


def test_telegram_shows_the_shop_details_once_per_shop() -> None:
    shop = shop_details_from_entries([LICENCE, CERTIFIED])
    served = ucp_product(_upload_product(), shop)
    assert served is not None
    summary = _product_summary("kaveri", served)
    assert summary["shop_details_declared_by_shop"] == [
        "FSSAI licence number: 10019022008571",
        "Certified organic: India Organic, certificate ORG-2026-114",
    ]
    body = _fit_search_results(
        [_product_summary("kaveri", served), _product_summary("kaveri", served), _product_summary("nilgiri", served)]
    )
    carried = [item["shop"] for item in body["products"] if "shop_details_declared_by_shop" in item]
    assert carried == ["kaveri", "nilgiri"]
