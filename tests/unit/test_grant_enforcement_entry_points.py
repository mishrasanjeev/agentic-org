# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — every agent run entry point resolves, carries and honours the grant.

Entry points and where they are covered:

* ``POST /agents/{id}/run``, chat, A2A, MCP, voice, per-type wrappers — all
  run through ``core.langgraph.runner.run_agent``, which resolves the run
  grant unless the route already did (chat, A2A, MCP resolve it themselves so
  the caller's grant and the right agent are used);
* ``runner.resume_agent`` — resolves the grant again and replaces the
  checkpointed token;
* workflow agent steps, collaboration steps (which run agent steps), Celery
  workflow resume (which re-enters the workflow engine) and the sales
  pipeline — all run ``BaseAgent.execute``, whose tool calls go through
  ``BaseAgent._call_tool`` to ``execute_agent_tool`` or a ``ToolGateway``;
* workflow ``connector_tool`` steps — no agent, so no grant: recorded in warn,
  refused in deny.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant, resolve_run_grant

TENANT = str(uuid.UUID(int=0x1F1D))
AGENT = str(uuid.UUID(int=0xB0B))
PLACEHOLDER_TOKEN = "placeholder-grant-token"  # noqa: S105 - not a credential
REPO = pathlib.Path(__file__).resolve().parents[2]


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == name]


def _client(allowed: bool, reason_code: str = "") -> MagicMock:
    client = MagicMock()
    client.enforce.return_value = MagicMock(
        allowed=allowed, reason=reason_code, reason_code=reason_code, sub_reason="", grant_id="grnt_placeholder"
    )
    return client


# ── Resolution without an agent ──────────────────────────────────────────


async def test_a_call_with_no_agent_has_no_grant_to_resolve():
    grant = await resolve_run_grant(tenant_id=TENANT, agent_id="", mode=EnforcementMode.WARN)
    assert (grant.token, grant.missing_sub_reason) == ("", "no_agent")


# ── BaseAgent path: workflow steps, collaboration, Celery resume, sales ──


def _base_agent(**kwargs: Any):
    from core.agents.base import BaseAgent

    return BaseAgent(agent_id=AGENT, tenant_id=TENANT, authorized_tools=["hubspot:get_contact"], **kwargs)


async def test_base_agent_off_mode_keeps_the_legacy_tool_path():
    agent = _base_agent()
    executed = AsyncMock(return_value={"id": "c-1"})
    with (
        patch("core.agents.base.resolve_run_grant", AsyncMock(return_value=RunGrant(mode=EnforcementMode.OFF))),
        patch("core.langgraph.tool_adapter.execute_agent_tool", executed),
    ):
        await agent._call_tool("hubspot", "get_contact", {})
    assert executed.await_args.kwargs["run_grant"].mode is EnforcementMode.OFF


async def test_base_agent_resolves_the_grant_once_and_passes_it_to_every_call():
    agent = _base_agent()
    grant = RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted")
    resolve = AsyncMock(return_value=grant)
    executed = AsyncMock(return_value={"id": "c-1"})
    with (
        patch("core.agents.base.resolve_run_grant", resolve),
        patch("core.langgraph.tool_adapter.execute_agent_tool", executed),
    ):
        await agent._call_tool("hubspot", "get_contact", {})
        await agent._call_tool("hubspot", "get_contact", {})
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["agent_id"] == AGENT
    assert resolve.await_args.kwargs["tenant_id"] == TENANT
    assert all(call.kwargs["run_grant"] is grant for call in executed.await_args_list)


async def _execute_agent_tool(run_grant: RunGrant, client: MagicMock | None = None):
    from core.langgraph import tool_adapter

    dispatched = AsyncMock(return_value={"id": "c-1"})
    patches = [
        patch.object(tool_adapter, "_execute_connector_tool", dispatched),
        patch.object(tool_adapter, "load_connector_config", AsyncMock(return_value={})),
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client or _client(True)),
    ]
    for p in patches:
        p.start()
    try:
        with capture_logs() as logs:
            result = await tool_adapter.execute_agent_tool(
                "hubspot",
                "get_contact",
                {},
                tenant_id=TENANT,
                company_id=str(uuid.UUID(int=0xC0)),
                domain=None,
                authorized_tools=["hubspot:get_contact"],
                run_grant=run_grant,
                agent_id=AGENT,
            )
    finally:
        for p in patches:
            p.stop()
    return result, dispatched, logs


