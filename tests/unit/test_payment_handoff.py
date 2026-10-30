# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Buyer payment hand-off: settings, the Shopify cart permalink, the arbiter action, Plural hosted calls.

Pure and transport-level checks; the full Complete flow on a real database is in
``tests/integration/test_payment_handoff_postgres.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from core.commerce import arbiter, plural_hosted
from core.commerce import payment_handoff as ph
from core.commerce.executors.plural import PluralCredentials, PluralWebhookError
from core.commerce.order_notifications import (
    delivery_heading,
    delivery_view,
    order_email,
    shipping_fee_line,
    surface_label,
)
from core.commerce.ucp.completion import arbiter_context
from core.commerce.ucp.merchant import merchant_ucp_from_config
from tests import commerce_arbiter_fixtures as fx

TENANT = "11111111-1111-4111-8111-111111111111"
SECRET = base64.b64encode(b"synthetic-merchant-webhook-secret").decode()
REF = "commerce_plural_" + "a" * 24


def _credentials() -> PluralCredentials:
    return PluralCredentials(
        client_id="client-example",
        client_secret="secret-example",
        base_url="https://plural.example/api",
        webhook_secret=SECRET,
    )


def _signed(body: bytes, *, webhook_id: str = "wh_1", timestamp: int | None = None, secret: str = SECRET) -> dict:
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(base64.b64decode(secret), f"{webhook_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": "v1," + base64.b64encode(mac).decode(),
    }


# --- settings -------------------------------------------------------------------------------------


def test_settings_parse_both_modes_and_refuse_anything_else() -> None:
    assert ph.parse_payment_settings({"mode": "shopify_cart"}) == ph.PaymentSettings("shopify_cart")
    plural = ph.parse_payment_settings({"mode": "plural_hosted", "plural": {"credential_ref": REF}})
    assert plural.credential_ref == REF and plural.to_dict() == {
        "mode": "plural_hosted",
        "plural": {"credential_ref": REF},
    }
    for bad in (
        {"mode": "agent_charges_card"},
        {"mode": "plural_hosted"},
        {"mode": "plural_hosted", "plural": {"credential_ref": "env:PLURAL_CLIENT_SECRET"}},
        {"mode": "shopify_cart", "pre_auth": True},
        "shopify_cart",
    ):
        with pytest.raises(ph.PaymentSettingsError):
            ph.parse_payment_settings(bad)


def test_unusable_settings_mean_no_handoff() -> None:
    assert ph.payment_settings({"provider_policy": {}}) is None
    assert ph.payment_settings(None) is None
    assert ph.payment_settings({"provider_policy": {"payments": {"mode": "bogus"}}}) is None
    assert ph.payment_settings({"provider_policy": {"payments": {"mode": "shopify_cart"}}}).mode == "shopify_cart"


# --- Shopify cart permalink -------------------------------------------------------------------------


def test_shop_domain_comes_from_the_enabled_shopify_source_only() -> None:
    sources = [
        {"connector_type": "catalog_upload"},
        {"connector_type": "shopify", "enabled": False, "shop_domain": "old.myshopify.com"},
        {"connector_type": "shopify", "shop_domain": "Example-Store.myshopify.com"},
    ]
    assert ph.shopify_shop_domain(sources) == "example-store.myshopify.com"
    assert ph.shopify_shop_domain([{"connector_type": "shopify", "shop_domain": "evil.example/cart"}]) is None
    assert ph.shopify_shop_domain(None) is None


def test_cart_url_uses_the_server_side_numeric_variants() -> None:
    lines = [
        {"id": "li_1", "item": {"id": "shopify_variant:abc:redacted"}, "quantity": 2, "storefront_variant": "4401"},
        {"id": "li_2", "item": {"id": "shopify_variant:def:redacted"}, "quantity": 1, "storefront_variant": "4402"},
    ]
    assert (
        ph.shopify_cart_url("example-store.myshopify.com", lines)
        == "https://example-store.myshopify.com/cart/4401:2,4402:1"
    )


