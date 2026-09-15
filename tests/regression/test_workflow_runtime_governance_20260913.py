"""Regression tests for the 2026-09-13 workflow-runtime deep-fix findings.

Each test replays a confirmed bug (failing before the fix, passing after):

1. ToolGateway never wired: BaseAgent tool calls outside ``authorized_tools``
   must be denied and a failed tool call must fail the step; the gateway
   idempotency check is a SET NX reservation; ``_tools_to_scopes`` emits
   read/write scopes the Grantex SDK resolves.
2. HITL timeouts: ``timeout_workflow_hitl`` fails the run with
   ``error.code=hitl_timeout`` (or escalates once under an auto_escalate
   policy) and the approvals list keeps expired items visible.
3. ``on_failure: retry(N)`` actually retries idempotent steps and never
   retries notify / unkeyed writes; ``retry(N) then continue`` is honoured.
4. PII placeholders are restored before the connector call and re-masked in
   the tool result returned to the model.
5. Notify step runs ``send_email`` off the event loop behind the action
   policy; fake embeddings / fake LLM are gated on a relaxed runtime.
6. Content-safety endpoint passes the authenticated tenant to the checker.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from workflows.engine import WorkflowEngine
from workflows.state_store import InMemoryWorkflowStateRepository, WorkflowStateStore


def _engine() -> tuple[WorkflowEngine, InMemoryWorkflowStateRepository]:
    repo = InMemoryWorkflowStateRepository()
    return WorkflowEngine(WorkflowStateStore(repository=repo, redis=None)), repo


def _import_workflow_tasks():
    """Import core.tasks.workflow_tasks, tolerating PEP 695 syntax in
    core.tasks.async_runner on interpreters older than 3.12 (CI runs 3.12)."""
    try:
        from core.tasks import workflow_tasks
    except SyntaxError:
        stub = types.ModuleType("core.tasks.async_runner")

        def run_async(awaitable):  # pragma: no cover - fallback shim only
            return asyncio.run(awaitable)

        stub.run_async = run_async
        sys.modules["core.tasks.async_runner"] = stub
        from core.tasks import workflow_tasks
    return workflow_tasks


# ---------------------------------------------------------------------------
# 1. ToolGateway wiring / governed BaseAgent tool path
# ---------------------------------------------------------------------------
def _base_agent(**overrides: Any):
    from core.agents.base import BaseAgent

    kwargs: dict[str, Any] = {
        "agent_id": "agent-1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "domain": "sales",
        "authorized_tools": ["hubspot:list_contacts"],
    }
    kwargs.update(overrides)
    return BaseAgent(**kwargs)


@pytest.mark.asyncio
async def test_base_agent_tool_call_outside_authorized_tools_is_denied() -> None:
    """Before: ``{"error": "No tool gateway configured"}`` for every call.
    After: unauthorized tools are denied with E1007 before any connector work."""
    agent = _base_agent()
    with patch("core.langgraph.tool_adapter._execute_connector_tool", new=AsyncMock()) as execute:
        result = await agent._call_tool(connector_name="hubspot", tool_name="create_contact", params={})
    assert result["error"]["code"] == "E1007"
    assert "scope_denied" in result["error"]["message"]
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_base_agent_authorized_tool_routes_through_governed_connector_path() -> None:
    agent = _base_agent()
    with (
        patch("core.langgraph.tool_adapter.load_connector_config", new=AsyncMock(return_value={"k": "v"})),
        patch(
            "core.langgraph.tool_adapter._execute_connector_tool",
            new=AsyncMock(return_value={"contacts": []}),
        ) as execute,
    ):
        result = await agent._call_tool(connector_name="hubspot", tool_name="list_contacts", params={"limit": 1})
    assert result == {"contacts": []}
    kwargs = execute.call_args.kwargs
    assert kwargs["tenant_id"] == agent.tenant_id
    assert kwargs["company_id"] == agent.company_id
    assert kwargs["domain"] == "sales"
    assert execute.call_args.args[3] == {"k": "v"}


@pytest.mark.asyncio
async def test_base_agent_missing_connector_config_fails_closed() -> None:
    agent = _base_agent()
    with (
        patch("core.langgraph.tool_adapter.load_connector_config", new=AsyncMock(return_value=None)),
        patch("core.langgraph.tool_adapter._execute_connector_tool", new=AsyncMock()) as execute,
    ):
        result = await agent._call_tool(connector_name="hubspot", tool_name="list_contacts", params={})
    assert result["error"]["code"] == "E1005"
    execute.assert_not_called()


def test_is_tool_authorized_bare_name_never_unlocks_other_connector() -> None:
    from core.langgraph.tool_adapter import is_tool_authorized

    assert is_tool_authorized(["hubspot.list_contacts"], "hubspot", "list_contacts")
    assert is_tool_authorized(["tool:hubspot:read:list_contacts"], "hubspot", "list_contacts")
    assert not is_tool_authorized(["hubspot:list_contacts"], "salesforce", "list_contacts")
    assert not is_tool_authorized([], "hubspot", "list_contacts")


@pytest.mark.asyncio
async def test_base_agent_failed_tool_call_fails_the_step() -> None:
    """Before: a tool error was fed to synthesis and the step completed."""
    from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

    agent = _base_agent(authorized_tools=["hubspot:list_contacts"])
    agent.confidence_floor = 0.0
    task = TaskAssignment(
        message_id="m",
        correlation_id="c",
        workflow_run_id="r",
        workflow_definition_id="d",
        step_id="s",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(agent_id="agent-1", agent_type="custom", agent_token="t"),
        task=TaskInput(action="process", inputs={}),
    )
    llm_output = {
        "status": "completed",
        "confidence": 0.99,
        "tool_calls": [{"connector": "hubspot", "tool": "list_contacts", "params": {}}],
    }
    with (
        patch.object(agent, "_reason", new=AsyncMock(return_value=dict(llm_output))),
        patch.object(agent, "_build_tool_descriptions", return_value=None),
        patch.object(agent, "_call_tool", new=AsyncMock(return_value={"error": {"code": "E1001", "message": "boom"}})),
        patch.object(agent, "_synthesize_with_tools", new=AsyncMock()) as synth,
    ):
        result = await agent.execute(task)
    assert result.status == "failed"
    assert result.error["code"] == "E1001"
    assert "hubspot.list_contacts" in result.error["message"]
    synth.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_idempotency_is_a_reservation_not_check_then_store() -> None:
    """Two concurrent calls with the same key must execute the connector once."""
    from core.tool_gateway.gateway import ToolGateway
    from core.tool_gateway.idempotency import IdempotencyStore

    class FakeRedis:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.data:
                return None
            self.data[key] = value
            return True

        async def get(self, key):
            return self.data.get(key)

        async def setex(self, key, ttl, value):
            self.data[key] = value

        async def delete(self, key):
            self.data.pop(key, None)

    store = IdempotencyStore()
    store.redis = FakeRedis()

    calls = 0

    class Connector:
        async def execute_tool(self, tool, params):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"ok": True, "n": calls}

    gateway = ToolGateway(idempotency_store=store)
    gateway.register_connector("hubspot", Connector())
    with patch("core.tool_gateway.gateway.is_strict_runtime_env", return_value=False):
        kwargs = {
            "tenant_id": "t1",
            "agent_id": "a1",
            "agent_scopes": ["tool:hubspot:read:contacts"],
            "connector_name": "hubspot",
            "tool_name": "list_contacts",
            "params": {},
            "idempotency_key": "k1",
            "run_grant": NO_RUN_GRANT_FOR_TESTS,
        }
        first, second = await asyncio.gather(gateway.execute(**kwargs), gateway.execute(**kwargs))
        third = await gateway.execute(**kwargs)
    assert calls == 1
    results = sorted([first, second], key=lambda r: "error" in r)
    assert results[0] == {"ok": True, "n": 1}
    assert results[1]["error"]["code"] == "E1009"
    assert third == {"ok": True, "n": 1}


@pytest.mark.asyncio
async def test_gateway_releases_reservation_on_connector_error() -> None:
    from core.tool_gateway.gateway import ToolGateway
    from core.tool_gateway.idempotency import IdempotencyStore

    store = IdempotencyStore()
    store.reserve = AsyncMock(return_value=(True, None))
    store.release = AsyncMock()
    store.store = AsyncMock()

    class Connector:
        async def execute_tool(self, tool, params):
            raise RuntimeError("upstream down")

    gateway = ToolGateway(idempotency_store=store)
    gateway.register_connector("hubspot", Connector())
    with patch("core.tool_gateway.gateway.is_strict_runtime_env", return_value=False):
        result = await gateway.execute(
            tenant_id="t1",
            agent_id="a1",
            agent_scopes=["tool:hubspot:read:contacts"],
            connector_name="hubspot",
            tool_name="list_contacts",
            params={},
            idempotency_key="k1",
            run_grant=NO_RUN_GRANT_FOR_TESTS,
        )
    assert result["error"]["code"] == "E1001"
    store.release.assert_awaited_once()
    store.store.assert_not_awaited()


def test_grantex_tools_to_scopes_emits_sdk_permission_levels() -> None:
    """Before: ``tool:hubspot:execute:list_contacts`` — ``execute`` resolves
    to no permission level in ``grantex.enforce`` so every call was denied."""
    from grantex import Grantex

    from core.langgraph.grantex_auth import _tools_to_scopes

    scopes = _tools_to_scopes(["list_contacts", "create_contact"])
    assert "tool:hubspot:read:list_contacts" in scopes
    assert "tool:hubspot:write:create_contact" in scopes
    assert not any(":execute:" in scope for scope in scopes)
    assert Grantex._resolve_granted_permission(scopes, "hubspot") == "write"


def test_dead_grantex_helpers_are_removed() -> None:
    import core.langgraph.grantex_auth as grantex_auth

    assert not hasattr(grantex_auth, "debit_budget")
    assert not hasattr(grantex_auth, "log_audit_entry")


# ---------------------------------------------------------------------------
# 2. HITL timeout enforcement
# ---------------------------------------------------------------------------
def _hitl_waiting_state(run_id: str, *, policy: dict | None = None) -> dict:
    output = {
        "step_id": "approve",
        "type": "human_in_loop",
        "status": "waiting_hitl",
        "assignee_role": "manager",
        "timeout_hours": 1,
        "approval_timeout_policy": policy,
    }
    return {
        "id": run_id,
        "status": "waiting_hitl",
        "waiting_step_id": "approve",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workflow_run_id": "33333333-3333-3333-3333-333333333333",
        "definition": {
            "steps": [
                {"id": "approve", "type": "human_in_loop", "timeout_hours": 1},
                {"id": "pay", "type": "agent", "agent": "x", "depends_on": ["approve"]},
            ]
        },
        "step_results": {"approve": {"output": output, "status": "waiting_hitl", "confidence": None}},
        "steps_completed": 1,
    }


@pytest.mark.asyncio
async def test_timeout_workflow_hitl_fails_run_and_expires_items() -> None:
    workflow_tasks = _import_workflow_tasks()
    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()
    store.close = AsyncMock()
    await store.save(_hitl_waiting_state("run-hitl"))

    expire = AsyncMock(return_value=1)
    sync = AsyncMock()
    with (
        patch.object(workflow_tasks, "_state_store", return_value=store),
        patch("workflows.run_sync.expire_pending_hitl_items", expire),
        patch("workflows.run_sync.sync_engine_state_to_workflow_run", sync),
    ):
        result = await workflow_tasks._timeout_workflow_hitl_async("run-hitl", "approve")

    assert result["status"] == "timed_out"
    state = repo.states["run-hitl"]["state"]
    assert state["status"] == "failed"
    assert state["error"]["code"] == "hitl_timeout"
    assert state["step_results"]["approve"]["status"] == "timed_out"
    assert "waiting_step_id" not in state
    assert "pay" not in state["step_results"]
    expire.assert_awaited_once()
    assert expire.call_args.kwargs["step_id"] == "approve"
    sync.assert_awaited_once()
    assert sync.call_args.kwargs["state"]["status"] == "failed"


@pytest.mark.asyncio
async def test_timeout_workflow_hitl_is_noop_once_decided() -> None:
    workflow_tasks = _import_workflow_tasks()
    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()
    store.close = AsyncMock()
    state = _hitl_waiting_state("run-decided")
    state["status"] = "running"
    state.pop("waiting_step_id")
    await store.save(state)

    with patch.object(workflow_tasks, "_state_store", return_value=store):
        result = await workflow_tasks._timeout_workflow_hitl_async("run-decided", "approve")
    assert result["status"] == "noop"
    assert repo.states["run-decided"]["state"]["status"] == "running"


@pytest.mark.asyncio
async def test_timeout_workflow_hitl_escalates_once_under_auto_escalate_policy() -> None:
    workflow_tasks = _import_workflow_tasks()
    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()
    store.close = AsyncMock()
    policy = {"timeout_outcome": "auto_escalate", "escalation_role": "cmo", "default_sla_hours": 2}
    await store.save(_hitl_waiting_state("run-esc", policy=policy))

    reassign = AsyncMock(return_value=1)
    schedule = MagicMock(return_value=True)
    sync = AsyncMock()
    with (
        patch.object(workflow_tasks, "_state_store", return_value=store),
        patch("workflows.run_sync.expire_pending_hitl_items", reassign),
        patch("workflows.run_sync.schedule_hitl_timeout", schedule),
        patch("workflows.run_sync.sync_engine_state_to_workflow_run", sync),
    ):
        first = await workflow_tasks._timeout_workflow_hitl_async("run-esc", "approve")
        second = await workflow_tasks._timeout_workflow_hitl_async("run-esc", "approve")

    assert first["status"] == "escalated" and first["escalation_role"] == "cmo"
    assert reassign.call_args_list[0].kwargs["assignee_role"] == "cmo"
    assert reassign.call_args_list[0].kwargs["new_status"] == "pending"
    schedule.assert_called_once()
    # Second deadline: no further escalation, the run fails.
    assert second["status"] == "timed_out"
    state = repo.states["run-esc"]["state"]
    assert state["status"] == "failed" and state["error"]["code"] == "hitl_timeout"


def test_hitl_timeout_hours_prefers_engine_result_over_definition() -> None:
    from workflows.run_sync import hitl_timeout_hours

    assert hitl_timeout_hours({"output": {"timeout_hours": 2.5}}, {"timeout_hours": 8}) == 2.5
    assert hitl_timeout_hours({"output": {}}, {"timeout_hours": 8}) == 8
    assert hitl_timeout_hours({}, {}) == 4.0


def test_schedule_hitl_timeout_queues_celery_task_with_eta() -> None:
    _import_workflow_tasks()
    from workflows.run_sync import schedule_hitl_timeout

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    with patch("core.tasks.workflow_tasks.timeout_workflow_hitl") as task:
        assert schedule_hitl_timeout("run-1", "approve", expires_at) is True
    task.apply_async.assert_called_once_with(args=["run-1", "approve"], eta=expires_at)


def test_approvals_list_reports_deadline_passed_items_as_expired() -> None:
    from api.v1.approvals import _effective_status

    item = MagicMock(status="pending", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert _effective_status(item) == "expired"
    live = MagicMock(status="pending", expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert _effective_status(live) == "pending"
    decided = MagicMock(status="decided", expires_at=datetime.now(UTC) - timedelta(hours=1))
    assert _effective_status(decided) == "decided"


def test_approvals_list_has_include_expired_filter() -> None:
    import inspect

    from api.v1.approvals import list_approvals

    assert "include_expired" in inspect.signature(list_approvals).parameters


# ---------------------------------------------------------------------------
# 3. retry(N)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_directive_retries_read_only_agent_step_until_success() -> None:
    """Before: ``execute_step`` returned ``{"status": "failed"}`` so
    ``retry_with_backoff`` never saw an exception and never retried."""
    engine, repo = _engine()
    attempts = 0

    async def flaky(step: dict, state: dict) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return {"step_id": step["id"], "type": "agent", "status": "failed", "error": {"code": "x"}}
        return {"step_id": step["id"], "type": "agent", "status": "completed", "output": {"ok": True}}

    definition = {
        "name": "r",
        "steps": [{"id": "fetch", "type": "agent", "agent": "x", "action": "fetch_data", "on_failure": "retry(3)"}],
    }
    with (
        patch("workflows.engine.execute_step", side_effect=flaky),
        patch("workflows.retry.asyncio.sleep", new=AsyncMock()),
    ):
        run_id = await engine.start_run(definition)
        result = await engine.execute(run_id)
    assert attempts == 3
    assert result["status"] == "completed"
    assert repo.states[run_id]["state"]["step_results"]["fetch"]["status"] == "completed"


@pytest.mark.asyncio
async def test_retry_directive_exhausted_returns_failed_result_and_fails_run() -> None:
    engine, _ = _engine()
    attempts = 0

    async def always_fail(step: dict, state: dict) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {"step_id": step["id"], "type": "agent", "status": "failed", "error": {"code": "x", "message": "m"}}

    definition = {
        "name": "r",
        "steps": [{"id": "fetch", "type": "agent", "agent": "x", "action": "list_items", "on_failure": "retry(2)"}],
    }
    with (
        patch("workflows.engine.execute_step", side_effect=always_fail),
        patch("workflows.retry.asyncio.sleep", new=AsyncMock()),
    ):
        run_id = await engine.start_run(definition)
        result = await engine.execute(run_id)
    assert attempts == 3  # 1 + 2 retries
    assert result["status"] == "failed"
    assert result["step_results"]["fetch"]["error"]["code"] == "x"


@pytest.mark.asyncio
async def test_retry_directive_never_retries_notify_or_unkeyed_writes() -> None:
    engine, _ = _engine()
    calls: list[str] = []

    async def fail_once(step: dict, state: dict) -> dict[str, Any]:
        calls.append(step["id"])
        return {"step_id": step["id"], "type": step["type"], "status": "failed", "error": {"code": "x"}}

    for step in (
        {"id": "n", "type": "notify", "connector": "email", "to": "a@b.co", "message": "hi", "on_failure": "retry(3)"},
        {
            "id": "w",
            "type": "connector_tool",
            "connector": "hubspot",
            "tool": "create_contact",
            "on_failure": "retry(3)",
        },
        {"id": "a", "type": "agent", "agent": "x", "action": "send_campaign", "on_failure": "retry(3)"},
    ):
        calls.clear()
        with (
            patch("workflows.engine.execute_step", side_effect=fail_once),
            patch("workflows.retry.asyncio.sleep", new=AsyncMock()),
        ):
            run_id = await engine.start_run({"name": "w", "steps": [step]})
            result = await engine.execute(run_id)
        assert calls == [step["id"]], step
        assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_directive_retries_keyed_write_and_get_http() -> None:
    engine, _ = _engine()
    calls: list[str] = []

    async def fail_always(step: dict, state: dict) -> dict[str, Any]:
        calls.append(step["id"])
        return {"step_id": step["id"], "type": step["type"], "status": "failed", "error": {"code": "x"}}

    step = {
        "id": "w",
        "type": "connector_tool",
        "connector": "hubspot",
        "tool": "create_contact",
        "idempotency_key": "k",
        "on_failure": "retry(1)",
    }
    with (
        patch("workflows.engine.execute_step", side_effect=fail_always),
        patch("workflows.retry.asyncio.sleep", new=AsyncMock()),
    ):
        run_id = await engine.start_run({"name": "w", "steps": [step]})
        await engine.execute(run_id)
    assert calls == ["w", "w"]
    # ``http`` is not a parser-accepted step type yet; the rule is pinned directly.
    assert WorkflowEngine._step_is_retryable({"id": "h", "type": "http", "method": "GET"}) is True
    assert WorkflowEngine._step_is_retryable({"id": "h", "type": "http", "method": "POST"}) is False


def test_step_allows_failure_honours_retry_then_continue() -> None:
    assert WorkflowEngine._step_allows_failure({"on_failure": "retry(3)"}) is False
    assert WorkflowEngine._step_allows_failure({"on_failure": "retry(3) then continue"}) is True
    assert WorkflowEngine._step_allows_failure({"on_failure": "retry(2), ignore"}) is True
    assert WorkflowEngine._step_allows_failure({"on_failure": "continue"}) is True
    assert WorkflowEngine._parse_retry_count("retry(3) then continue") == 3


# ---------------------------------------------------------------------------
# 4. PII placeholders in tool calls
# ---------------------------------------------------------------------------
class _FakeRedactor:
    mode = "before_llm"

    def redact(self, text: str) -> tuple[str, dict[str, str]]:
        token_map: dict[str, str] = {}
        counter = 0
        import re

        def _sub(match):
            nonlocal counter
            counter += 1
            token = f"<EMAIL_ADDRESS_{counter}>"
            token_map[token] = match.group(0)
            return token

        return re.sub(r"[\w.]+@[\w.]+", _sub, text), token_map


@pytest.mark.asyncio
async def test_tool_fn_deanonymizes_args_and_remasks_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before: ``<EMAIL_ADDRESS_1>`` was sent verbatim to the connector."""
    from core.langgraph import tool_adapter

    token_map = {"<EMAIL_ADDRESS_1>": "alice@example.com"}
    seen: dict[str, Any] = {}

    async def fake_execute(cn, tn, params, config, **kwargs):
        seen["params"] = params
        return {"contact": {"email": params["email"], "other": "bob@example.com"}, "items": [params["email"]]}

    monkeypatch.setattr(tool_adapter, "_execute_connector_tool", fake_execute)
    monkeypatch.setattr(
        tool_adapter,
        "_build_tool_index",
        lambda *a, **k: {"hubspot:get_contact": ("hubspot", "Get a contact")},
    )
    monkeypatch.setattr("core.pii.redactor.PIIRedactor", lambda: _FakeRedactor())

    tools = tool_adapter.build_tools_for_agent(["hubspot:get_contact"], {}, ["hubspot"], pii_token_map=token_map)
    assert len(tools) == 1
    result = await tools[0].coroutine(email="<EMAIL_ADDRESS_1>", nested={"e": ["<EMAIL_ADDRESS_1>"]})

    assert seen["params"]["email"] == "alice@example.com"
    assert seen["params"]["nested"]["e"] == ["alice@example.com"]
    # Result returned to the model is masked; known values reuse their token,
    # new PII gets a non-colliding token and is added to the shared map.
    assert result["contact"]["email"] == "<EMAIL_ADDRESS_1>"
    assert result["items"] == ["<EMAIL_ADDRESS_1>"]
    assert result["contact"]["other"] != "bob@example.com"
    assert token_map[result["contact"]["other"]] == "bob@example.com"
    assert token_map["<EMAIL_ADDRESS_1>"] == "alice@example.com"


