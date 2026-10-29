# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Plural hosted checkout against the Plural sandbox: skipped unless the sandbox credentials are configured.

Uses ``PLURAL_PINE_CLIENT_ID`` and ``PLURAL_PINE_CLIENT_SECRET`` (the names the
other Plural sandbox checks use) and refuses to run unless
``PLURAL_PINE_ENVIRONMENT`` is unset or ``sandbox``. It creates one hosted-checkout
order exactly as a payment hand-off does (``pre_auth: false``), reads its status
back, and checks the credential test. No payment is made; nothing is printed
but the outcome.
"""

from __future__ import annotations

import os
import uuid

import pytest

from core.commerce import plural_hosted
from core.commerce.executors.plural import PluralCredentials
from core.commerce.payment_handoff import handoff_reference, payment_state


def _sandbox_credentials() -> PluralCredentials:
    client_id = (os.environ.get("PLURAL_PINE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("PLURAL_PINE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        pytest.skip("Plural sandbox credentials are not configured")
    if (os.environ.get("PLURAL_PINE_ENVIRONMENT") or "sandbox").strip().lower() != "sandbox":
        pytest.skip("PLURAL_PINE_ENVIRONMENT is not sandbox")
    return PluralCredentials(
        client_id=client_id, client_secret=client_secret, base_url=plural_hosted.base_url_for("sandbox")
    )


@pytest.mark.asyncio
async def test_sandbox_credentials_pass_the_token_check() -> None:
    assert await plural_hosted.check_credentials(_sandbox_credentials()) == (True, "token_issued")


@pytest.mark.asyncio
async def test_sandbox_hosted_order_returns_a_payment_page_and_a_readable_status() -> None:
    credentials = _sandbox_credentials()
    session_id = f"chk_sandbox_{uuid.uuid4().hex[:16]}"
    reference = handoff_reference(session_id, "arbdec_" + uuid.uuid4().hex[:40])
    order = await plural_hosted.create_hosted_order(
        credentials,
        reference=reference,
        amount_minor_units=100,
        currency="INR",
        callback_url=f"https://api.merchant.example/api/v1/payments/plural/return/{uuid.uuid4()}/{session_id}",
        buyer={"email": "buyer@merchant.example", "first_name": "Sandbox", "phone_number": "+919800000000"},
        metadata={"oacp_checkout_session_id": session_id},
    )
    assert order.order_id
    assert order.redirect_url.startswith("https://")
    status = plural_hosted.outcome_from_order(await plural_hosted.fetch_order(credentials, order.order_id))
    assert status is not None and status.reference == reference and status.order_id == order.order_id
    assert status.amount_minor_units == 100 and status.currency == "INR"
    # Nobody has paid: the order must not read as money received.
    assert payment_state(status.status) != "paid"
