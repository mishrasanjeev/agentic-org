"""Tests for Tier 1 features: wait step, A/B engine, webhooks, intent, push.

Covers the wait/wait_for_event workflow steps, the ABTestEngine,
webhook parsing (SendGrid/Mailchimp/MoEngage), the IntentAggregator,
push notification sender, new marketing connectors (Bombora/G2/TrustRadius),
and the ABM API endpoints.

All external services (Redis, Celery, HTTP APIs) are mocked.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
#  WAIT STEP
# ═══════════════════════════════════════════════════════════════════════════


class TestWaitStep:
    """Tests for the wait (delay) workflow step."""

    @pytest.fixture(autouse=True)
    def _patch_celery(self):
        """Prevent Celery from actually scheduling tasks."""
        with patch(
            "core.tasks.workflow_tasks.resume_workflow_wait",
            new=MagicMock(apply_async=MagicMock(), delay=MagicMock()),
        ):
            yield

    def _run(self, step: dict, state: dict | None = None) -> dict:
        from workflows.step_types import execute_step

        return asyncio.run(
            execute_step(step, state or {"id": "run-1"})
        )

    def test_wait_with_duration_hours(self):
        result = self._run({"id": "w1", "type": "wait", "duration_hours": 2})
        assert result["status"] == "waiting_delay"
        assert "resume_at" in result

    def test_wait_with_no_duration_completes_immediately(self):
        result = self._run({"id": "w2", "type": "wait"})
        assert result["status"] == "completed"

    def test_wait_with_past_until_completes_immediately(self):
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        result = self._run({"id": "w3", "type": "wait", "until": past})
        assert result["status"] == "completed"

    def test_wait_with_future_until_returns_waiting_delay(self):
        future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
        result = self._run({"id": "w4", "type": "wait", "until": future})
        assert result["status"] == "waiting_delay"
        assert "resume_at" in result

    def test_wait_with_duration_minutes(self):
        result = self._run({"id": "w5", "type": "wait", "duration_minutes": 30})
        assert result["status"] == "waiting_delay"

    def test_wait_with_duration_seconds(self):
        result = self._run({"id": "w6", "type": "wait", "duration_seconds": 120})
        assert result["status"] == "waiting_delay"

    def test_wait_with_zero_duration_completes_immediately(self):
        result = self._run({
            "id": "w7",
            "type": "wait",
            "duration_hours": 0,
            "duration_minutes": 0,
            "duration_seconds": 0,
        })
        assert result["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════
#  WAIT FOR EVENT
# ═══════════════════════════════════════════════════════════════════════════


class TestWaitForEvent:
    """Tests for the wait_for_event workflow step."""

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        mock_redis_cls = MagicMock()
        mock_redis_inst = AsyncMock()
        mock_redis_inst.set = AsyncMock()
        mock_redis_inst.aclose = AsyncMock()
        mock_redis_cls.from_url.return_value = mock_redis_inst
        self._mock_redis = mock_redis_inst

        with patch("workflows.step_types.aioredis", mock_redis_cls, create=True):
            with patch(
                "core.tasks.workflow_tasks.timeout_workflow_event",
                new=MagicMock(apply_async=MagicMock()),
            ):
                with patch("core.config.settings", new=MagicMock(redis_url="redis://mock:6379/0")):
                    yield

    def _run(self, step: dict, state: dict | None = None) -> dict:
        from workflows.step_types import execute_step

        return asyncio.run(
            execute_step(step, state or {"id": "run-42"})
        )

    def test_returns_waiting_event_with_correct_type(self):
        result = self._run({
            "id": "we1",
            "type": "wait_for_event",
            "event_type": "email.opened",
        })
        assert result["status"] == "waiting_event"
        assert result["event_type"] == "email.opened"

    def test_has_timeout_at(self):
        result = self._run({
            "id": "we2",
            "type": "wait_for_event",
            "event_type": "email.clicked",
            "timeout_hours": 24,
        })
        assert "timeout_at" in result

    def test_default_timeout_48_hours(self):
        result = self._run({
            "id": "we3",
            "type": "wait_for_event",
            "event_type": "webhook.received",
        })
        timeout_at = datetime.fromisoformat(result["timeout_at"])
        # Should be roughly 48 hours from now
        diff = (timeout_at - datetime.now(UTC)).total_seconds()
        assert 47 * 3600 < diff < 49 * 3600


# ═══════════════════════════════════════════════════════════════════════════
#  A/B TEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════


class TestABEngine:
    """Tests for the ABTestEngine in-memory mode."""

    @pytest.fixture(autouse=True)
    def engine(self):
        """Create an engine with Redis disabled (in-memory fallback)."""
        with patch("core.marketing.ab_test.redis", create=True):
            from core.marketing.ab_test import ABTestEngine

            self.engine = ABTestEngine(redis_url=None)
            # Force in-memory mode
            self.engine._redis = None
            yield

    def _make_variants(self) -> list[dict]:
        return [
            {"id": "v-a", "subject": "Subject A", "content_html": "<p>A</p>"},
            {"id": "v-b", "subject": "Subject B", "content_html": "<p>B</p>"},
        ]

    def test_create_test_returns_test_id(self):
        test_id = self.engine.create_test(
            campaign_id="camp-1",
            variants=self._make_variants(),
        )
        assert isinstance(test_id, str)
        assert len(test_id) == 36  # UUID format

    def test_record_metrics_updates_variant_counts(self):
        test_id = self.engine.create_test(
            campaign_id="camp-2",
            variants=self._make_variants(),
        )
        result = self.engine.record_metrics(
            test_id, "v-a", opens=50, clicks=10, conversions=2, sent=100
        )
        assert result["status"] == "updated"
        assert result["metrics"]["opens"] == 50
        assert result["metrics"]["sent"] == 100

    def test_check_winner_returns_none_before_min_sample(self):
        test_id = self.engine.create_test(
            campaign_id="camp-3",
            variants=self._make_variants(),
        )
        # Send < 100 per variant
        self.engine.record_metrics(test_id, "v-a", opens=5, sent=50)
        self.engine.record_metrics(test_id, "v-b", opens=3, sent=50)
        winner = self.engine.check_winner(test_id)
        assert winner is None

    def test_check_winner_returns_winner_after_sufficient_sample(self):
        test_id = self.engine.create_test(
            campaign_id="camp-4",
            variants=self._make_variants(),
        )
        self.engine.record_metrics(test_id, "v-a", opens=60, sent=200)
        self.engine.record_metrics(test_id, "v-b", opens=30, sent=200)
        winner = self.engine.check_winner(test_id)
        assert winner is not None
        assert winner["winner_variant_id"] == "v-a"
        assert "confidence" in winner

    def test_finalize_test_uses_auto_winner(self):
        test_id = self.engine.create_test(
            campaign_id="camp-5",
            variants=self._make_variants(),
        )
        self.engine.record_metrics(test_id, "v-a", opens=80, sent=200)
        self.engine.record_metrics(test_id, "v-b", opens=40, sent=200)
        self.engine.check_winner(test_id)
        result = self.engine.finalize_test(test_id)
        assert result["status"] == "finalized"
        assert result["winner_id"] == "v-a"
        assert result["was_override"] is False

    def test_finalize_test_respects_cmo_override(self):
        test_id = self.engine.create_test(
            campaign_id="camp-6",
            variants=self._make_variants(),
        )
        self.engine.record_metrics(test_id, "v-a", opens=80, sent=200)
        self.engine.record_metrics(test_id, "v-b", opens=40, sent=200)
        result = self.engine.finalize_test(test_id, winner_override="v-b")
        assert result["status"] == "finalized"
        assert result["winner_id"] == "v-b"
        assert result["was_override"] is True

    def test_finalize_with_invalid_override(self):
        test_id = self.engine.create_test(
            campaign_id="camp-7",
            variants=self._make_variants(),
        )
        result = self.engine.finalize_test(test_id, winner_override="invalid-id")
        assert result.get("error") == "invalid_variant_id"

    def test_get_results_returns_all_variants_with_metrics(self):
        test_id = self.engine.create_test(
            campaign_id="camp-8",
            variants=self._make_variants(),
        )
        self.engine.record_metrics(test_id, "v-a", opens=10, sent=50)
        results = self.engine.get_results(test_id)
        assert results is not None
        assert len(results["variants"]) == 2
        variant_a = next(v for v in results["variants"] if v["id"] == "v-a")
        assert variant_a["metrics"]["opens"] == 10

    def test_get_results_nonexistent_returns_none(self):
        results = self.engine.get_results("nonexistent-test-id")
        assert results is None

    def test_winning_metric_enum_values(self):
        from core.marketing.ab_test import WinningMetric

        assert WinningMetric.OPEN_RATE == "open_rate"
        assert WinningMetric.CLICK_RATE == "click_rate"
        assert WinningMetric.CONVERSION_RATE == "conversion_rate"

    def test_record_metrics_increments(self):
        test_id = self.engine.create_test(
            campaign_id="camp-incr",
            variants=self._make_variants(),
        )
        self.engine.record_metrics(test_id, "v-a", opens=10, sent=50)
        self.engine.record_metrics(test_id, "v-a", opens=5, sent=30)
        results = self.engine.get_results(test_id)
        variant_a = next(v for v in results["variants"] if v["id"] == "v-a")
        assert variant_a["metrics"]["opens"] == 15
        assert variant_a["metrics"]["sent"] == 80

    def test_record_metrics_unknown_test(self):
        result = self.engine.record_metrics("no-such-test", "v-a", opens=1)
        assert result.get("error") == "test_not_found"

    def test_record_metrics_unknown_variant(self):
        test_id = self.engine.create_test(
            campaign_id="camp-unkn",
            variants=self._make_variants(),
        )
        result = self.engine.record_metrics(test_id, "v-nonexistent", opens=1)
        assert result.get("error") == "variant_not_found"

    def test_high_confidence_margin(self):
        test_id = self.engine.create_test(
            campaign_id="camp-hc",
            variants=self._make_variants(),
        )
        # A has 30% open rate, B has 10% -- > 10% margin -> high confidence
        self.engine.record_metrics(test_id, "v-a", opens=60, sent=200)
        self.engine.record_metrics(test_id, "v-b", opens=20, sent=200)
        winner = self.engine.check_winner(test_id)
        assert winner["confidence"] == "high"
        assert winner["should_auto_send"] is True


# ═══════════════════════════════════════════════════════════════════════════
#  WEBHOOK PARSING
# ═══════════════════════════════════════════════════════════════════════════


class TestWebhookParsing:
    """Tests for SendGrid, Mailchimp, and MoEngage webhook endpoints."""

    @pytest.fixture(scope="class")
    def app(self):
        from api.main import app as _app

        @asynccontextmanager
        async def _noop_lifespan(app):
            yield

        _app.router.lifespan_context = _noop_lifespan
        return _app

    @pytest.fixture
    def client(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    @pytest.fixture(autouse=True)
    def _mock_redis(self):
        mock_redis = AsyncMock()
        mock_redis.hset = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, []))
        mock_redis.aclose = AsyncMock()
        with patch("api.v1.webhooks._get_redis", return_value=mock_redis):
            self._redis = mock_redis
            yield

    def test_sendgrid_event_array_parsed(self, client):
        events = [
            {
                "email": "user@example.com",
                "event": "open",
                "sg_message_id": "msg-123",
                "timestamp": 1711929600,
                "category": ["campaign-abc"],
            },
            {
                "email": "user2@example.com",
                "event": "click",
                "sg_message_id": "msg-456",
                "timestamp": 1711929700,
                "url": "https://example.com",
            },
        ]
        resp = client.post("/api/v1/webhooks/email/sendgrid", content=json.dumps(events))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["processed"] == 2

    def test_mailchimp_form_data_parsed(self, client):
        form_data = {
            "type": "subscribe",
            "fired_at": "2026-03-29 12:00:00",
            "data[email]": "subscriber@example.com",
            "data[list_id]": "list-abc",
        }
        resp = client.post("/api/v1/webhooks/email/mailchimp", data=form_data)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["type"] == "subscribe"

    def test_moengage_callback_parsed(self, client):
        payload = {
            "event_type": "EMAIL_OPEN",
            "email": "user@example.com",
            "campaign_id": "moe-camp-1",
            "timestamp": "2026-03-29T12:00:00Z",
        }
        resp = client.post("/api/v1/webhooks/email/moengage", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["event_type"] == "opened"

    def test_sendgrid_stores_events(self, client):
        events = [
            {
                "email": "stored@example.com",
                "event": "delivered",
                "sg_message_id": "msg-store",
                "timestamp": 1711929600,
            }
        ]
        client.post("/api/v1/webhooks/email/sendgrid", content=json.dumps(events))
        # Redis hset should have been called
        assert self._redis.hset.called

    def test_mailchimp_missing_type_returns_400(self, client):
        resp = client.post("/api/v1/webhooks/email/mailchimp", data={})
        assert resp.status_code == 400

    def test_moengage_missing_fields_returns_400(self, client):
        resp = client.post("/api/v1/webhooks/email/moengage", json={})
        assert resp.status_code == 400

    def test_sendgrid_invalid_json_returns_400(self, client):
        resp = client.post(
            "/api/v1/webhooks/email/sendgrid",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
#  INTENT AGGREGATOR
# ═══════════════════════════════════════════════════════════════════════════


class TestIntentAggregator:
    """Tests for the IntentAggregator composite scoring logic."""

    @pytest.fixture(autouse=True)
    def _patch_connectors(self):
        """Mock all three connector classes so no HTTP calls are made."""
        mock_bombora = AsyncMock()
        mock_bombora.connect = AsyncMock()
        mock_bombora.disconnect = AsyncMock()
        mock_bombora.get_surge_scores = AsyncMock(
            return_value={
                "companies": [{"surge_score": 80, "topics": [{"name": "Cloud"}]}],
            }
        )

        mock_g2 = AsyncMock()
        mock_g2.connect = AsyncMock()
        mock_g2.disconnect = AsyncMock()
        mock_g2.get_intent_signals = AsyncMock(
            return_value={
                "intent_score": 60,
                "signals": [{"topic": "CRM"}],
            }
        )

        mock_tr = AsyncMock()
        mock_tr.connect = AsyncMock()
        mock_tr.disconnect = AsyncMock()
        mock_tr.get_buyer_intent = AsyncMock(
            return_value={
                "intent_score": 50,
                "activity": [{"topic": "Analytics"}],
            }
        )

        with patch(
            "core.marketing.intent_aggregator.BomboraConnector",
            return_value=mock_bombora,
        ), patch(
            "core.marketing.intent_aggregator.G2Connector",
            return_value=mock_g2,
        ), patch(
            "core.marketing.intent_aggregator.TrustRadiusConnector",
            return_value=mock_tr,
        ):
            self._mock_bombora = mock_bombora
            self._mock_g2 = mock_g2
            self._mock_tr = mock_tr
            yield

    def test_aggregate_intent_merges_three_sources(self):
        from core.marketing.intent_aggregator import IntentAggregator

        agg = IntentAggregator()
        result = asyncio.run(
            agg.aggregate_intent("acme.com")
        )
        assert result["domain"] == "acme.com"
        assert "composite_score" in result
        assert "bombora_surge" in result
        assert "g2_signals" in result
        assert "trustradius_intent" in result
        assert "topics" in result

    def test_weights_bombora_40_g2_30_trustradius_30(self):
        from core.marketing.intent_aggregator import IntentAggregator

        agg = IntentAggregator()
        result = asyncio.run(
            agg.aggregate_intent("test.com")
        )
        # Bombora=80, G2=60, TR=50
        # Expected: 80*0.4 + 60*0.3 + 50*0.3 = 32 + 18 + 15 = 65
        assert result["composite_score"] == 65.0

    def test_batch_aggregate_handles_list(self):
        from core.marketing.intent_aggregator import IntentAggregator

        agg = IntentAggregator()
        results = asyncio.run(
            agg.batch_aggregate(
                ["a.com", "b.com", "c.com"],
                configs={"bombora": {}, "g2": {}, "trustradius": {}},
            )
        )
        assert len(results) == 3
        for r in results:
            assert "composite_score" in r

    def test_missing_provider_returns_partial_score(self):
        from core.marketing.intent_aggregator import IntentAggregator

        # Make Bombora fail
        self._mock_bombora.get_surge_scores = AsyncMock(
            side_effect=Exception("Bombora API down")
        )
        agg = IntentAggregator()
        result = asyncio.run(
            agg.aggregate_intent("failing.com")
        )
        # Should not crash, just have Bombora at 0
        assert result["bombora_surge"] == 0.0
        # G2 and TR should still contribute
        assert result["composite_score"] > 0

    def test_topics_merged_from_all_providers(self):
        from core.marketing.intent_aggregator import IntentAggregator

        agg = IntentAggregator()
        result = asyncio.run(
            agg.aggregate_intent("topics.com")
        )
        # Should include Cloud (Bombora), CRM (G2), Analytics (TR)
        assert "Cloud" in result["topics"]
        assert "CRM" in result["topics"]
        assert "Analytics" in result["topics"]

    def test_last_updated_is_iso_format(self):
        from core.marketing.intent_aggregator import IntentAggregator

        agg = IntentAggregator()
        result = asyncio.run(
            agg.aggregate_intent("ts.com")
        )
        # Should parse as ISO datetime without error
        dt = datetime.fromisoformat(result["last_updated"])
        assert dt.year >= 2026


# ═══════════════════════════════════════════════════════════════════════════
#  PUSH SENDER
# ═══════════════════════════════════════════════════════════════════════════


class TestPushSender:
    """Tests for the push notification sender (in-memory mode)."""

    @pytest.fixture(autouse=True)
    def _setup_memory_mode(self):
        """Ensure Redis is unavailable so sender falls back to in-memory."""
        with patch("core.push.sender._get_redis", return_value=None):
            # Clear any prior in-memory state
            from core.push import sender
            sender._memory_store.clear()
            yield

    def test_save_subscription_stores_in_memory(self):
        from core.push.sender import _memory_store, save_subscription

        sub = {
            "endpoint": "https://push.example.com/sub1",
            "keys": {"p256dh": "abc", "auth": "xyz"},
        }
        asyncio.run(
            save_subscription("tenant-1", sub)
        )
        assert len(_memory_store.get("tenant-1", set())) == 1

    def test_send_push_empty_subscription_returns_zero(self):
        from core.push.sender import send_push_notification

        with patch("core.push.sender.get_vapid_keys", return_value=("pub", "priv")):
            result = asyncio.run(
                send_push_notification("empty-tenant", "Test", "Body")
            )
        assert result["sent"] == 0
        assert result["failed"] == 0

    def test_send_push_sends_to_all_subscriptions(self):
        from core.push.sender import save_subscription, send_push_notification

        for i in range(3):
            asyncio.run(save_subscription("tenant-multi", {
                "endpoint": f"https://push.example.com/sub{i}",
                "keys": {"p256dh": f"key{i}", "auth": f"auth{i}"},
            }))

        with patch("core.push.sender.get_vapid_keys", return_value=("pub", "priv")):
            with patch("pywebpush.webpush") as mock_push:
                result = asyncio.run(
                    send_push_notification("tenant-multi", "Title", "Body")
                )
        assert mock_push.call_count == 3
        assert result["sent"] == 3

    def test_410_gone_removes_stale_subscription(self):
        from core.push.sender import (
            save_subscription,
            send_push_notification,
        )

        asyncio.run(save_subscription("tenant-stale", {
            "endpoint": "https://push.example.com/stale",
            "keys": {"p256dh": "k", "auth": "a"},
        }))

        # Simulate 410 Gone from push service
        from pywebpush import WebPushException

        mock_response = MagicMock()
        mock_response.status_code = 410

        exc = WebPushException("Gone")
        exc.response = mock_response

        with patch("core.push.sender.get_vapid_keys", return_value=("pub", "priv")):
            with patch("pywebpush.webpush", side_effect=exc):
                result = asyncio.run(
                    send_push_notification("tenant-stale", "Title", "Body")
                )
        assert result["stale_removed"] == 1
        assert result["sent"] == 0

    def test_remove_subscription(self):
        from core.push.sender import (
            _memory_store,
            remove_subscription,
            save_subscription,
        )

        asyncio.run(save_subscription("tenant-rm", {
            "endpoint": "https://push.example.com/to-remove",
            "keys": {"p256dh": "k", "auth": "a"},
        })
        )
        assert len(_memory_store.get("tenant-rm", set())) == 1

        asyncio.run(
            remove_subscription("tenant-rm", "https://push.example.com/to-remove")
        )
        assert len(_memory_store.get("tenant-rm", set())) == 0


# ═══════════════════════════════════════════════════════════════════════════
#  NEW CONNECTORS (Bombora / G2 / TrustRadius)
# ═══════════════════════════════════════════════════════════════════════════


class TestNewConnectors:
    """Verify new marketing connectors are registered and have correct tools."""

    def test_bombora_connector_has_4_tools(self):
        from connectors.marketing.bombora import BomboraConnector

        c = BomboraConnector({})
        c._register_tools()
        tools = list(c._tool_registry.keys())
        assert len(tools) == 4
        assert "get_surge_scores" in tools
        assert "get_topic_clusters" in tools
        assert "get_weekly_report" in tools
        assert "search_companies" in tools

    def test_g2_connector_has_4_tools(self):
        from connectors.marketing.g2 import G2Connector

        c = G2Connector({})
        c._register_tools()
        tools = list(c._tool_registry.keys())
        assert len(tools) == 4
        assert "get_intent_signals" in tools
        assert "get_product_reviews" in tools
        assert "get_comparison_data" in tools
        assert "get_category_leaders" in tools

    def test_trustradius_connector_has_4_tools(self):
        from connectors.marketing.trustradius import TrustRadiusConnector

        c = TrustRadiusConnector({})
        c._register_tools()
        tools = list(c._tool_registry.keys())
        assert len(tools) == 4
        assert "get_buyer_intent" in tools
        assert "get_product_reviews" in tools
        assert "get_comparison_traffic" in tools
        assert "search_vendors" in tools

    def test_all_three_registered_in_registry(self):
        # Force registration by importing the package
        import connectors  # noqa: F401
        from connectors.registry import ConnectorRegistry

        names = ConnectorRegistry.all_names()
        assert "bombora" in names
        assert "g2" in names
        assert "trustradius" in names

    def test_bombora_connector_attributes(self):
        from connectors.marketing.bombora import BomboraConnector

        assert BomboraConnector.name == "bombora"
        assert BomboraConnector.category == "marketing"
        assert BomboraConnector.auth_type == "api_key"

    def test_g2_connector_attributes(self):
        from connectors.marketing.g2 import G2Connector

        assert G2Connector.name == "g2"
        assert G2Connector.category == "marketing"

    def test_trustradius_connector_attributes(self):
        from connectors.marketing.trustradius import TrustRadiusConnector

        assert TrustRadiusConnector.name == "trustradius"
        assert TrustRadiusConnector.category == "marketing"


# ═══════════════════════════════════════════════════════════════════════════
#  ABM API
# ═══════════════════════════════════════════════════════════════════════════
# TestABMApi moved to
# tests/integration/test_db_api_endpoints.py::TestABMApiIntegration
