from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime as commerce_runtime_api
from core.commerce.oacp_merchant_config import (
    MerchantCommerceConfigError,
    merchant_config_readiness,
    normalize_merchant_commerce_config,
)
from core.rbac import get_scopes_for_role

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _config(**overrides):
    params = {
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_demo",
        "seller_agent_id": "seller_agent_demo",
        "merchant_display_name": "Demo Store",
        "public_brand_profile": {"display_name": "Demo Store"},
        "commerce_categories": ["bags"],
        "source_connectors": [
            {
                "connector_type": "shopify",
                "store_id": "demo.myshopify.com",
                "shop_domain": "demo.myshopify.com",
                "mode": "read_only",
                "enabled": True,
                "credential_custody": "agenticorg_vault",
                "credential_ref": "commerce_shopify_merchant_demo",
                "source_of_record": "merchant Shopify Admin API",
            }
        ],
        "buyer_channels": {
            "web": True,
            "whatsapp": {
                "enabled": True,
                "credential_ref": "channel:whatsapp:merchant_demo",
                "external_approval_status": "pending",
            },
        },
        "payment_providers": [
            {
                "provider_type": "bank",
                "provider_key": "bank_xyz",
                "provider_display_name": "Bank XYZ",
                "credential_custody": "provider_owned",
                "credential_ref": "provider:bank_xyz:merchant_demo",
                "capability_types": ["mandate_capability", "payment_link"],
            }
        ],
        "offline_pos_stores": [
            {
                "store_id": "store_blr",
                "display_name": "Bengaluru Store",
                "pos_provider": "merchant_pos",
                "webhook_secret_ref": "pos:webhook:store_blr",
            }
        ],
        "public_publishing": {"enabled": True},
        "source_freshness_policy": {"max_age_seconds": 900},
        "provider_policy": {"human_confirmation_required": True},
    }
    params.update(overrides)
    return normalize_merchant_commerce_config(**params)


def test_merchant_config_is_tenant_merchant_scoped_and_redacted() -> None:
    config = _config()

    assert config["tenant_id"] == TENANT_ID
    assert config["merchant_id"] == "merchant_demo"
    assert config["seller_agent_id"] == "seller_agent_demo"
    assert config["source_connectors"][0]["credential_ref"] == "commerce_shopify_merchant_demo"
    assert config["payment_providers"][0]["provider_type"] == "bank"
    assert config["payment_providers"][0]["owns_execution"] is True
    assert config["payment_providers"][0]["agenticorg_executes_payment"] is False
    assert config["offline_pos_stores"][0]["webhook_secret_ref"] == "pos:webhook:store_blr"
    assert config["allowed_to_execute"] is False
    assert config["no_payment_execution"] is True
    assert "shpat_" not in str(config)


def test_merchant_config_readiness_marks_future_connectors_pending_adapter() -> None:
    config = _config(
        source_connectors=[
            {
                "connector_type": "woocommerce",
                "store_id": "woo_store",
                "base_url": "https://shop.example.test",
                "mode": "read_only",
                "credential_custody": "external_integration_provider",
                "credential_ref": "external:vault:woo_store",
            }
        ],
        payment_providers=[
            {
                "provider_type": "bank",
                "provider_key": "bank_owned_rail",
                "provider_display_name": "Merchant Bank",
            }
        ],
    )

    readiness = merchant_config_readiness(config)

    assert readiness["source_connectors"][0]["connector_type"] == "woocommerce"
    assert readiness["source_connectors"][0]["status"] == "configured_pending_adapter"
    assert readiness["payment_providers"][0]["bank_owned_provider"] is True
    assert readiness["payment_providers"][0]["status"] == "configured_provider_owned"
    assert readiness["allowed_to_execute"] is False


def test_merchant_config_rejects_secret_values_in_public_metadata() -> None:
    with pytest.raises(MerchantCommerceConfigError):
        _config(public_brand_profile={"client_secret": "do-not-publish"})


def test_merchant_config_permission_accepts_merchant_scope_without_admin() -> None:
    merchant_scopes = get_scopes_for_role("merchant")

    assert commerce_runtime_api.MERCHANT_COMMERCE_CONFIG_SCOPE in merchant_scopes
    assert "agenticorg:admin" not in merchant_scopes
    commerce_runtime_api.require_merchant_commerce_config_write(
        SimpleNamespace(state=SimpleNamespace(scopes=merchant_scopes))
    )
    commerce_runtime_api.require_merchant_commerce_config_write(
        SimpleNamespace(state=SimpleNamespace(scopes=["agenticorg:admin:platform"]))
    )
    with pytest.raises(HTTPException):
        commerce_runtime_api.require_merchant_commerce_config_write(
            SimpleNamespace(state=SimpleNamespace(scopes=[]))
        )


def test_shopify_merchant_config_syncs_to_runtime_packet() -> None:
    body = commerce_runtime_api.MerchantCommerceConfigUpsert(
        seller_agent_id="seller_agent_demo",
        merchant_display_name="Demo Store",
        commerce_categories=["bags"],
        source_connectors=[
            {
                "connector_type": "shopify",
                "store_id": "demo.myshopify.com",
                "shop_domain": "demo.myshopify.com",
                "credential_custody": "agenticorg_vault",
                "credential_ref": "commerce_shopify_merchant_demo",
            }
        ],
        buyer_channels={"web": True, "chatgpt": {"enabled": True, "external_approval_status": "pending"}},
        payment_providers=[{"provider_type": "plural_pine", "provider_key": "plural_pine"}],
    )
    config = commerce_runtime_api._normalize_merchant_config_from_body(
        tenant_id=TENANT_ID,
        merchant_id="merchant_demo",
        body=body,
    )
    packet = commerce_runtime_api._build_packet_from_merchant_config(config)

    assert packet["tenant_id"] == TENANT_ID
    assert packet["merchant_id"] == "merchant_demo"
    assert packet["seller_agent_id"] == "seller_agent_demo"
    assert packet["connector_choice"] == "shopify"
    assert packet["connector_metadata_redacted"]["shop_domain"] == "demo.myshopify.com"
    assert packet["channel_capability_preferences"]["chatgpt"] is True


def test_non_shopify_config_is_not_silently_runtime_packet_ready() -> None:
    body = commerce_runtime_api.MerchantCommerceConfigUpsert(
        seller_agent_id="seller_agent_demo",
        merchant_display_name="ERP Store",
        commerce_categories=["industrial"],
        source_connectors=[
            {
                "connector_type": "erp",
                "store_id": "sap_store",
                "credential_custody": "external_integration_provider",
                "credential_ref": "external:erp:sap_store",
            }
        ],
        buyer_channels={"web": True},
    )
    config = commerce_runtime_api._normalize_merchant_config_from_body(
        tenant_id=TENANT_ID,
        merchant_id="merchant_erp",
        body=body,
    )

    assert commerce_runtime_api._primary_source_connector(config)["connector_type"] == "erp"
    with pytest.raises(HTTPException):
        commerce_runtime_api._build_packet_from_merchant_config(config)
