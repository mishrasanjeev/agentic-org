# SPDX-License-Identifier: Apache-2.0
"""Tracking normaliser: Shopify fulfillment and fulfillment-event payloads to UCP order fulfillment events.

Pure: no I/O, no clock. Input is a Shopify Admin ``Fulfillment`` object
(webhook topics ``fulfillments/create`` and ``fulfillments/update``) or a
``FulfillmentEvent`` (``fulfillment_events/create``) as the merchant's Shopify
integration delivers it, plus the UCP order snapshot it belongs to. Output is
one UCP ``fulfillment_event`` (``shopping/types/fulfillment_event.json``) for
``core.commerce.ucp.orders.apply_fulfillment_event``, or None when the payload
carries no status worth an event.

Status: Shopify's shipment statuses map onto the UCP open ``type`` values
(``STATUS_MAP``); a fulfillment created with status ``success`` is
``shipped`` (the only type, with ``picked_up`` for pickups, that advances the
order line's ``fulfilled`` quantity), and a cancelled fulfillment is
``canceled``. Carrier pick-up is ``in_transit``, so it never counts the same
shipment twice.

Carrier: the display name with whitespace collapsed, then the merchant's alias
table (``provider_policy.ucp.carrier_aliases``: ``{"any spelling": "Canonical
Name"}``, matched case-insensitively on a slug), so one carrier reads the same
on every event. No carrier list is built in.

Idempotency: the event id is derived from the Shopify fulfillment id, the
event id (if any) and the Shopify status, so a redelivered webhook yields the
same id and the order refuses it as a duplicate.

Lines: Shopify line items are matched to the order's lines by SKU or variant
id (the numeric part of a Shopify variant gid); a line that does not match
makes the payload unusable (``TrackingNormalisationError``), never a guess.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from core.commerce.arbiter import parse_time

STATUS_MAP: dict[str, str] = {
    "label_printed": "processing",
    "label_purchased": "processing",
    "confirmed": "processing",
    "ready_for_pickup": "processing",
    "carrier_picked_up": "in_transit",
    "in_transit": "in_transit",
    "out_for_delivery": "in_transit",
    "delayed": "in_transit",
    "attempted_delivery": "failed_attempt",
    "delivered": "delivered",
    "picked_up": "picked_up",
    "failure": "undeliverable",
}
DESCRIPTIONS: dict[str, str] = {
    "ready_for_pickup": "Ready for pickup",
    "out_for_delivery": "Out for delivery",
    "delayed": "Delayed",
    "carrier_picked_up": "Picked up by the carrier",
}
_SLUG = re.compile(r"[^a-z0-9]+")


class TrackingNormalisationError(ValueError):
    """A Shopify payload that cannot be mapped onto the order (unknown lines, no fulfillment id)."""


@dataclass(frozen=True)
class NormalisedEvent:
    event_id: str
    event: dict[str, Any]
    source_status: str


def carrier_slug(name: str) -> str:
    return _SLUG.sub("_", name.strip().lower()).strip("_")


def normalise_carrier(name: Any, aliases: Mapping[str, str] | None = None) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    display = " ".join(name.split())[:120]
    table = {carrier_slug(str(k)): str(v) for k, v in (aliases or {}).items() if isinstance(v, str) and v.strip()}
    return " ".join(table.get(carrier_slug(display), display).split())[:120]


def _first(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Sequence) and not isinstance(value, str):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def _line_keys(line: Mapping[str, Any]) -> set[str]:
    item = line.get("item") if isinstance(line.get("item"), Mapping) else {}
    keys = {str(item.get("id") or ""), str(line.get("id") or "")}
    return {k for k in keys if k}


def _match_lines(order: Mapping[str, Any], shopify_lines: Any) -> list[dict[str, Any]]:
    lines = [line for line in order.get("line_items") or [] if isinstance(line, Mapping)]
    if not isinstance(shopify_lines, list) or not shopify_lines:
        # A fulfillment with no line items covers the whole order (Shopify fulfills all open lines).
        return [{"id": line["id"], "quantity": int(line["quantity"]["total"])} for line in lines
                if int(line["quantity"]["total"]) > 0]
    out: dict[str, int] = {}
    for raw in shopify_lines:
        if not isinstance(raw, Mapping):
            raise TrackingNormalisationError("line_items entries must be objects")
        candidates = {str(raw.get("sku") or "")}
        variant = str(raw.get("variant_id") or "")
        if variant:
            candidates |= {variant, variant.rsplit("/", 1)[-1]}
        candidates.discard("")
        match = next((line for line in lines if _line_keys(line) & candidates), None)
        quantity = raw.get("quantity")
        if match is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise TrackingNormalisationError("a Shopify line item does not match an order line")
        out[match["id"]] = out.get(match["id"], 0) + quantity
    return [{"id": line_id, "quantity": quantity} for line_id, quantity in out.items()]


def normalise_shopify_fulfillment(
    payload: Mapping[str, Any],
    order: Mapping[str, Any],
    *,
    carrier_aliases: Mapping[str, str] | None = None,
) -> NormalisedEvent | None:
    """One UCP fulfillment event from a Shopify fulfillment (or fulfillment event) payload, or None."""
    fulfillment_id = str(payload.get("fulfillment_id") or payload.get("id") or "")
    is_event = "fulfillment_id" in payload
    if not fulfillment_id:
        raise TrackingNormalisationError("the payload has no fulfillment id")
    if is_event:
        source_status = str(payload.get("status") or "").lower()
    else:
        fulfillment_status = str(payload.get("status") or "").lower()
        shipment = str(payload.get("shipment_status") or "").lower()
        if fulfillment_status in ("cancelled", "canceled"):
            source_status = "cancelled"
        elif shipment:
            source_status = shipment
        elif fulfillment_status == "success":
            source_status = "fulfilled"
        else:
            return None
    if source_status == "cancelled":
        event_type = "canceled"
    elif source_status == "fulfilled":
        event_type = "shipped"
    else:
        mapped = STATUS_MAP.get(source_status)
        if mapped is None:
            return None
        event_type = mapped
    line_items = _match_lines(order, payload.get("line_items"))
    if not line_items:
        return None
    event: dict[str, Any] = {"type": event_type, "line_items": line_items}
    tracking_number = _first(payload.get("tracking_numbers")) or _first(payload.get("tracking_number"))
    tracking_url = _first(payload.get("tracking_urls")) or _first(payload.get("tracking_url"))
    carrier = normalise_carrier(payload.get("tracking_company") or payload.get("company"), carrier_aliases)
    if tracking_number:
        event["tracking_number"] = tracking_number[:200]
    if tracking_url and tracking_url.startswith("https://"):
        event["tracking_url"] = tracking_url
    if carrier:
        event["carrier"] = carrier
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        event["description"] = " ".join(message.split())[:500]
    elif source_status in DESCRIPTIONS:
        event["description"] = DESCRIPTIONS[source_status]
    occurred = parse_time(payload.get("happened_at") or payload.get("updated_at") or payload.get("created_at"))
    if occurred is not None:
        event["occurred_at"] = occurred.astimezone(UTC).isoformat().replace("+00:00", "Z")
    source = f"shopify|{fulfillment_id}|{payload.get('id') if is_event else ''}|{source_status}"
    event_id = "evt_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return NormalisedEvent(event_id=event_id, event=event, source_status=source_status)
