# SPDX-License-Identifier: Apache-2.0
"""The one synthetic OACP demo merchant, "Nilgiri Tea House": a clearly labelled demo tenant, catalog and store.

``scripts/seed_oacp_demo_merchant.py`` creates (or converges, or tears down)
one demo tenant so the OACP surfaces can be shown end to end with invented
data. Nothing here pretends to be a Shopify store: the catalog is the
``synthetic_demo`` connector source (``core.commerce.c6z_runtime_vertical.
DEMO_SOURCE_SYSTEM``), every product description starts "Synthetic demo product", every address
is invented, and the authority request tells Grantex ``synthetic_data: true``.

The source is accepted only while ``AGENTICORG_OACP_DEMO_MERCHANT_ENABLED`` is
on, and an artifact that names it is cached only for a tenant whose catalogue
row carries the demo marker (``tenants.settings["oacp_demo_merchant"]``), so a
real merchant can never be backed by synthetic artifacts. With the flag off
every path accepts Shopify read-only sources only, as before.

The data is kept as tuples and built into fresh dictionaries on each call (no
process-local mutable state).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from core.commerce.c6z_runtime_vertical import (
    DEMO_SOURCE_SYSTEM,
    artifact_names_demo_source,
    runtime_source_systems,
    validate_connector_evidence,
    validate_seller_onboarding_packet,
)

DEMO_SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://demo.agenticorg.example/oacp-demo-merchant")
DEMO_TENANT_ID = str(uuid.uuid5(DEMO_SEED_NAMESPACE, "tenant"))
DEMO_ADMIN_USER_ID = str(uuid.uuid5(DEMO_SEED_NAMESPACE, "user:demo-admin"))
DEMO_TENANT_SLUG = "nilgiri-tea-house-demo"
DEMO_TENANT_NAME = "Nilgiri Tea House (OACP demo)"
DEMO_TENANT_MARKER = "oacp_demo_merchant"
DEMO_MERCHANT_ID = "nilgiri_tea_house"
DEMO_SELLER_AGENT_ID = "seller_agent_nilgiri_tea"
DEMO_DISPLAY_NAME = "Nilgiri Tea House (synthetic demo)"
DEMO_EMAIL_DOMAIN = "demo.agenticorg.example"
DEMO_ADMIN_EMAIL = f"demo-admin@{DEMO_EMAIL_DOMAIN}"
DEMO_ADMIN_NAME = "Nilgiri Tea House demo admin (synthetic)"
DEMO_CURRENCY = "INR"
DEMO_STORE_ID = "nilgiri_tea_house_ooty"
# The serving host the owner maps to the API for the public UCP profile (attested by DNS TXT, never
# invented by the seed); its UCP vendor namespace is the host reversed.
DEMO_SERVING_HOST = "nilgiritea.agenticorg.ai"
DEMO_NAMESPACE = "ai.agenticorg.nilgiritea"
# Policy pages and product images are placeholders under the reserved demo domain; nothing is hosted there.
DEMO_SITE = f"https://{DEMO_EMAIL_DOMAIN}"
DEMO_SEEDED_BY = "scripts/seed_oacp_demo_merchant.py"
DEMO_RETURNS_POLICY = (
    "Synthetic demo policy: sealed tea and unused accessories can be returned within 7 days of delivery "
    "for a refund to the original payment method. No real goods are sold by this demo merchant."
)

# (key, product title, product type, description, ((sku, variant title, price INR, stock), ...))
_CATALOG: tuple[tuple[str, str, str, str, tuple[tuple[str, str, str, int], ...]], ...] = (
    (
        "NTH-FROST",
        "Nilgiri Frost Black Tea",
        "Black tea",
        "Synthetic demo product. Brisk winter-flush black tea with a bright, clean cup.",
        (("NTH-FROST-100", "100 g loose leaf", "349.00", 60), ("NTH-FROST-250", "250 g loose leaf", "799.00", 25)),
    ),
    (
        "NTH-GREEN",
        "Doddabetta Green Tea",
        "Green tea",
        "Synthetic demo product. Pan-fired green tea with a light, grassy finish.",
        (("NTH-GREEN-100", "100 g loose leaf", "399.00", 40),),
    ),
    (
        "NTH-WHITE",
        "Silver Tips White Tea",
        "White tea",
        "Synthetic demo product. Hand-picked silver buds, delicate and sweet.",
        (("NTH-WHITE-50", "50 g", "1299.00", 8),),
    ),
    (
        "NTH-OOLONG",
        "Blue Mountain Oolong",
        "Oolong tea",
        "Synthetic demo product. Lightly oxidised oolong with floral notes.",
        (("NTH-OOLONG-100", "100 g", "899.00", 0),),
    ),
    (
        "NTH-MASALA",
        "Masala Chai Blend",
        "Spiced tea",
        "Synthetic demo product. Black tea with cardamom, ginger, clove and cinnamon.",
        (("NTH-MASALA-250", "250 g", "449.00", 75),),
    ),
    (
        "NTH-HOMEMADE",
        "Hill Station Homemade Chocolate Tea",
        "Flavoured tea",
        "Synthetic demo product. Black tea with cocoa nibs from the hills.",
        (("NTH-HOMEMADE-100", "100 g", "499.00", 30),),
    ),
    (
        "NTH-SAMPLER",
        "Nilgiri Estate Sampler, 6 Teas",
        "Gift set",
        "Synthetic demo product. Six 25 g pouches from six imagined estates.",
        (("NTH-SAMPLER-6", "6 x 25 g", "999.00", 15),),
    ),
    (
        "NTH-KETTLE",
        "Cast Iron Tea Kettle 800 ml",
        "Accessories",
        "Synthetic demo product. Enamel-lined cast iron kettle with a removable infuser.",
        (("NTH-KETTLE-800", "800 ml", "2499.00", 5),),
    ),
    (
        "NTH-INFUSER",
        "Stainless Steel Tea Infuser",
        "Accessories",
        "Synthetic demo product. Fine-mesh infuser that fits most mugs.",
        (("NTH-INFUSER-1", "Standard", "199.00", 120),),
    ),
    (
        "NTH-CADDY",
        "Tin Tea Caddy",
        "Accessories",
        "Synthetic demo product. Airtight tin caddy with an inner lid.",
        (("NTH-CADDY-250", "250 g capacity", "299.00", 45),),
    ),
)

_STORE_HOURS: tuple[tuple[str, str, str], ...] = (
    ("monday", "09:00", "19:00"),
    ("tuesday", "09:00", "19:00"),
    ("wednesday", "09:00", "19:00"),
    ("thursday", "09:00", "19:00"),
    ("friday", "09:00", "19:00"),
    ("saturday", "09:00", "20:00"),
    ("sunday", "10:00", "18:00"),
)


class DemoMerchantError(RuntimeError):
    """The demo merchant cannot be seeded or used safely."""


def demo_merchant_enabled() -> bool:
    return DEMO_SOURCE_SYSTEM in runtime_source_systems()


def require_demo_merchant_enabled() -> None:
    if not demo_merchant_enabled():
        raise DemoMerchantError("AGENTICORG_OACP_DEMO_MERCHANT_ENABLED is off; the synthetic demo source is refused")


def demo_tenant_settings(existing: Mapping[str, Any] | None, *, now_iso: str) -> dict[str, Any]:
    """The demo tenant's catalogue settings: the marker merged into what is stored (host index kept)."""
    settings_value = dict(existing or {})
    marker = dict(settings_value.get(DEMO_TENANT_MARKER) or {})
    marker.update(
        {
            "synthetic": True,
            "merchant_id": DEMO_MERCHANT_ID,
            "seller_agent_id": DEMO_SELLER_AGENT_ID,
            "seeded_by": DEMO_SEEDED_BY,
        }
    )
    marker.setdefault("created_at", now_iso)
    marker.pop("torn_down_at", None)
    settings_value[DEMO_TENANT_MARKER] = marker
    settings_value["onboarding_complete"] = True
    return settings_value


