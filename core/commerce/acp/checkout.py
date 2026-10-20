# SPDX-License-Identifier: Apache-2.0
"""ACP checkout sessions over the UCP session table and state engine (G34, G35, G38, G40).

ACP 2026-04-17 (``openapi.agentic_checkout.yaml``, ``schema.agentic_checkout.json``):
``POST /checkout_sessions`` (201), ``POST /checkout_sessions/{id}`` (partial update),
``GET /checkout_sessions/{id}``, ``POST /checkout_sessions/{id}/complete`` and
``POST /checkout_sessions/{id}/cancel`` (405 when the session can no longer be
canceled). Every success answers the full ``CheckoutSession``.

There is one state machine. An ACP request is translated into the UCP checkout
request shape and run by the UCP operations (``core.commerce.ucp.handlers``) on the
same ``commerce_checkout_sessions`` rows, with the same state engine
(``checkout.evaluate``), idempotency records, completion payload, commerce arbiter
decision, executor and order entity; this module only translates in and renders out:

* line items: ACP ``Item`` has no quantity in 2026-04-17, so one entry is one unit and
  a repeated ``id`` adds a unit to the same line (premise correction: the program
  assumed ``{id, quantity}``, the schema of 2026-01-16 and earlier);
* an update is partial: the members present replace the stored ACP input, and the
  merged input becomes a full UCP replacement under the session lock;
* ``fulfillment_details.address`` becomes a shipping destination and a
  ``shipping`` ``selected_fulfillment_options`` entry the selected rate (pickup is
  not offered over ACP: ``FulfillmentOptionPickup`` needs a full store address);
* ``payment_data`` becomes the one payment instrument; ``risk_signals`` become the
  signals sealed on the ledger; the vendor extension carries buyer authority, a
  ``decision_id`` or ``decide_only``, and the Commerce Passport;
* statuses: ``incomplete`` is ``not_ready_for_payment``, ``ready_for_complete`` is
  ``ready_for_payment`` (unless an ACP check blocks it), an expired session is
  ``expired``, the rest keep their names.

Sessions are tagged ``protocol: acp`` and owned by the API key that created them; a UCP
route never sees them and another platform gets 404.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import structlog
from jsonschema import Draft202012Validator

from core.commerce.acp import API_VERSION
from core.commerce.acp import capabilities as caps
from core.commerce.acp import delegate_payment as dp
from core.commerce.acp import headers as hdr
from core.commerce.acp.errors import acp_error, map_message, protocol_error
from core.commerce.grantex_service import GrantexServiceClient
from core.commerce.ledger import commerce_payload_hash, record_commerce_event_in_own_transaction
from core.commerce.ucp import handlers, idempotency
from core.commerce.ucp import releases as v
from core.commerce.ucp.capabilities import RegistryContext
from core.commerce.ucp.completion import VENDOR_MEMBER
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.negotiation import Negotiated, PlatformProfile

logger = structlog.get_logger()

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / API_VERSION
# The UCP release whose request shape and engine semantics ACP sessions run on.
ENGINE_RELEASE = v.V2026_04_08
OPERATIONS: dict[str, str] = {
    "create": "create_checkout",
    "get": "get_checkout",
    "update": "update_checkout",
    "complete": "complete_checkout",
    "cancel": "cancel_checkout",
}
EVENTS: dict[str, str] = {
    "create_checkout": "commerce.acp.checkout.create",
    "update_checkout": "commerce.acp.checkout.update",
    "complete_checkout": "commerce.acp.checkout.complete",
    "cancel_checkout": "commerce.acp.checkout.cancel",
    "execution": "commerce.acp.checkout.execution",
    "order_created": "commerce.acp.order.created",
    "risk_signals": "commerce.acp.payment.risk_signals",
}
ALLOWANCE_EVENT = "commerce.acp.delegate_payment.allowance"
REQUEST_SCHEMAS = {
    "create": "CheckoutSessionCreateRequest",
    "update": "CheckoutSessionUpdateRequest",
    "complete": "CheckoutSessionCompleteRequest",
    "cancel": "CancelSessionRequest",
}
MAX_BODY_BYTES = 256 * 1024
MAX_REQUEST_ITEMS = 500
INPUT_FIELDS = (
    "line_items",
    "currency",
    "buyer",
    "fulfillment_details",
    "selected_fulfillment_options",
    "metadata",
    "locale",
    "timezone",
    "order_notes",
)
UPDATE_FIELDS = ("line_items", "buyer", "fulfillment_details", "selected_fulfillment_options", "order_notes")
STATUS_MAP = {
    "incomplete": "not_ready_for_payment",
    "requires_escalation": "requires_escalation",
    "ready_for_complete": "ready_for_payment",
    "complete_in_progress": "complete_in_progress",
    "completed": "completed",
    "canceled": "canceled",
}
_LINK_TYPES = {
    "terms_of_service": "terms_of_use",
    "terms_of_use": "terms_of_use",
    "privacy_policy": "privacy_policy",
    "refund_policy": "return_policy",
    "return_policy": "return_policy",
    "shipping_policy": "shipping_policy",
    "contact_us": "contact_us",
    "about_us": "about_us",
    "faq": "faq",
    "support": "support",
}
_TOTAL_LABELS = {"subtotal": "Subtotal", "fulfillment": "Shipping", "total": "Total"}
VENDOR_KEYS = ("buyer_authority", "decision_id", "decide_only", "passport_jwt")


@cache
def checkout_schema() -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / "schema.agentic_checkout.json").read_text(encoding="utf-8"))


@cache
def _validator(definition: str) -> Draft202012Validator:
    schema = checkout_schema()
    return Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _json_path(parts: Sequence[Any]) -> str:
    out = "$"
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def request_problem(definition: str, document: Any) -> tuple[str, str] | None:
    """The first schema violation of a request (message, JSONPath), or None."""
    errors = sorted(_validator(definition).iter_errors(document), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    first = errors[0]
    return first.message[:300], _json_path(list(first.absolute_path))


# --- requests and responses -------------------------------------------------------------------


@dataclass
class AcpRequest:
    operation: str
    tenant_id: str
    host: str
    platform: str
    method: str
    path: str
    raw_body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    session_id: str | None = None


@dataclass
class AcpResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    outcome: str = "success"
    recorded: bool = False
    merchant_id: str | None = None
    seller_agent_id: str | None = None
    resource_id: str | None = None


@dataclass
class AcpRuntime:
    """The UCP runtime the sessions run on plus the ACP-only dependencies (tests replace them)."""

    ucp: handlers.UcpRuntime
    grantex: Callable[[], GrantexServiceClient | None] = lambda: None
    signature_mode: Callable[[], str] = lambda: "log"


class _RefusalError(Exception):
    def __init__(self, status: int, error: dict[str, Any], headers: Mapping[str, str] | None = None) -> None:
        super().__init__(error.get("code"))
        self.status = status
        self.error = error
        self.headers = dict(headers or {})


# --- translation in -------------------------------------------------------------------------------


def merchant_accepts_acp(merchant: MerchantUcp) -> bool:
    """The merchant opted in: ``provider_policy.acp.checkout_enabled`` is true."""
    policy = merchant.raw_config.get("provider_policy") if isinstance(merchant.raw_config, Mapping) else None
    acp = policy.get("acp") if isinstance(policy, Mapping) else None
    return isinstance(acp, Mapping) and acp.get("checkout_enabled") is True


def _vendor(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.pop(caps.VENDOR_FIELD, None)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _RefusalError(
            400, acp_error("invalid_request", "invalid", "must be an object", param="$.agenticorg_commerce")
        )
    unknown = sorted(set(raw) - set(VENDOR_KEYS))
    if unknown:
        raise _RefusalError(
            400,
            acp_error("invalid_request", "invalid", "unknown member", param=f"$.agenticorg_commerce.{unknown[0]}"),
        )
    passport = raw.get("passport_jwt")
    if passport is not None and (not isinstance(passport, str) or not 1 <= len(passport) <= dp.MAX_PASSPORT_LENGTH):
        raise _RefusalError(
            400,
            acp_error(
                "invalid_request", "invalid", "must be a compact JWT", param="$.agenticorg_commerce.passport_jwt"
            ),
        )
    return dict(raw)


def _ucp_vendor(vendor: Mapping[str, Any]) -> dict[str, Any] | None:
    out = {k: vendor[k] for k in ("buyer_authority", "decision_id", "decide_only") if k in vendor}
    return out or None


def _ucp_lines(items: Sequence[Mapping[str, Any]], previous: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """ACP items (no quantity) as UCP lines: a repeated id is one more unit on the same line."""
    line_ids = {str((line.get("item") or {}).get("id")): str(line.get("id")) for line in previous}
    counts: dict[str, int] = {}
    for item in items:
        item_id = str(item.get("id"))
        counts[item_id] = counts.get(item_id, 0) + 1
    out = []
    for item_id, quantity in counts.items():
        line: dict[str, Any] = {"item": {"id": item_id}, "quantity": quantity}
        if item_id in line_ids:
            line["id"] = line_ids[item_id]
        out.append(line)
    return out


def _ucp_fulfillment(acp_input: Mapping[str, Any]) -> dict[str, Any] | None:
    details = acp_input.get("fulfillment_details") or {}
    address = details.get("address") if isinstance(details, Mapping) else None
    if not isinstance(address, Mapping):
        return None
    destination = {
        "id": "dest_1",
        "street_address": address.get("line_one"),
        "extended_address": address.get("line_two"),
        "address_locality": address.get("city"),
        "address_region": address.get("state"),
        "postal_code": address.get("postal_code"),
        "address_country": address.get("country"),
    }
    method: dict[str, Any] = {
        "type": "shipping",
        "destinations": [{k: val for k, val in destination.items() if val}],
        "selected_destination_id": "dest_1",
    }
    selected = next(
        (
            option.get("option_id")
            for option in acp_input.get("selected_fulfillment_options") or []
            if isinstance(option, Mapping) and option.get("type") == "shipping"
        ),
        None,
    )
    if selected:
        method["groups"] = [{"selected_option_id": selected}]
    return {"methods": [method]}


def _ucp_buyer(buyer: Any) -> dict[str, Any] | None:
    if not isinstance(buyer, Mapping):
        return None
    return {k: buyer[k] for k in ("first_name", "last_name", "email", "phone_number") if isinstance(buyer.get(k), str)}


def make_translator(*, vendor: Mapping[str, Any], delegate_state: Mapping[str, Any] | None) -> handlers.BodyTranslator:
    """The UCP body for a create (no previous state) or a partial update, and the ACP state to keep."""

    def _translate(previous: Mapping[str, Any] | None, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        prior = dict((previous or {}).get("acp") or {})
        if previous is None:
            acp_input = {k: body[k] for k in INPUT_FIELDS if k in body}
            requested = caps.requested_extensions(body.get("capabilities"))
        else:
            acp_input = dict(prior.get("input") or {})
            for name in UPDATE_FIELDS:
                if name in body:
                    acp_input[name] = body[name]
            requested = list(prior.get("extensions") or [])
        ucp_body: dict[str, Any] = {
            "line_items": _ucp_lines(acp_input.get("line_items") or [], (previous or {}).get("line_items") or []),
        }
        buyer = _ucp_buyer(acp_input.get("buyer"))
        if buyer:
            ucp_body["buyer"] = buyer
        fulfillment = _ucp_fulfillment(acp_input)
        if fulfillment is not None:
            ucp_body["fulfillment"] = fulfillment
        vendor_member = _ucp_vendor(vendor)
        if vendor_member:
            ucp_body[VENDOR_MEMBER] = vendor_member
        kept = {
            "acp": {
                "input": acp_input,
                "extensions": requested,
                "delegate_payment": dict(delegate_state) if delegate_state else prior.get("delegate_payment"),
            }
        }
        return ucp_body, kept

    return _translate


def complete_body(body: Mapping[str, Any], vendor: Mapping[str, Any]) -> dict[str, Any]:
    """An ACP complete request as the UCP complete body (one instrument, signals, vendor member)."""
    out: dict[str, Any] = {}
    payment = body.get("payment_data") if isinstance(body.get("payment_data"), Mapping) else {}
    instrument = payment.get("instrument") if isinstance(payment.get("instrument"), Mapping) else None
    if payment.get("handler_id") and instrument is not None:
        entry: dict[str, Any] = {
            "id": "pi_1",
            "handler_id": payment["handler_id"],
            "type": instrument.get("type"),
            "credential": dict(instrument.get("credential") or {}),
        }
        if isinstance(payment.get("billing_address"), Mapping):
            entry["billing_address"] = dict(payment["billing_address"])
        out["payment"] = {"instruments": [entry]}
    else:
        out["payment"] = {"instruments": []}
    if isinstance(body.get("risk_signals"), Mapping):
        out["signals"] = dict(body["risk_signals"])
    vendor_member = _ucp_vendor(vendor)
    if vendor_member:
        out[VENDOR_MEMBER] = vendor_member
    return out


# --- rendering out --------------------------------------------------------------------------------


def _acp_state(row: Any) -> dict[str, Any]:
    return dict((row.state or {}).get("acp") or {})


def binding_messages(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ACP binding's own blocking checks on a session (internal codes; mapped on rendering)."""
    acp = dict(state.get("acp") or {})
    acp_input = acp.get("input") or {}
    out: list[dict[str, Any]] = []
    wanted = str(acp_input.get("currency") or "").upper()
    if wanted and state.get("currency") and str(state["currency"]).upper() != wanted:
        out.append(
            {
                "type": "error",
                "code": "currency_mismatch",
                "content": f"The items are priced in {state['currency']}, not {wanted}.",
                "severity": "requires_buyer_review",
                "path": "$.currency",
            }
        )
    out.extend(dp.allowance_messages(acp.get("delegate_payment")))
    return out


