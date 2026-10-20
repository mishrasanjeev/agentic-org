# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""India payment rails: UPI AutoPay e-mandates and the RBI additional factor of authentication (AFA).

Pure functions and constants: no I/O, no clock (dates and amounts are inputs).
Both protocol surfaces map the same requirement: the UCP checkout binding emits
an ``actions[]`` step (``UCP_AFA_ACTION``) or, when the platform did not
negotiate the extension that declares it, a ``requires_escalation`` message
with a ``continue_url``; the ACP binding maps it with ``acp_afa_out_of_band``.
Either way AFA is the buyer's out-of-band step at the payer PSP (UPI PIN
entry): no agent can perform it and nothing here waits for it. The commerce
arbiter rule ``afa_evidence_present`` (``core.commerce.arbiter``) refuses
completion until server-side AFA evidence exists.

Regulatory sources (public text):

* RBI Master Direction "Digital Payments - E-mandate Framework, 2026"
  (RBI/DPSS/2026-27/396, 21 April 2026), cited ``RBI-EM26``:
  registration needs AFA (s.4(a)); the first transaction needs AFA and may be
  combined with registration (s.5(a)); subsequent debits up to INR 15,000
  (INR 1,00,000 for insurance premiums, mutual fund subscriptions and
  credit-card bill payments) need no AFA, larger ones do (s.8); every mandate
  states a validity period (s.4(b)) and a fixed or variable amount (s.4(c)).
* NPCI AutoPay on UPI (2020): the frequency list below. NPCI's API enum
  strings, the UPI-specific maximum mandate amount and maximum validity are
  not public (NPCI-UNCONFIRMED); they are merchant configuration
  (``provider_policy.ucp.mandate_capability.upi_autopay``), never constants
  here, and a capability without them is not offered.

The AFA thresholds are settings (``AGENTICORG_RBI_EMANDATE_AFA_LIMIT_MINOR_UNITS``,
``AGENTICORG_RBI_EMANDATE_AFA_HIGHER_LIMIT_MINOR_UNITS``) defaulting to the
RBI-EM26 s.8 values in paise, so a later RBI revision is a configuration change.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

RAIL_UPI_AUTOPAY = "upi_autopay"
# The payment instrument ``type`` a platform sends for a UPI AutoPay mandate (handler-defined).
INSTRUMENT_UPI_AUTOPAY = "upi_autopay"
CURRENCY_INR = "INR"

# UCP vendor extension (extends dev.ucp.shopping.checkout) declaring the AFA Action type
# (UCP 2026-08-25 overview, Actions: an Action type may be emitted only while the
# extension declaring it is active). Authority-bound to agenticorg.ai.
UCP_UPI_MANDATE_EXTENSION = "ai.agenticorg.payment.upi_mandate"
UCP_UPI_MANDATE_VERSION = "2026-10-20"
UCP_UPI_MANDATE_SCHEMA = "https://agenticorg.ai/ucp/extensions/upi-mandate/2026-10-20/schema.json"
UCP_AFA_ACTION = "ai.agenticorg.payment.upi_mandate_authorization"
# ACP 2026-04-17 has no UPI or mandate intervention and a closed ``interventions.supported``
# enum, so the step is an ACP extension (reverse-DNS name) plus a requires_escalation session.
ACP_UPI_MANDATE_EXTENSION = "ai.agenticorg.upi_mandate"
ACP_AFA_OUT_OF_BAND = "ai.agenticorg.upi_mandate_authorization"
AFA_ERROR_CODE = "mandate_authorization_required"
AFA_CONTENT = "The buyer must authorise the UPI AutoPay mandate with their UPI PIN before this checkout completes."

# NPCI AutoPay frequencies (NPCI press release, 2020). These are AgenticOrg tokens;
# the NPCI API enum strings are NPCI-UNCONFIRMED and are mapped by the PSP.
FREQUENCIES: tuple[str, ...] = (
    "one_time",
    "daily",
    "weekly",
    "fortnightly",
    "monthly",
    "bimonthly",
    "quarterly",
    "half_yearly",
    "yearly",
)
AMOUNT_RULES: tuple[str, ...] = ("fixed", "max")
# RBI-EM26 s.8(b): the categories with the higher no-AFA limit.
HIGHER_LIMIT_PURPOSES = frozenset({"insurance_premium", "mutual_fund_subscription", "credit_card_bill"})
PURPOSES = frozenset({"general"}) | HIGHER_LIMIT_PURPOSES
# RBI-EM26 s.8(a)-(b) in paise (INR 15,000 and INR 1,00,000).
DEFAULT_AFA_LIMIT_MINOR_UNITS = 1_500_000
DEFAULT_AFA_HIGHER_LIMIT_MINOR_UNITS = 10_000_000

