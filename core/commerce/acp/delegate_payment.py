# SPDX-License-Identifier: Apache-2.0
"""The ACP delegate-payment allowance of a checkout session, from Grantex (G35, AgenticOrg side).

ACP delegated payment (RFC delegate payment; ``schema.delegate_payment.json``): the
agent sends the buyer's payment method to the PSP vault with an ``allowance``
(``reason: one_time``, ``max_amount``, ``currency``, ``checkout_session_id``,
``merchant_id``, ``expires_at``), ``risk_signals`` and ``metadata``, and the token
it gets back is usable only within that allowance.

For each ACP session that carries a Commerce Passport (the vendor extension's
``passport_jwt``), this module asks Grantex ``POST
/v1/commerce/oacp/acp/delegate-payment-allowance`` for the allowance derived from the
passport's amount cap: the AgenticOrg service token, async over the DNS-pinned
egress transport, a 5 second timeout, no retry, no redirect
(``core.commerce.grantex_service``). The request carries the tenant, the passport,
the session, the merchant and the currency, never card data. The answer is kept
in the ``DelegatePaymentRequest`` shape (``allowance``, ``risk_signals``,
``metadata``) and shown in the payment handler's config; the passport itself is
kept only as a SHA-256.

Grantex unreachable, slow or answering 5xx is a business outcome at once
(``delegate_payment_allowance_unavailable``), never a wait; a 4xx refusal is
``delegate_payment_allowance_refused`` with Grantex's code. Completion requires an
issued allowance that covers the total in its currency and has not expired.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from core.commerce.grantex_service import GrantexServiceClient, GrantexServiceUnavailableError

logger = structlog.get_logger()

ALLOWANCE_PATH = "/v1/commerce/oacp/acp/delegate-payment-allowance"
MAX_PASSPORT_LENGTH = 8192
_CURRENCY = re.compile(r"^[a-z]{3}$")
_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True)
class AllowanceOutcome:
    """``state`` is what the session keeps: ``status`` issued / unavailable / refused, never the passport."""

    state: dict[str, Any]

    @property
    def issued(self) -> bool:
        return self.state.get("status") == "issued"


def passport_sha256(passport_jwt: str) -> str:
    return hashlib.sha256(passport_jwt.encode("utf-8")).hexdigest()


def _clean_allowance(raw: Any, checkout_session_id: str) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    max_amount = raw.get("max_amount")
    currency = raw.get("currency")
    if (
        raw.get("reason") != "one_time"
        or isinstance(max_amount, bool)
        or not isinstance(max_amount, int)
        or max_amount < 0
        or not isinstance(currency, str)
        or not _CURRENCY.match(currency)
        or raw.get("checkout_session_id") != checkout_session_id
        or not isinstance(raw.get("merchant_id"), str)
        or not isinstance(raw.get("expires_at"), str)
    ):
        return None
    return {
        "reason": "one_time",
        "max_amount": max_amount,
        "currency": currency,
        "checkout_session_id": checkout_session_id,
        "merchant_id": str(raw["merchant_id"])[:256],
        "expires_at": str(raw["expires_at"])[:40],
    }


def _clean_risk_signals(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if (
            not isinstance(item, Mapping)
            or item.get("type") != "card_testing"
            or isinstance(item.get("score"), bool)
            or not isinstance(item.get("score"), int)
            or item.get("action") not in ("blocked", "manual_review", "authorized")
        ):
            return None
        out.append({"type": "card_testing", "score": int(item["score"]), "action": str(item["action"])})
    return out


def _clean_metadata(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(k)[:64]: str(val)[:256] for k, val in list(raw.items())[:20] if isinstance(val, str)}


async def request_allowance(
    client: GrantexServiceClient | None,
    *,
    tenant_id: str,
    merchant_id: str,
    checkout_session_id: str,
    passport_jwt: str,
    currency: str | None,
) -> AllowanceOutcome:
    """Ask Grantex once for the session's allowance; every failure is an immediate business outcome."""
    passport_hash = passport_sha256(passport_jwt)
    base = {"passport_sha256": passport_hash}
    if client is None:
        logger.warning("acp_delegate_payment_grantex_not_configured")
        return AllowanceOutcome({**base, "status": "unavailable", "reason": "grantex_not_configured"})
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "passport_jwt": passport_jwt,
        "checkout_session_id": checkout_session_id,
        "merchant_id": merchant_id,
    }
    if currency:
        body["currency"] = currency.lower()
    try:
        response = await client.post_json(ALLOWANCE_PATH, body)
    except GrantexServiceUnavailableError as exc:
        logger.warning("acp_delegate_payment_grantex_unavailable", error_type=type(exc).__name__)
        return AllowanceOutcome({**base, "status": "unavailable", "reason": "grantex_unreachable"})
    if response.status_code >= 500:
        return AllowanceOutcome({**base, "status": "unavailable", "reason": f"grantex_http_{response.status_code}"})
    if response.status_code != 200:
        error = response.body.get("error") if isinstance(response.body.get("error"), Mapping) else {}
        code = str(error.get("code") or "")
        return AllowanceOutcome(
            {
                **base,
                "status": "refused",
                "reason": code if _CODE.match(code) else f"grantex_http_{response.status_code}",
            }
        )
    allowance = _clean_allowance(response.body.get("allowance"), checkout_session_id)
    risk_signals = _clean_risk_signals(response.body.get("risk_signals"))
    if allowance is None or risk_signals is None:
        return AllowanceOutcome({**base, "status": "unavailable", "reason": "grantex_answer_malformed"})
    audit_event_id = response.body.get("audit_event_id")
    return AllowanceOutcome(
        {
            **base,
            "status": "issued",
            "allowance": allowance,
            "risk_signals": risk_signals,
            "metadata": _clean_metadata(response.body.get("metadata")),
            "audit_event_id": str(audit_event_id)[:80] if audit_event_id is not None else None,
        }
    )