@pytest.mark.parametrize(
    "line",
    [
        {"item": {"id": "x"}, "quantity": 1},
        {"item": {"id": "x"}, "quantity": 1, "storefront_variant": "gid://shopify/ProductVariant/1"},
        {"item": {"id": "x", "quantity_unit": {"unit": "MTR"}}, "quantity": 15, "storefront_variant": "9"},
        {"item": {"id": "x"}, "quantity": 0, "storefront_variant": "9"},
        {"item": {"id": "x"}, "quantity": True, "storefront_variant": "9"},
    ],
)
def test_cart_url_refuses_lines_shopify_cannot_take(line: dict) -> None:
    assert ph.shopify_cart_url("example-store.myshopify.com", [line]) is None


def test_cart_url_refuses_a_foreign_host() -> None:
    line = {"item": {"id": "x"}, "quantity": 1, "storefront_variant": "9"}
    assert ph.shopify_cart_url("example.com", [line]) is None
    assert ph.shopify_cart_url(None, [line]) is None


# --- state helpers ---------------------------------------------------------------------------------


def _state() -> dict:
    return {
        "currency": "INR",
        "totals": [{"type": "subtotal", "amount": 2500}, {"type": "total", "amount": 2500}],
        "line_items": [{"id": "li_1", "item": {"id": "x", "price": 2500}, "quantity": 1}],
        "messages": [],
    }


def test_issue_reuse_and_retire_a_link() -> None:
    now = datetime.now(UTC)
    state = _state()
    ph.issue(
        state,
        mode="shopify_cart",
        continue_url="https://a.myshopify.com/cart/1:1",
        decision_id="arbdec_1",
        now=now,
        expires_at=now + timedelta(hours=1),
    )
    assert state["continue_url"] == "https://a.myshopify.com/cart/1:1"
    assert [m["code"] for m in state["messages"]] == ["payment_handoff"]
    assert state["messages"][0]["severity"] == "requires_buyer_input"
    assert ph.active_handoff(state, now)["decision_id"] == "arbdec_1"
    assert ph.active_handoff(state, now + timedelta(hours=2)) is None
    ph.clear_handoff(state, "expired")
    assert "continue_url" not in state and state["messages"] == [] and state["handoff"]["status"] == "expired"


@pytest.mark.parametrize("mode", ["shopify_cart", "plural_hosted"])
def test_a_link_holds_the_daily_cap_while_open_and_all_the_day_it_was_issued(mode: str) -> None:
    day_start = datetime(2026, 10, 30, tzinfo=UTC)
    issued = day_start + timedelta(hours=9)
    state = _state()
    ph.issue(
        state,
        mode=mode,
        continue_url="https://a.myshopify.com/cart/1:1",
        decision_id="arbdec_1",
        now=issued,
        expires_at=issued + timedelta(minutes=30),
    )
    assert ph.holds_daily_cap(state, now=issued + timedelta(minutes=29), day_start=day_start)
    # Lapsed the same UTC day, the link can still be paid: a Shopify cart permalink never expires, and a
    # Plural order has no expiry of its own.
    lapsed = issued + timedelta(hours=2)
    assert ph.holds_daily_cap(state, now=lapsed, day_start=day_start)
    # The next UTC day a lapsed link counts for nothing, whatever the mode.
    tomorrow = day_start + timedelta(days=1)
    assert not ph.holds_daily_cap(state, now=tomorrow + timedelta(hours=1), day_start=tomorrow)
    # A retired link (paid, failed or replaced) counts for nothing, even on the day it was issued.
    for retired in ("paid", "failed", "expired"):
        state["handoff"]["status"] = retired
        assert not ph.holds_daily_cap(state, now=issued + timedelta(minutes=1), day_start=day_start)
    assert not ph.holds_daily_cap(_state(), now=issued, day_start=day_start)


