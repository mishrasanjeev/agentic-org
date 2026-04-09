"""Tests for billing module — tiers, limits, usage, webhooks, Plural SDK flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest

# ── Tier & Limit Tests ──────────────────────────────────────────────


class TestFreeTierLimits:
    """test_free_tier_limits — Free tier: 3 agents, 1K runs."""

    def test_free_tier_limits(self):
        from core.billing.limits import TIERS

        free = TIERS["free"]
        assert free["agent_count"] == 3
        assert free["agent_runs"] == 1_000
        assert free["storage_bytes"] == 1 * 1024 * 1024 * 1024


class TestProTierLimits:
    """test_pro_tier_limits — Pro tier: 15 agents, 10K runs."""

    def test_pro_tier_limits(self):
        from core.billing.limits import TIERS

        pro = TIERS["pro"]
        assert pro["agent_count"] == 15
        assert pro["agent_runs"] == 10_000
        assert pro["storage_bytes"] == 50 * 1024 * 1024 * 1024

        enterprise = TIERS["enterprise"]
        assert enterprise["agent_count"] == -1  # unlimited
        assert enterprise["agent_runs"] == -1
        assert enterprise["storage_bytes"] == -1


class TestUsageCounterIncrements:
    """test_usage_counter_increments — Redis-based usage counter works."""

    @patch("core.billing.usage_tracker._get_redis")
    def test_usage_counter_increments(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.incrby.return_value = 5
        mock_redis.ttl.return_value = -1
        mock_get_redis.return_value = mock_redis

        from core.billing.usage_tracker import increment_agent_runs

        result = increment_agent_runs("tenant_abc", count=1)
        assert result == 5
        mock_redis.incrby.assert_called_once_with("usage:tenant_abc:runs", 1)
        # Should set TTL on fresh key
        mock_redis.expire.assert_called_once()

    @patch("core.billing.usage_tracker._get_redis")
    def test_get_usage_returns_dict(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: {
            "usage:t1:runs": "42",
            "usage:t1:agents": "3",
            "usage:t1:storage": "1048576",
        }.get(k)
        mock_get_redis.return_value = mock_redis

        from core.billing.usage_tracker import get_usage

        usage = get_usage("t1")
        assert usage == {"agent_runs": 42, "agent_count": 3, "storage_bytes": 1048576}


class TestSoftWarningAt80Percent:
    """test_soft_warning_at_80_percent — Warning fires at 80% usage."""

    @patch("core.billing.limits._get_tenant_tier", return_value="free")
    @patch("core.billing.usage_tracker.get_usage")
    def test_soft_warning_at_80_percent(self, mock_usage, mock_tier):
        mock_usage.return_value = {
            "agent_runs": 800,  # 80% of 1000
            "agent_count": 2,
            "storage_bytes": 0,
        }

        from core.billing.limits import check_limit

        result = check_limit("t1", "agent_runs")
        assert result.allowed is True
        assert result.warning is True
        assert result.usage == 800
        assert result.limit == 1_000


class TestHardBlockAt100Percent:
    """test_hard_block_at_100_percent — Hard block at 100% usage."""

    @patch("core.billing.limits._get_tenant_tier", return_value="free")
    @patch("core.billing.usage_tracker.get_usage")
    def test_hard_block_at_100_percent(self, mock_usage, mock_tier):
        mock_usage.return_value = {
            "agent_runs": 1000,  # 100% of 1000
            "agent_count": 3,
            "storage_bytes": 0,
        }

        from core.billing.limits import check_limit

        result = check_limit("t1", "agent_runs")
        assert result.allowed is False
        assert result.warning is False
        assert result.usage == 1_000
        assert result.limit == 1_000

    @patch("core.billing.limits._get_tenant_tier", return_value="free")
    @patch("core.billing.usage_tracker.get_usage")
    def test_over_limit_also_blocked(self, mock_usage, mock_tier):
        mock_usage.return_value = {
            "agent_runs": 1200,
            "agent_count": 5,
            "storage_bytes": 0,
        }

        from core.billing.limits import check_limit

        result = check_limit("t1", "agent_runs")
        assert result.allowed is False

        result_agents = check_limit("t1", "agent_count")
        assert result_agents.allowed is False


# ── Stripe Webhook Tests ────────────────────────────────────────────


class TestStripeWebhookValidatesSignature:
    """test_stripe_webhook_validates_signature — signature validation."""

    @patch("core.billing.stripe_client._get_stripe")
    def test_stripe_webhook_validates_signature(self, mock_get_stripe):
        mock_stripe = MagicMock()

        mock_stripe.Webhook.construct_event.return_value = {
            "type": "invoice.paid",
            "created": int(time.time()),
            "data": {
                "object": {
                    "metadata": {"tenant_id": "t1"},
                    "amount_paid": 9900,
                    "currency": "usd",
                }
            },
        }
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import handle_webhook

        result = handle_webhook(b'{"type":"invoice.paid"}', "sig_header_test")
        assert result["event_type"] == "invoice.paid"
        assert result["processed"] is True
        assert result["tenant_id"] == "t1"
        mock_stripe.Webhook.construct_event.assert_called_once()

    @patch("core.billing.stripe_client._get_stripe")
    def test_stripe_webhook_invalid_signature_raises(self, mock_get_stripe):
        mock_stripe = MagicMock()
        mock_stripe.Webhook.construct_event.side_effect = ValueError("Invalid signature")
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import handle_webhook

        with pytest.raises(ValueError, match="Invalid signature"):
            handle_webhook(b"bad", "bad_sig")


# ── Plans Tests ─────────────────────────────────────────────────────


class TestPlansEndpointReturnsTiers:
    """test_plans_endpoint_returns_tiers — /billing/plans returns all tiers."""

    def test_plans_endpoint_returns_tiers(self):
        from core.billing.limits import PLAN_PRICING

        assert len(PLAN_PRICING) == 3
        plan_names = [p["plan"] for p in PLAN_PRICING]
        assert "free" in plan_names
        assert "pro" in plan_names
        assert "enterprise" in plan_names

        # Each plan has required fields
        for plan in PLAN_PRICING:
            assert "price_usd" in plan
            assert "price_inr" in plan
            assert "features" in plan
            assert isinstance(plan["features"], list)
            assert len(plan["features"]) > 0


class TestIndiaPricingInINR:
    """test_india_pricing_in_inr — Plural plans have correct INR amounts."""

    def test_india_pricing_in_inr(self):
        from core.billing.limits import PLAN_PRICING

        pro = next(p for p in PLAN_PRICING if p["plan"] == "pro")
        ent = next(p for p in PLAN_PRICING if p["plan"] == "enterprise")

        assert pro["price_inr"] == 9_999
        assert ent["price_inr"] == 49_999
        assert pro["price_usd"] == 99
        assert ent["price_usd"] == 499

    def test_pinelabs_plan_amounts(self):
        from core.billing.pinelabs_client import PLAN_AMOUNT_INR

        assert PLAN_AMOUNT_INR["pro"] == 9_999_00  # paise
        assert PLAN_AMOUNT_INR["enterprise"] == 49_999_00

    def test_free_plan_has_zero_inr(self):
        from core.billing.limits import PLAN_PRICING

        free = next(p for p in PLAN_PRICING if p["plan"] == "free")
        assert free["price_inr"] == 0
        assert free["price_usd"] == 0


# ── Plural API v1 Tests ─────────────────────────────────────────────


class TestPluralPaymentMethods:
    """All payment methods are available for redirect mode."""

    def test_all_payment_methods_listed(self):
        from core.billing.pinelabs_client import ALL_PAYMENT_METHODS

        assert "CARD" in ALL_PAYMENT_METHODS
        assert "UPI" in ALL_PAYMENT_METHODS
        assert "NETBANKING" in ALL_PAYMENT_METHODS
        assert "WALLET" in ALL_PAYMENT_METHODS
        assert "CREDIT_EMI" in ALL_PAYMENT_METHODS
        assert "DEBIT_EMI" in ALL_PAYMENT_METHODS
        assert len(ALL_PAYMENT_METHODS) == 6


class TestPluralOAuthToken:
    """Plural OAuth token acquisition and caching."""

    @patch("core.billing.pinelabs_client._get_http")
    def test_token_acquired_on_first_call(self, mock_http):
        from core.billing.pinelabs_client import _get_access_token, _token_cache

        # Reset cache
        _token_cache["access_token"] = ""
        _token_cache["expires_at"] = 0.0

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "test_token_123",
            "expires_at": "2099-12-31T23:59:59.000Z",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.return_value.post.return_value = mock_resp

        token = _get_access_token()
        assert token == "test_token_123"
        assert _token_cache["access_token"] == "test_token_123"

    @patch("core.billing.pinelabs_client._get_http")
    def test_cached_token_reused(self, mock_http):
        from core.billing.pinelabs_client import _get_access_token, _token_cache

        # Set a valid cached token
        _token_cache["access_token"] = "cached_token"
        _token_cache["expires_at"] = time.time() + 3600  # 1 hour from now

        token = _get_access_token()
        assert token == "cached_token"
        # HTTP should NOT be called since token is cached
        mock_http.return_value.post.assert_not_called()


class TestPluralCreateOrder:
    """Plural create order (hosted checkout redirect) flow."""

    @patch("core.billing.pinelabs_client._get_access_token", return_value="test_token")
    @patch("core.billing.pinelabs_client._get_http")
    def test_create_order_returns_challenge_url(self, mock_http, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "order_id": "v1-order-12345",
            "redirect_url": "https://pluraluat.v2.pinepg.in/api/v3/checkout-bff/redirect/checkout?token=test1",
            "status": "CREATED",
            "merchant_order_reference": "ao_t1_pro_abc123",
            "integration_mode": "REDIRECT",
            "allowed_payment_methods": [
                "CARD", "UPI", "NETBANKING", "WALLET", "CREDIT_EMI", "DEBIT_EMI",
            ],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.return_value.post.return_value = mock_resp

        from core.billing.pinelabs_client import create_payment_order

        result = create_payment_order(tenant_id="t1", plan="pro")

        assert result["order_id"] == "v1-order-12345"
        assert "checkout-bff/redirect/checkout" in result["challenge_url"]
        assert result["status"] == "CREATED"
        assert result["currency"] == "INR"
        assert result["amount"] == 9_999_00
        assert "CARD" in result["allowed_payment_methods"]
        assert "UPI" in result["allowed_payment_methods"]

        # Verify the POST was called with correct endpoint
        call_args = mock_http.return_value.post.call_args
        assert "/checkout/v1/orders" in call_args[0][0]

        # Verify the body has all payment methods
        body = call_args[1]["json"]
        assert body["allowed_payment_methods"] == [
            "CARD", "UPI", "NETBANKING", "WALLET", "CREDIT_EMI", "DEBIT_EMI",
        ]
        assert body["order_amount"]["value"] == 9_999_00
        assert body["order_amount"]["currency"] == "INR"

    @patch("core.billing.pinelabs_client._get_access_token", return_value="test_token")
    @patch("core.billing.pinelabs_client._get_http")
    def test_create_order_with_customer_details(self, mock_http, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "order_id": "v1-order-99",
            "redirect_url": "https://pluraluat.v2.pinepg.in/api/v3/checkout-bff/redirect/checkout?token=test2",
            "status": "CREATED",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.return_value.post.return_value = mock_resp

        from core.billing.pinelabs_client import create_payment_order

        result = create_payment_order(
            tenant_id="t2",
            plan="enterprise",
            customer_email="cfo@example.com",
            customer_name="Ramesh Kumar",
            customer_phone="9876543210",
        )

        assert result["order_id"] == "v1-order-99"

        body = mock_http.return_value.post.call_args[1]["json"]
        customer = body["purchase_details"]["customer"]
        assert customer["email_id"] == "cfo@example.com"
        assert customer["first_name"] == "Ramesh"
        assert customer["last_name"] == "Kumar"
        assert customer["mobile_number"] == "9876543210"
        assert customer["country_code"] == "91"

    def test_create_order_rejects_unknown_plan(self):
        from core.billing.pinelabs_client import create_payment_order

        with pytest.raises(ValueError, match="Unknown plan"):
            create_payment_order(tenant_id="t1", plan="nonexistent")


class TestPluralGetOrderStatus:
    """Plural order status verification."""

    @patch("core.billing.pinelabs_client._get_access_token", return_value="test_token")
    @patch("core.billing.pinelabs_client._get_http")
    def test_get_order_status_processed(self, mock_http, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {
            "order_id": "v1-order-12345",
            "merchant_order_reference": "ao_t1_pro_abc",
            "status": "PROCESSED",
            "order_amount": {"value": 9_999_00, "currency": "INR"},
            "payments": [{"payment_id": "pay_1", "status": "CAPTURED"}],
            "created_at": "2026-04-09T10:00:00Z",
            "updated_at": "2026-04-09T10:01:00Z",
        }}
        mock_resp.raise_for_status = MagicMock()
        mock_http.return_value.get.return_value = mock_resp

        from core.billing.pinelabs_client import get_order_status

        result = get_order_status("v1-order-12345")

        assert result["status"] == "PROCESSED"
        assert result["order_id"] == "v1-order-12345"
        assert len(result["payments"]) == 1

        # Verify GET called with correct URL
        call_args = mock_http.return_value.get.call_args
        assert "/pay/v1/orders/v1-order-12345" in call_args[0][0]


class TestPluralWebhookSignature:
    """Plural webhook HMAC-SHA256 signature verification."""

    def test_valid_signature_passes(self):
        from core.billing.pinelabs_client import verify_webhook_signature

        secret = base64.b64encode(b"test_webhook_secret_key").decode()
        webhook_id = "evt_12345"
        webhook_ts = str(int(time.time()))
        body = b'{"order_id":"v1-order-1","status":"PROCESSED"}'

        # Compute expected signature
        signed_content = f"{webhook_id}.{webhook_ts}.".encode() + body
        expected_sig = base64.b64encode(
            hmac.new(
                base64.b64decode(secret),
                signed_content,
                hashlib.sha256,
            ).digest()
        ).decode()

        with patch("core.billing.pinelabs_client._WEBHOOK_SECRET", secret):
            result = verify_webhook_signature(
                raw_body=body,
                webhook_id=webhook_id,
                webhook_timestamp=webhook_ts,
                webhook_signature=f"v1,{expected_sig}",
            )
            assert result is True

    def test_invalid_signature_fails(self):
        from core.billing.pinelabs_client import verify_webhook_signature

        secret = base64.b64encode(b"test_secret").decode()
        with patch("core.billing.pinelabs_client._WEBHOOK_SECRET", secret):
            result = verify_webhook_signature(
                raw_body=b'{"order_id":"1"}',
                webhook_id="evt_1",
                webhook_timestamp=str(int(time.time())),
                webhook_signature="v1,invalid_signature_base64",
            )
            assert result is False

    def test_missing_secret_fails(self):
        from core.billing.pinelabs_client import verify_webhook_signature

        with patch("core.billing.pinelabs_client._WEBHOOK_SECRET", ""):
            result = verify_webhook_signature(
                raw_body=b"{}",
                webhook_id="evt_1",
                webhook_timestamp=str(int(time.time())),
                webhook_signature="v1,sig",
            )
            assert result is False


class TestPluralWebhookHandler:
    """Full webhook handler flow."""

    def test_handle_webhook_success(self):
        from core.billing.pinelabs_client import handle_webhook

        secret = base64.b64encode(b"webhook_key").decode()
        webhook_id = "evt_abc"
        webhook_ts = str(int(time.time()))
        payload = {
            "order_id": "v1-order-42",
            "status": "PROCESSED",
            "merchant_order_reference": "ao_tenant1_pro_xyz",
        }
        body = json.dumps(payload).encode()

        signed_content = f"{webhook_id}.{webhook_ts}.".encode() + body
        sig = base64.b64encode(
            hmac.new(base64.b64decode(secret), signed_content, hashlib.sha256).digest()
        ).decode()

        headers = {
            "webhook-id": webhook_id,
            "webhook-timestamp": webhook_ts,
            "webhook-signature": f"v1,{sig}",
        }

        with patch("core.billing.pinelabs_client._WEBHOOK_SECRET", secret):
            result = handle_webhook(body, headers)

        assert result["order_id"] == "v1-order-42"
        assert result["status"] == "PROCESSED"
        assert result["tenant_id"] == "tenant1"
        assert result["plan"] == "pro"
        assert result["processed"] is True

    def test_handle_webhook_rejects_old_timestamp(self):
        from core.billing.pinelabs_client import handle_webhook

        old_ts = str(int(time.time()) - 600)  # 10 minutes ago
        headers = {
            "webhook-id": "evt_1",
            "webhook-timestamp": old_ts,
            "webhook-signature": "v1,sig",
        }

        with pytest.raises(ValueError, match="replay"):
            handle_webhook(b"{}", headers)

    def test_handle_webhook_rejects_missing_headers(self):
        from core.billing.pinelabs_client import handle_webhook

        with pytest.raises(ValueError, match="Missing"):
            handle_webhook(b"{}", {"webhook-id": "", "webhook-timestamp": "", "webhook-signature": ""})


# ── End-to-End Flow Test ────────────────────────────────────────────


class TestPluralE2ERedirectFlow:
    """End-to-end test: create order → redirect → callback → verify."""

    @patch("core.billing.pinelabs_client._get_access_token", return_value="e2e_token")
    @patch("core.billing.pinelabs_client._get_http")
    def test_full_redirect_payment_flow(self, mock_http, mock_token):
        """Simulate the complete Plural hosted checkout flow:
        1. Create order → get challenge_url
        2. (User pays on Plural page — simulated)
        3. Check order status → PROCESSED
        4. Webhook arrives → verified and processed
        """
        from core.billing.pinelabs_client import (
            ALL_PAYMENT_METHODS,
            create_payment_order,
            get_order_status,
            handle_webhook,
        )

        # Step 1: Create order
        mock_create_resp = MagicMock()
        mock_create_resp.json.return_value = {
            "order_id": "v1-e2e-order-001",
            "redirect_url": "https://pluraluat.v2.pinepg.in/api/v3/checkout-bff/redirect/checkout?token=e2e1",
            "status": "CREATED",
            "merchant_order_reference": "ao_e2e_pro_test1",
            "integration_mode": "REDIRECT",
            "allowed_payment_methods": ALL_PAYMENT_METHODS,
        }
        mock_create_resp.raise_for_status = MagicMock()
        mock_http.return_value.post.return_value = mock_create_resp

        order = create_payment_order(tenant_id="e2e", plan="pro")

        assert order["order_id"] == "v1-e2e-order-001"
        assert "checkout-bff/redirect/checkout" in order["challenge_url"]
        assert order["status"] == "CREATED"
        assert len(order["allowed_payment_methods"]) == 6

        # Step 2: Simulate user completing payment (on Plural page)
        # ... user picks UPI/Card/NetBanking on the hosted checkout page ...

        # Step 3: Check status after redirect back
        mock_status_resp = MagicMock()
        mock_status_resp.json.return_value = {"data": {
            "order_id": "v1-e2e-order-001",
            "merchant_order_reference": "ao_e2e_pro_test1",
            "status": "PROCESSED",
            "order_amount": {"value": 9_999_00, "currency": "INR"},
            "payments": [
                {
                    "payment_id": "pay_upi_001",
                    "status": "CAPTURED",
                    "payment_method": "UPI",
                    "amount": {"value": 9_999_00, "currency": "INR"},
                }
            ],
        }}
        mock_status_resp.raise_for_status = MagicMock()
        mock_http.return_value.get.return_value = mock_status_resp

        status = get_order_status("v1-e2e-order-001")
        assert status["status"] == "PROCESSED"
        assert status["payments"][0]["payment_method"] == "UPI"
        assert status["payments"][0]["status"] == "CAPTURED"

        # Step 4: Webhook confirmation
        secret = base64.b64encode(b"e2e_webhook_secret").decode()
        webhook_id = "evt_e2e_001"
        webhook_ts = str(int(time.time()))
        webhook_payload = {
            "order_id": "v1-e2e-order-001",
            "status": "PROCESSED",
            "merchant_order_reference": "ao_e2e_pro_test1",
        }
        webhook_body = json.dumps(webhook_payload).encode()

        signed = f"{webhook_id}.{webhook_ts}.".encode() + webhook_body
        sig = base64.b64encode(
            hmac.new(base64.b64decode(secret), signed, hashlib.sha256).digest()
        ).decode()

        with patch("core.billing.pinelabs_client._WEBHOOK_SECRET", secret):
            webhook_result = handle_webhook(
                webhook_body,
                {
                    "webhook-id": webhook_id,
                    "webhook-timestamp": webhook_ts,
                    "webhook-signature": f"v1,{sig}",
                },
            )

        assert webhook_result["processed"] is True
        assert webhook_result["tenant_id"] == "e2e"
        assert webhook_result["plan"] == "pro"
        assert webhook_result["order_id"] == "v1-e2e-order-001"


# ── Stripe SDK Tests ────────────────────────────────────────────────


class TestStripeCheckoutSession:
    """Stripe Checkout Session creation and verification."""

    @patch("core.billing.stripe_client._get_stripe")
    def test_create_checkout_session(self, mock_get_stripe):
        mock_stripe = MagicMock()

        # Mock Customer.search to return empty (no existing customer)
        mock_stripe.Customer.search.return_value = MagicMock(data=[])
        # Mock Customer.create
        mock_customer = MagicMock()
        mock_customer.id = "cus_test123"
        mock_stripe.Customer.create.return_value = mock_customer
        # Mock Checkout.Session.create
        mock_session = MagicMock()
        mock_session.id = "cs_test_session_001"
        mock_session.url = "https://checkout.stripe.com/c/pay/cs_test_session_001"
        mock_stripe.checkout.Session.create.return_value = mock_session

        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import create_checkout_session

        with patch("core.billing.stripe_client.PLAN_PRICE_MAP", {"pro": "price_pro_real"}):
            result = create_checkout_session(
                tenant_id="t1",
                plan="pro",
                customer_email="cfo@example.com",
                customer_name="Test User",
            )

        assert result["session_id"] == "cs_test_session_001"
        assert "checkout.stripe.com" in result["checkout_url"]
        assert result["customer_id"] == "cus_test123"

        # Verify checkout session created with correct params
        call_kwargs = mock_stripe.checkout.Session.create.call_args[1]
        assert call_kwargs["mode"] == "subscription"
        assert call_kwargs["customer"] == "cus_test123"
        assert call_kwargs["metadata"]["tenant_id"] == "t1"
        assert call_kwargs["metadata"]["plan"] == "pro"

    @patch("core.billing.stripe_client._get_stripe")
    def test_create_checkout_rejects_missing_price(self, mock_get_stripe):
        mock_get_stripe.return_value = MagicMock()

        from core.billing.stripe_client import create_checkout_session

        # Empty price map means no price ID configured
        with patch("core.billing.stripe_client.PLAN_PRICE_MAP", {"pro": ""}):
            with pytest.raises(ValueError, match="Unknown plan or missing price"):
                create_checkout_session(tenant_id="t1", plan="pro")


class TestStripeSessionVerification:
    """Stripe checkout session verification after redirect."""

    @patch("core.billing.stripe_client._get_stripe")
    def test_verify_successful_session(self, mock_get_stripe):
        mock_stripe = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {"tenant_id": "t1", "plan": "pro"}
        mock_session.payment_status = "paid"
        mock_session.status = "complete"
        mock_session.subscription = "sub_123"
        mock_session.customer = "cus_123"
        mock_stripe.checkout.Session.retrieve.return_value = mock_session
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import verify_checkout_session

        result = verify_checkout_session("cs_test_001")

        assert result["verified"] is True
        assert result["tenant_id"] == "t1"
        assert result["plan"] == "pro"
        assert result["status"] == "complete"
        assert result["payment_status"] == "paid"
        assert result["subscription_id"] == "sub_123"

    @patch("core.billing.stripe_client._get_stripe")
    def test_verify_unpaid_session(self, mock_get_stripe):
        mock_stripe = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {"tenant_id": "t1", "plan": "pro"}
        mock_session.payment_status = "unpaid"
        mock_session.status = "open"
        mock_session.subscription = None
        mock_session.customer = "cus_123"
        mock_stripe.checkout.Session.retrieve.return_value = mock_session
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import verify_checkout_session

        result = verify_checkout_session("cs_test_002")
        assert result["verified"] is False


class TestStripeWebhookActivation:
    """Stripe webhook processes checkout.session.completed and activates subscription."""

    @patch("core.billing.stripe_client._get_stripe")
    @patch("core.billing.stripe_client._activate_subscription")
    def test_checkout_completed_activates_subscription(self, mock_activate, mock_get_stripe):
        mock_stripe = MagicMock()
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "created": int(time.time()),
            "data": {
                "object": {
                    "metadata": {"tenant_id": "t1", "plan": "enterprise"},
                    "payment_status": "paid",
                    "subscription": "sub_e2e_001",
                    "customer": "cus_e2e_001",
                }
            },
        }
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import handle_webhook

        result = handle_webhook(b'{"type":"checkout.session.completed"}', "sig_test")

        assert result["event_type"] == "checkout.session.completed"
        assert result["processed"] is True
        assert result["tenant_id"] == "t1"
        assert result["plan"] == "enterprise"

        # Verify subscription activation was called
        mock_activate.assert_called_once_with(
            tenant_id="t1",
            plan="enterprise",
            subscription_id="sub_e2e_001",
            customer_id="cus_e2e_001",
        )

    @patch("core.billing.stripe_client._get_stripe")
    @patch("core.billing.stripe_client._deactivate_subscription")
    def test_subscription_deleted_deactivates(self, mock_deactivate, mock_get_stripe):
        mock_stripe = MagicMock()
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "customer.subscription.deleted",
            "created": int(time.time()),
            "data": {
                "object": {
                    "metadata": {"tenant_id": "t1"},
                }
            },
        }
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import handle_webhook

        result = handle_webhook(b'{"type":"customer.subscription.deleted"}', "sig_test")

        assert result["processed"] is True
        assert result["cancelled"] is True
        mock_deactivate.assert_called_once_with("t1")


class TestStripeCustomerPortal:
    """Stripe Customer Portal session creation."""

    @patch("core.billing.stripe_client._get_stripe")
    @patch("core.billing.usage_tracker._get_redis")
    def test_create_portal_session(self, mock_redis_fn, mock_get_stripe):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "cus_portal_123"
        mock_redis_fn.return_value = mock_redis

        mock_stripe = MagicMock()
        mock_portal = MagicMock()
        mock_portal.url = "https://billing.stripe.com/p/session/test_portal"
        mock_stripe.billing_portal.Session.create.return_value = mock_portal
        mock_get_stripe.return_value = mock_stripe

        from core.billing.stripe_client import create_portal_session

        url = create_portal_session("t1")

        assert "billing.stripe.com" in url
        mock_stripe.billing_portal.Session.create.assert_called_once()


class TestStripeE2ECheckoutFlow:
    """End-to-end Stripe Checkout flow: create → verify → webhook → activate."""

    @patch("core.billing.stripe_client._get_stripe")
    @patch("core.billing.stripe_client._activate_subscription")
    def test_full_stripe_checkout_flow(self, mock_activate, mock_get_stripe):
        mock_stripe = MagicMock()
        mock_get_stripe.return_value = mock_stripe

        # Step 1: Create checkout session
        mock_stripe.Customer.search.return_value = MagicMock(data=[])
        mock_customer = MagicMock()
        mock_customer.id = "cus_e2e_stripe"
        mock_stripe.Customer.create.return_value = mock_customer

        mock_session = MagicMock()
        mock_session.id = "cs_e2e_001"
        mock_session.url = "https://checkout.stripe.com/c/pay/cs_e2e_001"
        mock_stripe.checkout.Session.create.return_value = mock_session

        from core.billing.stripe_client import create_checkout_session

        with patch("core.billing.stripe_client.PLAN_PRICE_MAP", {"pro": "price_pro_live"}):
            checkout = create_checkout_session(tenant_id="e2e_stripe", plan="pro")

        assert checkout["session_id"] == "cs_e2e_001"
        assert "checkout.stripe.com" in checkout["checkout_url"]

        # Step 2: User pays on Stripe... (simulated)

        # Step 3: Verify session after redirect
        mock_verified_session = MagicMock()
        mock_verified_session.metadata = {"tenant_id": "e2e_stripe", "plan": "pro"}
        mock_verified_session.payment_status = "paid"
        mock_verified_session.status = "complete"
        mock_verified_session.subscription = "sub_e2e_stripe"
        mock_verified_session.customer = "cus_e2e_stripe"
        mock_stripe.checkout.Session.retrieve.return_value = mock_verified_session

        from core.billing.stripe_client import verify_checkout_session

        verified = verify_checkout_session("cs_e2e_001")
        assert verified["verified"] is True
        assert verified["plan"] == "pro"

        # Step 4: Webhook confirms payment
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "created": int(time.time()),
            "data": {
                "object": {
                    "metadata": {"tenant_id": "e2e_stripe", "plan": "pro"},
                    "payment_status": "paid",
                    "subscription": "sub_e2e_stripe",
                    "customer": "cus_e2e_stripe",
                }
            },
        }

        from core.billing.stripe_client import handle_webhook

        webhook_result = handle_webhook(b'{"type":"checkout.session.completed"}', "sig")
        assert webhook_result["processed"] is True
        assert webhook_result["tenant_id"] == "e2e_stripe"

        # Verify subscription was activated
        mock_activate.assert_called_once_with(
            tenant_id="e2e_stripe",
            plan="pro",
            subscription_id="sub_e2e_stripe",
            customer_id="cus_e2e_stripe",
        )
