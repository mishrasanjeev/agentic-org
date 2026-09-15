# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — every route that starts a run is bound to the caller's Grantex token.

Grantex agent tokens skip the route scope checks (``api/route_enforcement.py``),
so a route that starts a run without passing the caller token would let a
caller run an agent past its own grant. Every such route binds it:

* ``POST /agents/{id}/run`` — into ``resolve_run_grant``; an approval the run
  pauses on records the binding, and the resume (which has no token) refuses
  tool calls;
* ``POST /workflows/{id}/run`` — the background execution holds the token in
  memory and the run state records the binding; agent steps (and
  collaboration/parallel steps, which run agent steps) bind it into
  ``BaseAgent``, connector steps into their grant, sub-workflows inherit it; a
  resumed run without the token refuses tool calls;
* sales ``process-lead``, ``followups/run``, ``seed-prospects``, ``import-csv``
  and ``process-inbox`` — into the sales agent (``BaseAgent``);
* chat, A2A and MCP — covered in test_grant_enforcement_caller_binding.py.

The Twilio voice webhook is signed by the telephony provider, not by a Grantex
token, and the public demo-request form runs the sales agent with no
authenticated caller, so neither has a caller to bind; SOP routes deploy
agents but run none.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from auth.grant_enforcement import DenialReason, EnforcementMode, GrantCallContext
from auth.run_grants import (
    CALLER_GRANT_KEY,
    NO_CALLER,
    CallerGrant,
    bind_caller_grant,
    caller_grant_for_run,
    caller_grant_from_request,
    check_run_grant,
    resolve_run_grant,
)

TENANT = str(uuid.UUID(int=0x1F1E))
RUN_AGENT = str(uuid.UUID(int=0xA11))
CALLER_AGENT = str(uuid.UUID(int=0xA22))
RUN = "placeholder-run-grant"  # noqa: S105 - not a credential
CALLER = "placeholder-caller-grant"  # noqa: S105 - not a credential
CONTEXT = GrantCallContext(tenant_id=TENANT, agent_id=RUN_AGENT, runtime="test")


def _grantex_request(token: str = CALLER, agent_id: str = CALLER_AGENT) -> SimpleNamespace:
    claims = {"sub": "agent:placeholder", "agenticorg:tenant_id": TENANT, "grantex:scopes": []}
    return SimpleNamespace(
        state=SimpleNamespace(
            claims=claims, scopes=[], auth_mode="grantex", grant_token=token, agent_id=agent_id, tenant_id=TENANT
        )
    )


def _legacy_request() -> SimpleNamespace:
    claims = {"sub": "user@example.com", "role": "admin", "agenticorg:agent_id": CALLER_AGENT}
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=["agenticorg:admin"], auth_mode="legacy", agent_id=CALLER_AGENT)
    )


def _enforcer(allowed_tokens: set[str]) -> MagicMock:
    client = MagicMock()

    def _enforce(*, grant_token: str, **_: Any) -> MagicMock:
        allowed = grant_token in allowed_tokens
        return MagicMock(
            allowed=allowed,
            reason_code="" if allowed else "tool_not_granted",
            sub_reason="",
            reason="",
            grant_id="grnt_placeholder",
        )

    client.enforce.side_effect = _enforce
    return client


# ── The caller grant of a request ─────────────────────────────────────────


def test_a_request_is_bound_when_it_authenticated_with_a_grantex_token():
    caller = caller_grant_from_request(_grantex_request())
    assert (caller.token, caller.agent_id, caller.bound) == (CALLER, CALLER_AGENT, True)
    assert caller.marker() == {"agent_id": CALLER_AGENT}
    assert CALLER not in repr(caller)


@pytest.mark.parametrize(
    "request_double",
    [
        _legacy_request(),  # an agent id claim without a Grantex token binds nothing
        SimpleNamespace(state=SimpleNamespace(grant_token=MagicMock(), agent_id=CALLER_AGENT)),
        SimpleNamespace(state=SimpleNamespace(grant_token="   ")),
        SimpleNamespace(),
        None,
    ],
)
def test_a_request_without_a_grantex_token_is_not_bound(request_double):
    caller = caller_grant_from_request(request_double)
    assert caller == NO_CALLER and not caller.bound and caller.marker() is None


# ── A binding whose token is unavailable ──────────────────────────────────


