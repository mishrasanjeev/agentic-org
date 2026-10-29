# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Buyer payment hand-off: a completed checkout gives the buyer a link to pay on the merchant's own page.

AgenticOrg never collects money for a merchant. A merchant chooses how its
buyers pay in its commerce config:

``provider_policy.payments``::

    {"mode": "shopify_cart"}
    {"mode": "plural_hosted", "plural": {"credential_ref": "commerce_plural_<digest>"}}

``shopify_cart``
    Complete hands the buyer to the merchant's own Shopify cart permalink,
    ``https://<shop>.myshopify.com/cart/<variant id>:<qty>,...`` (Shopify's
    documented cart permalink). Payment and the order happen in Shopify. The
    numeric variant ids stay server-side: the checkout state keeps each line's
    ``storefront_variant`` (the numeric part of the Shopify variant gid from the
    synced product evidence) and public item ids stay the redacted UCP ids.
``plural_hosted``
    Complete creates a Plural hosted-checkout order with the merchant's own
    Plural credentials (encrypted in the tenant vault, ``core.commerce.plural_hosted``)
    and hands the buyer the Plural ``redirect_url``. Plural's signed webhook (or
    the status poll) confirms the payment and the checkout becomes an order.

The hand-off is a commerce action like any other: Complete seals an arbiter
decision for ``payment_handoff`` over ``handoff_payload`` and consumes the
allow (with the Grantex grant where it is required) before any link exists.
No mandate, consent or passport is asked for, because the agent moves no money
and holds no credential: the buyer authenticates and pays on the payment page
(3-D Secure, UPI PIN). The session answers ``requires_escalation`` with
``continue_url`` and a ``requires_buyer_input`` message; repeating Complete
returns the same link while it is valid. Without ``provider_policy.payments``
nothing here runs and Complete keeps the agent-executed path (executor,
mandates) unchanged.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from core.commerce.ledger import commerce_payload_hash, json_safe
from core.commerce.ucp import messages as msg

MODE_SHOPIFY_CART = "shopify_cart"
MODE_PLURAL_HOSTED = "plural_hosted"
MODES: tuple[str, ...] = (MODE_SHOPIFY_CART, MODE_PLURAL_HOSTED)
ACTION = "payment_handoff"
MESSAGE_CODE = "payment_handoff"
# The order entity's ``executor`` for an order a hand-off produced (only Plural confirms one back to us).
ORDER_EXECUTOR = {MODE_PLURAL_HOSTED: "plural_hosted"}
# How long a Plural payment link is offered before Complete issues a new one.
PLURAL_LINK_TTL = timedelta(minutes=30)
MAX_REFERENCES = 10
MAX_SEEN_EVENTS = 20

_SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
_CREDENTIAL_REF = re.compile(r"^commerce_plural_[0-9a-f]{24}$")
_VARIANT = re.compile(r"^[0-9]{1,20}$")

SHOPIFY_MESSAGE = "Open continue_url to pay for this order in the shop's own Shopify checkout."
PLURAL_MESSAGE = "Open continue_url to pay for this order on the shop's secure Plural payment page."


class PaymentSettingsError(ValueError):
    """A malformed ``provider_policy.payments`` object."""


@dataclass(frozen=True)
class PaymentSettings:
    mode: str
    credential_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode}
        if self.mode == MODE_PLURAL_HOSTED:
            out["plural"] = {"credential_ref": self.credential_ref}
        return out


def parse_payment_settings(raw: Any) -> PaymentSettings:
    """Validate a ``provider_policy.payments`` object (raises ``PaymentSettingsError``)."""
    if not isinstance(raw, Mapping):
        raise PaymentSettingsError("payments must be an object")
    unknown = set(raw) - {"mode", "plural"}
    if unknown:
        raise PaymentSettingsError(f"payments.{sorted(unknown)[0]} is not a known field")
    mode = raw.get("mode")
    if mode not in MODES:
        raise PaymentSettingsError("payments.mode must be shopify_cart or plural_hosted")
    if mode == MODE_SHOPIFY_CART:
        return PaymentSettings(mode=mode)
    plural = raw.get("plural")
    ref = plural.get("credential_ref") if isinstance(plural, Mapping) else None
    if not isinstance(ref, str) or not _CREDENTIAL_REF.match(ref):
        raise PaymentSettingsError("payments.plural.credential_ref must name saved Plural credentials")
    return PaymentSettings(mode=mode, credential_ref=ref)


