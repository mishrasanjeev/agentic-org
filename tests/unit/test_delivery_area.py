# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Delivery area before a payment link: Shopify zones by state, postal code prefixes, pickup untouched."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import commerce_merchant_setup as setup_api
from core.commerce.ucp import fulfillment as f
from core.commerce.ucp.delivery_area import (
    MAX_POSTAL_PREFIXES,
    DeliveryAreaError,
    describe_area,
    in_area,
    normalize_postal_prefixes,
    outside_area_message,
    stored_postal_prefixes,
)
from core.commerce.ucp.shopify_reads import DeliveryRate, normalize_delivery_profiles
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000d5"))


def _merchant(**fulfillment: Any):
    settings = {"shipping": True, "pickup": True, "multi_destination": [], "method_combinations": [], **fulfillment}
    return uf.merchant(TENANT, fulfillment=settings)


def _shipping(postal_code: str | None = "560001", region: str | None = None, country: str = "IN") -> dict[str, Any]:
    destination: dict[str, Any] = {"id": "d1", "street_address": "1 MG Road", "address_country": country}
    if postal_code is not None:
        destination["postal_code"] = postal_code
    if region is not None:
        destination["address_region"] = region
    return {
        "methods": [
            {
                "type": "shipping",
                "destinations": [destination],
                "selected_destination_id": "d1",
                "groups": [{"selected_option_id": "ship-std"}],
            }
        ]
    }


async def _build(request: Any, merchant=None, rates=None) -> f.FulfillmentResult:
    async def _rates(_m):
        return uf.RATES if rates is None else rates

    return await f.build_fulfillment(
        merchant=merchant or _merchant(),
        request=request,
        previous=None,
        line_item_ids=["li_1"],
        currency="INR",
        subtotal_minor_units=2500,
        rates_source=_rates,
    )


# --- postal code prefixes -----------------------------------------------------------------------


def test_prefixes_are_compacted_upper_cased_and_deduplicated() -> None:
    assert normalize_postal_prefixes([" 5600", "560 1", "sw1", "5600", "", "56-02"]) == ["5600", "5601", "SW1", "5602"]
    for bad in (["56.00"], ["ABCDEFGHIJK"], ["#1"]):
        with pytest.raises(DeliveryAreaError):
            normalize_postal_prefixes(bad)
    with pytest.raises(DeliveryAreaError):
        normalize_postal_prefixes([str(560000 + i) for i in range(MAX_POSTAL_PREFIXES + 1)])
    # Stored values are read leniently: bad entries are skipped, not fatal.
    assert stored_postal_prefixes(["5600", "bad!", 5601, None]) == ("5600", "5601")
    assert stored_postal_prefixes("5600") == ()


def test_a_postal_code_is_in_the_area_when_it_starts_with_a_prefix() -> None:
    assert in_area("560 001", ("5600",)) and in_area("sw1a 1aa", ("SW1",))
    assert not in_area("570001", ("5600", "5601")) and not in_area("", ("5600",))
    assert in_area(None, ())
    assert describe_area(("5600",)) == "postal codes starting with 5600"
    assert describe_area(("5600", "5601", "5602")) == "postal codes starting with 5600, 5601 or 5602"
    many = tuple(str(5600 + i) for i in range(10))
    assert describe_area(many).endswith("5607 and 2 more")
    assert outside_area_message("570 001", ("5600", "5601")) == (
        "This shop delivers only to postal codes starting with 5600 or 5601; 570001 is outside it."
    )


@pytest.mark.asyncio
async def test_a_destination_outside_the_area_gets_no_option_and_the_message_names_the_postal_code() -> None:
    result = await _build(_shipping("570001"), _merchant(postal_code_prefixes=["5600", "5601"]))
    assert not result.ready and result.amount_minor_units == 0
    group = result.state["methods"][0]["groups"][0]
    assert group["options"] == [] and group["selected_option_id"] is None
    [problem] = [m for m in result.messages if m["code"] == "address_undeliverable"]
    assert problem["content"] == (
        "This shop delivers only to postal codes starting with 5600 or 5601; 570001 is outside it."
    )


@pytest.mark.asyncio
async def test_a_destination_inside_the_area_ships_as_before() -> None:
    result = await _build(_shipping("560 001"), _merchant(postal_code_prefixes=["5600"]))
    assert result.ready and result.amount_minor_units == 5000 and result.messages == []


@pytest.mark.asyncio
async def test_a_restricted_shop_asks_for_the_postal_code_when_it_is_missing() -> None:
    result = await _build(_shipping(None), _merchant(postal_code_prefixes=["5600"]))
    assert not result.ready
    [problem] = [m for m in result.messages if m["code"] == "missing"]
    assert "Add the postal code" in problem["content"] and "5600" in problem["content"]
    assert problem["path"] == "$.fulfillment.methods[0].destinations"
    # Without an area a missing postal code is fine, as before.
    assert (await _build(_shipping(None))).ready


