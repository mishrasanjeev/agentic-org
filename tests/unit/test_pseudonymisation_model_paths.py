# SPDX-License-Identifier: Apache-2.0
"""Pre-model pseudonymisation on both model paths (PRD F-5, flag ``pseudonymisation.pre_model``).

Acceptance criteria covered here:

* no raw identifier from the fixture case reaches a model request, on the
  LangGraph path (captured by the scripted model) and the ``LLMRouter`` path
  (captured by the record/replay harness as a cassette), system prompt
  included;
* tool calls receive the restored values, and tool results are pseudonymised
  before the next model turn;
* a tool call whose pseudonym cannot be restored is refused, never sent;
* the map survives a pause: a resumed run restores from the stored map, and
  refuses to resume when the map is missing or cannot be read, or the
  checkpoint cannot be read;
* the map is keyed by the server's run id: a case id in the request never
  reaches another run's map, and tokens from another run are never restored;
* structured tool results on the router path are pseudonymised by field;
* a flag that cannot be read refuses the run instead of turning pseudonymisation off;
* with the flag off nothing changes.
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from core.pii.pseudonymiser import PseudonymMap
from core.test_doubles.pseudonym_store import InMemoryPseudonymMapStore
from core.test_doubles.scripted_model import final, tool_call
from tests import pseudonymisation_case as case

_TOKEN = r"\[\[{entity}_\d+:[0-9a-f]{{6}}\]\]"


def _token(text: str, entity: str) -> str:
    match = re.search(_TOKEN.format(entity=entity), text)
    assert match, f"no {entity} pseudonym in {text[:200]!r}"
    return match[0]


def _request_text(messages: list[BaseMessage]) -> str:
    return json.dumps(
        [{"content": m.content, "tool_calls": getattr(m, "tool_calls", None)} for m in messages], default=str
    )


def _assert_no_raw_values(text: str) -> None:
    leaked = [raw for raw in case.RAW_VALUES if raw in text]
    assert not leaked, f"raw values reached the model: {leaked}"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPseudonymMapStore:
    """Turn the flag on for the fixture tenant and keep maps in memory."""
    from core.pii import pseudonymiser

    shared = InMemoryPseudonymMapStore()

    async def enabled(tenant_id: Any) -> bool:
        return str(tenant_id) == case.TENANT_ID

    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", enabled)
    monkeypatch.setattr(pseudonymiser, "DatabasePseudonymMapStore", lambda: shared)
    return shared


@pytest.fixture
def explanations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, Any]]:
    """Keep the runner offline and record what the explanation model would be sent."""
    from core.langgraph import runner

    seen: list[tuple[Any, Any]] = []

    async def fake_explanation(trace: Any, output: Any, tools: Any) -> dict[str, Any]:
        seen.append((trace, output))
        return {"bullets": [f"Summary: {json.dumps(output)}"]}

    def no_database(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("no database in unit tests")

    monkeypatch.setattr("core.billing.metering.gate_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr("core.billing.metering.meter_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "prefetch_llm_credential", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "generate_explanation", fake_explanation)
    monkeypatch.setattr("core.database.get_tenant_session", no_database)
    return seen


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture audit entries instead of writing them to a database."""
    entries: list[dict[str, Any]] = []

    async def log(self: Any, **kwargs: Any) -> None:
        entries.append(kwargs)

    monkeypatch.setattr("core.tool_gateway.audit_logger.AuditLogger.log", log)
    return entries


@pytest.fixture
def connector_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_execute(connector: str, tool: str, params: dict[str, Any], config: Any, **_: Any) -> dict[str, Any]:
        calls.append({"connector": connector, "tool": tool, "params": params})
        # The provider echoes personal data back, as real ones do.
        return {"status": "sent", "to": params.get("to"), "account": case.IBAN}

    monkeypatch.setattr("core.langgraph.tool_adapter._execute_connector_tool", fake_execute)
    return calls


async def _run(**overrides: Any) -> dict[str, Any]:
    from core.langgraph.runner import run_agent

    arguments: dict[str, Any] = {
        "agent_id": "agent-f5",
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": case.TENANT_ID,
        "system_prompt": case.system_prompt(),
        "authorized_tools": ["gmail:send_email"],
        "task_input": case.task_input(),
        "confidence_floor": 0.5,
        "connector_config": {},
        "connector_names": ["gmail"],
    }
    arguments.update(overrides)
    return await run_agent(**arguments)