def payment_settings(config: Mapping[str, Any] | None) -> PaymentSettings | None:
    """The merchant's hand-off settings, or None (no hand-off; an unusable object also means none: fail closed)."""
    policy = config.get("provider_policy") if isinstance(config, Mapping) else None
    raw = policy.get("payments") if isinstance(policy, Mapping) else None
    if raw is None:
        return None
    try:
        return parse_payment_settings(raw)
    except PaymentSettingsError:
        return None


# --- Shopify cart permalink ---------------------------------------------------------------------


def shopify_shop_domain(source_connectors: Sequence[Any] | None) -> str | None:
    """The ``*.myshopify.com`` domain of the merchant's enabled Shopify source connector, if any."""
    for source in source_connectors or ():
        if not isinstance(source, Mapping) or source.get("connector_type") != "shopify":
            continue
        if source.get("enabled") is False:
            continue
        domain = str(source.get("shop_domain") or "").strip().lower()
        if _SHOP_DOMAIN.match(domain):
            return domain
    return None


def shopify_cart_url(shop_domain: str | None, line_items: Sequence[Mapping[str, Any]]) -> str | None:
    """``https://<shop>/cart/<variant>:<qty>,...`` or None when a line has no Shopify variant or whole quantity."""
    if not shop_domain or not _SHOP_DOMAIN.match(shop_domain) or not line_items:
        return None
    parts: list[str] = []
    for line in line_items:
        variant = str(line.get("storefront_variant") or "")
        quantity = line.get("quantity")
        item = line.get("item") if isinstance(line.get("item"), Mapping) else {}
        if not _VARIANT.match(variant) or item.get("quantity_unit"):
            return None
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 9999:
            return None
        parts.append(f"{variant}:{quantity}")
    return f"https://{shop_domain}/cart/{','.join(parts)}"


# --- the decision's payload and the session state ---------------------------------------------------


def cart_hash(state: Mapping[str, Any]) -> str:
    """What the buyer is paying for: lines, totals, currency and the selected fulfillment (RFC 8785 SHA-256)."""
    fulfillment = [
        {
            "type": m.get("type"),
            "destination": m.get("selected_destination_id"),
            "options": [g.get("selected_option_id") for g in m.get("groups") or []],
        }
        for m in (state.get("fulfillment") or {}).get("methods") or []
    ]
    return commerce_payload_hash(
        json_safe(
            {
                "currency": state.get("currency"),
                "totals": state.get("totals"),
                "line_items": [
                    {"item_id": li["item"]["id"], "quantity": li["quantity"], "price": li["item"]["price"]}
                    for li in state.get("line_items") or []
                ],
                "fulfillment": fulfillment,
            }
        )
    )


def handoff_payload(
    *, session_id: str, revision: int, merchant_id: str, seller_agent_id: str, state: Mapping[str, Any], mode: str
) -> dict[str, Any]:
    """The binding payload of a ``payment_handoff`` decision (JSON-safe; no buyer data)."""
    return json_safe(
        {
            "action": ACTION,
            "session_id": session_id,
            "revision": revision,
            "merchant_id": merchant_id,
            "seller_agent_id": seller_agent_id,
            "mode": mode,
            "currency": state.get("currency"),
            "totals": state.get("totals"),
            "cart_hash": cart_hash(state),
        }
    )


def handoff_reference(session_id: str, decision_id: str) -> str:
    """The Plural ``merchant_order_reference`` of one hand-off (A-Z a-z 0-9 - _, at most 50)."""
    return "aoh-" + hashlib.sha256(f"{session_id}|{decision_id}".encode()).hexdigest()[:44]