def is_demo_tenant_settings(settings_value: Mapping[str, Any] | None) -> bool:
    marker = (settings_value or {}).get(DEMO_TENANT_MARKER)
    return isinstance(marker, Mapping) and marker.get("synthetic") is True


async def demo_tenant_marked(session: Any, tenant_id: str) -> bool:
    """Whether ``tenant_id``'s catalogue row carries the demo marker (tenants has no row-level security)."""
    from sqlalchemy import select

    from core.models.tenant import Tenant

    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except ValueError:
        return False
    settings_value = (await session.execute(select(Tenant.settings).where(Tenant.id == tenant_uuid))).scalar()
    return is_demo_tenant_settings(settings_value)


def demo_store() -> dict[str, Any]:
    """One invented tea shop in Ooty with UCP location hours in Asia/Kolkata."""
    return {
        "store_id": DEMO_STORE_ID,
        "display_name": "Nilgiri Tea House, Ooty (synthetic demo)",
        "pos_provider": "merchant_pos",
        "enabled": True,
        "city": "Ooty",
        "country_code": "IN",
        "staff_review_required": True,
        "location": {
            "timezone": "Asia/Kolkata",
            "address": {
                "street_address": "1 Demo Tea Lane",
                "address_locality": "Ooty",
                "address_region": "Tamil Nadu",
                "postal_code": "643001",
                "address_country": "IN",
            },
            "geo": {"latitude": 11.4102, "longitude": 76.6950},
            "hours": [{"day": day, "opens": opens, "closes": closes} for day, opens, closes in _STORE_HOURS],
        },
    }


