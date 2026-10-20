# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G56: UPI AutoPay e-mandates on the payment capability, RBI AFA as an out-of-band step, and the arbiter rule.

A session never completes without the AFA evidence ref when AFA applies: the
rule ``afa_evidence_present`` refuses, it never waits, and it reads the
evidence only from the server-side input.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from core.commerce import arbiter, india_rails
from core.commerce.arbiter import AfaEvidence, RefundEvidence, decide
from core.commerce.india_rails import (
    AfaThresholds,
    UpiMandateError,
    acp_afa_out_of_band,
    afa_requirement,
    mandate_reference,
    mandate_terms,
    parse_upi_autopay_capability,
    ucp_afa_action,
)
from tests.unit.test_commerce_arbiter import _context

CAPABILITY = {
    "amount_rule": "max",
    "max_amount_minor_units": 500_000,
    "frequency": "monthly",
    "validity_days": 365,
    "purpose": "general",
    "authorization_url": "https://pay.merchant.example/upi/mandates/{mandate_reference}",
}
LIMITS = AfaThresholds()


def _afa(evidence: str | None = None, **overrides) -> AfaEvidence:
    values = {
        "debit_kind": "registration",
        "amount_minor_units": 5000,
        "currency": "INR",
        "purpose": "general",
        "limit_minor_units": LIMITS.limit_minor_units,
        "higher_limit_minor_units": LIMITS.higher_limit_minor_units,
        "evidence_ref": evidence,
    }
    values.update(overrides)
    return AfaEvidence(**values)


def _upi_payload() -> dict:
    return {"action": "session_complete", "payment": [{"id": "instr_1", "type": "upi_autopay"}]}


# --- RBI thresholds -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "amount", "currency", "purpose", "required", "basis"),
    [
        ("registration", 100, "INR", "general", True, "rbi_em26_registration_and_first_debit"),
        ("subsequent", 1_500_000, "INR", "general", False, "rbi_em26_s8_within_limit"),
        ("subsequent", 1_500_001, "INR", "general", True, "rbi_em26_s8_above_limit"),
        ("subsequent", 10_000_000, "INR", "insurance_premium", False, "rbi_em26_s8_within_limit"),
        ("subsequent", 10_000_001, "INR", "mutual_fund_subscription", True, "rbi_em26_s8_above_limit"),
        ("subsequent", 2_000_000, "INR", "credit_card_bill", False, "rbi_em26_s8_within_limit"),
        ("subsequent", 100, "USD", "general", True, "amount_or_currency_unknown"),
        ("subsequent", None, "INR", "general", True, "amount_or_currency_unknown"),
        ("unknown", 100, "INR", "general", True, "debit_kind_unknown"),
    ],
)
def test_afa_applies_per_rbi_e_mandate_framework(kind, amount, currency, purpose, required, basis) -> None:
    result = afa_requirement(
        debit_kind=kind, amount_minor_units=amount, currency=currency, purpose=purpose, thresholds=LIMITS
    )
    assert (result.required, result.basis) == (required, basis)


def test_thresholds_are_configuration(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "rbi_emandate_afa_limit_minor_units", 1000)
    assert india_rails.afa_thresholds_from_settings().limit_minor_units == 1000
    assert india_rails.afa_thresholds_from_settings().higher_limit_minor_units == 10_000_000


# --- capability and terms -------------------------------------------------------------------------


def test_capability_needs_every_npci_unconfirmed_value_configured() -> None:
    parsed = parse_upi_autopay_capability(CAPABILITY)
    assert parsed is not None and parsed.to_dict()["currency"] == "INR"
    for missing in ("amount_rule", "max_amount_minor_units", "frequency", "validity_days", "authorization_url"):
        assert parse_upi_autopay_capability({k: v for k, v in CAPABILITY.items() if k != missing}) is None
    assert parse_upi_autopay_capability({**CAPABILITY, "frequency": "hourly"}) is None
    assert parse_upi_autopay_capability({**CAPABILITY, "authorization_url": "http://pay.merchant.example/{x}"}) is None
    assert parse_upi_autopay_capability({**CAPABILITY, "authorization_url": "https://pay.merchant.example/"}) is None


