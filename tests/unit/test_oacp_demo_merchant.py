# SPDX-License-Identifier: Apache-2.0
"""The synthetic OACP demo merchant source (core/commerce/oacp_demo_merchant.py).

The demo source is a clearly labelled ``synthetic_demo`` connector, accepted
only while ``AGENTICORG_OACP_DEMO_MERCHANT_ENABLED`` is on. With the flag off
every path keeps accepting Shopify read-only sources only; with it on, the
Shopify path is byte-for-byte what it was.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from api.v1 import commerce_runtime as runtime
from core.commerce.c6z_runtime_vertical import (
    DEMO_SOURCE_SYSTEM,
    C6ZRuntimeValidationError,
    build_cache_record_from_grantex_artifact,
    build_grantex_authority_request_payload,
    build_seller_onboarding_packet,
    build_shopify_connector_evidence,
    runtime_source_systems,
)
from core.commerce.oacp_demo_merchant import (
    DEMO_CURRENCY,
    DEMO_MERCHANT_ID,
    DEMO_SELLER_AGENT_ID,
    DEMO_TENANT_ID,
    DEMO_TENANT_MARKER,
    DemoMerchantError,
    build_demo_connector_evidence,
    demo_catalog_products,
    demo_merchant_config_body,
    demo_store,
    demo_tenant_settings,
    is_demo_tenant_settings,
    require_demo_merchant_enabled,
)
from core.commerce.ucp.catalog import ucp_variant_id
from core.commerce.ucp.locations import normalize_location_detail
from core.commerce.ucp.merchant import merchant_ucp_from_config
from core.config import settings

NOW = datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture()
def demo_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", True)


@pytest.fixture()
def demo_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", False)


def _demo_config() -> dict[str, Any]:
    body = runtime.MerchantCommerceConfigUpsert(**demo_merchant_config_body())
    return runtime._normalize_merchant_config_from_body(
        tenant_id=DEMO_TENANT_ID, merchant_id=DEMO_MERCHANT_ID, body=body
    )


def _demo_packet() -> dict[str, Any]:
    return runtime._build_packet_from_merchant_config(_demo_config())


def _shopify_packet() -> dict[str, Any]:
    return build_seller_onboarding_packet(
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        merchant_display_name="Merchant Example",
        public_brand_profile={"display_name": "Merchant Example"},
        commerce_categories=["bags"],
        requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
        artifact_cache_scope={},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": "merchant-example.myshopify.com", "credential_ref": "redacted"},
    )


def _shopify_evidence(packet: dict[str, Any]) -> dict[str, Any]:
    now = _iso(NOW)
    product = {
        "id": "gid://shopify/Product/1",
        "title": "Canvas Tote",
        "status": "ACTIVE",
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/11",
                    "sku": "TOTE-1",
                    "price": "100.00",
                    "inventoryItem": {"id": "gid://shopify/InventoryItem/11"},
                }
            ]
        },
    }
    return build_shopify_connector_evidence(
        packet=packet, products=[product], synced_at=now, source_observed_at=now, currency="INR"
    )


def _artifact_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "artifact_family": "catalog_snapshot",
        "tenant_id": DEMO_TENANT_ID,
        "merchant_id": DEMO_MERCHANT_ID,
        "seller_agent_id": DEMO_SELLER_AGENT_ID,
        "source_system": DEMO_SOURCE_SYSTEM,
        "source_lineage": {"source_system": DEMO_SOURCE_SYSTEM, "synthetic_data": True},
        "source_evidence_ref": "agenticorg:synthetic_demo:evidence:abc",
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "non_authoritative_for_transaction": True,
    }
    payload.update(overrides)
    return {
        "envelope": {
            "artifact_id": "oacp_art_DEMO0000000000000000000001",
            "artifact_type": "catalog_snapshot",
            "issued_at": _iso(NOW),
            "expires_at": _iso(NOW + timedelta(hours=1)),
        },
        "payload": payload,
    }


def test_catalog_is_synthetic_inr_with_stock_and_no_shopify_ids() -> None:
    products = demo_catalog_products(tenant_id=DEMO_TENANT_ID, synced_at=_iso(NOW))
    variants = [variant for product in products for variant in product["variants"]]
    assert 8 <= len(products) <= 12
    assert all(product["title"].startswith("Demo ") for product in products)
    assert all(product["source_system"] == DEMO_SOURCE_SYSTEM and product["synthetic_data"] for product in products)
    assert all(variant["currency"] == DEMO_CURRENCY == "INR" for variant in variants)
    assert all(isinstance(variant["inventory_quantity_snapshot"], int) for variant in variants)
    assert any(variant["inventory_quantity_snapshot"] == 0 for variant in variants)
    assert all(variant["variant_id"].startswith(f"{DEMO_SOURCE_SYSTEM}:variant:") for variant in variants)
    assert not any("shopify" in str(product).lower() for product in products)
    assert all(
        image["url"].startswith("https://demo.agenticorg.example/")
        for product in products
        for image in product["images"]
    )


def test_store_has_asia_kolkata_hours_for_every_day() -> None:
    store = demo_store()
    location = normalize_location_detail(store["location"])
    assert location is not None and location["timezone"] == "Asia/Kolkata"
    assert {entry["day"] for entry in location["hours"]} == {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }
    assert store["enabled"] is True and store["country_code"] == "IN"


@pytest.mark.usefixtures("demo_off")
def test_flag_off_refuses_the_synthetic_source_on_every_path() -> None:
    assert runtime_source_systems() == frozenset({"shopify"})
    with pytest.raises(DemoMerchantError):
        require_demo_merchant_enabled()
    with pytest.raises(Exception, match="unsupported value synthetic_demo"):
        _demo_config()
    with pytest.raises(C6ZRuntimeValidationError, match="connector_choice must be shopify$"):
        build_seller_onboarding_packet(
            tenant_id=DEMO_TENANT_ID,
            merchant_id=DEMO_MERCHANT_ID,
            seller_agent_id=DEMO_SELLER_AGENT_ID,
            merchant_display_name="Demo",
            public_brand_profile={},
            commerce_categories=["stationery"],
            requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
            artifact_cache_scope={},
            source_freshness_policy={"max_age_seconds": 900},
            connector_choice=DEMO_SOURCE_SYSTEM,
        )
    with pytest.raises(C6ZRuntimeValidationError, match="demo merchant is off"):
        build_cache_record_from_grantex_artifact(_artifact_payload(), cached_at=_iso(NOW))
    # The same refusal when only the lineage names the synthetic source.
    lineage_only = _artifact_payload(source_system="shopify")
    with pytest.raises(C6ZRuntimeValidationError, match="demo merchant is off"):
        build_cache_record_from_grantex_artifact(lineage_only, cached_at=_iso(NOW))


@pytest.mark.usefixtures("demo_on")
def test_flag_on_builds_the_demo_packet_evidence_and_authority_request() -> None:
    packet = _demo_packet()
    assert packet["connector_choice"] == DEMO_SOURCE_SYSTEM
    assert packet["connector_metadata_redacted"]["credential_ref"] is None
    first = build_demo_connector_evidence(packet=packet, synced_at=_iso(NOW))
    later = build_demo_connector_evidence(packet=packet, synced_at=_iso(NOW + timedelta(minutes=5)))
    # A refresh updates one evidence row: same id and reference, newer observation.
    assert (first["evidence_id"], first["source_evidence_ref"]) == (later["evidence_id"], later["source_evidence_ref"])
    assert later["source_observed_at"] > first["source_observed_at"]
    assert first["product_count"] == 10 and first["variant_count"] == 11
    payload = build_grantex_authority_request_payload(
        onboarding_packet=packet, connector_evidence=first, expected_tenant_id=DEMO_TENANT_ID
    )
    assert payload["request"]["connector_choice"] == DEMO_SOURCE_SYSTEM
    assert payload["connector_evidence"]["source_system"] == DEMO_SOURCE_SYSTEM
    assert payload["request"]["synthetic_data"] is True and payload["connector_evidence"]["synthetic_data"] is True
    assert payload["request"]["no_payment_execution"] is True
    assert payload["request"]["no_public_discovery_enablement"] is True


@pytest.mark.usefixtures("demo_on")
def test_demo_evidence_must_say_it_is_synthetic_and_match_its_packet() -> None:
    packet = _demo_packet()
    evidence = build_demo_connector_evidence(packet=packet, synced_at=_iso(NOW))
    with pytest.raises(C6ZRuntimeValidationError, match="synthetic_data"):
        build_grantex_authority_request_payload(
            onboarding_packet=packet, connector_evidence={**evidence, "synthetic_data": False}
        )
    shopify_packet = _shopify_packet()
    with pytest.raises(C6ZRuntimeValidationError, match="does not match the onboarding packet"):
        build_grantex_authority_request_payload(
            onboarding_packet=shopify_packet,
            connector_evidence={
                **evidence,
                "tenant_id": shopify_packet["tenant_id"],
                "merchant_id": shopify_packet["merchant_id"],
                "seller_agent_id": shopify_packet["seller_agent_id"],
            },
        )
    with pytest.raises(DemoMerchantError):
        build_demo_connector_evidence(packet=shopify_packet, synced_at=_iso(NOW))


def test_shopify_authority_request_is_unchanged_by_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def payload_shape(enabled: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", enabled)
        packet = _shopify_packet()
        payload = build_grantex_authority_request_payload(
            onboarding_packet=packet, connector_evidence=_shopify_evidence(packet)
        )
        request = {k: v for k, v in payload["request"].items() if k != "requested_at"}
        return request, payload["connector_evidence"]

    off_request, off_evidence = payload_shape(False)
    on_request, on_evidence = payload_shape(True)
    assert (off_request, off_evidence) == (on_request, on_evidence)
    assert off_request["connector_choice"] == "shopify" and off_evidence["source_system"] == "shopify"
    assert "synthetic_data" not in off_request and "synthetic_data" not in off_evidence


@pytest.mark.usefixtures("demo_on")
def test_demo_config_keeps_links_and_never_names_a_payment_executor() -> None:
    config = _demo_config()
    ucp_policy = config["provider_policy"]["ucp"]
    assert "executor" not in ucp_policy
    assert config["source_connectors"][0]["connector_type"] == DEMO_SOURCE_SYSTEM
    assert config["source_connectors"][0]["adapter_status"] == "runtime_supported"
    merchant = merchant_ucp_from_config(config, tenant_id=DEMO_TENANT_ID, serving_host="shop-demo.example.com")
    assert merchant is not None and merchant.executor is None and merchant.currency == "INR"
    assert {link["type"] for link in merchant.links} == {"privacy_policy", "terms_of_service", "refund_policy"}
    assert merchant.fulfillment.pickup is True and merchant.pickup_locations[0]["id"] == "demo_store_bengaluru"


@pytest.mark.usefixtures("demo_on")
def test_demo_connector_mode_must_stay_read_only() -> None:
    body = demo_merchant_config_body()
    body["source_connectors"][0]["mode"] = "external_reference"
    with pytest.raises(Exception, match="read_only"):
        runtime._normalize_merchant_config_from_body(
            tenant_id=DEMO_TENANT_ID, merchant_id=DEMO_MERCHANT_ID, body=runtime.MerchantCommerceConfigUpsert(**body)
        )


@pytest.mark.usefixtures("demo_on")
def test_cache_record_accepts_a_demo_artifact_only_with_the_flag_on() -> None:
    record = build_cache_record_from_grantex_artifact(_artifact_payload(), cached_at=_iso(NOW))
    assert record.tenant_id == DEMO_TENANT_ID and record.artifact_type == "catalog_snapshot"


def test_ucp_variant_ids_name_their_source() -> None:
    assert ucp_variant_id({"variant_id": f"{DEMO_SOURCE_SYSTEM}:variant:DEMO-MUG-350"}).startswith(
        "synthetic_demo_variant:"
    )
    assert ucp_variant_id({"variant_id": "gid://shopify/ProductVariant/11"}).startswith("shopify_variant:")


def test_tenant_marker_merges_and_keeps_the_host_index() -> None:
    existing = {
        "ucp_serving_hosts": {"shop-demo.example.com": {"merchant_id": DEMO_MERCHANT_ID}},
        DEMO_TENANT_MARKER: {"synthetic": True, "created_at": "2026-10-01T00:00:00Z", "torn_down_at": "x"},
    }
    merged = demo_tenant_settings(existing, now_iso="2026-10-21T00:00:00Z")
    assert merged["ucp_serving_hosts"] == existing["ucp_serving_hosts"]
    assert merged[DEMO_TENANT_MARKER]["created_at"] == "2026-10-01T00:00:00Z"
    assert "torn_down_at" not in merged[DEMO_TENANT_MARKER]
    assert merged["onboarding_complete"] is True
    assert is_demo_tenant_settings(merged)
    assert not is_demo_tenant_settings({"onboarding_complete": True})
    assert not is_demo_tenant_settings({DEMO_TENANT_MARKER: {"synthetic": "yes"}})