def demo_merchant_config_body() -> dict[str, Any]:
    """The ``PUT /commerce/runtime/merchant-configs/{merchant_id}`` body the seed saves."""
    return {
        "seller_agent_id": DEMO_SELLER_AGENT_ID,
        "merchant_display_name": DEMO_DISPLAY_NAME,
        "public_brand_profile": {
            "display_name": DEMO_DISPLAY_NAME,
            "description": "Synthetic demo tea retailer for showing OACP end to end. No real goods or payments.",
            "synthetic_data": True,
            "website": DEMO_SITE,
            # The returns policy link is under provider_policy.ucp.links: the onboarding
            # packet's unsafe-value check reads a dotted host plus path as a compact JWS.
            "return_policy_summary": DEMO_RETURNS_POLICY,
        },
        "commerce_categories": ["tea", "tea_accessories"],
        "source_connectors": [
            {
                "connector_key": f"{DEMO_SOURCE_SYSTEM}:catalog",
                "connector_type": DEMO_SOURCE_SYSTEM,
                "store_id": "synthetic_catalog",
                "mode": "read_only",
                "enabled": True,
                "credential_custody": "not_required",
                "source_of_record": "synthetic demo catalog (no external system)",
                "sync_enabled": True,
            }
        ],
        "buyer_channels": {"web": {"enabled": True, "external_approval_status": "not_required"}},
        "payment_providers": [
            {
                "provider_key": "plural_pine",
                "provider_type": "plural_pine",
                "provider_display_name": "Pine Labs Plural/P3P",
                "environment": "sandbox",
                "credential_custody": "provider_owned",
                "capability_types": ["mandate_capability"],
            }
        ],
        "offline_pos_stores": [demo_store()],
        "public_publishing": {"enabled": False},
        "source_freshness_policy": {"max_age_seconds": 900},
        "provider_policy": {
            "arbiter": {
                "max_order_amount_minor_units": 1_000_000,
                "currency_allowlist": [DEMO_CURRENCY],
                "max_quantity_per_order": 10,
                "human_not_present": {"max_order_amount_minor_units": 200_000, "max_quantity_per_order": 3},
                "escalate_to_human": False,
            },
            "ucp": {
                "currency": DEMO_CURRENCY,
                "links": [
                    {"type": "privacy_policy", "url": f"{DEMO_SITE}/policies/privacy", "title": "Privacy (demo)"},
                    {"type": "terms_of_service", "url": f"{DEMO_SITE}/policies/terms", "title": "Terms (demo)"},
                    {"type": "refund_policy", "url": f"{DEMO_SITE}/policies/returns", "title": "Returns (demo)"},
                ],
                "fulfillment": {"shipping": True, "pickup": True},
            },
            "acp": {
                "checkout_enabled": True,
                "feed": {
                    "target_country": "IN",
                    "enable_search": True,
                    "product_url_template": f"{DEMO_SITE}/products/{{item_id}}",
                },
            },
        },
        "status": "configured",
        "sync_onboarding_packet": True,
    }


