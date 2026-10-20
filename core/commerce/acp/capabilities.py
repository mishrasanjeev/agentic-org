# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""ACP capability negotiation, extensions and payment handlers (G38).

ACP 2026-04-17 (RFC capability negotiation; ``Capabilities``,
``ExtensionDeclaration`` and ``PaymentHandler`` in ``schema.agentic_checkout.json``):
the agent sends ``capabilities`` on create (``interventions`` it can handle and
``extensions`` as names); the seller answers with what is active for the session.
This server declares only what it serves:

* ``payment.handlers``: the merchant's payment handlers that have an ACP projection
  here (``ACP_HANDLERS``). The Plural tokenised-instrument handler requires the
  delegate-payment flow; its config carries the Grantex delegate-payment allowance
  for the session in the ``DelegatePaymentRequest`` shape (``allowance``,
  ``risk_signals``, ``metadata``).
* ``extensions``: the intersection of what the agent asked for and
  ``SERVED_EXTENSIONS``. The core ``discount`` extension is not served, so it is
  never declared and ``discounts`` never appears in a response.
* ``interventions``: none is served (no 3DS, biometric or address verification
  step is offered over ACP), so the member is omitted.

The vendor extension ``ai.agenticorg.commerce`` adds one member,
``agenticorg_commerce``, to the create, update and complete requests and to the
session: buyer authority references, a ``decision_id`` or ``decide_only`` for the
commerce arbiter, and the Commerce Passport (``passport_jwt``) Grantex derives the
delegate-payment allowance from. The session echoes the arbiter decision there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.commerce.payment_handlers import plural
from core.commerce.ucp.capabilities import PaymentHandlerEntry

VENDOR_EXTENSION = "ai.agenticorg.commerce"
VENDOR_EXTENSION_VERSION = "2026-10-20"
VENDOR_FIELD = "agenticorg_commerce"
SERVED_EXTENSIONS: dict[str, dict[str, Any]] = {
    VENDOR_EXTENSION: {
        "name": VENDOR_EXTENSION,
        "extends": [
            f"$.CheckoutSessionCreateRequest.{VENDOR_FIELD}",
            f"$.CheckoutSessionUpdateRequest.{VENDOR_FIELD}",
            f"$.CheckoutSessionCompleteRequest.{VENDOR_FIELD}",
            f"$.CheckoutSession.{VENDOR_FIELD}",
        ],
    }
}

# Payment handler name -> the ACP PaymentHandler members the UCP handler entry does not carry.
_HANDLER_DOCS = "https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/acp-feed-and-sessions.md"
ACP_HANDLERS: dict[str, dict[str, Any]] = {
    plural.HANDLER_NAME: {
        "display_name": "Card",
        "spec": f"{_HANDLER_DOCS}#payment-handler",
        "requires_delegate_payment": True,
        "requires_pci_compliance": False,
        "psp": "plural_pine",
        "instrument_schemas": [plural.INSTRUMENT_SCHEMA],
    }
}


def requested_extensions(capabilities: Any) -> list[str]:
    """Extension names an agent asked for on create (``name`` or ``name@YYYY-MM-DD``)."""
    raw = capabilities.get("extensions") if isinstance(capabilities, Mapping) else None
    if not isinstance(raw, list):
        return []
    return sorted({str(item)[:120] for item in raw if isinstance(item, str) and item})


def active_extensions(requested: Sequence[str]) -> list[str]:
    """Served extensions the agent asked for; a pinned version must equal the served one."""
    active = []
    for item in requested:
        name, _, pinned = item.partition("@")
        if name in SERVED_EXTENSIONS and (not pinned or pinned == VENDOR_EXTENSION_VERSION):
            active.append(name)
    return sorted(set(active))


def extension_declarations(active: Sequence[str]) -> list[dict[str, Any]]:
    return [dict(SERVED_EXTENSIONS[name]) for name in sorted(set(active)) if name in SERVED_EXTENSIONS]


def acp_payment_handler(
    entry: PaymentHandlerEntry, delegate_payment: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """A UCP payment handler entry as an ACP ``PaymentHandler``; None when it has no ACP projection."""
    extra = ACP_HANDLERS.get(entry.name)
    if extra is None or not entry.schema:
        return None
    config: dict[str, Any] = dict(entry.config)
    if extra["requires_delegate_payment"] and delegate_payment:
        config["delegate_payment"] = dict(delegate_payment)
    return {
        "id": entry.id,
        "name": entry.name,
        "display_name": extra["display_name"],
        "version": entry.version,
        "spec": extra["spec"],
        "requires_delegate_payment": extra["requires_delegate_payment"],
        "requires_pci_compliance": extra["requires_pci_compliance"],
        "psp": extra["psp"],
        "config_schema": entry.schema,
        "instrument_schemas": list(extra["instrument_schemas"]),
        "config": config,
    }


def response_capabilities(
    handlers: Sequence[PaymentHandlerEntry],
    active: Sequence[str],
    delegate_payment: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The session's ``capabilities``: payment handlers served over ACP and the active extensions."""
    projected = [h for h in (acp_payment_handler(entry, delegate_payment) for entry in handlers) if h is not None]
    out: dict[str, Any] = {"payment": {"handlers": projected}}
    declarations = extension_declarations(active)
    if declarations:
        out["extensions"] = declarations
    return out
