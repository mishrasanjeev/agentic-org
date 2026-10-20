# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G25/G30: the read-only Shopify Admin GraphQL reads behind loyalty and shipping rates.

The client posts to the shop's ``*.myshopify.com`` Admin endpoint only, never follows a redirect, fails closed
on GraphQL errors, caches delivery profiles per merchant and keeps only active flat-rate methods.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.commerce.c6z_runtime_vertical import ShopifyCredentials
from core.commerce.ucp import shopify_reads
from core.commerce.ucp.shopify_reads import (
    ShopifyAdminReads,
    clear_delivery_cache,
    normalize_delivery_profiles,
)

SHOP = "example-store.myshopify.com"
ACCESS = "-".join(("admin", "access", "placeholder"))


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_delivery_cache()
    yield
    clear_delivery_cache()


def _credentials(domain: str = SHOP) -> ShopifyCredentials:
    return ShopifyCredentials(shop_domain=domain, admin_access_token=ACCESS, api_version="2026-04")


def _zone(countries: list[dict], methods: list[dict | None]) -> dict:
    return {
        "zone": {"id": "gid://shopify/DeliveryZone/1", "countries": countries},
        "methodDefinitions": {"nodes": methods},
    }


def _method(gid: str, amount: str | None, currency: str | None = "INR", **extra) -> dict:
    price = {"amount": amount, "currencyCode": currency} if amount is not None or currency is not None else None
    return {"id": gid, "name": extra.pop("name", "Standard"), "rateProvider": {"price": price}, **extra}


PROFILES = {
    "deliveryProfiles": {
        "nodes": [
            {
                "id": "gid://shopify/DeliveryProfile/1",
                "profileLocationGroups": [
                    {
                        "locationGroupZones": {
                            "nodes": [
                                _zone(
                                    [{"code": {"countryCode": "in"}}, {"code": {"countryCode": "IN"}}, {"code": {}}],
                                    [
                                        _method(
                                            "gid://shopify/DeliveryMethodDefinition/11", "50.00", description="3-5 days"
                                        ),
                                        _method("gid://shopify/DeliveryMethodDefinition/12", "99.00", active=False),
                                        _method("gid://shopify/DeliveryMethodDefinition/11", "50.00"),
                                        _method("gid://shopify/DeliveryMethodDefinition/13", None, currency=None),
                                        _method("", "10.00"),
                                        None,
                                    ],
                                ),
                                _zone(
                                    [{"code": {"restOfWorld": True}}],
                                    [
                                        _method(
                                            "gid://shopify/DeliveryMethodDefinition/21",
                                            "15.5",
                                            currency="usd",
                                            name=None,
                                        )
                                    ],
                                ),
                            ]
                        }
                    }
                ],
            },
            {
                # A single location group object (not a list) is read the same way.
                "id": "gid://shopify/DeliveryProfile/2",
                "profileLocationGroups": {
                    "locationGroupZones": {
                        "nodes": [_zone([{"code": {"countryCode": "SG"}}], [_method("gid://x/31", "4.00", "SGD")])]
                    }
                },
            },
        ]
    }
}


def test_normalize_keeps_active_priced_unique_methods_per_zone() -> None:
    rates = normalize_delivery_profiles(PROFILES)
    assert [r.id for r in rates] == ["ship-11", "ship-21", "ship-31"]
    standard, world, singapore = rates
    assert standard.amount_minor_units == 5000 and standard.currency == "INR"
    assert standard.countries == ("IN",) and not standard.rest_of_world
    assert standard.description == "3-5 days"
    assert standard.serves("in") and not standard.serves("SG") and not standard.serves(None)
    assert world.title == "Shipping" and world.currency == "USD" and world.amount_minor_units == 1550
    assert world.rest_of_world and world.serves("BR") and world.serves(None)
    assert world.description is None
    assert singapore.countries == ("SG",)
    assert normalize_delivery_profiles({}) == []


