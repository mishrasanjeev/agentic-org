# SPDX-License-Identifier: Apache-2.0
"""Synthetic merchant, key and artifact fixtures for the UCP tests (example.com hosts, generated keys)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from core.commerce import merchant_keys
from core.commerce.oacp_artifacts import OACP_ARTIFACT_TTLS_SECONDS, OacpPersistentArtifactCacheRecord
from core.commerce.ucp.catalog import REQUIRED_CATALOG_ARTIFACTS
from core.commerce.ucp.state import with_ucp_state

TENANT_ID = "11111111-1111-1111-1111-111111111111"
MERCHANT_ID = "merchant_1"
SELLER_AGENT_ID = "seller_agent_1"
HOST = "shop.merchant.example"
NAMESPACE = "example.merchant.shop"
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ATTESTATION_REF = "agenticorg:ucp-attestation:0123456789abcdef0123456789abcdef"
PUBLIC_DISCOVERY_ARTIFACT_ID = "oacp_art_public_discovery"
VERIFIED_REF = (
    "oacp-artifact-jws-v1;kid=oacp-20261019-0123456789abcdef;payload_hash=ab;status=valid;"
    "verified_at=2026-10-19T10:00:00Z"
)


def fake_tenant_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the tenant KEK envelope with a reversible test wrapper (no KMS, no database)."""

    async def _encrypt(pem: str, tenant_id: str) -> str:
        assert tenant_id == TENANT_ID
        return "test-envelope:" + pem[::-1]

    async def _decrypt(ciphertext: str) -> str:
        assert ciphertext.startswith("test-envelope:")
        return ciphertext[len("test-envelope:") :][::-1]

    monkeypatch.setattr(merchant_keys, "_encrypt_private_key", _encrypt)
    monkeypatch.setattr(merchant_keys, "_decrypt_private_key", _decrypt)


def key_record(now: datetime = NOW) -> dict[str, Any]:
    return asyncio.run(merchant_keys.new_merchant_key_record(TENANT_ID, now=now))


def published_state(keys: list[dict[str, Any]], *, host: str = HOST, namespace: str = NAMESPACE) -> dict[str, Any]:
    return {
        "serving_host": host,
        "attestation": {
            "method": "dns_txt",
            "host": host,
            "record_name": f"_agenticorg-ucp.{host}",
            "record_value_sha256": "0" * 64,
            "verified_at": "2026-10-19T08:00:00Z",
            "verified_by": {"actor_type": "user", "actor_id": "operator@merchant.example"},
            "ref": ATTESTATION_REF,
        },
        "namespace": namespace,
        "published": {"published_at": "2026-10-19T08:05:00Z", "namespace": namespace},
        "signing_keys": keys,
        "verified_public_discovery": {
            "artifact_id": PUBLIC_DISCOVERY_ARTIFACT_ID,
            "verifier_result_ref": VERIFIED_REF,
            "captured_at": "2026-10-19T08:10:00Z",
            "publication_allowed": True,
            "serving_host": host,
            "namespace_attestation_ref": ATTESTATION_REF,
        },
    }


LIVE_DISCOVERY = frozenset({"oacp_art_public_discovery"})


def serve_state(row: Any, *, host: str = HOST, now: datetime = NOW, live: Any = LIVE_DISCOVERY) -> Any:
    from core.commerce.ucp.profile import merchant_serve_state

    return merchant_serve_state(row, host=host, now=now, live_public_discovery_ids=set(live))


