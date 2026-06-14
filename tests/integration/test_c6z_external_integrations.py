from __future__ import annotations

import os

import pytest

from core.commerce.c6z_runtime_vertical import (
    PLURAL_PINE_ENV_VARS,
    SHOPIFY_ENV_VARS,
    ShopifyAdminGraphQLClient,
    resolve_env,
    resolve_shopify_credentials,
    verify_plural_pine_mandate_capability,
)


@pytest.mark.asyncio
async def test_shopify_admin_graphql_read_only_sync_when_env_present() -> None:
    resolution = resolve_env(SHOPIFY_ENV_VARS, os.environ)
    if not resolution.ready:
        pytest.skip(f"Shopify sandbox env vars missing: {', '.join(resolution.missing)}")

    client = ShopifyAdminGraphQLClient(resolve_shopify_credentials())
    products = await client.fetch_products(page_size=1, max_pages=1)

    assert isinstance(products, list)


@pytest.mark.asyncio
async def test_plural_pine_capability_check_when_env_present() -> None:
    resolution = resolve_env(PLURAL_PINE_ENV_VARS, os.environ)
    if not resolution.ready:
        pytest.skip(f"Plural/Pine sandbox env vars missing: {', '.join(resolution.missing)}")

    evidence = await verify_plural_pine_mandate_capability(
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant_integration",
    )

    assert evidence.external_validation_performed is True
    assert evidence.raw_payload_stored is False
    assert evidence.allowed_to_execute is False
    assert evidence.no_payment_execution is True
