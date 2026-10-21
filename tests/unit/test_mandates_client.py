# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The mandate client: Intent, Cart, Payment requested in order with the service token, verified locally.

Against the fake Grantex (``tests/grantex_service_fakes.py``): mandates signed
with an ``oacp-`` key generated here, a Token Status List, refusals by step.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from typing import Any

import pytest

from core.commerce import mandates_client as mc
from core.commerce import oacp_mandates as m
from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches
from tests.grantex_service_fakes import (
    MANDATE_MERCHANT,
    MANDATE_TENANT,
    PASSPORT_JWT,
    FakeGrantexService,
    webauthn_assertion,
)

CHECKOUT = {
    "id": "chk_TEST01",
    "revision": 1,
    "currency": "INR",
    "merchant": {"id": MANDATE_MERCHANT},
    "line_items": [{"id": "li_1", "item": {"id": "TOTE-1", "price": 40000}, "quantity": 1}],
    "totals": [{"type": "total", "amount": 45000}],
}


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_oacp_trust_caches()
    yield
    reset_oacp_trust_caches()


def _request(**overrides: Any) -> mc.MandateChainRequest:
    values: dict[str, Any] = {
        "tenant_id": MANDATE_TENANT,
        "passport_jwt": PASSPORT_JWT,
        "description": "A tote bag under 500 rupees",
        "max_amount_minor_units": 50000,
        "currency": "INR",
        "presence": "human_not_present",
        "checkout": CHECKOUT,
        "rail": "plural",
        "token_binding": {"type": "plural_pine_token", "id": "sha256:" + "a" * 64},
    }
    values.update(overrides)
    return mc.MandateChainRequest(**values)


async def _verify(fake: FakeGrantexService, issued: dict[str, mc.IssuedMandate], **expected: Any):
    client = fake.client()
    now = datetime.now(UTC)
    jwks, reader, local = m.trust_sources(client.verifier, now)
    values = {
        "tenant_id": MANDATE_TENANT,
        "merchant_id": MANDATE_MERCHANT,
        "checkout_hash": m.checkout_hash(CHECKOUT),
        "amount_minor_units": 45000,
        "currency": "INR",
    }
    values.update(expected)
    return await m.verify_mandate_chain(
        {k: v.mandate for k, v in issued.items()},
        issuer=client.issuer,
        jwks=jwks,
        now=now,
        expected=m.MandateExpectation(**values),
        status_reader=reader,
        allow_local_endpoints=local,
    )


async def test_chain_is_requested_in_order_with_the_service_token_and_verifies_locally() -> None:
    fake = FakeGrantexService()
    issued = await mc.request_mandate_chain(_request(), client=fake.client())
    assert [path for path, _ in fake.posted] == [mc.INTENT_PATH, mc.CART_PATH, mc.PAYMENT_PATH]
    intent_body, cart_body, payment_body = (body for _, body in fake.posted)
    assert intent_body["max_amount"] == {"value": 50000, "currency": "INR"}
    assert cart_body["intent_mandate"] == issued["intent"].mandate and cart_body["checkout"] == CHECKOUT
    assert "webauthn_assertion" not in cart_body
    assert payment_body["cart_mandate"] == issued["cart"].mandate
    verified = await _verify(fake, issued)
    assert verified.valid, verified
    assert verified.presence == "human_not_present"
    assert verified.hashes == {k: m.mandate_hash(v.mandate) for k, v in issued.items()}
    ref = m.parse_verification_ref(verified.verification_ref)
    assert ref is not None and ref["jti"] == issued["payment"].jti


async def test_human_present_cart_carries_the_platform_assertion_unchanged() -> None:
    fake = FakeGrantexService()
    assertion = webauthn_assertion()
    issued = await mc.request_mandate_chain(
        _request(presence="human_present", webauthn_assertion=assertion), client=fake.client()
    )
    assert fake.assertions == [assertion]
    verified = await _verify(fake, issued)
    assert verified.valid and verified.presence == "human_present" and "passkey" in verified.amr


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"presence": "human_present"}, "webauthn_assertion"),
        ({"webauthn_assertion": {"type": "public-key"}}, "accepted only"),
        ({"description": " "}, "description"),
        ({"presence": "sometimes"}, "presence"),
    ],
)
async def test_invalid_requests_make_no_call(overrides: dict[str, Any], message: str) -> None:
    fake = FakeGrantexService()
    with pytest.raises(mc.MandateRequestError, match=message):
        await mc.request_mandate_chain(_request(**overrides), client=fake.client())
    assert fake.posted == []


@pytest.mark.parametrize("step", ["intent", "cart", "payment"])
async def test_a_grantex_refusal_names_its_code_and_stops_the_chain(step: str) -> None:
    fake = FakeGrantexService(mandate_refusals={step: "passport_cap_exceeded"})
    with pytest.raises(mc.MandateRequestRefusedError) as raised:
        await mc.request_mandate_chain(_request(), client=fake.client())
    assert (raised.value.mandate_type, raised.value.code) == (step, "passport_cap_exceeded")
    assert raised.value.refusal_record_id == "rr_1"
    assert fake.posted[-1][0].endswith(f"/{step}")


async def test_an_unreachable_grantex_is_unavailable() -> None:
    fake = FakeGrantexService(mode="unreachable")
    with pytest.raises(mc.MandateServiceUnavailableError):
        await mc.request_mandate_chain(_request(), client=fake.client())


async def test_a_revoked_mandate_fails_verification_closed() -> None:
    fake = FakeGrantexService(mandate_statuses={1: 1})
    issued = await mc.request_mandate_chain(_request(), client=fake.client())
    verified = await _verify(fake, issued)
    assert (verified.valid, verified.refusal_code, verified.failed_type) == (False, "mandate_revoked", "cart")


async def test_a_payment_mandate_for_another_amount_is_a_chain_mismatch() -> None:
    fake = FakeGrantexService(payment_amount_skew=1)
    issued = await mc.request_mandate_chain(_request(), client=fake.client())
    verified = await _verify(fake, issued)
    assert verified.refusal_code == m.MANDATE_CHAIN_MISMATCH and verified.verification_ref is None


async def test_a_chain_for_another_checkout_is_not_bound() -> None:
    fake = FakeGrantexService()
    issued = await mc.request_mandate_chain(_request(), client=fake.client())
    verified = await _verify(fake, issued, checkout_hash=m.checkout_hash({**CHECKOUT, "revision": 2}))
    assert verified.refusal_code == m.MANDATE_BINDING_MISMATCH


def test_the_client_never_builds_an_assertion_or_waits() -> None:
    tree = ast.parse(inspect.getsource(mc))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not names & {"sleep", "wait_for", "sign", "generate_private_key"}
    assert not any(isinstance(n, ast.While) for n in ast.walk(tree))
