# SPDX-License-Identifier: Apache-2.0
"""The checkout payment model: instruments, credentials, split payments, terms, actions and token binding.

* ``payment.instruments[]`` need ``id``, ``handler_id`` (a handler entry this
  merchant offers) and ``type``. Credentials flow platform to business only
  and are never echoed or stored: the session keeps the instrument's id,
  handler, type, display data and the SHA-256 of the canonical credential.
* Credential types: ``pan`` (2026-08-25), ``network_token`` (2026-08-25,
  needs ``cryptogram``), handler tokens (``token``), and the deprecated
  ``card`` (``card_number_type`` ``fpan``/``network_token``/``dpan``, the only
  card form in 2026-04-08). A PAN (``pan``, or ``card`` with ``fpan``) is
  refused by a handler that does not list it.
* Exactly one instrument at completion unless
  ``dev.ucp.common.payment.split_payments`` is active; then instruments carry
  ``amount``, sum to the total and match an allowed combination.
* ``dev.ucp.common.payment.terms`` (2026-08-25 only): the merchant's terms with
  computed schedules (descriptions as ``description.json`` objects);
  ``selected_term_id`` on update; the order keeps ``accepted_term``.
* Token binding: tokenizers bind a token to ``{type, id}`` (2026-08-25,
  ``binding.json``) or ``{checkout_id}`` (2026-04-08); the executor receives the
  binding of the session it pays.
* ``ucp.request_constraints`` a handler emits are enforced here at completion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from core.commerce.ledger import commerce_payload_hash
from core.commerce.payment_handlers import resolve_handler, satisfies
from core.commerce.ucp import messages as msg
from core.commerce.ucp import releases as v
from core.commerce.ucp.merchant import MerchantUcp

DISPLAY_FIELDS = ("brand", "last_digits", "expiry_month", "expiry_year", "description", "card_art")


class PaymentRequestError(ValueError):
    """A malformed payment object (protocol error, 400)."""


@dataclass
class Instrument:
    id: str
    handler_id: str
    type: str
    credential: dict[str, Any] | None = field(default=None, repr=False)
    display: dict[str, Any] = field(default_factory=dict)
    billing_address: dict[str, Any] | None = None
    selected: bool | None = None
    amount: int | None = None

    @property
    def credential_type(self) -> str | None:
        if not self.credential:
            return None
        value = self.credential.get("type")
        return str(value) if value else None

    @property
    def credential_sha256(self) -> str | None:
        return commerce_payload_hash(self.credential) if self.credential else None

    def is_pan(self) -> bool:
        kind = self.credential_type
        return kind == "pan" or (kind == "card" and (self.credential or {}).get("card_number_type") == "fpan")

    def stored(self) -> dict[str, Any]:
        """What the session keeps: never the credential."""
        out: dict[str, Any] = {"id": self.id, "handler_id": self.handler_id, "type": self.type}
        if self.display:
            out["display"] = dict(self.display)
        if self.billing_address:
            out["billing_address"] = dict(self.billing_address)
        if self.selected is not None:
            out["selected"] = self.selected
        if self.amount is not None:
            out["amount"] = self.amount
        if self.credential:
            out["credential_type"] = self.credential_type
            out["credential_sha256"] = self.credential_sha256
        return out


def parse_payment(raw: Any) -> tuple[list[Instrument], str | None]:
    """Instruments and ``selected_term_id`` from a request ``payment`` object."""
    if raw is None:
        return [], None
    if not isinstance(raw, Mapping):
        raise PaymentRequestError("payment must be an object")
    items = raw.get("instruments") or []
    if not isinstance(items, list):
        raise PaymentRequestError("payment.instruments must be an array")
    out: list[Instrument] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise PaymentRequestError(f"payment.instruments[{index}] must be an object")
        for name in ("id", "handler_id", "type"):
            if not isinstance(item.get(name), str) or not item.get(name):
                raise PaymentRequestError(f"payment.instruments[{index}].{name} is required")
        credential = item.get("credential")
        if credential is not None and (not isinstance(credential, Mapping) or not credential.get("type")):
            raise PaymentRequestError(f"payment.instruments[{index}].credential needs a type")
        amount = item.get("amount")
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
            raise PaymentRequestError(f"payment.instruments[{index}].amount must be a non-negative integer")
        raw_display = item.get("display")
        display: Mapping[str, Any] = raw_display if isinstance(raw_display, Mapping) else {}
        out.append(
            Instrument(
                id=str(item["id"])[:120],
                handler_id=str(item["handler_id"])[:120],
                type=str(item["type"])[:60],
                credential=dict(credential) if isinstance(credential, Mapping) else None,
                display={k: display[k] for k in DISPLAY_FIELDS if k in display},
                billing_address=dict(item["billing_address"])
                if isinstance(item.get("billing_address"), Mapping)
                else None,
                selected=item.get("selected") if isinstance(item.get("selected"), bool) else None,
                amount=amount,
            )
        )
    term = raw.get("selected_term_id")
    return out, str(term) if isinstance(term, str) and term else None


def credential_problem(credential: Mapping[str, Any], version: str) -> str | None:
    """Why a credential is malformed for its type, or None (``pan_credential``, ``network_token_credential``,
    ``card_credential``, ``token_credential``)."""
    kind = str(credential.get("type") or "")
    if kind == "pan":
        if version < v.V2026_08_25:
            return "pan credentials do not exist in 2026-04-08; use card"
        return None if credential.get("number") else "a pan credential needs number"
    if kind == "network_token":
        if version < v.V2026_08_25:
            return "network_token credentials do not exist in 2026-04-08; use card"
        return (
            None
            if credential.get("number") and credential.get("cryptogram")
            else "a network token needs number and cryptogram"
        )
    if kind == "card":
        if credential.get("card_number_type") not in ("fpan", "network_token", "dpan"):
            return "a card credential needs card_number_type"
        return None if credential.get("number") else "a card credential needs number"
    return None if credential.get("token") else f"a {kind} credential needs token"


def validate_instruments(
    instruments: Sequence[Instrument], *, merchant: MerchantUcp, version: str
) -> list[dict[str, Any]]:
    """Messages for instruments whose handler is unknown (create/update: recoverable, nothing is charged)."""
    out = []
    for index, instrument in enumerate(instruments):
        if resolve_handler(merchant, version, instrument.handler_id) is None:
            out.append(
                msg.error(
                    "payment_handler_unavailable",
                    "This payment handler is not offered for this checkout.",
                    path=f"$.payment.instruments[{index}].handler_id",
                )
            )
    return out


def completion_messages(
    instruments: Sequence[Instrument],
    *,
    merchant: MerchantUcp,
    version: str,
    split_active: bool,
    total: int,
    currency: str | None,
) -> list[dict[str, Any]]:
    """Every reason the payment cannot complete (``payment_failed`` business outcomes)."""
    out: list[dict[str, Any]] = []
    if not instruments:
        return [msg.error("payment_failed", "A payment instrument is required to complete.", path="$.payment")]
    if len(instruments) > 1 and not split_active:
        return [
            msg.error("payment_failed", "Exactly one payment instrument is accepted.", path="$.payment.instruments")
        ]
    for index, instrument in enumerate(instruments):
        path = f"$.payment.instruments[{index}]"
        resolved = resolve_handler(merchant, version, instrument.handler_id)
        if resolved is None:
            out.append(msg.error("payment_failed", "This payment handler is not offered.", path=f"{path}.handler_id"))
            continue
        handler, entry = resolved
        offered_types = {str(item.get("type")) for item in entry.available_instruments} or {instrument.type}
        if instrument.type not in offered_types:
            out.append(msg.error("payment_failed", "This instrument type is not accepted.", path=f"{path}.type"))
            continue
        if not instrument.credential:
            out.append(msg.error("payment_failed", "A credential is required to complete.", path=f"{path}.credential"))
            continue
        problem = credential_problem(instrument.credential, version)
        if problem:
            out.append(msg.error("payment_failed", problem, path=f"{path}.credential"))
            continue
        accepted = handler.accepted_credentials(version)
        if instrument.is_pan() and "pan" not in accepted:
            out.append(
                msg.error(
                    "payment_failed",
                    "Card numbers are not accepted; send a network or instrument token.",
                    path=f"{path}.credential",
                )
            )
            continue
        if instrument.credential_type not in accepted:
            out.append(
                msg.error("payment_failed", "This credential type is not accepted.", path=f"{path}.credential.type")
            )
            continue
        constraint = handler.request_constraints(entry, instrument.type, version)
        candidate = {
            "id": instrument.id,
            "handler_id": instrument.handler_id,
            "type": instrument.type,
            "credential": instrument.credential,
        }
        if constraint and not satisfies(candidate, _object_part(constraint)):
            out.append(
                msg.error(
                    "payment_failed", "The credential does not meet the request constraints.", path=f"{path}.credential"
                )
            )
            continue
        allowance = handler.allowance(merchant)
        charge = instrument.amount if split_active and instrument.amount is not None else total
        if allowance.currency and currency and allowance.currency != currency:
            out.append(msg.error("payment_failed", "This handler cannot charge in this currency.", path=path))
        elif allowance.max_amount_minor_units is not None and charge > allowance.max_amount_minor_units:
            out.append(msg.error("payment_failed", "The amount exceeds this handler's payment allowance.", path=path))
    if split_active and len(instruments) > 1 and not out:
        out.extend(_split_messages(instruments, merchant, total))
    return out


def _object_part(constraint: Mapping[str, Any]) -> dict[str, Any]:
    return {k: val for k, val in constraint.items() if k != "path"}


def _split_messages(instruments: Sequence[Instrument], merchant: MerchantUcp, total: int) -> list[dict[str, Any]]:
    if any(i.amount is None for i in instruments):
        return [
            msg.error(
                "payment_failed", "Each instrument in a split payment needs an amount.", path="$.payment.instruments"
            )
        ]
    if sum(i.amount or 0 for i in instruments) != total:
        return [
            msg.error("payment_failed", "Split payment amounts must add up to the total.", path="$.payment.instruments")
        ]
    combos = (merchant.split_payments or {}).get("allowed_combinations") or []
    types = [i.type for i in instruments]
    for combo in combos:
        if _combination_matches(combo, types):
            return []
    return [
        msg.error("payment_failed", "This combination of instruments is not accepted.", path="$.payment.instruments")
    ]


def _combination_matches(combo: Any, types: Sequence[str]) -> bool:
    """``allowed_combinations[i]``: groups ``{types, min=0, max=1}``; every instrument in exactly one group."""
    if not isinstance(combo, list):
        return False
    remaining = list(types)
    for group in combo:
        if not isinstance(group, Mapping):
            return False
        group_types = set(group.get("types") or [])
        count = sum(1 for t in remaining if t in group_types)
        if count < int(group.get("min", 0)) or count > int(group.get("max", 1)):
            return False
        remaining = [t for t in remaining if t not in group_types]
    return not remaining


def payment_terms(merchant: MerchantUcp, total: int, now: datetime) -> list[dict[str, Any]]:
    """The merchant's payment terms with schedule amounts computed from the total (``payment_term.json``)."""
    terms: list[dict[str, Any]] = []
    for term in merchant.payment_terms:
        schedules = []
        raw_schedules = [s for s in term.get("schedules") or [] if isinstance(s, Mapping)]
        allocated = 0
        for position, schedule in enumerate(raw_schedules):
            share = schedule.get("share_bps")
            if isinstance(share, int) and not isinstance(share, bool):
                amount = total - allocated if position == len(raw_schedules) - 1 else total * share // 10000
            else:
                amount = int(schedule.get("amount") or 0)
            allocated += amount
            entry: dict[str, Any] = {
                "id": str(schedule.get("id") or f"s{position + 1}"),
                "type": str(schedule.get("type") or "installment"),
                "description": {"plain": str(schedule.get("description") or "Payment")},
                "amount": amount,
            }
            due_in_days = schedule.get("due_in_days")
            if isinstance(due_in_days, int) and not isinstance(due_in_days, bool):
                entry["due_at"] = (now + timedelta(days=due_in_days)).isoformat().replace("+00:00", "Z")
            schedules.append(entry)
        if schedules:
            terms.append({"id": str(term["id"]), "title": str(term.get("title") or term["id"]), "schedules": schedules})
    return terms


def token_binding(version: str, checkout_id: str) -> dict[str, Any]:
    """The tokenizer binding of a checkout: ``{type, id}`` (2026-08-25) or ``{checkout_id}`` (2026-04-08)."""
    if version >= v.V2026_08_25:
        return {"type": v.CHECKOUT, "id": checkout_id}
    return {"checkout_id": checkout_id}