def buyer_message(mode: str) -> dict[str, Any]:
    content = SHOPIFY_MESSAGE if mode == MODE_SHOPIFY_CART else PLURAL_MESSAGE
    return msg.error(MESSAGE_CODE, content, severity="requires_buyer_input", path="$.payment")


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def active_handoff(state: Mapping[str, Any], now: datetime) -> Mapping[str, Any] | None:
    """The issued hand-off whose link is still valid, or None."""
    handoff = state.get("handoff")
    if not isinstance(handoff, Mapping) or handoff.get("status") != "issued" or not handoff.get("continue_url"):
        return None
    expires = _parse(handoff.get("expires_at"))
    if expires is None or expires <= now:
        return None
    return handoff


def clear_handoff(state: dict[str, Any], status: str) -> None:
    """Retire the current hand-off: its message and ``continue_url`` go, its record stays with ``status``."""
    state["messages"] = [m for m in state.get("messages") or [] if m.get("code") != MESSAGE_CODE]
    state.pop("continue_url", None)
    handoff = state.get("handoff")
    if isinstance(handoff, dict):
        handoff["status"] = status


def issue(
    state: dict[str, Any],
    *,
    mode: str,
    continue_url: str,
    decision_id: str,
    now: datetime,
    expires_at: datetime,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Record an issued hand-off: the link, its decision and validity, and the buyer's escalation message."""
    current = dict(state.get("handoff") or {}) if isinstance(state.get("handoff"), Mapping) else {}
    handoff = {
        **(current if current.get("decision_id") == decision_id else {}),
        **dict(extra or {}),
        "mode": mode,
        "status": "issued",
        "decision_id": decision_id,
        "continue_url": continue_url,
        "issued_at": iso(now),
        "expires_at": iso(expires_at),
    }
    state["handoff"] = handoff
    state["continue_url"] = continue_url
    messages = [m for m in state.get("messages") or [] if m.get("code") != MESSAGE_CODE]
    messages.append(buyer_message(mode))
    state["messages"] = messages


def remember_reference(state: dict[str, Any], reference: str, record: Mapping[str, Any]) -> None:
    """Keep every Plural reference of the session (a late payment on an older link must still match)."""
    refs = [r for r in state.get("handoff_refs") or [] if r != reference]
    refs.append(reference)
    orders = dict(state.get("handoff_orders") or {})
    orders[reference] = {**dict(orders.get(reference) or {}), **dict(record)}
    for stale in refs[:-MAX_REFERENCES]:
        orders.pop(stale, None)
    state["handoff_refs"] = refs[-MAX_REFERENCES:]
    state["handoff_orders"] = orders


def seen_event(state: dict[str, Any], event_id: str | None) -> bool:
    """True when ``event_id`` was already applied to this session; otherwise remember it."""
    if not event_id:
        return False
    seen = list(state.get("handoff_events") or [])
    if event_id in seen:
        return True
    seen.append(event_id)
    state["handoff_events"] = seen[-MAX_SEEN_EVENTS:]
    return False


def _public_api_base() -> str | None:
    from core.config import settings

    base = str(getattr(settings, "public_api_base_url", "") or "").strip().rstrip("/")
    return base if base.lower().startswith("https://") else None


def return_url(tenant_id: str, session_id: str) -> str | None:
    """Where Plural sends the buyer back (a simple result page); None without an https public API base."""
    base = _public_api_base()
    return f"{base}/api/v1/payments/plural/return/{tenant_id}/{session_id}" if base else None


def webhook_url(tenant_id: str, webhook_key: str) -> str | None:
    """The webhook URL the merchant registers in its Plural dashboard."""
    base = _public_api_base()
    return f"{base}/api/v1/payments/plural/webhooks/{tenant_id}/{webhook_key}" if base else None


PAID = frozenset({"PROCESSED", "CAPTURED"})
FAILED = frozenset({"FAILED", "CANCELLED", "DECLINED", "REJECTED", "EXPIRED", "ATTEMPTED_FAILED"})


def payment_state(status: str | None) -> str:
    """Plural order status -> ``paid``, ``failed`` or ``pending``.

    With ``pre_auth: false`` a successful hosted payment is ``PROCESSED``
    (``CAPTURED`` is accepted too); ``AUTHORIZED`` without a capture is not
    money received, so it stays pending.
    """
    value = str(status or "").upper()
    if value in PAID:
        return "paid"
    if value in FAILED:
        return "failed"
    return "pending"
