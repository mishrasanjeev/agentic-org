# SPDX-License-Identifier: Apache-2.0
"""Per-agent execution limits and loop detection: a run over its limit or repeating a tool call pattern is stopped."""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError

from api.v1 import agents as agents_api
from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from core.config import settings
from core.langgraph import limits
from core.test_doubles.scripted_model import final, tool_call

ROOT = Path(__file__).resolve().parents[2]


def _ai(*calls):
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)])


class TestParse:
    def test_limits_are_whole_numbers_within_the_platform_bounds(self):
        assert limits.parse_limits(
            {"max_steps": 20, "max_duration_seconds": 60, "max_tool_calls": 10, "max_repeats": 2, "loop_window": 3}
        ) == {
            "max_steps": 20,
            "max_duration_seconds": 60,
            "max_tool_calls": 10,
            "max_repeats": 2,
            "loop_window": 3,
        }
        assert limits.parse_limits({"max_steps": 5, "max_repeats": None}) == {"max_steps": 5}

    @pytest.mark.parametrize(
        "raw",
        [
            "20",
            {},
            {"max_steps": 0},
            {"max_steps": limits.PLATFORM_MAX_STEPS + 1},
            {"max_duration_seconds": limits.PLATFORM_MAX_DURATION_SECONDS + 1},
            {"max_tool_calls": "many"},
            {"max_repeats": 1},
            {"loop_window": 11},
            {"max_steps": True},
            {"colour": "blue"},
        ],
    )
    def test_bad_limits_are_refused(self, raw):
        with pytest.raises(limits.LimitError):
            limits.parse_limits(raw)

    def test_the_effective_limits_are_the_agents_bounded_by_the_platforms_only_while_on(self, monkeypatch):
        assert settings.runtime_limits_enabled is False and limits.enabled() is False
        own = {"max_steps": 5, "max_duration_seconds": 10, "max_tool_calls": 3, "max_repeats": 2, "loop_window": 2}
        assert limits.effective(own) == limits.platform()
        monkeypatch.setattr(settings, "runtime_limits_enabled", True)
        assert limits.effective(own) == limits.Limits(5, 10, 3, 2, 2)
        assert limits.effective({"max_steps": 10**9}).max_steps == limits.PLATFORM_MAX_STEPS
        assert limits.effective(None) == limits.platform() and limits.effective({}) == limits.platform()
        assert limits.declared({"limits": own}) == own and limits.declared({"limits": {}}) is None
        assert limits.declared(type("A", (), {"config": {"limits": {"max_steps": 2}}})()) == {"max_steps": 2}