def test_a_plural_order_being_created_and_every_payable_link_on_record_hold_the_daily_cap() -> None:
    day_start = datetime(2026, 10, 30, tzinfo=UTC)
    issued = day_start + timedelta(hours=9)
    later = issued + timedelta(hours=1)
    state = _state()
    state["handoff"] = {"mode": "plural_hosted", "status": "creating", "reference": "aoh-b"}
    assert ph.holds_daily_cap(state, now=issued, day_start=day_start)
    # The current link failed, but an earlier one on record can still be paid: it counts on its issue day.
    state["handoff"] = {"mode": "plural_hosted", "status": "failed", "reference": "aoh-b"}
    assert not ph.holds_daily_cap(state, now=later, day_start=day_start)
    link_a = {
        "continue_url": "https://pay.plural.example/a",
        "issued_at": ph.iso(issued),
        "expires_at": ph.iso(issued + timedelta(minutes=30)),
    }
    state["handoff_orders"] = {"aoh-a": ph.link_record(link_a), "aoh-b": {"status": "failed"}}
    assert ph.holds_daily_cap(state, now=later, day_start=day_start)
    tomorrow = day_start + timedelta(days=1)
    assert not ph.holds_daily_cap(state, now=tomorrow + timedelta(hours=1), day_start=tomorrow)
    for retired in ("paid", "failed"):
        ph.set_reference_status(state, "aoh-a", retired)
        assert not ph.holds_daily_cap(state, now=later, day_start=day_start)


def test_clearing_a_link_replaces_its_record_instead_of_changing_the_stored_one() -> None:
    now = datetime.now(UTC)
    stored = _state()
    ph.issue(
        stored,
        mode="plural_hosted",
        continue_url="https://pay.plural.example/1",
        decision_id="arbdec_1",
        now=now,
        expires_at=now + timedelta(minutes=30),
    )
    copy = dict(stored)  # what a handler does with a row's state
    ph.clear_handoff(copy, "failed")
    assert copy["handoff"]["status"] == "failed"
    # The row's stored state is untouched, so the change to the copy is seen and written.
    assert stored["handoff"]["status"] == "issued" and copy != stored


def test_a_failed_link_brings_back_the_latest_earlier_link_that_can_still_be_paid() -> None:
    now = datetime.now(UTC)
    state = _state()
    ph.remember_reference(state, "aoh-a", {"decision_id": "arbdec_a", "amount": 2500})
    state["handoff"] = {"mode": "plural_hosted", "status": "creating", "reference": "aoh-a", "decision_id": "arbdec_a"}
    ph.issue(
        state,
        mode="plural_hosted",
        continue_url="https://pay.plural.example/a",
        decision_id="arbdec_a",
        now=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=30),
        extra={"provider_order_id": "v1-a"},
    )
    ph.remember_reference(state, "aoh-a", ph.link_record(state["handoff"]))
    issued_a = dict(state["handoff"])
    # The re-issue: A lapsed, keeps its record, and the order for B is being created.
    ph.remember_link(state, state["handoff"])
    ph.clear_handoff(state, "expired")
    state["handoff"] = {"mode": "plural_hosted", "status": "creating", "reference": "aoh-b"}
    ph.remember_reference(state, "aoh-b", {"decision_id": "arbdec_b", "amount": 2500})
    assert ph.retire_failed(state) == "requires_escalation"
    assert state["handoff"] == issued_a
    assert state["continue_url"] == "https://pay.plural.example/a"
    assert [m["code"] for m in state["messages"]] == [ph.MESSAGE_CODE]
    assert state["handoff_orders"]["aoh-b"]["status"] == "failed" and ph.payable(state["handoff_orders"]["aoh-a"])
    # When A fails too, nothing can be paid: the checkout is ready to complete again.
    assert ph.retire_failed(state) == "ready_for_complete"
    assert state["handoff"]["status"] == "failed" and "continue_url" not in state and state["messages"] == []
    assert not ph.payable(state["handoff_orders"]["aoh-a"])


def test_the_newest_issued_link_decides_whether_the_buyer_was_declined() -> None:
    state = _state()
    assert not ph.newest_link_failed(state)
    ph.remember_reference(state, "aoh-a", ph.link_record({"issued_at": "2026-10-30T09:00:00Z"}))
    ph.remember_reference(state, "aoh-b", ph.link_record({"issued_at": "2026-10-30T10:00:00Z"}))
    ph.remember_reference(state, "aoh-c", {"decision_id": "arbdec_c"})  # an order that was never created
    assert not ph.newest_link_failed(state)
    ph.set_reference_status(state, "aoh-a", "failed")
    assert not ph.newest_link_failed(state)
    ph.set_reference_status(state, "aoh-b", "failed")
    assert ph.newest_link_failed(state)