def test_runner_shares_token_map_with_graph_tools() -> None:
    import inspect

    from core.langgraph import runner

    src = inspect.getsource(runner.run_agent)
    assert "pii_token_map=pii_token_map" in src
    assert src.index("pii_token_map: dict[str, str] = {}") < src.index("graph = build_agent_graph(")


# ---------------------------------------------------------------------------
# 5. Sync I/O in async paths + relaxed-env gates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_notify_step_sends_email_off_loop_after_governance(monkeypatch: pytest.MonkeyPatch) -> None:
    from workflows import step_types

    sent: list[tuple] = []

    def send_email(to, subject, html):
        sent.append((to, subject, html))
        return True

    decision = MagicMock(dispatch_allowed=True)
    evaluate = AsyncMock(return_value=decision)
    to_thread = AsyncMock(side_effect=lambda fn, *a: fn(*a))
    monkeypatch.setattr("core.email.send_email", send_email)
    monkeypatch.setattr(
        step_types, "_validated_workflow_company", AsyncMock(return_value="22222222-2222-2222-2222-222222222222")
    )
    with (
        patch("core.governance.action_policy.evaluate_action", evaluate),
        patch.object(step_types.asyncio, "to_thread", to_thread),
    ):
        result = await step_types._execute_notify(
            {"id": "n", "type": "notify", "connector": "email", "to": "a@b.co", "message": "hi"},
            {"tenant_id": "11111111-1111-1111-1111-111111111111", "context": {"company_id": "x", "domain": "sales"}},
        )
    assert result["status"] == "completed"
    assert sent == [("a@b.co", "AgenticOrg workflow notification", "hi")]
    to_thread.assert_awaited_once()
    assert evaluate.call_args.args[0] == "email:send_email"
    assert evaluate.call_args.kwargs["context"].domain == "sales"