class TestLoops:
    def test_signatures_name_the_tool_and_hash_the_arguments(self):
        a = limits.signature("search", {"q": "x", "k": 3})
        b = limits.signature("search", {"k": 3, "q": "x"})
        assert a == b and a.startswith("search:") and len(a) == len("search:") + 16
        assert limits.signature("search", {"q": "y"}) != a and "x" not in a
        messages = [
            HumanMessage(content="go"),
            _ai(("search", {"q": "x", "k": 3})),
            ToolMessage(content="r", tool_call_id="c0"),
            _ai(("send", {"to": "a"})),
        ]
        assert limits.tool_signatures(messages) == [a, limits.signature("send", {"to": "a"})]
        assert limits.model_steps(messages) == 2

    def test_identical_calls_in_a_row_and_repeated_patterns_are_loops(self):
        s = limits.signature
        same = [s("search", {"q": "x"})] * 3
        assert "repeated 3 times in a row" in (limits.detect_loop(same, max_repeats=3) or "")
        assert limits.detect_loop(same[:2], max_repeats=3) is None
        pattern = [s("a", {}), s("b", {}), s("a", {}), s("b", {})]
        assert limits.detect_loop(pattern, max_repeats=3, window=4) == "a pattern of 2 tool calls (a, b) was repeated"
        assert limits.detect_loop(pattern, max_repeats=3, window=1) is None
        progress = [s("a", {"i": i}) for i in range(10)]
        assert limits.detect_loop(progress, max_repeats=3, window=4) is None
        assert limits.detect_loop([], max_repeats=3) is None

    def test_check_stops_on_steps_tool_calls_or_a_loop_and_the_update_says_why(self):
        tight = limits.Limits(max_steps=2, max_duration_seconds=60, max_tool_calls=3, max_repeats=3, loop_window=4)
        one = [_ai(("search", {"q": "x"}))]
        assert limits.check(one, tight) is None
        two = one + [ToolMessage(content="r", tool_call_id="c0"), _ai(("search", {"q": "y"}))]
        stop = limits.check(two, tight)
        assert stop is not None and stop.reason == "step_limit" and stop.steps == 2 and stop.tool_calls == 2
        roomy = limits.Limits(max_steps=50, max_duration_seconds=60, max_tool_calls=1, max_repeats=3, loop_window=4)
        assert limits.check(two, roomy).reason == "tool_call_limit"
        looping = [_ai(("search", {"q": "x"})), _ai(("search", {"q": "x"})), _ai(("search", {"q": "x"}))]
        loop = limits.check(looping, limits.Limits(50, 60, 50, 3, 4))
        assert loop is not None and loop.reason == "loop_detected" and "search" in loop.detail
        update = limits.stop_update(loop, ["step 1"])
        assert update["status"] == "failed" and update["error"].startswith("stopped: ")
        assert update["limit_stop"]["reason"] == "loop_detected" and update["reasoning_trace"][-1].startswith(
            "STOPPED (loop_detected)"
        )

    def test_a_run_may_make_exactly_its_limit_of_tool_calls(self):
        roomy: dict[str, Any] = {"max_steps": 50, "max_duration_seconds": 60, "max_repeats": 5, "loop_window": 2}
        one = [_ai(("search", {"q": "x"}))]
        assert limits.check(one, limits.Limits(max_tool_calls=1, **roomy)) is None
        two = one + [ToolMessage(content="r", tool_call_id="c0"), _ai(("search", {"q": "y"}))]
        assert limits.check(two, limits.Limits(max_tool_calls=2, **roomy)) is None
        stop = limits.check(two, limits.Limits(max_tool_calls=1, **roomy))
        assert stop is not None and stop.reason == "tool_call_limit" and stop.tool_calls == 2
        assert "more than its limit of 1 tool calls" in stop.detail
        ten = [_ai(*[("search", {"q": i}) for i in range(10)])]
        assert limits.check(ten, limits.Limits(max_tool_calls=10, **roomy)) is None
        assert limits.check(ten, limits.Limits(max_tool_calls=9, **roomy)).reason == "tool_call_limit"

    def test_the_step_check_before_a_model_call_counts_the_answers_so_far(self):
        tight = limits.Limits(max_steps=2, max_duration_seconds=60, max_tool_calls=50)
        assert limits.check_steps([HumanMessage(content="go")], tight) is None
        one = [HumanMessage(content="go"), _ai(("search", {"q": "x"}))]
        assert limits.check_steps(one, tight) is None
        two = [*one, ToolMessage(content="r", tool_call_id="c0"), AIMessage(content="{}"), HumanMessage(content="fix")]
        stop = limits.check_steps(two, tight)
        assert stop is not None and stop.reason == "step_limit" and stop.steps == 2 and stop.tool_calls == 1

    def test_metering_counts_by_reason(self):
        from observability import metrics as prom

        before = prom.agent_runs_stopped_total.labels(reason="loop_detected")._value.get()
        limits.meter("loop_detected")
        limits.meter("weird")
        assert prom.agent_runs_stopped_total.labels(reason="loop_detected")._value.get() == before + 1
        assert prom.agent_runs_stopped_total.labels(reason="other")._value.get() >= 1