def test_a_reference_whose_link_can_still_be_paid_is_never_dropped() -> None:
    state = _state()
    ph.remember_reference(state, "aoh-a", {**ph.link_record({"continue_url": "https://pay.plural.example/a"})})
    for attempt in range(ph.MAX_REFERENCES + 5):
        ph.remember_reference(state, f"aoh-b{attempt}", {"decision_id": f"arbdec_{attempt}"})
        ph.set_reference_status(state, f"aoh-b{attempt}", "failed")
    assert "aoh-a" in state["handoff_refs"] and ph.payable(state["handoff_orders"]["aoh-a"])
    assert len(state["handoff_refs"]) == ph.MAX_REFERENCES
    assert state["handoff_refs"][-1] == f"aoh-b{ph.MAX_REFERENCES + 4}"
    assert set(state["handoff_orders"]) == set(state["handoff_refs"])


def test_references_and_events_are_bounded_and_deduplicated() -> None:
    state = _state()
    for i in range(ph.MAX_REFERENCES + 3):
        ph.remember_reference(state, f"aoh-{i}", {"amount": i})
    assert len(state["handoff_refs"]) == ph.MAX_REFERENCES == len(state["handoff_orders"])
    assert state["handoff_refs"][-1] == f"aoh-{ph.MAX_REFERENCES + 2}"
    assert ph.seen_event(state, "wh_1") is False
    assert ph.seen_event(state, "wh_1") is True
    assert ph.seen_event(state, None) is False


def test_reference_fits_plural_and_payload_binds_the_cart() -> None:
    reference = ph.handoff_reference("chk_1", "arbdec_1")
    assert reference.startswith("aoh-") and len(reference) <= 50
    a = ph.handoff_payload(
        session_id="chk_1", revision=1, merchant_id="m", seller_agent_id="s", state=_state(), mode="plural_hosted"
    )
    changed = _state()
    changed["line_items"][0]["quantity"] = 2
    b = ph.handoff_payload(
        session_id="chk_1", revision=1, merchant_id="m", seller_agent_id="s", state=changed, mode="plural_hosted"
    )
    assert a["action"] == "payment_handoff" and a["cart_hash"] != b["cart_hash"]


def test_payment_state_is_paid_only_for_money_received() -> None:
    assert ph.payment_state("PROCESSED") == ph.payment_state("captured") == ph.payment_state("SETTLED") == "paid"
    assert ph.payment_state("AUTHORIZED") == ph.payment_state("CREATED") == ph.payment_state(None) == "pending"
    assert ph.payment_state("FAILED") == ph.payment_state("CANCELLED") == "failed"


def test_links_need_an_https_public_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings

    monkeypatch.delenv("AGENTICORG_APP_URL", raising=False)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    assert ph.return_url(TENANT, "chk_1") is None
    monkeypatch.setenv("AGENTICORG_APP_URL", "https://app.merchant.example")
    assert (
        ph.return_url(TENANT, "chk_1") == f"https://app.merchant.example/api/v1/payments/plural/return/{TENANT}/chk_1"
    )
    monkeypatch.setattr(settings, "public_api_base_url", "http://localhost:8000")
    assert ph.return_url(TENANT, "chk_1") is None
    monkeypatch.setattr(settings, "public_api_base_url", "https://api.merchant.example/")
    assert (
        ph.return_url(TENANT, "chk_1") == f"https://api.merchant.example/api/v1/payments/plural/return/{TENANT}/chk_1"
    )
    assert ph.webhook_url(TENANT, "k" * 32).endswith(f"/api/v1/payments/plural/webhooks/{TENANT}/{'k' * 32}")


# --- the arbiter action ----------------------------------------------------------------------------


def _context(*, records=None, config=None, require_verified: bool = True) -> arbiter.ArbiterContext:
    config = (
        config
        if config is not None
        else {"merchant_id": fx.MERCHANT, "seller_agent_id": fx.SELLER, "provider_policy": {}}
    )
    merchant = merchant_ucp_from_config(config, tenant_id=TENANT, serving_host="shop.merchant.example")
    assert merchant is not None
    return arbiter_context(
        merchant=merchant,
        now=datetime.now(UTC),
        payload={"action": "payment_handoff"},
        amount_minor_units=2500,
        currency="INR",
        quantity=1,
        authority=None,
        cache_records=fx.cache_records(TENANT) if records is None else records,
        merchant_config=config,
        buyer_agent_id="ucp_platform:abc",
        require_verified_authority=require_verified,
    )


