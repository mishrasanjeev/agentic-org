# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP 2026-08-25 shapes.

Differences from 2026-04-08 handled here: items carry ``quantity_unit`` when
they are not sold each; destinations carry ``type`` (``shipping_address`` or
``business_location``, the latter a location summary); consent is the
``dev.ucp.consent.*`` purpose map; outstanding payment authentication work is
the ``actions`` map (response-only, omitted when empty); payment handlers in
checkout responses carry the ``ucp.request_constraints`` the business will
enforce on each available instrument; payment terms render as
``payment.terms`` and the order keeps ``payment.accepted_term``.
``policies[]`` is left to the policies mapper of the profile work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.commerce.payment_handlers import registered_handlers
from core.commerce.ucp.capabilities import PaymentHandlerEntry
from core.commerce.ucp.consent import render_consent
from core.commerce.ucp.negotiation import Negotiated

VERSION = "2026-08-25"


def item(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": raw["id"], "title": raw["title"], "price": raw["price"]}
    if raw.get("quantity_unit"):
        out["quantity_unit"] = dict(raw["quantity_unit"])
    if raw.get("image_url"):
        out["image_url"] = raw["image_url"]
    return out


def consent(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return render_consent(state, version=VERSION)


def destination(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("kind") == "business_location":
        out: dict[str, Any] = {"type": "business_location", "id": raw["id"], "name": raw["name"]}
        if raw.get("address"):
            out["address"] = dict(raw["address"])
        return out
    return {"type": "shipping_address", "id": raw["id"], **dict(raw.get("address") or {})}


def _option(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": raw["id"], "title": raw["title"], "totals": [{"type": "total", "amount": raw["amount"]}]}
    if raw.get("description"):
        # 2026-08-25 descriptions are description.json objects.
        out["description"] = {"plain": raw["description"]}
    for key in ("carrier", "earliest_fulfillment_time", "latest_fulfillment_time"):
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
    available = []
    for entry in state.get("available_methods") or []:
        rendered = {
            "type": entry["type"],
            "line_item_ids": list(entry["line_item_ids"]),
            "fulfillable_on": entry.get("fulfillable_on"),
        }
        if entry["type"] == "pickup":
            rendered["description"] = "Pick up at a store."
        available.append(rendered)
    return {"methods": methods, "available_methods": available}


def instrument(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": raw["id"], "handler_id": raw["handler_id"], "type": raw["type"]}
    for key in ("display", "billing_address", "selected", "amount"):
        if key in raw:
            out[key] = raw[key]
    return out


def payment_handlers(handlers: Sequence[PaymentHandlerEntry]) -> dict[str, list[dict[str, Any]]]:
    """Handlers for this checkout, each available instrument with the constraints the business enforces."""
    registered = registered_handlers()
    out: dict[str, list[dict[str, Any]]] = {}
    for entry in handlers:
        rendered = entry.render()
        handler = registered.get(entry.name)
        if handler is not None and rendered.get("available_instruments"):
            instruments = []
            for available in rendered["available_instruments"]:
                constraint = handler.request_constraints(entry, str(available.get("type")), VERSION)
                instruments.append(
                    {**available, "ucp": {"request_constraints": constraint}} if constraint else available
                )
            rendered["available_instruments"] = instruments
        out.setdefault(entry.name, []).append(rendered)
    return out


def payment_terms(terms: Sequence[Mapping[str, Any]], selected: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if terms:
        out["terms"] = [dict(t) for t in terms]
    if selected:
        out["selected_term_id"] = selected
    return out


def checkout_extras(out: dict[str, Any], state: Mapping[str, Any], negotiated: Negotiated) -> None:
    actions = state.get("actions")
    if actions:
        out["actions"] = {key: [dict(i) for i in items] for key, items in actions.items() if items}


def expectation(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {
        "id": raw["id"],
        "line_items": [dict(li) for li in raw["line_items"]],
        "method_type": raw["method_type"],
        "destination": dict(raw.get("destination") or {}),
    }
    if raw.get("fulfillable_on"):
        out["fulfillable_on"] = raw["fulfillable_on"]
    if raw.get("description"):
        out["description"] = raw["description"]
    return out


def order_extras(out: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("accepted_term"):
        out["payment"] = {"accepted_term": dict(snapshot["accepted_term"])}


def policies(out: dict[str, Any], items: Sequence[Mapping[str, Any]]) -> None:
    """``policies[]`` from the merchant's verified OACP policy (``map_oacp_policy_to_ucp_policies``)."""
    if items:
        out["policies"] = [dict(item) for item in items]
