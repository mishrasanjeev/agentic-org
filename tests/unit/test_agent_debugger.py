# SPDX-License-Identifier: Apache-2.0
"""The debugging console: breakpoints, step-through of a thread's checkpoints, inspection, and the runner's pause."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from core.config import settings
from core.langgraph import debugger
from core.langgraph.thread_ids import new_thread_id

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
OTHER = uuid.uuid4()


# ── Breakpoints ────────────────────────────────────────────────────────────────


class TestBreakpoints:
    def test_breakpoints_are_known_nodes_each_once_in_graph_order(self):
        assert debugger.parse_breakpoints(["evaluate", "reason", "reason"]) == ["reason", "evaluate"]
        assert debugger.parse_breakpoints([]) == []

    @pytest.mark.parametrize("raw", ["reason", ["lunch"], [1], {"a": 1}])
    def test_bad_breakpoints_are_refused(self, raw):
        with pytest.raises(debugger.DebugError) as info:
            debugger.parse_breakpoints(raw)
        assert info.value.status == 422

    def test_declared_reads_the_agent_config_and_ignores_what_is_not_a_node(self):
        agent = SimpleNamespace(config={"debug": {"break_before": ["execute_tools", "nope", "reason"]}})
        assert debugger.declared(agent) == ["reason", "execute_tools"]
        assert debugger.declared(SimpleNamespace(config=None)) == []
        assert debugger.declared({"config": {"debug": {"break_before": "reason"}}}) == []

    def test_the_runner_only_gets_breakpoints_while_the_console_is_on(self, monkeypatch):
        config = {"debug": {"break_before": ["execute_tools"]}}
        monkeypatch.setattr(settings, "runtime_debug_console_enabled", False)
        assert debugger.breakpoints_for_run(config) is None
        monkeypatch.setattr(settings, "runtime_debug_console_enabled", True)
        assert debugger.breakpoints_for_run(config) == ["execute_tools"]
        assert debugger.breakpoints_for_run({}) is None


# ── State views ────────────────────────────────────────────────────────────────


class TestViews:
    def test_next_nodes_come_from_the_branch_channels_in_graph_order(self):
        values = {"branch:to:evaluate": None, "messages": [], "branch:to:reason": None}
        assert debugger.next_nodes(values) == ["reason", "evaluate"]
        assert debugger.next_nodes({}) == []

    def test_the_view_hides_the_grant_token_redacts_secret_keys_and_bounds_values(self):
        values = {
            "grant_token": "eyJ-secret",
            "status": "running",
            "output": {"api_key": "sk-1", "summary": "x" * 5_000, "nested": {"Authorization": "b", "ok": 1}},
            "reasoning_trace": [f"step {i}" for i in range(80)],
            "branch:to:reason": None,
            "messages": [
                SystemMessage(content="be brief"),
                HumanMessage(content="hello"),
                AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "c1"}]),
                ToolMessage(content="42", tool_call_id="c1", name="lookup"),
            ],
        }
        view = debugger.view(values)
        assert "grant_token" not in view and "branch:to:reason" not in view
        assert view["output"]["api_key"] == "[redacted]" and view["output"]["nested"]["Authorization"] == "[redacted]"
        assert view["output"]["summary"].startswith("x" * 10) and "3000 more characters" in view["output"]["summary"]
        assert len(view["reasoning_trace"]) == 51 and view["reasoning_trace"][-1] == "… [30 more items]"
        assert [m["type"] for m in view["messages"]] == ["system", "human", "ai", "tool"]
        assert view["messages"][2]["tool_calls"] == ["lookup"] and view["messages"][3]["name"] == "lookup"

    def test_the_input_step_keeps_its_state_under_start(self):
        assert debugger.state_values({"__start__": {"status": "running", "grant_token": "t"}}) == {
            "status": "running",
            "grant_token": "t",
        }
        assert debugger.view({"__start__": {"status": "running", "grant_token": "t"}}) == {"status": "running"}

    def test_changed_names_the_keys_whose_view_differs(self):
        before = {"status": "running", "output": {}, "confidence": 0.0}
        after = {"status": "completed", "output": {"a": 1}, "confidence": 0.0, "error": ""}
        assert debugger.changed(before, after) == ["status", "output", "error"]
        assert debugger.changed(None, {"a": 1}) == ["a"]


# ── Step-through over a real checkpointer ──────────────────────────────────────


class _State(TypedDict):
    messages: list
    status: str
    output: dict
    grant_token: str


def _graph():
    def reason(state):
        return {"messages": state["messages"] + [AIMessage(content="thinking")], "status": "running"}

    def evaluate(state):
        return {"status": "completed", "output": {"summary": "done", "secret_token": "s"}}

    graph = StateGraph(_State)
    graph.add_node("reason", reason)
    graph.add_node("evaluate", evaluate)
    graph.add_edge(START, "reason")
    graph.add_edge("reason", "evaluate")
    graph.add_edge("evaluate", END)
    return graph


async def _run_thread(saver, thread_id, *, interrupt_before=None):
    compiled = _graph().compile(checkpointer=saver, interrupt_before=interrupt_before)
    config = {"configurable": {"thread_id": thread_id}}
    await compiled.ainvoke(
        {"messages": [HumanMessage(content="hi")], "status": "running", "output": {}, "grant_token": "jwt"},
        config=config,
    )
    return compiled, config


class TestStepThrough:
    @pytest.mark.asyncio
    async def test_steps_list_the_checkpoints_oldest_first_with_node_changes_and_next(self, monkeypatch):
        saver = MemorySaver()
        thread = new_thread_id(TENANT)
        await _run_thread(saver, thread)
        monkeypatch.setattr(debugger, "get_checkpointer", AsyncMock(return_value=saver))

        answer = await debugger.steps(TENANT, thread)

        assert answer["thread_id"] == thread and answer["paused"] is False and answer["next"] == []
        steps = answer["steps"]
        assert [s["source"] for s in steps] == ["input", "loop", "loop", "loop"]
        assert [s["node"] for s in steps] == ["", "__start__", "reason", "evaluate"]
        assert [s["next"] for s in steps] == [[], ["reason"], ["evaluate"], []]
        assert steps[0]["state"]["status"] == "running" and "grant_token" not in steps[0]["state"]  # the input
        assert "grant_token" not in steps[1]["state"] and steps[1]["state"]["status"] == "running"
        assert "messages" in steps[2]["changed"] and steps[2]["state"]["messages"][-1]["type"] == "ai"
        assert set(steps[3]["changed"]) == {"status", "output"}
        assert steps[3]["state"]["output"] == {"summary": "done", "secret_token": "[redacted]"}
        assert all(s["checkpoint_id"] for s in steps) and [s["index"] for s in steps] == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_a_paused_thread_says_so_and_names_the_node_it_waits_on(self, monkeypatch):
        saver = MemorySaver()
        thread = new_thread_id(TENANT)
        await _run_thread(saver, thread, interrupt_before=["evaluate"])
        monkeypatch.setattr(debugger, "get_checkpointer", AsyncMock(return_value=saver))

        answer = await debugger.steps(TENANT, thread)

        assert answer["paused"] is True and answer["next"] == ["evaluate"]
        assert answer["steps"][-1]["state"]["status"] == "running"

    @pytest.mark.asyncio
    async def test_inspect_returns_one_value_by_path_and_refuses_hidden_ones(self, monkeypatch):
        saver = MemorySaver()
        thread = new_thread_id(TENANT)
        await _run_thread(saver, thread)
        monkeypatch.setattr(debugger, "get_checkpointer", AsyncMock(return_value=saver))
        last = (await debugger.steps(TENANT, thread))["steps"][-1]["checkpoint_id"]

        assert (await debugger.inspect(TENANT, thread, last, "output.summary"))["value"] == "done"
        whole = await debugger.inspect(TENANT, thread, last, "")
        assert whole["value"]["status"] == "completed" and "grant_token" not in whole["value"]
        assert whole["value"]["output"]["secret_token"] == "[redacted]" and whole["truncated"] is False
        message = await debugger.inspect(TENANT, thread, last, "messages.1")
        assert message["value"] == {"type": "ai", "content": "thinking"}
        for path, status in (
            ("grant_token", 403),
            ("output.secret_token", 403),
            ("output.nope", 404),
            ("messages.9", 404),
        ):
            with pytest.raises(debugger.DebugError) as info:
                await debugger.inspect(TENANT, thread, last, path)
            assert info.value.status == status, path
        with pytest.raises(debugger.DebugError) as info:
            await debugger.inspect(TENANT, thread, "no-such-checkpoint", "")
        assert info.value.code == "step_not_found"

    @pytest.mark.asyncio
    async def test_a_large_value_is_cut_and_says_so(self, monkeypatch):
        saver = MemorySaver()
        thread = new_thread_id(TENANT)
        await _run_thread(saver, thread)
        monkeypatch.setattr(debugger, "get_checkpointer", AsyncMock(return_value=saver))
        monkeypatch.setattr(debugger, "INSPECT_BYTES", 20)
        last = (await debugger.steps(TENANT, thread))["steps"][-1]["checkpoint_id"]

        answer = await debugger.inspect(TENANT, thread, last, "output")

        assert answer["truncated"] is True and isinstance(answer["value"], str) and answer["bytes"] > 20

    @pytest.mark.asyncio
    async def test_another_tenants_thread_is_refused_before_the_store_is_read(self, monkeypatch):
        store = AsyncMock()
        monkeypatch.setattr(debugger, "get_checkpointer", store)
        thread = new_thread_id(OTHER)
        for call in (debugger.steps(TENANT, thread), debugger.inspect(TENANT, thread, "c1", "")):
            with pytest.raises(debugger.DebugError) as info:
                await call
            assert info.value.status == 403 and info.value.code == "thread_tenant_mismatch"
        store.assert_not_awaited()


# ── The runner's pause ─────────────────────────────────────────────────────────


class TestRunnerPause:
    def test_paused_result_carries_the_state_so_far_and_where_it_stopped(self):
        result = debugger.paused_result(
            {"output": {"a": 1}, "reasoning_trace": ["t"], "tool_calls_log": [{"tool": "x"}], "confidence": 0.5},
            ["execute_tools"],
            thread_id="thread",
            latency_ms=12,
            tokens_used=3,
            cost_usd=0.01,
        )
        assert result["status"] == "paused" and result["paused_before"] == ["execute_tools"]
        assert result["thread_id"] == "thread" and result["tool_calls"] == [{"tool": "x"}]
        assert result["performance"] == {"total_latency_ms": 12, "llm_tokens_used": 3, "llm_cost_usd": 0.01}

    @pytest.mark.asyncio
    async def test_waiting_nodes_reads_the_snapshot_and_is_empty_when_it_cannot(self):
        compiled = MagicMock()
        compiled.aget_state = AsyncMock(return_value=SimpleNamespace(next=("execute_tools",)))
        assert await debugger.waiting_nodes(compiled, {}) == ["execute_tools"]
        compiled.aget_state = AsyncMock(side_effect=RuntimeError("no store"))
        assert await debugger.waiting_nodes(compiled, {}) == []

    @pytest.mark.asyncio
    async def test_a_debug_step_compiles_with_every_node_as_a_breakpoint_and_reports_the_pause(self):
        from core.langgraph import runner

        compiled = MagicMock()
        compiled.aget_state = AsyncMock(
            side_effect=[
                SimpleNamespace(values={"status": "running"}, next=("reason",)),
                SimpleNamespace(next=("execute_tools",)),
            ]
        )
        compiled.ainvoke = AsyncMock(return_value={"status": "running", "output": {}, "messages": []})
        graph = MagicMock()
        graph.compile = MagicMock(return_value=compiled)
        thread = new_thread_id(TENANT)
        with (
            patch.object(runner, "build_agent_graph", return_value=graph),
            patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        ):
            result = await runner.resume_agent(
                agent_id="a",
                thread_id=thread,
                decision={},
                system_prompt="s",
                authorized_tools=[],
                tenant_id=str(TENANT),
                require_paused=True,
                debug={"mode": "step", "breakpoints": ["evaluate"]},
            )
        assert graph.compile.call_args.kwargs["interrupt_before"] == list(debugger.NODES)
        assert result["status"] == "paused" and result["paused_before"] == ["execute_tools"]
        assert result["thread_id"] == thread

    @pytest.mark.asyncio
    async def test_a_debug_continue_compiles_with_the_agents_breakpoints_and_finishes_when_none_is_hit(self):
        from core.langgraph import runner

        compiled = MagicMock()
        compiled.aget_state = AsyncMock(
            side_effect=[SimpleNamespace(values={"status": "running"}, next=("reason",)), SimpleNamespace(next=())]
        )
        compiled.ainvoke = AsyncMock(return_value={"status": "completed", "output": {"a": 1}, "messages": []})
        graph = MagicMock()
        graph.compile = MagicMock(return_value=compiled)
        with (
            patch.object(runner, "build_agent_graph", return_value=graph),
            patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        ):
            result = await runner.resume_agent(
                agent_id="a",
                thread_id=new_thread_id(TENANT),
                decision={},
                system_prompt="s",
                authorized_tools=[],
                tenant_id=str(TENANT),
                require_paused=True,
                debug={"mode": "continue", "breakpoints": ["evaluate"]},
            )
        assert graph.compile.call_args.kwargs["interrupt_before"] == ["evaluate"]
        assert result["status"] == "completed" and result["output"] == {"a": 1}

    @pytest.mark.asyncio
    async def test_a_debug_step_of_a_thread_that_is_not_paused_is_refused(self):
        from core.langgraph import runner

        compiled = MagicMock()
        compiled.aget_state = AsyncMock(return_value=SimpleNamespace(values={"status": "completed"}, next=()))
        compiled.ainvoke = AsyncMock()
        graph = MagicMock()
        graph.compile = MagicMock(return_value=compiled)
        with (
            patch.object(runner, "build_agent_graph", return_value=graph),
            patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        ):
            result = await runner.resume_agent(
                agent_id="a",
                thread_id=new_thread_id(TENANT),
                decision={},
                system_prompt="s",
                authorized_tools=[],
                tenant_id=str(TENANT),
                require_paused=True,
                debug={"mode": "step", "breakpoints": []},
            )
        assert result["reason"] == "checkpoint_not_paused"
        compiled.ainvoke.assert_not_awaited()

    def test_the_run_path_pauses_at_breakpoints_and_the_api_records_the_session(self):
        runner = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        assert "breakpoints: list[str] | None = None," in runner
        assert "compiled = graph.compile(checkpointer=checkpointer, interrupt_before=list(breakpoints))" in runner
        assert "waiting = await debugger.waiting_nodes(compiled, config)" in runner
        assert 'run_span.set(**{"agent.thread_id": run_thread_id})' in runner
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert 'breakpoints=run_debugger.breakpoints_for_run(agent_config.get("config") or {})' in api
        assert "await run_debugger.open_session(" in api
        assert 'response["paused_before"] = list(lg_result.get("paused_before") or [])' in api
        # One source of the resume parameters for approvals and debug steps.
        assert api.count("_run_resume_spec(") == 3


# ── Routes ─────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_args, **_kw):
        return _Result(self.row)


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_breakpoint_routes_store_and_show_the_agents_breakpoints(self, monkeypatch):
        from api.v1 import agent_debug

        agent = SimpleNamespace(id=uuid.uuid4(), config={"limits": {"max_steps": 3}})
        monkeypatch.setattr(agent_debug, "get_tenant_session", lambda *_a, **_k: _Session(agent))
        monkeypatch.setattr(agent_debug, "require_agent_mutable", lambda *_a, **_k: None)
        monkeypatch.setattr(agent_debug, "require_agent_visible", lambda *_a, **_k: None)
        monkeypatch.setattr(settings, "runtime_debug_console_enabled", False)

        stored = await agent_debug.set_breakpoints(
            agent.id,
            agent_debug.BreakpointsIn(break_before=["evaluate", "reason"]),
            tenant_id=str(TENANT),
            user_domains=None,
            caller=None,
        )
        assert stored["break_before"] == ["reason", "evaluate"] and stored["enforced"] is False
        assert agent.config == {"limits": {"max_steps": 3}, "debug": {"break_before": ["reason", "evaluate"]}}

        shown = await agent_debug.get_breakpoints(agent.id, tenant_id=str(TENANT), user_domains=None, caller=None)
        assert shown["break_before"] == ["reason", "evaluate"] and shown["nodes"] == list(debugger.NODES)

        cleared = await agent_debug.set_breakpoints(
            agent.id,
            agent_debug.BreakpointsIn(break_before=None),
            tenant_id=str(TENANT),
            user_domains=None,
            caller=None,
        )
        assert cleared["break_before"] == [] and "debug" not in agent.config

        with pytest.raises(HTTPException) as info:
            await agent_debug.set_breakpoints(
                agent.id,
                agent_debug.BreakpointsIn(break_before=["lunch"]),
                tenant_id=str(TENANT),
                user_domains=None,
                caller=None,
            )
        assert info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_the_console_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import agent_debug

        monkeypatch.setattr(settings, "runtime_debug_console_enabled", False)
        agent_id = uuid.uuid4()
        thread = new_thread_id(TENANT)
        for call in (
            agent_debug.thread_steps(agent_id, thread, limit=10, tenant_id=str(TENANT), user_domains=None, caller=None),
            agent_debug.inspect_step(
                agent_id, thread, "c1", path="", tenant_id=str(TENANT), user_domains=None, caller=None
            ),
            agent_debug.list_sessions(agent_id, limit=10, tenant_id=str(TENANT), user_domains=None, caller=None),
            agent_debug.step_thread(agent_id, thread, tenant_id=str(TENANT), caller=None),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "debug_console_disabled"

    @pytest.mark.asyncio
    async def test_a_step_claims_the_session_reenters_the_run_and_records_where_it_stopped(self, monkeypatch):
        from api.v1 import agent_debug
        from core.langgraph import runner

        monkeypatch.setattr(settings, "runtime_debug_console_enabled", True)
        agent = SimpleNamespace(id=uuid.uuid4(), config={})
        thread = new_thread_id(TENANT)
        monkeypatch.setattr(agent_debug, "get_tenant_session", lambda *_a, **_k: _Session(agent))
        monkeypatch.setattr(agent_debug, "require_agent_mutable", lambda *_a, **_k: None)
        claim = debugger.Claim(
            thread_id=thread,
            breakpoints=["evaluate"],
            spec={"authorized_tools": ["lookup"], "confidence_floor": 0.9, "domain": "ops", "llm_model": "m"},
            system_prompt="be brief",
        )
        monkeypatch.setattr(debugger, "claim_session", AsyncMock(return_value=claim))
        finished = AsyncMock(return_value={"status": "paused", "paused_before": ["evaluate"], "steps_taken": 1})
        monkeypatch.setattr(debugger, "finish_session", finished)
        resumed = AsyncMock(
            return_value={"status": "paused", "paused_before": ["evaluate"], "thread_id": thread, "output": {"a": 1}}
        )
        monkeypatch.setattr(runner, "resume_agent", resumed)
        monkeypatch.setattr(agent_debug, "gate_agent_run", AsyncMock(return_value=None))

        answer = await agent_debug.step_thread(agent.id, thread, tenant_id=str(TENANT), caller=None)

        kwargs = resumed.call_args.kwargs
        assert kwargs["debug"] == {"mode": "step", "breakpoints": ["evaluate"]}
        assert kwargs["system_prompt"] == "be brief" and kwargs["authorized_tools"] == ["lookup"]
        assert kwargs["require_paused"] is True and kwargs["llm_model"] == "m"
        assert answer["status"] == "paused" and answer["paused_before"] == ["evaluate"] and answer["output"] == {"a": 1}
        assert finished.call_args.args[3]["status"] == "paused"

    @pytest.mark.asyncio
    async def test_a_refused_claim_is_the_sessions_state(self, monkeypatch):
        from api.v1 import agent_debug

        monkeypatch.setattr(settings, "runtime_debug_console_enabled", True)
        agent = SimpleNamespace(id=uuid.uuid4(), config={})
        monkeypatch.setattr(agent_debug, "get_tenant_session", lambda *_a, **_k: _Session(agent))
        monkeypatch.setattr(agent_debug, "require_agent_mutable", lambda *_a, **_k: None)
        monkeypatch.setattr(
            debugger, "claim_session", AsyncMock(return_value=debugger.Claim(refusal="session_running", status=409))
        )
        monkeypatch.setattr(agent_debug, "gate_agent_run", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await agent_debug.continue_thread(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)
        assert info.value.status_code == 409 and info.value.detail["error"] == "session_running"

    def test_session_dict_never_carries_the_resume_spec(self):
        row = SimpleNamespace(
            id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            thread_id="t",
            status="paused",
            paused_before=["reason"],
            breakpoints=["reason"],
            spec={"authorized_tools": ["x"]},
            steps_taken=2,
            last_status="paused",
            created_at=None,
            updated_at=None,
        )
        shown = debugger.session_dict(row)
        assert "spec" not in shown and shown["steps_taken"] == 2 and shown["paused_before"] == ["reason"]


# ── A debug step runs under the run's policies ─────────────────────────────────


def _ai(tokens: int, content: str = "step") -> AIMessage:
    return AIMessage(
        content=content, usage_metadata={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens}
    )


async def _resume_debug(compiled, *, mode="step", **extra):
    from core.langgraph import runner

    graph = MagicMock()
    graph.compile = MagicMock(return_value=compiled)
    with (
        patch.object(runner, "build_agent_graph", return_value=graph) as built,
        patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
    ):
        result = await runner.resume_agent(
            agent_id="a",
            thread_id=extra.pop("thread_id", new_thread_id(TENANT)),
            decision={},
            system_prompt="s",
            authorized_tools=[],
            tenant_id=str(TENANT),
            require_paused=True,
            debug={"mode": mode, "breakpoints": ["evaluate"]},
            **extra,
        )
    return result, built


class TestDebugStepPolicies:
    @pytest.mark.asyncio
    async def test_a_paused_step_reports_only_the_tokens_it_spent(self):
        before = [SystemMessage(content="s"), HumanMessage(content="h"), _ai(100, "first")]
        compiled = MagicMock()
        compiled.aget_state = AsyncMock(
            side_effect=[
                SimpleNamespace(values={"status": "running", "messages": before}, next=("reason",)),
                SimpleNamespace(next=("execute_tools",)),
            ]
        )
        compiled.ainvoke = AsyncMock(return_value={"status": "running", "messages": [*before, _ai(40)]})
        result, _ = await _resume_debug(compiled)
        assert result["status"] == "paused"
        assert result["performance"]["llm_tokens_used"] == 40
        assert 0 < result["performance"]["llm_cost_usd"] < 0.01

    @pytest.mark.asyncio
    async def test_a_finished_step_reports_only_the_tokens_it_spent(self):
        before = [_ai(70, "first")]
        compiled = MagicMock()
        compiled.aget_state = AsyncMock(
            side_effect=[
                SimpleNamespace(values={"status": "running", "messages": before}, next=("evaluate",)),
                SimpleNamespace(next=()),
            ]
        )
        compiled.ainvoke = AsyncMock(
            return_value={"status": "completed", "output": {"a": 1}, "messages": [*before, _ai(5)]}
        )
        result, _ = await _resume_debug(compiled, mode="continue")
        assert result["status"] == "completed" and result["performance"]["llm_tokens_used"] == 5

    @pytest.mark.asyncio
    async def test_the_runs_output_schema_and_limits_are_rebuilt_into_the_graph(self):
        compiled = MagicMock()
        compiled.aget_state = AsyncMock(
            side_effect=[SimpleNamespace(values={"status": "running"}, next=("evaluate",)), SimpleNamespace(next=())]
        )
        compiled.ainvoke = AsyncMock(return_value={"status": "completed", "output": {}, "messages": []})
        schema = {"type": "object", "required": ["summary"]}
        _, built = await _resume_debug(
            compiled,
            output_schema="invoice_summary",
            output_schema_json=schema,
            limits={"max_steps": 4, "max_tool_calls": 2},
        )
        kwargs = built.call_args.kwargs
        assert kwargs["output_schema"] == "invoice_summary" and kwargs["output_schema_json"] == schema
        assert kwargs["limits"] == {"max_steps": 4, "max_tool_calls": 2}
        assert compiled.ainvoke.call_args.kwargs["config"]["recursion_limit"] > 0

    @pytest.mark.asyncio
    async def test_a_step_is_held_to_the_runs_duration_limit(self):
        from core.langgraph import runner

        async def slow(*_args, **_kwargs):
            await asyncio.sleep(5)

        compiled = MagicMock()
        compiled.aget_state = AsyncMock(return_value=SimpleNamespace(values={"status": "running"}, next=("reason",)))
        compiled.ainvoke = AsyncMock(side_effect=slow)
        with patch.object(
            runner.execution_limits, "effective", return_value=SimpleNamespace(max_duration_seconds=0.05)
        ):
            result, _ = await _resume_debug(compiled, limits={"max_duration_seconds": 1})
        assert result["status"] == "failed" and result["reason"] == "duration_limit"

    @pytest.mark.asyncio
    async def test_an_approval_reached_by_a_step_is_reported_as_hitl_not_another_pause(self):
        before = [_ai(10, "first")]
        compiled = MagicMock()
        compiled.aget_state = AsyncMock(
            side_effect=[
                SimpleNamespace(values={"status": "running", "messages": before}, next=("hitl_gate",)),
                SimpleNamespace(next=("hitl_gate",)),
            ]
        )
        compiled.ainvoke = AsyncMock(
            return_value={
                "status": "running",
                "output": {"amount": 900},
                "messages": [*before, _ai(3)],
                "__interrupt__": [SimpleNamespace(value={"hitl_trigger": "amount above limit"})],
            }
        )
        thread = new_thread_id(TENANT)
        result, _ = await _resume_debug(compiled, thread_id=thread)
        assert result["status"] == "hitl_triggered" and result["hitl_trigger"] == "amount above limit"
        assert result["thread_id"] == thread and result["paused_before"] == []
        assert result["performance"]["llm_tokens_used"] == 3


class _ApprovalSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add(self, row):
        self.added.append(row)


def _step_agent(**extra):
    base = {
        "id": uuid.uuid4(),
        "config": {"region": "in"},
        "cost_controls": {},
        "agent_type": "ap_processor",
        "domain": "finance",
        "name": "Payables",
        "status": "active",
        "visibility": "tenant",
        "owner_user_id": None,
    }
    base.update(extra)
    return SimpleNamespace(**base)


def _wire_step(monkeypatch, agent, spec, result):
    from api.v1 import agent_debug
    from core.langgraph import runner

    monkeypatch.setattr(settings, "runtime_debug_console_enabled", True)
    monkeypatch.setattr(agent_debug, "get_tenant_session", lambda *_a, **_k: _Session(agent))
    monkeypatch.setattr(agent_debug, "require_agent_mutable", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_debug, "gate_agent_run", AsyncMock(return_value=None))
    claim = debugger.Claim(thread_id="t", breakpoints=["evaluate"], spec=spec, system_prompt="p")
    claimed = AsyncMock(return_value=claim)
    monkeypatch.setattr(debugger, "claim_session", claimed)
    finished = AsyncMock(return_value={"status": "paused"})
    monkeypatch.setattr(debugger, "finish_session", finished)
    released = AsyncMock()
    monkeypatch.setattr(debugger, "release_session", released)
    resumed = AsyncMock(return_value=result)
    monkeypatch.setattr(runner, "resume_agent", resumed)
    ledger = AsyncMock(return_value=True)
    monkeypatch.setattr(agent_debug, "_record_cost_ledger", ledger)
    return SimpleNamespace(claimed=claimed, finished=finished, released=released, resumed=resumed, ledger=ledger)


_BASE_SPEC = {"authorized_tools": ["lookup"], "confidence_floor": 0.9, "domain": "finance", "llm_model": "m"}


class TestDebugStepRoute:
    @pytest.mark.asyncio
    async def test_a_step_over_the_plan_limit_is_refused_before_the_session_is_claimed(self, monkeypatch):
        from api.v1 import agent_debug

        agent = _step_agent()
        wired = _wire_step(monkeypatch, agent, dict(_BASE_SPEC), {"status": "paused"})
        monkeypatch.setattr(
            agent_debug, "gate_agent_run", AsyncMock(return_value={"status": "limit_exceeded", "error": "limit"})
        )
        with pytest.raises(HTTPException) as info:
            await agent_debug.step_thread(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)
        assert info.value.status_code == 409 and info.value.detail["error"] == "limit_exceeded"
        wired.claimed.assert_not_awaited()
        wired.resumed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_step_over_the_agents_monthly_budget_is_refused(self, monkeypatch):
        from api.v1 import agent_debug

        agent = _step_agent(cost_controls={"monthly_cost_cap_usd": 5})
        wired = _wire_step(monkeypatch, agent, dict(_BASE_SPEC), {"status": "paused"})
        budget = AsyncMock(return_value={"status": "budget_exceeded", "error": {"message": "Monthly budget exceeded"}})
        monkeypatch.setattr(agent_debug, "_monthly_budget_refusal", budget)
        with pytest.raises(HTTPException) as info:
            await agent_debug.continue_thread(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)
        assert info.value.detail["error"] == "budget_exceeded"
        assert budget.call_args.args[2] == {"monthly_cost_cap_usd": 5}
        wired.claimed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_steps_own_usage_joins_the_cost_ledger_without_counting_a_new_task(self, monkeypatch):
        from api.v1 import agent_debug

        agent = _step_agent()
        perf = {"total_latency_ms": 9, "llm_tokens_used": 40, "llm_cost_usd": 0.0002}
        wired = _wire_step(monkeypatch, agent, dict(_BASE_SPEC), {"status": "paused", "performance": perf})
        answer = await agent_debug.step_thread(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)
        args, kwargs = wired.ledger.call_args
        assert args[1] == agent.id and args[2] == perf and kwargs == {"count_task": False}
        assert answer["usage_recorded"] is True and answer["performance"] == perf

    @pytest.mark.asyncio
    async def test_connector_credentials_are_resolved_again_from_the_connector_ids(self, monkeypatch):
        from api.v1 import agent_debug

        agent = _step_agent()
        spec = {**_BASE_SPEC, "connector_ids": ["c-1"], "connector_names": ["books"], "company_id": None}
        wired = _wire_step(monkeypatch, agent, spec, {"status": "paused"})
        ownership = AsyncMock(return_value=None)
        monkeypatch.setattr(agent_debug, "_assert_connectors_ready_for_dispatch", ownership)
        resolved = AsyncMock(return_value=({"api_key": "synthetic-test-value"}, ["books"]))
        monkeypatch.setattr(agent_debug, "_resolve_connector_configs", resolved)

        await agent_debug.step_thread(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)

        kwargs = wired.resumed.call_args.kwargs
        assert kwargs["connector_config"] == {"api_key": "synthetic-test-value"}
        assert kwargs["connector_names"] == ["books"]
        assert resolved.call_args.kwargs["connector_ids"] == ["c-1"]
        assert resolved.call_args.kwargs["agent_level_config"] == {"region": "in"}
        assert ownership.call_args.kwargs["linked_connector_ids"] == ["c-1"]
        # Nothing decrypted is written back into the session's spec.
        assert "api_key" not in str(spec)

    @pytest.mark.asyncio
    async def test_a_connector_no_longer_allowed_refuses_the_step_and_leaves_the_session_paused(self, monkeypatch):
        from api.v1 import agent_debug

        agent = _step_agent()
        spec = {**_BASE_SPEC, "connector_ids": ["c-1"]}
        wired = _wire_step(monkeypatch, agent, spec, {"status": "paused"})
        monkeypatch.setattr(
            agent_debug,
            "_assert_connectors_ready_for_dispatch",
            AsyncMock(side_effect=HTTPException(403, detail={"error": "connector_not_available"})),
        )
        with pytest.raises(HTTPException) as info:
            await agent_debug.step_thread(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)
        assert info.value.status_code == 403
        wired.released.assert_awaited_once()
        wired.resumed.assert_not_awaited()
        wired.finished.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_runs_output_schema_and_limits_reach_every_resume(self, monkeypatch):
        from api.v1 import agent_debug

        agent = _step_agent()
        schema = {"type": "object", "required": ["summary"]}
        spec = {
            **_BASE_SPEC,
            "output_schema": "invoice_summary",
            "output_schema_json": schema,
            "limits": {"max_steps": 4},
        }
        wired = _wire_step(monkeypatch, agent, spec, {"status": "paused"})
        for advance in (agent_debug.step_thread, agent_debug.continue_thread):
            await advance(agent.id, new_thread_id(TENANT), tenant_id=str(TENANT), caller=None)
            kwargs = wired.resumed.call_args.kwargs
            assert kwargs["output_schema"] == "invoice_summary" and kwargs["output_schema_json"] == schema
            assert kwargs["limits"] == {"max_steps": 4}

    @pytest.mark.asyncio
    async def test_an_approval_reached_by_a_step_opens_the_normal_approval(self, monkeypatch):
        from api.v1 import agent_debug
        from core.approvals.agent_run_resume import RESUME_SPEC_KEY
        from core.push import sender

        agent = _step_agent()
        thread = new_thread_id(TENANT)
        result = {
            "status": "hitl_triggered",
            "hitl_trigger": "amount above limit",
            "thread_id": thread,
            "confidence": 0.95,
            "output": {"amount": 900},
            "performance": {"llm_tokens_used": 3, "llm_cost_usd": 0.00001},
        }
        spec = {**_BASE_SPEC, "connector_ids": []}
        wired = _wire_step(monkeypatch, agent, spec, result)
        approvals = _ApprovalSession()
        sessions = iter([_Session(agent), approvals])
        monkeypatch.setattr(agent_debug, "get_tenant_session", lambda *_a, **_k: next(sessions))
        notified = AsyncMock(return_value={})
        monkeypatch.setattr(sender, "notify_approval_created", notified)

        answer = await agent_debug.step_thread(agent.id, thread, tenant_id=str(TENANT), caller=None)

        (item,) = approvals.added
        assert item.checkpoint_thread_id == thread and item.agent_id == agent.id and item.workflow_run_id is None
        assert item.context[RESUME_SPEC_KEY]["confidence_floor"] == 0.9
        assert item.context["trigger"] == "amount above limit" and item.trigger_type == "policy_condition"
        assert answer["status"] == "hitl_triggered" and answer["approval_id"] == str(item.id)
        assert notified.call_args.kwargs["item_id"] == str(item.id)
        assert wired.finished.call_args.args[3]["status"] == "hitl_triggered"
        assert wired.ledger.await_count == 1


class TestDebugResumeSpec:
    def test_the_spec_keeps_policies_and_connector_ids_never_credentials(self):
        from api.v1.agents import _debug_resume_spec

        spec = _debug_resume_spec(
            {
                "output_schema": "invoice_summary",
                "config": {"output_schema_json": {"type": "object"}, "limits": {"max_steps": 3}},
            },
            ["c-1"],
        )
        assert spec["output_schema"] == "invoice_summary"
        assert spec["connector_ids"] == ["c-1"]
        assert set(spec) == {"output_schema", "output_schema_json", "limits", "connector_ids"}

    def test_the_run_route_records_the_debug_spec(self):
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert "**_debug_resume_spec(agent_config, raw_connector_ids)," in api

    @pytest.mark.asyncio
    async def test_a_ledger_write_for_a_step_adds_usage_but_not_a_task(self, monkeypatch):
        from api.v1 import agents

        ledger = SimpleNamespace(token_count=10, cost_usd=0.5, task_count=1)

        class _LedgerSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *_args, **_kw):
                return _Result(ledger)

        monkeypatch.setattr(agents, "get_tenant_session", lambda *_a, **_k: _LedgerSession())
        ok = await agents._record_cost_ledger(
            TENANT, uuid.uuid4(), {"llm_tokens_used": 40, "llm_cost_usd": 0.25}, count_task=False
        )
        assert ok is True and ledger.token_count == 50 and ledger.cost_usd == 0.75 and ledger.task_count == 1