def test_handoff_is_allowed_without_buyer_authority_even_where_authority_is_required() -> None:
    decision = arbiter.decide("payment_handoff", _context())
    assert decision.allowed, decision.reason
    assert decision.rules_evaluated == ("oacp_artifacts_valid", "handoff_merchant_limits")
    assert "buyer_authority_verified" not in decision.rules_evaluated
    # The same context could never complete an agent-executed payment.
    assert arbiter.decide("session_complete", _context()).failing_rule == "mandate_present_unexpired"


def test_handoff_needs_verified_artifacts_but_not_mandate_capability() -> None:
    records = fx.cache_records(TENANT)
    without_capability = [r for r in records if r.artifact_type != "mandate_capability"]
    assert arbiter.decide("payment_handoff", _context(records=without_capability)).allowed
    without_price = [r for r in records if r.artifact_type != "price"]
    refused = arbiter.decide("payment_handoff", _context(records=without_price))
    assert refused.failing_rule == "oacp_artifacts_valid" and refused.reason == "artifact_missing:price"


def test_handoff_respects_merchant_limits_when_configured() -> None:
    config = fx.merchant_config()
    config["provider_policy"]["arbiter"]["max_order_amount_minor_units"] = 1000
    refused = arbiter.decide("payment_handoff", _context(config=config))
    assert refused.failing_rule == "handoff_merchant_limits" and refused.reason == "order_amount_cap_exceeded"
    assert arbiter.decide("payment_handoff", _context(config=fx.merchant_config())).allowed


# --- Plural hosted checkout ---------------------------------------------------------------------------


def test_customer_details_carry_only_what_the_buyer_gave() -> None:
    assert plural_hosted.customer_details(
        {"email": "buyer@merchant.example", "first_name": " Asha ", "phone_number": "+91 98000 00000"}
    ) == {
        "email_id": "buyer@merchant.example",
        "first_name": "Asha",
        "mobile_number": "9800000000",
        "country_code": "91",
    }
    assert plural_hosted.customer_details({"phone_number": "+1 555 0100"}) == {}
    assert plural_hosted.customer_details(None) == {}


@pytest.mark.asyncio
async def test_hosted_order_request_has_no_pre_auth_and_returns_the_redirect() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/auth/v1/token"):
            return httpx.Response(200, json={"access_token": "access-example"})
        return httpx.Response(
            200,
            json={
                "data": {
                    "order_id": "v1-hosted-1",
                    "redirect_url": "https://pay.plural.example/checkout?token=t",
                    "response_code": 200,
                }
            },
        )

    order = await plural_hosted.create_hosted_order(
        _credentials(),
        reference="aoh-" + "0" * 44,
        amount_minor_units=2500,
        currency="inr",
        callback_url="https://api.merchant.example/return",
        buyer={"email": "buyer@merchant.example"},
        metadata={"oacp_decision_id": "arbdec_1"},
        transport_factory=lambda: httpx.MockTransport(handle),
    )
    assert order == plural_hosted.HostedOrder("v1-hosted-1", "https://pay.plural.example/checkout?token=t")
    call = seen[-1]
    assert call.url.path == "/api/checkout/v1/orders" and call.headers["authorization"] == "Bearer access-example"
    assert call.headers["request-id"] and call.headers["request-timestamp"]
    body = json.loads(call.content)
    assert body["pre_auth"] is False
    assert body["order_amount"] == {"value": 2500, "currency": "INR"}
    assert body["allowed_payment_methods"] == ["CARD", "UPI", "NETBANKING"]
    assert body["callback_url"] == "https://api.merchant.example/return"
    assert body["purchase_details"]["customer"] == {"email_id": "buyer@merchant.example"}
    assert body["purchase_details"]["merchant_metadata"] == {"oacp_decision_id": "arbdec_1"}


@pytest.mark.asyncio
async def test_hosted_order_without_an_https_redirect_is_unusable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v1/token"):
            return httpx.Response(200, json={"access_token": "a"})
        return httpx.Response(200, json={"order_id": "v1-1", "redirect_url": "http://pay.plural.example"})

    with pytest.raises(plural_hosted.PluralUnavailableError):
        await plural_hosted.create_hosted_order(
            _credentials(),
            reference="aoh-1",
            amount_minor_units=1,
            currency="INR",
            callback_url="https://x.example",
            buyer=None,
            metadata={},
            transport_factory=lambda: httpx.MockTransport(handle),
        )


