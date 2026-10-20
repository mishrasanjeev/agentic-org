# SPDX-License-Identifier: Apache-2.0
"""The order entity (``dev.ucp.shopping.order``) and its lifecycle events.

A completed checkout produces one order. The stored snapshot is
version-neutral and complete: ``GET /ucp/orders/{id}`` and every order
webhook render the full current state (UCP order, Events: there are no
``order_create``/``order_update`` event types in UCP; the first delivery is
the "order created" snapshot and each change sends the full entity again).

Line item quantities are integer step counts ``{original, total, fulfilled}``
and the line ``status`` is derived from them (``processing``, ``partial``,
``fulfilled``, ``removed``). Fulfillment ``events[]`` and ``adjustments[]`` are
append-only; a ``shipped`` or ``picked_up`` event advances ``fulfilled``; a
completed ``cancellation`` or ``return`` adjustment with negative line
quantities lowers ``total``.

Event hook: ``register_order_event_listener`` receives every order event
(``order.created`` / ``order.updated``) inside the transaction that changed
the order, so a listener (the ACP order event mapper) can enqueue its own
deliveries atomically.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.commerce.ucp.merchant import MerchantUcp

ORDER_CREATED = "order.created"
ORDER_UPDATED = "order.updated"
FULFILLING_EVENTS = frozenset({"shipped", "picked_up", "fulfilled"})
REDUCING_ADJUSTMENTS = frozenset({"cancellation", "return"})


class OrderUpdateError(ValueError):
    """An order change that does not apply (unknown line, bad quantity)."""


class DuplicateOrderEventError(OrderUpdateError):
    """A fulfillment event whose id the order already holds (a redelivered source event)."""


def new_order_id() -> str:
    return f"ord_{uuid.uuid4().hex}"


def order_permalink(merchant: MerchantUcp, order_id: str) -> str:
    template = str(merchant.raw_config.get("provider_policy", {}).get("ucp", {}).get("order_page_url") or "")
    if template.startswith("https://") and "{order_id}" in template:
        return template.replace("{order_id}", order_id)
    return f"{merchant.endpoint}/orders/{order_id}"


def line_status(quantity: Mapping[str, int]) -> str:
    total, fulfilled = int(quantity["total"]), int(quantity["fulfilled"])
    if total == 0:
        return "removed"
    if fulfilled == 0:
        return "processing"
    return "fulfilled" if fulfilled >= total else "partial"


def build_order_snapshot(
    *,
    order_id: str,
    checkout_id: str,
    merchant: MerchantUcp,
    state: Mapping[str, Any],
    accepted_term: Mapping[str, Any] | None,
) -> dict[str, Any]:
    lines = []
    for line in state.get("line_items") or []:
        quantity = {"original": int(line["quantity"]), "total": int(line["quantity"]), "fulfilled": 0}
        lines.append(
            {
                "id": line["id"],
                "item": dict(line["item"]),
                "quantity": quantity,
                "totals": [dict(t) for t in line.get("totals") or []],
                "status": line_status(quantity),
            }
        )
    quantities = {line["id"]: line["quantity"]["total"] for line in lines}
    expectations = []
    for method in (state.get("fulfillment") or {}).get("methods") or []:
        selected = next(
            (d for d in method.get("destinations") or [] if d["id"] == method.get("selected_destination_id")), None
        )
        if selected is None:
            continue
        chosen = [
            option
            for group in method.get("groups") or []
            for option in group.get("options") or []
            if option.get("id") == group.get("selected_option_id")
        ]
        expectations.append(
            {
                "id": f"exp_{method['id']}",
                "description": chosen[0]["title"] if chosen else None,
                "line_items": [
                    {"id": i, "quantity": quantities[i]} for i in method["line_item_ids"] if i in quantities
                ],
                "method_type": method["type"],
                "destination": dict(selected.get("address") or {}),
                "fulfillable_on": "now",
            }
        )
    snapshot: dict[str, Any] = {
        "id": order_id,
        "checkout_id": checkout_id,
        "permalink_url": order_permalink(merchant, order_id),
        "line_items": lines,
        "fulfillment": {"expectations": expectations, "events": []},
        "adjustments": [],
        "currency": state.get("currency"),
        "totals": [dict(t) for t in state.get("totals") or []],
    }
    if state.get("attribution"):
        snapshot["attribution"] = dict(state["attribution"])
    if accepted_term:
        snapshot["accepted_term"] = dict(accepted_term)
    return snapshot


def _line_refs(raw: Any, lines: dict[str, dict[str, Any]], *, signed: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise OrderUpdateError("line_items must be a non-empty array")
    out = []
    for ref in raw:
        if not isinstance(ref, Mapping) or ref.get("id") not in lines:
            raise OrderUpdateError("line_items reference an unknown order line")
        quantity = ref.get("quantity")
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or (quantity < 1 and not signed)
            or quantity == 0
        ):
            raise OrderUpdateError("line item quantities are non-zero integers")
        out.append({"id": ref["id"], "quantity": quantity})
    return out


def apply_fulfillment_event(
    snapshot: dict[str, Any], event: Mapping[str, Any], *, now: datetime, event_id: str | None = None
) -> dict[str, Any]:
    """Append a fulfillment event (``fulfillment_event.json``) and advance fulfilled quantities.

    ``event_id`` (a source-derived id, ``core.commerce.fulfillment_tracking``) makes the append
    idempotent: an id the order already holds raises ``DuplicateOrderEventError``; with it, the
    event's ``occurred_at`` (the source's time, RFC 3339 with an offset) is kept, otherwise ``now``.
    """
    from core.commerce.arbiter import parse_time

    lines = {line["id"]: line for line in snapshot["line_items"]}
    event_type = str(event.get("type") or "")
    if not event_type:
        raise OrderUpdateError("type is required")
    if event_id is not None and any(e.get("id") == event_id for e in snapshot["fulfillment"]["events"]):
        raise DuplicateOrderEventError("the order already holds this fulfillment event")
    refs = _line_refs(event.get("line_items"), lines, signed=False)
    occurred = (parse_time(event.get("occurred_at")) if event_id is not None else None) or now
    entry: dict[str, Any] = {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
        "occurred_at": occurred.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "type": event_type,
        "line_items": refs,
    }
    for key in ("tracking_number", "carrier", "description"):
        if isinstance(event.get(key), str) and event[key]:
            entry[key] = event[key][:500]
    if isinstance(event.get("tracking_url"), str) and event["tracking_url"].startswith("https://"):
        entry["tracking_url"] = event["tracking_url"]
    if event_type in FULFILLING_EVENTS:
        for ref in refs:
            quantity = lines[ref["id"]]["quantity"]
            quantity["fulfilled"] = min(quantity["total"], quantity["fulfilled"] + ref["quantity"])
    snapshot["fulfillment"]["events"].append(entry)
    for line in snapshot["line_items"]:
        line["status"] = line_status(line["quantity"])
    return entry


def apply_adjustment(snapshot: dict[str, Any], adjustment: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """Append an adjustment (``adjustment.json``); a completed cancellation or return lowers line totals."""
    lines = {line["id"]: line for line in snapshot["line_items"]}
    adj_type = str(adjustment.get("type") or "")
    status = str(adjustment.get("status") or "")
    if not adj_type or status not in ("pending", "completed", "failed"):
        raise OrderUpdateError("type and a status of pending, completed or failed are required")
    entry: dict[str, Any] = {
        "id": f"adj_{uuid.uuid4().hex[:16]}",
        "type": adj_type,
        "occurred_at": now.isoformat().replace("+00:00", "Z"),
        "status": status,
    }
    if adjustment.get("line_items") is not None:
        entry["line_items"] = _line_refs(adjustment.get("line_items"), lines, signed=True)
    totals = adjustment.get("totals")
    if isinstance(totals, list):
        clean = []
        for total in totals:
            if not isinstance(total, Mapping) or not isinstance(total.get("type"), str):
                raise OrderUpdateError("totals entries need a type and an integer amount")
            amount = total.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise OrderUpdateError("totals entries need a type and an integer amount")
            clean.append({"type": total["type"], "amount": amount})
        entry["totals"] = clean
    if isinstance(adjustment.get("description"), str):
        entry["description"] = adjustment["description"][:500]
    if adj_type in REDUCING_ADJUSTMENTS and status == "completed":
        for ref in entry.get("line_items") or []:
            quantity = lines[ref["id"]]["quantity"]
            if ref["quantity"] < 0:
                quantity["total"] = max(0, quantity["total"] + ref["quantity"])
                quantity["fulfilled"] = min(quantity["fulfilled"], quantity["total"])
    snapshot["adjustments"].append(entry)
    for line in snapshot["line_items"]:
        line["status"] = line_status(line["quantity"])
    return entry


@dataclass(frozen=True)
class OrderEvent:
    event_type: str
    tenant_id: str
    merchant_id: str
    order_id: str
    revision: int
    snapshot: dict[str, Any]
    ucp_version: str


OrderEventListener = Callable[[Any, OrderEvent], Awaitable[None]]
# enterprise-gate: process-local-ok reason=import-time-listener-registry-identical-on-every-replica
_listeners: list[OrderEventListener] = []


def register_order_event_listener(listener: OrderEventListener) -> None:
    """Called with ``(db_session, OrderEvent)`` in the transaction that created or changed the order."""
    if listener not in _listeners:
        _listeners.append(listener)


def unregister_order_event_listener(listener: OrderEventListener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


async def emit_order_event(session: Any, event: OrderEvent) -> None:
    for listener in list(_listeners):
        await listener(session, event)


def listeners() -> Sequence[OrderEventListener]:
    return tuple(_listeners)
