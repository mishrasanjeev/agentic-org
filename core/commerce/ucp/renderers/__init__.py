# SPDX-License-Identifier: Apache-2.0
"""Render version-neutral state as UCP JSON for one release.

The shared builders here assemble checkout, cart and order resources; every
shape that differs between releases (item fields, destinations, consent,
actions, policies, request constraints, payment terms) is delegated to the
release's module: ``v2026_04_08`` or ``v2026_08_25``. Credentials and signals
are never rendered: the state does not hold them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.commerce.ucp import releases as v
from core.commerce.ucp.capabilities import PaymentHandlerEntry
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.negotiation import Negotiated
from core.commerce.ucp.renderers import v2026_04_08, v2026_08_25


def release(version: str) -> Any:
    return v2026_08_25 if version >= v.V2026_08_25 else v2026_04_08


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SessionView:
    id: str
    status: str
    expires_at: datetime
    continue_url: str | None = None
    order_id: str | None = None
    order_permalink_url: str | None = None


def _line_items(lines: Sequence[Mapping[str, Any]], version: str) -> list[dict[str, Any]]:
    mod = release(version)
    out = []
    for line in lines:
        rendered = {
            "id": line["id"],
            "item": mod.item(line["item"]),
            "quantity": line["quantity"],
            "totals": [dict(t) for t in line.get("totals") or []],
        }
        if line.get("parent_id"):
            rendered["parent_id"] = line["parent_id"]
        out.append(rendered)
    return out


def _ucp_block(
    negotiated: Negotiated, resource: str, handlers: Sequence[PaymentHandlerEntry] | None, version: str
) -> dict[str, Any]:
    block: dict[str, Any] = {"version": version, "capabilities": negotiated.response_capabilities(resource)}
    if handlers is not None:
        block["payment_handlers"] = release(version).payment_handlers(handlers)
    return block


def _links(merchant: MerchantUcp) -> list[dict[str, Any]]:
    return [dict(link) for link in merchant.links]


def render_checkout(
    *,
    session: SessionView,
    state: Mapping[str, Any],
    negotiated: Negotiated,
    merchant: MerchantUcp,
    handlers: Sequence[PaymentHandlerEntry],
    extra_messages: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    version = negotiated.version
    mod = release(version)
    out: dict[str, Any] = {
        "ucp": _ucp_block(negotiated, "checkout", handlers, version),
        "id": session.id,
        "line_items": _line_items(state.get("line_items") or [], version),
        "status": session.status,
        "currency": state.get("currency") or merchant.currency or "USD",
        "totals": [dict(t) for t in state.get("totals") or []],
        "links": _links(merchant),
        "expires_at": iso(session.expires_at),
    }
    buyer = dict(state.get("buyer") or {})
    if negotiated.has(v.BUYER_CONSENT):
        consent = mod.consent(state.get("consent"))
        if consent:
            buyer["consent"] = consent
    if buyer:
        out["buyer"] = buyer
    if state.get("context"):
        out["context"] = dict(state["context"])
    if state.get("attribution"):
        out["attribution"] = dict(state["attribution"])
    messages = [dict(m) for m in (state.get("messages") or [])] + [dict(m) for m in extra_messages]
    if messages:
        out["messages"] = messages
    if session.continue_url:
        out["continue_url"] = session.continue_url
    if negotiated.has(v.FULFILLMENT) and state.get("fulfillment") is not None:
        out["fulfillment"] = mod.fulfillment(state["fulfillment"], state.get("line_items") or [])
    payment = state.get("payment") or {}
    rendered_payment: dict[str, Any] = {"instruments": [mod.instrument(i) for i in payment.get("instruments") or []]}
    if version >= v.V2026_08_25 and negotiated.has(v.PAYMENT_TERMS):
        rendered_payment.update(mod.payment_terms(state.get("payment_terms") or [], payment.get("selected_term_id")))
    out["payment"] = rendered_payment
    if negotiated.has(v.LOYALTY) and state.get("loyalty"):
        out["loyalty"] = dict(state["loyalty"])
    mod.checkout_extras(out, state, negotiated)
    if session.status == "completed" and session.order_id:
        out["order"] = {"id": session.order_id, "permalink_url": session.order_permalink_url}
    return out


def render_cart(
    *,
    cart_id: str,
    state: Mapping[str, Any],
    negotiated: Negotiated,
    merchant: MerchantUcp,
    expires_at: datetime,
) -> dict[str, Any]:
    version = negotiated.version
    mod = release(version)
    out: dict[str, Any] = {
        "ucp": _ucp_block(negotiated, "cart", None, version),
        "id": cart_id,
        "line_items": _line_items(state.get("line_items") or [], version),
        "currency": state.get("currency") or merchant.currency or "USD",
        "totals": [dict(t) for t in state.get("totals") or []],
        "expires_at": iso(expires_at),
    }
    links = _links(merchant)
    if links:
        out["links"] = links
    buyer = dict(state.get("buyer") or {})
    if version >= v.V2026_08_25 and negotiated.has(v.BUYER_CONSENT):
        consent = mod.consent(state.get("consent"))
        if consent:
            buyer["consent"] = consent
    if buyer:
        out["buyer"] = buyer
    for name in ("context", "attribution"):
        if state.get(name):
            out[name] = dict(state[name])
    if state.get("messages"):
        out["messages"] = [dict(m) for m in state["messages"]]
    if state.get("permalink_url"):
        out["continue_url"] = state["permalink_url"]
    if negotiated.has(v.LOYALTY) and state.get("loyalty"):
        out["loyalty"] = dict(state["loyalty"])
    return out


def render_order(*, snapshot: Mapping[str, Any], version: str, capabilities: Mapping[str, Any]) -> dict[str, Any]:
    """The full order entity (``order.json``); ``snapshot`` is the stored version-neutral order."""
    mod = release(version)
    out: dict[str, Any] = {
        "ucp": {"version": version, "capabilities": dict(capabilities)},
        "id": snapshot["id"],
        "checkout_id": snapshot["checkout_id"],
        "permalink_url": snapshot["permalink_url"],
        "line_items": [
            {
                "id": line["id"],
                "item": mod.item(line["item"]),
                "quantity": dict(line["quantity"]),
                "totals": [dict(t) for t in line.get("totals") or []],
                "status": line["status"],
            }
            for line in snapshot.get("line_items") or []
        ],
        "fulfillment": {
            "expectations": [mod.expectation(e) for e in (snapshot.get("fulfillment") or {}).get("expectations") or []],
            "events": [dict(e) for e in (snapshot.get("fulfillment") or {}).get("events") or []],
        },
        "adjustments": [dict(a) for a in snapshot.get("adjustments") or []],
        "currency": snapshot["currency"],
        "totals": [dict(t) for t in snapshot.get("totals") or []],
    }
    if snapshot.get("label"):
        out["label"] = snapshot["label"]
    if snapshot.get("attribution"):
        out["attribution"] = dict(snapshot["attribution"])
    mod.order_extras(out, snapshot)
    return out
