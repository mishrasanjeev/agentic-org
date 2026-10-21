# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Regression: merchant config lists of objects kept their shape through the config route.

Failure replayed: ``PUT /api/v1/commerce/runtime/merchant-configs/{merchant_id}``
with the documented ``provider_policy.ucp.links``
(``[{"type": "privacy_policy", "url": "https://merchant.example/privacy"}]``,
docs/oacp/ucp-checkout.md, Merchant Settings) stored each link as the text
``"{'type': 'privacy_policy', ...}"``. Every reader skips a non-object link, so
the merchant had no privacy or terms link on its UCP checkout and ACP feed, and
``payment_terms`` and ``fulfillment.method_combinations`` were lost the same
way. Lists of objects and nested lists now keep their shape (each object goes
through the same unsafe-value checks); lists of scalars are unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime as runtime
from core.commerce.ucp.merchant import config_from_row, merchant_ucp_from_config

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _body(provider_policy: dict[str, Any]) -> runtime.MerchantCommerceConfigUpsert:
    return runtime.MerchantCommerceConfigUpsert(
        seller_agent_id="seller-01",
        merchant_display_name="Merchant Example",
        commerce_categories=["bags"],
        source_connectors=[
            {
                "connector_type": "shopify",
                "store_id": "merchant-example.myshopify.com",
                "shop_domain": "merchant-example.myshopify.com",
                "mode": "read_only",
                "credential_custody": "agenticorg_vault",
                "credential_ref": "commerce_shopify_merchant_example",
            }
        ],
        provider_policy=provider_policy,
    )


def _saved(provider_policy: dict[str, Any]) -> dict[str, Any]:
    """The config exactly as the PUT handler stores it on the row (normalize, then apply)."""
    config = runtime._normalize_merchant_config_from_body(
        tenant_id=TENANT_ID, merchant_id="merchant.example", body=_body(provider_policy)
    )
    row = SimpleNamespace(public_publishing={})
    runtime._apply_merchant_config(row, config, {})
    return config_from_row(row)


def test_ucp_links_survive_the_config_route() -> None:
    saved = _saved(
        {
            "ucp": {
                "links": [
                    {"type": "privacy_policy", "url": "https://merchant.example/privacy"},
                    {"type": "terms_of_service", "url": "https://merchant.example/terms"},
                ],
                "fulfillment": {"shipping": True, "method_combinations": [["shipping", "pickup"]]},
                "payment_terms": [{"id": "net-30", "title": "Net 30", "schedules": [{"id": "s1", "type": "net"}]}],
            },
            "arbiter": {"currency_allowlist": ["INR", ""]},
        }
    )
    ucp = saved["provider_policy"]["ucp"]
    assert ucp["links"][0] == {"type": "privacy_policy", "url": "https://merchant.example/privacy"}
    assert ucp["fulfillment"]["method_combinations"] == [["shipping", "pickup"]]
    assert ucp["payment_terms"][0]["schedules"] == [{"id": "s1", "type": "net"}]
    # Scalar lists are cleaned as before: trimmed text, empty values dropped.
    assert saved["provider_policy"]["arbiter"]["currency_allowlist"] == ["INR"]
    merchant = merchant_ucp_from_config(saved, tenant_id=TENANT_ID, serving_host="shop.merchant.example")
    assert merchant is not None
    assert [link["type"] for link in merchant.links] == ["privacy_policy", "terms_of_service"]
    assert merchant.fulfillment.method_combinations == (("shipping", "pickup"),)


def test_objects_inside_lists_still_go_through_the_unsafe_value_check() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _saved({"ucp": {"links": [{"type": "privacy_policy", "access_token": "placeholder"}]}})
    assert exc_info.value.status_code == 422
