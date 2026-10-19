# SPDX-License-Identifier: Apache-2.0
"""Loyalty (``dev.ucp.common.loyalty``) from Shopify customer metafields.

When the extension is negotiated, the merchant has a loyalty programme in its
UCP settings, the buyer's email is known and the platform's
``context.eligibility`` carries the programme's claim, the checkout or cart
response carries ``loyalty: {<claim>: membership}``. The membership is read
asynchronously from the Shopify customer's metafields in the programme's
namespace (keys ``member_id``, ``tier_id``, ``tier_name``,
``tier_benefits`` as ``id:description`` pairs separated by ``;``). The
earning forecast is ``earn_points_per_major_unit`` x the total in major units
(rounded down) and is ``provisional`` until the order completes.

The email is buyer-asserted until identity linking authenticates the buyer,
so only the tier and the forecast are disclosed (no balance) and a lookup
failure omits loyalty rather than failing the checkout.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

from core.commerce.arbiter import amount_minor_units
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.shopify_reads import ShopifyReads

logger = structlog.get_logger()


async def loyalty_view(
    *,
    merchant: MerchantUcp,
    reads: ShopifyReads | None,
    email: str | None,
    eligibility: Sequence[str],
    total_minor_units: int,
    currency: str | None,
) -> dict[str, Any] | None:
    settings = merchant.loyalty
    if settings is None or reads is None or not email or settings.claim not in eligibility:
        return None
    try:
        fields = await reads.customer_loyalty_metafields(email=email, namespace=settings.metafield_namespace)
    # enterprise-gate: broad-except-ok reason=loyalty-is-informational-a-lookup-failure-omits-it
    except Exception as exc:
        logger.warning("ucp_loyalty_lookup_failed", error_type=type(exc).__name__)
        return None
    if not fields or not fields.get("member_id"):
        return None
    membership: dict[str, Any] = {
        "id": f"{settings.claim}:member",
        "name": settings.name,
        "provisional": True,
    }
    if fields.get("tier_id") and fields.get("tier_name"):
        tier: dict[str, Any] = {"id": fields["tier_id"], "name": fields["tier_name"]}
        benefits = []
        for part in (fields.get("tier_benefits") or "").split(";"):
            benefit_id, _, description = part.partition(":")
            if benefit_id.strip() and description.strip():
                benefits.append({"id": benefit_id.strip(), "description": description.strip()})
        if benefits:
            tier["benefits"] = benefits
        membership["tiers"] = [tier]
    if settings.earn_points_per_major_unit and currency:
        minor_per_major = amount_minor_units("1", currency, 1) or 100
        points = total_minor_units // minor_per_major * settings.earn_points_per_major_unit
        membership["rewards"] = [
            {
                "currency": dict(settings.reward_currency),
                "earning_forecast": {
                    "amount": max(points, 0),
                    "breakdown": [{"id": "base", "amount": max(points, 0), "description": "Points on this purchase"}],
                },
            }
        ]
    return {settings.claim: membership}
