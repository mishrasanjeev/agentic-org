# SPDX-License-Identifier: Apache-2.0
"""Operator override: decisions, and the enforcement points that honour them.

Everything here runs without a database or Redis: the active override list is
patched at ``core.governance.operator_override.active_overrides`` and the
control is switched on through ``settings.operator_override_enabled``.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.governance import operator_override as oo
from core.governance.operator_override import (
    ERROR_CODE,
    OperatorOverrideBlocked,
    Override,
    OverrideDecision,
    blocked_run_result,
    check,
    normalise_provider,
)

TENANT = uuid.uuid4()
AGENT = str(uuid.uuid4())
ROOT = Path(__file__).resolve().parents[3]


def _override(kind: str, target: str = "", mode: str = "halt", limit: int | None = None) -> Override:
    return Override(
        id=str(uuid.uuid4()), target_kind=kind, target_id=target, mode=mode, limit_per_minute=limit, reason="drill"
    )


def _rows(enabled: bool):
    """A flag store answer: the global row enables the control, or no rows at all."""
    from core.feature_flags import FlagRows

    row = {"enabled": True, "rollout_percentage": 100} if enabled else None
    return FlagRows(global_row=row, tenant_row=None)


@pytest.fixture
def control_on(monkeypatch):
    monkeypatch.setattr(oo.settings, "operator_override_enabled", True)


def _with(overrides: list[Override]):
    return patch.object(oo, "active_overrides", AsyncMock(return_value=overrides))


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class TestDecision:
    def test_off_by_default_reads_nothing(self, monkeypatch):
        monkeypatch.setattr(oo.settings, "operator_override_enabled", False)
        loader = AsyncMock(return_value=[_override("all_agents")])
        with patch.object(oo, "active_overrides", loader):
            with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(False))):
                decision = asyncio.run(check(TENANT, agent_id=AGENT))
        assert decision.blocked is False
        loader.assert_not_called()

    def test_authority_flag_turns_the_control_on(self, monkeypatch):
        monkeypatch.setattr(oo.settings, "operator_override_enabled", False)
        with (
            patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(True))),
            _with([_override("all_agents")]),
        ):
            decision = asyncio.run(check(TENANT, agent_id=AGENT))
        assert decision.blocked is True

    def test_no_tenant_is_allowed(self, control_on):
        with _with([_override("all_agents")]) as loader:
            assert asyncio.run(check(None, agent_id=AGENT)).blocked is False
            assert asyncio.run(check("not-a-uuid", agent_id=AGENT)).blocked is False
        loader.assert_not_called()

    @pytest.mark.parametrize(
        ("kind", "target", "kwargs", "blocked"),
        [
            ("provider", "gemini", {"provider": "gemini", "model": "gemini-2.5-flash"}, True),
            ("provider", "anthropic", {"provider": "claude", "model": "claude-sonnet"}, True),
            ("provider", "openai", {"provider": "gpt", "model": "gpt-4o"}, True),
            ("provider", "openai", {"provider": "gemini", "model": "gemini-2.5-flash"}, False),
            ("model", "gemini-2.5-flash", {"provider": "gemini", "model": "Gemini-2.5-Flash"}, True),
            ("model", "gemini-2.5-pro", {"provider": "gemini", "model": "gemini-2.5-flash"}, False),
            ("agent", AGENT, {"agent_id": AGENT}, True),
            ("agent", str(uuid.uuid4()), {"agent_id": AGENT}, False),
            ("all_agents", "", {"agent_id": AGENT}, True),
            ("all_agents", "", {"provider": "gemini"}, False),
            ("workflow", "wf-1", {"workflow_id": "wf-1"}, True),
            ("workflow", "wf-1", {"workflow_id": "wf-2"}, False),
            ("connector", "mock", {"connector": "mock", "tool": "ownership"}, True),
            ("connector", "hubspot", {"connector": "mock", "tool": "ownership"}, False),
            ("tool", "ownership", {"connector": "mock", "tool": "ownership"}, True),
            ("tool", "mock:ownership", {"connector": "mock", "tool": "ownership"}, True),
            ("tool", "hubspot:ownership", {"connector": "mock", "tool": "ownership"}, False),
            ("tool_pipeline", "", {"connector": "mock", "tool": "ownership"}, True),
            ("tool_pipeline", "", {"agent_id": AGENT}, False),
        ],
    )
    def test_matching(self, control_on, kind, target, kwargs, blocked):
        with _with([_override(kind, target)]):
            decision = asyncio.run(check(TENANT, **kwargs))
        assert decision.blocked is blocked
        if blocked:
            assert decision.override is not None and decision.override.target_kind == kind
            assert "halted" in decision.reason

    def test_halt_beats_throttle(self, control_on):
        throttle = _override("all_agents", mode="throttle", limit=1000)
        halt = _override("agent", AGENT)
        with _with([throttle, halt]), patch.object(oo, "_throttled", AsyncMock(return_value=False)):
            decision = asyncio.run(check(TENANT, agent_id=AGENT, throttle_unit="agent"))
        assert decision.blocked and decision.override is halt

    def test_throttle_blocks_above_the_window_limit(self, control_on):
        throttle = _override("connector", "mock", mode="throttle", limit=2)
        calls = iter([False, False, True])
        with (
            _with([throttle]),
            patch("core.auth_state.check_window_rate", AsyncMock(side_effect=lambda *a, **k: next(calls))),
        ):
            first = asyncio.run(check(TENANT, connector="mock", tool="ownership", throttle_unit="tool"))
            second = asyncio.run(check(TENANT, connector="mock", tool="ownership", throttle_unit="tool"))
            third = asyncio.run(check(TENANT, connector="mock", tool="ownership", throttle_unit="tool"))
        assert not first.blocked and not second.blocked and third.blocked
        assert "throttled to 2 calls per minute" in third.reason

    def test_throttle_counter_failure_fails_closed(self, control_on):
        throttle = _override("connector", "mock", mode="throttle", limit=2)
        with (
            _with([throttle]),
            patch("core.auth_state.check_window_rate", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            assert asyncio.run(check(TENANT, connector="mock", tool="ownership", throttle_unit="tool")).blocked is True

    def test_zero_limit_throttle_blocks_everything(self, control_on):
        with _with([_override("provider", "gemini", mode="throttle", limit=0)]):
            assert (
                asyncio.run(check(TENANT, provider="gemini", model="gemini-2.5-flash", throttle_unit="model")).blocked
                is True
            )

    def test_read_failure_fails_closed_in_strict_runtime(self, control_on, monkeypatch):
        monkeypatch.setattr(oo.settings, "env", "production")
        with patch.object(oo, "active_overrides", AsyncMock(side_effect=RuntimeError("db down"))):
            decision = asyncio.run(check(TENANT, agent_id=AGENT))
        assert decision.blocked is True and "could not be read" in decision.reason

    def test_read_failure_allows_in_relaxed_runtime(self, control_on, monkeypatch):
        monkeypatch.setattr(oo.settings, "env", "test")
        with patch.object(oo, "active_overrides", AsyncMock(side_effect=RuntimeError("db down"))):
            assert asyncio.run(check(TENANT, agent_id=AGENT)).blocked is False

    def test_blocks_are_metered(self, control_on):
        from observability.metrics import operator_override_blocks_total

        before = operator_override_blocks_total.labels(target_kind="agent", mode="halt")._value.get()
        with _with([_override("agent", AGENT)]):
            asyncio.run(check(TENANT, agent_id=AGENT))
        assert operator_override_blocks_total.labels(target_kind="agent", mode="halt")._value.get() == before + 1

    def test_error_payload_and_run_result_shapes(self):
        o = _override("agent", AGENT)
        decision = OverrideDecision(blocked=True, reason="halted", override=o)
        assert decision.to_error()["error"] == {"code": ERROR_CODE, "message": "halted"}
        assert decision.to_error()["override"]["id"] == o.id
        result = blocked_run_result(decision)
        assert result["status"] == "operator_override" and result["error"] == "halted"
        assert set(result) >= {
            "output",
            "confidence",
            "reasoning_trace",
            "tool_calls_log",
            "tool_calls",
            "hitl_trigger",
            "performance",
        }

    def test_provider_aliases(self):
        assert normalise_provider("claude") == "anthropic"
        assert normalise_provider("GPT") == "openai"
        assert normalise_provider("azure_openai") == "openai"
        assert normalise_provider("gemini") == "gemini"

    def test_a_throttle_counts_only_at_its_dispatch_unit(self, control_on):
        agent_throttle = _override("all_agents", mode="throttle", limit=0)
        tool_throttle = _override("tool_pipeline", mode="throttle", limit=0)
        with _with([agent_throttle, tool_throttle]):
            # The HTTP pre-check passes no unit: halts only.
            assert asyncio.run(check(TENANT, agent_id=AGENT)).blocked is False
            # The agent run is the agent throttle's unit; the model boundary is not.
            assert asyncio.run(check(TENANT, agent_id=AGENT, throttle_unit="agent")).blocked is True
            assert asyncio.run(check(TENANT, agent_id=AGENT, throttle_unit="model")).blocked is False
            tool = asyncio.run(check(TENANT, agent_id=AGENT, connector="mock", tool="t", throttle_unit="tool"))
            assert tool.blocked is True and tool.override is tool_throttle

    def test_authority_flag_lookup_failure_fails_closed_in_strict_runtime(self, monkeypatch):
        from core.feature_flags import FeatureFlagLookupError

        monkeypatch.setattr(oo.settings, "operator_override_enabled", False)
        loader = AsyncMock(return_value=[_override("all_agents")])
        with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(side_effect=FeatureFlagLookupError("down"))):
            with patch.object(oo, "active_overrides", loader):
                monkeypatch.setattr(oo.settings, "env", "test")
                assert asyncio.run(check(TENANT, agent_id=AGENT)).blocked is False
                monkeypatch.setattr(oo.settings, "env", "production")
                decision = asyncio.run(check(TENANT, agent_id=AGENT))
        assert decision.blocked is True and "could not be read" in decision.reason
        loader.assert_not_called()


class TestCache:
    def test_cache_hit_skips_the_database(self):
        cached = [_override("all_agents").to_dict()]
        import json

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(cached))
        with (
            patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)),
            patch.object(oo, "_load_from_db", AsyncMock()) as db,
        ):
            overrides = asyncio.run(oo.active_overrides(TENANT))
        assert [o.to_dict() for o in overrides] == cached
        db.assert_not_called()

    def test_cache_miss_reads_the_database_and_fills_the_cache(self):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        rows = [_override("agent", AGENT)]
        with (
            patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)),
            patch.object(oo, "_load_from_db", AsyncMock(return_value=rows)),
        ):
            overrides = asyncio.run(oo.active_overrides(TENANT))
        assert overrides == rows
        redis.set.assert_awaited_once()
        assert redis.set.await_args.kwargs["ex"] == oo.CACHE_TTL_SECONDS

    def test_without_redis_the_database_is_read(self):
        rows = [_override("agent", AGENT)]
        with (
            patch("core.async_redis.get_async_redis", AsyncMock(return_value=None)),
            patch.object(oo, "_load_from_db", AsyncMock(return_value=rows)),
        ):
            assert asyncio.run(oo.active_overrides(TENANT)) == rows


# ---------------------------------------------------------------------------
# Enforcement points
# ---------------------------------------------------------------------------


class TestHaltedWorkflowRetry:
    """A halted workflow's retry lives in the task queue, not in the API process."""

    def test_the_retry_is_queued_with_the_configured_countdown(self, monkeypatch):
        from core.config import settings
        from workflows.run_sync import schedule_halted_workflow_retry

        monkeypatch.setattr(settings, "operator_halt_retry_seconds", 7)
        with patch("core.tasks.workflow_tasks.resume_halted_workflow.apply_async") as apply:
            assert schedule_halted_workflow_retry("run-1") is True
        apply.assert_called_once_with(args=["run-1"], countdown=7)
        broken = patch("core.tasks.workflow_tasks.resume_halted_workflow.apply_async", side_effect=RuntimeError("down"))
        with broken:
            assert schedule_halted_workflow_retry("run-1") is False

    def test_the_task_retries_while_halted_and_stops_when_released_or_cancelled(self, monkeypatch):
        from core.config import settings
        from core.tasks import workflow_tasks as wt

        monkeypatch.setattr(settings, "operator_halt_retry_seconds", 7)
        store = SimpleNamespace(init=AsyncMock(), close=AsyncMock(), load=AsyncMock(return_value={"status": "running"}))
        monkeypatch.setattr(wt, "_state_store", lambda: store)
        from celery.exceptions import Retry

        with (
            patch.object(wt, "_drive_engine_and_sync", AsyncMock(return_value={"halted": True, "status": "running"})),
            patch.object(wt.resume_halted_workflow, "retry", side_effect=Retry("requeued")) as retry,
            pytest.raises(Retry),
        ):
            wt.resume_halted_workflow.run("run-1")
        # The explicit requeue, not the autoretry wrapper, decides the countdown.
        assert retry.call_args_list[0] == call(countdown=7, max_retries=None)
        with patch.object(wt, "_drive_engine_and_sync", AsyncMock(return_value={"status": "completed"})):
            assert wt.resume_halted_workflow.run("run-1") == {"status": "resumed", "run_status": "completed"}
        store.load = AsyncMock(return_value={"status": "cancelled"})
        assert wt.resume_halted_workflow.run("run-1")["status"] == "noop"
        store.load = AsyncMock(return_value=None)
        assert wt.resume_halted_workflow.run("run-1")["status"] == "error"
        assert store.close.await_count == 4

    def test_the_background_executor_retries_in_process_while_the_queue_is_unavailable(self, monkeypatch):
        from api.v1 import workflows as wf

        monkeypatch.setattr(wf.settings, "operator_halt_retry_seconds", 9)
        tenant_id, run_id = uuid.uuid4(), uuid.uuid4()
        db_run = SimpleNamespace(context={}, status="running")
        result = MagicMock()
        result.scalar_one.return_value = db_run
        result.scalar_one_or_none.return_value = db_run
        session = SimpleNamespace(execute=AsyncMock(return_value=result))

        @contextlib.asynccontextmanager
        async def _session(*_args, **_kwargs):
            yield session

        engine = SimpleNamespace(
            start_run=AsyncMock(return_value="eng-1"),
            execute_next=AsyncMock(return_value={"status": "running", "halted": True, "error": "halted"}),
        )
        store = SimpleNamespace(init=AsyncMock(), close=AsyncMock(), load=AsyncMock(return_value=None))
        sleep = AsyncMock()
        with (
            patch("workflows.state_store.WorkflowStateStore", return_value=store),
            patch("workflows.engine.WorkflowEngine", return_value=engine),
            patch.object(wf, "get_tenant_session", _session),
            patch("workflows.run_sync.schedule_halted_workflow_retry", side_effect=[False, False, True]) as schedule,
            patch("workflows.run_sync.record_ab_outcome_if_terminal", AsyncMock()),
            patch.object(wf.asyncio, "sleep", sleep),
        ):
            asyncio.run(wf._execute_workflow_bg(tenant_id, run_id, {"steps": []}, None, workflow_id="wf-1"))
        # Two passes retried here while the queue refused; the third pass handed the retry to the queue.
        assert schedule.call_count == 3 and engine.execute_next.await_count == 3
        assert sleep.await_args_list == [call(9), call(9)]
        assert db_run.status == "running"
        store.close.assert_awaited_once()

    def test_the_background_executor_queues_the_retry_and_returns(self):
        from api.v1 import workflows as wf

        tenant_id, run_id = uuid.uuid4(), uuid.uuid4()
        db_run = SimpleNamespace(context={}, status="running")
        result = MagicMock()
        result.scalar_one.return_value = db_run
        result.scalar_one_or_none.return_value = db_run
        session = SimpleNamespace(execute=AsyncMock(return_value=result))

        @contextlib.asynccontextmanager
        async def _session(*_args, **_kwargs):
            yield session

        engine = SimpleNamespace(
            start_run=AsyncMock(return_value="eng-1"),
            execute_next=AsyncMock(return_value={"status": "running", "halted": True, "error": "halted"}),
        )
        store = SimpleNamespace(init=AsyncMock(), close=AsyncMock(), load=AsyncMock(return_value=None))
        with (
            patch("workflows.state_store.WorkflowStateStore", return_value=store),
            patch("workflows.engine.WorkflowEngine", return_value=engine),
            patch.object(wf, "get_tenant_session", _session),
            patch("workflows.run_sync.schedule_halted_workflow_retry", return_value=True) as schedule,
            patch("workflows.run_sync.record_ab_outcome_if_terminal", AsyncMock()),
        ):
            asyncio.run(wf._execute_workflow_bg(tenant_id, run_id, {"steps": []}, None, workflow_id="wf-1"))
        schedule.assert_called_once_with("eng-1")
        assert engine.execute_next.await_count == 1 and db_run.status == "running"
        store.close.assert_awaited_once()