def demo_catalog_products(
    *,
    tenant_id: str,
    merchant_id: str = DEMO_MERCHANT_ID,
    seller_agent_id: str = DEMO_SELLER_AGENT_ID,
    synced_at: str,
) -> list[dict[str, Any]]:
    """The synthetic products in the normalized connector-evidence shape (INR prices, stock snapshots)."""
    products: list[dict[str, Any]] = []
    for product_key, title, product_type, description, variants in _CATALOG:
        products.append(
            {
                "product_ref": f"{DEMO_SOURCE_SYSTEM}_product:{_digest(product_key)[:24]}:synthetic",
                "source_product_id": f"{DEMO_SOURCE_SYSTEM}:product:{product_key}",
                "tenant_id": tenant_id,
                "merchant_id": merchant_id,
                "seller_agent_id": seller_agent_id,
                "title": title,
                "description": description,
                "vendor": DEMO_DISPLAY_NAME,
                "product_type": product_type,
                "status": "ACTIVE",
                # Placeholder addresses under the reserved demo domain; nothing is hosted there.
                "images": [{"url": f"{DEMO_SITE}/images/{product_key.lower()}.png", "alt_text": title}],
                "variants": [
                    {
                        "variant_id": f"{DEMO_SOURCE_SYSTEM}:variant:{sku}",
                        "sku": sku,
                        "title": variant_title,
                        "price": price,
                        "compare_at_price": None,
                        "currency": DEMO_CURRENCY,
                        "inventory_quantity_snapshot": stock,
                        "inventory_item_ref": f"{DEMO_SOURCE_SYSTEM}_inventory_item:{_digest(sku)[:24]}:synthetic",
                        "updated_at": synced_at,
                        "selected_options": [],
                    }
                    for sku, variant_title, price, stock in variants
                ],
                "updated_at": synced_at,
                "synced_at": synced_at,
                "source_system": DEMO_SOURCE_SYSTEM,
                "source_mode": "read_only",
                "synthetic_data": True,
            }
        )
    return products


def demo_catalog_digest() -> str:
    """SHA-256 of the catalog definition: the evidence id and reference change only when the catalog does."""
    return _digest(json.dumps(_CATALOG, sort_keys=True))


