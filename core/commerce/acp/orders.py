# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""ACP order webhook events: ``order_create`` / ``order_update`` signed with ``Merchant-Signature`` (G37).

ACP 2026-04-17 (``openapi.agentic_checkout_webhook.yaml``): the merchant POSTs
``WebhookEvent`` ``{type, data}`` to the agent platform's receiver, where ``type`` is
``order_create`` or ``order_update`` (there are no dotted ``order.*`` names in ACP)
and ``data`` is the full ``Order`` (``type: "order"``, ``checkout_session_id``,
``permalink_url``, ``status``; never a delta). Every delivery carries
``Merchant-Signature: t=<unix seconds>,v1=<64 hex>`` with
``v1 = hex(HMAC-SHA256(secret, t + "." + raw_body))``; receivers allow 300 seconds.

The order entity is the UCP one (``core.commerce.ucp.orders``): this module maps its
version-neutral snapshot to the ACP ``Order`` and is registered as the payload
renderer of ``acp_hmac`` webhook subscriptions (``core.commerce.ucp.webhooks``), so
ACP events ride the same outbox, lease, retry and dead-letter machinery. The order
status is derived from the line quantities (all removed: ``canceled``; all
fulfilled: ``shipped``; some: ``processing``; none: ``confirmed``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

from core.commerce.ucp.orders import ORDER_CREATED, OrderEvent

SIGNATURE_HEADER = "Merchant-Signature"
TOLERANCE_SECONDS = 300
EVENT_TYPES = {ORDER_CREATED: "order_create"}
_HEADER = re.compile(r"^t=(\d+),v1=([a-fA-F0-9]{64})$")
_TOTAL_LABELS = {
    "subtotal": "Subtotal",
    "fulfillment": "Shipping",
    "total": "Total",
    "discount": "Discount",
    "tax": "Tax",
    "fee": "Fee",
}
_FULFILLMENT_STATUS = {
    "shipped": "shipped",
    "in_transit": "in_transit",
    "out_for_delivery": "out_for_delivery",
    "delivered": "delivered",
    "picked_up": "delivered",
    "fulfilled": "delivered",
    "ready_for_pickup": "ready_for_pickup",
    "failed": "failed",
    "canceled": "canceled",
}


def acp_event_type(event: OrderEvent) -> str:
    return EVENT_TYPES.get(event.event_type, "order_update")


def merchant_signature(secret: str, raw_body: bytes, timestamp: int) -> str:
    """``Merchant-Signature`` header value for ``raw_body`` signed at ``timestamp``."""
    mac = hmac.new(secret.encode("utf-8"), str(timestamp).encode("ascii") + b"." + raw_body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify_merchant_signature(
    header: str | None, raw_body: bytes, secret: str, *, now: int, tolerance: int = TOLERANCE_SECONDS
) -> bool:
    """Receiver-side check (the agent platform's): format, freshness, then a constant-time MAC compare."""
    match = _HEADER.match(header or "")
    if match is None:
        return False
    timestamp = int(match.group(1))
    if abs(now - timestamp) > tolerance:
        return False
    expected = merchant_signature(secret, raw_body, timestamp).split("v1=", 1)[1]
    return hmac.compare_digest(expected, match.group(2).lower())


def _status(lines: list[Mapping[str, Any]]) -> str:
    totals = [int(line["quantity"]["total"]) for line in lines]
    fulfilled = [int(line["quantity"]["fulfilled"]) for line in lines]
    if lines and all(t == 0 for t in totals):
        return "canceled"
    active = [(t, f) for t, f in zip(totals, fulfilled, strict=True) if t > 0]
    if active and all(f >= t for t, f in active):
        return "shipped"
    if any(f > 0 for _t, f in active):
        return "processing"
    return "confirmed"


def _totals(totals: Any) -> list[dict[str, Any]]:
    out = []
    for total in totals or []:
        if isinstance(total, Mapping) and total.get("type") in _TOTAL_LABELS and isinstance(total.get("amount"), int):
            out.append(
                {"type": total["type"], "display_text": _TOTAL_LABELS[total["type"]], "amount": int(total["amount"])}
            )
    return out


def _line(line: Mapping[str, Any]) -> dict[str, Any]:
    item = line.get("item") or {}
    quantity = line["quantity"]
    out: dict[str, Any] = {
        "id": str(line["id"]),
        "title": str(item.get("title") or item.get("id")),
        "product_id": str(item.get("id")),
        "quantity": {
            "ordered": max(1, int(quantity["original"])),
            "current": int(quantity["total"]),
            "fulfilled": int(quantity["fulfilled"]),
        },
        "status": str(line.get("status") or "processing"),
    }
    if isinstance(item.get("price"), int):
        out["unit_price"] = int(item["price"])
    amounts = {t.get("type"): t.get("amount") for t in line.get("totals") or [] if isinstance(t, Mapping)}
    if isinstance(amounts.get("total"), int):
        out["subtotal"] = int(amounts["total"])
    if isinstance(item.get("image_url"), str) and item["image_url"].startswith("https://"):
        out["image_url"] = item["image_url"]
    return out


def _fulfillments(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    fulfillment = snapshot.get("fulfillment") or {}
    events = list(fulfillment.get("events") or [])
    out = []
    for expectation in fulfillment.get("expectations") or []:
        kind = expectation.get("method_type")
        if kind not in ("shipping", "pickup"):
            continue
        ids = {str(ref.get("id")) for ref in expectation.get("line_items") or []}
        related = [e for e in events if ids & {str(ref.get("id")) for ref in e.get("line_items") or []}]
        entry: dict[str, Any] = {
            "id": str(expectation.get("id")),
            "type": kind,
            "status": "pending",
            "line_items": [
                {"id": str(ref["id"]), "quantity": int(ref["quantity"])}
                for ref in expectation.get("line_items") or []
                if int(ref.get("quantity") or 0) >= 1
            ],
        }
        if expectation.get("description"):
            entry["description"] = str(expectation["description"])
        if related:
            last = related[-1]
            entry["status"] = _FULFILLMENT_STATUS.get(str(last.get("type")), "processing")
            for key in ("carrier", "tracking_number"):
                if last.get(key):
                    entry[key] = str(last[key])
            if str(last.get("tracking_url") or "").startswith("https://"):
                entry["tracking_url"] = last["tracking_url"]
            entry["events"] = [
                {
                    "id": str(e["id"]),
                    "type": _FULFILLMENT_STATUS.get(str(e.get("type")), "processing"),
                    "occurred_at": str(e["occurred_at"]),
                    **({"description": str(e["description"])} if e.get("description") else {}),
                }
                for e in related
            ]
        out.append(entry)
    return out


def _adjustments(snapshot: Mapping[str, Any], currency: str | None) -> list[dict[str, Any]]:
    out = []
    for adjustment in snapshot.get("adjustments") or []:
        entry: dict[str, Any] = {
            "id": str(adjustment["id"]),
            "type": str(adjustment.get("type")),
            "occurred_at": str(adjustment.get("occurred_at")),
            "status": str(adjustment.get("status")),
        }
        refs = [
            {"id": str(ref["id"]), "quantity": abs(int(ref["quantity"]))}
            for ref in adjustment.get("line_items") or []
            if int(ref.get("quantity") or 0) != 0
        ]
        if refs:
            entry["line_items"] = refs
        amounts = [
            abs(int(t["amount"]))
            for t in adjustment.get("totals") or []
            if isinstance(t, Mapping) and t.get("type") == "total" and isinstance(t.get("amount"), int)
        ]
        if amounts:
            entry["amount"] = amounts[0]
            if currency:
                entry["currency"] = currency.lower()
        if adjustment.get("description"):
            entry["description"] = str(adjustment["description"])
        out.append(entry)
    return out


def acp_order(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The full ACP ``Order`` (``EventDataOrder``) of an order snapshot."""
    lines = [_line(line) for line in snapshot.get("line_items") or []]
    currency = snapshot.get("currency")
    order: dict[str, Any] = {
        "type": "order",
        "id": str(snapshot["id"]),
        "checkout_session_id": str(snapshot["checkout_id"]),
        "permalink_url": str(snapshot["permalink_url"]),
        "status": _status(list(snapshot.get("line_items") or [])),
        "line_items": lines,
        "fulfillments": _fulfillments(snapshot),
        "adjustments": _adjustments(snapshot, currency if isinstance(currency, str) else None),
        "totals": _totals(snapshot.get("totals")),
    }
    return order


def acp_order_event(event: OrderEvent) -> dict[str, Any]:
    """The ``WebhookEvent`` body for an ``acp_hmac`` subscription."""
    return {"type": acp_event_type(event), "data": acp_order(event.snapshot)}
