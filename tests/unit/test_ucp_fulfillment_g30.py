# SPDX-License-Identifier: Apache-2.0
"""G30: fulfillment methods, shipping and pickup destinations, options from Shopify delivery profiles, config."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.commerce.ucp import fulfillment as f
from core.commerce.ucp.registry import DEFAULT_REGISTRY, RegistryContext
from core.commerce.ucp.renderers import v2026_04_08, v2026_08_25
from core.commerce.ucp.shopify_reads import normalize_delivery_profiles
from tests import ucp_fixtures as uf
from tests import ucp_schema

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a4"))


async def _rates(_m):
    return uf.RATES


async def _build(request: Any, merchant=None, previous=None, subtotal: int = 2500) -> f.FulfillmentResult:
    return await f.build_fulfillment(
        merchant=merchant or uf.merchant(TENANT),
        request=request,
        previous=previous,
        line_item_ids=["li_1"],
        currency="INR",
        subtotal_minor_units=subtotal,
        rates_source=_rates,
    )


@pytest.mark.asyncio
async def test_shipping_destination_selection_and_options() -> None:
    result = await _build(uf.SHIPPING)
    method = result.state["methods"][0]
    assert method["destinations"][0]["kind"] == "shipping_address"
    assert [o["id"] for o in method["groups"][0]["options"]] == ["ship-std", "ship-exp"]
    assert method["groups"][0]["selected_option_id"] == "ship-std"
    assert result.ready and result.amount_minor_units == 5000


@pytest.mark.asyncio
async def test_missing_selection_and_undeliverable_country_are_messages() -> None:
    unselected = await _build(
        {"methods": [{"type": "shipping", "destinations": uf.SHIPPING["methods"][0]["destinations"]}]}
    )
    assert not unselected.ready and {m["code"] for m in unselected.messages} == {"missing"}
    abroad = {
        "methods": [
            {"type": "shipping", "destinations": [{"id": "d", "address_country": "US"}], "selected_destination_id": "d"}
        ]
    }
    result = await _build(abroad)
    assert any(m["code"] == "address_undeliverable" for m in result.messages)
    with pytest.raises(f.FulfillmentRequestError):
        await _build({"methods": [{"type": "drone"}]})


@pytest.mark.asyncio
async def test_pickup_destinations_are_the_merchants_locations() -> None:
    result = await _build(
        {
            "methods": [
                {
                    "type": "pickup",
                    "selected_destination_id": "store-1",
                    "groups": [{"selected_option_id": "pickup-store-1"}],
                }
            ]
        }
    )
    method = result.state["methods"][0]
    assert method["destinations"] == [
        {
            "id": "store-1",
            "kind": "business_location",
            "name": "Example Store Pune",
            "address": {"address_locality": "Pune", "address_country": "IN"},
        }
    ]
    assert result.ready and result.amount_minor_units == 0
    r08 = v2026_04_08.destination(method["destinations"][0])
    r25 = v2026_08_25.destination(method["destinations"][0])
    assert "type" not in r08 and r25["type"] == "business_location"


@pytest.mark.asyncio
async def test_multi_destination_and_method_combinations_follow_business_config() -> None:
    two = {
        "methods": [
            dict(uf.SHIPPING["methods"][0], line_item_ids=["li_1"]),
            dict(uf.SHIPPING["methods"][0], line_item_ids=["li_1"]),
        ]
    }
    refused = await _build(two)
    assert any(m["code"] == "multi_destination_unsupported" for m in refused.messages)
    mixed = {"methods": [uf.SHIPPING["methods"][0], {"type": "pickup", "selected_destination_id": "store-1"}]}
    assert any(m["code"] == "method_combination_unsupported" for m in (await _build(mixed)).messages)
    allowed = uf.merchant(
        TENANT,
        fulfillment={
            "shipping": True,
            "pickup": True,
            "multi_destination": ["shipping"],
            "method_combinations": [["shipping", "pickup"]],
        },
    )
    assert not any(m["code"] == "multi_destination_unsupported" for m in (await _build(two, allowed)).messages)


@pytest.mark.asyncio
async def test_free_shipping_threshold() -> None:
    merchant = uf.merchant(TENANT, fulfillment={"shipping": True, "free_shipping_min_subtotal_minor_units": 2000})
    result = await _build(uf.SHIPPING, merchant)
    assert all(o["amount"] == 0 for o in result.state["methods"][0]["groups"][0]["options"])


def test_business_config_shape_per_release() -> None:
    merchant = uf.merchant(
        TENANT,
        fulfillment={
            "shipping": True,
            "pickup": True,
            "multi_destination": ["shipping"],
            "method_combinations": [["shipping", "pickup"]],
        },
    )
    r08 = DEFAULT_REGISTRY.business_capabilities(merchant, "2026-04-08", RegistryContext())
    r25 = DEFAULT_REGISTRY.business_capabilities(merchant, "2026-08-25", RegistryContext())
    c08 = r08["dev.ucp.shopping.fulfillment"][0]["config"]
    c25 = r25["dev.ucp.shopping.fulfillment"][0]["config"]
    assert c08 == {
        "allows_multi_destination": {"shipping": True, "pickup": False},
        "allows_method_combinations": [["shipping", "pickup"]],
    }
    assert c25 == {"multi_destination": [{"method": "shipping"}], "method_combinations": [["shipping", "pickup"]]}
    ucp_schema.assert_valid("2026-04-08", "shopping/types/business_fulfillment_config.json", c08)
    ucp_schema.assert_valid("2026-08-25", "shopping/types/business_fulfillment_config.json", c25)


def test_shopify_delivery_profiles_normalise_to_zone_rates() -> None:
    data = {
        "deliveryProfiles": {
            "nodes": [
                {
                    "profileLocationGroups": [
                        {
                            "locationGroupZones": {
                                "nodes": [
                                    {
                                        "zone": {"countries": [{"code": {"countryCode": "IN", "restOfWorld": False}}]},
                                        "methodDefinitions": {
                                            "nodes": [
                                                {
                                                    "id": "gid://shopify/DeliveryMethodDefinition/11",
                                                    "name": "Standard",
                                                    "active": True,
                                                    "rateProvider": {
                                                        "price": {"amount": "50.00", "currencyCode": "INR"}
                                                    },
                                                },
                                                {
                                                    "id": "gid://shopify/DeliveryMethodDefinition/12",
                                                    "name": "Old",
                                                    "active": False,
                                                    "rateProvider": {
                                                        "price": {"amount": "10.00", "currencyCode": "INR"}
                                                    },
                                                },
                                            ]
                                        },
                                    },
                                    {
                                        "zone": {"countries": [{"code": {"countryCode": None, "restOfWorld": True}}]},
                                        "methodDefinitions": {
                                            "nodes": [
                                                {
                                                    "id": "gid://shopify/DeliveryMethodDefinition/13",
                                                    "name": "World",
                                                    "rateProvider": {"price": {"amount": "900", "currencyCode": "INR"}},
                                                }
                                            ]
                                        },
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    }
    rates = normalize_delivery_profiles(data)
    assert [(r.id, r.amount_minor_units, r.countries, r.rest_of_world) for r in rates] == [
        ("ship-11", 5000, ("IN",), False),
        ("ship-13", 90000, (), True),
    ]
    assert rates[1].serves("US") and not rates[0].serves("US")


@pytest.mark.parametrize("version", ["2026-04-08", "2026-08-25"])
@pytest.mark.asyncio
async def test_rendered_fulfillment_validates(version: str) -> None:
    result = await _build(uf.SHIPPING)
    mod = v2026_08_25 if version == "2026-08-25" else v2026_04_08
    rendered = mod.fulfillment(result.state, [])
    ucp_schema.assert_valid(version, "shopping/types/fulfillment.json", rendered)
