# SPDX-License-Identifier: Apache-2.0
"""UCP releases this server speaks and the version-specific names and URLs it publishes.

Each response is rendered in exactly one release (the platform's, after
negotiation). Every ``dev.ucp.*`` entry declares that release as its
``version`` (UCP 2026-08-25 overview, Component Versioning and Release
Snapshots). Schema URLs are the files each release publishes under
https://ucp.dev/<version>/schemas/.
"""

from __future__ import annotations

import re

V2026_04_08 = "2026-04-08"
V2026_08_25 = "2026-08-25"
KNOWN_VERSIONS: tuple[str, ...] = (V2026_04_08, V2026_08_25)
_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Capability and extension names this server can serve, per release.
CHECKOUT = "dev.ucp.shopping.checkout"
CART = "dev.ucp.shopping.cart"
ORDER = "dev.ucp.shopping.order"
FULFILLMENT = "dev.ucp.shopping.fulfillment"
BUYER_CONSENT = "dev.ucp.shopping.buyer_consent"
LOYALTY = "dev.ucp.common.loyalty"
PERMALINK = "dev.ucp.shopping.permalink"
PAYMENT_AUTHENTICATION = "dev.ucp.common.payment.authentication"
SPLIT_PAYMENTS = "dev.ucp.common.payment.split_payments"
PAYMENT_TERMS = "dev.ucp.common.payment.terms"
AP2_MANDATE_R25 = "dev.ucp.common.payment.ap2_mandate"
AP2_MANDATE_R08 = "dev.ucp.shopping.ap2_mandate"

# Action types declared by dev.ucp.common.payment.authentication (2026-08-25 only).
ACTION_DEVICE_DATA_COLLECTION = "dev.ucp.common.payment.device_data_collection"
ACTION_THREE_DS_CHALLENGE = "dev.ucp.common.payment.three_ds_challenge"


def is_version(value: object) -> bool:
    return isinstance(value, str) and bool(_VERSION_RE.match(value))


def ap2_mandate_name(version: str) -> str:
    return AP2_MANDATE_R25 if version >= V2026_08_25 else AP2_MANDATE_R08


def schema_url(version: str, name: str) -> str:
    """The published schema URL of a capability in a release (authority-bound to ucp.dev)."""
    paths = {
        CHECKOUT: "schemas/shopping/checkout.json",
        CART: "schemas/shopping/cart.json",
        ORDER: "schemas/shopping/order.json",
        FULFILLMENT: "schemas/shopping/fulfillment.json",
        BUYER_CONSENT: "schemas/shopping/buyer_consent.json",
        LOYALTY: "schemas/common/loyalty.json",
        PERMALINK: "schemas/shopping/permalink.json",
        PAYMENT_AUTHENTICATION: "schemas/common/payment_authentication.json",
        SPLIT_PAYMENTS: "schemas/common/payment_split_payments.json",
        PAYMENT_TERMS: "schemas/common/payment_terms.json",
        AP2_MANDATE_R25: "schemas/common/payment_ap2_mandate.json",
        AP2_MANDATE_R08: "schemas/shopping/ap2_mandate.json",
    }
    return f"https://ucp.dev/{version}/{paths[name]}"
