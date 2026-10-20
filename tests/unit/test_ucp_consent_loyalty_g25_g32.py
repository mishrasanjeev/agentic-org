# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G25 + G32: buyer consent (dev.ucp.consent.* purpose map, source marker), loyalty from Shopify customer
metafields, attribution on the session, and the transport-neutral handler registry an MCP binding uses."""

from __future__ import annotations

import inspect
import uuid

import pytest

from core.commerce.ucp import consent as c
from core.commerce.ucp import handlers
from core.commerce.ucp.loyalty import loyalty_view
from tests import ucp_schema
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a6"))


def test_r25_purpose_map_rendered_with_platform_source_and_descriptions() -> None:
    raw = {
        c.MARKETING: {
            "granted": True,
            "source": "business",
            "segments": {c.MARKETING_EMAIL: {"granted": True}, c.MARKETING_SMS: {"granted": False}},
        },
        c.ANALYTICS: {"granted": False},
    }
    state = c.parse_consent(raw, version="2026-08-25")
    # A platform cannot assert the business's own consent records.
    assert state[c.MARKETING]["source"] == "platform"
    rendered = c.render_consent(state, version="2026-08-25")
    assert rendered[c.MARKETING]["description"] and rendered[c.ANALYTICS]["granted"] is False
    assert rendered[c.MARKETING]["segments"][c.MARKETING_SMS]["source"] == "platform"
    buyer = {"email": "buyer@example.com", "consent": rendered}
    ucp_schema.assert_valid("2026-08-25", "shopping/buyer_consent.json", buyer, pointer="/$defs/buyer")


def test_r08_booleans_map_to_the_same_purposes() -> None:
    state = c.parse_consent({"analytics": True, "sale_of_data": False}, version="2026-04-08")
    assert set(state) == {c.ANALYTICS, c.SALE_OR_SHARING}
    rendered = c.render_consent(state, version="2026-04-08")
    assert rendered == {"analytics": True, "sale_of_data": False}
    ucp_schema.assert_valid("2026-04-08", "shopping/buyer_consent.json", rendered, pointer="/$defs/consent")
    with pytest.raises(c.ConsentRequestError):
        c.parse_consent({"analytics": "yes"}, version="2026-04-08")
    with pytest.raises(c.ConsentRequestError):
        c.parse_consent({"NotReverseDns": {"granted": True}}, version="2026-08-25")


@pytest.mark.asyncio
async def test_loyalty_from_customer_metafields_when_eligible() -> None:
    merchant = uf.merchant(TENANT)
    reads = uf.FakeShopifyReads()
    view = await loyalty_view(
        merchant=merchant,
        reads=reads,
        email="buyer@example.com",
        eligibility=["com.merchant.rewards"],
        total_minor_units=7550,
        currency="INR",
    )
    membership = view["com.merchant.rewards"]
    assert membership["tiers"] == [
        {"id": "gold", "name": "Gold", "benefits": [{"id": "ship", "description": "Free shipping"}]}
    ]
    assert membership["rewards"][0]["earning_forecast"]["amount"] == 150  # 75 INR x 2 points
    assert membership["provisional"] is True
    assert reads.lookups == [("buyer@example.com", "loyalty")]
    ucp_schema.assert_valid("2026-04-08", "common/loyalty.json", view, pointer="/$defs/loyalty")


@pytest.mark.asyncio
async def test_loyalty_omitted_without_claim_email_or_on_lookup_failure() -> None:
    merchant = uf.merchant(TENANT)
    assert (
        await loyalty_view(
            merchant=merchant,
            reads=uf.FakeShopifyReads(),
            email="buyer@example.com",
            eligibility=[],
            total_minor_units=1,
            currency="INR",
        )
        is None
    )
    assert (
        await loyalty_view(
            merchant=merchant,
            reads=uf.FakeShopifyReads(),
            email=None,
            eligibility=["com.merchant.rewards"],
            total_minor_units=1,
            currency="INR",
        )
        is None
    )
    assert (
        await loyalty_view(
            merchant=merchant,
            reads=uf.FakeShopifyReads(fail=True),
            email="buyer@example.com",
            eligibility=["com.merchant.rewards"],
            total_minor_units=1,
            currency="INR",
        )
        is None
    )


def test_operations_registry_uses_ucp_tool_names_with_one_signature() -> None:
    assert set(handlers.OPERATIONS) == {
        "create_checkout",
        "get_checkout",
        "update_checkout",
        "complete_checkout",
        "cancel_checkout",
        "create_cart",
        "get_cart",
        "update_cart",
        "cancel_cart",
        "get_order",
    }
    for operation in handlers.OPERATIONS.values():
        names = list(inspect.signature(operation).parameters)
        assert names[:4] == ["runtime", "req", "merchant", "negotiated"] and len(names) == 5
    assert set(handlers.EVENTS) <= set(handlers.OPERATIONS)