# ── LangGraph path ──────────────────────────────────────────────────────────


async def test_langgraph_no_raw_identifier_reaches_the_model_and_tools_receive_restored_values(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
    connector_calls: list[dict[str, Any]],
) -> None:
    def send(messages: list[BaseMessage]) -> Any:
        human = next(m for m in messages if isinstance(m, HumanMessage)).content
        return tool_call(
            "gmail__send_email",
            to=_token(human, "EMAIL_ADDRESS"),
            subject="Case update",
            body=f"SSN {_token(human, 'US_SSN')} checked for {_token(human, 'PERSON')}",
        )

    def finish(messages: list[BaseMessage]) -> Any:
        human = next(m for m in messages if isinstance(m, HumanMessage)).content
        return final(
            {"status": "completed", "confidence": 0.95, "summary": f"Emailed {_token(human, 'EMAIL_ADDRESS')}"}
        )

    model = scripted_model([send, finish])
    result = await _run()

    assert result["status"] == "completed", result
    # 1. Nothing raw in any rendered request, the system prompt included.
    assert len(model.calls) == 2
    for request in model.calls:
        _assert_no_raw_values(_request_text(request))
    system = model.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert "<pseudonymised_data>" in system.content
    assert _token(system.content, "EMAIL_ADDRESS")
    # 2. The tool received the true values.
    assert len(connector_calls) == 1
    sent = connector_calls[0]
    assert (sent["connector"], sent["tool"]) == ("gmail", "send_email")
    assert sent["params"]["to"] == case.EMAIL
    assert sent["params"]["subject"] == "Case update"
    assert sent["params"]["body"] == f"SSN {case.SSN} checked for {case.APPLICANT_NAME}"
    # 3. Its result was pseudonymised before the next model turn.
    tool_message = next(m for m in model.calls[1] if isinstance(m, ToolMessage))
    assert _token(str(tool_message.content), "IBAN_CODE")
    # 4. People get the real values back; the explanation model does not.
    assert result["output"]["summary"] == f"Emailed {case.EMAIL}"
    assert explanations and all(case.EMAIL not in json.dumps(seen) for seen in explanations)
    assert case.EMAIL in json.dumps(result["explanation"])
    # 5. The map is stored under the server's thread id, not the request's case id.
    [(tenant, key)] = store.rows
    assert tenant == case.TENANT_ID
    assert key.startswith(f"tenant:{case.TENANT_ID}:") and key != case.CASE_ID


