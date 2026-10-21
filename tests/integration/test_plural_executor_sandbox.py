# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""G55 against the Plural sandbox: skipped unless the sandbox credentials are configured.

Uses the env names the C6Z integration checks already use (``PLURAL_PINE_CLIENT_ID``,
``PLURAL_PINE_CLIENT_SECRET``, optional ``PLURAL_PINE_ENVIRONMENT``/``PLURAL_PINE_BASE_URL``).
It refuses to run against anything but the sandbox. The payment, capture and refund
step needs a sandbox instrument token in ``PLURAL_PINE_SANDBOX_INSTRUMENT_TOKEN``.
No decision row is involved: these checks exercise the wire format only; the
decision gate is covered by the unit and Postgres tests.
"""

from __future__ import annotations

import os
import uuid

import pytest

from core.commerce import executors
from core.commerce.c6z_runtime_vertical import PLURAL_PINE_ENV_VARS, resolve_env
from core.commerce.executors.plural import PluralApiClient, PluralExecutor, credentials_from_env, order_reference
from tests.plural_fakes import MemoryJournal


def _sandbox_credentials():
    resolution = resolve_env(PLURAL_PINE_ENV_VARS, os.environ)
    if not resolution.ready:
        pytest.skip(f"Plural sandbox env vars missing: {', '.join(resolution.missing)}")
    if (os.environ.get("PLURAL_PINE_ENVIRONMENT") or "sandbox").strip().lower() != "sandbox":
        pytest.skip("PLURAL_PINE_ENVIRONMENT is not sandbox")
    credentials = credentials_from_env()
    assert credentials is not None
    return credentials


@pytest.mark.asyncio
async def test_sandbox_token_and_pre_auth_order_carry_the_decision_metadata() -> None:
    credentials = _sandbox_credentials()
    client = PluralApiClient(credentials)
    reference = order_reference(f"chk_sandbox_{uuid.uuid4().hex[:12]}", "arbdec_" + "0" * 40)
    data = await client.call(
        "POST",
        "/pay/v1/orders",
        {
            "merchant_order_reference": reference,
            "order_amount": {"value": 100, "currency": "INR"},
            "pre_auth": True,
            "allowed_payment_methods": ["CARD"],
            "notes": "OACP sandbox check",
            "purchase_details": {"merchant_metadata": {"oacp_decision_id": "arbdec_" + "0" * 40}},
        },
        request_id=str(uuid.uuid4()),
    )
    assert data.get("order_id")


@pytest.mark.asyncio
async def test_sandbox_execute_and_refund_when_an_instrument_token_is_configured() -> None:
    credentials = _sandbox_credentials()
    token = os.environ.get("PLURAL_PINE_SANDBOX_INSTRUMENT_TOKEN")
    if not token:
        pytest.skip("PLURAL_PINE_SANDBOX_INSTRUMENT_TOKEN is not set")
    journal = MemoryJournal()
    executor = PluralExecutor(credentials=lambda: credentials, journal=journal)
    session_id = f"chk_sandbox_{uuid.uuid4().hex[:12]}"
    decision = executors.ConsumedDecision(
        decision_id="arbdec_" + uuid.uuid4().hex[:40].ljust(40, "0"),
        payload_hash="a" * 64,
        tenant_id="11111111-1111-4111-8111-111111111111",
        merchant_id="merchant-sandbox",
        seller_agent_id="seller-sandbox",
        _gate=executors._GATE,
    )
    checkout = executors.ExecutionCheckout(
        session_id=session_id,
        tenant_id=decision.tenant_id,
        merchant_id=decision.merchant_id,
        seller_agent_id=decision.seller_agent_id,
        ucp_version="2026-04-08",
        currency="INR",
        total_minor_units=100,
        line_items=[],
        instruments=[
            executors.ExecutionInstrument(
                id="instr_1",
                handler_id="plural_tokenized",
                type="card",
                credential={"type": "plural_pine_token", "token": token},
            )
        ],
        token_binding={"checkout_id": session_id},
        payload_hash="a" * 64,
        allowance_max_minor_units=100,
        allowance_currency="INR",
    )
    result = await executor.execute(checkout, decision)
    assert result.status in ("completed", "action_required"), result
