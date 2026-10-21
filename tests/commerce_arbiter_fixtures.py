# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Synthetic runtime scope for commerce arbiter route tests (verified OACP records, a product, provider evidence)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.commerce.c6z_runtime_vertical import build_seller_onboarding_packet, build_shopify_connector_evidence
from core.commerce.oacp_artifacts import OACP_ARTIFACT_TTLS_SECONDS, OacpPersistentArtifactCacheRecord

MERCHANT = "merchant.example"
SELLER = "seller-01"
BUYER = "shopper-01"
FAMILIES = ("catalog_snapshot", "price", "inventory", "policy", "mandate_capability", "protocol_adapter")


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def cache_records(tenant_id: str, *, now: datetime | None = None) -> list[OacpPersistentArtifactCacheRecord]:
    moment = now or datetime.now(UTC)
    return [
        OacpPersistentArtifactCacheRecord(
            cache_record_id=f"cache_{family}",
            artifact_id=f"oacp_art_{family}",
            artifact_type=family,
            authority="grantex.internal.oacp.authority",
            issuer="grantex.internal.oacp.authority",
            scope_kind="seller_agent",
            tenant_id=tenant_id,
            merchant_id=MERCHANT,
            seller_agent_id=SELLER,
            buyer_agent_id=None,
            source_refs=("agenticorg:shopify:evidence:abc:redacted",),
            evidence_refs=("agenticorg:shopify:evidence:abc:redacted",),
            generated_at=iso(moment - timedelta(seconds=5)),
            cached_at=iso(moment - timedelta(seconds=5)),
            expires_at=iso(moment - timedelta(seconds=5) + timedelta(seconds=OACP_ARTIFACT_TTLS_SECONDS[family])),
            freshness_status="fresh",
            revocation_snapshot_status="fresh",
            revocation_snapshot_observed_at=iso(moment - timedelta(seconds=5)),
            ttl_policy_seconds=OACP_ARTIFACT_TTLS_SECONDS[family],
            risk_tier="low",
            blocked_capabilities=("checkout", "payment", "order", "mandate"),
            unsupported_capabilities=("execution", "public_discovery", "live_provider"),
            verifier_result_ref=(
                f"oacp-artifact-jws-v1;kid=oacp-test-2026-10;payload_hash={'c' * 64};status=valid;"
                f"verified_at={iso(moment)}"
            ),
            revocation_snapshot_age_seconds=5,
        )
        for family in FAMILIES
    ]


def products(tenant_id: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(UTC)
    packet = build_seller_onboarding_packet(
        tenant_id=tenant_id,
        merchant_id=MERCHANT,
        seller_agent_id=SELLER,
        merchant_display_name="Example Store",
        public_brand_profile={"display_name": "Example Store"},
        commerce_categories=["apparel"],
        requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
        artifact_cache_scope={"merchant_id": MERCHANT, "seller_agent_id": SELLER},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": "example-store.myshopify.com", "credential_ref": "env"},
    )
    product = {
        "id": "gid://shopify/Product/1",
        "title": "Canvas Tote",
        "descriptionHtml": "Heavy canvas tote",
        "vendor": "Example Brand",
        "productType": "Bags",
        "status": "ACTIVE",
        "updatedAt": iso(moment),
        "media": {"nodes": []},
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "sku": "TOTE-1",
                    "title": "Default",
                    "price": "25.00",
                    "compareAtPrice": None,
                    "inventoryQuantity": 7,
                    "updatedAt": iso(moment),
                    "selectedOptions": [],
                    "inventoryItem": {"id": "gid://shopify/InventoryItem/1"},
                }
            ]
        },
    }
    evidence = build_shopify_connector_evidence(
        packet=packet,
        products=[product],
        synced_at=iso(moment - timedelta(seconds=10)),
        source_observed_at=iso(moment - timedelta(seconds=10)),
        currency="INR",
    )
    return list(evidence["products"])


def capabilities(*, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(UTC)
    return [
        {
            "result_status": "available",
            "checked_at": iso(moment - timedelta(seconds=30)),
            "expires_at": iso(moment + timedelta(minutes=10)),
            "redacted_evidence_ref": "provider:plural_pine:capability:abc:redacted",
            "provider_environment": "sandbox",
            "raw_payload_stored": False,
        }
    ]


def merchant_config(*, escalate_to_human: bool = False, review_agent_id: str | None = None) -> dict[str, Any]:
    return {
        "merchant_id": MERCHANT,
        "seller_agent_id": SELLER,
        "provider_policy": {
            "arbiter": {
                "max_order_amount_minor_units": 100000,
                "currency_allowlist": ["INR"],
                "max_quantity_per_order": 5,
                "escalate_to_human": escalate_to_human,
                "review_agent_id": review_agent_id,
            }
        },
    }


def buyer_authority(*, now: datetime | None = None, **overrides: Any) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    authority = {
        "mandate_ref": "mandate-example-1",
        "mandate_hash": "d" * 64,
        "mandate_expires_at": iso(moment + timedelta(hours=1)),
        "consent_ref": "consent-example-1",
        "consent_status": "active",
        "passport_ref": "passport-example-1",
        "passport_max_amount_minor_units": 50000,
        "passport_currency": "INR",
        "passport_expires_at": iso(moment + timedelta(hours=1)),
    }
    authority.update(overrides)
    return authority