async def test_base_agent_tool_call_without_a_grant_runs_and_is_recorded_in_warn():
    result, dispatched, logs = await _execute_agent_tool(
        RunGrant(mode=EnforcementMode.WARN, source="none", missing_sub_reason="agent_not_registered")
    )
    assert dispatched.await_count == 1 and result == {"id": "c-1"}
    assert [(e["reason"], e["runtime"]) for e in _events(logs, "grant_enforcement_would_deny")] == [
        ("grant_missing", "base_agent")
    ]


async def test_base_agent_tool_call_without_a_grant_is_refused_in_deny():
    result, dispatched, _ = await _execute_agent_tool(
        RunGrant(mode=EnforcementMode.DENY, source="none", missing_sub_reason="agent_not_registered")
    )
    assert dispatched.await_count == 0
    assert result == {
        "error": {
            "code": "E1007",
            "message": "grant_denied: grant_missing",
            "reason": "grant_missing",
            "sub_reason": "agent_not_registered",
        }
    }


async def test_base_agent_tool_call_with_a_covering_grant_runs_in_deny():
    client = _client(True)
    result, dispatched, _ = await _execute_agent_tool(
        RunGrant(mode=EnforcementMode.DENY, token=PLACEHOLDER_TOKEN, source="minted"), client
    )
    assert dispatched.await_count == 1
    assert client.enforce.call_args.kwargs["grant_token"] == PLACEHOLDER_TOKEN


# ── Tool gateway ─────────────────────────────────────────────────────────


class _Connector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, params))
        return {"id": "c-1"}


async def _gateway_call(run_grant: RunGrant | None, *, agent_scopes: list[str], client: MagicMock | None = None):
    from core.tool_gateway.gateway import ToolGateway

    gateway = ToolGateway()
    connector = _Connector()
    gateway.register_connector("hubspot", connector, tenant_id=TENANT)
    kwargs: dict[str, Any] = {} if run_grant is None else {"run_grant": run_grant}
    with (
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client or _client(True)),
        capture_logs() as logs,
    ):
        result = await gateway.execute(
            tenant_id=TENANT,
            agent_id=AGENT,
            agent_scopes=agent_scopes,
            connector_name="hubspot",
            tool_name="get_contact",
            params={},
            **kwargs,
        )
    return result, connector, logs


async def test_gateway_off_mode_without_grant_or_scopes_is_still_denied_as_before():
    result, connector, _ = await _gateway_call(RunGrant(mode=EnforcementMode.OFF), agent_scopes=[])
    assert result["error"]["message"] == "scope_denied: missing_grant_and_legacy_scopes"
    assert connector.calls == []


async def test_gateway_warn_mode_records_the_missing_grant_and_keeps_legacy_scope_checks():
    grant = RunGrant(mode=EnforcementMode.WARN, source="none", missing_sub_reason="minting_unconfigured")
    allowed, connector, logs = await _gateway_call(grant, agent_scopes=["tool:hubspot:read:contact"])
    assert allowed == {"id": "c-1"} and len(connector.calls) == 1
    assert [e["runtime"] for e in _events(logs, "grant_enforcement_would_deny")] == ["tool_gateway"]

    refused, connector, _ = await _gateway_call(grant, agent_scopes=[])
    assert refused["error"]["message"] == "scope_denied: missing_grant_and_legacy_scopes"
    assert connector.calls == []


async def test_gateway_deny_mode_refuses_before_the_connector():
    grant = RunGrant(mode=EnforcementMode.DENY, token=PLACEHOLDER_TOKEN, source="minted")
    client = _client(False, "permission_insufficient")
    result, connector, logs = await _gateway_call(grant, agent_scopes=["tool:hubspot:admin"], client=client)
    assert result["error"]["code"] == "E1007"
    assert result["error"]["message"] == "grant_denied: permission_insufficient"
    assert result["error"]["reason"] == "permission_insufficient"
    assert connector.calls == []
    assert [e["reason"] for e in _events(logs, "grant_enforcement_denied")] == ["permission_insufficient"]