@pytest.mark.asyncio
async def test_pickup_is_not_affected_by_the_delivery_area() -> None:
    pickup = {
        "methods": [
            {
                "type": "pickup",
                "selected_destination_id": "store-1",
                "groups": [{"selected_option_id": "pickup-store-1"}],
            }
        ]
    }
    result = await _build(pickup, _merchant(postal_code_prefixes=["5600"]))
    assert result.ready and result.messages == []


def test_the_merchant_settings_read_the_area() -> None:
    assert _merchant(postal_code_prefixes=["5600", "5601"]).fulfillment.postal_code_prefixes == ("5600", "5601")
    assert _merchant().fulfillment.postal_code_prefixes == ()


# --- Shopify zones by state --------------------------------------------------------------------


def _profiles(countries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "deliveryProfiles": {
            "nodes": [
                {
                    "profileLocationGroups": [
                        {
                            "locationGroupZones": {
                                "nodes": [
                                    {
                                        "zone": {"id": "z1", "name": "South", "countries": countries},
                                        "methodDefinitions": {
                                            "nodes": [
                                                {
                                                    "id": "gid://shopify/DeliveryMethodDefinition/7",
                                                    "name": "Standard",
                                                    "active": True,
                                                    "rateProvider": {
                                                        "price": {"amount": "50.0", "currencyCode": "INR"}
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    }


def test_shopify_zone_provinces_are_parsed() -> None:
    [rate] = normalize_delivery_profiles(
        _profiles(
            [
                {
                    "code": {"countryCode": "IN"},
                    "provinces": [{"code": "KA", "name": "Karnataka"}, {"code": "TN", "name": "Tamil Nadu"}],
                },
                {"code": {"countryCode": "LK"}, "provinces": []},
            ]
        )
    )
    assert rate.countries == ("IN", "LK")
    assert rate.provinces == (("IN", "KA", "Karnataka"), ("IN", "TN", "Tamil Nadu"))
    assert rate.needs_region("IN") and not rate.needs_region("LK")
    for region in ("KA", "ka", "IN-KA", "Karnataka", "tamil nadu", "Tamil-Nadu"):
        assert rate.serves("IN", region), region
    assert not rate.serves("IN", "MH") and not rate.serves("IN", "Maharashtra") and not rate.serves("IN", None)
    assert rate.serves("LK") and not rate.serves("US", "KA")


def test_a_zone_without_provinces_serves_the_whole_country_as_before() -> None:
    [rate] = normalize_delivery_profiles(_profiles([{"code": {"countryCode": "IN"}, "provinces": []}]))
    assert rate.provinces == () and rate.serves("IN") and rate.serves("IN", "MH")
    [world] = normalize_delivery_profiles(_profiles([{"code": {"restOfWorld": True}}]))
    assert world.serves("US", None)


STATE_RATE = DeliveryRate(
    id="ship-south",
    title="South India",
    description=None,
    amount_minor_units=4000,
    currency="INR",
    countries=("IN",),
    rest_of_world=False,
    provinces=(("IN", "KA", "Karnataka"),),
)


@pytest.mark.asyncio
async def test_checkout_matches_the_destination_state_against_the_zone() -> None:
    inside = await _build(
        {**_shipping(region="Karnataka"), "methods": [{**_shipping(region="Karnataka")["methods"][0], "groups": []}]},
        rates=[STATE_RATE],
    )
    assert [o["id"] for o in inside.state["methods"][0]["groups"][0]["options"]] == ["ship-south"]
    outside = await _build(_shipping(region="MH"), rates=[STATE_RATE])
    assert any(m["code"] == "address_undeliverable" for m in outside.messages) and not outside.ready
    no_state = await _build(_shipping(region=None), rates=[STATE_RATE])
    [problem] = [m for m in no_state.messages if m["code"] == "missing"]
    assert "address_region" in problem["content"] and not no_state.ready


# --- the setup step ----------------------------------------------------------------------------


def _policies(**fields: Any) -> setup_api.PoliciesRequest:
    return setup_api.PoliciesRequest(
        returns="Returns within 7 days of delivery.", shipping="Ships in 3 to 5 working days.", **fields
    )


def test_the_policies_step_checks_the_area_and_reads_it_back() -> None:
    assert setup_api._postal_prefixes(_policies()) == []
    assert setup_api._postal_prefixes(_policies(delivery_postal_prefixes=["5600", " 5601 "])) == ["5600", "5601"]
    with pytest.raises(HTTPException) as refused:
        setup_api._postal_prefixes(_policies(delivery_postal_prefixes=["56.00"]))
    assert refused.value.status_code == 422 and refused.value.detail["code"] == "invalid_setup"
    stored = {"ucp": {"fulfillment": {"postal_code_prefixes": ["5600", "5601"]}}}
    assert setup_api.policies_view(stored)["delivery_postal_prefixes"] == ["5600", "5601"]
    assert setup_api.policies_view({})["delivery_postal_prefixes"] == []