@pytest.mark.asyncio
async def test_notify_step_contained_by_policy_never_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    from workflows import step_types

    send_email = MagicMock(return_value=True)
    decision = MagicMock(dispatch_allowed=False, reason="capability_flag_missing")
    decision.to_dict.return_value = {"reason": "capability_flag_missing"}
    monkeypatch.setattr("core.email.send_email", send_email)
    monkeypatch.setattr(
        step_types, "_validated_workflow_company", AsyncMock(return_value="22222222-2222-2222-2222-222222222222")
    )
    with patch("core.governance.action_policy.evaluate_action", AsyncMock(return_value=decision)):
        result = await step_types._execute_notify(
            {
                "id": "n",
                "type": "notify",
                "connector": "email",
                "to": "a@b.co",
                "message": "hi",
                "company_id": "x",
                "domain": "sales",
            },
            {"tenant_id": "11111111-1111-1111-1111-111111111111"},
        )
    assert result["status"] == "failed"
    assert result["error"]["code"] == "notify_action_contained"
    send_email.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_embeds_via_async_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import embeddings
    from core.rag import ingest

    embed_async = AsyncMock(return_value=[[0.1] * 384])
    monkeypatch.setattr(embeddings, "embed_async", embed_async)
    assert await ingest._embed_chunks(["hello"]) == [[0.1] * 384]
    embed_async.assert_awaited_once_with(["hello"])