@pytest.mark.asyncio
async def test_credential_check_fetches_a_token_only() -> None:
    seen: list[str] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"access_token": "access-example"})

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "INVALID_CLIENT"})

    assert await plural_hosted.check_credentials(_credentials(), transport_factory=lambda: httpx.MockTransport(ok)) == (
        True,
        "token_issued",
    )
    assert seen == ["/api/auth/v1/token"]
    assert await plural_hosted.check_credentials(
        _credentials(), transport_factory=lambda: httpx.MockTransport(refused)
    ) == (
        False,
        "plural_refused:INVALID_CLIENT",
    )


def test_outcome_reads_only_our_handoff_references() -> None:
    ours = {
        "order_id": "v1-1",
        "merchant_order_reference": "aoh-abc",
        "status": "processed",
        "order_amount": {"value": 2500, "currency": "inr"},
    }
    outcome = plural_hosted.outcome_from_order(ours)
    assert outcome == plural_hosted.HostedOutcome("v1-1", "aoh-abc", "PROCESSED", 2500, "INR")
    assert plural_hosted.outcome_from_order({"order": ours}) == outcome
    assert plural_hosted.outcome_from_order({**ours, "merchant_order_reference": "aoc-executor-order"}) is None
    assert plural_hosted.outcome_from_order({**ours, "status": ""}) is None


def test_webhook_signature_valid_forged_and_replayed() -> None:
    body = json.dumps({"event_type": "ORDER_PROCESSED", "data": {"order_id": "v1-1"}}).encode()
    event = plural_hosted.verify_hosted_webhook(body, _signed(body), secret=SECRET)
    assert event.webhook_id == "wh_1" and event.data == {"order_id": "v1-1"}
    forged = _signed(body, secret=base64.b64encode(b"someone-else").decode())
    with pytest.raises(PluralWebhookError) as exc:
        plural_hosted.verify_hosted_webhook(body, forged, secret=SECRET)
    assert exc.value.code == "signature_invalid"
    tampered = _signed(body)
    with pytest.raises(PluralWebhookError):
        plural_hosted.verify_hosted_webhook(body.replace(b"v1-1", b"v1-2"), tampered, secret=SECRET)
    replayed = _signed(body, timestamp=int(time.time()) - 3600)
    with pytest.raises(PluralWebhookError) as exc:
        plural_hosted.verify_hosted_webhook(body, replayed, secret=SECRET)
    assert exc.value.code == "timestamp_out_of_window"
    with pytest.raises(PluralWebhookError) as exc:
        plural_hosted.verify_hosted_webhook(body, _signed(body), secret="")
    assert exc.value.code == "secret_missing"


def test_credential_ref_is_a_stable_digest_of_the_merchant() -> None:
    ref = plural_hosted.credential_ref_for("tea-corner")
    assert ref == plural_hosted.credential_ref_for("tea-corner") != plural_hosted.credential_ref_for("tea-corner-2")
    assert ph.parse_payment_settings({"mode": "plural_hosted", "plural": {"credential_ref": ref}}).credential_ref == ref
    with pytest.raises(ValueError):
        plural_hosted.base_url_for("https://attacker.example")


# --- the merchant's email ----------------------------------------------------------------------------


def test_order_email_escapes_buyer_text_and_names_the_surface() -> None:
    snapshot = {
        "currency": "INR",
        "totals": [{"type": "total", "amount": 44900}],
        "line_items": [{"item": {"title": "<b>Chai</b>"}, "quantity": {"total": 2}}],
    }
    subject, body = order_email(
        merchant_name="Tea & Co",
        order_id="ord_1",
        snapshot=snapshot,
        buyer={"first_name": "<script>x</script>", "email": "buyer@merchant.example"},
        surface="Telegram",
    )
    assert subject == "New order ord_1 - Rs 449.00"
    assert "<script>" not in body and "&lt;script&gt;" in body and "&lt;b&gt;Chai" in body
    assert "Tea &amp; Co" in body and "Telegram" in body and "buyer@merchant.example" in body
    assert (
        "gave no contact" in order_email(merchant_name="m", order_id="o", snapshot=snapshot, buyer={}, surface="x")[1]
    )