async def test_langgraph_tool_call_with_an_unrestorable_pseudonym_is_refused_audited_and_not_sent(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
    connector_calls: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> None:
    model = scripted_model(
        [
            tool_call("gmail__send_email", to="[[EMAIL_ADDRESS_1:ffffff]]", subject="x", body="forged"),
            final({"status": "completed", "confidence": 0.9}),
        ]
    )
    result = await _run()

    assert connector_calls == []
    tool_message = next(m for m in model.calls[1] if isinstance(m, ToolMessage))
    assert "E1012" in str(tool_message.content)
    assert "pseudonym_restore_failed: unknown_pseudonym" in str(tool_message.content)
    assert result["tool_calls_log"][0]["status"] == "error"
    assert [(a["action"], a["outcome"], a["details"]["reason"]) for a in audits] == [
        ("pseudonym_restore_failed", "blocked", "unknown_pseudonym")
    ]


async def test_langgraph_map_survives_a_pause_and_resume_restores_from_the_stored_map(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
) -> None:
    from core.langgraph.runner import resume_agent

    def decide(messages: list[BaseMessage]) -> Any:
        human = next(m for m in messages if isinstance(m, HumanMessage)).content
        return final({"status": "completed", "confidence": 0.95, "total": 750000, "applicant": _token(human, "PERSON")})

    scripted_model([decide])
    paused = await _run(authorized_tools=[], hitl_condition="total > 500000")
    assert paused["status"] == "hitl_triggered", paused
    assert paused["output"]["applicant"] == case.APPLICANT_NAME

    resumed = await resume_agent(
        agent_id="agent-f5",
        thread_id=paused["thread_id"],
        decision={"action": "approve"},
        system_prompt=case.system_prompt(),
        authorized_tools=[],
        confidence_floor=0.5,
        hitl_condition="total > 500000",
        tenant_id=case.TENANT_ID,
    )
    assert resumed["status"] == "completed", resumed
    assert resumed["output"]["applicant"] == case.APPLICANT_NAME


async def test_langgraph_resume_is_refused_when_the_stored_map_cannot_be_read(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
) -> None:
    from core.langgraph.runner import resume_agent

    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    paused = await _run(authorized_tools=[], hitl_condition="total > 500000")
    store.fail_next = "map_unreadable"

    resumed = await resume_agent(
        agent_id="agent-f5",
        thread_id=paused["thread_id"],
        decision={"action": "approve"},
        system_prompt=case.system_prompt(),
        authorized_tools=[],
        hitl_condition="total > 500000",
        tenant_id=case.TENANT_ID,
    )
    assert resumed == {
        "status": "failed",
        "error": "pseudonymisation_unavailable: map_unreadable",
        "reason": "pseudonymisation_unavailable",
    }


async def test_langgraph_run_is_refused_before_any_model_call_when_the_map_store_fails(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
) -> None:
    model = scripted_model([])
    store.fail_next = "map_store_unavailable"
    result = await _run()
    assert result["status"] == "failed"
    assert result["error"] == "pseudonymisation_unavailable: map_store_unavailable"
    assert model.calls == []


async def test_langgraph_flag_off_leaves_the_existing_redaction_path_unchanged(
    scripted_model: Any,
    monkeypatch: pytest.MonkeyPatch,
    explanations: list[tuple[Any, Any]],
) -> None:
    from core.pii import pseudonymiser

    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(pseudonymiser, "DatabasePseudonymMapStore", lambda: pytest.fail("store must not be used"))
    model = scripted_model([final({"status": "completed", "confidence": 0.95})])
    result = await _run(authorized_tools=[])

    assert result["status"] == "completed"
    system = model.calls[0][0]
    assert system.content.startswith(case.system_prompt())
    assert "<pseudonymised_data>" not in system.content
    assert "[[" not in _request_text(model.calls[0])


# ── LLMRouter path (BaseAgent), captured by the record/replay harness ───────


async def test_router_no_raw_identifier_reaches_the_recorded_request_and_tools_receive_restored_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    store: InMemoryPseudonymMapStore,
    connector_calls: list[dict[str, Any]],
) -> None:
    from core.agents.base import BaseAgent
    from core.llm.router import LLMResponse, LLMRouter
    from core.model_replay import cassette_scope
    from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    monkeypatch.setattr("core.langgraph.tool_adapter.load_connector_config", AsyncMock(return_value={}))

    async def provider(self: Any, model: str, messages: list[dict[str, Any]], *args: Any) -> LLMResponse:
        task = messages[1]["content"]
        email, ssn = _token(task, "EMAIL_ADDRESS"), _token(task, "US_SSN")
        if len(messages) == 2:
            call = {"connector": "gmail", "tool": "send_email", "params": {"to": email, "body": f"SSN {ssn} checked"}}
            return LLMResponse(content=json.dumps({"status": "in_progress", "tool_calls": [call]}), model=model)
        return LLMResponse(
            content=json.dumps({"status": "completed", "confidence": 0.95, "summary": f"Emailed {email}"}), model=model
        )

    monkeypatch.setattr(LLMRouter, "_call_provider", provider)

    agent = BaseAgent(
        agent_id="agent-f5",
        tenant_id=case.TENANT_ID,
        authorized_tools=["gmail:send_email"],
        llm_model="gemini-2.5-flash",
    )
    agent._system_prompt = case.system_prompt()
    task = TaskAssignment(
        message_id="msg-f5",
        correlation_id="corr-f5",
        workflow_run_id="wfr-f5",
        workflow_definition_id="wfd-f5",
        step_id="screen",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(agent_id="agent-f5", agent_type="analyst", agent_token="placeholder"),
        task=TaskInput(**case.task_input()),
    )

    with cassette_scope(tmp_path):
        result = await agent.execute(task)

    assert result.status == "completed", result.error
    cassettes = sorted(tmp_path.glob("*.json"))
    assert len(cassettes) == 2
    for cassette in cassettes:
        recorded = json.loads(cassette.read_text(encoding="utf-8"))
        _assert_no_raw_values(json.dumps(recorded["request"]))
        assert "<pseudonymised_data>" in recorded["request"]["messages"][0]["content"]
    assert connector_calls[0]["params"] == {"to": case.EMAIL, "body": f"SSN {case.SSN} checked"}
    assert result.output["summary"] == f"Emailed {case.EMAIL}"
    assert list(store.rows) == [(case.TENANT_ID, "wfr-f5")]  # the workflow run, not the task's case_id