async def test_gateway_deny_mode_runs_a_call_the_grant_and_legacy_scopes_cover():
    grant = RunGrant(mode=EnforcementMode.DENY, token=PLACEHOLDER_TOKEN, source="minted")
    result, connector, _ = await _gateway_call(grant, agent_scopes=["tool:hubspot:read:contact"], client=_client(True))
    assert result == {"id": "c-1"} and len(connector.calls) == 1


async def test_base_agent_passes_the_grant_to_a_gateway_that_takes_it_in_every_mode():
    # ToolGateway.execute requires run_grant, so it is passed in off as well.
    gateway = MagicMock()
    gateway.execute = AsyncMock(return_value={"id": "c-1"})
    for mode in (EnforcementMode.OFF, EnforcementMode.WARN):
        agent = _base_agent(tool_gateway=gateway)
        with patch("core.agents.base.resolve_run_grant", AsyncMock(return_value=RunGrant(mode=mode))):
            await agent._call_tool("hubspot", "get_contact", {})
        assert gateway.execute.await_args.kwargs["run_grant"].mode is mode


async def test_base_agent_passes_the_grant_to_a_gateway_that_predates_it_only_when_enforcement_is_on():
    seen: list[dict[str, Any]] = []

    class _LegacyGateway:
        async def execute(
            self, *, tenant_id, agent_id, agent_scopes, connector_name, tool_name, params, idempotency_key=None
        ):
            seen.append({"connector": connector_name})
            return {"id": "c-1"}

    gateway = _LegacyGateway()
    agent = _base_agent(tool_gateway=gateway)
    with patch("core.agents.base.resolve_run_grant", AsyncMock(return_value=RunGrant(mode=EnforcementMode.OFF))):
        assert await agent._call_tool("hubspot", "get_contact", {}) == {"id": "c-1"}
    agent = _base_agent(tool_gateway=gateway)
    with (
        patch("core.agents.base.resolve_run_grant", AsyncMock(return_value=RunGrant(mode=EnforcementMode.WARN))),
        pytest.raises(TypeError),  # it cannot check the grant, so it never dispatches
    ):
        await agent._call_tool("hubspot", "get_contact", {})
    assert seen == [{"connector": "hubspot"}]


# ── Workflow connector_tool step: no agent, no grant ─────────────────────


