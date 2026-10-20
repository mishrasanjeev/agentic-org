# SPDX-License-Identifier: Apache-2.0
"""G37: ACP order_create / order_update events from the order entity, Merchant-Signature checked against the
shared Grantex vectors, and the acp_hmac webhook delivery signing with it."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from core.cases.push import SigningKey
from core.commerce.acp import orders as acp_orders
from core.commerce.ucp import orders, webhooks
from tests import ucp_txn_fixtures as uf

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "tests" / "fixtures" / "acp" / "merchant-signature-v1.json").read_text(encoding="utf-8"))
CHECKOUT_SCHEMA = json.loads(
    (ROOT / "core" / "commerce" / "acp" / "schemas" / "2026-04-17" / "schema.agentic_checkout.json").read_text(
        encoding="utf-8"
    )
)
TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000c7"))


def _event_errors(body: dict) -> list[str]:
    """``WebhookEvent`` of openapi.agentic_checkout_webhook.yaml: ``data`` is ``Order`` with four required members."""
    schema = {
        "$schema": CHECKOUT_SCHEMA["$schema"],
        "$defs": CHECKOUT_SCHEMA["$defs"],
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "data"],
        "properties": {
            "type": {"type": "string"},
            "data": {
                "allOf": [
                    {"$ref": "#/$defs/Order"},
                    {"required": ["type", "checkout_session_id", "permalink_url", "status"]},
                ]
            },
        },
    }
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(body)]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_merchant_signature_matches_the_shared_grantex_vectors(case) -> None:
    body = case["raw_body"].encode("utf-8")
    assert (
        acp_orders.verify_merchant_signature(case["header"], body, VECTORS["secret"], now=case["now"]) is case["valid"]
    )
    if case["name"] == "valid":
        assert acp_orders.merchant_signature(VECTORS["secret"], body, case["t"]) == case["header"]


def test_signature_header_format_and_wrong_secret() -> None:
    header = acp_orders.merchant_signature("secret-a", b"{}", 1_800_000_000)
    assert header.startswith("t=1800000000,v1=") and len(header.split("v1=")[1]) == 64
    assert acp_orders.verify_merchant_signature(header, b"{}", "secret-a", now=1_800_000_000)
    assert not acp_orders.verify_merchant_signature(header, b"{}", "secret-b", now=1_800_000_000)
    assert not acp_orders.verify_merchant_signature(None, b"{}", "secret-a", now=1_800_000_000)


def _snapshot() -> dict:
    state = {
        "currency": "INR",
        "line_items": [
            {
                "id": "li_1",
                "item": {"id": "var_1", "title": "Canvas Tote", "price": 2500},
                "quantity": 2,
                "totals": [{"type": "subtotal", "amount": 5000}, {"type": "total", "amount": 5000}],
            }
        ],
        "fulfillment": {
            "methods": [
                {
                    "id": "fm_1",
                    "type": "shipping",
                    "line_item_ids": ["li_1"],
                    "destinations": [{"id": "dest_1", "address": {"address_country": "IN"}}],
                    "selected_destination_id": "dest_1",
                    "groups": [
                        {"options": [{"id": "ship-std", "title": "Standard"}], "selected_option_id": "ship-std"}
                    ],
                }
            ]
        },
        "totals": [
            {"type": "subtotal", "amount": 5000},
            {"type": "fulfillment", "amount": 500},
            {"type": "total", "amount": 5500},
        ],
    }
    return orders.build_order_snapshot(
        order_id="ord_1", checkout_id="chk_1", merchant=uf.merchant(TENANT), state=state, accepted_term=None
    )


def _event(kind: str, snapshot: dict, revision: int = 1) -> orders.OrderEvent:
    return orders.OrderEvent(
        event_type=kind,
        tenant_id=TENANT,
        merchant_id="m",
        order_id="ord_1",
        revision=revision,
        snapshot=snapshot,
        ucp_version="2026-04-08",
    )


def test_order_create_is_the_full_acp_order() -> None:
    body = acp_orders.acp_order_event(_event(orders.ORDER_CREATED, _snapshot()))
    assert body["type"] == "order_create"
    assert _event_errors(body) == []
    data = body["data"]
    assert data["type"] == "order" and data["checkout_session_id"] == "chk_1" and data["status"] == "confirmed"
    assert data["line_items"][0]["quantity"] == {"ordered": 2, "current": 2, "fulfilled": 0}
    assert data["line_items"][0]["unit_price"] == 2500 and data["line_items"][0]["subtotal"] == 5000
    assert data["fulfillments"][0]["type"] == "shipping" and data["fulfillments"][0]["status"] == "pending"
    assert [t["type"] for t in data["totals"]] == ["subtotal", "fulfillment", "total"]


def test_order_updates_follow_fulfillment_and_adjustments() -> None:
    now = datetime.now(UTC)
    snapshot = _snapshot()
    orders.apply_fulfillment_event(
        snapshot,
        {
            "type": "shipped",
            "line_items": [{"id": "li_1", "quantity": 1}],
            "carrier": "Example Post",
            "tracking_number": "TRK1",
            "tracking_url": "https://track.example/TRK1",
        },
        now=now,
    )
    partial = acp_orders.acp_order_event(_event(orders.ORDER_UPDATED, snapshot, 2))
    assert partial["type"] == "order_update" and _event_errors(partial) == []
    assert partial["data"]["status"] == "processing"
    shipment = partial["data"]["fulfillments"][0]
    assert shipment["status"] == "shipped" and shipment["tracking_number"] == "TRK1"
    assert shipment["events"][0]["type"] == "shipped"
    orders.apply_adjustment(
        snapshot,
        {
            "type": "refund",
            "status": "completed",
            "line_items": [{"id": "li_1", "quantity": -1}],
            "totals": [{"type": "total", "amount": -2500}],
            "description": "Damaged",
        },
        now=now,
    )
    refunded = acp_orders.acp_order_event(_event(orders.ORDER_UPDATED, snapshot, 3))
    assert _event_errors(refunded) == []
    adjustment = refunded["data"]["adjustments"][0]
    assert adjustment["amount"] == 2500 and adjustment["currency"] == "inr"
    assert adjustment["line_items"] == [{"id": "li_1", "quantity": 1}]
    orders.apply_adjustment(
        snapshot,
        {"type": "cancellation", "status": "completed", "line_items": [{"id": "li_1", "quantity": -2}]},
        now=now,
    )
    canceled = acp_orders.acp_order_event(_event(orders.ORDER_UPDATED, snapshot, 4))
    assert canceled["data"]["status"] == "canceled" and _event_errors(canceled) == []


def test_the_webhook_machinery_renders_acp_events_for_acp_hmac_subscriptions() -> None:
    payload = webhooks._renderers["acp_hmac"](_event(orders.ORDER_CREATED, _snapshot()))
    assert payload["type"] == "order_create" and payload["data"]["id"] == "ord_1"


@pytest.mark.asyncio
async def test_acp_hmac_delivery_is_signed_with_merchant_signature() -> None:
    received: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json={"received": True})

    moment = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    dispatcher = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.MockTransport(_handler), clock=lambda: moment
    )
    secret = "synthetic-" + "s" * 24  # test-only shared secret
    item = {
        "id": uuid.uuid4(),
        "webhook_id": str(uuid.uuid4()),
        "event_type": orders.ORDER_CREATED,
        "payload": acp_orders.acp_order_event(_event(orders.ORDER_CREATED, _snapshot())),
        "attempts": 1,
        "url": "https://agent.example/agentic_checkout/webhooks/order_events",
        "scheme": "acp_hmac",
        "keys": [SigningKey("k_active", secret)],
        "merchant_id": "m",
        "seller_agent_id": "s",
        "business_profile": "https://shop.merchant.example/.well-known/ucp",
        "event_timestamp": int(moment.timestamp()),
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        error, status = await dispatcher._send(client, item, uuid.UUID(TENANT))
    assert (error, status) == ("", 200)
    sent = received[0]
    header = sent.headers["merchant-signature"]
    assert acp_orders.verify_merchant_signature(header, sent.content, secret, now=int(moment.timestamp()))
    assert sent.headers["request-id"] == item["webhook_id"]
    assert sent.headers["timestamp"] == "2026-10-20T12:00:00Z"
    assert json.loads(sent.content)["type"] == "order_create"
    item["keys"] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        assert await dispatcher._send(client, item, uuid.UUID(TENANT)) == ("signing_key_unavailable", None)