class TestRoutes:
    def test_a_refused_limit_is_a_422(self):
        refused = agents_api._limits_refused(limits.LimitError("max_steps is between 1 and 200"))
        assert refused.status_code == 422 and refused.detail["error"] == "runtime_limits"

    def test_the_graph_and_the_runner_apply_the_limits(self):
        graph = (ROOT / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")
        assert "limits: dict[str, Any] | None = None," in graph
        assert "run_limits = execution_limits.effective(limits)" in graph
        reason = graph[graph.index("async def reason(state: AgentState)") :]
        reason = reason[: reason.index("context_guard(messages)")]
        assert "execution_limits.check_steps(messages, run_limits)" in reason
        validate = graph[graph.index("async def validate_scopes(state: AgentState)") :]
        validate = validate[: validate.index('graph.add_node("validate_scopes"')]
        assert (
            'execution_limits.check(state.get("messages")' in validate and "execution_limits.stop_update(" in validate
        )
        evaluate = graph[
            graph.index("async def evaluate(state: AgentState)") : graph.index("graph = StateGraph(AgentState)")
        ]
        assert 'state.get("limit_stop")' in evaluate
        state = (ROOT / "core" / "langgraph" / "state.py").read_text(encoding="utf-8")
        assert "limit_stop:" in state
        runner = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        assert "timeout=run_limits.max_duration_seconds" in runner and "except _limit_stop_errors():" in runner
        assert "return (GraphRecursionError,) if execution_limits.enabled() else ()" in runner
        # A reused thread does not inherit an earlier turn's stop.
        assert '"limit_stop": {},' in runner
        assert '"limit": result.get("limit_stop")' in runner or '"limit": ' in runner
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert 'limits=(agent_config.get("config") or {}).get(execution_limits.LIMITS_KEY)' in api
        assert '@router.put("/agents/{agent_id}/limits")' in api and '@router.get("/agents/{agent_id}/limits")' in api
        assert '**({"limit": lg_result["limit"]} if lg_result.get("limit") else {})' in api

    @pytest.mark.asyncio
    async def test_the_limits_routes_store_and_show_the_agents_limits(self, monkeypatch):
        from types import SimpleNamespace

        agent = SimpleNamespace(
            id=uuid.uuid4(), tenant_id=uuid.uuid4(), config={}, owner_user_id=None, visibility="tenant"
        )

        class _Result:
            def scalar_one_or_none(self):
                return agent

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, _statement):
                return _Result()

        monkeypatch.setattr(agents_api, "get_tenant_session", lambda *_args, **_kw: _Session())
        monkeypatch.setattr(agents_api, "require_agent_mutable", lambda *_args, **_kw: None)
        monkeypatch.setattr(agents_api, "require_agent_visible", lambda *_args, **_kw: None)
        body = agents_api.AgentLimitsIn(limits={"max_steps": 12, "max_repeats": 2})
        stored = await agents_api.set_agent_limits(
            agent.id, body, tenant_id=str(agent.tenant_id), user_domains=None, caller=None
        )
        assert stored["limits"] == {"max_steps": 12, "max_repeats": 2} and agent.config["limits"] == {
            "max_steps": 12,
            "max_repeats": 2,
        }
        assert stored["effective"]["max_steps"] == limits.PLATFORM_MAX_STEPS and stored["enforced"] is False
        shown = await agents_api.get_agent_limits(
            agent.id, tenant_id=str(agent.tenant_id), user_domains=None, caller=None
        )
        assert (
            shown["limits"] == {"max_steps": 12, "max_repeats": 2}
            and shown["platform"]["max_steps"] == limits.PLATFORM_MAX_STEPS
        )
        with pytest.raises(HTTPException) as refused:
            await agents_api.set_agent_limits(
                agent.id,
                agents_api.AgentLimitsIn(limits={"max_steps": 0}),
                tenant_id=str(agent.tenant_id),
                user_domains=None,
                caller=None,
            )
        assert refused.value.status_code == 422
        cleared = await agents_api.set_agent_limits(
            agent.id,
            agents_api.AgentLimitsIn(limits=None),
            tenant_id=str(agent.tenant_id),
            user_domains=None,
            caller=None,
        )
        assert cleared["limits"] is None and "limits" not in agent.config


GOOD = {"status": "approved", "amount": 10.0, "confidence": 0.95}
BAD = {"status": "maybe", "confidence": 0.95}
OPEN_SCHEMA = {
    "type": "object",
    "required": ["status", "amount"],
    "properties": {"status": {"type": "string", "enum": ["approved"]}, "amount": {"type": "number"}},
}


def _state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="decide")],
        "agent_id": "agent-scripted",
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": "",
        "grant_token": "",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setattr(settings, "runtime_limits_enabled", True)