def delegate_payment_request_shape(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``{allowance, risk_signals, metadata}`` for the handler config when an allowance was issued."""
    if not state or state.get("status") != "issued":
        return None
    return {
        "allowance": dict(state["allowance"]),
        "risk_signals": [dict(s) for s in state.get("risk_signals") or []],
        "metadata": dict(state.get("metadata") or {}),
    }


def _message(code: str, content: str, *, severity: str = "recoverable", path: str = "$.capabilities") -> dict[str, Any]:
    return {"type": "error", "code": code, "content": content, "severity": severity, "path": path}


def allowance_messages(state: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Messages (internal codes) for a session whose allowance is missing, unavailable or refused."""
    if not state:
        return [
            _message(
                "delegate_payment_passport_missing",
                "A Commerce Passport is required for the delegated-payment allowance.",
                severity="requires_buyer_input",
                path="$.agenticorg_commerce.passport_jwt",
            )
        ]
    status = state.get("status")
    if status == "unavailable":
        return [
            _message(
                "delegate_payment_allowance_unavailable",
                "The delegated-payment allowance could not be obtained now; update the session to retry.",
            )
        ]
    if status == "refused":
        return [
            _message(
                "delegate_payment_allowance_refused",
                f"The Commerce Passport was refused for a delegated-payment allowance ({state.get('reason')}).",
                severity="requires_buyer_input",
                path="$.agenticorg_commerce.passport_jwt",
            )
        ]
    return []


def completion_messages(
    state: Mapping[str, Any] | None, *, total: int, currency: str | None, now: datetime
) -> list[dict[str, Any]]:
    """Every reason the issued allowance does not cover this completion."""
    problems = allowance_messages(state)
    if problems or not state:
        return problems
    allowance = state["allowance"]
    expires = None
    try:
        expires = datetime.fromisoformat(str(allowance["expires_at"]).replace("Z", "+00:00"))
    except ValueError:
        expires = None
    if expires is None or expires.tzinfo is None or expires <= now:
        return [_message("delegate_payment_allowance_expired", "The delegated-payment allowance has expired.")]
    if not currency or allowance["currency"] != currency.lower():
        return [
            _message(
                "delegate_payment_allowance_currency",
                "The delegated-payment allowance is in another currency.",
                severity="unrecoverable",
            )
        ]
    if total > int(allowance["max_amount"]):
        return [
            _message(
                "delegate_payment_allowance_exceeded",
                "The total exceeds the delegated-payment allowance.",
                severity="requires_buyer_review",
                path="$.totals",
            )
        ]
    return []