async def test_tool_gateway_restores_arguments_and_refuses_unrestorable_ones(
    store: InMemoryPseudonymMapStore,
) -> None:
    from core.pii.pseudonymiser import open_session
    from core.tool_gateway.gateway import ToolGateway

    session = await open_session(case.TENANT_ID, case.CASE_ID)
    token = (await session.pseudonymise_value({"email": case.EMAIL}))["email"]
    connector = AsyncMock()
    connector.execute_tool = AsyncMock(return_value={"status": "ok"})
    audit = AsyncMock()
    gateway = ToolGateway(audit_logger=audit)
    gateway.register_connector("gmail", connector, tenant_id=case.TENANT_ID)
    arguments = {
        "tenant_id": case.TENANT_ID,
        "agent_id": "agent-f5",
        "agent_scopes": ["tool:gmail:write:email"],
        "connector_name": "gmail",
        "tool_name": "send_email",
        "pseudonymiser": session,
        "run_grant": NO_RUN_GRANT_FOR_TESTS,
    }

    refused = await gateway.execute(**arguments, params={"to": "[[EMAIL_ADDRESS_2:ffffff]]"})
    assert refused == {"error": {"code": "E1012", "message": "pseudonym_restore_failed: unknown_pseudonym"}}
    connector.execute_tool.assert_not_called()
    assert audit.log.await_args.kwargs["action"] == "pseudonym_restore_failed"

    await gateway.execute(**arguments, params={"to": token})
    connector.execute_tool.assert_awaited_once_with("send_email", {"to": case.EMAIL})


async def test_graph_pseudonymises_every_message_immediately_before_the_model_call(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
) -> None:
    """Whatever put raw text into the state (a resumed checkpoint, another node), the request is masked."""
    from core.langgraph.agent_graph import build_agent_graph
    from core.pii.pseudonymiser import open_session

    session = await open_session(case.TENANT_ID, case.CASE_ID)
    model = scripted_model([final({"status": "completed", "confidence": 0.95})])
    graph = build_agent_graph(
        system_prompt=case.system_prompt(),
        authorized_tools=[],
        confidence_floor=0.5,
        pseudonymiser=session,
        run_grant=NO_RUN_GRANT_FOR_TESTS,
    )
    state = {
        "messages": [
            SystemMessage(content=case.system_prompt()),
            HumanMessage(content=case.task_input()["inputs"]["notes"]),
        ],
        "agent_id": "agent-f5",
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": case.TENANT_ID,
        "grant_token": "",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }
    await session.register_structured(case.task_input())
    await graph.compile().ainvoke(state)

    _assert_no_raw_values(_request_text(model.calls[0]))