DebitKind = Literal["registration", "subsequent"]
_HTTPS_URL = re.compile(r"^https://[^/?#\s\\@]+(/[^\s\\]*)?$")


class UpiMandateError(ValueError):
    """UPI AutoPay mandate terms that cannot be offered (misconfigured capability, wrong currency)."""


@dataclass(frozen=True)
class AfaThresholds:
    """The RBI-EM26 s.8 amounts above which a subsequent e-mandate debit needs AFA (minor units)."""

    limit_minor_units: int = DEFAULT_AFA_LIMIT_MINOR_UNITS
    higher_limit_minor_units: int = DEFAULT_AFA_HIGHER_LIMIT_MINOR_UNITS


def afa_thresholds_from_settings() -> AfaThresholds:
    from core.config import settings

    return AfaThresholds(
        limit_minor_units=int(getattr(settings, "rbi_emandate_afa_limit_minor_units", DEFAULT_AFA_LIMIT_MINOR_UNITS)),
        higher_limit_minor_units=int(
            getattr(settings, "rbi_emandate_afa_higher_limit_minor_units", DEFAULT_AFA_HIGHER_LIMIT_MINOR_UNITS)
        ),
    )


@dataclass(frozen=True)
class AfaRequirement:
    required: bool
    basis: str


def afa_requirement(
    *,
    debit_kind: DebitKind,
    amount_minor_units: int | None,
    currency: str | None,
    purpose: str | None,
    thresholds: AfaThresholds,
) -> AfaRequirement:
    """Whether RBI AFA applies to this UPI AutoPay payment (fail closed: anything unknown requires it)."""
    if debit_kind == "registration":
        # RBI-EM26 s.4(a) and s.5(a): registration, and the first debit combined with it.
        return AfaRequirement(True, "rbi_em26_registration_and_first_debit")
    if debit_kind != "subsequent":
        return AfaRequirement(True, "debit_kind_unknown")
    if amount_minor_units is None or str(currency or "").upper() != CURRENCY_INR:
        return AfaRequirement(True, "amount_or_currency_unknown")
    limit = (
        thresholds.higher_limit_minor_units
        if (purpose or "general") in HIGHER_LIMIT_PURPOSES
        else thresholds.limit_minor_units
    )
    if amount_minor_units > limit:
        return AfaRequirement(True, "rbi_em26_s8_above_limit")
    return AfaRequirement(False, "rbi_em26_s8_within_limit")


@dataclass(frozen=True)
class UpiAutopayCapability:
    """What the merchant's rail accepts for UPI AutoPay (the NPCI-UNCONFIRMED values are configuration)."""

    amount_rule: str
    max_amount_minor_units: int
    frequency: str
    validity_days: int
    purpose: str
    authorization_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_rule": self.amount_rule,
            "max_amount_minor_units": self.max_amount_minor_units,
            "currency": CURRENCY_INR,
            "frequency": self.frequency,
            "validity_days": self.validity_days,
            "purpose": self.purpose,
            "afa": "rbi_e_mandate_2026",
        }


def parse_upi_autopay_capability(raw: Any) -> UpiAutopayCapability | None:
    """``mandate_capability.upi_autopay`` from merchant config; None when absent or incomplete (not offered).

    ``{amount_rule: fixed|max, max_amount_minor_units, frequency, validity_days,
    purpose?: general|insurance_premium|mutual_fund_subscription|credit_card_bill,
    authorization_url: https URL with {mandate_reference}}``. The authorization URL
    is the merchant's or its PSP's page where the buyer registers the mandate
    with the UPI PIN.
    """
    if not isinstance(raw, Mapping):
        return None
    amount_rule = raw.get("amount_rule")
    frequency = raw.get("frequency")
    purpose = raw.get("purpose") or "general"
    max_amount = raw.get("max_amount_minor_units")
    validity = raw.get("validity_days")
    url = raw.get("authorization_url")
    if amount_rule not in AMOUNT_RULES or frequency not in FREQUENCIES or purpose not in PURPOSES:
        return None
    if isinstance(max_amount, bool) or not isinstance(max_amount, int) or max_amount < 1:
        return None
    if isinstance(validity, bool) or not isinstance(validity, int) or not 1 <= validity <= 3650:
        return None
    if not isinstance(url, str) or not _HTTPS_URL.match(url) or "{mandate_reference}" not in url:
        return None
    return UpiAutopayCapability(
        amount_rule=str(amount_rule),
        max_amount_minor_units=max_amount,
        frequency=str(frequency),
        validity_days=validity,
        purpose=str(purpose),
        authorization_url=url,
    )


