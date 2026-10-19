# SPDX-License-Identifier: Apache-2.0
"""G22: merchant-side carts (dev.ucp.shopping.cart), update without cart.id, the shopping permalink continue_url."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.commerce.ucp.catalog import ProductEvidenceCatalog
from core.commerce.ucp.checkout import EngineDeps, evaluate, permalink_url
from core.commerce.ucp.handlers import _cart_as_checkout_body
from core.commerce.ucp.negotiation import negotiate
from core.commerce.ucp.registry import DEFAULT_REGISTRY, RegistryContext
from core.commerce.ucp.renderers import render_cart
from tests import ucp_fixtures as uf
from tests import ucp_schema

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a8"))


async def _cart(version: str, body: dict[str, Any]) -> dict[str, Any]:
    merchant = uf.merchant(TENANT)

    async def _fetch(_url: str) -> Any:
        return uf.platform_profile(version)

    negotiated = await negotiate(
        ucp_agent=f'profile="{uf.PLATFORM_PROFILE}"',
        merchant=merchant,
        registry=DEFAULT_REGISTRY,
        context=RegistryContext(),
        resource="cart",
        fetcher=_fetch,
        supported_versions=(version,),
    )

    async def _products(_m):
        return uf.products(TENANT)

    evaluation = await evaluate(
        resource="cart",
        merchant=merchant,
        negotiated=negotiated,
        body=body,
        previous=None,
        deps=EngineDeps(catalog=ProductEvidenceCatalog(_products), shopify_reads=uf.FakeShopifyReads()),
        now=datetime.now(UTC),
    )
    return render_cart(
        cart_id="cart_1",
        state=evaluation.state,
        negotiated=negotiated,
        merchant=merchant,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )


@pytest.mark.parametrize("version", ["2026-04-08", "2026-08-25"])
@pytest.mark.asyncio
async def test_cart_response_is_schema_valid_with_continue_url(version: str) -> None:
    body = uf.create_body(2, buyer={"email": "buyer@example.com"}, context={"eligibility": ["com.merchant.rewards"]})
    cart = await _cart(version, body)
    ucp_schema.assert_valid_cart(version, cart)
    assert "status" not in cart  # carts have no lifecycle status
    assert cart["continue_url"] == "https://merchant.example/buy/TOTE-1:2"
    assert set(cart["ucp"]["capabilities"]) >= {"dev.ucp.shopping.cart"}
    assert "dev.ucp.shopping.checkout" not in cart["ucp"]["capabilities"]
    assert "loyalty" in cart
    ucp_schema.assert_valid(version, "shopping/cart.json", body, request_op="create")


def test_update_body_omits_cart_id_in_2026_08_25() -> None:
    update = {"line_items": [{"item": {"id": uf.SKU}, "quantity": 1}]}
    ucp_schema.assert_valid("2026-08-25", "shopping/cart.json", update, request_op="update")
    assert ucp_schema.errors("2026-08-25", "shopping/cart.json", {**update, "id": "cart_1"}, request_op="update")
    # 2026-04-08 still lists cart.id as required on update; the handler accepts either form.
    ucp_schema.assert_valid("2026-04-08", "shopping/cart.json", {**update, "id": "cart_1"}, request_op="update")


def test_permalink_tokens_follow_the_ucp_grammar() -> None:
    merchant = uf.merchant(TENANT)
    lines = [{"item": {"id": "TOTE-1"}, "quantity": 2}, {"item": {"id": "gid://x/1 2"}, "quantity": 1}]
    url = permalink_url(merchant, lines)
    assert url.startswith("https://merchant.example/buy/TOTE-1:2,~")
    assert permalink_url(uf.merchant(TENANT, permalink_endpoint=None), lines) is None
    assert uf.merchant(TENANT, permalink_endpoint="https://merchant.example/buy/").permalink_endpoint is None


def test_cart_contents_replace_overlapping_checkout_fields() -> None:
    cart_state = {
        "line_items": [{"item": {"id": "TOTE-1"}, "quantity": 3}],
        "buyer": {"email": "b@example.com"},
        "context": {"address_country": "IN"},
    }
    body = _cart_as_checkout_body(
        cart_state,
        {
            "cart_id": "cart_1",
            "line_items": [{"item": {"id": "X"}, "quantity": 9}],
            "buyer": {"email": "other@example.com"},
            "payment": {"instruments": []},
        },
    )
    assert body["line_items"] == [{"item": {"id": "TOTE-1"}, "quantity": 3}]
    assert body["buyer"] == {"email": "b@example.com"} and "cart_id" not in body and "payment" in body