class TestGraph:
    """The graph holds a run to its limits on every edge into the model and every round of tools."""

    @staticmethod
    def _graph(**declared: Any):
        from core.langgraph.agent_graph import build_agent_graph

        return build_agent_graph(
            system_prompt="scripted",
            authorized_tools=[],
            confidence_floor=0.5,
            run_grant=NO_RUN_GRANT_FOR_TESTS,
            **declared,
        )

    async def test_an_output_schema_correction_does_not_take_the_run_over_its_step_limit(
        self, monkeypatch, limits_on, scripted_model
    ):
        monkeypatch.setattr(settings, "output_schema_enforced", True)
        model = scripted_model([final(BAD)])
        compiled = self._graph(output_schema_json=OPEN_SCHEMA, limits={"max_steps": 1}).compile(
            checkpointer=MemorySaver()
        )
        result = await compiled.ainvoke(_state(), {"configurable": {"thread_id": "limits-repair"}})
        assert len(model.calls) == 1
        assert result["status"] == "failed" and result["error"].startswith("stopped: ")
        assert result["limit_stop"]["reason"] == "step_limit" and "__interrupt__" not in result
        assert result["reasoning_trace"][-2].startswith("STOPPED (step_limit)")

    async def test_off_a_correction_runs_as_before(self, monkeypatch, scripted_model):
        monkeypatch.setattr(settings, "output_schema_enforced", True)
        model = scripted_model([final(BAD), final(GOOD)])
        result = await self._graph(output_schema_json=OPEN_SCHEMA, limits={"max_steps": 1}).compile().ainvoke(_state())
        assert result["status"] == "completed" and len(model.calls) == 2 and not result.get("limit_stop")

    def _tool_graph(self, max_tool_calls: int):
        from core.langgraph.agent_graph import build_agent_graph

        return build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.5,
            run_grant=NO_RUN_GRANT_FOR_TESTS,
            limits={"max_tool_calls": max_tool_calls},
        )

    async def test_a_run_makes_its_limit_of_tool_calls_and_is_stopped_past_it(self, limits_on, scripted_model):
        executed = AsyncMock(return_value={"id": "msg-1", "status": "sent"})
        scripted_model([tool_call("gmail__send_email", to="ap@example.com"), final(GOOD)])
        with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
            result = await self._tool_graph(1).compile().ainvoke(_state())
        assert result["status"] == "completed" and executed.await_count == 1 and not result.get("limit_stop")

        executed.reset_mock()
        model = scripted_model(
            [tool_call("gmail__send_email", to="ap@example.com"), tool_call("gmail__send_email", to="ar@example.com")]
        )
        with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
            compiled = self._tool_graph(1).compile(checkpointer=MemorySaver())
            stopped = await compiled.ainvoke(_state(), {"configurable": {"thread_id": "limits-tools"}})
        # The first call ran, the second was refused, and the stopped run is not sent to review.
        assert executed.await_count == 1 and len(model.calls) == 2
        assert stopped["status"] == "failed" and stopped["limit_stop"]["reason"] == "tool_call_limit"
        assert "__interrupt__" not in stopped and not stopped.get("hitl_trigger")


class _Raising:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def compile(self, **_kwargs: Any) -> _Raising:
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self.exc