def _reads(handler, *, domain: str = SHOP) -> tuple[ShopifyAdminReads, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    reads = ShopifyAdminReads(
        _credentials(domain), cache_key=("tenant-1", "merchant-1"), transport=httpx.MockTransport(_record)
    )
    return reads, seen


@pytest.mark.asyncio
async def test_customer_loyalty_metafields_query_and_mapping() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "customers": {
                        "nodes": [
                            {
                                "id": "gid://shopify/Customer/1",
                                "metafields": {
                                    "nodes": [
                                        {"key": "tier_id", "value": "gold", "type": "single_line_text_field"},
                                        {"key": "points", "value": 120},
                                        {"key": None, "value": "dropped"},
                                        "not-a-field",
                                    ]
                                },
                            }
                        ]
                    }
                }
            },
        )

    reads, seen = _reads(_handler)
    assert reads.graphql_url == f"https://{SHOP}/admin/api/2026-04/graphql.json"
    fields = await reads.customer_loyalty_metafields(email=' buyer"\\@example.com ', namespace="loyalty")
    assert fields == {"tier_id": "gold", "points": "120"}
    sent = seen[0]
    assert sent.method == "POST" and str(sent.url) == reads.graphql_url
    assert sent.headers["x-shopify-access-token"] == ACCESS
    variables = json.loads(sent.content)["variables"]
    # Quotes and backslashes cannot break out of the search expression.
    assert variables == {"query": 'email:"buyer@example.com"', "namespace": "loyalty"}


@pytest.mark.asyncio
async def test_unknown_customer_is_none() -> None:
    reads, _ = _reads(lambda _r: httpx.Response(200, json={"data": {"customers": {"nodes": []}}}))
    assert await reads.customer_loyalty_metafields(email="nobody@example.com", namespace="loyalty") is None


@pytest.mark.asyncio
async def test_graphql_errors_and_http_errors_fail_closed() -> None:
    reads, _ = _reads(lambda _r: httpx.Response(200, json={"errors": [{"message": "throttled"}]}))
    with pytest.raises(ValueError, match="GraphQL returned errors"):
        await reads.customer_loyalty_metafields(email="buyer@example.com", namespace="loyalty")
    listed, _ = _reads(lambda _r: httpx.Response(200, json=["not", "an", "object"]))
    with pytest.raises(ValueError, match="GraphQL returned errors"):
        await listed.delivery_rates()
    failing, _ = _reads(lambda _r: httpx.Response(503, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        await failing.delivery_rates()


@pytest.mark.asyncio
async def test_redirects_are_not_followed() -> None:
    reads, seen = _reads(lambda _r: httpx.Response(302, headers={"Location": "https://elsewhere.example/graphql"}))
    with pytest.raises(httpx.HTTPStatusError):
        await reads.delivery_rates()
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_endpoint_outside_the_shopify_allowlist_is_refused_before_any_request() -> None:
    reads, seen = _reads(lambda _r: httpx.Response(200, json={"data": {}}), domain="shop.merchant.example")
    with pytest.raises(ValueError, match="unsafe Shopify Admin API endpoint"):
        await reads.delivery_rates()
    assert seen == []


@pytest.mark.asyncio
async def test_missing_data_reads_as_no_rates() -> None:
    reads, _ = _reads(lambda _r: httpx.Response(200, json={"data": None}))
    assert await reads.delivery_rates() == []


@pytest.mark.asyncio
async def test_delivery_rates_are_cached_per_merchant_until_cleared() -> None:
    reads, seen = _reads(lambda _r: httpx.Response(200, json={"data": PROFILES}))
    first = await reads.delivery_rates()
    second = await reads.delivery_rates()
    assert [r.id for r in first] == [r.id for r in second] == ["ship-11", "ship-21", "ship-31"]
    assert second[0] == first[0]
    assert len(seen) == 1
    other, other_seen = _reads(lambda _r: httpx.Response(200, json={"data": {}}))
    # Same cache key (tenant, merchant): served from the cache.
    assert [r.id for r in await other.delivery_rates()] == ["ship-11", "ship-21", "ship-31"]
    assert other_seen == []
    clear_delivery_cache()
    assert await other.delivery_rates() == []
    assert len(other_seen) == 1


@pytest.mark.asyncio
async def test_expired_cache_entry_is_refetched() -> None:
    reads, seen = _reads(lambda _r: httpx.Response(200, json={"data": PROFILES}))
    await reads.delivery_rates()
    expires_at, cached = shopify_reads._delivery_cache[("tenant-1", "merchant-1")]
    assert expires_at > 0 and len(cached) == 3
    shopify_reads._delivery_cache[("tenant-1", "merchant-1")] = (0.0, cached)
    await reads.delivery_rates()
    assert len(seen) == 2
