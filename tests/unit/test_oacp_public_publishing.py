import json

import pytest

from core.commerce.oacp_public_publishing import (
    OacpPublicPublishingError,
    build_public_catalog_html,
    build_public_catalog_snapshot,
    build_public_llms_txt,
    build_public_sitemap_xml,
    find_public_product,
    public_catalog_enabled,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _evidence_record() -> dict:
    return {
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_demo",
        "seller_agent_id": "seller_demo",
        "source_observed_at": "2026-06-24T06:00:00Z",
        "synced_at": "2026-06-24T06:01:00Z",
        "products": [
            {
                "product_ref": "shopify_product:public:abc123",
                "source_product_id": "gid://shopify/Product/1",
                "title": "Canvas Tote",
                "description": "<p>Heavy canvas tote</p>",
                "vendor": "Demo Brand",
                "product_type": "Bags",
                "status": "ACTIVE",
                "images": [{"url": "https://cdn.example.test/tote.png", "alt_text": "Canvas Tote"}],
                "variants": [
                    {
                        "variant_id": "gid://shopify/ProductVariant/1",
                        "sku": "TOTE-001",
                        "title": "Natural",
                        "price": "1299.00",
                        "currency": "INR",
                        "inventory_quantity_snapshot": 4,
                        "selected_options": [{"name": "Color", "value": "Natural"}],
                    }
                ],
                "updated_at": "2026-06-24T05:58:00Z",
                "synced_at": "2026-06-24T06:01:00Z",
            }
        ],
    }


def _snapshot(**overrides):
    params = {
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_demo",
        "seller_agent_id": "seller_demo",
        "merchant_display_name": "Demo Store",
        "public_brand_profile": {"display_name": "Demo Store"},
        "commerce_categories": ["bags"],
        "connector_metadata_redacted": {"channel_capability_preferences": {"web": True, "chatgpt": True}},
        "evidence_records": [_evidence_record()],
        "base_url": "https://agenticorg.ai",
        "public_enabled": True,
    }
    params.update(overrides)
    return build_public_catalog_snapshot(**params)


def test_public_catalog_snapshot_generates_pages_jsonld_sitemap_and_llms() -> None:
    snapshot = _snapshot()

    assert snapshot["status"] == "public_catalog_ready"
    assert snapshot["publishing"]["oacp_public_discovery_certification"] == "none"
    assert snapshot["publishing"]["allowed_to_execute"] is False
    assert snapshot["products"][0]["variants"][0]["sku"] == "TOTE-001"
    assert snapshot["products"][0]["variants"][0]["inventory_quantity_snapshot"] == 4

    raw = json.dumps(snapshot)
    assert "gid://shopify" not in raw
    assert "source_product_id" not in raw
    assert "Source: Shopify via Grantex OACP artifact/evidence" in snapshot["source_label"]
    assert "2026-06-24T06:00:00Z" in snapshot["freshness_label"]

    schema = snapshot["schema_org_jsonld"]
    graph_types = {item["@type"] for item in schema["@graph"]}
    assert {"Organization", "Product"}.issubset(graph_types)
    assert schema["oacp_certification_status"] == "compatibility_mapping_only_not_external_certification"
    assert schema["no_payment_execution"] is True

    product = find_public_product(snapshot, snapshot["products"][0]["slug"])
    assert product and product["title"] == "Canvas Tote"
    html = build_public_catalog_html(snapshot, product_slug=product["slug"])
    assert "Canvas Tote" in html
    assert "No checkout or payment execution" in html
    assert "application/ld+json" in html
    assert 'type="application/ld+json">' not in html

    sitemap = build_public_sitemap_xml(snapshot)
    assert "<urlset" in sitemap
    assert product["public_url"].replace("&", "&amp;") in sitemap

    llms = build_public_llms_txt(snapshot)
    assert "TOTE-001: 1299.00 INR" in llms
    assert "not a checkout, payment, order, mandate" in llms


def test_public_catalog_fails_closed_when_operator_flag_is_disabled() -> None:
    snapshot = _snapshot(public_enabled=False)

    assert snapshot == {
        "status": "blocked",
        "reason": "public_catalog_disabled",
        "message": "Public OACP catalog publishing is disabled until the merchant enables public publishing.",
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_demo",
        "seller_agent_id": "seller_demo",
        "products": [],
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


def test_public_catalog_rejects_private_metadata() -> None:
    with pytest.raises(OacpPublicPublishingError):
        _snapshot(connector_metadata_redacted={"client_secret": "do-not-publish"})


def test_public_catalog_html_escapes_untrusted_fields_and_drops_unsafe_media_urls() -> None:
    evidence = _evidence_record()
    product = evidence["products"][0]
    product.update(
        {
            "title": 'Canvas <script>alert("title")</script>',
            "description": '<img src=x onerror=alert("description")>Heavy canvas tote',
            "vendor": '<svg onload=alert("vendor")>',
            "product_type": 'Bags"><script>alert("type")</script>',
            "images": [
                {"url": "javascript:alert(1)", "alt_text": "unsafe image"},
                {"url": "https://cdn.example.test/tote.png", "alt_text": '"><script>alert("alt")</script>'},
            ],
            "variants": [
                {
                    "variant_id": "gid://shopify/ProductVariant/1",
                    "sku": 'SKU"><script>alert("sku")</script>',
                    "title": '<script>alert("variant")</script>',
                    "price": '1299.00"><script>alert("price")</script>',
                    "currency": "INR",
                    "inventory_quantity_snapshot": 4,
                }
            ],
        }
    )

    snapshot = _snapshot(
        merchant_display_name='Demo <script>alert("merchant")</script>',
        evidence_records=[evidence],
    )
    html = build_public_catalog_html(snapshot)

    assert "<script" not in html.lower()
    assert "javascript:alert" not in html.lower()
    assert "onerror=" not in html.lower()
    assert "&lt;script&gt;alert(&quot;title&quot;)&lt;/script&gt;" in html
    assert "&lt;svg onload=alert(&quot;vendor&quot;)&gt;" in html
    assert 'type="application/ld+json">' not in html


def test_public_catalog_enabled_env_flag() -> None:
    assert public_catalog_enabled({"OACP_PUBLIC_CATALOG_ENABLED": "true"}) is True
    assert public_catalog_enabled({"OACP_PUBLIC_CATALOG_ENABLED": "0"}) is False
