# SPDX-License-Identifier: Apache-2.0
"""G31 + G35: payment instruments, credentials (PAN vs network token), split payments, terms, request constraints,
token binding, and the Plural tokenised-instrument handler (allowance = mandate_capability.max_amount)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from core.commerce.payment_handlers import registered_handlers, resolve_handler, satisfies
from core.commerce.payment_handlers.plural import HANDLER_ID, HANDLER_NAME
from core.commerce.ucp import payment as p
from core.commerce.ucp.capabilities import DEFAULT_REGISTRY
from core.commerce.ucp.renderers import v2026_08_25
from tests import ucp_fixtures as uf
from tests import ucp_schema

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a5"))


def _instruments(*credentials, amounts=None):
    raw = []
    for index, credential in enumerate(credentials):
        item = {"id": f"i{index}", "handler_id": HANDLER_ID, "type": "card", "credential": credential}
        if amounts:
            item["amount"] = amounts[index]
        raw.append(item)
    return p.parse_payment({"instruments": raw})[0]


def _codes(messages):
    return [m["code"] for m in messages]


def test_credentials_are_never_stored_or_echoed() -> None:
    instrument = _instruments({"type": "plural_pine_token", "token": "tok_secret_1"})[0]
    stored = instrument.stored()
    assert "credential" not in stored and "tok_secret_1" not in str(stored)
    assert stored["credential_type"] == "plural_pine_token" and len(stored["credential_sha256"]) == 64
    rendered = v2026_08_25.instrument(stored)
    assert set(rendered) <= {"id", "handler_id", "type", "display", "billing_address", "selected", "amount"}


@pytest.mark.parametrize("version", ["2026-04-08", "2026-08-25"])
def test_pan_is_refused_by_the_tokenised_handler(version: str) -> None:
    merchant = uf.merchant(TENANT)
    pan = {"type": "pan", "number": "4111111111111111", "cvc": "123"}
    fpan = {"type": "card", "card_number_type": "fpan", "number": "4111111111111111"}
    for credential in (pan, fpan):
        messages = p.completion_messages(
            _instruments(credential), merchant=merchant, version=version, split_active=False, total=7500, currency="INR"
        )
        assert _codes(messages) == ["payment_failed"]


def test_network_token_needs_cryptogram_and_meets_request_constraints() -> None:
    merchant = uf.merchant(TENANT)
    ok = p.completion_messages(
        _instruments({"type": "network_token", "number": "4895370012003478", "cryptogram": "AgAAAAAA"}),
        merchant=merchant,
        version="2026-08-25",
        split_active=False,
        total=7500,
        currency="INR",
    )
    assert ok == []
    missing = p.completion_messages(
        _instruments({"type": "network_token", "number": "4895370012003478"}),
        merchant=merchant,
        version="2026-08-25",
        split_active=False,
        total=7500,
        currency="INR",
    )
    assert _codes(missing) == ["payment_failed"]
    r08 = p.completion_messages(
        _instruments({"type": "card", "card_number_type": "network_token", "number": "4895", "cryptogram": "A"}),
        merchant=merchant,
        version="2026-04-08",
        split_active=False,
        total=7500,
        currency="INR",
    )
    assert r08 == []


def test_exactly_one_instrument_unless_split_payments() -> None:
    merchant = uf.merchant(TENANT)
    token = {"type": "plural_pine_token", "token": "t"}
    two = _instruments(token, token, amounts=[5000, 2500])
    assert _codes(
        p.completion_messages(
            two, merchant=merchant, version="2026-08-25", split_active=False, total=7500, currency="INR"
        )
    ) == ["payment_failed"]
    assert (
        p.completion_messages(
            two, merchant=merchant, version="2026-08-25", split_active=True, total=7500, currency="INR"
        )
        == []
    )
    wrong_sum = _instruments(token, token, amounts=[5000, 2000])
    assert _codes(
        p.completion_messages(
            wrong_sum, merchant=merchant, version="2026-08-25", split_active=True, total=7500, currency="INR"
        )
    ) == ["payment_failed"]


def test_allowance_is_the_mandate_capability_cap() -> None:
    merchant = uf.merchant(TENANT)
    token = {"type": "plural_pine_token", "token": "t"}
    over = p.completion_messages(
        _instruments(token), merchant=merchant, version="2026-08-25", split_active=False, total=50001, currency="INR"
    )
    assert _codes(over) == ["payment_failed"]
    other_currency = p.completion_messages(
        _instruments(token), merchant=merchant, version="2026-08-25", split_active=False, total=100, currency="USD"
    )
    assert _codes(other_currency) == ["payment_failed"]


def test_unknown_handler_and_missing_credential() -> None:
    merchant = uf.merchant(TENANT)
    unknown = p.parse_payment({"instruments": [{"id": "x", "handler_id": "nope", "type": "card"}]})[0]
    assert _codes(p.validate_instruments(unknown, merchant=merchant, version="2026-04-08")) == [
        "payment_handler_unavailable"
    ]
    bare = p.parse_payment({"instruments": [{"id": "x", "handler_id": HANDLER_ID, "type": "card"}]})[0]
    assert _codes(
        p.completion_messages(
            bare, merchant=merchant, version="2026-04-08", split_active=False, total=1, currency="INR"
        )
    ) == ["payment_failed"]
    with pytest.raises(p.PaymentRequestError):
        p.parse_payment({"instruments": [{"id": "x", "type": "card"}]})


def test_terms_schedules_and_token_binding() -> None:
    merchant = uf.merchant(TENANT)
    terms = p.payment_terms(merchant, 7501, datetime(2026, 10, 20, tzinfo=UTC))
    assert [s["amount"] for s in terms[0]["schedules"]] == [3750, 3751]
    assert terms[0]["schedules"][1]["due_at"] == "2026-11-19T00:00:00Z"
    ucp_schema.assert_valid("2026-08-25", "common/types/payment_term.json", terms[0])
    assert p.token_binding("2026-08-25", "chk_1") == {"type": "dev.ucp.shopping.checkout", "id": "chk_1"}
    assert p.token_binding("2026-04-08", "chk_1") == {"checkout_id": "chk_1"}
    ucp_schema.assert_valid("2026-08-25", "common/types/binding.json", p.token_binding("2026-08-25", "chk_1"))
    ucp_schema.assert_valid("2026-04-08", "shopping/types/binding.json", p.token_binding("2026-04-08", "chk_1"))


def test_plural_handler_entry_carries_mandate_capability_and_allowance() -> None:
    merchant = uf.merchant(TENANT)
    assert HANDLER_NAME in registered_handlers()
    entries = DEFAULT_REGISTRY.payment_handlers(merchant, "2026-08-25")
    entry = next(e for e in entries if e.name == HANDLER_NAME)
    assert entry.config["allowance"] == {"max_amount": 50000, "currency": "INR"}
    assert entry.config["mandate_capability"]["rails"] == ["card", "upi"]
    assert entry.config["mandate_capability"]["instrument_types"] == ["card"]
    assert "pan" not in entry.config["accepted_credentials"]
    rendered = v2026_08_25.payment_handlers(entries)[HANDLER_NAME][0]
    constraint = rendered["available_instruments"][0]["ucp"]["request_constraints"]
    ucp_schema.assert_valid("2026-08-25", "common/types/request_constraints.json", constraint)
    candidate = {"handler_id": HANDLER_ID, "type": "card", "credential": {"type": "plural_pine_token", "token": "x"}}
    body = {k: val for k, val in constraint.items() if k != "path"}
    assert satisfies(candidate, body)
    assert not satisfies({**candidate, "credential": {"type": "network_token"}}, body)
    ucp_schema.assert_valid("2026-08-25", "payment_handler.json", entry.render(), pointer="/$defs/business_schema")


def test_plural_handler_absent_without_provider_or_capability() -> None:
    no_cap = uf.merchant(TENANT, mandate_capability=None)
    assert resolve_handler(no_cap, "2026-08-25", HANDLER_ID) is None
    config = uf.merchant_config(TENANT)
    config["payment_providers"] = []
    from core.commerce.ucp.merchant import merchant_ucp_from_config

    no_provider = merchant_ucp_from_config(config, tenant_id=TENANT)
    assert resolve_handler(no_provider, "2026-04-08", HANDLER_ID) is None
