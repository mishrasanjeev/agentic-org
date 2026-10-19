# SPDX-License-Identifier: Apache-2.0
"""The checkout and cart state engine: line items, totals, fulfillment, consent, loyalty, payment and status.

Status machine (UCP checkout, Status Values; identical in 2026-04-08 and
2026-08-25):

``incomplete``
    Something blocks completion: an error message (an unknown or out-of-stock
    item, a missing fulfillment selection, ...).
``requires_escalation``
    The buyer must act in a merchant surface; at least one
    ``requires_buyer_input``/``requires_buyer_review`` message and a
    ``continue_url`` are present. Without a ``continue_url`` the session
    stays ``incomplete``.
``ready_for_complete``
    No error: Complete may be called.
``complete_in_progress``
    Complete was accepted and the executor is working (or waits for a
    payment authentication action); Update and Complete are refused, the
    response never carries ``order``.
``completed``
    Terminal; carries ``order{id, permalink_url}``.
``canceled``
    Terminal; reachable from every non-terminal state, and from expiry.

Totals: exactly one ``subtotal`` and one ``total`` (plus ``fulfillment`` when
an option is selected), signed integers in minor units; line totals are exact
(``core.commerce.ucp.quantity``). Business outcomes are messages, never
exceptions; ``RequestError`` is a protocol error (400).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.commerce.ucp import messages as msg
from core.commerce.ucp import versions as v
from core.commerce.ucp.catalog import CatalogItem, ItemCatalog
from core.commerce.ucp.consent import ConsentRequestError, parse_consent
from core.commerce.ucp.fulfillment import FulfillmentRequestError, RatesSource, build_fulfillment
from core.commerce.ucp.loyalty import loyalty_view
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.negotiation import Negotiated
from core.commerce.ucp.payment import PaymentRequestError, parse_payment, payment_terms, validate_instruments
from core.commerce.ucp.quantity import QuantityError, line_amount, parse_quantity, parse_quantity_unit, reconcile
from core.commerce.ucp.shopify_reads import ShopifyReads

TERMINAL = frozenset({"completed", "canceled"})
FROZEN = frozenset({"complete_in_progress", "completed", "canceled"})
MAX_LINE_ITEMS = 100
BUYER_FIELDS = ("first_name", "last_name", "email", "phone_number")
_REVERSE_DNS = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")
_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


class RequestError(ValueError):
    """A malformed request (protocol error, HTTP 400)."""


@dataclass
class EngineDeps:
    catalog: ItemCatalog
    rates_source: RatesSource | None = None
    shopify_reads: ShopifyReads | None = None


@dataclass
class Evaluation:
    state: dict[str, Any]
    status: str
    currency: str | None
    total_minor_units: int
    messages: list[dict[str, Any]] = field(default_factory=list)


def _require_list(body: Mapping[str, Any], name: str) -> list[Any]:
    value = body.get(name)
    if not isinstance(value, list) or not value:
        raise RequestError(f"{name} must be a non-empty array")
    if len(value) > MAX_LINE_ITEMS:
        raise RequestError(f"at most {MAX_LINE_ITEMS} line items")
    return value


async def build_line_items(
    *,
    merchant: MerchantUcp,
    raw_items: Sequence[Any],
    previous: Sequence[Mapping[str, Any]],
    next_seq: int,
    version: str,
    catalog: ItemCatalog,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, int, int]:
    """Priced line items, their messages, the currency, the subtotal and the next line id counter."""
    parsed: list[tuple[str | None, str, int, Any, str | None]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("item"), Mapping):
            raise RequestError(f"line_items[{index}] needs an item object")
        item_id = raw["item"].get("id")
        if not isinstance(item_id, str) or not item_id or len(item_id) > 200:
            raise RequestError(f"line_items[{index}].item.id is required")
        try:
            quantity = parse_quantity(raw.get("quantity"))
        except QuantityError as exc:
            raise RequestError(f"line_items[{index}].quantity: {exc}") from exc
        asserted = raw["item"].get("quantity_unit") if version >= v.V2026_08_25 else None
        line_id = raw.get("id") if isinstance(raw.get("id"), str) else None
        parent = raw.get("parent_id") if isinstance(raw.get("parent_id"), str) else None
        parsed.append((line_id, item_id, quantity, asserted, parent))
    found = await catalog.lookup(merchant, [p[1] for p in parsed])
    previous_ids = {str(item.get("id")) for item in previous}
    used: set[str] = set()
    items: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    currency: str | None = None
    subtotal = 0
    for index, (line_id, item_id, quantity, asserted, parent) in enumerate(parsed):
        path = f"$.line_items[{index}]"
        if line_id and line_id in previous_ids and line_id not in used:
            final_id = line_id
        else:
            final_id = f"li_{next_seq}"
            next_seq += 1
        used.add(final_id)
        item: CatalogItem | None = found.get(item_id)
        line: dict[str, Any] = {"id": final_id, "quantity": quantity}
        if parent and parent in previous_ids:
            line["parent_id"] = parent
        if item is None:
            line["item"] = {"id": item_id, "title": item_id, "price": 0}
            line["totals"] = [{"type": "subtotal", "amount": 0}, {"type": "total", "amount": 0}]
            messages.append(msg.error("not_found", f"Item {item_id} was not found.", path=path))
            items.append(line)
            continue
        if currency is None:
            currency = item.currency
        elif item.currency != currency:
            messages.append(msg.error("item_unavailable", "Items must share one currency.", path=path))
        if asserted is not None:
            try:
                reconciled = reconcile(parse_quantity_unit(asserted), item.quantity_unit, quantity)
            except QuantityError as exc:
                messages.append(msg.error(exc.code, str(exc), path=f"{path}.item.quantity_unit"))
                reconciled = None
            if reconciled is not None:
                quantity = reconciled.quantity
                line["quantity"] = quantity
                if reconciled.warning:
                    messages.append({**reconciled.warning, "path": f"{path}.quantity"})
        if item.quantity_unit.increment > 1 and quantity % item.quantity_unit.increment:
            messages.append(
                msg.warning(
                    "quantity_increment",
                    f"This item is ordered in multiples of {item.quantity_unit.increment}.",
                    path=f"{path}.quantity",
                )
            )
        whole_units = -(-quantity // (10**item.quantity_unit.scale))
        if item.inventory is not None and whole_units > item.inventory:
            messages.append(msg.error("out_of_stock", f"Only {max(item.inventory, 0)} in stock.", path=path))
        amount = line_amount(item.price_minor_units, quantity, item.quantity_unit.scale)
        rendered_item: dict[str, Any] = {"id": item_id, "title": item.title, "price": item.price_minor_units}
        if item.image_url:
            rendered_item["image_url"] = item.image_url
        if not item.quantity_unit.is_each:
            rendered_item["quantity_unit"] = item.quantity_unit.to_dict()
        line["item"] = rendered_item
        line["totals"] = [{"type": "subtotal", "amount": amount}, {"type": "total", "amount": amount}]
        if item.storefront_variant_token:
            line["storefront_variant"] = item.storefront_variant_token
        items.append(line)
        subtotal += amount
    return items, messages, currency, subtotal, next_seq


def parse_buyer(raw: Any, *, version: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Buyer fields and the consent map; the consent is kept apart so it is never mixed into buyer data."""
    if raw is None:
        return None, None
    if not isinstance(raw, Mapping):
        raise RequestError("buyer must be an object")
    buyer = {k: str(raw[k])[:320] for k in BUYER_FIELDS if isinstance(raw.get(k), str) and raw.get(k)}
    try:
        consent = parse_consent(raw.get("consent"), version=version)
    except ConsentRequestError as exc:
        raise RequestError(str(exc)) from exc
    return buyer, consent