async def test_agent_tool_dispatch_without_a_gateway_refuses_unrestorable_arguments(
    store: InMemoryPseudonymMapStore,
    connector_calls: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> None:
    from core.langgraph.tool_adapter import execute_agent_tool
    from core.pii.pseudonymiser import open_session

    session = await open_session(case.TENANT_ID, case.CASE_ID)
    result = await execute_agent_tool(
        "gmail",
        "send_email",
        {"to": "[[EMAIL_ADDRESS_1:ffffff]]"},
        tenant_id=case.TENANT_ID,
        company_id=None,
        domain=None,
        authorized_tools=["gmail:send_email"],
        pseudonymiser=session,
        run_grant=NO_RUN_GRANT_FOR_TESTS,
    )
    assert result == {"error": {"code": "E1012", "message": "pseudonym_restore_failed: unknown_pseudonym"}}
    assert connector_calls == []
    assert [a["action"] for a in audits] == ["pseudonym_restore_failed"]


# ── Review findings: case binding, structured results, flag failures, resume ─


async def test_a_case_id_in_the_request_never_reaches_another_runs_map(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
    connector_calls: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> None:
    """A second caller names the first run's case and replays its tokens; nothing is restored or written."""

    def victim_decides(messages: list[BaseMessage]) -> Any:
        human = next(m for m in messages if isinstance(m, HumanMessage)).content
        return final({"status": "completed", "confidence": 0.95, "ssn": _token(human, "US_SSN")})

    scripted_model([victim_decides])
    victim = await _run(authorized_tools=[])
    assert victim["output"]["ssn"] == case.SSN
    [(tenant, victim_key)] = store.rows
    victim_row = store.rows[(tenant, victim_key)]
    tag = PseudonymMap.from_json(victim_row).tag
    forged = f"[[US_SSN_1:{tag}]] [[PERSON_1:{tag}]]"

    attacker_task = {
        "action": "screen_applicant",
        "case_id": victim_key,
        "inputs": {"case_id": victim_key, "notes": "reach me at probe@example.com"},
        "context": {"case_id": victim_key},
    }
    attacker_model = scripted_model(
        [
            tool_call("gmail__send_email", to=f"[[US_SSN_1:{tag}]]", subject="x", body="y"),
            final({"status": "completed", "confidence": 0.95, "leak": forged}),
        ]
    )
    attacker = await _run(task_input=attacker_task)

    assert attacker["output"]["leak"] == forged  # never restored
    assert case.SSN not in json.dumps(attacker, default=str)
    assert case.APPLICANT_NAME not in json.dumps(attacker, default=str)
    assert connector_calls == []  # the replayed token was refused, not dispatched
    assert audits and audits[0]["details"]["reason"] == "unknown_pseudonym"
    assert store.rows[(tenant, victim_key)] == victim_row  # nothing written into the first run's map
    assert len(store.rows) == 2
    assert tag not in _request_text(attacker_model.calls[0]).replace(forged, "")


async def test_router_synthesis_pseudonymises_structured_tool_results_by_field(
    monkeypatch: pytest.MonkeyPatch,
    store: InMemoryPseudonymMapStore,
) -> None:
    from core.agents.base import BaseAgent
    from core.llm.router import LLMResponse, LLMRouter
    from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

    record = {"full_name": "Zelda Nobodyson", "address": "2 Sample Road, Nowhereville", "dob": "1900-02-02"}

    async def fake_execute(connector: str, tool: str, params: dict[str, Any], config: Any, **_: Any) -> dict[str, Any]:
        return {"status": "ok", "record": record}

    monkeypatch.setattr("core.langgraph.tool_adapter._execute_connector_tool", fake_execute)
    monkeypatch.setattr("core.langgraph.tool_adapter.load_connector_config", AsyncMock(return_value={}))
    sent: list[str] = []

    async def provider(self: Any, model: str, messages: list[dict[str, Any]], *args: Any) -> LLMResponse:
        sent.append(json.dumps(messages))
        if len(messages) == 2:
            call = {"connector": "gmail", "tool": "send_email", "params": {"to": "ops"}}
            return LLMResponse(content=json.dumps({"status": "in_progress", "tool_calls": [call]}), model=model)
        name = _token(messages[-1]["content"], "PERSON")
        return LLMResponse(content=json.dumps({"status": "completed", "confidence": 0.95, "who": name}), model=model)

    monkeypatch.setattr(LLMRouter, "_call_provider", provider)
    monkeypatch.setattr("core.test_doubles.fake_llm.is_active", lambda: False)  # reach the provider double
    agent = BaseAgent(
        agent_id="agent-f5",
        tenant_id=case.TENANT_ID,
        authorized_tools=["gmail:send_email"],
        llm_model="gemini-2.5-flash",
    )
    agent._system_prompt = "You screen applicants."
    task = TaskAssignment(
        message_id="msg-f5",
        correlation_id="corr-f5",
        workflow_run_id="wfr-f5-structured",
        workflow_definition_id="wfd-f5",
        step_id="screen",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(agent_id="agent-f5", agent_type="analyst", agent_token="placeholder"),
        task=TaskInput(**case.task_input()),
    )

    result = await agent.execute(task)

    assert result.status == "completed", (result.error, result.reasoning_trace, result.output)
    assert len(sent) == 2
    for request in sent:
        leaked = [raw for raw in (*record.values(), *case.RAW_VALUES) if raw in request]
        assert not leaked, leaked
    assert result.output["who"] == record["full_name"]
    assert result.output["tool_results"][0]["result"]["record"] == record


@pytest.mark.real_flag_lookup
async def test_langgraph_run_is_refused_when_the_flag_cannot_be_read(
    scripted_model: Any,
    monkeypatch: pytest.MonkeyPatch,
    explanations: list[tuple[Any, Any]],
) -> None:
    from core import feature_flags

    def database_down(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database unavailable")

    feature_flags.clear_cache()
    monkeypatch.setattr(feature_flags, "get_tenant_session", database_down)
    model = scripted_model([])

    result = await _run()

    assert result["status"] == "failed"
    assert result["error"] == "pseudonymisation_unavailable: flag_lookup_failed"
    assert model.calls == []
    feature_flags.clear_cache()


@pytest.mark.real_flag_lookup
async def test_router_agent_is_refused_when_the_flag_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import feature_flags
    from core.agents.base import BaseAgent
    from core.llm.router import LLMRouter
    from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

    def database_down(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database unavailable")

    feature_flags.clear_cache()
    monkeypatch.setattr(feature_flags, "get_tenant_session", database_down)
    provider = AsyncMock()
    monkeypatch.setattr(LLMRouter, "_call_provider", provider)
    monkeypatch.setattr("core.test_doubles.fake_llm.is_active", lambda: False)
    agent = BaseAgent(agent_id="agent-f5", tenant_id=case.TENANT_ID, llm_model="gemini-2.5-flash")
    task = TaskAssignment(
        message_id="msg-f5",
        correlation_id="corr-f5",
        workflow_run_id="wfr-f5-flag",
        workflow_definition_id="wfd-f5",
        step_id="screen",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(agent_id="agent-f5", agent_type="analyst", agent_token="placeholder"),
        task=TaskInput(**case.task_input()),
    )

    result = await agent.execute(task)

    assert result.status == "failed"
    assert "flag_lookup_failed" in str(result.error)
    provider.assert_not_called()
    feature_flags.clear_cache()


async def test_langgraph_resume_is_refused_when_the_map_row_is_missing(
    scripted_model: Any,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
) -> None:
    from core.langgraph.runner import resume_agent

    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    paused = await _run(authorized_tools=[], hitl_condition="total > 500000")
    store.rows.clear()

    resumed = await resume_agent(
        agent_id="agent-f5",
        thread_id=paused["thread_id"],
        decision={"action": "approve"},
        system_prompt=case.system_prompt(),
        authorized_tools=[],
        hitl_condition="total > 500000",
        tenant_id=case.TENANT_ID,
    )
    assert resumed == {
        "status": "failed",
        "error": "pseudonymisation_unavailable: map_missing",
        "reason": "pseudonymisation_unavailable",
    }


async def test_langgraph_resume_returns_failed_when_the_checkpoint_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    store: InMemoryPseudonymMapStore,
    explanations: list[tuple[Any, Any]],
) -> None:
    from core.langgraph import runner

    class UnreadableCheckpoints:
        async def aget_tuple(self, config: Any) -> Any:
            raise RuntimeError("checkpoint store unavailable")

    async def checkpointer() -> Any:
        return UnreadableCheckpoints()

    monkeypatch.setattr(runner, "get_checkpointer", checkpointer)
    resumed = await runner.resume_agent(
        agent_id="agent-f5",
        thread_id=f"tenant:{case.TENANT_ID}:run:0a1b2c3d",
        decision={"action": "approve"},
        system_prompt=case.system_prompt(),
        authorized_tools=[],
        tenant_id=case.TENANT_ID,
    )
    assert resumed == {"status": "failed", "error": "checkpoint store unavailable", "reason": "resume_failed"}
