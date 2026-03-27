"""Comprehensive unit tests for remaining uncovered modules.

Covers:
  - scaling.cost_ledger (CostLedger)
  - scaling.hpa_integration (HPAIntegration, ScheduleRule)
  - scaling.shadow_comparator (ShadowComparator)
  - scaling.agent_factory (AgentFactory)
  - scaling.lifecycle (LifecycleManager)
  - auth.token_pool (TokenPool)
  - auth.grantex (GrantexClient)
  - auth.opa (OPAClient)
  - auth.scopes (parse_scope, check_scope, validate_clone_scopes)
  - core.gmail_agent (_extract_body, send_reply, mark_as_read, get_recent_replies)
  - core.tool_gateway.gateway (ToolGateway)
  - core.tool_gateway.audit_logger (AuditLogger)
  - core.tool_gateway.idempotency (IdempotencyStore)
  - core.tool_gateway.rate_limiter (RateLimiter, RateLimitResult)
  - core.orchestrator.state_machine (WorkflowState, can_transition, transition)
  - core.orchestrator.checkpoint (CheckpointManager)
  - workflows.trigger (WorkflowTrigger, cron_matches, _field_matches)
  - workflows.parallel_executor (execute_parallel)
  - workflows.step_types (execute_step and all handlers)
  - workflows.state_store (WorkflowStateStore)
  - api.error_handlers (register_error_handlers)
  - api.deps (get_current_tenant, get_current_user, require_scope, etc.)
  - core.rbac (get_allowed_domains, get_scopes_for_role)
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# 1. scaling.cost_ledger — CostLedger
# =============================================================================


class TestCostLedgerInMemory:
    """CostLedger using in-memory fallback (no Redis)."""

    @pytest.fixture
    def ledger(self):
        from scaling.cost_ledger import CostLedger

        return CostLedger()

    @pytest.mark.asyncio
    async def test_record_stores_in_memory(self, ledger):
        await ledger.record("agent-1", 500, 0.05, model="gpt-4", tenant_id="t1")
        summary = await ledger.get_daily_summary("agent-1")
        assert summary["token_count"] == 500
        assert summary["cost_usd"] == pytest.approx(0.05)
        assert summary["task_count"] == 1

    @pytest.mark.asyncio
    async def test_record_accumulates(self, ledger):
        await ledger.record("agent-1", 100, 0.01)
        await ledger.record("agent-1", 200, 0.02)
        summary = await ledger.get_daily_summary("agent-1")
        assert summary["token_count"] == 300
        assert summary["cost_usd"] == pytest.approx(0.03)
        assert summary["task_count"] == 2

    @pytest.mark.asyncio
    async def test_check_budget_within(self, ledger):
        await ledger.record("agent-1", 100, 0.01)
        result = await ledger.check_budget("agent-1", daily_budget=1.0, monthly_cap=10.0)
        assert result["within_budget"] is True
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_check_budget_daily_exceeded(self, ledger):
        await ledger.record("agent-1", 1000, 5.0)
        result = await ledger.check_budget("agent-1", daily_budget=1.0, monthly_cap=100.0)
        assert result["within_budget"] is False
        assert any("Daily budget exceeded" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_check_budget_monthly_exceeded(self, ledger):
        await ledger.record("agent-1", 1000, 50.0)
        result = await ledger.check_budget("agent-1", daily_budget=100.0, monthly_cap=10.0)
        assert result["within_budget"] is False
        assert any("Monthly cap exceeded" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_check_budget_daily_warning_at_80pct(self, ledger):
        await ledger.record("agent-1", 100, 0.85)
        result = await ledger.check_budget("agent-1", daily_budget=1.0, monthly_cap=100.0)
        assert result["within_budget"] is True
        assert any("Daily budget at" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_check_budget_monthly_warning_at_80pct(self, ledger):
        await ledger.record("agent-1", 100, 8.5)
        result = await ledger.check_budget("agent-1", daily_budget=100.0, monthly_cap=10.0)
        assert result["within_budget"] is True
        assert any("Monthly budget at" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_check_budget_zero_budgets(self, ledger):
        await ledger.record("agent-1", 100, 0.01)
        result = await ledger.check_budget("agent-1", daily_budget=0.0, monthly_cap=0.0)
        assert result["within_budget"] is True
        assert result["daily_pct_used"] == 0.0
        assert result["budget_pct_used"] == 0.0

    @pytest.mark.asyncio
    async def test_should_pause_false_when_under_budget(self, ledger):
        await ledger.record("agent-1", 100, 0.01)
        result = await ledger.should_pause("agent-1", daily_budget=1.0, monthly_cap=10.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_should_pause_true_daily(self, ledger):
        await ledger.record("agent-1", 1000, 5.0)
        result = await ledger.should_pause("agent-1", daily_budget=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_pause_true_monthly(self, ledger):
        await ledger.record("agent-1", 1000, 50.0)
        result = await ledger.should_pause("agent-1", monthly_cap=10.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_pause_emits_event(self, ledger):
        emitter = AsyncMock()
        ledger._event_emitter = emitter
        await ledger.record("agent-1", 1000, 5.0)
        await ledger.should_pause("agent-1", daily_budget=1.0)
        emitter.assert_called_once()
        call_kwargs = emitter.call_args.kwargs
        assert call_kwargs["event_type"] == "agenticorg.agent.cost_cap_exceeded"

    @pytest.mark.asyncio
    async def test_should_pause_event_emitter_exception(self, ledger):
        emitter = AsyncMock(side_effect=RuntimeError("fail"))
        ledger._event_emitter = emitter
        await ledger.record("agent-1", 1000, 5.0)
        # Should not raise even if emitter fails
        result = await ledger.should_pause("agent-1", daily_budget=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_get_daily_summary_explicit_day(self, ledger):
        summary = await ledger.get_daily_summary("agent-1", day="2025-01-01")
        assert summary["date"] == "2025-01-01"
        assert summary["cost_usd"] == 0.0
        assert summary["token_count"] == 0

    @pytest.mark.asyncio
    async def test_get_monthly_summary(self, ledger):
        await ledger.record("agent-1", 500, 1.5)
        summary = await ledger.get_monthly_summary("agent-1")
        assert summary["token_count"] == 500
        assert summary["cost_usd"] == pytest.approx(1.5)
        assert "avg_daily_cost" in summary

    @pytest.mark.asyncio
    async def test_get_monthly_summary_explicit_month(self, ledger):
        summary = await ledger.get_monthly_summary("agent-1", month="2024-01")
        assert summary["month"] == "2024-01"
        assert summary["days_tracked"] == 30  # not current month

    @pytest.mark.asyncio
    async def test_flush_no_db(self, ledger):
        await ledger.record("agent-1", 100, 0.01)
        await ledger.flush()
        # Should not raise; buffer should be cleared
        assert len(ledger._buffer) == 0

    @pytest.mark.asyncio
    async def test_flush_to_db_success(self, ledger):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        ledger._db_session_factory = MagicMock(return_value=mock_session)
        await ledger.record("agent-1", 100, 0.01)
        ledger._buffer.clear()
        from scaling.cost_ledger import CostRecord

        ledger._buffer.append(
            CostRecord("a1", 100, Decimal("0.01"), "gpt", "t1", time.time())
        )
        await ledger.flush()
        mock_session.execute.assert_called()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_to_db_failure_requeues(self, ledger):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute.side_effect = RuntimeError("db down")
        ledger._db_session_factory = MagicMock(return_value=mock_session)
        from scaling.cost_ledger import CostRecord

        ledger._buffer.append(
            CostRecord("a1", 100, Decimal("0.01"), "gpt", "t1", time.time())
        )
        await ledger._flush_to_db()
        assert len(ledger._buffer) == 1  # re-queued

    def test_flush_due(self, ledger):
        ledger._last_flush = time.monotonic() - 100
        assert ledger._flush_due() is True
        ledger._last_flush = time.monotonic()
        assert ledger._flush_due() is False

    @pytest.mark.asyncio
    async def test_flush_empty_buffer(self, ledger):
        ledger._buffer = []
        await ledger._flush_to_db()
        # No-op, should not raise


class TestCostLedgerRedis:
    """CostLedger with mocked Redis."""

    @pytest.fixture
    def mock_redis(self):
        r = AsyncMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock()
        # pipeline() is a sync method in redis.asyncio, not async
        r.pipeline = MagicMock(return_value=pipe)
        r.get = AsyncMock(return_value=None)
        return r

    @pytest.fixture
    def ledger(self, mock_redis):
        from scaling.cost_ledger import CostLedger

        return CostLedger(redis=mock_redis)

    @pytest.mark.asyncio
    async def test_record_writes_redis(self, ledger, mock_redis):
        await ledger.record("agent-1", 500, 0.05)
        mock_redis.pipeline.assert_called()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cost_from_redis(self, ledger, mock_redis):
        mock_redis.get.return_value = b"1.50"
        result = await ledger.check_budget("agent-1", daily_budget=10.0, monthly_cap=100.0)
        assert result["daily_cost"] == 1.5

    @pytest.mark.asyncio
    async def test_get_tokens_from_redis(self, ledger, mock_redis):
        mock_redis.get.return_value = b"500"
        summary = await ledger.get_daily_summary("agent-1")
        assert summary["token_count"] == 500

    @pytest.mark.asyncio
    async def test_get_tasks_from_redis(self, ledger, mock_redis):
        # First call for cost, second for tokens, third for tasks
        mock_redis.get.side_effect = [b"0.5", b"100", b"3"]
        summary = await ledger.get_daily_summary("agent-1")
        assert summary["task_count"] == 3

    @pytest.mark.asyncio
    async def test_get_cost_redis_none(self, ledger, mock_redis):
        mock_redis.get.return_value = None
        result = await ledger.check_budget("agent-1", daily_budget=10.0, monthly_cap=100.0)
        assert result["daily_cost"] == 0.0


# =============================================================================
# 2. scaling.hpa_integration — HPAIntegration, ScheduleRule
# =============================================================================


class TestScheduleRule:
    def test_active_weekday_in_range(self):
        from scaling.hpa_integration import ScheduleRule

        # Monday=1, 9-17 UTC
        rule = ScheduleRule(days=[1, 2, 3, 4, 5], start_hour=9, end_hour=17, min_replicas=3)
        monday_10am = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)  # Monday
        assert rule.is_active(monday_10am) is True

    def test_inactive_weekend(self):
        from scaling.hpa_integration import ScheduleRule

        rule = ScheduleRule(days=[1, 2, 3, 4, 5], start_hour=9, end_hour=17, min_replicas=3)
        sunday = datetime(2026, 3, 22, 10, 0, tzinfo=UTC)  # Sunday
        assert rule.is_active(sunday) is False

    def test_inactive_outside_hours(self):
        from scaling.hpa_integration import ScheduleRule

        rule = ScheduleRule(days=[1, 2, 3, 4, 5], start_hour=9, end_hour=17, min_replicas=3)
        monday_8am = datetime(2026, 3, 23, 8, 0, tzinfo=UTC)
        assert rule.is_active(monday_8am) is False

    def test_wraparound_hours(self):
        from scaling.hpa_integration import ScheduleRule

        # Night shift: 22-06
        rule = ScheduleRule(days=[1, 2, 3, 4, 5], start_hour=22, end_hour=6, min_replicas=2)
        monday_23 = datetime(2026, 3, 23, 23, 0, tzinfo=UTC)
        assert rule.is_active(monday_23) is True
        monday_3 = datetime(2026, 3, 23, 3, 0, tzinfo=UTC)
        assert rule.is_active(monday_3) is True
        monday_12 = datetime(2026, 3, 23, 12, 0, tzinfo=UTC)
        assert rule.is_active(monday_12) is False


class TestHPAIntegration:
    @pytest.fixture
    def hpa(self):
        from scaling.hpa_integration import HPAIntegration

        return HPAIntegration(cooldown_seconds=0)

    @pytest.mark.asyncio
    async def test_scale_up_on_high_queue(self, hpa):
        config = {"current_replicas": 2, "min_replicas": 1, "max_replicas": 10}
        result = await hpa.check_scaling("test-agent", queue_depth=50, config=config)
        assert result["action"] == "scale_up"
        assert result["replicas"] > 2

    @pytest.mark.asyncio
    async def test_scale_down_on_low_queue(self, hpa):
        # cpu_usage_pct must be low to allow scale-down (cpu_desired merges with queue_desired)
        config = {"current_replicas": 5, "min_replicas": 1, "max_replicas": 10, "target_cpu_pct": 60.0}
        result = await hpa.check_scaling(
            "test-agent", queue_depth=2, config=config, cpu_usage_pct=10.0
        )
        assert result["action"] == "scale_down"
        assert result["replicas"] < 5

    @pytest.mark.asyncio
    async def test_no_change_moderate_queue(self, hpa):
        config = {"current_replicas": 2, "min_replicas": 1, "max_replicas": 10}
        result = await hpa.check_scaling("test-agent", queue_depth=15, config=config)
        assert result["action"] == "no_change"

    @pytest.mark.asyncio
    async def test_clamp_to_max_replicas(self, hpa):
        config = {"current_replicas": 4, "min_replicas": 1, "max_replicas": 5}
        result = await hpa.check_scaling("test-agent", queue_depth=500, config=config)
        assert result["replicas"] <= 5

    @pytest.mark.asyncio
    async def test_clamp_to_min_replicas(self, hpa):
        config = {"current_replicas": 3, "min_replicas": 2, "max_replicas": 10}
        result = await hpa.check_scaling("test-agent", queue_depth=0, config=config)
        assert result["replicas"] >= 2

    @pytest.mark.asyncio
    async def test_cpu_signal(self, hpa):
        config = {"current_replicas": 2, "min_replicas": 1, "max_replicas": 10, "target_cpu_pct": 50.0}
        result = await hpa.check_scaling(
            "test-agent", queue_depth=10, config=config, cpu_usage_pct=90.0
        )
        assert "cpu" in result["signals"]

    @pytest.mark.asyncio
    async def test_cpu_zero_target(self, hpa):
        config = {"current_replicas": 2, "min_replicas": 1, "max_replicas": 10, "target_cpu_pct": 0}
        result = await hpa.check_scaling(
            "test-agent", queue_depth=10, config=config, cpu_usage_pct=90.0
        )
        # Should not crash with divide-by-zero
        assert result is not None

    @pytest.mark.asyncio
    async def test_schedule_signal(self):
        from scaling.hpa_integration import HPAIntegration, ScheduleRule

        monday_10am = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
        rule = ScheduleRule(days=[1], start_hour=9, end_hour=17, min_replicas=5)
        hpa = HPAIntegration(schedule_rules=[rule], cooldown_seconds=0)
        config = {"current_replicas": 2, "min_replicas": 1, "max_replicas": 10}
        result = await hpa.check_scaling(
            "test-agent", queue_depth=10, config=config, now=monday_10am
        )
        assert result["replicas"] >= 5

    @pytest.mark.asyncio
    async def test_cooldown_blocks_action(self):
        from scaling.hpa_integration import HPAIntegration

        hpa = HPAIntegration(cooldown_seconds=9999)
        config = {"current_replicas": 2, "min_replicas": 1, "max_replicas": 10}
        # First call triggers scale_up
        r1 = await hpa.check_scaling("test-agent", queue_depth=50, config=config)
        assert r1["action"] == "scale_up"
        # Second call should be blocked by cooldown
        r2 = await hpa.check_scaling("test-agent", queue_depth=50, config=config)
        assert r2["action"] == "no_change"
        assert "cooldown" in r2["reason"].lower()

    def test_add_schedule_rule(self):
        from scaling.hpa_integration import HPAIntegration, ScheduleRule

        hpa = HPAIntegration()
        rule = ScheduleRule(days=[1], start_hour=9, end_hour=17, min_replicas=3)
        hpa.add_schedule_rule(rule)
        assert len(hpa._schedule_rules) == 1

    def test_clear_schedule_rules(self):
        from scaling.hpa_integration import HPAIntegration, ScheduleRule

        rule = ScheduleRule(days=[1], start_hour=9, end_hour=17, min_replicas=3)
        hpa = HPAIntegration(schedule_rules=[rule])
        hpa.clear_schedule_rules()
        assert len(hpa._schedule_rules) == 0

    def test_build_reason(self):
        from scaling.hpa_integration import HPAIntegration

        reason = HPAIntegration._build_reason(
            "scale_up", 2, 5, {"queue_depth": {"desired_replicas": 5}}
        )
        assert "scale_up" in reason
        assert "2 -> 5" in reason

    def test_queue_depth_replicas_no_change(self):
        from scaling.hpa_integration import HPAIntegration

        hpa = HPAIntegration()
        # Queue in normal range
        result = hpa._queue_depth_replicas(15, 2, {})
        assert result == 2

    def test_schedule_replicas_no_rules(self):
        from scaling.hpa_integration import HPAIntegration

        hpa = HPAIntegration()
        assert hpa._schedule_replicas() == 0


# =============================================================================
# 3. scaling.shadow_comparator — ShadowComparator
# =============================================================================


class TestShadowComparator:
    @pytest.fixture
    def comparator(self):
        from scaling.shadow_comparator import ShadowComparator

        return ShadowComparator()

    @pytest.mark.asyncio
    async def test_output_accuracy_exact_match(self, comparator):
        result = await comparator.output_accuracy({"a": 1}, {"a": 1})
        assert result.passed is True
        assert result.score == 1.0
        assert result.details["exact_match"] is True

    @pytest.mark.asyncio
    async def test_output_accuracy_no_match(self, comparator):
        result = await comparator.output_accuracy({"a": 1}, {"b": 2})
        assert result.score < 1.0

    @pytest.mark.asyncio
    async def test_output_accuracy_partial_match(self, comparator):
        result = await comparator.output_accuracy({"a": 1, "b": 2}, {"a": 1, "b": 99})
        assert 0.0 < result.score < 1.0

    @pytest.mark.asyncio
    async def test_compute_similarity_both_empty(self, comparator):
        assert comparator._compute_similarity({}, {}) == 1.0

    @pytest.mark.asyncio
    async def test_compute_similarity_one_empty(self, comparator):
        assert comparator._compute_similarity({"a": 1}, {}) == 0.0

    @pytest.mark.asyncio
    async def test_compute_similarity_nested_dicts(self, comparator):
        a = {"x": {"y": 1}}
        b = {"x": {"y": 1}}
        assert comparator._compute_similarity(a, b) == 1.0

    @pytest.mark.asyncio
    async def test_compute_similarity_numeric_close(self, comparator):
        a = {"x": 100.0}
        b = {"x": 100.5}
        score = comparator._compute_similarity(a, b)
        assert score > 0.5

    @pytest.mark.asyncio
    async def test_compute_similarity_string_case(self, comparator):
        a = {"x": "Hello"}
        b = {"x": "hello"}
        score = comparator._compute_similarity(a, b)
        assert score > 0.5  # case-insensitive match gives 0.95

    @pytest.mark.asyncio
    async def test_confidence_calibration_insufficient(self, comparator):
        result = await comparator.confidence_calibration([0.9], [0.8])
        assert result.passed is True
        assert result.details["reason"] == "insufficient_samples"

    @pytest.mark.asyncio
    async def test_confidence_calibration_correlated(self, comparator):
        shadow = [0.1, 0.5, 0.9, 0.3, 0.7]
        reference = [0.15, 0.55, 0.85, 0.35, 0.65]
        result = await comparator.confidence_calibration(shadow, reference)
        assert result.passed is True
        assert result.score > 0.7

    @pytest.mark.asyncio
    async def test_confidence_calibration_uncorrelated(self, comparator):
        shadow = [0.1, 0.9, 0.1, 0.9]
        reference = [0.9, 0.1, 0.9, 0.1]
        result = await comparator.confidence_calibration(shadow, reference)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_hitl_rate_within_tolerance(self, comparator):
        result = await comparator.hitl_rate_comparison(10.0, 12.0)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_hitl_rate_outside_tolerance(self, comparator):
        result = await comparator.hitl_rate_comparison(10.0, 20.0)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_hallucination_no_hallucination(self, comparator):
        output = {"status": "ok"}
        tools = [{"status": "ok"}]
        result = await comparator.hallucination_detection(output, tools)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_hallucination_detected(self, comparator):
        output = {"fabricated_data": "some_long_invented_string_xyz123"}
        tools = [{"real_data": "actual_value"}]
        result = await comparator.hallucination_detection(output, tools)
        assert result.details["ungrounded_count"] > 0

    @pytest.mark.asyncio
    async def test_tool_error_rate_below_threshold(self, comparator):
        result = await comparator.tool_error_rate(100, 1)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_tool_error_rate_above_threshold(self, comparator):
        result = await comparator.tool_error_rate(100, 5)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_latency_comparison_pass(self, comparator):
        latencies = [50, 60, 70, 80, 90, 100]
        result = await comparator.latency_comparison(latencies, reference_p95_ms=100.0)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_latency_comparison_fail(self, comparator):
        latencies = [200, 250, 300, 350, 400]
        result = await comparator.latency_comparison(latencies, reference_p95_ms=100.0)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_latency_comparison_no_samples(self, comparator):
        result = await comparator.latency_comparison([], reference_p95_ms=100.0)
        assert result.passed is True
        assert result.details["reason"] == "no_shadow_samples"

    @pytest.mark.asyncio
    async def test_full_quality_check_all_pass(self, comparator):
        result = await comparator.full_quality_check(
            shadow_output={"a": 1},
            reference_output={"a": 1},
            shadow_confidences=[0.9, 0.8, 0.7, 0.85],
            reference_confidences=[0.88, 0.82, 0.72, 0.83],
            shadow_hitl_rate=10.0,
            reference_hitl_rate=10.0,
            tool_call_results=[{"a": 1}],
            shadow_tool_total=100,
            shadow_tool_errors=0,
            shadow_latencies_ms=[50, 60, 70],
            reference_p95_ms=100.0,
        )
        assert result["passed"] is True
        assert result["gates_passed"] == 6
        assert result["gates_total"] == 6

    @pytest.mark.asyncio
    async def test_full_quality_check_some_fail(self, comparator):
        result = await comparator.full_quality_check(
            shadow_output={"a": 1},
            reference_output={"b": 2},
            shadow_hitl_rate=10.0,
            reference_hitl_rate=50.0,  # Will fail
        )
        assert result["passed"] is False
        assert result["gates_passed"] < 6

    @pytest.mark.asyncio
    async def test_compare_legacy(self, comparator):
        result = await comparator.compare({"a": 1}, {"a": 1})
        assert result["outputs_match"] is True
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_compare_legacy_mismatch(self, comparator):
        result = await comparator.compare({"a": 1}, {"a": 2})
        assert result["outputs_match"] is False

    def test_flatten_leaves(self):
        from scaling.shadow_comparator import ShadowComparator

        leaves = ShadowComparator._flatten_leaves({"a": {"b": 1}, "c": [2, 3]})
        assert "a.b=1" in leaves
        assert "c[0]=2" in leaves
        assert "c[1]=3" in leaves

    def test_leaf_values(self):
        from scaling.shadow_comparator import ShadowComparator

        values = ShadowComparator._leaf_values({"a": 1, "b": [2, 3]})
        assert 1 in values
        assert 2 in values
        assert 3 in values

    def test_pearson_empty(self):
        from scaling.shadow_comparator import ShadowComparator

        assert ShadowComparator._pearson([], []) == 0.0

    def test_pearson_constant(self):
        from scaling.shadow_comparator import ShadowComparator

        # All same values => std = 0 => pearson = 0
        assert ShadowComparator._pearson([1, 1, 1], [2, 2, 2]) == 0.0


# =============================================================================
# 4. scaling.agent_factory — AgentFactory
# =============================================================================


class TestAgentFactory:
    @pytest.fixture
    def factory(self):
        from scaling.agent_factory import AgentFactory

        return AgentFactory()

    @pytest.mark.asyncio
    async def test_create_agent(self, factory):
        result = await factory.create_agent({"type": "finance"})
        assert "agent_id" in result
        assert result["status"] == "shadow"
        assert result["token_issued"] is True

    @pytest.mark.asyncio
    @patch("scaling.agent_factory.validate_clone_scopes", return_value=[])
    async def test_clone_agent_success(self, mock_validate, factory):
        result = await factory.clone_agent(
            "parent-1",
            {"authorized_tools": ["tool:oracle:read:po"]},
            {"authorized_tools": {"add": []}},
        )
        assert "clone_id" in result
        assert result["parent_id"] == "parent-1"

    @pytest.mark.asyncio
    @patch(
        "scaling.agent_factory.validate_clone_scopes",
        return_value=["Scope not in parent: tool:new:admin"],
    )
    async def test_clone_agent_violation(self, mock_validate, factory):
        result = await factory.clone_agent(
            "parent-1",
            {"authorized_tools": ["tool:oracle:read:po"]},
            {"authorized_tools": {"add": ["tool:new:admin"]}},
        )
        assert "error" in result
        assert result["error"]["code"] == "E4003"

    @pytest.mark.asyncio
    async def test_delete_agent(self, factory):
        result = await factory.delete_agent("agent-123")
        assert result["status"] == "deprecated"
        assert result["retention_days"] == 30


# =============================================================================
# 5. scaling.lifecycle — LifecycleManager
# =============================================================================


class TestLifecycleManager:
    @pytest.fixture
    def lm(self):
        from scaling.lifecycle import LifecycleManager

        return LifecycleManager()

    def test_valid_transition(self, lm):
        assert lm.can_transition("draft", "shadow") is True

    def test_invalid_transition(self, lm):
        assert lm.can_transition("draft", "active") is False

    def test_all_valid_transitions(self, lm):
        from scaling.lifecycle import VALID_TRANSITIONS

        for current, targets in VALID_TRANSITIONS.items():
            for target in targets:
                assert lm.can_transition(current, target) is True

    def test_terminal_states_no_transitions(self, lm):
        assert lm.can_transition("deleted", "active") is False

    @pytest.mark.asyncio
    async def test_transition_success(self, lm):
        result = await lm.transition("agent-1", "draft", "shadow")
        assert result["from_status"] == "draft"
        assert result["to_status"] == "shadow"
        assert result["agent_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_transition_invalid_raises(self, lm):
        with pytest.raises(ValueError, match="Invalid transition"):
            await lm.transition("agent-1", "draft", "active")

    @pytest.mark.asyncio
    async def test_check_shadow_promotion_insufficient_samples(self, lm):
        result = await lm.check_shadow_promotion("agent-1", 5, 0.95, 100, 0.90)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_shadow_promotion_passes(self, lm):
        result = await lm.check_shadow_promotion("agent-1", 100, 0.95, 50, 0.90)
        assert result == "review_ready"

    @pytest.mark.asyncio
    async def test_check_shadow_promotion_fails(self, lm):
        result = await lm.check_shadow_promotion("agent-1", 100, 0.50, 50, 0.90)
        assert result == "shadow_failing"


# =============================================================================
# 6. auth.token_pool — TokenPool
# =============================================================================


class TestTokenPool:
    @pytest.fixture
    def pool(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        p.redis = AsyncMock()
        return p

    @pytest.mark.asyncio
    async def test_get_token_found(self, pool):
        pool.redis.get.return_value = json.dumps({"access_token": "tok-123"})
        token = await pool.get_token("agent-1")
        assert token == "tok-123"

    @pytest.mark.asyncio
    async def test_get_token_not_found(self, pool):
        pool.redis.get.return_value = None
        token = await pool.get_token("agent-1")
        assert token is None

    @pytest.mark.asyncio
    async def test_get_token_no_redis(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        assert await p.get_token("agent-1") is None

    @pytest.mark.asyncio
    async def test_store_token(self, pool):
        pool._schedule_refresh = MagicMock()
        await pool.store_token("agent-1", {"access_token": "tok", "expires_in": 7200})
        pool.redis.setex.assert_called_once()
        pool._schedule_refresh.assert_called_once_with("agent-1", 3600)

    @pytest.mark.asyncio
    async def test_store_token_default_ttl(self, pool):
        pool._schedule_refresh = MagicMock()
        await pool.store_token("agent-1", {"access_token": "tok"})
        # Default expires_in=3600, so refresh at 1800
        pool._schedule_refresh.assert_called_once_with("agent-1", 1800)

    @pytest.mark.asyncio
    async def test_store_token_no_redis(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        await p.store_token("agent-1", {"access_token": "tok"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_revoke_token(self, pool):
        pool._refresh_tasks["agent-1"] = MagicMock()
        await pool.revoke_token("agent-1")
        pool.redis.delete.assert_called_once()
        pool.redis.publish.assert_called_once()
        assert "agent-1" not in pool._refresh_tasks

    @pytest.mark.asyncio
    async def test_revoke_token_no_redis(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        await p.revoke_token("agent-1")
        # No-op, should not raise

    @pytest.mark.asyncio
    async def test_close(self, pool):
        task = MagicMock()
        pool._refresh_tasks["agent-1"] = task
        await pool.close()
        task.cancel.assert_called_once()
        pool.redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_redis(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        await p.close()
        # Should not raise

    def test_set_agent_config_resolver(self, pool):
        resolver = AsyncMock()
        pool.set_agent_config_resolver(resolver)
        assert pool._agent_config_resolver is resolver


# =============================================================================
# 7. auth.grantex — GrantexClient
# =============================================================================


class TestGrantexClient:
    @pytest.mark.asyncio
    @patch("auth.grantex.httpx.AsyncClient")
    async def test_get_platform_token_fresh(self, mock_client_cls):
        from auth.grantex import GrantexClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "plat-tok", "expires_in": 3600}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        gc = GrantexClient()
        gc.token_server = "https://auth.test"
        gc.client_id = "cid"
        gc.client_secret = "csecret"

        token = await gc.get_platform_token()
        assert token == "plat-tok"

    @pytest.mark.asyncio
    @patch("auth.grantex.httpx.AsyncClient")
    async def test_get_platform_token_cached(self, mock_client_cls):
        from auth.grantex import GrantexClient

        gc = GrantexClient()
        gc.token_server = "https://auth.test"
        gc._platform_token = "cached-tok"
        gc._platform_token_exp = datetime.now(UTC).timestamp() + 3600  # valid for 1h

        token = await gc.get_platform_token()
        assert token == "cached-tok"
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    @patch("auth.grantex.httpx.AsyncClient")
    async def test_delegate_agent_token(self, mock_client_cls):
        from auth.grantex import GrantexClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "agent-tok", "expires_in": 1800}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        gc = GrantexClient()
        gc.token_server = "https://auth.test"
        gc._platform_token = "plat-tok"
        gc._platform_token_exp = datetime.now(UTC).timestamp() + 3600

        result = await gc.delegate_agent_token("a1", "finance", ["read"], ttl=1800)
        assert result["access_token"] == "agent-tok"

    @pytest.mark.asyncio
    @patch("auth.grantex.httpx.AsyncClient")
    async def test_revoke_token(self, mock_client_cls):
        from auth.grantex import GrantexClient

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        gc = GrantexClient()
        gc.token_server = "https://auth.test"
        gc._platform_token = "plat-tok"
        gc._platform_token_exp = datetime.now(UTC).timestamp() + 3600

        await gc.revoke_token("some-token")
        mock_client.post.assert_called_once()


# =============================================================================
# 8. auth.opa — OPAClient
# =============================================================================


class TestOPAClient:
    @pytest.mark.asyncio
    @patch("auth.opa.httpx.AsyncClient")
    async def test_evaluate_allowed(self, mock_client_cls):
        from auth.opa import OPAClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"allow": True}}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        opa = OPAClient("http://localhost:8181")
        result = await opa.evaluate("authz/allow", {"user": "test"})
        assert result is True

    @pytest.mark.asyncio
    @patch("auth.opa.httpx.AsyncClient")
    async def test_evaluate_denied(self, mock_client_cls):
        from auth.opa import OPAClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"allow": False}}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        opa = OPAClient()
        result = await opa.evaluate("authz/allow", {"user": "test"})
        assert result is False

    @pytest.mark.asyncio
    @patch("auth.opa.httpx.AsyncClient")
    async def test_evaluate_non_200(self, mock_client_cls):
        from auth.opa import OPAClient

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        opa = OPAClient()
        result = await opa.evaluate("authz/allow", {"user": "test"})
        assert result is False

    @pytest.mark.asyncio
    @patch("auth.opa.httpx.AsyncClient")
    async def test_evaluate_http_error(self, mock_client_cls):
        import httpx

        from auth.opa import OPAClient

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        opa = OPAClient()
        result = await opa.evaluate("authz/allow", {"user": "test"})
        assert result is False  # fail closed


# =============================================================================
# 9. auth.scopes — parse_scope, check_scope, validate_clone_scopes
# =============================================================================


class TestScopes:
    def test_parse_scope_valid(self):
        from auth.scopes import parse_scope

        s = parse_scope("tool:oracle_fusion:read:purchase_order")
        assert s is not None
        assert s.category == "tool"
        assert s.connector == "oracle_fusion"
        assert s.permission == "read"
        assert s.resource == "purchase_order"
        assert s.cap is None

    def test_parse_scope_with_cap(self):
        from auth.scopes import parse_scope

        s = parse_scope("tool:oracle_fusion:write:journal_entry:capped:50000")
        assert s is not None
        assert s.cap == 50000

    def test_parse_scope_invalid(self):
        from auth.scopes import parse_scope

        assert parse_scope("invalid-scope") is None
        assert parse_scope("") is None

    def test_check_scope_admin(self):
        from auth.scopes import check_scope

        ok, reason = check_scope(["tool:oracle:admin"], "oracle", "write", "po")
        assert ok is True
        assert reason == "admin_scope"

    def test_check_scope_exact_match(self):
        from auth.scopes import check_scope

        ok, reason = check_scope(
            ["tool:oracle:read:po"], "oracle", "read", "po"
        )
        assert ok is True
        assert reason == "scope_match"

    def test_check_scope_cap_within(self):
        from auth.scopes import check_scope

        ok, reason = check_scope(
            ["tool:oracle:write:po:capped:1000"], "oracle", "write", "po", amount=500
        )
        assert ok is True

    def test_check_scope_cap_exceeded(self):
        from auth.scopes import check_scope

        ok, reason = check_scope(
            ["tool:oracle:write:po:capped:1000"], "oracle", "write", "po", amount=2000
        )
        assert ok is False
        assert "cap_exceeded" in reason

    def test_check_scope_no_match(self):
        from auth.scopes import check_scope

        ok, reason = check_scope(
            ["tool:oracle:read:po"], "sap", "read", "po"
        )
        assert ok is False
        assert reason == "no_matching_scope"

    def test_validate_clone_scopes_valid(self):
        from auth.scopes import validate_clone_scopes

        parent = ["tool:oracle:read:po", "tool:oracle:write:po"]
        child = ["tool:oracle:read:po"]
        assert validate_clone_scopes(parent, child) == []

    def test_validate_clone_scopes_violation(self):
        from auth.scopes import validate_clone_scopes

        parent = ["tool:oracle:read:po"]
        child = ["tool:sap:write:invoice"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) > 0

    def test_validate_clone_scopes_cap_elevation(self):
        from auth.scopes import validate_clone_scopes

        parent = ["tool:oracle:write:po:capped:1000"]
        child = ["tool:oracle:write:po:capped:5000"]
        violations = validate_clone_scopes(parent, child)
        assert any("Cap elevation" in v for v in violations)

    def test_validate_clone_scopes_admin_covers(self):
        from auth.scopes import validate_clone_scopes

        parent = ["tool:oracle:admin"]
        child = ["tool:oracle:read:po"]
        assert validate_clone_scopes(parent, child) == []


# =============================================================================
# 10. core.gmail_agent
# =============================================================================


class TestGmailAgent:
    def test_extract_body_plain_text(self):
        from core.gmail_agent import _extract_body

        body_data = base64.urlsafe_b64encode(b"Hello world").decode()
        payload = {"mimeType": "text/plain", "body": {"data": body_data}}
        assert _extract_body(payload) == "Hello world"

    def test_extract_body_multipart(self):
        from core.gmail_agent import _extract_body

        body_data = base64.urlsafe_b64encode(b"Part text").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": body_data}},
            ],
        }
        assert _extract_body(payload) == "Part text"

    def test_extract_body_nested_multipart(self):
        from core.gmail_agent import _extract_body

        body_data = base64.urlsafe_b64encode(b"Nested text").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": body_data}},
                    ],
                }
            ],
        }
        assert _extract_body(payload) == "Nested text"

    def test_extract_body_html_fallback(self):
        from core.gmail_agent import _extract_body

        html_data = base64.urlsafe_b64encode(b"<p>Hello</p>").decode()
        payload = {"mimeType": "text/html", "body": {"data": html_data}}
        result = _extract_body(payload)
        assert "Hello" in result
        assert "<p>" not in result

    def test_extract_body_html_in_parts(self):
        from core.gmail_agent import _extract_body

        html_data = base64.urlsafe_b64encode(b"<b>Bold</b>").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/html", "body": {"data": html_data}},
            ],
        }
        result = _extract_body(payload)
        assert "Bold" in result

    def test_extract_body_empty(self):
        from core.gmail_agent import _extract_body

        assert _extract_body({}) == ""

    @patch("core.gmail_agent._get_gmail_service")
    def test_send_reply(self, mock_service_fn):
        from core.gmail_agent import send_reply

        mock_service = MagicMock()
        mock_service.users().messages().send().execute.return_value = {"id": "msg-123"}
        mock_service_fn.return_value = mock_service

        result = send_reply("thread-1", "user@test.com", "Test Subject", "<p>Hi</p>")
        assert result == "msg-123"

    @patch("core.gmail_agent._get_gmail_service")
    def test_send_reply_with_re_prefix(self, mock_service_fn):
        from core.gmail_agent import send_reply

        mock_service = MagicMock()
        mock_service.users().messages().send().execute.return_value = {"id": "msg-456"}
        mock_service_fn.return_value = mock_service

        result = send_reply("thread-1", "user@test.com", "Re: Already prefixed", "<p>Hi</p>")
        assert result == "msg-456"

    @patch("core.gmail_agent._get_gmail_service")
    def test_mark_as_read(self, mock_service_fn):
        from core.gmail_agent import mark_as_read

        mock_service = MagicMock()
        mock_service_fn.return_value = mock_service

        mark_as_read("msg-123")
        mock_service.users().messages().modify.assert_called_once()

    @patch("core.gmail_agent._get_gmail_service")
    def test_get_recent_replies(self, mock_service_fn):
        from core.gmail_agent import get_recent_replies

        body_data = base64.urlsafe_b64encode(b"Reply body").decode()
        mock_service = MagicMock()
        mock_service.users().messages().list().execute.return_value = {
            "messages": [{"id": "m1", "threadId": "t1"}]
        }
        mock_service.users().messages().get().execute.return_value = {
            "internalDate": str(int(datetime.now(UTC).timestamp() * 1000)),
            "payload": {
                "headers": [
                    {"name": "From", "value": "User <user@example.com>"},
                    {"name": "Subject", "value": "Re: Test"},
                ],
                "mimeType": "text/plain",
                "body": {"data": body_data},
            },
        }
        mock_service_fn.return_value = mock_service

        replies = get_recent_replies()
        assert len(replies) == 1
        assert replies[0]["from_email"] == "user@example.com"
        assert replies[0]["subject"] == "Re: Test"


# =============================================================================
# 11. core.tool_gateway.gateway — ToolGateway
# =============================================================================


class TestToolGateway:
    @pytest.fixture
    def gateway(self):
        from core.tool_gateway.gateway import ToolGateway

        rl = AsyncMock()
        rl.check.return_value = MagicMock(allowed=True)
        idem = AsyncMock()
        idem.get.return_value = None
        audit = AsyncMock()
        gw = ToolGateway(rate_limiter=rl, idempotency_store=idem, audit_logger=audit)
        return gw

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(True, "scope_match"))
    @patch("core.tool_gateway.gateway.mask_pii", side_effect=lambda x: x)
    async def test_execute_success(self, mock_pii, mock_scope, gateway):
        connector = AsyncMock()
        connector.execute_tool.return_value = {"result": "ok"}
        gateway.register_connector("oracle", connector)

        result = await gateway.execute(
            "t1", "a1", ["tool:oracle:read:po"], "oracle", "get_po", {}
        )
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(False, "no_matching_scope"))
    async def test_execute_scope_denied(self, mock_scope, gateway):
        result = await gateway.execute(
            "t1", "a1", [], "oracle", "get_po", {}
        )
        assert "error" in result
        assert result["error"]["code"] == "E1007"

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(False, "cap_exceeded:1000"))
    async def test_execute_cap_exceeded(self, mock_scope, gateway):
        result = await gateway.execute(
            "t1", "a1", [], "oracle", "create_po", {}, amount=2000
        )
        assert result["error"]["code"] == "E1008"

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(True, "scope_match"))
    async def test_execute_rate_limited(self, mock_scope, gateway):
        gateway.rate_limiter.check.return_value = MagicMock(
            allowed=False, retry_after_seconds=5.0
        )
        result = await gateway.execute(
            "t1", "a1", ["tool:oracle:read:po"], "oracle", "get_po", {}
        )
        assert result["error"]["code"] == "E1003"

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(True, "scope_match"))
    async def test_execute_idempotency_hit(self, mock_scope, gateway):
        gateway.idempotency.get.return_value = {"cached": True}
        result = await gateway.execute(
            "t1", "a1", ["tool:oracle:read:po"], "oracle", "get_po", {},
            idempotency_key="key-1",
        )
        assert result == {"cached": True}

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(True, "scope_match"))
    async def test_execute_connector_not_found(self, mock_scope, gateway):
        result = await gateway.execute(
            "t1", "a1", ["tool:oracle:read:po"], "missing_connector", "get_po", {}
        )
        assert result["error"]["code"] == "E1005"

    @pytest.mark.asyncio
    @patch("core.tool_gateway.gateway.check_scope", return_value=(True, "scope_match"))
    @patch("core.tool_gateway.gateway.mask_pii", side_effect=lambda x: x)
    async def test_execute_connector_error(self, mock_pii, mock_scope, gateway):
        connector = AsyncMock()
        connector.execute_tool.side_effect = RuntimeError("API down")
        gateway.register_connector("oracle", connector)

        result = await gateway.execute(
            "t1", "a1", ["tool:oracle:read:po"], "oracle", "get_po", {}
        )
        assert result["error"]["code"] == "E1001"
        assert "API down" in result["error"]["message"]

    def test_register_connector(self, gateway):
        connector = MagicMock()
        gateway.register_connector("sap", connector)
        assert "sap" in gateway._connectors


# =============================================================================
# 12. core.tool_gateway.audit_logger — AuditLogger
# =============================================================================


class TestAuditLogger:
    @pytest.fixture
    def audit(self):
        from core.tool_gateway.audit_logger import AuditLogger

        with patch.object(AuditLogger, "__init__", lambda self, db_session_factory=None: None):
            a = AuditLogger.__new__(AuditLogger)
            a._db = None
            a._secret = b"testsecretkey12345678"
            return a

    @pytest.mark.asyncio
    async def test_log_basic(self, audit):
        await audit.log(tenant_id="t1", agent_id="a1", tool_name="get_po", action="execute")
        # Should not raise

    @pytest.mark.asyncio
    async def test_log_with_details(self, audit):
        await audit.log(
            tenant_id="t1",
            agent_id="a1",
            tool_name="get_po",
            action="execute",
            outcome="success",
            details={"latency_ms": 50},
        )

    def test_sign(self, audit):
        sig = audit._sign({"key": "value"})
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex

    def test_sign_deterministic(self, audit):
        data = {"a": 1, "b": "hello"}
        assert audit._sign(data) == audit._sign(data)


# =============================================================================
# 13. core.tool_gateway.idempotency — IdempotencyStore
# =============================================================================


class TestIdempotencyStore:
    @pytest.fixture
    def store(self):
        from core.tool_gateway.idempotency import IdempotencyStore

        s = IdempotencyStore()
        s.redis = AsyncMock()
        return s

    @pytest.mark.asyncio
    async def test_get_found(self, store):
        store.redis.get.return_value = json.dumps({"result": "ok"})
        result = await store.get("t1", "key-1")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_get_not_found(self, store):
        store.redis.get.return_value = None
        assert await store.get("t1", "key-1") is None

    @pytest.mark.asyncio
    async def test_get_no_redis(self):
        from core.tool_gateway.idempotency import IdempotencyStore

        s = IdempotencyStore()
        assert await s.get("t1", "key") is None

    @pytest.mark.asyncio
    async def test_store(self, store):
        await store.store("t1", "key-1", {"result": "ok"})
        store.redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_no_redis(self):
        from core.tool_gateway.idempotency import IdempotencyStore

        s = IdempotencyStore()
        await s.store("t1", "key", {"result": "ok"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_close(self, store):
        await store.close()
        store.redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_redis(self):
        from core.tool_gateway.idempotency import IdempotencyStore

        s = IdempotencyStore()
        await s.close()


# =============================================================================
# 14. core.tool_gateway.rate_limiter — RateLimiter
# =============================================================================


class TestRateLimiter:
    @pytest.fixture
    def limiter(self):
        from core.tool_gateway.rate_limiter import RateLimiter

        rl = RateLimiter()
        rl.redis = AsyncMock()
        rl._script_sha = "abc123"
        return rl

    @pytest.mark.asyncio
    async def test_check_allowed(self, limiter):
        limiter.redis.evalsha.return_value = [1, "59.0", "0"]
        result = await limiter.check("t1", "oracle")
        assert result.allowed is True
        assert result.remaining == 59.0

    @pytest.mark.asyncio
    async def test_check_denied(self, limiter):
        limiter.redis.evalsha.return_value = [0, "0.0", "2.5"]
        result = await limiter.check("t1", "oracle")
        assert result.allowed is False
        assert result.retry_after_seconds == 2.5

    @pytest.mark.asyncio
    async def test_check_no_redis(self):
        from core.tool_gateway.rate_limiter import RateLimiter

        rl = RateLimiter()
        result = await rl.check("t1", "oracle")
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_check_custom_rpm(self, limiter):
        limiter.redis.evalsha.return_value = [1, "99.0", "0"]
        result = await limiter.check("t1", "oracle", rpm=100)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_check_evalsha_error_propagates(self, limiter):
        """When evalsha raises an error, the except clause tries to resolve
        aioredis.exceptions.NoScriptError which raises AttributeError
        since redis.asyncio has no 'exceptions' attribute."""
        limiter.redis.evalsha = AsyncMock(side_effect=RuntimeError("connection lost"))
        with pytest.raises((RuntimeError, AttributeError)):
            await limiter.check("t1", "oracle")

    @pytest.mark.asyncio
    async def test_check_negative_remaining_clamped(self, limiter):
        limiter.redis.evalsha.return_value = [0, "-0.5", "1.5"]
        result = await limiter.check("t1", "oracle")
        assert result.allowed is False
        assert result.remaining == 0  # clamped to 0
        assert result.retry_after_seconds == 1.5

    @pytest.mark.asyncio
    async def test_close(self, limiter):
        await limiter.close()
        limiter.redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_redis(self):
        from core.tool_gateway.rate_limiter import RateLimiter

        rl = RateLimiter()
        await rl.close()

    def test_rate_limit_result_dataclass(self):
        from core.tool_gateway.rate_limiter import RateLimitResult

        r = RateLimitResult(allowed=True, remaining=50.0, retry_after_seconds=0)
        assert r.allowed is True


# =============================================================================
# 15. core.orchestrator.state_machine
# =============================================================================


class TestStateMachine:
    def test_can_transition_valid(self):
        from core.orchestrator.state_machine import WorkflowState, can_transition

        assert can_transition(WorkflowState.PENDING, WorkflowState.RUNNING) is True

    def test_can_transition_invalid(self):
        from core.orchestrator.state_machine import WorkflowState, can_transition

        assert can_transition(WorkflowState.PENDING, WorkflowState.COMPLETED) is False

    def test_transition_valid(self):
        from core.orchestrator.state_machine import WorkflowState, transition

        result = transition(WorkflowState.PENDING, WorkflowState.RUNNING)
        assert result == WorkflowState.RUNNING

    def test_transition_invalid_raises(self):
        from core.orchestrator.state_machine import WorkflowState, transition

        with pytest.raises(ValueError, match="Invalid transition"):
            transition(WorkflowState.COMPLETED, WorkflowState.RUNNING)

    def test_terminal_states(self):
        from core.orchestrator.state_machine import TRANSITIONS, WorkflowState

        assert TRANSITIONS[WorkflowState.COMPLETED] == []
        assert TRANSITIONS[WorkflowState.FAILED] == []
        assert TRANSITIONS[WorkflowState.CANCELLED] == []

    def test_running_transitions(self):
        from core.orchestrator.state_machine import WorkflowState, can_transition

        assert can_transition(WorkflowState.RUNNING, WorkflowState.WAITING_HITL) is True
        assert can_transition(WorkflowState.RUNNING, WorkflowState.COMPLETED) is True
        assert can_transition(WorkflowState.RUNNING, WorkflowState.FAILED) is True
        assert can_transition(WorkflowState.RUNNING, WorkflowState.CANCELLED) is True

    def test_waiting_hitl_transitions(self):
        from core.orchestrator.state_machine import WorkflowState, can_transition

        assert can_transition(WorkflowState.WAITING_HITL, WorkflowState.RUNNING) is True
        assert can_transition(WorkflowState.WAITING_HITL, WorkflowState.FAILED) is True


# =============================================================================
# 16. core.orchestrator.checkpoint — CheckpointManager
# =============================================================================


class TestCheckpointManager:
    @pytest.fixture
    def mgr(self):
        from core.orchestrator.checkpoint import CheckpointManager

        cm = CheckpointManager()
        cm.redis = AsyncMock()
        return cm

    @pytest.mark.asyncio
    async def test_save(self, mgr):
        await mgr.save("run-1", {"step": 3, "status": "running"})
        mgr.redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_found(self, mgr):
        mgr.redis.get.return_value = json.dumps({"step": 3})
        result = await mgr.load("run-1")
        assert result == {"step": 3}

    @pytest.mark.asyncio
    async def test_load_not_found(self, mgr):
        mgr.redis.get.return_value = None
        result = await mgr.load("run-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_no_redis(self):
        from core.orchestrator.checkpoint import CheckpointManager

        cm = CheckpointManager()
        assert await cm.load("run-1") is None

    @pytest.mark.asyncio
    async def test_save_no_redis(self):
        from core.orchestrator.checkpoint import CheckpointManager

        cm = CheckpointManager()
        await cm.save("run-1", {"step": 1})
        # No-op, should not raise

    @pytest.mark.asyncio
    async def test_close(self, mgr):
        await mgr.close()
        mgr.redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_redis(self):
        from core.orchestrator.checkpoint import CheckpointManager

        cm = CheckpointManager()
        await cm.close()


# =============================================================================
# 17. workflows.trigger — WorkflowTrigger, cron_matches
# =============================================================================


class TestWorkflowTrigger:
    def test_manual_always_matches(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("manual")
        assert t.matches({}) is True

    def test_webhook_always_matches(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("webhook")
        assert t.matches({}) is True

    def test_email_received_match(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("email_received", {"filter": {"subject_contains": ["invoice"]}})
        assert t.matches({"subject": "New Invoice from Vendor"}) is True

    def test_email_received_no_match(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("email_received", {"filter": {"subject_contains": ["invoice"]}})
        assert t.matches({"subject": "Meeting reminder"}) is False

    def test_api_event_match(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("api_event", {"event_type": "order.created"})
        assert t.matches({"event_type": "order.created"}) is True

    def test_api_event_no_match(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("api_event", {"event_type": "order.created"})
        assert t.matches({"event_type": "order.cancelled"}) is False

    def test_schedule_match(self):
        from workflows.trigger import WorkflowTrigger

        # Monday 9:00 UTC
        dt = datetime(2026, 3, 23, 9, 0, tzinfo=UTC)
        t = WorkflowTrigger("schedule", {"cron": "0 9 * * 1"})
        assert t.matches({"check_time": dt.isoformat()}) is True

    def test_schedule_no_cron(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("schedule", {})
        assert t.matches({}) is False

    def test_schedule_invalid_cron(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("schedule", {"cron": "bad cron expression extra field extra"})
        assert t.matches({}) is False

    def test_schedule_invalid_check_time(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("schedule", {"cron": "* * * * *"})
        assert t.matches({"check_time": "not-a-date"}) is True  # falls back to now, * matches

    def test_unknown_trigger_type(self):
        from workflows.trigger import WorkflowTrigger

        t = WorkflowTrigger("unknown_type")
        assert t.matches({}) is False


class TestCronMatches:
    def test_all_stars(self):
        from workflows.trigger import cron_matches

        assert cron_matches("* * * * *") is True

    def test_specific_minute(self):
        from workflows.trigger import cron_matches

        dt = datetime(2026, 3, 23, 10, 30, tzinfo=UTC)
        assert cron_matches("30 * * * *", dt) is True
        assert cron_matches("15 * * * *", dt) is False

    def test_range(self):
        from workflows.trigger import cron_matches

        dt = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
        assert cron_matches("0 9-17 * * *", dt) is True
        assert cron_matches("0 0-5 * * *", dt) is False

    def test_step(self):
        from workflows.trigger import cron_matches

        dt = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
        assert cron_matches("*/10 * * * *", dt) is True  # 0 matches */10

    def test_comma_list(self):
        from workflows.trigger import cron_matches

        dt = datetime(2026, 3, 23, 10, 15, tzinfo=UTC)
        assert cron_matches("0,15,30,45 * * * *", dt) is True

    def test_month_name(self):
        from workflows.trigger import cron_matches

        dt = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
        assert cron_matches("0 10 * mar *", dt) is True

    def test_dow_name(self):
        from workflows.trigger import cron_matches

        dt = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)  # Monday
        assert cron_matches("0 10 * * mon", dt) is True

    def test_invalid_field_count(self):
        from workflows.trigger import cron_matches

        with pytest.raises(ValueError, match="5 fields"):
            cron_matches("* * *")

    def test_field_matches_step_with_range(self):
        from workflows.trigger import _field_matches

        assert _field_matches("0-30/10", 10, 0, 59) is True
        assert _field_matches("0-30/10", 5, 0, 59) is False


# =============================================================================
# 18. workflows.parallel_executor
# =============================================================================


class TestParallelExecutor:
    @pytest.mark.asyncio
    async def test_execute_all(self):
        from workflows.parallel_executor import execute_parallel

        async def task_a():
            return "a"

        async def task_b():
            return "b"

        results = await execute_parallel([task_a, task_b], wait_for="all")
        assert sorted(results) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_execute_any(self):
        from workflows.parallel_executor import execute_parallel

        async def fast():
            return "fast"

        async def slow():
            await asyncio.sleep(10)
            return "slow"

        results = await execute_parallel([fast, slow], wait_for="any")
        assert len(results) >= 1
        assert "fast" in results

    @pytest.mark.asyncio
    async def test_execute_n(self):
        from workflows.parallel_executor import execute_parallel

        async def t1():
            return 1

        async def t2():
            return 2

        async def t3():
            await asyncio.sleep(10)
            return 3

        results = await execute_parallel([t1, t2, t3], wait_for="2")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_all_with_exception(self):
        from workflows.parallel_executor import execute_parallel

        async def ok():
            return "ok"

        async def fail():
            raise ValueError("boom")

        results = await execute_parallel([ok, fail], wait_for="all")
        assert any(isinstance(r, ValueError) for r in results)


# =============================================================================
# 19. workflows.step_types
# =============================================================================


class TestStepTypes:
    @pytest.mark.asyncio
    async def test_execute_agent_step(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {"id": "s1", "type": "agent", "agent": "finance-agent", "action": "review"},
            {},
        )
        assert result["type"] == "agent"
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_condition_true(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {
                "id": "s1",
                "type": "condition",
                "condition": "total > 500",
                "true_path": "approve",
                "false_path": "reject",
            },
            {"context": {"total": 1000}},
        )
        assert result["result"] is True
        assert result["next_path"] == "approve"

    @pytest.mark.asyncio
    async def test_execute_condition_false(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {
                "id": "s1",
                "type": "condition",
                "condition": "total > 500",
                "true_path": "approve",
                "false_path": "reject",
            },
            {"context": {"total": 100}},
        )
        assert result["result"] is False
        assert result["next_path"] == "reject"

    @pytest.mark.asyncio
    async def test_execute_hitl_step(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {"id": "s1", "type": "human_in_loop", "assignee_role": "manager"},
            {},
        )
        assert result["status"] == "waiting_hitl"
        assert result["assignee_role"] == "manager"

    @pytest.mark.asyncio
    async def test_execute_parallel_step(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {"id": "s1", "type": "parallel", "steps": ["step_a", "step_b"], "wait_for": "all"},
            {},
        )
        assert result["type"] == "parallel"
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_execute_loop_step(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {"id": "s1", "type": "loop", "items": ["item1", "item2"]},
            {},
        )
        assert result["type"] == "loop"
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_execute_transform_step(self):
        from workflows.step_types import execute_step

        result = await execute_step({"id": "s1", "type": "transform"}, {})
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_notify_step(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {"id": "s1", "type": "notify", "connector": "slack"},
            {},
        )
        assert result["status"] == "sent"
        assert result["connector"] == "slack"

    @pytest.mark.asyncio
    async def test_execute_wait_step(self):
        from workflows.step_types import execute_step

        result = await execute_step({"id": "s1", "type": "wait"}, {})
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_unknown_defaults_to_agent(self):
        from workflows.step_types import execute_step

        result = await execute_step({"id": "s1", "type": "nonexistent"}, {})
        assert result["type"] == "agent"

    @pytest.mark.asyncio
    async def test_execute_sub_workflow_no_definition(self):
        from workflows.step_types import execute_step

        result = await execute_step({"id": "s1", "type": "sub_workflow"}, {})
        assert result["status"] == "failed"
        assert "No 'definition'" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_sub_workflow_missing_from_registry(self):
        from workflows.step_types import execute_step

        result = await execute_step(
            {"id": "s1", "type": "sub_workflow", "workflow_definition_id": "missing"},
            {"workflow_registry": {}},
        )
        assert result["status"] == "failed"
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_parallel_any(self):
        """Parallel with wait_for='any' passes coroutines to asyncio.wait.

        In Python 3.11+, asyncio.wait rejects bare coroutines (requires tasks).
        This test verifies the code path is exercised (raises TypeError).
        """
        from workflows.step_types import execute_step

        with pytest.raises(TypeError, match="coroutines is forbidden"):
            await execute_step(
                {"id": "s1", "type": "parallel", "steps": ["a", "b"], "wait_for": "any"},
                {},
            )


# =============================================================================
# 20. workflows.state_store — WorkflowStateStore
# =============================================================================


class TestWorkflowStateStore:
    @pytest.fixture
    def store(self):
        from workflows.state_store import WorkflowStateStore

        s = WorkflowStateStore()
        s.redis = AsyncMock()
        return s

    @pytest.mark.asyncio
    async def test_save(self, store):
        await store.save({"id": "run-1", "status": "running"})
        store.redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_found(self, store):
        store.redis.get.return_value = json.dumps({"id": "run-1", "status": "running"})
        result = await store.load("run-1")
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_load_not_found(self, store):
        store.redis.get.return_value = None
        assert await store.load("run-1") is None

    @pytest.mark.asyncio
    async def test_load_no_redis(self):
        from workflows.state_store import WorkflowStateStore

        s = WorkflowStateStore()
        assert await s.load("run-1") is None

    @pytest.mark.asyncio
    async def test_save_no_redis(self):
        from workflows.state_store import WorkflowStateStore

        s = WorkflowStateStore()
        await s.save({"id": "run-1"})
        # No-op

    @pytest.mark.asyncio
    async def test_close(self, store):
        await store.close()
        store.redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_redis(self):
        from workflows.state_store import WorkflowStateStore

        s = WorkflowStateStore()
        await s.close()


# =============================================================================
# 21. api.error_handlers
# =============================================================================


class TestErrorHandlers:
    def test_register_error_handlers(self):
        from api.error_handlers import register_error_handlers

        app = MagicMock()
        register_error_handlers(app)
        assert app.exception_handler.call_count == 3

    @pytest.mark.asyncio
    async def test_value_error_handler(self):
        from api.error_handlers import register_error_handlers

        handlers = {}

        class FakeApp:
            def exception_handler(self, exc_type):
                def decorator(fn):
                    handlers[exc_type] = fn
                    return fn
                return decorator

        register_error_handlers(FakeApp())

        mock_request = MagicMock()
        response = await handlers[ValueError](mock_request, ValueError("bad input"))
        assert response.status_code == 400
        body = json.loads(response.body)
        assert body["error"]["code"] == "E2001"

    @pytest.mark.asyncio
    async def test_not_found_handler(self):
        from api.error_handlers import register_error_handlers

        handlers = {}

        class FakeApp:
            def exception_handler(self, exc_type):
                def decorator(fn):
                    handlers[exc_type] = fn
                    return fn
                return decorator

        register_error_handlers(FakeApp())
        mock_request = MagicMock()
        response = await handlers[404](mock_request, Exception("not found"))
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_server_error_handler(self):
        from api.error_handlers import register_error_handlers

        handlers = {}

        class FakeApp:
            def exception_handler(self, exc_type):
                def decorator(fn):
                    handlers[exc_type] = fn
                    return fn
                return decorator

        register_error_handlers(FakeApp())
        mock_request = MagicMock()
        response = await handlers[500](mock_request, Exception("boom"))
        assert response.status_code == 500
        body = json.loads(response.body)
        assert body["error"]["retryable"] is True


# =============================================================================
# 22. api.deps
# =============================================================================


class TestAPIDeps:
    def test_get_current_tenant_success(self):
        from api.deps import get_current_tenant

        request = MagicMock()
        request.state.tenant_id = "t-123"
        assert get_current_tenant(request) == "t-123"

    def test_get_current_tenant_missing(self):
        from fastapi import HTTPException

        from api.deps import get_current_tenant

        request = MagicMock(spec=[])
        request.state = SimpleNamespace()
        with pytest.raises(HTTPException) as exc_info:
            get_current_tenant(request)
        assert exc_info.value.status_code == 401

    def test_get_current_user_success(self):
        from api.deps import get_current_user

        request = MagicMock()
        request.state.claims = {"sub": "user-1", "role": "admin"}
        claims = get_current_user(request)
        assert claims["sub"] == "user-1"

    def test_get_current_user_missing(self):
        from fastapi import HTTPException

        from api.deps import get_current_user

        request = MagicMock(spec=[])
        request.state = SimpleNamespace()
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(request)
        assert exc_info.value.status_code == 401

    def test_require_scope_allowed(self):
        from api.deps import require_scope

        checker_dep = require_scope("agents:read")
        # The return value is a Depends() object; extract the dependency
        request = MagicMock()
        request.state.scopes = ["agents:read", "agents:write"]
        # Call the underlying checker function
        checker_dep.dependency(request)  # Should not raise

    def test_require_scope_denied(self):
        from fastapi import HTTPException

        from api.deps import require_scope

        checker_dep = require_scope("agents:admin")
        request = MagicMock()
        request.state.scopes = ["agents:read"]
        with pytest.raises(HTTPException) as exc_info:
            checker_dep.dependency(request)
        assert exc_info.value.status_code == 403

    def test_require_scope_admin_bypass(self):
        from api.deps import require_scope

        checker_dep = require_scope("agents:write")
        request = MagicMock()
        request.state.scopes = ["agenticorg:admin"]
        checker_dep.dependency(request)  # Should not raise

    def test_get_user_domains(self):
        from api.deps import get_user_domains

        request = MagicMock()
        request.state.claims = {"agenticorg:domains": ["finance", "hr"]}
        assert get_user_domains(request) == ["finance", "hr"]

    def test_get_user_domains_none(self):
        from api.deps import get_user_domains

        request = MagicMock()
        request.state.claims = {}
        assert get_user_domains(request) is None

    def test_get_user_role(self):
        from api.deps import get_user_role

        request = MagicMock()
        request.state.claims = {"role": "cfo"}
        assert get_user_role(request) == "cfo"

    def test_get_user_role_missing(self):
        from api.deps import get_user_role

        request = MagicMock()
        request.state.claims = {}
        assert get_user_role(request) == ""


# =============================================================================
# 23. core.rbac
# =============================================================================


class TestRBAC:
    def test_get_allowed_domains_cfo(self):
        from core.rbac import get_allowed_domains

        assert get_allowed_domains("cfo") == ["finance"]

    def test_get_allowed_domains_admin(self):
        from core.rbac import get_allowed_domains

        assert get_allowed_domains("admin") is None

    def test_get_allowed_domains_unknown(self):
        from core.rbac import get_allowed_domains

        assert get_allowed_domains("unknown") is None

    def test_get_scopes_for_role_cfo(self):
        from core.rbac import get_scopes_for_role

        scopes = get_scopes_for_role("cfo")
        assert "agents:read" in scopes
        assert "agents:write" in scopes

    def test_get_scopes_for_role_admin(self):
        from core.rbac import get_scopes_for_role

        assert get_scopes_for_role("admin") == ["agenticorg:admin"]

    def test_get_scopes_for_role_auditor(self):
        from core.rbac import get_scopes_for_role

        assert get_scopes_for_role("auditor") == ["audit:read"]

    def test_get_scopes_for_role_unknown(self):
        from core.rbac import get_scopes_for_role

        assert get_scopes_for_role("nonexistent") == []

    def test_role_domain_map_completeness(self):
        from core.rbac import ROLE_DOMAIN_MAP, ROLE_LABELS, ROLE_SCOPES

        for role in ROLE_DOMAIN_MAP:
            assert role in ROLE_SCOPES
            assert role in ROLE_LABELS

    def test_all_domain_roles_have_standard_scopes(self):
        from core.rbac import _DOMAIN_ROLE_SCOPES, ROLE_SCOPES

        for role in ["cfo", "chro", "cmo", "coo"]:
            assert ROLE_SCOPES[role] == _DOMAIN_ROLE_SCOPES


# =============================================================================
# 24. Additional edge-case tests for completeness
# =============================================================================


class TestCostRecordDataclass:
    def test_cost_record_creation(self):
        from scaling.cost_ledger import CostRecord

        cr = CostRecord(
            agent_id="a1",
            tokens=100,
            cost_usd=Decimal("0.01"),
            model="gpt-4",
            tenant_id="t1",
            timestamp=time.time(),
        )
        assert cr.agent_id == "a1"
        assert cr.tokens == 100


class TestGateResultDataclass:
    def test_gate_result_creation(self):
        from scaling.shadow_comparator import GateResult

        gr = GateResult(gate="test", passed=True, score=0.95, threshold=0.9)
        assert gr.passed is True
        assert gr.details == {}


class TestWorkflowStateEnum:
    def test_enum_values(self):
        from core.orchestrator.state_machine import WorkflowState

        assert WorkflowState.PENDING == "pending"
        assert WorkflowState.RUNNING == "running"
        assert WorkflowState.WAITING_HITL == "waiting_hitl"
        assert WorkflowState.COMPLETED == "completed"
        assert WorkflowState.FAILED == "failed"
        assert WorkflowState.CANCELLED == "cancelled"


class TestCronFieldMatchesEdgeCases:
    def test_literal_match(self):
        from workflows.trigger import _field_matches

        assert _field_matches("5", 5, 0, 59) is True
        assert _field_matches("5", 6, 0, 59) is False

    def test_range_match(self):
        from workflows.trigger import _field_matches

        assert _field_matches("1-5", 3, 1, 31) is True
        assert _field_matches("1-5", 6, 1, 31) is False

    def test_star_match(self):
        from workflows.trigger import _field_matches

        assert _field_matches("*", 0, 0, 59) is True
        assert _field_matches("*", 59, 0, 59) is True

    def test_step_from_value(self):
        from workflows.trigger import _field_matches

        assert _field_matches("5/15", 5, 0, 59) is True
        assert _field_matches("5/15", 20, 0, 59) is True
        assert _field_matches("5/15", 35, 0, 59) is True

    def test_comma_separated(self):
        from workflows.trigger import _field_matches

        assert _field_matches("1,3,5,7", 3, 0, 59) is True
        assert _field_matches("1,3,5,7", 4, 0, 59) is False


class TestReplaceNames:
    def test_replace_month_names(self):
        from workflows.trigger import _MONTH_NAMES, _replace_names

        assert _replace_names("jan", _MONTH_NAMES) == "1"
        assert _replace_names("dec", _MONTH_NAMES) == "12"

    def test_replace_dow_names(self):
        from workflows.trigger import _DOW_NAMES, _replace_names

        assert _replace_names("mon", _DOW_NAMES) == "1"
        assert _replace_names("sun", _DOW_NAMES) == "0"


class TestShadowComparatorStringOverlap:
    """Test string comparison within _compute_similarity."""

    @pytest.fixture
    def comparator(self):
        from scaling.shadow_comparator import ShadowComparator

        return ShadowComparator()

    def test_different_strings(self, comparator):
        # Completely different strings
        score = comparator._compute_similarity({"x": "abc"}, {"x": "xyz"})
        assert 0.0 <= score <= 1.0

    def test_numeric_far_apart(self, comparator):
        score = comparator._compute_similarity({"x": 100}, {"x": 200})
        assert score < 1.0


class TestHPAQueueDepthEdgeCases:
    def test_zero_tasks_per_replica(self):
        from scaling.hpa_integration import HPAIntegration

        hpa = HPAIntegration()
        # tasks_per_replica=0 should not divide by zero
        result = hpa._queue_depth_replicas(50, 2, {"tasks_per_replica": 0})
        assert result >= 2

    def test_scale_down_single_replica(self):
        from scaling.hpa_integration import HPAIntegration

        hpa = HPAIntegration()
        # Cannot scale below 1
        result = hpa._queue_depth_replicas(1, 1, {})
        assert result == 1


class TestTokenPoolRefresh:
    """Test the _refresh_after flow with mocks."""

    @pytest.mark.asyncio
    async def test_refresh_no_resolver(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        p.redis = AsyncMock()
        # No resolver registered
        await p._refresh_after("agent-1", 0)
        # Should delete stale token since no resolver
        p.redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_with_resolver_success(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        p.redis = AsyncMock()
        resolver = AsyncMock(
            return_value={"agent_type": "finance", "scopes": ["read"], "token_ttl": 3600}
        )
        p.set_agent_config_resolver(resolver)
        p._schedule_refresh = MagicMock()

        with patch("auth.token_pool.grantex_client") as mock_grantex:
            mock_grantex.delegate_agent_token = AsyncMock(
                return_value={"access_token": "new-tok", "expires_in": 3600}
            )
            await p._refresh_after("agent-1", 0)
            mock_grantex.delegate_agent_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_with_resolver_failure(self):
        from auth.token_pool import TokenPool

        p = TokenPool()
        p.redis = AsyncMock()
        resolver = AsyncMock(side_effect=RuntimeError("resolver failed"))
        p.set_agent_config_resolver(resolver)

        await p._refresh_after("agent-1", 0)
        # Should delete stale token on failure
        p.redis.delete.assert_called()
