"""Tests for billing module — tiers, limits, usage, webhooks, plans API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


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


class TestStripeWebhookValidatesSignature:
    """test_stripe_webhook_validates_signature — signature validation."""

    @patch("core.billing.stripe_client._get_stripe")
    def test_stripe_webhook_validates_signature(self, mock_get_stripe):
        mock_stripe = MagicMock()
        import time

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
    """test_india_pricing_in_inr — PineLabs plans have correct INR amounts."""

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