def test_terms_carry_validity_amount_rule_and_frequency() -> None:
    capability = parse_upi_autopay_capability(CAPABILITY)
    terms = mandate_terms(capability, amount_minor_units=7500, currency="INR", start=date(2026, 10, 20))
    assert terms.to_dict() == {
        "amount_rule": "max",
        "amount": 500_000,
        "currency": "INR",
        "frequency": "monthly",
        "start_date": "2026-10-20",
        "end_date": "2027-10-20",
        "purpose": "general",
    }
    fixed = parse_upi_autopay_capability({**CAPABILITY, "amount_rule": "fixed"})
    fixed_terms = mandate_terms(fixed, amount_minor_units=7500, currency="INR", start=date(2026, 10, 20))
    assert fixed_terms.amount_minor_units == 7500
    with pytest.raises(UpiMandateError):
        mandate_terms(capability, amount_minor_units=7500, currency="USD", start=date(2026, 10, 20))
    with pytest.raises(UpiMandateError):
        mandate_terms(capability, amount_minor_units=500_001, currency="INR", start=date(2026, 10, 20))


def test_merchant_capability_offers_upi_autopay_only_with_complete_terms() -> None:
    from tests import ucp_txn_fixtures as uf

    tenant = "11111111-1111-4111-8111-111111111111"
    base = uf.ucp_settings()["mandate_capability"]
    with_upi = {**base, "instrument_types": ["card", "upi_autopay"], "upi_autopay": CAPABILITY}
    merchant = uf.merchant(tenant, mandate_capability=with_upi)
    assert merchant.mandate_capability.instrument_types == ("card", "upi_autopay")
    assert merchant.mandate_capability.to_dict()["upi_autopay"]["frequency"] == "monthly"
    incomplete = {**with_upi, "upi_autopay": {"amount_rule": "max"}}
    assert uf.merchant(tenant, mandate_capability=incomplete).mandate_capability.instrument_types == ("card",)


# --- the out-of-band step on both protocols ---------------------------------------------------------


def test_ucp_action_and_acp_mapping_carry_the_same_step() -> None:
    capability = parse_upi_autopay_capability(CAPABILITY)
    terms = mandate_terms(capability, amount_minor_units=7500, currency="INR", start=date(2026, 10, 20))
    reference = mandate_reference("tenant", "chk_1", "instr_1")
    assert reference.startswith("aom-") and len(reference) <= 50
    url = india_rails.authorization_url(capability, reference)
    action = ucp_afa_action(reference=reference, instrument_id="instr_1", terms=terms, url=url)
    assert action["config"]["payment_instrument_id"] == "instr_1"
    assert action["config"]["url"] == f"https://pay.merchant.example/upi/mandates/{reference}"
    assert action["id"] == ucp_afa_action(reference=reference, instrument_id="instr_1", terms=terms, url=url)["id"]
    changed = mandate_terms(capability, amount_minor_units=7500, currency="INR", start=date(2026, 10, 21))
    assert ucp_afa_action(reference=reference, instrument_id="instr_1", terms=changed, url=url)["id"] != action["id"]
    message = india_rails.ucp_afa_blocking_message()
    assert message["severity"] == "recoverable"
    assert message["path"] == f"$.actions['{india_rails.UCP_AFA_ACTION}'][0]"
    assert india_rails.ucp_afa_escalation_message()["severity"] == "requires_buyer_input"
    acp = acp_afa_out_of_band(reference=reference, instrument_id="instr_1", terms=terms, url=url)
    assert acp["type"] == india_rails.ACP_AFA_OUT_OF_BAND and acp["status"] == "requires_escalation"
    assert acp["continue_url"] == url
    assert acp["message"]["code"] == "approval_required" and acp["message"]["resolution"] == "requires_buyer_input"
    assert acp["upi_mandate"]["action_id"] == action["id"]