@dataclass(frozen=True)
class MandateTerms:
    """The e-mandate the buyer registers (RBI-EM26 s.4(b)-(c)): amount rule and limit, frequency, validity."""

    amount_rule: str
    amount_minor_units: int
    currency: str
    frequency: str
    start_date: str
    end_date: str
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_rule": self.amount_rule,
            "amount": self.amount_minor_units,
            "currency": self.currency,
            "frequency": self.frequency,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "purpose": self.purpose,
        }

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mandate_terms(
    capability: UpiAutopayCapability, *, amount_minor_units: int, currency: str | None, start: date
) -> MandateTerms:
    """The terms for a first debit of ``amount_minor_units``; raises ``UpiMandateError`` when they cannot apply."""
    if str(currency or "").upper() != CURRENCY_INR:
        raise UpiMandateError("UPI AutoPay mandates are in INR")
    if amount_minor_units < 1 or amount_minor_units > capability.max_amount_minor_units:
        raise UpiMandateError("the amount is outside the mandate limit")
    limit = amount_minor_units if capability.amount_rule == "fixed" else capability.max_amount_minor_units
    return MandateTerms(
        amount_rule=capability.amount_rule,
        amount_minor_units=limit,
        currency=CURRENCY_INR,
        frequency=capability.frequency,
        start_date=start.isoformat(),
        end_date=(start + timedelta(days=capability.validity_days)).isoformat(),
        purpose=capability.purpose,
    )


def mandate_reference(tenant_id: str, session_id: str, instrument_id: str) -> str:
    """The merchant mandate reference (A-Z a-z 0-9 - _, at most 50 characters) for one session instrument."""
    digest = hashlib.sha256(f"{tenant_id}|{session_id}|{instrument_id}".encode()).hexdigest()
    return f"aom-{digest[:40]}"


def afa_action_id(reference: str, terms: MandateTerms) -> str:
    """Stable for the same outstanding work; different terms are replacement work with a new id."""
    return f"afa_{hashlib.sha256(f'{reference}|{terms.sha256}'.encode()).hexdigest()[:24]}"


def authorization_url(capability: UpiAutopayCapability, reference: str) -> str:
    return capability.authorization_url.replace("{mandate_reference}", reference)


def evidence_ref(provider: str, provider_mandate_ref: str, reference: str) -> str:
    """The AFA evidence reference sealed into a decision (a digest; the provider's ids stay out of it)."""
    digest = hashlib.sha256(f"{provider}|{provider_mandate_ref}|{reference}".encode()).hexdigest()
    return f"afa:{provider}:{digest[:40]}"


def ucp_afa_action(*, reference: str, instrument_id: str, terms: MandateTerms, url: str) -> dict[str, Any]:
    """One instance of the UCP Action ``UCP_AFA_ACTION`` (``{id, config}``, actions.json)."""
    return {
        "id": afa_action_id(reference, terms),
        "config": {
            "payment_instrument_id": instrument_id,
            "url": url,
            "mandate_reference": reference,
            "mandate": terms.to_dict(),
        },
    }


def ucp_afa_blocking_message(index: int = 0) -> dict[str, Any]:
    """The recoverable error that points at the outstanding Action (UCP 2026-08-25 overview, Actions)."""
    return {
        "type": "error",
        "code": AFA_ERROR_CODE,
        "content": AFA_CONTENT,
        "severity": "recoverable",
        "path": f"$.actions['{UCP_AFA_ACTION}'][{index}]",
    }


def ucp_afa_escalation_message() -> dict[str, Any]:
    """Core UCP fallback: ``requires_escalation`` with this message and the authorization URL as ``continue_url``."""
    return {
        "type": "error",
        "code": AFA_ERROR_CODE,
        "content": AFA_CONTENT,
        "severity": "requires_buyer_input",
        "path": "$.payment",
    }


def acp_afa_out_of_band(*, reference: str, instrument_id: str, terms: MandateTerms, url: str) -> dict[str, Any]:
    """The ACP mapping of the same step (for the ACP binding): a ``requires_escalation`` session.

    ``status``/``continue_url``/``message`` are core ACP members (``MessageError``
    code ``approval_required`` with ``resolution: requires_buyer_input``);
    ``extension`` names the ACP extension whose ``upi_mandate`` member carries
    the terms and the action id.
    """
    return {
        "type": ACP_AFA_OUT_OF_BAND,
        "extension": ACP_UPI_MANDATE_EXTENSION,
        "status": "requires_escalation",
        "continue_url": url,
        "message": {
            "type": "error",
            "code": "approval_required",
            "content_type": "plain",
            "content": "Authorise the UPI AutoPay mandate with your UPI PIN, then return to complete the purchase.",
            "resolution": "requires_buyer_input",
        },
        "upi_mandate": {
            "action_id": afa_action_id(reference, terms),
            "payment_instrument_id": instrument_id,
            "mandate_reference": reference,
            "mandate": terms.to_dict(),
        },
    }


def upi_instruments(instruments: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [i for i in instruments if i.get("type") == INSTRUMENT_UPI_AUTOPAY]