@pytest.mark.parametrize("mode", [EnforcementMode.WARN, EnforcementMode.DENY])
async def test_a_run_whose_caller_token_is_unavailable_refuses_every_tool_call(mode):
    grant = await resolve_run_grant(
        tenant_id=TENANT,
        agent_id=RUN_AGENT,
        supplied_token=RUN,
        mode=mode,
        runtime="test",
        **CallerGrant(agent_id=CALLER_AGENT, required=True).resolve_kwargs(),
    )
    assert (grant.token, grant.caller_token, grant.caller_token_unavailable) == (RUN, "", True)
    client = _enforcer({RUN, CALLER})
    check = await check_run_grant(
        grant, connector="hubspot", tool="get_contact", context=CONTEXT, client_factory=lambda: client
    )
    assert not check.dispatch_allowed  # strict in warn too: the caller check was strict before
    assert (check.denial.reason, check.denial.sub_reason) == (DenialReason.GRANT_MISSING, "caller_token_unavailable")
    client.enforce.assert_not_called()


async def test_off_mode_ignores_an_unavailable_caller_binding():
    grant = await resolve_run_grant(
        tenant_id=TENANT,
        agent_id=RUN_AGENT,
        supplied_token=RUN,
        mode=EnforcementMode.OFF,
        caller_agent_id=CALLER_AGENT,
        caller_required=True,
    )
    assert (grant.mode, grant.token, grant.caller_token_unavailable) == (EnforcementMode.OFF, RUN, False)


async def test_a_caller_token_that_is_present_is_never_marked_unavailable():
    grant = await resolve_run_grant(
        tenant_id=TENANT,
        agent_id=RUN_AGENT,
        supplied_token=RUN,
        mode=EnforcementMode.DENY,
        caller_token=CALLER,
        caller_agent_id=CALLER_AGENT,
        caller_required=True,
    )
    assert (grant.caller_token, grant.caller_token_unavailable) == (CALLER, False)


def test_work_for_a_bound_run_gets_its_caller_only_while_that_caller_is_active():
    live = CallerGrant(token=CALLER, agent_id=CALLER_AGENT)
    marker = live.marker()
    assert caller_grant_for_run(None) == NO_CALLER
    assert caller_grant_for_run(marker) == CallerGrant(agent_id=CALLER_AGENT, required=True)
    with bind_caller_grant(live):
        assert caller_grant_for_run(marker) is live
        assert caller_grant_for_run(None) is live
        # Another caller's token never stands in for the one the run recorded.
        assert caller_grant_for_run({"agent_id": RUN_AGENT}) == CallerGrant(agent_id=RUN_AGENT, required=True)
    assert caller_grant_for_run(marker).required


# ── BaseAgent (workflow agent steps, sales) ───────────────────────────────


async def test_base_agent_resolves_its_grant_with_the_bound_caller():
    from core.agents.base import BaseAgent
    from core.langgraph import tool_adapter

    agent = BaseAgent(agent_id=RUN_AGENT, tenant_id=TENANT, authorized_tools=["hubspot:get_contact"])
    agent.bind_caller(CallerGrant(token=CALLER, agent_id=CALLER_AGENT))
    client = _enforcer({RUN})  # the run agent's grant allows it; the caller's does not
    dispatched = AsyncMock(return_value={"id": "c-1"})
    with (
        patch("auth.run_grants.resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch("auth.run_grants._load_agent_grantex_config", AsyncMock(return_value={"grant_token": RUN})),
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client),
        patch.object(tool_adapter, "_execute_connector_tool", dispatched),
        patch.object(tool_adapter, "load_connector_config", AsyncMock(return_value={})),
    ):
        result = await agent._call_tool("hubspot", "get_contact", {})
    assert agent._run_grant is not None
    assert (agent._run_grant.token, agent._run_grant.caller_token) == (RUN, CALLER)
    dispatched.assert_not_awaited()
    assert "error" in result


def test_binding_a_caller_discards_a_grant_resolved_without_it():
    from core.agents.base import BaseAgent

    agent = BaseAgent(agent_id=RUN_AGENT, tenant_id=TENANT)
    agent._run_grant = MagicMock()
    agent.bind_caller(CallerGrant(token=CALLER, agent_id=CALLER_AGENT))
    assert agent._run_grant is None and agent.caller_grant.token == CALLER


# ── POST /agents/{id}/run ─────────────────────────────────────────────────


def _function_source(module: Any, name: str) -> str:
    return textwrap.dedent(inspect.getsource(getattr(module, name)))


def _calls(source: str, func_name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == func_name or getattr(node.func, "attr", None) == func_name)
    ]


