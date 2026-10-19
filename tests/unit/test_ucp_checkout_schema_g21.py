# SPDX-License-Identifier: Apache-2.0
"""G21: checkout state, status machine and totals, rendered for 2026-04-08 and 2026-08-25 and schema-validated."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.commerce.ucp import checkout as engine
from core.commerce.ucp.catalog import ProductEvidenceCatalog
from core.commerce.ucp.negotiation import negotiate
from core.commerce.ucp.registry import DEFAULT_REGISTRY, RegistryContext
from core.commerce.ucp.renderers import SessionView, render_checkout
from tests import ucp_fixtures as uf
from tests import ucp_schema

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a1"))
VERSIONS = ("2026-04-08", "2026-08-25")


async def _negotiated(version: str, capabilities=None):
    merchant = uf.merchant(TENANT)

    async def _fetch(_url: str) -> Any:
        return uf.platform_profile(version, capabilities=capabilities)

    negotiated = await negotiate(
        ucp_agent=f'profile="{uf.PLATFORM_PROFILE}"',
        merchant=merchant,
        registry=DEFAULT_REGISTRY,
        context=RegistryContext(merchant_signing_available=False),
        resource="checkout",
        fetcher=_fetch,
        supported_versions=VERSIONS,
    )
    return merchant, negotiated


def _deps() -> engine.EngineDeps:
    async def _products(_m):
        return uf.products(TENANT)

    async def _rates(_m):
        return uf.RATES

    return engine.EngineDeps(
        catalog=ProductEvidenceCatalog(_products), rates_source=_rates, shopify_reads=uf.FakeShopifyReads()
    )


async def _render(version: str, body: dict[str, Any], *, status: str | None = None, capabilities=None) -> dict:
    merchant, negotiated = await _negotiated(version, capabilities)
    evaluation = await engine.evaluate(
        resource="checkout",
        merchant=merchant,
        negotiated=negotiated,
        body=body,
        previous=None,
        deps=_deps(),
        now=datetime.now(UTC),
    )
    return render_checkout(
        session=SessionView(
            id="chk_test", status=status or evaluation.status, expires_at=datetime.now(UTC) + timedelta(hours=6)
        ),
        state=evaluation.state,
        negotiated=negotiated,
        merchant=merchant,
        handlers=DEFAULT_REGISTRY.payment_handlers(merchant, version),
    )


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.asyncio
async def test_ready_checkout_is_schema_valid_with_extensions(version: str) -> None:
    body = uf.create_body(
        2,
        fulfillment=uf.SHIPPING,
        buyer={"email": "buyer@example.com", "first_name": "Asha"},
        context={"eligibility": ["com.merchant.rewards"], "address_country": "IN"},
        attribution={"campaign_id": "spring", "gclid": "abc"},
        signals={"dev.ucp.buyer_ip": "203.0.113.7"},
    )
    rendered = await _render(version, body)
    assert rendered["status"] == "ready_for_complete"
    ucp_schema.assert_valid_checkout(version, rendered)
    assert rendered["ucp"]["version"] == version
    assert set(rendered["ucp"]["capabilities"]) >= {"dev.ucp.shopping.checkout", "dev.ucp.shopping.fulfillment"}
    assert "dev.ucp.shopping.cart" not in rendered["ucp"]["capabilities"]
    # Signals are never echoed; attribution is stored and shown.
    assert "signals" not in rendered
    assert rendered["attribution"] == {"campaign_id": "spring", "gclid": "abc"}


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.asyncio
async def test_totals_have_exactly_one_subtotal_and_total_in_signed_minor_units(version: str) -> None:
    rendered = await _render(version, uf.create_body(3, fulfillment=uf.SHIPPING))
    types = [t["type"] for t in rendered["totals"]]
    assert types.count("subtotal") == 1 and types.count("total") == 1
    amounts = {t["type"]: t["amount"] for t in rendered["totals"]}
    assert amounts == {"subtotal": 7500, "fulfillment": 5000, "total": 12500}
    assert all(isinstance(t["amount"], int) for t in rendered["totals"])
    assert sum(t["amount"] for t in rendered["totals"] if t["type"] != "total") == amounts["total"]


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.asyncio
async def test_business_outcomes_are_messages_and_status_incomplete(version: str) -> None:
    rendered = await _render(
        version,
        {"line_items": [{"item": {"id": "MUG-0"}, "quantity": 1}, {"item": {"id": "NOPE"}, "quantity": 1}]},
    )
    assert rendered["status"] == "incomplete"
    codes = {m["code"] for m in rendered["messages"] if m["type"] == "error"}
    assert {"out_of_stock", "not_found", "missing"} <= codes
    assert all(m["severity"] for m in rendered["messages"] if m["type"] == "error")
    ucp_schema.assert_valid_checkout(version, rendered)


@pytest.mark.asyncio
async def test_status_machine_escalation_needs_a_continue_url() -> None:
    from core.commerce.ucp import messages as msg

    escalate = [msg.error("requires_3ds", "Authenticate", severity="requires_buyer_input")]
    assert engine.status_for(escalate, "https://payments.example/3ds/1") == "requires_escalation"
    assert engine.status_for(escalate, None) == "incomplete"
    assert engine.status_for([msg.warning("final_sale", "Final sale")], None) == "ready_for_complete"
    assert engine.status_for([msg.error("out_of_stock", "None left")], None) == "incomplete"


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.asyncio
async def test_completed_checkout_carries_order_id_and_permalink(version: str) -> None:
    merchant, negotiated = await _negotiated(version)
    evaluation = await engine.evaluate(
        resource="checkout",
        merchant=merchant,
        negotiated=negotiated,
        body=uf.create_body(fulfillment=uf.SHIPPING),
        previous=None,
        deps=_deps(),
        now=datetime.now(UTC),
    )
    rendered = render_checkout(
        session=SessionView(
            id="chk_1",
            status="completed",
            expires_at=datetime.now(UTC) + timedelta(hours=6),
            order_id="ord_1",
            order_permalink_url="https://shop.merchant.example/ucp/orders/ord_1",
        ),
        state=evaluation.state,
        negotiated=negotiated,
        merchant=merchant,
        handlers=DEFAULT_REGISTRY.payment_handlers(merchant, version),
    )
    assert rendered["order"] == {"id": "ord_1", "permalink_url": "https://shop.merchant.example/ucp/orders/ord_1"}
    ucp_schema.assert_valid_checkout(version, rendered)
    in_progress = render_checkout(
        session=SessionView(
            id="chk_1",
            status="complete_in_progress",
            expires_at=datetime.now(UTC),
            order_id="ord_1",
            order_permalink_url="https://shop.merchant.example/ucp/orders/ord_1",
        ),
        state=evaluation.state,
        negotiated=negotiated,
        merchant=merchant,
        handlers=[],
    )
    assert "order" not in in_progress


@pytest.mark.parametrize("version", VERSIONS)
def test_create_update_complete_request_bodies_are_schema_valid(version: str) -> None:
    create = uf.create_body(1, buyer={"email": "buyer@example.com"}, context={"address_country": "IN"})
    ucp_schema.assert_valid(version, "shopping/checkout.json", create, request_op="create")
    update = {"line_items": [{"id": "li_1", "item": {"id": uf.SKU}, "quantity": 2}]}
    ucp_schema.assert_valid(version, "shopping/checkout.json", update, request_op="update")
    complete = {"payment": uf.token_payment()}
    ucp_schema.assert_valid(version, "shopping/checkout.json", complete, request_op="complete")


@pytest.mark.asyncio
async def test_line_items_are_capped_and_quantities_validated() -> None:
    merchant, negotiated = await _negotiated("2026-04-08")
    with pytest.raises(engine.RequestError):
        await engine.evaluate(
            resource="checkout",
            merchant=merchant,
            negotiated=negotiated,
            body={"line_items": []},
            previous=None,
            deps=_deps(),
            now=datetime.now(UTC),
        )
    with pytest.raises(engine.RequestError):
        await engine.evaluate(
            resource="checkout",
            merchant=merchant,
            negotiated=negotiated,
            body=uf.create_body(1.5),
            previous=None,
            deps=_deps(),
            now=datetime.now(UTC),
        )
    with pytest.raises(engine.RequestError):
        await engine.evaluate(
            resource="checkout",
            merchant=merchant,
            negotiated=negotiated,
            body=uf.create_body(True),
            previous=None,
            deps=_deps(),
            now=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_update_keeps_line_item_ids_and_assigns_new_ones() -> None:
    merchant, negotiated = await _negotiated("2026-04-08")
    first = await engine.evaluate(
        resource="checkout",
        merchant=merchant,
        negotiated=negotiated,
        body=uf.create_body(),
        previous=None,
        deps=_deps(),
        now=datetime.now(UTC),
    )
    line_id = first.state["line_items"][0]["id"]
    second = await engine.evaluate(
        resource="checkout",
        merchant=merchant,
        negotiated=negotiated,
        body={
            "line_items": [
                {"id": line_id, "item": {"id": uf.SKU}, "quantity": 2},
                {"item": {"id": uf.SKU}, "quantity": 1},
            ]
        },
        previous=first.state,
        deps=_deps(),
        now=datetime.now(UTC),
    )
    ids = [li["id"] for li in second.state["line_items"]]
    assert ids[0] == line_id and ids[1] != line_id