def test_order_email_carries_the_delivery_address_and_shipping_choice() -> None:
    snapshot = {
        "currency": "INR",
        "totals": [{"type": "fulfillment", "amount": 4900}, {"type": "total", "amount": 88900}],
        "line_items": [{"item": {"title": "Filter coffee"}, "quantity": {"total": 2}}],
        "fulfillment": {
            "expectations": [
                {
                    "method_type": "shipping",
                    "description": "Standard <delivery>",
                    "destination": {
                        "first_name": "Asha",
                        "street_address": "12 MG Road",
                        "address_locality": "Bengaluru",
                        "postal_code": "560001",
                        "address_country": "IN",
                        "phone_number": "",
                    },
                },
                {"method_type": "pickup", "description": "MG Road store", "destination": "not an address"},
                "not an expectation",
            ]
        },
    }
    _, body = order_email(merchant_name="m", order_id="o", snapshot=snapshot, buyer={}, surface="x")
    assert "<li>Ship - Standard &lt;delivery&gt;<br>Asha<br>12 MG Road<br>Bengaluru, 560001<br>IN</li>" in body
    assert "<li>Store pickup - MG Road store</li>" in body
    assert body.count("Rs 49.00") == 1 and "<p>Shipping fee: Rs 49.00</p>" in body
    no_delivery = order_email(merchant_name="m", order_id="o", snapshot={"currency": "INR"}, buyer={}, surface="x")[1]
    assert "The buyer gave no delivery details." in no_delivery and "Shipping fee" not in no_delivery
    assert delivery_view({"fulfillment": {"expectations": [{"destination": {}}]}}) == [
        {"method": "shipping", "option": None, "address": []}
    ]


def test_an_order_shipped_twice_shows_its_one_shipping_fee_once() -> None:
    # Two shipping destinations at Rs 49 each: the order keeps one fulfillment total of Rs 98.
    def shipped(option: str, street: str) -> dict:
        return {
            "method_type": "shipping",
            "description": option,
            "destination": {"first_name": "Asha", "street_address": street, "address_country": "IN"},
        }

    snapshot = {
        "currency": "INR",
        "totals": [{"type": "fulfillment", "amount": 9800}, {"type": "total", "amount": 98800}],
        "line_items": [{"item": {"title": "Filter coffee"}, "quantity": {"total": 2}}],
        "fulfillment": {"expectations": [shipped("Standard", "12 MG Road"), shipped("Express", "4 Park Street")]},
    }
    _, body = order_email(merchant_name="m", order_id="o", snapshot=snapshot, buyer={}, surface="x")
    assert "<li>Ship - Standard<br>Asha<br>12 MG Road<br>IN</li>" in body
    assert "<li>Ship - Express<br>Asha<br>4 Park Street<br>IN</li>" in body
    assert body.count("Rs 98.00") == 1 and "<p>Shipping fee for all deliveries: Rs 98.00</p>" in body
    assert delivery_heading({"method": "shipping", "option": "Standard"}) == "Ship - Standard"
    assert delivery_heading({"method": "pickup", "option": None}) == "Store pickup"
    deliveries = delivery_view(snapshot)
    assert shipping_fee_line(deliveries, "Rs 98.00") == "Shipping fee for all deliveries: Rs 98.00"
    assert shipping_fee_line(deliveries[:1], "Rs 49.00") == "Shipping fee: Rs 49.00"
    # Nothing ships (a store pickup), or the order has no fulfillment total: no fee line.
    assert shipping_fee_line([{"method": "pickup", "option": "MG Road store", "address": []}], "Rs 0.00") == ""
    assert shipping_fee_line(deliveries, "") == ""


def test_surface_label() -> None:
    assert surface_label({"platform": {"surface": "telegram"}}, None) == "Telegram"
    assert surface_label({"platform": {"surface": "mcp"}}, None) == "AI assistant (MCP)"
    assert surface_label({"protocol": "acp"}, None) == "Agentic Commerce Protocol"
    assert surface_label({}, "https://platform.example/p.json") == "UCP platform"