def test_agent_run_route_resolves_its_grant_with_the_request_caller():
    from api.v1 import agents

    assert "request" in inspect.signature(agents.run_agent).parameters
    source = _function_source(agents, "run_agent")
    assert "run_caller = caller_grant_from_request(request)" in source
    [resolve] = _calls(source, "resolve_run_grant")
    spread = [kw.value for kw in resolve.keywords if kw.arg is None]
    assert [ast.unparse(value) for value in spread] == ["run_caller.resolve_kwargs()"]
    # An approval the run pauses on records the binding for the resume.
    assert "resume_spec[RESUME_SPEC_KEY][CALLER_GRANT_KEY] = caller_marker" in source


async def test_an_approved_resume_of_a_bound_agent_run_refuses_tool_calls():
    from core.approvals import agent_run_resume as ar

    claim = ar._Claim(
        agent_id=uuid.UUID(RUN_AGENT),
        thread_id=f"{TENANT}:thread",
        command={"action": "approve"},
        spec={"confidence_floor": 0.5, CALLER_GRANT_KEY: {"agent_id": CALLER_AGENT}},
    )
    resume = AsyncMock(return_value={"status": "completed"})
    with (
        patch.object(ar, "_claim", AsyncMock(return_value=claim)),
        patch.object(ar, "_record", AsyncMock()),
        patch.object(ar, "_delete_thread", AsyncMock(return_value=True)),
        patch("core.langgraph.runner.resume_agent", resume),
        patch("auth.run_grants.resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch("auth.run_grants._load_agent_grantex_config", AsyncMock(return_value={"grant_token": RUN})),
    ):
        await ar.resume_approved_agent_run(uuid.UUID(TENANT), uuid.uuid4())
    run_grant = resume.await_args.kwargs["run_grant"]
    assert (run_grant.token, run_grant.caller_agent_id, run_grant.caller_token_unavailable) == (
        RUN,
        CALLER_AGENT,
        True,
    )


async def test_an_approved_resume_of_an_unbound_agent_run_resolves_its_grant_as_before():
    from core.approvals import agent_run_resume as ar

    claim = ar._Claim(
        agent_id=uuid.UUID(RUN_AGENT),
        thread_id=f"{TENANT}:thread",
        command={"action": "approve"},
        spec={"confidence_floor": 0.5},
    )
    resume = AsyncMock(return_value={"status": "completed"})
    with (
        patch.object(ar, "_claim", AsyncMock(return_value=claim)),
        patch.object(ar, "_record", AsyncMock()),
        patch.object(ar, "_delete_thread", AsyncMock(return_value=True)),
        patch("core.langgraph.runner.resume_agent", resume),
    ):
        await ar.resume_approved_agent_run(uuid.UUID(TENANT), uuid.uuid4())
    assert "run_grant" not in resume.await_args.kwargs


# ── POST /workflows/{id}/run ──────────────────────────────────────────────


def _workflow_session(definition: dict[str, Any]) -> tuple[MagicMock, list[Any]]:
    wf = MagicMock(id=uuid.uuid4(), company_id=None, is_active=True, definition=definition)
    result = MagicMock()
    result.scalar_one_or_none.return_value = wf
    added: list[Any] = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.add = MagicMock(side_effect=added.append)
    return session, added


async def _run_workflow(request: Any) -> tuple[BackgroundTasks, list[Any]]:
    from api.v1 import workflows

    session, added = _workflow_session({"steps": [{"id": "s1", "type": "agent", "agent_type": "analyst"}]})
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    tasks = BackgroundTasks()
    with (
        patch.object(workflows, "get_tenant_session", return_value=ctx),
        patch("core.workflow_ab.pick_variant", AsyncMock(return_value=None)),
    ):
        await workflows.run_workflow(wf_id=uuid.uuid4(), background_tasks=tasks, request=request, tenant_id=TENANT)
    return tasks, added


async def test_workflow_run_started_with_a_grantex_token_records_the_binding_and_passes_the_token():
    tasks, added = await _run_workflow(_grantex_request())
    [run] = added
    assert run.context[CALLER_GRANT_KEY] == {"agent_id": CALLER_AGENT}
    assert CALLER not in str(run.context)  # the token is never persisted
    [task] = tasks.tasks
    assert task.func.__name__ == "_run_workflow_in_background"
    assert task.args[-1] == CallerGrant(token=CALLER, agent_id=CALLER_AGENT)


async def test_workflow_run_started_by_a_user_is_not_bound():
    tasks, added = await _run_workflow(_legacy_request())
    [run] = added
    assert CALLER_GRANT_KEY not in (run.context or {})
    assert tasks.tasks[0].args[-1] == NO_CALLER


async def test_workflow_background_execution_holds_the_caller_only_while_it_runs():
    from api.v1 import workflows

    caller = CallerGrant(token=CALLER, agent_id=CALLER_AGENT)
    seen: dict[str, Any] = {}

    async def _steps(tenant_id, run_id, definition, trigger_payload, bound):
        seen["active"] = caller_grant_for_run({"agent_id": CALLER_AGENT})
        seen["bound"] = bound

    with patch.object(workflows, "_execute_workflow_bg", _steps):
        await workflows._run_workflow_in_background(uuid.UUID(TENANT), uuid.uuid4(), {}, None, caller)
    assert seen == {"active": caller, "bound": caller}
    assert caller_grant_for_run(None) == NO_CALLER
    [start] = _calls(_function_source(workflows, "_execute_workflow_bg"), "start_run")
    assert {kw.arg: ast.unparse(kw.value) for kw in start.keywords}["caller_grant"] == "caller.marker()"


async def test_workflow_engine_state_records_the_binding_without_the_token():
    from workflows.engine import WorkflowEngine

    store = MagicMock()
    store.save = AsyncMock()
    engine = WorkflowEngine(store)
    await engine.start_run(
        {"steps": [{"id": "s1", "type": "agent", "agent_type": "analyst"}]},
        tenant_id=TENANT,
        caller_grant=CallerGrant(token=CALLER, agent_id=CALLER_AGENT).marker(),
    )
    state = store.save.await_args.args[0]
    assert state[CALLER_GRANT_KEY] == {"agent_id": CALLER_AGENT}
    assert CALLER not in str(state)


def test_sub_workflows_inherit_the_binding():
    from workflows import step_types

    source = _function_source(step_types, "_execute_sub_workflow")
    [start] = _calls(source, "start_run")
    assert {kw.arg: ast.unparse(kw.value) for kw in start.keywords}["caller_grant"] == "state.get(CALLER_GRANT_KEY)"


class _BindableAgent:
    def __init__(self) -> None:
        self.bound: CallerGrant | None = None

    def bind_caller(self, caller: CallerGrant) -> None:
        self.bound = caller

    async def execute(self, task: Any) -> Any:
        from core.schemas.messages import TaskResult

        return TaskResult(
            message_id="msg_placeholder",
            correlation_id=task.correlation_id,
            workflow_run_id=task.workflow_run_id,
            step_id=task.step_id,
            agent_id=task.target_agent.agent_id,
            status="completed",
            output={"ok": True},
            confidence=0.9,
        )


class _UnbindableAgent(_BindableAgent):
    bind_caller = None  # type: ignore[assignment]


async def _agent_step(monkeypatch, scope: dict[str, str], agent: Any, state_extra: dict[str, Any]) -> dict[str, Any]:
    from core.agents.registry import AgentRegistry
    from workflows import step_types

    async def _config(agent_id: str, tenant_id: str) -> dict[str, Any]:
        return {"id": agent_id, "tenant_id": tenant_id, "company_id": scope["company_id"], "agent_type": "analyst"}

    monkeypatch.setattr(step_types, "_llm_available_for_workflow", lambda: True)
    monkeypatch.setattr(step_types, "_fake_llm_allowed", lambda: False)
    monkeypatch.setattr(step_types, "_load_workflow_agent_config", _config)
    monkeypatch.setattr(AgentRegistry, "create_from_config", staticmethod(lambda config: agent))
    return await step_types.execute_step(
        {"id": "s1", "type": "agent", "agent_id": RUN_AGENT, "action": "process", "inputs": {}},
        {**scope, "id": "wfr_placeholder", "context": {}, **state_extra},
    )


async def test_workflow_agent_step_binds_the_active_caller(monkeypatch, workflow_company_scope):
    agent = _BindableAgent()
    caller = CallerGrant(token=CALLER, agent_id=CALLER_AGENT)
    with bind_caller_grant(caller):
        result = await _agent_step(monkeypatch, workflow_company_scope, agent, {CALLER_GRANT_KEY: caller.marker()})
    assert result["status"] == "completed" and agent.bound is caller


async def test_resumed_workflow_agent_step_is_bound_to_an_unavailable_caller(monkeypatch, workflow_company_scope):
    agent = _BindableAgent()
    await _agent_step(monkeypatch, workflow_company_scope, agent, {CALLER_GRANT_KEY: {"agent_id": CALLER_AGENT}})
    assert agent.bound == CallerGrant(agent_id=CALLER_AGENT, required=True)


async def test_workflow_agent_step_without_a_binding_binds_no_caller(monkeypatch, workflow_company_scope):
    agent = _BindableAgent()
    await _agent_step(monkeypatch, workflow_company_scope, agent, {})
    assert agent.bound == NO_CALLER


async def test_bound_workflow_agent_step_refuses_an_agent_that_cannot_take_the_binding(
    monkeypatch, workflow_company_scope
):
    result = await _agent_step(
        monkeypatch, workflow_company_scope, _UnbindableAgent(), {CALLER_GRANT_KEY: {"agent_id": CALLER_AGENT}}
    )
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    ("active", "allowed", "dispatched"),
    [
        (CallerGrant(token=CALLER, agent_id=CALLER_AGENT), {CALLER}, 1),
        (CallerGrant(token=CALLER, agent_id=CALLER_AGENT), set(), 0),
        (NO_CALLER, {CALLER}, 0),  # resumed without the token
    ],
)
async def test_workflow_connector_step_of_a_bound_run_checks_the_caller(active, allowed, dispatched):
    from workflows import step_types

    execute = AsyncMock(return_value={"id": "c-1"})
    with (
        bind_caller_grant(active),
        patch("auth.run_grants.resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.WARN)),
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=_enforcer(allowed)),
        patch.object(step_types, "_validated_workflow_company", AsyncMock(return_value=uuid.UUID(int=0xC0))),
        patch.object(step_types, "_load_workflow_connector_config", AsyncMock(return_value={})),
        patch("core.langgraph.tool_adapter._execute_connector_tool", execute),
    ):
        await step_types._execute_connector_tool_step(
            {"id": "s1", "type": "connector_tool", "connector": "hubspot", "tool": "get_contact"},
            {"tenant_id": TENANT, "id": "run-1", CALLER_GRANT_KEY: {"agent_id": CALLER_AGENT}},
        )
    assert execute.await_count == dispatched


