# SPDX-License-Identifier: Apache-2.0
"""Synthetic UCP scope for tests: a merchant on merchant.example, platform profiles, fakes for every dependency."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec

from core.commerce import executors
from core.commerce.ucp import handlers
from core.commerce.ucp.httpsig import LocalEs256Signer
from core.commerce.ucp.items import ProductEvidenceCatalog
from core.commerce.ucp.merchant import MerchantUcp, merchant_ucp_from_config
from core.commerce.ucp.shopify_reads import DeliveryRate
from tests import commerce_arbiter_fixtures as fx

HOST = "shop.merchant.example"
PLATFORM_PROFILE = "https://platform.example/profiles/shopper.json"
WEBHOOK_URL = "https://platform.example/webhooks/ucp/orders"
SKU = "TOTE-1"
ALL_CAPABILITIES = (
    "dev.ucp.shopping.checkout",
    "dev.ucp.shopping.cart",
    "dev.ucp.shopping.order",
    "dev.ucp.shopping.fulfillment",
    "dev.ucp.shopping.buyer_consent",
    "dev.ucp.common.loyalty",
)
R25_EXTRA = (
    "dev.ucp.common.payment.authentication",
    "dev.ucp.common.payment.split_payments",
    "dev.ucp.common.payment.terms",
)


def ucp_settings(**overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "serving_host": HOST,
        "currency": "INR",
        "executor": "test_executor",
        "links": [
            {"type": "privacy_policy", "url": "https://merchant.example/privacy"},
            {"type": "terms_of_service", "url": "https://merchant.example/terms"},
        ],
        "fulfillment": {"shipping": True, "pickup": True, "multi_destination": [], "method_combinations": []},
        "loyalty": {
            "claim": "com.merchant.rewards",
            "name": "Merchant Rewards",
            "metafield_namespace": "loyalty",
            "reward_currency": {"name": "Points", "code": "PTS", "decimal_places": 0},
            "earn_points_per_major_unit": 2,
        },
        "mandate_capability": {
            "rails": ["card", "upi"],
            "max_amount_minor_units": 50000,
            "currency": "INR",
            "instrument_types": ["card"],
        },
        "split_payments": {"allowed_combinations": [[{"types": ["card"], "min": 1, "max": 2}]]},
        "payment_terms": [
            {
                "id": "pay_in_two",
                "title": "Pay in two",
                "schedules": [
                    {"id": "s1", "type": "installment", "description": "Today", "share_bps": 5000},
                    {
                        "id": "s2",
                        "type": "installment",
                        "description": "In 30 days",
                        "share_bps": 5000,
                        "due_in_days": 30,
                    },
                ],
            }
        ],
        "payment_authentication": {"allowed_origins": ["https://payments.example"]},
        "permalink_endpoint": "https://merchant.example/buy",
        "quantity_units": {"FABRIC-1": {"unit": "MTR", "scale": 1, "display_text": "metre", "increment": 5}},
    }
    settings.update(overrides)
    return settings


def merchant_config(tenant_id: str, **ucp_overrides: Any) -> dict[str, Any]:
    config = fx.merchant_config()
    config["merchant_display_name"] = "Example Store"
    config["offline_pos_stores"] = [
        {
            "store_id": "store-1",
            "display_name": "Example Store Pune",
            "city": "Pune",
            "country_code": "IN",
            "enabled": True,
        }
    ]
    config["payment_providers"] = [
        {"provider_key": "plural_pine", "provider_type": "plural_pine", "enabled": True, "environment": "sandbox"}
    ]
    config["provider_policy"]["ucp"] = ucp_settings(**ucp_overrides)
    config["tenant_id"] = tenant_id
    return config


def merchant(tenant_id: str, **ucp_overrides: Any) -> MerchantUcp:
    resolved = merchant_ucp_from_config(merchant_config(tenant_id, **ucp_overrides), tenant_id=tenant_id)
    assert resolved is not None
    return resolved


def products(tenant_id: str) -> list[dict[str, Any]]:
    base = fx.products(tenant_id)
    extra = copy.deepcopy(base[0])
    extra["title"] = "Canvas Fabric"
    extra["product_ref"] = "agenticorg:shopify_product:fabric"
    extra["variants"] = [
        {
            **dict(base[0]["variants"][0]),
            "variant_id": "gid://shopify/ProductVariant/2",
            "sku": "FABRIC-1",
            "price": "12.00",
            "inventory_quantity_snapshot": 40,
        }
    ]
    sold_out = copy.deepcopy(base[0])
    sold_out["title"] = "Sold Out Mug"
    sold_out["variants"] = [
        {
            **dict(base[0]["variants"][0]),
            "variant_id": "gid://shopify/ProductVariant/3",
            "sku": "MUG-0",
            "price": "9.00",
            "inventory_quantity_snapshot": 0,
        }
    ]
    return [*base, extra, sold_out]


def platform_profile(version: str = "2026-04-08", *, capabilities: Sequence[str] | None = None) -> dict[str, Any]:
    names = list(capabilities if capabilities is not None else ALL_CAPABILITIES)
    if capabilities is None and version >= "2026-08-25":
        names += list(R25_EXTRA)
    caps: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        entry: dict[str, Any] = {"version": version, "spec": "https://ucp.dev/spec", "schema": "https://ucp.dev/schema"}
        if name == "dev.ucp.shopping.order":
            entry["config"] = {"webhook_url": WEBHOOK_URL}
        caps[name] = [entry]
    return {"ucp": {"version": version, "services": {}, "capabilities": caps, "payment_handlers": {}}}


class StaticResolver:
    def __init__(self, resolved: MerchantUcp) -> None:
        self.merchant = resolved

    async def resolve(self, *, host: str, tenant_id: str) -> MerchantUcp | None:
        if host.split(":")[0].lower() == self.merchant.serving_host and tenant_id == self.merchant.tenant_id:
            return self.merchant
        return None


class FakeShopifyReads:
    def __init__(self, metafields: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.metafields = (
            metafields
            if metafields is not None
            else {"member_id": "M-100", "tier_id": "gold", "tier_name": "Gold", "tier_benefits": "ship:Free shipping"}
        )
        self.fail = fail
        self.lookups: list[tuple[str, str]] = []

    async def customer_loyalty_metafields(self, *, email: str, namespace: str) -> dict[str, str] | None:
        self.lookups.append((email, namespace))
        if self.fail:
            raise RuntimeError("shopify unavailable")
        return self.metafields if email == "buyer@example.com" else None

    async def delivery_rates(self) -> list[DeliveryRate]:
        return RATES


RATES = [
    DeliveryRate(
        id="ship-std",
        title="Standard",
        description="3-5 days",
        amount_minor_units=5000,
        currency="INR",
        countries=("IN",),
        rest_of_world=False,
    ),
    DeliveryRate(
        id="ship-exp",
        title="Express",
        description="1-2 days",
        amount_minor_units=12000,
        currency="INR",
        countries=("IN",),
        rest_of_world=False,
    ),
]


class FakeExecutor:
    name = "test_executor"

    def __init__(self, result: executors.ExecutionResult | None = None) -> None:
        self.result = result or executors.ExecutionResult(status="completed", provider_order_ref="prov-1")
        self.calls: list[tuple[executors.ExecutionCheckout, executors.ConsumedDecision]] = []

    async def execute(self, session: executors.ExecutionCheckout, decision: executors.ConsumedDecision):
        self.calls.append((session, decision))
        return self.result


def signer() -> LocalEs256Signer:
    return LocalEs256Signer(ec.generate_private_key(ec.SECP256R1()), kid="merchant-test-key")


def runtime(
    tenant_id: str,
    *,
    resolved: MerchantUcp | None = None,
    profile: Mapping[str, Any] | None = None,
    reads: FakeShopifyReads | None = None,
    signing: LocalEs256Signer | None = None,
    config: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    kicks: list[uuid.UUID] | None = None,
) -> handlers.UcpRuntime:
    resolved = resolved or merchant(tenant_id)
    profile_doc = dict(profile or platform_profile())
    shopify = reads or FakeShopifyReads()

    async def _fetch(_url: str) -> Any:
        return json.loads(json.dumps(profile_doc))

    async def _products(_m: MerchantUcp) -> list[dict[str, Any]]:
        return products(tenant_id)

    async def _rates(_m: MerchantUcp) -> list[DeliveryRate]:
        return await shopify.delivery_rates()

    async def _reads(_m: MerchantUcp) -> FakeShopifyReads:
        return shopify

    async def _scope(_m: MerchantUcp) -> tuple[list[Any], dict[str, Any]]:
        return fx.cache_records(tenant_id, now=now), dict(config or merchant_config(tenant_id))

    return handlers.UcpRuntime(
        resolver=StaticResolver(resolved),
        catalog_for=lambda _m: ProductEvidenceCatalog(_products),
        rates_for=lambda _m: _rates,
        shopify_reads_for=_reads,
        arbiter_scope=_scope,
        signer=lambda: signing,
        fetcher=_fetch,
        clock=(lambda: now) if now else (lambda: datetime.now(UTC)),
        after_commit=(lambda tenant: kicks.append(tenant)) if kicks is not None else None,
        supported_versions=("2026-04-08", "2026-08-25"),
    )


def request(
    tenant_id: str, body: Any = None, *, key: str | None = None, agent: str | None = None, host: str = HOST
) -> handlers.UcpRequest:
    raw = b"" if body is None else json.dumps(body).encode()
    return handlers.UcpRequest(
        tenant_id=tenant_id,
        host=host,
        ucp_agent=agent if agent is not None else f'profile="{PLATFORM_PROFILE}"',
        raw_body=raw,
        idempotency_key=key,
    )


def create_body(quantity: int = 1, item_id: str = SKU, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"line_items": [{"item": {"id": item_id}, "quantity": quantity}]}
    body.update(extra)
    return body


SHIPPING = {
    "methods": [
        {
            "type": "shipping",
            "destinations": [
                {
                    "id": "dest_1",
                    "street_address": "1 MG Road",
                    "address_locality": "Pune",
                    "postal_code": "411001",
                    "address_country": "IN",
                }
            ],
            "selected_destination_id": "dest_1",
            "groups": [{"selected_option_id": "ship-std"}],
        }
    ]
}


def token_payment(**credential: Any) -> dict[str, Any]:
    return {
        "instruments": [
            {
                "id": "instr_1",
                "handler_id": "plural_tokenized",
                "type": "card",
                "display": {"brand": "visa", "last_digits": "4242"},
                "credential": {"type": "plural_pine_token", "token": "tok_example_1", **credential},
            }
        ]
    }


def authority(now: datetime | None = None, **overrides: Any) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    values = fx.buyer_authority(now=moment)
    values["passport_max_amount_minor_units"] = 100000
    values.update(overrides)
    return values


def complete_body(
    now: datetime | None = None,
    *,
    decision_id: str | None = None,
    decide_only: bool = False,
    payment: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    vendor: dict[str, Any] = {"buyer_authority": authority(now)}
    if decision_id:
        vendor["decision_id"] = decision_id
    if decide_only:
        vendor["decide_only"] = True
    body = {"payment": payment or token_payment(), "ai.agenticorg.commerce": vendor}
    body.update(extra)
    return body


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def soon(minutes: int = 5) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
