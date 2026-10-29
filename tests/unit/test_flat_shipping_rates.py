# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Flat shipping rates for shops without Shopify delivery profiles (core.commerce.ucp.flat_rates)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import commerce_merchant_setup as setup_api
from core.commerce.ucp.flat_rates import configured_delivery_rates


def _config(rates: Any) -> dict[str, Any]:
    return {"provider_policy": {"ucp": {"fulfillment": {"shipping": True, "flat_rates": rates}}}}


def test_a_stored_rate_becomes_a_delivery_rate_for_its_countries() -> None:
    [rate] = configured_delivery_rates(
        _config(
            [
                {
                    "id": "standard",
                    "title": "Standard shipping",
                    "amount_minor_units": 4950,
                    "currency": "inr",
                    "countries": ["in"],
                    "rest_of_world": False,
                }
            ]
        )
    )
    assert (rate.id, rate.amount_minor_units, rate.currency, rate.countries) == ("flat_standard", 4950, "INR", ("IN",))
    assert rate.serves("IN") and not rate.serves("US") and not rate.serves(None)


def test_no_countries_means_everywhere_and_zero_ships_free() -> None:
    [rate] = configured_delivery_rates(
        _config([{"id": "standard", "amount_minor_units": 0, "currency": "INR", "countries": []}])
    )
    assert rate.rest_of_world and rate.serves("US") and rate.amount_minor_units == 0


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "standard",
        [{"id": "Bad Id", "amount_minor_units": 1, "currency": "INR"}],
        [{"id": "standard", "amount_minor_units": -1, "currency": "INR"}],
        [{"id": "standard", "amount_minor_units": True, "currency": "INR"}],
        [{"id": "standard", "amount_minor_units": "100", "currency": "INR"}],
        [{"id": "standard", "amount_minor_units": 100, "currency": "RUPEES"}],
        ["standard"],
    ],
)
def test_malformed_rates_are_skipped(raw: Any) -> None:
    assert configured_delivery_rates(_config(raw)) == []


def test_a_config_without_rates_has_none() -> None:
    assert configured_delivery_rates({}) == [] == configured_delivery_rates(None)


@pytest.mark.asyncio
async def test_the_rate_lookup_falls_back_to_the_merchants_own_rate_without_shopify(monkeypatch) -> None:
    from types import SimpleNamespace

    from api.v1 import commerce_ucp

    async def _no_shopify(_m: Any) -> None:
        return None

    monkeypatch.setattr(commerce_ucp, "_shopify_reads", _no_shopify)
    merchant = SimpleNamespace(
        raw_config=_config([{"id": "standard", "amount_minor_units": 5000, "currency": "INR", "countries": ["IN"]}])
    )
    rates = await commerce_ucp._rates_for(merchant)(merchant)
    assert [(r.id, r.amount_minor_units) for r in rates] == [("flat_standard", 5000)]


def _policies(**fields: Any) -> setup_api.PoliciesRequest:
    return setup_api.PoliciesRequest(
        returns="Returns within 7 days of delivery.", shipping="Ships in 3 to 5 working days.", **fields
    )


def test_the_policies_step_stores_one_rate_in_minor_units() -> None:
    assert setup_api._flat_rates(_policies()) is None
    assert setup_api._flat_rates(
        _policies(shipping_fee="49.5", shipping_currency="inr", shipping_countries=["in"])
    ) == [
        {
            "id": "standard",
            "title": "Standard shipping",
            "amount_minor_units": 4950,
            "currency": "INR",
            "countries": ["IN"],
            "rest_of_world": False,
        }
    ]
    [free] = setup_api._flat_rates(_policies(shipping_fee="0", shipping_currency="INR")) or []
    assert free["amount_minor_units"] == 0 and free["rest_of_world"] is True


@pytest.mark.parametrize(
    "fields",
    [
        {"shipping_fee": "50"},
        {"shipping_fee": "-5", "shipping_currency": "INR"},
        {"shipping_fee": "1.234", "shipping_currency": "INR"},
        {"shipping_fee": "fifty", "shipping_currency": "INR"},
        {"shipping_fee": "50", "shipping_currency": "INR", "shipping_countries": ["India"]},
    ],
)
def test_the_policies_step_refuses_a_bad_fee(fields: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as refused:
        setup_api._flat_rates(_policies(**fields))
    assert refused.value.status_code == 422


def test_the_policy_view_reads_the_rate_back_in_major_units() -> None:
    policy = _config([{"id": "standard", "amount_minor_units": 4950, "currency": "INR", "countries": ["IN"]}])
    assert setup_api.shipping_rate_view(policy["provider_policy"]) == {
        "fee": "49.50",
        "currency": "INR",
        "countries": ["IN"],
    }
    assert setup_api.shipping_rate_view({}) is None
