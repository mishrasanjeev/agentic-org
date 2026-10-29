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
from core.commerce.order_notifications import order_email, surface_label
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


def test_surface_label() -> None:
    assert surface_label({"platform": {"surface": "telegram"}}, None) == "Telegram"
    assert surface_label({"platform": {"surface": "mcp"}}, None) == "AI assistant (MCP)"
    assert surface_label({"protocol": "acp"}, None) == "Agentic Commerce Protocol"
    assert surface_label({}, "https://platform.example/p.json") == "UCP platform"