def merchant_row(state: dict[str, Any] | None = None, *, stores: list[dict[str, Any]] | None = None) -> Any:
    return SimpleNamespace(
        config_id="commerce_config_test",
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        offline_pos_stores=stores or [],
        public_publishing=with_ucp_state({"enabled": False}, state or {}),
    )


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def cache_record(
    artifact_type: str,
    *,
    ref: str | None = VERIFIED_REF,
    now: datetime | None = None,
    expires_in: timedelta = timedelta(minutes=2),
    buyer_agent_id: str | None = None,
) -> OacpPersistentArtifactCacheRecord:
    now = now or datetime.now(UTC)
    return OacpPersistentArtifactCacheRecord(
        cache_record_id=f"cache_{artifact_type}",
        artifact_id=f"oacp_art_{artifact_type}",
        artifact_type=artifact_type,  # type: ignore[arg-type]
        authority="grantex.internal.oacp.authority",
        issuer="grantex.internal.oacp.authority",
        scope_kind="seller_agent",
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        buyer_agent_id=buyer_agent_id,
        source_refs=("agenticorg:shopify:evidence:abc:redacted",),
        evidence_refs=("agenticorg:shopify:evidence:abc:redacted",),
        generated_at=_iso(now - timedelta(minutes=1)),
        cached_at=_iso(now - timedelta(minutes=1)),
        expires_at=_iso(now + expires_in),
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_observed_at=_iso(now - timedelta(seconds=10)),
        ttl_policy_seconds=min(OACP_ARTIFACT_TTLS_SECONDS[artifact_type], 300),  # type: ignore[index]
        risk_tier="low",
        revocation_snapshot_age_seconds=10,
        verifier_result_ref=ref,
    )


def catalog_records(**kwargs: Any) -> list[OacpPersistentArtifactCacheRecord]:
    return [cache_record(kind, **kwargs) for kind in REQUIRED_CATALOG_ARTIFACTS]


def synced_products() -> list[dict[str, Any]]:
    return [
        {
            "product_ref": "shopify_product:aaaaaaaaaaaaaaaaaaaaaaaa:redacted",
            "title": "Canvas Tote",
            "description": "<p>Sturdy cotton <b>tote</b> bag.</p>",
            "vendor": "Merchant Example",
            "product_type": "Bags",
            "status": "ACTIVE",
            "images": [{"url": "https://cdn.merchant.example/tote.jpg", "alt_text": "Tote"}],
            "variants": [
                {
                    "variant_id": "gid://shopify/ProductVariant/1001",
                    "sku": "TOTE-NAT",
                    "title": "Natural",
                    "price": "1299.00",
                    "compare_at_price": "1499.00",
                    "currency": "INR",
                    "inventory_quantity_snapshot": 4,
                    "selected_options": [{"name": "Colour", "value": "Natural"}],
                },
                {
                    "variant_id": "gid://shopify/ProductVariant/1002",
                    "sku": "TOTE-BLK",
                    "title": "Black",
                    "price": "1399.00",
                    "currency": None,
                    "inventory_quantity_snapshot": 0,
                    "selected_options": [{"name": "Colour", "value": "Black"}],
                },
            ],
            "_evidence_currency": "INR",
        },
        {
            "product_ref": "shopify_product:bbbbbbbbbbbbbbbbbbbbbbbb:redacted",
            "title": "Linen Apron",
            "description": None,
            "product_type": "Kitchen",
            "status": "ACTIVE",
            "variants": [
                {
                    "variant_id": "gid://shopify/ProductVariant/2001",
                    "sku": "APRON-1",
                    "title": "Default Title",
                    "price": "899",
                    "currency": "INR",
                    "inventory_quantity_snapshot": 9,
                }
            ],
        },
        {
            "product_ref": "shopify_product:cccccccccccccccccccccccc:redacted",
            "title": "Draft Mug",
            "status": "DRAFT",
            "variants": [{"variant_id": "gid://shopify/ProductVariant/3001", "price": "10", "currency": "INR"}],
        },
    ]


def store_with_hours() -> dict[str, Any]:
    return {
        "store_id": "store_pune_1",
        "display_name": "Merchant Example Pune",
        "enabled": True,
        "city": "Pune",
        "country_code": "IN",
        "location": {
            "timezone": "Asia/Kolkata",
            "address": {"street_address": "1 Example Road", "postal_code": "411001", "address_country": "IN"},
            "geo": {"latitude": 18.52, "longitude": 73.85},
            "hours": [
                {"day": day, "opens": "10:00", "closes": "20:00"}
                for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
            ],
            "exception_hours": [{"valid_from": "2026-10-21", "valid_through": "2026-10-21", "title": "Festival"}],
        },
    }