@pytest.mark.asyncio
async def test_embed_async_uses_async_client_for_tei(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import embeddings

    monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", "1")
    monkeypatch.setenv("AGENTICORG_TEI_URL", "http://example/tei")
    monkeypatch.setattr(embeddings, "_tei_request_target", lambda: ("http://example/tei", {}))

    response = MagicMock()
    response.json.return_value = [[0.5] * 1024]
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__aenter__.return_value.post = AsyncMock(return_value=response)
    client.__aexit__.return_value = None
    with patch("httpx.AsyncClient", return_value=client) as async_client, patch("httpx.Client") as sync_client:
        out = await embeddings.embed_async(["hello"])
    assert out == [[0.5] * 1024]
    async_client.assert_called_once()
    sync_client.assert_not_called()
    kwargs = client.__aenter__.return_value.post.call_args.kwargs
    assert kwargs["json"]["inputs"] == ["hello"]


@pytest.mark.asyncio
async def test_embed_async_runs_in_process_model_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import embeddings

    monkeypatch.delenv("AGENTICORG_RAG_USE_BGE_M3", raising=False)
    monkeypatch.setattr(embeddings, "embed", lambda texts: [[1.0]])
    with patch.object(embeddings.asyncio, "to_thread", new=AsyncMock(side_effect=lambda fn, *a: fn(*a))) as to_thread:
        assert await embeddings.embed_async(["x"]) == [[1.0]]
    to_thread.assert_awaited_once()


def test_fake_embeddings_flag_is_inert_outside_relaxed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import embeddings

    monkeypatch.setenv("AGENTICORG_TEST_FAKE_EMBEDDINGS", "1")
    with patch.object(embeddings, "is_relaxed_env", return_value=False):
        assert embeddings._use_fake_embeddings() is False
    with patch.object(embeddings, "is_relaxed_env", return_value=True):
        assert embeddings._use_fake_embeddings() is True


@pytest.mark.asyncio
async def test_fake_llm_flag_is_inert_outside_relaxed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm import router as router_mod

    monkeypatch.setenv("AGENTICORG_TEST_FAKE_LLM", "1")
    router = router_mod.LLMRouter()
    gemini = AsyncMock(return_value="real-provider")
    with (
        patch.object(router_mod, "is_relaxed_env", return_value=False),
        patch.object(router, "_call_gemini", gemini),
    ):
        assert (
            await router._call_model("gemini-2.5-flash", [{"role": "user", "content": "hi"}], 0.1, 10)
            == "real-provider"
        )
    gemini.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. Content-safety tenant scoping / tenant_id on LLM calls
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_content_safety_endpoint_passes_authenticated_tenant() -> None:
    from api.v1 import content_safety

    checker = AsyncMock(return_value={"safe": True, "issues": [], "scores": {}})
    with patch.object(content_safety, "check_content_safety", checker):
        await content_safety.check_safety(
            content_safety.ContentSafetyRequest(text="hello"),
            tenant_id="11111111-1111-1111-1111-111111111111",
        )
    assert checker.call_args.kwargs["config"]["tenant_id"] == "11111111-1111-1111-1111-111111111111"


def test_content_safety_route_requires_tenant_context() -> None:
    import inspect

    from api.v1 import content_safety

    src = inspect.getsource(content_safety)
    assert "tenant_required=True" in src
    assert "Depends(get_current_tenant)" in inspect.getsource(content_safety.check_safety)


def test_content_factory_forwards_tenant_id_to_router() -> None:
    import inspect

    from core.agents.marketing import content_factory

    src = inspect.getsource(content_factory)
    assert src.count("llm_router.complete(") == src.count("tenant_id=self.tenant_id")