# --- the arbiter rule -----------------------------------------------------------------------------


def test_session_complete_evaluates_afa_last_and_purchase_handoff_does_not() -> None:
    assert arbiter.RULES_BY_ACTION["session_complete"] == (*arbiter.RULE_ORDER, "afa_evidence_present")
    assert "afa_evidence_present" not in arbiter.RULES_BY_ACTION["purchase_handoff"]
    assert arbiter.POLICY_VERSION == "commerce-arbiter-v4"


def test_no_upi_instrument_passes_without_afa_input() -> None:
    decision = decide("session_complete", _context(payload={"action": "session_complete", "payment": []}))
    assert decision.outcome == "allow"
    result = next(r for r in decision.rule_results if r.rule == "afa_evidence_present")
    assert result.reason == "afa_not_applicable"


def test_upi_instrument_without_afa_input_refuses() -> None:
    decision = decide("session_complete", _context(payload=_upi_payload()))
    assert decision.outcome == "refuse" and decision.failing_rule == "afa_evidence_present"
    assert decision.reason == "afa_input_missing"


def test_session_never_completes_without_afa_evidence_ref() -> None:
    refused = decide("session_complete", _context(payload=_upi_payload(), afa=_afa(None)))
    assert refused.outcome == "refuse"
    assert refused.failing_rule == "afa_evidence_present" and refused.reason == "afa_evidence_missing"
    allowed = decide("session_complete", _context(payload=_upi_payload(), afa=_afa("afa:plural_pine:" + "e" * 40)))
    assert allowed.outcome == "allow"
    assert any(h.startswith("afa:") for h in allowed.evidence_hashes)
    assert allowed.decision_id != refused.decision_id


def test_subsequent_debit_within_the_limit_needs_no_evidence() -> None:
    within = _afa(None, debit_kind="subsequent", amount_minor_units=1_500_000)
    assert decide("session_complete", _context(payload=_upi_payload(), afa=within)).outcome == "allow"
    above = dataclasses.replace(within, amount_minor_units=1_500_001)
    assert decide("session_complete", _context(payload=_upi_payload(), afa=above)).outcome == "refuse"


# --- refund rule and mandate hashes ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "currency", "captured", "refunded", "outcome", "reason"),
    [
        (2000, "INR", 5000, 0, "allow", "all_rules_passed"),
        (5000, "INR", 5000, 0, "allow", "all_rules_passed"),
        (3001, "INR", 5000, 2000, "refuse", "refund_exceeds_captured"),
        (100, "INR", 0, 0, "refuse", "order_not_captured"),
        (100, "USD", 5000, 0, "refuse", "refund_currency_mismatch"),
        (0, "INR", 5000, 0, "refuse", "refund_amount_invalid"),
    ],
)
def test_refund_is_bounded_by_the_captured_amount(amount, currency, captured, refunded, outcome, reason) -> None:
    context = _context(
        payload={"action": "refund", "order_id": "ord_1", "amount": amount},
        amount_minor_units=amount,
        currency=currency,
        refund=RefundEvidence(
            order_id="ord_1", currency="INR", captured_minor_units=captured, refunded_minor_units=refunded
        ),
    )
    decision = decide("refund", context)
    assert decision.rules_evaluated == ("refund_within_captured",)
    assert (decision.outcome, decision.reason) == (outcome, reason)


def test_refund_without_order_state_refuses() -> None:
    decision = decide("refund", _context())
    assert decision.outcome == "refuse" and decision.reason == "refund_input_missing"


def test_mandate_hashes_are_sealed_only_when_well_formed() -> None:
    decision = decide(
        "purchase_handoff", _context(mandate_hashes={"intent": "a" * 64, "cart": "NOT-A-HASH", "payment": None})
    )
    assert decision.extra["mandate_hashes"] == {"intent": "a" * 64, "cart": None, "payment": None}
    assert decide("purchase_handoff", _context()).extra["mandate_hashes"] == {
        "intent": None,
        "cart": None,
        "payment": None,
    }