# ── Sales pipeline ────────────────────────────────────────────────────────


async def test_sales_process_lead_passes_the_request_caller_to_the_agent_run():
    from api.v1 import sales

    run = AsyncMock(return_value={"status": "completed", "confidence": 0.9})
    with patch.object(sales, "_run_sales_agent_on_lead", run):
        await sales.process_lead_with_agent(
            request=_grantex_request(), payload={"lead_id": str(uuid.uuid4())}, tenant_id=TENANT
        )
    assert run.await_args.kwargs["caller"] == CallerGrant(token=CALLER, agent_id=CALLER_AGENT)


async def test_sales_agent_run_binds_the_caller_before_executing():
    from api.v1 import sales

    lead = MagicMock(id=uuid.uuid4())
    agent_row = MagicMock(
        id=uuid.UUID(RUN_AGENT),
        agent_type="sales_agent",
        authorized_tools=[],
        prompt_variables={},
        hitl_condition="",
        output_schema=None,
        system_prompt_text="",
    )
    results = [MagicMock(), MagicMock()]
    results[0].scalar_one_or_none.return_value = lead
    results[1].scalar_one_or_none.return_value = agent_row
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    order: list[str] = []
    agent = MagicMock()
    agent.bind_caller.side_effect = lambda caller: order.append(f"bind:{caller.agent_id}")

    def _execute(task: Any) -> Any:
        order.append("execute")
        raise RuntimeError("stop after the run starts")  # the route reports it; nothing more to fake

    agent.execute = AsyncMock(side_effect=_execute)
    caller = CallerGrant(token=CALLER, agent_id=CALLER_AGENT)
    with (
        patch.object(sales, "get_tenant_session", return_value=ctx),
        patch.object(sales, "_lead_to_dict", return_value={"id": str(lead.id)}),
        patch.object(sales.AgentRegistry, "create_from_config", return_value=agent),
    ):
        await sales._run_sales_agent_on_lead(tenant_id=TENANT, lead_id=str(lead.id), caller=caller)
    assert order[:2] == [f"bind:{CALLER_AGENT}", "execute"]
    agent.bind_caller.assert_called_once_with(caller)


@pytest.mark.parametrize("route", ["run_automated_followups", "seed_target_prospects", "import_leads_csv"])
def test_sales_routes_that_process_leads_pass_their_request_on(route):
    from api.v1 import sales

    assert "request" in inspect.signature(getattr(sales, route)).parameters
    [call] = _calls(_function_source(sales, route), "process_lead_with_agent")
    assert {kw.arg: ast.unparse(kw.value) for kw in call.keywords}["request"] == "request"


def test_sales_inbox_processing_binds_the_request_caller():
    from api.v1 import sales

    assert "request" in inspect.signature(sales.process_inbox).parameters
    [call] = _calls(_function_source(sales, "process_inbox"), "_run_sales_agent_on_lead")
    assert {kw.arg: ast.unparse(kw.value) for kw in call.keywords}["caller"] == "caller_grant_from_request(request)"