def parse_context(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise RequestError("context must be an object")
    out: dict[str, Any] = {}
    for key in ("address_country", "address_region", "postal_code", "intent", "language", "currency"):
        if isinstance(raw.get(key), str):
            out[key] = raw[key][:200]
    eligibility = raw.get("eligibility")
    if isinstance(eligibility, list):
        out["eligibility"] = sorted({e for e in eligibility if isinstance(e, str) and _REVERSE_DNS.match(e)})
    payment = raw.get("payment")
    if isinstance(payment, list):
        out["payment"] = [
            {"handler": p["handler"], "types": [t for t in p.get("types") or [] if isinstance(t, str)]}
            for p in payment
            if isinstance(p, Mapping) and isinstance(p.get("handler"), str)
        ]
    return out


def parse_attribution(raw: Any) -> dict[str, str] | None:
    """A flat string map (informational; never negotiated). Kept as data, never interpreted."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or not all(isinstance(val, str) for val in raw.values()):
        raise RequestError("attribution must map strings to strings")
    return {str(k)[:120]: str(val)[:500] for k, val in list(raw.items())[:50]}


def signal_summary(raw: Any) -> dict[str, Any] | None:
    """Signals are platform-observed risk inputs: kept only as keys and a digest, never echoed."""
    from core.commerce.ledger import commerce_payload_hash

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise RequestError("signals must be an object")
    return {"keys": sorted(str(k) for k in raw), "sha256": commerce_payload_hash(dict(raw))}


def compute_totals(subtotal: int, fulfillment_amount: int | None) -> list[dict[str, Any]]:
    totals = [{"type": "subtotal", "amount": subtotal}]
    total = subtotal
    if fulfillment_amount is not None:
        totals.append({"type": "fulfillment", "amount": fulfillment_amount})
        total += fulfillment_amount
    totals.append({"type": "total", "amount": total})
    return totals


def total_of(totals: Sequence[Mapping[str, Any]]) -> int:
    return next(int(t["amount"]) for t in totals if t.get("type") == "total")


def status_for(messages: Sequence[Mapping[str, Any]], continue_url: str | None) -> str:
    if msg.escalates(list(messages)) and continue_url:
        return "requires_escalation"
    if msg.has_errors(list(messages)):
        return "incomplete"
    return "ready_for_complete"


def permalink_url(merchant: MerchantUcp, line_items: Sequence[Mapping[str, Any]]) -> str | None:
    """A UCP permalink (``<endpoint>/<item_token>:<qty>,...``) on the merchant's permalink endpoint."""
    if not merchant.permalink_endpoint or not line_items:
        return None
    import base64

    parts = []
    for line in line_items:
        item_id = str((line.get("item") or {}).get("id") or "")
        token = (
            item_id if _TOKEN.match(item_id) else "~" + base64.urlsafe_b64encode(item_id.encode()).decode().rstrip("=")
        )
        parts.append(f"{token}:{int(line.get('quantity') or 1)}")
    return f"{merchant.permalink_endpoint}/{','.join(parts)}"


async def evaluate(
    *,
    resource: str,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    body: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    deps: EngineDeps,
    now: datetime,
) -> Evaluation:
    """Build the full state of a checkout (or cart) from a create or update body (full replacement)."""
    version = negotiated.version
    prior = previous or {}
    raw_items = _require_list(body, "line_items")
    items, messages, currency, subtotal, next_seq = await build_line_items(
        merchant=merchant,
        raw_items=raw_items,
        previous=prior.get("line_items") or [],
        next_seq=int(prior.get("next_line_seq") or 1),
        version=version,
        catalog=deps.catalog,
    )
    buyer, consent = parse_buyer(body.get("buyer"), version=version)
    context = parse_context(body.get("context"))
    if consent is not None and not negotiated.has(v.BUYER_CONSENT):
        consent = None
    state: dict[str, Any] = {
        "line_items": items,
        "next_line_seq": next_seq,
        "buyer": buyer,
        "consent": consent,
        "context": context,
        "attribution": parse_attribution(body.get("attribution")),
        "signals": signal_summary(body.get("signals")),
        "currency": currency,
        "capabilities": sorted(negotiated.active),
        "ucp_version": version,
    }
    fulfillment_amount: int | None = None
    if resource == "checkout" and negotiated.has(v.FULFILLMENT):
        try:
            fulfillment = await build_fulfillment(
                merchant=merchant,
                request=body.get("fulfillment"),
                previous=prior.get("fulfillment"),
                line_item_ids=[i["id"] for i in items],
                currency=currency,
                subtotal_minor_units=subtotal,
                rates_source=deps.rates_source,
            )
        except FulfillmentRequestError as exc:
            raise RequestError(str(exc)) from exc
        state["fulfillment"] = fulfillment.state
        messages.extend(fulfillment.messages)
        if any(g.get("selected_option_id") for m in fulfillment.state["methods"] for g in m["groups"]):
            fulfillment_amount = fulfillment.amount_minor_units
    if resource == "checkout":
        try:
            instruments, term_id = parse_payment(body.get("payment"))
        except PaymentRequestError as exc:
            raise RequestError(str(exc)) from exc
        messages.extend(validate_instruments(instruments, merchant=merchant, version=version))
        state["payment"] = {"instruments": [i.stored() for i in instruments]}
        if negotiated.has(v.PAYMENT_TERMS):
            known = {str(t.get("id")) for t in merchant.payment_terms}
            if term_id and term_id not in known:
                messages.append(msg.error("invalid_payment_term", "Unknown payment term.", path="$.payment"))
                term_id = None
            state["payment"]["selected_term_id"] = term_id
    totals = compute_totals(subtotal, fulfillment_amount)
    state["totals"] = totals
    if resource == "checkout" and negotiated.has(v.PAYMENT_TERMS):
        state["payment_terms"] = payment_terms(merchant, total_of(totals), now)
    if negotiated.has(v.LOYALTY):
        state["loyalty"] = await loyalty_view(
            merchant=merchant,
            reads=deps.shopify_reads,
            email=(buyer or {}).get("email"),
            eligibility=(context or {}).get("eligibility") or [],
            total_minor_units=total_of(totals),
            currency=currency,
        )
    state["messages"] = messages
    state["permalink_url"] = permalink_url(merchant, items)
    status = status_for(messages, None)
    return Evaluation(
        state=state, status=status, currency=currency, total_minor_units=total_of(totals), messages=messages
    )
