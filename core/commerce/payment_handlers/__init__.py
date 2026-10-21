# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Payment handlers: the instruments and credentials a merchant accepts, advertised in UCP profiles.

A payment handler (UCP overview, Payment Handlers) is declared under
``ucp.payment_handlers[<reverse-DNS name>][]`` with an ``id``, a ``version`` and
``available_instruments``; checkout responses repeat the handlers that apply
to that checkout. A handler here only *describes* acceptance: which credential
types it takes (a PAN is never accepted by a tokenised handler), the
``ucp.request_constraints`` it emits and enforces, and the allowance it
grants. Moving money is the job of an executor (``core.commerce.executors``),
which runs only on a consumed ``allow`` decision.

Handlers register themselves with ``register_payment_handler``; the capability
registry then lists their entries for each merchant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from core.commerce.ucp.capabilities import DEFAULT_REGISTRY, PaymentHandlerEntry, Registry
from core.commerce.ucp.merchant import MerchantUcp


@dataclass(frozen=True)
class Allowance:
    """The most one completion may charge through the handler (minor units), from the mandate capability."""

    max_amount_minor_units: int | None
    currency: str | None


class PaymentHandler(Protocol):
    name: str

    def entries(self, merchant: MerchantUcp, version: str) -> list[PaymentHandlerEntry]:
        """The handler instances this merchant offers in ``version`` (empty when not configured)."""
        ...

    def accepted_credentials(self, version: str) -> tuple[str, ...]:
        """Credential ``type`` values the handler accepts on completion."""
        ...

    def allowance(self, merchant: MerchantUcp) -> Allowance: ...

    def request_constraints(
        self, entry: PaymentHandlerEntry, instrument_type: str, version: str
    ) -> dict[str, Any] | None:
        """The ``ucp.request_constraints`` emitted on an available instrument (2026-08-25), enforced at completion."""
        ...


_HANDLERS: dict[str, PaymentHandler] = {}


def register_payment_handler(handler: PaymentHandler, registry: Registry = DEFAULT_REGISTRY) -> None:
    _HANDLERS[handler.name] = handler
    registry.register_payment_handler_provider(handler.entries)


def unregister_payment_handler(name: str, registry: Registry = DEFAULT_REGISTRY) -> None:
    handler = _HANDLERS.pop(name, None)
    if handler is not None and handler.entries in registry._handler_providers:
        registry._handler_providers.remove(handler.entries)


def registered_handlers() -> dict[str, PaymentHandler]:
    return dict(_HANDLERS)


def resolve_handler(
    merchant: MerchantUcp, version: str, handler_id: str
) -> tuple[PaymentHandler, PaymentHandlerEntry] | None:
    """The handler and entry a payment instrument's ``handler_id`` refers to, for this merchant."""
    for handler in _HANDLERS.values():
        for entry in handler.entries(merchant, version):
            if entry.id == handler_id:
                return handler, entry
    return None


def satisfies(value: Any, constraint: Mapping[str, Any]) -> bool:
    """Evaluate a UCP Constraint Expression (``required``, ``properties``, ``anyOf``, ``enum``, ``const``)."""
    if "const" in constraint and value != constraint["const"]:
        return False
    if "enum" in constraint and value not in constraint["enum"]:
        return False
    if "required" in constraint or "properties" in constraint or "anyOf" in constraint:
        if not isinstance(value, Mapping):
            return False
        if any(name not in value for name in constraint.get("required", [])):
            return False
        for name, sub in (constraint.get("properties") or {}).items():
            if name in value and not satisfies(value[name], sub):
                return False
        options = constraint.get("anyOf")
        if options and not any(satisfies(value, option) for option in options):
            return False
    return True


# Import for side effects: the Plural tokenised-instrument handler registers itself.
from core.commerce.payment_handlers import plural as _plural  # noqa: E402,F401