@pytest.mark.parametrize(("mode", "dispatched"), [("off", 1), ("warn", 1), ("deny", 0)])
async def test_workflow_connector_step_without_an_agent_follows_the_mode(mode, dispatched):
    from workflows import step_types

    execute = AsyncMock(return_value={"id": "c-1"})
    with (
        patch("auth.run_grants.resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode(mode))),
        patch.object(step_types, "_validated_workflow_company", AsyncMock(return_value=uuid.UUID(int=0xC0))),
        patch.object(step_types, "_load_workflow_connector_config", AsyncMock(return_value={})),
        patch("core.langgraph.tool_adapter._execute_connector_tool", execute),
    ):
        result = await step_types._execute_connector_tool_step(
            {"id": "s1", "type": "connector_tool", "connector": "hubspot", "tool": "get_contact"},
            {"tenant_id": TENANT, "id": "run-1"},
        )
    assert execute.await_count == dispatched
    if mode == "deny":
        assert result["status"] == "failed"


# ── resume_agent re-resolves the grant ───────────────────────────────────


async def test_resume_replaces_the_checkpointed_token_with_a_fresh_grant():
    from core.langgraph import runner

    grant = RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted")
    captured: dict[str, Any] = {}

    class _Compiled:
        async def ainvoke(self, command, config=None):
            captured["command"] = command
            return {"status": "completed", "messages": []}

    graph = MagicMock()
    graph.compile.return_value = _Compiled()
    build = MagicMock(return_value=graph)
    with (
        patch.object(runner, "resolve_run_grant", AsyncMock(return_value=grant)) as resolve,
        patch.object(runner, "build_agent_graph", build),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
    ):
        await runner.resume_agent(
            agent_id=AGENT,
            thread_id=runner._run_thread_id(TENANT, "t-1", AGENT),
            decision={"action": "approve"},
            system_prompt="scripted",
            authorized_tools=[],
            tenant_id=TENANT,
        )
    assert resolve.await_args.kwargs["agent_id"] == AGENT
    assert build.call_args.kwargs["run_grant"] is grant
    assert captured["command"].update == {"grant_token": PLACEHOLDER_TOKEN}


async def test_resume_in_off_mode_leaves_the_checkpointed_state_alone():
    from core.langgraph import runner

    captured: dict[str, Any] = {}

    class _Compiled:
        async def ainvoke(self, command, config=None):
            captured["command"] = command
            return {"status": "completed", "messages": []}

    graph = MagicMock()
    graph.compile.return_value = _Compiled()
    with (
        patch.object(runner, "resolve_run_grant", AsyncMock(return_value=RunGrant(mode=EnforcementMode.OFF))),
        patch.object(runner, "build_agent_graph", MagicMock(return_value=graph)),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
    ):
        await runner.resume_agent(
            agent_id=AGENT,
            thread_id=runner._run_thread_id(TENANT, "t-1", AGENT),
            decision={},
            system_prompt="s",
            authorized_tools=[],
            tenant_id=TENANT,
        )
    assert captured["command"].update is None


async def test_resume_through_a_real_checkpoint_enforces_the_fresh_grant(scripted_model):
    """Interrupt a real graph, then resume it in warn mode with a new grant."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from core.langgraph.agent_graph import build_agent_graph
    from core.test_doubles.scripted_model import final

    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    graph = build_agent_graph(
        system_prompt="scripted",
        authorized_tools=[],
        confidence_floor=0.5,
        hitl_condition="total > 500000",
        run_grant=RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted"),
    )
    compiled = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "f1-resume-1"}}
    state = {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="go")],
        "agent_id": AGENT,
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "grant_token": "placeholder-old-grant",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }
    paused = await compiled.ainvoke(state, config)
    assert paused["__interrupt__"]
    resumed = await compiled.ainvoke(
        Command(resume={"action": "approve"}, update={"grant_token": PLACEHOLDER_TOKEN}), config
    )
    assert resumed["grant_token"] == PLACEHOLDER_TOKEN


# ── Routes that resolve before running (chat, A2A, MCP) ──────────────────


async def test_type_routes_use_a_caller_token_issued_to_the_type_agent():
    from api.v1 import agents

    row = MagicMock(id=uuid.UUID(AGENT), config={"grantex": {"grantex_agent_id": "ag_1", "grantex_scopes": ["s"]}})
    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(return_value=row)),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token=PLACEHOLDER_TOKEN,
            caller_agent_id=AGENT,
            runtime="a2a",
        )
    assert (grant.token, grant.source, grant.mode) == (PLACEHOLDER_TOKEN, "supplied", EnforcementMode.WARN)


async def test_type_routes_resolve_the_grant_of_the_agent_the_type_runs_as():
    from api.v1 import agents

    row = MagicMock(id=uuid.UUID(AGENT), config={"grantex": {"grantex_agent_id": "ag_1", "grantex_scopes": ["s"]}})
    resolve = AsyncMock(return_value=RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted"))
    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(return_value=row)),
        patch.object(agents, "resolve_run_grant", resolve),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token="",
            caller_agent_id="",
            runtime="mcp",
        )
    assert grant.token == PLACEHOLDER_TOKEN
    assert resolve.await_args.kwargs["agent_id"] == AGENT
    assert resolve.await_args.kwargs["grantex_config"] == {"grantex_agent_id": "ag_1", "grantex_scopes": ["s"]}
    assert resolve.await_args.kwargs["caller_token"] == ""


async def test_type_routes_without_a_matching_agent_have_no_grant():
    from api.v1 import agents

    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(return_value=None)),
    ):
        missing = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token="",
            caller_agent_id="",
            runtime="a2a",
        )
    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        failed = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token="",
            caller_agent_id="",
            runtime="a2a",
        )
    assert (missing.token, missing.missing_sub_reason) == ("", "no_agent")
    assert (failed.token, failed.missing_sub_reason) == ("", "lookup_failed")


async def test_type_routes_in_off_mode_pass_the_callers_token_through_without_lookups():
    from api.v1 import agents

    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.OFF)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(side_effect=AssertionError("no lookup in off"))),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token="",
            caller_agent_id="",
            runtime="a2a",
        )
    assert (grant.mode, grant.token) == (EnforcementMode.OFF, "")


def _source(module: str, function: str) -> str:
    import importlib

    return inspect.getsource(getattr(importlib.import_module(module), function))


@pytest.mark.parametrize(
    ("module", "function", "resolver"),
    [
        ("api.v1.chat", "chat_query", "resolve_run_grant("),
        ("api.v1.a2a", "create_task", "_resolve_run_grant_for_type("),
        ("api.v1.mcp", "call_tool", "_resolve_run_grant_for_type("),
        ("api.v1.agents", "run_agent", "resolve_run_grant("),
    ],
)
def test_routes_resolve_the_grant_before_running_and_pass_it_to_the_runner(module, function, resolver):
    src = _source(module, function)
    assert resolver in src
    assert "run_grant=run_grant" in src
    assert src.index(resolver) < src.index("run_grant=run_grant")


def test_chat_checks_the_grant_before_returning_a_deterministic_tds_answer():
    src = _source("api.v1.chat", "chat_query")
    assert src.index("direct_tool_call_permitted(") < src.index("if det is not None:\n        hitl_trigger")


# ── Voice and per-type wrappers run through the resolving runner ─────────


async def test_voice_turns_run_through_the_runner_with_the_session_grant():
    from core.voice.livekit_agent import VoiceAgentWorker

    worker = VoiceAgentWorker({"agent_id": AGENT, "tenant_id": TENANT}, grant_token=PLACEHOLDER_TOKEN)
    run = AsyncMock(return_value={"status": "completed", "output": {"response": "ok"}})
    with patch("core.langgraph.runner.run_agent", run):
        await worker.handle_call(object(), "hello")
    kwargs = run.await_args.kwargs
    assert kwargs["grant_token"] == PLACEHOLDER_TOKEN
    assert kwargs["agent_id"] == AGENT and kwargs["tenant_id"] == TENANT
    assert "run_grant" not in kwargs  # the runner resolves it


def _wrapper_modules() -> list[pathlib.Path]:
    return sorted((REPO / "core" / "langgraph" / "agents").glob("*.py"))


@pytest.mark.parametrize("path", _wrapper_modules(), ids=lambda p: p.stem)
def test_per_type_wrappers_forward_the_grant_to_the_resolving_runner(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_agent"
    ]
    if not calls:
        pytest.skip("module does not start runs")
    for call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "grant_token" in keywords, f"{path.stem} drops the caller's grant"
        assert "run_grant" not in keywords, f"{path.stem} bypasses run grant resolution"


# ── run_grant is required on the tool-call paths ─────────────────────────


def test_tool_call_paths_require_run_grant_as_a_keyword():
    from core.langgraph.tool_adapter import execute_agent_tool
    from core.tool_gateway.gateway import ToolGateway

    for function in (execute_agent_tool, ToolGateway.execute):
        parameter = inspect.signature(function).parameters["run_grant"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, function.__qualname__
        assert parameter.default is inspect.Parameter.empty, function.__qualname__


async def test_a_gateway_call_without_run_grant_is_refused_by_python():
    from core.tool_gateway.gateway import ToolGateway

    with pytest.raises(TypeError, match="run_grant"):
        await ToolGateway().execute(  # type: ignore[call-arg]
            tenant_id=TENANT,
            agent_id=AGENT,
            agent_scopes=["tool:hubspot:read:contact"],
            connector_name="hubspot",
            tool_name="get_contact",
            params={},
        )
