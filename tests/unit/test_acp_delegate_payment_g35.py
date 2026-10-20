# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G35 (AgenticOrg side, ACP): the Grantex delegate-payment allowance per session, in the DelegatePaymentRequest
shape; no card data is sent; Grantex unreachable is an immediate business outcome."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from core.commerce.acp import delegate_payment as dp
from core.commerce.grantex_service import GrantexServiceClient

ROOT = Path(__file__).resolve().parents[2]
DP_SCHEMA = json.loads(
    (ROOT / "core" / "commerce" / "acp" / "schemas" / "2026-04-17" / "schema.delegate_payment.json").read_text(
        encoding="utf-8"
    )
)
SESSION = "chk_0123456789abcdef"
# A synthetic compact JWS (header, payload {"sub": "shopper-01"}, fake signature), assembled at import.
PASSPORT = ".".join(("eyJhbGciOiJFUzI1NiJ9", "eyJzdWIiOiJzaG9wcGVyLTAxIn0", "c2lnbmF0dXJl"))


def _grantex_answer(**overrides) -> dict:
    body = {
        "api_version": "2026-04-17",
        "allowance": {
            "reason": "one_time",
            "max_amount": 50000,
            "currency": "inr",
            "checkout_session_id": SESSION,
            "merchant_id": "merchant.example",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        },
        "risk_signals": [],
        "metadata": {"grantex_passport_jti": "jti-1", "grantex_environment": "sandbox"},
        "authority": {"passport_jti": "jti-1", "checks": ["passport_signature"]},
        "audit_event_id": "cae_1",
    }
    body.update(overrides)
    return body


def _client(handler) -> GrantexServiceClient:
    return GrantexServiceClient(
        base_url="https://grantex.example",
        token="service-token-placeholder",
        verifier=object(),  # not used by post_json
        transport_factory=lambda: httpx.MockTransport(handler),
        allow_local_endpoints=True,
    )


async def _request(client) -> dp.AllowanceOutcome:
    return await dp.request_allowance(
        client,
        tenant_id="00000000-0000-4000-8000-0000000000d1",
        merchant_id="merchant.example",
        checkout_session_id=SESSION,
        passport_jwt=PASSPORT,
        currency="INR",
    )


@pytest.mark.asyncio
async def test_allowance_is_requested_with_the_service_token_and_no_card_data() -> None:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_grantex_answer())

    outcome = await _request(_client(_handler))
    assert outcome.issued
    request = seen[0]
    assert request.url.path == dp.ALLOWANCE_PATH and request.method == "POST"
    assert request.headers["authorization"] == "Bearer service-token-placeholder"
    sent = json.loads(request.content)
    assert set(sent) == {"tenant_id", "passport_jwt", "checkout_session_id", "merchant_id", "currency"}
    assert sent["currency"] == "inr"
    card_keys = {"payment_method", "card", "number", "pan", "cvc", "cryptogram", "billing_address"}
    assert not card_keys & set(sent)
    # The passport is never kept: only its SHA-256.
    assert PASSPORT not in json.dumps(outcome.state)
    assert outcome.state["passport_sha256"] == dp.passport_sha256(PASSPORT)


@pytest.mark.asyncio
async def test_the_allowance_is_shown_in_the_delegate_payment_request_shape() -> None:
    outcome = await _request(_client(lambda _r: httpx.Response(200, json=_grantex_answer())))
    shape = dp.delegate_payment_request_shape(outcome.state)
    assert set(shape) == {"allowance", "risk_signals", "metadata"}
    request = {
        **shape,
        "payment_method": {
            "type": "card",
            "card_number_type": "network_token",
            "number": "4000000000000002",
            "display_card_funding_type": "credit",
            "metadata": {},
        },
    }
    validator = Draft202012Validator(
        {"$schema": DP_SCHEMA["$schema"], "$defs": DP_SCHEMA["$defs"], "$ref": "#/$defs/DelegatePaymentRequest"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    assert [e.message for e in validator.iter_errors(request)] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "status", "reason"),
    [
        (lambda _r: (_ for _ in ()).throw(httpx.ConnectError("down")), "unavailable", "grantex_unreachable"),
        (lambda _r: httpx.Response(503, json={"error": {"code": "unavailable"}}), "unavailable", "grantex_http_503"),
        (lambda _r: httpx.Response(200, text="not json"), "unavailable", "grantex_unreachable"),
        (lambda _r: httpx.Response(200, json=_grantex_answer(allowance={"reason": "x"})), "unavailable", None),
        (
            lambda _r: httpx.Response(200, json=_grantex_answer(risk_signals=[{"type": "velocity"}])),
            "unavailable",
            "grantex_answer_malformed",
        ),
        (
            lambda _r: httpx.Response(422, json={"error": {"code": "passport_cap_missing", "message": "m"}}),
            "refused",
            "passport_cap_missing",
        ),
        (lambda _r: httpx.Response(403, json={"error": {"code": "BAD CODE"}}), "refused", "grantex_http_403"),
    ],
)
async def test_every_grantex_failure_is_an_immediate_business_outcome(handler, status, reason) -> None:
    outcome = await _request(_client(handler))
    assert outcome.state["status"] == status
    if reason:
        assert outcome.state["reason"] == reason
    assert dp.delegate_payment_request_shape(outcome.state) is None
    messages = dp.allowance_messages(outcome.state)
    assert [m["code"] for m in messages] == [
        "delegate_payment_allowance_unavailable" if status == "unavailable" else "delegate_payment_allowance_refused"
    ]


@pytest.mark.asyncio
async def test_no_grantex_configuration_is_unavailable() -> None:
    outcome = await _request(None)
    assert outcome.state["status"] == "unavailable" and outcome.state["reason"] == "grantex_not_configured"


def test_missing_passport_asks_for_one() -> None:
    assert [m["code"] for m in dp.allowance_messages(None)] == ["delegate_payment_passport_missing"]


@pytest.mark.asyncio
async def test_completion_checks_amount_currency_and_expiry() -> None:
    outcome = await _request(_client(lambda _r: httpx.Response(200, json=_grantex_answer())))
    now = datetime.now(UTC)
    assert dp.completion_messages(outcome.state, total=50000, currency="INR", now=now) == []
    over = dp.completion_messages(outcome.state, total=50001, currency="INR", now=now)
    assert [m["code"] for m in over] == ["delegate_payment_allowance_exceeded"]
    other = dp.completion_messages(outcome.state, total=100, currency="USD", now=now)
    assert [m["code"] for m in other] == ["delegate_payment_allowance_currency"]
    later = dp.completion_messages(outcome.state, total=100, currency="INR", now=now + timedelta(hours=2))
    assert [m["code"] for m in later] == ["delegate_payment_allowance_expired"]
    assert [m["code"] for m in dp.completion_messages(None, total=1, currency="INR", now=now)] == [
        "delegate_payment_passport_missing"
    ]