def build_demo_connector_evidence(*, packet: Mapping[str, Any], synced_at: str) -> dict[str, Any]:
    """Connector evidence for the synthetic catalog, validated like Shopify evidence.

    The evidence id and ``source_evidence_ref`` are stable for a given tenant,
    merchant, seller and catalog, so a refresh updates one evidence row (its
    observation time) instead of adding another.
    """
    validate_seller_onboarding_packet(packet)
    if packet.get("connector_choice") != DEMO_SOURCE_SYSTEM:
        raise DemoMerchantError("the onboarding packet is not the synthetic demo packet")
    tenant_id = str(packet["tenant_id"])
    merchant_id = str(packet["merchant_id"])
    seller_agent_id = str(packet["seller_agent_id"])
    products = demo_catalog_products(
        tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, synced_at=synced_at
    )
    seed = _digest("|".join((tenant_id, merchant_id, seller_agent_id, demo_catalog_digest())))
    evidence = {
        "evidence_id": f"c6z_demo_evidence_{seed[:24]}",
        "packet_id": packet["packet_id"],
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "source_system": DEMO_SOURCE_SYSTEM,
        "source_mode": "read_only",
        "source_evidence_ref": f"agenticorg:{DEMO_SOURCE_SYSTEM}:evidence:{seed[24:48]}",
        "source_observed_at": synced_at,
        "synced_at": synced_at,
        "currency": DEMO_CURRENCY,
        "products": products,
        "product_count": len(products),
        "variant_count": sum(len(product["variants"]) for product in products),
        "idempotency_key": None,
        "synthetic_data": True,
        "raw_payload_stored": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }
    validate_connector_evidence(evidence)
    return evidence


# The tenant's commerce tables whose rows a teardown may delete. The append-only
# ledger tables (audit_log, commerce_arbiter_decisions, the OACP decision and
# manifest records, witness checkpoints, payment operations and AFA evidence)
# are evidence and are never deleted.
TEARDOWN_TABLES: tuple[str, ...] = (
    "commerce_order_webhook_deliveries",
    "commerce_order_webhook_subscriptions",
    "commerce_ucp_idempotency",
    "commerce_orders",
    "commerce_carts",
    "commerce_checkout_sessions",
    "commerce_a2a_buyer_access",
    "commerce_c6z_offline_pos_confirmations",
    "commerce_c6z_offline_pos_handoff_packets",
    "commerce_c6z_provider_capability_evidence",
    "commerce_c6z_connector_evidence_records",
    "commerce_c6z_seller_onboarding_packets",
    "oacp_artifact_cache_records",
    "commerce_c6z_merchant_configs",
)


async def delete_demo_commerce_rows(session: Any, tenant_id: str) -> dict[str, int]:
    """Delete the demo tenant's rows from ``TEARDOWN_TABLES`` in a tenant-bound session; counts per table.

    Refuses unless the tenant carries the demo marker. Table names are the
    constants above, never input; the tenant id is a bound parameter (and
    row-level security binds the session to the same tenant).
    """
    from sqlalchemy import text

    if not await demo_tenant_marked(session, tenant_id):
        raise DemoMerchantError("refusing to delete commerce data of a tenant without the demo marker")
    counts: dict[str, int] = {}
    for table in TEARDOWN_TABLES:
        result = await session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id::text = :tenant_id"),  # noqa: S608  # nosec B608 — constant table names; the tenant is a bind parameter
            {"tenant_id": str(tenant_id)},
        )
        counts[table] = int(getattr(result, "rowcount", 0) or 0)
    return counts


async def prune_expired_demo_cache_records(
    session: Any, *, tenant_id: str, merchant_id: str, seller_agent_id: str, now: datetime | None = None
) -> int:
    """Delete the demo scope's expired artifact cache rows (a keep-fresh run issues a set every minute).

    Expired rows are refused by every reader; removing them keeps the scope's
    cache reads small. Demo tenants only.
    """
    from sqlalchemy import delete

    from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow

    if not await demo_tenant_marked(session, tenant_id):
        raise DemoMerchantError("refusing to prune the artifact cache of a tenant without the demo marker")
    result = await session.execute(
        delete(OacpArtifactCacheRecordRow).where(
            OacpArtifactCacheRecordRow.tenant_id == str(tenant_id),
            OacpArtifactCacheRecordRow.merchant_id == merchant_id,
            OacpArtifactCacheRecordRow.seller_agent_id == seller_agent_id,
            OacpArtifactCacheRecordRow.expires_at < (now or datetime.now(UTC)),
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


def demo_sourced(payloads: Sequence[Mapping[str, Any]]) -> bool:
    return any(artifact_names_demo_source(payload) for payload in payloads)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
