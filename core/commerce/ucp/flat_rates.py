# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Shipping rates a merchant sets itself, for shops whose catalog is not read from Shopify.

The setup flow stores one flat rate at ``provider_policy.ucp.fulfillment.flat_rates``::

    [{"id": "standard", "title": "Standard shipping", "amount_minor_units": 5000,
      "currency": "INR", "countries": ["IN"], "rest_of_world": false}]

A Shopify merchant's rates come from its delivery profiles instead
(``ShopifyAdminReads.delivery_rates``); these are used only when there is no
Shopify read. Malformed entries are skipped: no rate means no shipping option.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from core.commerce.ucp.shopify_reads import DeliveryRate

MAX_FLAT_RATES = 5
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_RATE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def flat_rates_config(config: Mapping[str, Any] | None) -> list[Any]:
    """The raw ``flat_rates`` list of a merchant config (``[]`` when absent)."""
    policy = config.get("provider_policy") if isinstance(config, Mapping) else None
    ucp = policy.get("ucp") if isinstance(policy, Mapping) else None
    fulfillment = ucp.get("fulfillment") if isinstance(ucp, Mapping) else None
    rates = fulfillment.get("flat_rates") if isinstance(fulfillment, Mapping) else None
    return list(rates) if isinstance(rates, list) else []


def configured_delivery_rates(config: Mapping[str, Any] | None) -> list[DeliveryRate]:
    """The merchant's own flat shipping rates as delivery rates."""
    out: list[DeliveryRate] = []
    for raw in flat_rates_config(config)[:MAX_FLAT_RATES]:
        if not isinstance(raw, Mapping):
            continue
        rate_id = str(raw.get("id") or "")
        currency = str(raw.get("currency") or "").upper()
        amount = raw.get("amount_minor_units")
        if not _RATE_ID.match(rate_id) or not _CURRENCY.match(currency):
            continue
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            continue
        countries = tuple(
            str(c).upper() for c in raw.get("countries") or [] if isinstance(c, str) and _COUNTRY.match(c.upper())
        )
        rest_of_world = raw.get("rest_of_world") is True or not countries
        out.append(
            DeliveryRate(
                id=f"flat_{rate_id}",
                title=str(raw.get("title") or "Standard shipping")[:120],
                description=None,
                amount_minor_units=amount,
                currency=currency,
                countries=countries,
                rest_of_world=rest_of_world,
            )
        )
    return out