class TestChatOverride:
    """Chat consults the override before either route answers; the agent throttle counts for the deterministic route."""

    @staticmethod
    def _query(det, blocked):
        from fastapi import HTTPException

        from api.v1 import chat

        class _Row(SimpleNamespace):
            def __getattr__(self, name):  # attributes the route reads but this test does not set
                return None

        agent = _Row(
            id=uuid.uuid4(), domain="finance", name="TDS", agent_type="tds_compliance", status="active",
            authorized_tools=[], connector_ids=[],
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = agent

        @contextlib.asynccontextmanager
        async def _session(*_args, **_kwargs):
            yield SimpleNamespace(execute=AsyncMock(return_value=result))

        check = AsyncMock(return_value=_blocked("agent halted") if blocked else OverrideDecision(blocked=False))
        body = chat.ChatQueryRequest(query="calculate TDS on 100000 under 194C", agent_id=str(agent.id))
        with (
            patch("api.v1.agents._require_company_for_tenant", AsyncMock(return_value=uuid.uuid4())),
            patch.object(chat, "caller_from_request", return_value=SimpleNamespace(user_id=None, is_admin=True)),
            patch.object(chat, "get_tenant_session", _session),
            patch.object(chat, "require_agent_visible", lambda *_: None),
            patch.object(chat, "_pinned_llm_provider", return_value=None),
            patch.object(chat, "agent_ownership_fields", return_value={"visibility": "tenant"}),
            patch("api.v1._tds_routing.try_tds_deterministic_route", AsyncMock(return_value=det)),
            patch.object(chat, "resolve_run_grant", AsyncMock(return_value=None)),
            patch.object(chat, "direct_tool_call_permitted", AsyncMock(return_value=True)),
            patch.object(chat, "check_operator_override", check),
        ):
            try:
                asyncio.run(chat.chat_query(body, MagicMock(), tenant_id=str(TENANT), user_domains=None))
            except HTTPException as exc:
                return exc, check, str(agent.id)
        raise AssertionError("expected the route to be refused")

    def test_a_halt_refuses_the_deterministic_route_and_counts_the_agent_throttle(self):
        exc, check, agent_id = self._query({"answer": "x", "confidence": 0.9, "tool_calls": []}, blocked=True)
        assert exc.status_code == 423 and exc.detail["error"] == "operator_override"
        check.assert_awaited_once_with(str(TENANT), agent_id=agent_id, throttle_unit="agent")

    def test_a_halt_refuses_the_model_route_without_counting_a_throttle(self):
        exc, check, agent_id = self._query(None, blocked=True)
        assert exc.status_code == 423
        check.assert_awaited_once_with(str(TENANT), agent_id=agent_id, throttle_unit=None)


def _blocked(reason: str = "Operator override: agent halted (drill).") -> OverrideDecision:
    return OverrideDecision(blocked=True, reason=reason, override=_override("agent", AGENT))


class TestModelRouter:
    def test_call_model_refuses_before_any_provider_call(self):
        from core.llm.router import LLMRouter, _is_transient_llm_failure

        router = LLMRouter()
        with patch(
            "core.governance.operator_override.check", AsyncMock(return_value=_blocked("provider halted"))
        ) as chk:
            with patch.object(router, "_call_provider", AsyncMock()) as provider:
                with pytest.raises(OperatorOverrideBlocked):
                    asyncio.run(
                        router._call_model(
                            "gemini-2.5-flash", [{"role": "user", "content": "hi"}], 0.1, 10, tenant_id=str(TENANT)
                        )
                    )
        chk.assert_awaited_once()
        assert chk.await_args.kwargs == {"provider": "gemini", "model": "gemini-2.5-flash", "throttle_unit": "model"}
        provider.assert_not_called()
        # A block never triggers the fallback model.
        assert _is_transient_llm_failure(OperatorOverrideBlocked(_blocked())) is False

    def test_check_runs_after_model_validation(self):
        src = (ROOT / "core" / "llm" / "router.py").read_text(encoding="utf-8")
        body = src[src.index("async def _call_model(") :]
        assert body.index('raise ValueError(f"Unsupported model') < body.index("check_operator_override(")


class TestAgentRunner:
    def test_run_agent_returns_the_blocked_result_before_building_the_graph(self):
        src = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        body = src[src.index("async def run_agent(") : src.index("async def resume_agent(")]
        assert body.index("gate_agent_run(") < body.index("check_operator_override(") < body.index("build_agent_graph(")
        resume = src[src.index("async def resume_agent(") :]
        assert resume.index("check_operator_override(") < resume.index("get_checkpointer()")

    def test_run_agent_blocked(self):
        from core.langgraph import runner

        with patch("core.billing.metering.gate_agent_run", AsyncMock(return_value=None)):
            with patch("core.governance.operator_override.check", AsyncMock(return_value=_blocked())):
                with patch.object(runner, "build_agent_graph") as graph:
                    result = asyncio.run(
                        runner.run_agent(
                            agent_id=AGENT,
                            agent_type="finance",
                            domain="finance",
                            tenant_id=str(TENANT),
                            system_prompt="x",
                            authorized_tools=[],
                            task_input={},
                        )
                    )
        assert result["status"] == "operator_override"
        assert result["override"]["target_kind"] == "agent"
        graph.assert_not_called()


class TestBaseAgent:
    def test_execute_returns_a_failed_result_with_the_override_code(self):
        from core.agents.base import BaseAgent
        from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

        agent = BaseAgent(agent_id=AGENT, tenant_id=str(TENANT), authorized_tools=[])
        task = TaskAssignment(
            message_id="msg-1",
            correlation_id="corr-1",
            workflow_run_id="run-1",
            workflow_definition_id="wf-1",
            step_id="s1",
            step_index=0,
            total_steps=1,
            target_agent=TargetAgent(agent_id=AGENT, agent_type="finance", agent_token="test-token"),
            task=TaskInput(action="probe", inputs={}),
        )
        with patch("core.governance.operator_override.check", AsyncMock(return_value=_blocked())):
            with patch.object(agent, "_reason", AsyncMock(side_effect=AssertionError("must not run"))):
                result = asyncio.run(agent.execute(task))
        assert result.status == "failed"
        assert result.error["code"] == ERROR_CODE


class TestToolDispatch:
    def test_connector_dispatch_refuses_and_audits(self):
        from core.langgraph import tool_adapter

        with patch("core.governance.operator_override.check", AsyncMock(return_value=_blocked("tool halted"))) as chk:
            with patch.object(tool_adapter, "_audit_operator_override", AsyncMock()) as audit:
                with patch.object(tool_adapter.ConnectorRegistry, "get") as registry:
                    result = asyncio.run(
                        tool_adapter._execute_connector_tool(
                            "mock", "ownership", {}, None, tenant_id=str(TENANT), agent_id=AGENT
                        )
                    )
        assert result["error"] == "operator_override" and result["message"] == "tool halted"
        assert chk.await_args.kwargs == {
            "agent_id": AGENT,
            "connector": "mock",
            "tool": "ownership",
            "throttle_unit": "tool",
        }
        audit.assert_awaited_once()
        registry.assert_not_called()

    def test_tool_gateway_refuses_with_the_error_code(self):
        from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
        from core.tool_gateway.gateway import ToolGateway

        gateway = ToolGateway(audit_logger=AsyncMock())
        with patch("core.governance.operator_override.check", AsyncMock(return_value=_blocked("pipeline halted"))):
            result = asyncio.run(
                gateway.execute(str(TENANT), AGENT, [], "mock", "ownership", {}, run_grant=NO_RUN_GRANT_FOR_TESTS)
            )
        assert result["error"]["code"] == ERROR_CODE and result["error"]["message"] == "pipeline halted"
        gateway.audit.log.assert_awaited_once()
        assert gateway.audit.log.await_args.kwargs["action"] == "operator_override"


class TestWorkflowEngine:
    def test_halted_run_keeps_its_status_and_executes_no_step(self):
        from workflows.engine import WorkflowEngine

        engine = WorkflowEngine.__new__(WorkflowEngine)
        state = {
            "id": "wfr_1",
            "status": "running",
            "tenant_id": str(TENANT),
            "workflow_id": "wf-1",
            "step_results": {"a": {}},
        }
        with patch(
            "core.governance.operator_override.check", AsyncMock(return_value=_blocked("workflow halted"))
        ) as chk:
            halted = asyncio.run(engine._operator_halt(state, "b"))
        assert halted == {
            "status": "running",
            "halted": True,
            "override": halted["override"],
            "error": "workflow halted",
            "step_results": {"a": {}},
        }
        assert chk.await_args.kwargs == {"workflow_id": "wf-1", "throttle_unit": "workflow"}
        with patch("core.governance.operator_override.check", AsyncMock(return_value=oo.ALLOWED)):
            assert asyncio.run(engine._operator_halt(state, "b")) is None

    def test_both_loops_check_before_executing_a_step(self):
        src = (ROOT / "workflows" / "engine.py").read_text(encoding="utf-8")
        assert src.count("halted = await self._operator_halt(state, step_id)") == 2
        for marker in ("async def _execute_unguarded(", "async def _execute_next_unguarded("):
            body = src[src.index(marker) :]
            assert body.index("self._operator_halt(") < body.index("self._execute_with_retry(")

    def test_background_loop_queues_the_retry_while_halted(self):
        src = (ROOT / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
        assert 'step_result.get("halted")' in src and "schedule_halted_workflow_retry(engine_run_id)" in src
        assert "OPERATOR_HALT_POLL_SECONDS" not in src and "workflow_halt_retry_in_process" in src


class TestMigration:
    def test_revision_chain_and_rls(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z32_operator_overrides.py"
        spec = importlib.util.spec_from_file_location("v6_z32_operator_overrides", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z32_operator_overrides" and len(module.revision) <= 32
        assert module.down_revision == "v6z31_a2a_buyers"
        src = path.read_text(encoding="utf-8")
        assert "ALTER TABLE operator_overrides ENABLE ROW LEVEL SECURITY" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK" in src

    def test_flag_is_operator_managed(self):
        from core.feature_flags import is_reserved_flag_key

        assert is_reserved_flag_key(oo.FLAG_KEY)
