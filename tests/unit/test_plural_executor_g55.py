# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G55: the Plural executor calls Plural only under a consumed decision, within bounds, idempotently.

A fake Plural API (httpx MockTransport) counts every request; the journal is in memory.
The database-backed paths (decision consumption, ledger rows) are in
tests/integration/test_plural_executor_postgres.py.
"""

from __future__ import annotations

import json
import time

import pytest

from core.commerce import executors
from core.commerce.executors import plural
from core.commerce.executors.plural import (
    PluralExecutor,
    PluralWebhookError,
    RefundRequest,
    afa_outcome,
    credentials_from_env,
    order_reference,
    refund_reference,
    sync_registration,
    verify_webhook,
)
from tests.plural_fakes import WEBHOOK_SECRET, FakePlural, MemoryJournal, credentials, signed_headers

TENANT = "11111111-1111-4111-8111-111111111111"
PAYLOAD_HASH = "a" * 64
DECISION_ID = "arbdec_" + "1" * 40


def decision(action: str = executors.SESSION_COMPLETE, **overrides) -> executors.ConsumedDecision:
    values = {
        "decision_id": DECISION_ID,
        "payload_hash": PAYLOAD_HASH,
        "tenant_id": TENANT,
        "merchant_id": "merchant-1",
        "seller_agent_id": "seller-1",
        "action": action,
        "mandate_hashes": {"intent": None, "cart": "c" * 64, "payment": None},
    }
    values.update(overrides)
    return executors.ConsumedDecision(**values, _gate=executors._GATE)


def instrument(**overrides) -> executors.ExecutionInstrument:
    values = {
        "id": "instr_1",
        "handler_id": "plural_tokenized",
        "type": "card",
        "credential": {"type": "plural_pine_token", "token": "tok_example_1"},
    }
    values.update(overrides)
    return executors.ExecutionInstrument(**values)


def checkout(**overrides) -> executors.ExecutionCheckout:
    values = {
        "session_id": "chk_1",
        "tenant_id": TENANT,
        "merchant_id": "merchant-1",
        "seller_agent_id": "seller-1",
        "ucp_version": "2026-04-08",
        "currency": "INR",
        "total_minor_units": 7500,
        "line_items": [],
        "instruments": [instrument()],
        "token_binding": {"checkout_id": "chk_1"},
        "payload_hash": PAYLOAD_HASH,
        "allowance_max_minor_units": 50000,
        "allowance_currency": "INR",
    }
    values.update(overrides)
    return executors.ExecutionCheckout(**values)


def executor(fake: FakePlural, journal: MemoryJournal | None = None, *, creds=credentials) -> PluralExecutor:
    return PluralExecutor(credentials=creds, transport_factory=fake.factory(), journal=journal or MemoryJournal())


@pytest.mark.asyncio
async def test_complete_creates_order_pays_with_token_and_captures_with_decision_metadata() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    result = await executor(fake, journal).execute(checkout(), decision())
    assert result.status == "completed" and result.provider_order_ref == "v1-ord-1"
    paths = [(r.method, r.url.path) for r in fake.api_calls]
    assert paths == [
        ("POST", "/api/pay/v1/orders"),
        ("POST", "/api/pay/v1/orders/v1-ord-1/payments"),
        ("PUT", "/api/pay/v1/orders/v1-ord-1/capture"),
    ]
    order = json.loads(fake.api_calls[0].content)
    reference = order_reference("chk_1", DECISION_ID)
    assert order["merchant_order_reference"] == reference and len(reference) <= 50
    assert order["order_amount"] == {"value": 7500, "currency": "INR"} and order["pre_auth"] is True
    metadata = order["purchase_details"]["merchant_metadata"]
    assert metadata["oacp_decision_id"] == DECISION_ID
    assert metadata["oacp_payload_hash"] == PAYLOAD_HASH
    # The three mandate hashes are always present; populated once verified mandates land.
    assert metadata["oacp_intent_mandate_hash"] == "" and metadata["oacp_cart_mandate_hash"] == "c" * 64
    assert metadata["oacp_payment_mandate_hash"] == ""
    payment = json.loads(fake.api_calls[1].content)["payments"][0]
    assert payment["payment_option"]["card_token_details"]["token"] == "tok_example_1"
    capture = json.loads(fake.api_calls[2].content)
    assert capture["capture_amount"] == {"value": 7500, "currency": "INR"}
    # Request-ID is derived from the reference and the operation (stable on a retry of the same call).
    assert fake.api_calls[0].headers["request-id"] != fake.api_calls[1].headers["request-id"]
    assert fake.api_calls[0].headers["authorization"] == "Bearer access-test"
    phases = [(r.operation, r.phase) for r in journal.rows]
    assert phases == [
        ("create_order", "requested"),
        ("create_order", "succeeded"),
        ("create_payment", "requested"),
        ("create_payment", "succeeded"),
        ("capture", "requested"),
        ("capture", "succeeded"),
    ]
    assert {r.decision_id for r in journal.rows} == {DECISION_ID}


def test_no_decision_no_executor_call_a_consumed_decision_cannot_be_forged() -> None:
    with pytest.raises(TypeError):
        executors.ConsumedDecision(
            decision_id=DECISION_ID, payload_hash=PAYLOAD_HASH, tenant_id=TENANT, merchant_id="m", seller_agent_id="s"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_overrides", "decision_overrides", "reason"),
    [
        ({}, {"action": executors.REFUND}, "decision_action_mismatch"),
        ({}, {"payload_hash": "b" * 64}, "decision_scope_mismatch"),
        ({}, {"tenant_id": "22222222-2222-4222-8222-222222222222"}, "decision_scope_mismatch"),
        ({"total_minor_units": 60000}, {}, "amount_exceeds_allowance"),
        ({"allowance_max_minor_units": None}, {}, "allowance_missing"),
        ({"allowance_currency": "USD"}, {}, "currency_mismatch"),
        ({"total_minor_units": 0}, {}, "amount_invalid"),
        ({"instruments": [instrument(), instrument(id="instr_2")]}, {}, "single_plural_instrument_required"),
        ({"instruments": [instrument(handler_id="other")]}, {}, "single_plural_instrument_required"),
        ({"instruments": [instrument(credential={"type": "pan", "number": "4111"})]}, {}, "pan_not_accepted"),
        ({"instruments": [instrument(amount_minor_units=100)]}, {}, "instrument_amount_mismatch"),
        ({"instruments": [instrument(type="upi_autopay")]}, {}, "afa_evidence_missing"),
    ],
)
async def test_refused_before_any_http_call(session_overrides, decision_overrides, reason) -> None:
    fake, journal = FakePlural(), MemoryJournal()
    result = await executor(fake, journal).execute(checkout(**session_overrides), decision(**decision_overrides))
    assert result.status == "failed"
    assert fake.requests == []
    assert journal.rows == []
    assert journal.blocked_reasons == [("create_order", reason)]


@pytest.mark.asyncio
async def test_missing_credentials_refuses_without_a_call() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    result = await executor(fake, journal, creds=lambda: None).execute(checkout(), decision())
    assert result.status == "failed" and fake.requests == []
    assert journal.blocked_reasons == [("create_order", "plural_credentials_missing")]


@pytest.mark.asyncio
async def test_replayed_decision_is_refused_by_the_journal_without_a_second_order() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    first = await executor(fake, journal).execute(checkout(), decision())
    assert first.status == "completed"
    calls = len(fake.requests)
    again = await executor(fake, journal).execute(checkout(), decision())
    assert again.status == "failed"
    assert len(fake.requests) == calls  # no HTTP call at all for the replay


@pytest.mark.asyncio
async def test_upi_autopay_with_afa_evidence_pays_through_the_mandate() -> None:
    fake = FakePlural()
    session = checkout(
        instruments=[instrument(type="upi_autopay")],
        upi_mandate_reference="aom-" + "d" * 40,
        afa_evidence_ref="afa:plural_pine:" + "e" * 40,
    )
    result = await executor(fake).execute(session, decision())
    assert result.status == "completed"
    order = json.loads(fake.api_calls[0].content)
    assert order["allowed_payment_methods"] == ["UPI"]
    option = json.loads(fake.api_calls[1].content)["payments"][0]["payment_option"]
    assert option["upi_details"]["mandate_reference"] == "aom-" + "d" * 40


@pytest.mark.asyncio
async def test_three_ds_challenge_is_an_action_and_nothing_is_captured() -> None:
    fake = FakePlural(payment_status="PENDING", challenge_url="https://plural.example/3ds/abc")
    result = await executor(fake).execute(checkout(), decision())
    assert result.status == "action_required"
    assert result.escalation_url == "https://plural.example/3ds/abc"
    assert not any(r.url.path.endswith("/capture") for r in fake.requests)


@pytest.mark.asyncio
async def test_declined_payment_fails_and_never_captures() -> None:
    fake, journal = FakePlural(payment_http_status=402), MemoryJournal()
    result = await executor(fake, journal).execute(checkout(), decision())
    assert result.status == "failed" and result.error_code == "payment_declined"
    assert not any(r.url.path.endswith("/capture") for r in fake.requests)
    assert ("create_payment", "failed") in [(r.operation, r.phase) for r in journal.rows]


@pytest.mark.asyncio
async def test_capture_is_bounded_by_the_authorized_amount() -> None:
    fake, journal = FakePlural(authorized_amount=7000), MemoryJournal()
    result = await executor(fake, journal).execute(checkout(), decision())
    assert result.status == "failed"
    assert not any(r.url.path.endswith("/capture") for r in fake.requests)
    assert journal.blocked_reasons == [("capture", "authorized_amount_mismatch")]


@pytest.mark.asyncio
async def test_unreachable_plural_records_an_unknown_outcome_and_does_not_retry() -> None:
    import httpx

    def _boom(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v1/token"):
            return httpx.Response(200, json={"access_token": "access-test"})
        raise httpx.ConnectError("down")

    journal = MemoryJournal()
    ex = PluralExecutor(credentials=credentials, transport_factory=lambda: httpx.MockTransport(_boom), journal=journal)
    result = await ex.execute(checkout(), decision())
    assert result.status == "failed"
    phases = [(r.operation, r.phase) for r in journal.rows]
    assert phases == [("create_order", "requested"), ("create_order", "unknown")]


# --- refunds -------------------------------------------------------------------------------------


async def _captured(fake: FakePlural, journal: MemoryJournal) -> None:
    assert (await executor(fake, journal).execute(checkout(), decision())).status == "completed"


def refund_request(amount: int, **overrides) -> RefundRequest:
    values = {
        "tenant_id": TENANT,
        "merchant_id": "merchant-1",
        "seller_agent_id": "seller-1",
        "order_id": "ord_1",
        "checkout_session_id": "chk_1",
        "payload_hash": "f" * 64,
        "amount_minor_units": amount,
        "currency": "INR",
        "order_total_minor_units": 7500,
        "allowance_max_minor_units": 50000,
    }
    values.update(overrides)
    return RefundRequest(**values)


def refund_decision(n: int = 2, **overrides) -> executors.ConsumedDecision:
    values = {"action": executors.REFUND, "decision_id": f"arbdec_{str(n) * 40}", "payload_hash": "f" * 64}
    values.update(overrides)
    return decision(**values)


@pytest.mark.asyncio
async def test_partial_refunds_are_bounded_by_the_captured_amount() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    await _captured(fake, journal)
    first = await executor(fake, journal).refund(refund_request(5000), refund_decision(2))
    assert first.status == "succeeded" and first.provider_refund_ref == "rf-1"
    body = json.loads(fake.api_calls[-1].content)
    assert body["refund_amount"] == {"value": 5000, "currency": "INR"}
    assert body["merchant_metadata"]["oacp_decision_id"] == "arbdec_" + "2" * 40
    assert body["merchant_order_reference"] == refund_reference("ord_1", "arbdec_" + "2" * 40)
    calls = len(fake.requests)
    over = await executor(fake, journal).refund(refund_request(3000), refund_decision(3))
    assert over.status == "refused" and over.reason == "refund_exceeds_captured"
    assert len(fake.requests) == calls
    rest = await executor(fake, journal).refund(refund_request(2500), refund_decision(4))
    assert rest.status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount", "request_overrides", "decision_overrides", "reason"),
    [
        (100, {}, {"action": executors.SESSION_COMPLETE}, "decision_action_mismatch"),
        (100, {"payload_hash": "0" * 64}, {}, "decision_scope_mismatch"),
        (100, {"allowance_max_minor_units": 50}, {}, "amount_exceeds_allowance"),
        (100, {"allowance_max_minor_units": None}, {}, "allowance_missing"),
        (100, {"currency": "USD"}, {}, "currency_mismatch"),
        (8000, {}, {}, "refund_exceeds_captured"),
    ],
)
async def test_refund_refused_before_any_call(amount, request_overrides, decision_overrides, reason) -> None:
    fake, journal = FakePlural(), MemoryJournal()
    await _captured(fake, journal)
    calls = len(fake.requests)
    result = await executor(fake, journal).refund(
        refund_request(amount, **request_overrides), refund_decision(**decision_overrides)
    )
    assert result.status == "refused" and result.reason == reason
    assert len(fake.requests) == calls


@pytest.mark.asyncio
async def test_refund_without_capture_is_refused() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    result = await executor(fake, journal).refund(refund_request(100), refund_decision())
    assert result.status == "refused" and fake.requests == []


@pytest.mark.asyncio
async def test_pending_refund_keeps_its_amount_reserved() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    await _captured(fake, journal)
    fake.refund_status = "PENDING"
    pending = await executor(fake, journal).refund(refund_request(7000), refund_decision(2))
    assert pending.status == "unknown"
    over = await executor(fake, journal).refund(refund_request(1000), refund_decision(3))
    assert over.status == "refused" and over.reason == "refund_exceeds_captured"


@pytest.mark.asyncio
async def test_replayed_refund_decision_is_refused_without_a_call() -> None:
    fake, journal = FakePlural(), MemoryJournal()
    await _captured(fake, journal)
    assert (await executor(fake, journal).refund(refund_request(100), refund_decision(2))).status == "succeeded"
    calls = len(fake.requests)
    again = await executor(fake, journal).refund(refund_request(100), refund_decision(2))
    assert again.status == "refused" and again.reason == "operation_already_requested"
    assert len(fake.requests) == calls


# --- webhooks ------------------------------------------------------------------------------------


def _mandate_event(status: str = "ACTIVE") -> bytes:
    return json.dumps(
        {
            "event_type": "MANDATE_STATUS",
            "data": {
                "mandate": {"merchant_mandate_reference": "aom-" + "d" * 40, "mandate_id": "md-1", "status": status}
            },
        }
    ).encode()


def test_webhook_with_a_valid_signature_verifies_and_carries_the_mandate_outcome() -> None:
    body = _mandate_event()
    now = int(time.time())
    event = verify_webhook(body, signed_headers(body, timestamp=now), secret=WEBHOOK_SECRET, now=now)
    assert afa_outcome(event) == ("aom-" + "d" * 40, True, "md-1")
    rejected = verify_webhook(
        _mandate_event("REJECTED"), signed_headers(_mandate_event("REJECTED"), timestamp=now), secret=WEBHOOK_SECRET
    )
    assert afa_outcome(rejected) == ("aom-" + "d" * 40, False, "md-1")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda h, b: ({**h, "webhook-signature": "v1,AAAA"}, b), "signature_invalid"),
        (lambda h, b: (h, b + b" "), "signature_invalid"),
        (lambda h, b: ({k: v for k, v in h.items() if k != "webhook-id"}, b), "headers_missing"),
        (lambda h, b: ({**h, "webhook-timestamp": "yesterday"}, b), "timestamp_invalid"),
    ],
)
def test_webhook_bad_signature_or_headers_is_rejected(mutate, code) -> None:
    body = _mandate_event()
    now = int(time.time())
    headers, sent = mutate(signed_headers(body, timestamp=now), body)
    with pytest.raises(PluralWebhookError) as raised:
        verify_webhook(sent, headers, secret=WEBHOOK_SECRET, now=now)
    assert raised.value.code == code


def test_webhook_outside_the_timestamp_window_or_without_a_secret_is_rejected() -> None:
    body = _mandate_event()
    old = int(time.time()) - 3600
    with pytest.raises(PluralWebhookError) as raised:
        verify_webhook(body, signed_headers(body, timestamp=old), secret=WEBHOOK_SECRET)
    assert raised.value.code == "timestamp_out_of_window"
    with pytest.raises(PluralWebhookError) as missing:
        verify_webhook(body, signed_headers(body, timestamp=int(time.time())), secret="")
    assert missing.value.code == "secret_missing"


def test_events_that_are_not_ours_carry_no_afa_outcome() -> None:
    body = json.dumps({"data": {"mandate": {"merchant_mandate_reference": "other-1", "status": "ACTIVE"}}}).encode()
    now = int(time.time())
    assert afa_outcome(verify_webhook(body, signed_headers(body, timestamp=now), secret=WEBHOOK_SECRET)) is None


# --- flag and configuration ----------------------------------------------------------------------


def test_flag_off_means_no_executor_and_on_registers_it() -> None:
    original = executors.get_executor(plural.EXECUTOR_NAME)
    try:
        assert sync_registration({}) is False
        assert executors.get_executor(plural.EXECUTOR_NAME) is None
        assert sync_registration({"PLURAL_PINE_LIVE_EXECUTION_ENABLED": "true"}) is True
        assert isinstance(executors.get_executor(plural.EXECUTOR_NAME), PluralExecutor)
        assert sync_registration({"PLURAL_PINE_LIVE_EXECUTION_ENABLED": "false"}) is False
        assert executors.get_executor(plural.EXECUTOR_NAME) is None
    finally:
        if original is not None:
            executors.register_executor(original)


def test_credentials_need_both_values_and_an_https_base() -> None:
    assert credentials_from_env({}) is None
    assert credentials_from_env({"PLURAL_PINE_CLIENT_ID": "id"}) is None
    sandbox = credentials_from_env({"PLURAL_PINE_CLIENT_ID": "id", "PLURAL_PINE_CLIENT_SECRET": "secret"})
    assert sandbox is not None and sandbox.base_url.startswith("https://") and "secret" not in repr(sandbox)
    plain = {"PLURAL_PINE_CLIENT_ID": "id", "PLURAL_PINE_CLIENT_SECRET": "s", "PLURAL_PINE_BASE_URL": "http://x.example"}
    assert credentials_from_env(plain) is None


def test_executor_module_uses_async_http_only() -> None:
    import inspect

    source = inspect.getsource(plural)
    assert "httpx.AsyncClient" in source
    assert "httpx.post(" not in source and "httpx.get(" not in source and "requests." not in source
    assert "validate_public_url_async" in source and "build_pinned_async_transport" in source
    assert "follow_redirects=False" in source
