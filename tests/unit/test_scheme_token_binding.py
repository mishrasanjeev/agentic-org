# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Scheme token binding on the payment capability (G52).

Before G52 a ``mandate_capability`` artifact had no way to say which scheme
token a buyer agent's payment is bound to, and a ``token_binding`` member was
passed through unchecked, card-number-looking ``token_ref`` included. The
binding now has the Grantex shared shape and parse rules, is refused
(``token_binding_invalid``) when malformed or outside its capability, and
reaches the executor on ``ExecutionCheckout.scheme_token_binding``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from core.commerce.executors import ExecutionCheckout
from core.commerce.scheme_token_binding import (
    TOKEN_BINDING_SCHEMES,
    SchemeTokenBinding,
    TokenBindingError,
    check_token_binding_within_capability,
    parse_scheme_token_binding,
)

BINDING: dict[str, Any] = {
    "scheme": "visa",
    "token_ref": "tokref_0001",
    "agent_id": "shopper-01",
    "merchant_scope": ["merchant.example"],
    "limits": {"max_amount": 250000, "currency": "INR", "expires_at": "2026-11-01T00:00:00Z"},
}
CAPABILITY: dict[str, Any] = {
    "buyer_agent_id": "shopper-01",
    "merchant_scope": ["merchant.example", "other.example"],
    "max_amount": 500000,
    "currency": "INR",
}


def test_the_shared_example_parses_and_round_trips() -> None:
    binding = parse_scheme_token_binding(BINDING)
    assert isinstance(binding, SchemeTokenBinding)
    assert binding.to_dict() == BINDING
    assert TOKEN_BINDING_SCHEMES == ("visa", "mastercard", "upi_autopay", "provider_token")
    check_token_binding_within_capability(binding, CAPABILITY, "2026-11-02T00:00:00Z")


@pytest.mark.parametrize(
    "token_ref",
    ["4111111111111111", "card-4111111111111111-x", "5500005555555559", "tok:1234567890123", "a" * 129, "", "tok ref"],
)
def test_card_number_looking_or_malformed_token_refs_are_refused(token_ref: str) -> None:
    with pytest.raises(TokenBindingError) as raised:
        parse_scheme_token_binding({**BINDING, "token_ref": token_ref})
    assert raised.value.code == "token_binding_invalid"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"scheme": "amex"}, "scheme"),
        ({"agent_id": ""}, "agent_id"),
        ({"merchant_scope": []}, "merchant_scope"),
        ({"merchant_scope": ["m"] * 33}, "merchant_scope"),
        ({"merchant_scope": ["m", "m"]}, "twice"),
        ({"limits": {"max_amount": 0, "currency": "INR"}}, "max_amount"),
        ({"limits": {"max_amount": True, "currency": "INR"}}, "max_amount"),
        ({"limits": {"max_amount": 1.5, "currency": "INR"}}, "max_amount"),
        ({"limits": {"max_amount": 1, "currency": "inr"}}, "currency"),
        ({"limits": {"max_amount": 1, "currency": "INR", "expires_at": "tomorrow"}}, "expires_at"),
        ({"limits": {"max_amount": 1, "currency": "INR", "cap": 2}}, "no member cap"),
        ({"extra": 1}, "no member extra"),
    ],
)
def test_malformed_bindings_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(TokenBindingError, match=message):
        parse_scheme_token_binding({**BINDING, **change})


@pytest.mark.parametrize(
    ("capability", "expires", "message"),
    [
        ({**CAPABILITY, "buyer_agent_id": "shopper-02"}, None, "agent_id"),
        ({**CAPABILITY, "merchant_scope": ["other.example"]}, None, "merchant_scope"),
        ({**CAPABILITY, "currency": "USD"}, None, "currency"),
        ({**CAPABILITY, "max_amount": 1000}, None, "max_amount"),
        ({**CAPABILITY, "max_amount": True}, None, "max_amount"),
        ({**CAPABILITY, "max_amount": "500000"}, None, "max_amount"),
        (CAPABILITY, "2026-10-31T00:00:00Z", "expires_at"),
    ],
)
def test_a_binding_outside_its_capability_is_refused(
    capability: dict[str, Any], expires: str | None, message: str
) -> None:
    with pytest.raises(TokenBindingError, match=message):
        check_token_binding_within_capability(parse_scheme_token_binding(BINDING), capability, expires)


def test_the_executor_receives_the_binding_and_defaults_to_none() -> None:
    fields = {f.name: f for f in dataclasses.fields(ExecutionCheckout)}
    assert fields["scheme_token_binding"].default is None
    checkout = ExecutionCheckout(
        session_id="cs-1",
        tenant_id="t",
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        ucp_version="2026-04-08",
        currency="INR",
        total_minor_units=1000,
        line_items=[],
        instruments=[],
        token_binding={"checkout_id": "cs-1"},
        payload_hash="0" * 64,
        scheme_token_binding=parse_scheme_token_binding(BINDING),
    )
    assert checkout.scheme_token_binding is not None and checkout.scheme_token_binding.token_ref == "tokref_0001"


def test_mandate_capability_artifacts_validate_the_token_binding() -> None:
    from core.commerce.oacp_artifacts import (
        OACP_C6W3_VALID_ARTIFACT_FIXTURES,
        hash_oacp_payload,
        validate_oacp_artifact_family,
    )

    fixture = OACP_C6W3_VALID_ARTIFACT_FIXTURES["mandate_capability"]
    envelope, payload = fixture["envelope"], fixture["payload"]
    good = {
        "scheme": "provider_token",
        "token_ref": "tokref_0002",
        "agent_id": payload["buyer_agent_id"],
        "merchant_scope": list(payload["merchant_scope"]),
        "limits": {"max_amount": 100_000, "currency": payload["currency"]},
    }

    def validate(binding: Any) -> dict[str, Any]:
        candidate = {**payload, "token_binding": binding}
        return validate_oacp_artifact_family(
            envelope={**envelope, "payload_hash": hash_oacp_payload(candidate)},
            payload=candidate,
            now_iso="2026-06-11T00:00:00.000Z",
        )

    assert validate(good)["valid"] is True
    for bad in (
        {**good, "token_ref": "4111111111111111"},
        {**good, "agent_id": "someone-else"},
        {**good, "limits": {"max_amount": 10_000_000, "currency": payload["currency"]}},
        "tokref_0002",
    ):
        result = validate(bad)
        assert result["refusal_code"] == "token_binding_invalid", bad
    # Without the member the artifact validates as before.
    assert validate_oacp_artifact_family(envelope=envelope, payload=payload, now_iso="2026-06-11T00:00:00.000Z")[
        "valid"
    ]