def completion_check(state: Mapping[str, Any], total: int, currency: str | None, now: datetime) -> list[dict[str, Any]]:
    out = [m for m in binding_messages(state) if m["code"] == "currency_mismatch"]
    out.extend(
        dp.completion_messages(
            (state.get("acp") or {}).get("delegate_payment"), total=total, currency=currency, now=now
        )
    )
    return out


def _price_warnings(acp_input: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    prices = {str((line.get("item") or {}).get("id")): (index, line) for index, line in enumerate(lines)}
    out = []
    for item in acp_input.get("line_items") or []:
        expected = item.get("unit_amount") if isinstance(item, Mapping) else None
        found = prices.get(str(item.get("id"))) if isinstance(item, Mapping) else None
        if isinstance(expected, int) and found is not None and found[1]["item"].get("price") != expected:
            out.append(
                {
                    "type": "warning",
                    "code": "price_change",
                    "content": "The item price differs from the price the agent sent.",
                    "path": f"$.line_items[{found[0]}]",
                }
            )
    return out


def _dedupe(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for message in messages:
        key = (message.get("type"), message.get("code"), message.get("path"), message.get("content"))
        if key not in seen:
            seen.add(key)
            out.append(dict(message))
    return out


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _totals(totals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": t["type"], "display_text": _TOTAL_LABELS.get(t["type"], t["type"]), "amount": int(t["amount"])}
        for t in totals
        if t.get("type") in _TOTAL_LABELS
    ]


def _line_item(line: Mapping[str, Any]) -> dict[str, Any]:
    item = line.get("item") or {}
    amounts = {t["type"]: int(t["amount"]) for t in line.get("totals") or []}
    out: dict[str, Any] = {
        "id": str(line["id"]),
        "item": {
            "id": str(item.get("id")),
            "name": str(item.get("title") or item.get("id")),
            "unit_amount": int(item.get("price") or 0),
        },
        "quantity": max(1, int(line.get("quantity") or 1)),
        "name": str(item.get("title") or item.get("id")),
        "unit_amount": int(item.get("price") or 0),
        "totals": [
            {"type": "subtotal", "display_text": "Subtotal", "amount": amounts.get("subtotal", 0)},
            {"type": "total", "display_text": "Total", "amount": amounts.get("total", 0)},
        ],
    }
    if isinstance(item.get("image_url"), str) and item["image_url"].startswith("https://"):
        out["images"] = [item["image_url"]]
    return out


def _fulfillment(state: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    options: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for method in (state.get("fulfillment") or {}).get("methods") or []:
        if method.get("type") != "shipping":
            continue
        for group in method.get("groups") or []:
            for option in group.get("options") or []:
                entry: dict[str, Any] = {
                    "type": "shipping",
                    "id": str(option["id"]),
                    "title": str(option.get("title") or option["id"]),
                    "totals": [
                        {
                            "type": "fulfillment",
                            "display_text": str(option.get("title") or "Shipping"),
                            "amount": int(option.get("amount") or 0),
                        }
                    ],
                }
                if option.get("description"):
                    entry["description"] = str(option["description"])
                options.append(entry)
            if group.get("selected_option_id"):
                selected.append(
                    {
                        "type": "shipping",
                        "option_id": str(group["selected_option_id"]),
                        "item_ids": [str(i) for i in group.get("line_item_ids") or method.get("line_item_ids") or []],
                    }
                )
    return options, selected


def _links(merchant: MerchantUcp) -> list[dict[str, Any]]:
    out = []
    for link in merchant.links:
        kind = _LINK_TYPES.get(str(link.get("type")))
        if kind and str(link.get("url") or "").startswith("https://"):
            entry = {"type": kind, "url": link["url"]}
            if link.get("title"):
                entry["title"] = link["title"]
            out.append(entry)
    return out


def _public_decision(vendor: Mapping[str, Any] | None) -> dict[str, Any] | None:
    decision = (vendor or {}).get("decision")
    if not isinstance(decision, Mapping):
        return None
    return {
        k: decision[k]
        for k in ("decision_id", "action", "outcome", "failing_rule", "payload_hash", "expires_at")
        if decision.get(k) is not None
    }


def make_renderer(runtime: handlers.UcpRuntime) -> handlers.CheckoutRenderer:
    async def _render(
        *,
        row: Any,
        merchant: MerchantUcp,
        negotiated: Negotiated,
        now: datetime,
        extra_messages: Sequence[Mapping[str, Any]] = (),
        vendor: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return render_session(
            row=row,
            merchant=merchant,
            handlers=runtime.registry.payment_handlers(merchant, negotiated.version),
            now=now,
            extra_messages=extra_messages,
            vendor=vendor,
        )

    return _render


def render_session(
    *,
    row: Any,
    merchant: MerchantUcp,
    handlers: Sequence[Any],
    now: datetime,
    extra_messages: Sequence[Mapping[str, Any]] = (),
    vendor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The full ACP ``CheckoutSession`` (``CheckoutSessionWithOrder`` once completed) of a session row."""
    state = dict(row.state or {})
    acp = _acp_state(row)
    acp_input = acp.get("input") or {}
    active = caps.active_extensions(acp.get("extensions") or [])
    engine_messages = list(state.get("messages") or [])
    binding = binding_messages(state)
    messages = _dedupe(
        [*engine_messages, *binding, *extra_messages, *_price_warnings(acp_input, state.get("line_items") or [])]
    )
    status = row.status
    if status not in ("completed", "canceled") and row.expires_at <= now:
        acp_status = "expired"
    else:
        acp_status = STATUS_MAP.get(status, "not_ready_for_payment")
        if acp_status == "ready_for_payment" and any(m.get("type") == "error" for m in binding):
            acp_status = "not_ready_for_payment"
    options, selected = _fulfillment(state)
    currency = str(state.get("currency") or acp_input.get("currency") or "").lower()
    body: dict[str, Any] = {
        "id": row.id,
        "protocol": {"version": API_VERSION},
        "capabilities": caps.response_capabilities(
            handlers, active, dp.delegate_payment_request_shape(acp.get("delegate_payment"))
        ),
        "status": acp_status,
        "currency": currency,
        "line_items": [_line_item(line) for line in state.get("line_items") or []],
        "fulfillment_options": options,
        "totals": _totals(state.get("totals") or []),
        "messages": [map_message(m) for m in messages],
        "links": _links(merchant),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "expires_at": _iso(row.expires_at),
    }
    if isinstance(acp_input.get("buyer"), Mapping):
        body["buyer"] = dict(acp_input["buyer"])
    if isinstance(acp_input.get("fulfillment_details"), Mapping):
        body["fulfillment_details"] = dict(acp_input["fulfillment_details"])
    if selected:
        body["selected_fulfillment_options"] = selected
    for name in ("locale", "timezone"):
        if isinstance(acp_input.get(name), str):
            body[name] = acp_input[name]
    if isinstance(acp_input.get("metadata"), Mapping):
        body["metadata"] = dict(acp_input["metadata"])
    if acp_status == "requires_escalation" and state.get("continue_url"):
        body["continue_url"] = state["continue_url"]
    if status == "completed" and row.order_id:
        body["order"] = {
            "id": row.order_id,
            "checkout_session_id": row.id,
            "permalink_url": state.get("order_permalink_url"),
            "status": "confirmed",
        }
    decision = _public_decision(vendor)
    if decision and caps.VENDOR_EXTENSION in active:
        body[caps.VENDOR_FIELD] = {"decision": decision}
    return {k: val for k, val in body.items() if val is not None}


# --- the binding ----------------------------------------------------------------------------------


def acp_negotiated(runtime: handlers.UcpRuntime, merchant: MerchantUcp, platform: str) -> Negotiated:
    """The fixed capability set ACP sessions run with: checkout, order and fulfillment when offered."""
    wanted = {v.CHECKOUT, v.ORDER, v.FULFILLMENT}
    business = runtime.registry.capabilities(
        merchant, ENGINE_RELEASE, RegistryContext(merchant_signing_available=False)
    )
    active = {entry.name: entry for entry in business if entry.name in wanted}
    return Negotiated(
        version=ENGINE_RELEASE,
        active=active,
        platform=PlatformProfile(url=platform, version=ENGINE_RELEASE, capabilities={}),
    )


def _session_runtime(base: handlers.UcpRuntime, translator: handlers.BodyTranslator | None) -> handlers.UcpRuntime:
    runtime = dataclasses.replace(
        base,
        protocol="acp",
        events=dict(EVENTS),
        body_translator=translator,
        completion_check=completion_check,
        supported_versions=(ENGINE_RELEASE,),
    )
    runtime.checkout_renderer = make_renderer(runtime)
    return runtime


def _parse_body(req: AcpRequest) -> dict[str, Any]:
    if len(req.raw_body) > MAX_BODY_BYTES:
        raise _RefusalError(400, acp_error("invalid_request", "invalid", "The request body is too large.", param="$"))
    if not req.raw_body:
        return {}
    try:
        body = json.loads(req.raw_body)
    except ValueError as exc:
        raise _RefusalError(400, acp_error("invalid_request", "invalid", "The body is not JSON.", param="$")) from exc
    if not isinstance(body, dict):
        raise _RefusalError(400, acp_error("invalid_request", "invalid", "The body must be a JSON object.", param="$"))
    return body


async def _allowance(
    runtime: AcpRuntime, req: AcpRequest, merchant: MerchantUcp, session_id: str, passport: str, currency: str | None
) -> dict[str, Any]:
    outcome = await dp.request_allowance(
        runtime.grantex(),
        tenant_id=req.tenant_id,
        merchant_id=merchant.merchant_id,
        checkout_session_id=session_id,
        passport_jwt=passport,
        currency=currency,
    )
    state = outcome.state
    await record_commerce_event_in_own_transaction(
        tenant_id=req.tenant_id,
        event=ALLOWANCE_EVENT,
        outcome="success" if outcome.issued else "refused",
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        buyer_agent_id=None,
        payload={
            "checkout_session_id": session_id,
            "status": state.get("status"),
            "reason": state.get("reason"),
            "passport_sha256": state.get("passport_sha256"),
            "allowance": state.get("allowance"),
            "risk_signals_sha256": commerce_payload_hash(state.get("risk_signals") or []),
            "grantex_audit_event_id": state.get("audit_event_id"),
        },
        resource_id=session_id,
        details={
            "status": state.get("status"),
            "reason": state.get("reason"),
            "risk_signal_count": len(state.get("risk_signals") or []),
        },
    )
    return state


async def run(req: AcpRequest, runtime: AcpRuntime) -> AcpResponse:
    """One ACP checkout call: headers, schema, merchant, idempotency, then the shared UCP operation."""
    rid = hdr.request_id(req.headers.get("request-id"))
    key = req.headers.get("idempotency-key")
    out_headers = {"Request-Id": rid}
    if key and req.method == "POST":
        out_headers["Idempotency-Key"] = key[: hdr.MAX_IDEMPOTENCY_KEY_LENGTH]
    merchant: MerchantUcp | None = None
    claimed = False
    try:
        problem = hdr.api_version_error(req.headers.get("api-version"))
        if problem:
            raise _RefusalError(*problem)
        mode = runtime.signature_mode()
        check = hdr.check_signature(
            platform=req.platform,
            method=req.method,
            path=req.path,
            body=req.raw_body,
            headers=req.headers,
            mode=mode if mode in hdr.SIGNATURE_MODES else "enforce",
        )
        if check.refuse:
            raise _RefusalError(*hdr.signature_error())
        if req.method == "POST":
            problem = hdr.idempotency_key_error(key)
            if problem:
                raise _RefusalError(*problem)
        body = _parse_body(req) if req.method == "POST" else {}
        vendor = _vendor(body) if req.operation in ("create", "update", "complete") else {}
        definition = REQUEST_SCHEMAS.get(req.operation)
        if definition:
            found = request_problem(definition, body)
            if found:
                raise _RefusalError(400, acp_error("invalid_request", "invalid", found[0], param=found[1]))
            if len(body.get("line_items") or []) > MAX_REQUEST_ITEMS:
                raise _RefusalError(
                    400, acp_error("invalid_request", "invalid", "Too many line items.", param="$.line_items")
                )
        merchant = await runtime.ucp.resolver.resolve(host=req.host, tenant_id=req.tenant_id)
        if merchant is None or not merchant_accepts_acp(merchant):
            merchant = None
            raise _RefusalError(404, acp_error("invalid_request", "not_found", "No merchant is served on this host."))
        if req.method == "POST":
            try:
                claim = await idempotency.claim(
                    tenant_id=req.tenant_id,
                    merchant_id=merchant.merchant_id,
                    key=str(key),
                    operation=f"acp:{req.operation}:{req.session_id or ''}",
                    raw_body=hdr.canonical_body(req.raw_body),
                )
            except idempotency.IdempotencyUnavailableError as exc:
                raise _RefusalError(
                    503,
                    acp_error("service_unavailable", "idempotency_unavailable", "Idempotency records are unavailable."),
                ) from exc
            if claim.replay is not None:
                status, stored = claim.replay
                return AcpResponse(
                    status=status,
                    body=stored,
                    headers={**out_headers, "Idempotent-Replayed": "true"},
                    merchant_id=merchant.merchant_id,
                    seller_agent_id=merchant.seller_agent_id,
                    resource_id=req.session_id,
                )
            if claim.conflict == "idempotency_key_reused":
                raise _RefusalError(
                    422,
                    acp_error(
                        "invalid_request",
                        "idempotency_conflict",
                        "This Idempotency-Key was used with a different request.",
                    ),
                )
            if claim.conflict is not None:
                raise _RefusalError(
                    409,
                    acp_error(
                        "invalid_request",
                        "idempotency_in_flight",
                        "A request with this Idempotency-Key is still in progress.",
                    ),
                    headers={"Retry-After": "1"},
                )
            claimed = True
        try:
            response = await _operation(req, runtime, merchant, body, vendor)
        except BaseException:
            if claimed:
                # A 5xx answer releases the claim, so the client can retry the same key.
                await idempotency.store(
                    tenant_id=req.tenant_id, merchant_id=merchant.merchant_id, key=str(key), status=500, body={}
                )
            raise
    except _RefusalError as refusal:
        response = AcpResponse(
            status=refusal.status, body=refusal.error, headers=dict(refusal.headers), outcome="refused"
        )
    if merchant is not None:
        response.merchant_id = response.merchant_id or merchant.merchant_id
        response.seller_agent_id = response.seller_agent_id or merchant.seller_agent_id
    response.resource_id = response.resource_id or req.session_id
    response.headers = {**out_headers, **response.headers}
    if claimed and merchant is not None:
        await idempotency.store(
            tenant_id=req.tenant_id,
            merchant_id=merchant.merchant_id,
            key=str(key),
            status=response.status,
            body=response.body,
        )
    return response


async def _operation(
    req: AcpRequest, runtime: AcpRuntime, merchant: MerchantUcp, body: dict[str, Any], vendor: Mapping[str, Any]
) -> AcpResponse:
    session_id = req.session_id
    delegate_state: dict[str, Any] | None = None
    passport = vendor.get("passport_jwt")
    if req.operation == "create":
        session_id = f"chk_{uuid.uuid4().hex}"
    if isinstance(passport, str) and req.operation in ("create", "update") and session_id:
        delegate_state = await _allowance(runtime, req, merchant, session_id, passport, body.get("currency"))
    translator = (
        make_translator(vendor=vendor, delegate_state=delegate_state) if req.operation in ("create", "update") else None
    )
    ucp_runtime = _session_runtime(runtime.ucp, translator)
    payload = complete_body(body, vendor) if req.operation == "complete" else body
    ucp_req = handlers.UcpRequest(
        tenant_id=req.tenant_id,
        host=req.host,
        ucp_agent=None,
        raw_body=json.dumps(payload).encode("utf-8"),
        session_id=session_id if req.operation == "create" else None,
        platform=req.platform,
    )
    negotiated = acp_negotiated(ucp_runtime, merchant, req.platform)
    result = await handlers.run_operation(
        OPERATIONS[req.operation], ucp_runtime, ucp_req, merchant, negotiated, req.session_id
    )
    if result.status >= 400:
        status, error = protocol_error(result.status, result.body)
        return AcpResponse(
            status=status, body=error, outcome="refused", recorded=result.recorded, resource_id=session_id
        )
    return AcpResponse(
        status=result.status,
        body=result.body,
        outcome=result.outcome,
        recorded=result.recorded,
        resource_id=result.resource_id or session_id,
    )
