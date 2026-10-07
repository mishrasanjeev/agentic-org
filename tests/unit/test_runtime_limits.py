# SPDX-License-Identifier: Apache-2.0
"""Per-agent execution limits and loop detection: a run over its limit or repeating a tool call pattern is stopped."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.v1 import agents as agents_api
from core.config import settings
from core.langgraph import limits

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
        roomy = limits.Limits(max_steps=50, max_duration_seconds=60, max_tool_calls=2, max_repeats=3, loop_window=4)
        assert limits.check(two, roomy).reason == "tool_call_limit"
        looping = [_ai(("search", {"q": "x"})), _ai(("search", {"q": "x"})), _ai(("search", {"q": "x"}))]
        loop = limits.check(looping, limits.Limits(50, 60, 50, 3, 4))
        assert loop is not None and loop.reason == "loop_detected" and "search" in loop.detail
        update = limits.stop_update(loop, ["step 1"])
        assert update["status"] == "failed" and update["error"].startswith("stopped: ")
        assert update["limit_stop"]["reason"] == "loop_detected" and update["reasoning_trace"][-1].startswith(
            "STOPPED (loop_detected)"
        )

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
        assert "timeout=run_limits.max_duration_seconds" in runner and "except GraphRecursionError" in runner
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