class TestRunner:
    """With the switch off, a run that reaches the platform ceiling fails exactly as before."""

    @pytest.fixture
    def offline(self, monkeypatch):
        from core.langgraph import runner

        def no_database(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("no database in unit tests")

        monkeypatch.setattr("core.billing.metering.gate_agent_run", AsyncMock(return_value=None))
        monkeypatch.setattr("core.billing.metering.meter_agent_run", AsyncMock(return_value=None))
        monkeypatch.setattr(runner, "prefetch_llm_credential", AsyncMock(return_value=None))
        monkeypatch.setattr(runner, "generate_explanation", AsyncMock(return_value={}))
        monkeypatch.setattr(runner, "get_checkpointer", AsyncMock(return_value=None))
        monkeypatch.setattr("core.database.get_tenant_session", no_database)
        return runner

    async def _run(self, runner: Any, monkeypatch: Any, exc: BaseException) -> dict[str, Any]:
        monkeypatch.setattr(runner, "build_agent_graph", lambda **_kwargs: _Raising(exc))
        return await runner.run_agent(
            agent_id="agent-limits",
            agent_type="analyst",
            domain="ops",
            tenant_id="",
            system_prompt="scripted",
            authorized_tools=[],
            task_input={"action": "go", "inputs": {}, "context": {}},
            run_grant=NO_RUN_GRANT_FOR_TESTS,
            limits={"max_steps": 3},
        )

    @staticmethod
    def _stopped(reason: str) -> float:
        from observability import metrics as prom

        return prom.agent_runs_stopped_total.labels(reason=reason)._value.get()

    async def test_off_the_step_ceiling_is_the_generic_failure(self, offline, monkeypatch):
        before = self._stopped("step_limit")
        result = await self._run(offline, monkeypatch, GraphRecursionError("Recursion limit of 200 reached"))
        assert result["status"] == "failed" and result["error"] == "Recursion limit of 200 reached"
        assert "limit" not in result and result["reasoning_trace"] == ["Agent execution failed: GraphRecursionError"]
        assert self._stopped("step_limit") == before

    async def test_on_the_step_ceiling_is_a_limit_stop(self, offline, monkeypatch, limits_on):
        before = self._stopped("step_limit")
        result = await self._run(offline, monkeypatch, GraphRecursionError("Recursion limit of 200 reached"))
        assert result["status"] == "failed" and result["error"].startswith("stopped: ")
        assert result["limit"]["reason"] == "step_limit" and self._stopped("step_limit") == before + 1

    async def test_a_timeout_carries_the_limit_block_only_while_on(self, offline, monkeypatch):
        before = self._stopped("duration_limit")
        off = await self._run(offline, monkeypatch, TimeoutError())
        assert off["error"].startswith("timeout: agent exceeded ") and "limit" not in off
        assert self._stopped("duration_limit") == before
        monkeypatch.setattr(settings, "runtime_limits_enabled", True)
        on = await self._run(offline, monkeypatch, TimeoutError())
        assert on["limit"]["reason"] == "duration_limit" and self._stopped("duration_limit") == before + 1


class _CapturedError(Exception):
    pass


class TestEntryPoints:
    """Every path that runs a stored agent carries its limits, and the run response returns the limit block."""

    def test_chat_passes_the_agents_limits_to_the_runner(self):
        from api.v1 import chat

        class _Row(SimpleNamespace):
            def __getattr__(self, name):  # attributes the route reads but this test does not set
                return None

        agent = _Row(
            id=uuid.uuid4(),
            domain="finance",
            name="Analyst",
            agent_type="analyst",
            status="active",
            authorized_tools=[],
            connector_ids=[],
            config={"limits": {"max_steps": 4, "max_tool_calls": 2}},
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = agent

        @contextlib.asynccontextmanager
        async def _session(*_args, **_kwargs):
            yield SimpleNamespace(execute=AsyncMock(return_value=result))

        seen: dict[str, Any] = {}

        async def fake_run(**kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            raise _CapturedError

        from core.governance.operator_override import OverrideDecision

        body = chat.ChatQueryRequest(query="what is the balance", agent_id=str(agent.id))
        with (
            patch("api.v1.agents._require_company_for_tenant", AsyncMock(return_value=uuid.uuid4())),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
            patch("api.v1.agents._resolve_connector_configs", AsyncMock(return_value=({}, []))),
            patch.object(chat, "caller_from_request", return_value=SimpleNamespace(user_id=None, is_admin=True)),
            patch.object(chat, "get_tenant_session", _session),
            patch.object(chat, "require_agent_visible", lambda *_: None),
            patch.object(chat, "_pinned_llm_provider", return_value=None),
            patch.object(chat, "agent_ownership_fields", return_value={"visibility": "tenant"}),
            patch("api.v1._tds_routing.try_tds_deterministic_route", AsyncMock(return_value=None)),
            patch.object(chat, "resolve_run_grant", AsyncMock(return_value=None)),
            patch.object(chat, "check_operator_override", AsyncMock(return_value=OverrideDecision(blocked=False))),
            patch("core.langgraph.runner.run_agent", fake_run),
        ):
            with pytest.raises(_CapturedError):
                import asyncio

                asyncio.run(chat.chat_query(body, MagicMock(), tenant_id=str(uuid.uuid4()), user_domains=None))
        assert seen["limits"] == {"max_steps": 4, "max_tool_calls": 2}

    def test_the_run_response_returns_the_limit_block(self):
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        tail = api[api.index("# 7. Return result") :]
        tail = tail[: tail.index("return response")]
        assert 'if lg_result.get("limit"):' in tail and 'response["limit"] = lg_result["limit"]' in tail
        contract = (ROOT / "docs" / "api" / "agent-run-contract.md").read_text(encoding="utf-8")
        assert "| `limit` | no |" in contract
