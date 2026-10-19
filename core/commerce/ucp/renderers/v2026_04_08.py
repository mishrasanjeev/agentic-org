# SPDX-License-Identifier: Apache-2.0
"""UCP 2026-04-08 shapes.

Differences from 2026-08-25 handled here: items have no ``quantity_unit`` or
``unit_price``; shipping destinations are a postal address plus ``id`` and
pickup destinations are ``retail_location`` (``{id, name, address}``);
fulfillment method ``type`` is the enum ``shipping``/``pickup``; buyer consent
is four booleans; there is no ``actions`` map, no ``policies[]``, no
``ucp.request_constraints`` and no payment terms. A payment authentication
step is an escalation (``requires_escalation`` with a ``requires_3ds``
message and ``continue_url``), which the session state already carries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.commerce.ucp.consent import render_consent
from core.commerce.ucp.negotiation import Negotiated
from core.commerce.ucp.registry import PaymentHandlerEntry

VERSION = "2026-04-08"


def item(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": raw["id"], "title": raw["title"], "price": raw["price"]}
    if raw.get("image_url"):
        out["image_url"] = raw["image_url"]
    return out


def consent(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return render_consent(state, version=VERSION)


def destination(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("kind") == "business_location":
        out: dict[str, Any] = {"id": raw["id"], "name": raw["name"]}
        if raw.get("address"):
            out["address"] = dict(raw["address"])
        return out
    return {"id": raw["id"], **dict(raw.get("address") or {})}


def _option(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": raw["id"], "title": raw["title"], "totals": [{"type": "total", "amount": raw["amount"]}]}
    for key in ("description", "carrier", "earliest_fulfillment_time", "latest_fulfillment_time"):
        if raw.get(key):
            out[key] = raw[key]
    return out


def fulfillment(state: Mapping[str, Any], _lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    methods = []
    for method in state.get("methods") or []:
        methods.append(
            {
                "id": method["id"],
                "type": method["type"],
                "line_item_ids": list(method["line_item_ids"]),
                "destinations": [destination(d) for d in method.get("destinations") or []],
                "selected_destination_id": method.get("selected_destination_id"),
                "groups": [
                    {
                        "id": group["id"],
                        "line_item_ids": list(group["line_item_ids"]),
                        "options": [_option(o) for o in group.get("options") or []],
                        "selected_option_id": group.get("selected_option_id"),
                    }
                    for group in method.get("groups") or []
                ],
            }
        )
    available = [
        {"type": a["type"], "line_item_ids": list(a["line_item_ids"]), "fulfillable_on": a.get("fulfillable_on")}
        for a in state.get("available_methods") or []
    ]
    return {"methods": methods, "available_methods": available}


def instrument(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": raw["id"], "handler_id": raw["handler_id"], "type": raw["type"]}
    for key in ("display", "billing_address", "selected"):
        if key in raw:
            out[key] = raw[key]
    return out


def payment_handlers(handlers: Sequence[PaymentHandlerEntry]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for handler in handlers:
        out.setdefault(handler.name, []).append(handler.render())
    return out


def payment_terms(_terms: Sequence[Mapping[str, Any]], _selected: str | None) -> dict[str, Any]:
    return {}


def checkout_extras(out: dict[str, Any], state: Mapping[str, Any], negotiated: Negotiated) -> None:
    return None


def expectation(raw: Mapping[str, Any]) -> dict[str, Any]:
    method_type = raw["method_type"] if raw["method_type"] in ("shipping", "pickup", "digital") else "shipping"
    out = {
        "id": raw["id"],
        "line_items": [dict(li) for li in raw["line_items"]],
        "method_type": method_type,
        "destination": dict(raw.get("destination") or {}),
    }
    if raw.get("fulfillable_on"):
        out["fulfillable_on"] = raw["fulfillable_on"]
    if raw.get("description"):
        out["description"] = raw["description"]
    return out


def order_extras(out: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
    return None
