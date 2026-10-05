# SPDX-License-Identifier: Apache-2.0
"""Structured-output enforcement: an agent that declares an output schema never returns a payload that fails it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from core.config import settings
from core.langgraph.agent_graph import build_agent_graph
from core.prompts import output_schema as osch
from core.test_doubles.scripted_model import final

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = {
    "type": "object",
    "required": ["status", "amount"],
    "properties": {
        "status": {"type": "string", "enum": ["approved", "declined"]},
        "amount": {"type": "number", "minimum": 0},
    },
    "additionalProperties": False,
}
VALID = {"status": "approved", "amount": 1250.0}


def _state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="decide the claim")],
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


def _graph(**declared: Any):
    return build_agent_graph(
        system_prompt="scripted",
        authorized_tools=[],
        confidence_floor=0.5,
        run_grant=NO_RUN_GRANT_FOR_TESTS,
        **declared,
    )


GOOD = {**VALID, "confidence": 0.95}
BAD = {"status": "maybe", "confidence": 0.95}
OPEN_SCHEMA = {**SCHEMA, "additionalProperties": True}


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "output_schema_enforced", True)


class TestInlineSchema:
    def test_a_self_contained_object_schema_is_accepted(self):
        assert osch.check_inline_schema(SCHEMA) is SCHEMA

    @pytest.mark.parametrize(
        ("schema", "message"),
        [
            (None, "non-empty JSON object"),
            ({}, "non-empty JSON object"),
            ([], "non-empty JSON object"),
            ({"type": "array"}, "must describe an object"),
            ({"type": "object", "properties": {"a": {"$ref": "https://example.test/schema.json"}}}, "must not use"),
            ({"type": "object", "$dynamicRef": "https://example.test/schema"}, r"must not use \$dynamicRef"),
            ({"type": "object", "items": [{"$recursiveRef": "#"}]}, r"must not use \$recursiveRef"),
            ({"type": "object", "properties": {"a": {"type": "nonsense"}}}, "not a valid JSON Schema"),
            ({"type": "object", "description": "x" * 40_000}, "larger than"),
        ],
    )
    def test_an_unusable_schema_is_refused(self, schema, message):
        with pytest.raises(osch.OutputSchemaError, match=message):
            osch.check_inline_schema(schema)


class TestErrors:
    def test_a_property_named_like_a_reference_is_only_a_property(self):
        named = {"type": "object", "properties": {"note": {"type": "string", "description": "see $ref below"}}}
        assert osch.check_inline_schema(named) is named

    def test_a_schema_the_validator_cannot_run_is_unusable_not_a_crash(self, monkeypatch):
        import jsonschema

        class _Broken:
            def __init__(self, *args, **kwargs):
                pass

            @staticmethod
            def check_schema(_schema):
                return None

            def iter_errors(self, _document):
                raise RuntimeError("reference cannot be resolved: https://example.test/secret")

        monkeypatch.setattr(jsonschema, "Draft202012Validator", _Broken)
        with pytest.raises(osch.OutputSchemaError, match="could not be applied: RuntimeError") as refused:
            osch.errors_for(None, SCHEMA, VALID)
        assert "example.test" not in str(refused.value)
        monkeypatch.setattr(settings, "output_schema_enforced", True)
        verdict = osch.check(None, SCHEMA, VALID, repairs=0)
        assert (verdict["action"], verdict["trigger"]) == ("escalate", "output_schema_unusable")

    def test_a_conforming_answer_has_no_errors(self):
        assert osch.errors_for(None, SCHEMA, VALID) == []

    def test_errors_name_where_and_what(self):
        errors = osch.errors_for(None, SCHEMA, {"status": "maybe", "extra": 1})
        assert any(error.startswith("$.status: ") for error in errors)
        assert any("'amount' is a required property" in error for error in errors)
        assert any("extra" in error for error in errors)

    def test_errors_are_bounded_in_number_and_length(self):
        wide = {"type": "object", "properties": {f"f{i}": {"type": "integer"} for i in range(40)}}
        errors = osch.errors_for(None, wide, {f"f{i}": "x" * 2000 for i in range(40)})
        assert len(errors) == osch.MAX_ERRORS and all(len(error) <= osch.MAX_ERROR_CHARS for error in errors)

    def test_a_registered_document_schema_is_used_by_name(self):
        errors = osch.errors_for("underwriting_memo", None, {"not": "a memo"})
        assert errors and all(error.startswith("$") for error in errors)

    def test_a_name_that_is_not_registered_cannot_be_used(self):
        with pytest.raises(osch.OutputSchemaError, match="cannot be used: unknown_schema"):
            osch.errors_for("Invoice", None, VALID)

    def test_the_agents_own_schema_wins_over_a_name(self):
        assert osch.errors_for("Invoice", SCHEMA, VALID) == []


class TestCheck:
    def test_off_by_default_everything_is_accepted(self):
        assert settings.output_schema_enforced is False
        assert osch.check(None, SCHEMA, {"wrong": True}, repairs=0) == {"action": "accept"}
        assert osch.check("Invoice", None, {"wrong": True}, repairs=0) == {"action": "accept"}

    def test_an_agent_with_no_declared_schema_is_not_affected(self, enforced):
        assert osch.declared(None, None) is False and osch.declared(" ", {}) is False
        assert osch.check(None, None, {"anything": 1}, repairs=0) == {"action": "accept"}

    def test_a_valid_answer_is_accepted(self, enforced):
        assert osch.check(None, SCHEMA, VALID, repairs=0) == {"action": "accept"}

    def test_an_invalid_answer_is_sent_back_with_what_is_wrong(self, enforced):
        verdict = osch.check(None, SCHEMA, {"status": "maybe"}, repairs=0)
        assert verdict["action"] == "repair" and verdict["errors"]
        assert "does not match the required output schema" in verdict["message"]
        assert "$.status" in verdict["message"] and "single JSON object" in verdict["message"]
        assert osch.check(None, SCHEMA, {"status": "maybe"}, repairs=osch.MAX_REPAIRS - 1)["action"] == "repair"

    def test_an_answer_still_invalid_after_the_repairs_is_escalated_not_returned(self, enforced):
        verdict = osch.check(None, SCHEMA, {"status": "maybe"}, repairs=osch.MAX_REPAIRS)
        assert verdict["action"] == "escalate" and verdict["trigger"] == "output_schema_invalid"
        assert verdict["errors"]

    def test_a_declared_schema_that_cannot_be_used_escalates(self, enforced):
        verdict = osch.check("Invoice", None, VALID, repairs=0)
        assert (verdict["action"], verdict["trigger"]) == ("escalate", "output_schema_unusable")
        broken = osch.check(None, {"type": "array"}, VALID, repairs=0)
        assert (broken["action"], broken["trigger"]) == ("escalate", "output_schema_unusable")

    def test_results_are_metered_without_a_tenant_or_agent_label(self, enforced):
        from observability.metrics import output_schema_checks_total

        assert tuple(output_schema_checks_total._labelnames) == ("result",)

        def count(result: str) -> float:
            return output_schema_checks_total.labels(result=result)._value.get()

        before = {name: count(name) for name in ("valid", "repaired", "retry", "escalated", "unusable")}
        osch.check(None, SCHEMA, VALID, repairs=0)
        osch.check(None, SCHEMA, VALID, repairs=1)
        osch.check(None, SCHEMA, {}, repairs=0)
        osch.check(None, SCHEMA, {}, repairs=osch.MAX_REPAIRS)
        osch.check("Invoice", None, VALID, repairs=0)
        assert {name: count(name) - before[name] for name in before} == {
            "valid": 1,
            "repaired": 1,
            "retry": 1,
            "escalated": 1,
            "unusable": 1,
        }


class TestGraph:
    """The agent graph: evaluate checks the answer, a repair loops to the model, an escalation goes to a human."""

    def _src(self) -> str:
        return (ROOT / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")

    def test_evaluate_checks_the_answer_before_it_completes(self):
        src = self._src()
        evaluate = src[src.index("async def evaluate(") : src.index("async def hitl_gate(")]
        check = evaluate.index(
            "verdict = check_output_schema(output_schema, output_schema_json, output, repairs=repairs)"
        )
        assert check < evaluate.rindex('"status": "completed",')
        # A run refused by grant enforcement ends as failed before any schema check.
        assert evaluate.index('grant_denial = state.get("grant_denial")') < check
        assert '"messages": [HumanMessage(content=verdict["message"])],' in evaluate
        assert '"output_repairs": repairs + 1,' in evaluate and '"output_repair": True,' in evaluate
        assert '"output_invalid": output_invalid,' in evaluate

    def test_a_repair_returns_to_the_model_and_an_invalid_answer_goes_to_a_human(self):
        src = self._src()
        route = src[src.index("def should_escalate(") : src.index("# --- Build the graph ---")]
        assert route.index('if state.get("grant_denial"):') < route.index('if state.get("output_repair"):')
        assert 'return "reason"' in route
        assert route.index('if state.get("output_invalid"):') < route.index("trigger = _check_hitl_trigger(")
        edges = src[src.index('"evaluate",\n        should_escalate,') :][:200]
        assert '"reason": "reason",' in edges and '"hitl_gate": "hitl_gate",' in edges
        gate = src[src.index("async def hitl_gate(") : src.index("def should_escalate(")]
        assert 'trigger = str(state.get("output_invalid") or "") or _check_hitl_trigger(' in gate

    async def test_a_valid_answer_completes_in_one_turn(self, enforced, scripted_model):
        model = scripted_model([final(GOOD)])
        result = await _graph(output_schema_json=OPEN_SCHEMA).compile().ainvoke(_state())
        assert result["status"] == "completed" and result["output"]["status"] == "approved"
        assert not result.get("hitl_trigger") and not result.get("output_repairs")
        assert len(model.calls) == 1

    async def test_an_invalid_answer_is_corrected_by_the_model_and_then_completes(self, enforced, scripted_model):
        seen: list[str] = []

        def corrected(messages):
            seen.append(messages[-1].content)
            return final(GOOD)

        scripted_model([final(BAD), corrected])
        result = await _graph(output_schema_json=OPEN_SCHEMA).compile().ainvoke(_state())
        assert result["status"] == "completed" and result["output"]["status"] == "approved"
        assert result["output_repairs"] == 1 and result["output_repair"] is False
        assert not result.get("hitl_trigger") and not result.get("output_invalid")
        # The model was told what was wrong, by path.
        assert "does not match the required output schema" in seen[0] and "$.status" in seen[0]
        assert any("sent back for correction (1 of 2)" in line for line in result["reasoning_trace"])
        assert result["output_errors"] == []

    async def test_a_tool_call_is_listed_once_however_many_corrections_follow(self, enforced, scripted_model):
        from unittest.mock import AsyncMock, patch

        from core.test_doubles.scripted_model import tool_call

        scripted_model(
            [
                tool_call("gmail__send_email", to="ap@example.com", subject="Reminder"),
                final(BAD),
                final(BAD),
                final(GOOD),
            ]
        )
        executed = AsyncMock(return_value={"id": "msg-1", "status": "sent"})
        with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
            graph = build_agent_graph(
                system_prompt="scripted",
                authorized_tools=["gmail:send_email"],
                connector_config={},
                connector_names=["gmail"],
                confidence_floor=0.5,
                run_grant=NO_RUN_GRANT_FOR_TESTS,
                output_schema_json=OPEN_SCHEMA,
            )
            result = await graph.compile().ainvoke(_state())
        assert result["status"] == "completed" and result["output_repairs"] == 2
        assert executed.await_count == 1
        assert [entry["tool"] for entry in result["tool_calls_log"]] == ["gmail__send_email"]

    async def test_an_answer_that_never_becomes_valid_goes_to_a_human(self, enforced, scripted_model):
        model = scripted_model([final(BAD)] * (osch.MAX_REPAIRS + 1))
        compiled = _graph(output_schema_json=OPEN_SCHEMA).compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "output-schema-escalation"}}
        paused = await compiled.ainvoke(_state(), config)
        # High confidence, no review condition: only the schema sent it to a human.
        payload = paused["__interrupt__"][0].value
        assert payload["type"] == "hitl_approval" and payload["trigger"] == "output_schema_invalid"
        # The reviewer is told what is wrong, not only that something is.
        assert any(error.startswith("$.status: ") for error in payload["output_schema_errors"])
        assert any("'amount' is a required property" in error for error in payload["output_schema_errors"])
        assert compiled.get_state(config).values["output_errors"] == payload["output_schema_errors"]
        assert len(model.calls) == osch.MAX_REPAIRS + 1
        rejected = await compiled.ainvoke(Command(resume={"action": "reject", "reason": "wrong shape"}), config)
        assert rejected["status"] == "failed" and "wrong shape" in rejected["error"]

    async def test_a_declared_name_that_is_not_registered_goes_to_a_human_without_a_retry(
        self, enforced, scripted_model
    ):
        model = scripted_model([final(GOOD)])
        compiled = _graph(output_schema="Invoice").compile(checkpointer=MemorySaver())
        paused = await compiled.ainvoke(_state(), {"configurable": {"thread_id": "output-schema-unusable"}})
        assert paused["__interrupt__"][0].value["trigger"] == "output_schema_unusable"
        assert len(model.calls) == 1

    async def test_off_an_invalid_answer_completes_as_before(self, scripted_model):
        model = scripted_model([final(BAD)])
        result = await _graph(output_schema_json=OPEN_SCHEMA, output_schema="Invoice").compile().ainvoke(_state())
        assert result["status"] == "completed" and result["output"]["status"] == "maybe"
        assert not result.get("hitl_trigger") and len(model.calls) == 1

    async def test_an_agent_with_no_schema_is_not_touched_when_on(self, enforced, scripted_model):
        scripted_model([final(BAD)])
        result = await _graph().compile().ainvoke(_state())
        assert result["status"] == "completed" and not result.get("hitl_trigger")


class TestPlumbing:
    def test_the_runner_and_the_api_pass_the_declared_schema_to_the_graph(self):
        runner = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        run = runner[runner.index("async def run_agent(") : runner.index("async def resume_agent(")]
        assert "output_schema: str | None = None," in run and "output_schema_json: dict[str, Any] | None = None," in run
        assert "output_schema=output_schema," in run and "output_schema_json=output_schema_json," in run
        for key in ('"output_repairs": 0,', '"output_repair": False,', '"output_invalid": "",'):
            assert key in run
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert 'output_schema=agent_config.get("output_schema"),' in api
        assert '(agent_config.get("config") or {}).get(prompt_output_schema.INLINE_KEY)' in api

    def test_the_reviewer_gets_the_errors_through_the_runner_and_the_approval(self):
        from core.langgraph import runner

        class _Restore:
            def restore_text(self, text):
                return text.replace("[P1]", "Asha")

        assert runner._output_schema_errors({"output_errors": ["$.name: '[P1]' is too long"]}, _Restore()) == [
            "$.name: 'Asha' is too long"
        ]
        assert runner._output_schema_errors({"output_errors": ["$.a: bad"]}, None) == ["$.a: bad"]
        assert runner._output_schema_errors({}, None) == [] and runner._output_schema_errors(None, None) == []
        src = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        assert '"output_schema_errors": _output_schema_errors(state_values, pseudonymiser),' in src
        assert '"output_schema_errors": _output_schema_errors(result, pseudonymiser),' in src
        assert '"output_errors": [],' in src
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert '{"output_schema_errors": lg_result["output_schema_errors"]}' in api

    def test_a_declared_schema_takes_no_deterministic_bypass_and_a_registered_name_is_locked_too(self):
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        guard = api.index("if prompt_output_schema.enabled() and prompt_output_schema.declared(")
        assert guard < api.index("# Path 1: deterministic-route bypass for TDS.")
        assert 'fixture = {**fixture, "deterministic_route": ""}' in api[guard : guard + 400]
        replace = api[api.index("async def replace_agent(") :] if "async def replace_agent(" in api else api
        lock = replace.index("and (body.output_schema or None) != (agent.output_schema or None)")
        assert lock < replace.index("agent.output_schema = body.output_schema")
        assert "and prompt_output_schema.enabled()" in replace[lock - 200 : lock]

    def test_the_schema_is_locked_on_an_active_agent_like_its_prompt(self):
        api = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        handler = api[api.index("async def set_agent_output_schema(") :][:2600]
        assert "prompt_output_schema.check_inline_schema(body.schema_)" in handler
        assert "require_agent_mutable(agent, _effective_caller(caller, user_domains))" in handler
        assert 'if agent.status == "active":' in handler and "locked on active agents" in handler
        assert ".with_for_update()" in handler


class TestEndpoint:
    """PUT /agents/{id}/output-schema, called directly with a scripted session."""

    def _call(self, monkeypatch, agent, schema, *, mutable=True):
        import asyncio
        import uuid
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        from fastapi import HTTPException

        from api.v1 import agents as agents_api
        from core.schemas.api import AgentOutputSchemaIn

        statements: list[str] = []

        class _Session:
            async def execute(self, statement):
                statements.append(str(statement))
                return SimpleNamespace(scalar_one_or_none=lambda: agent)

        @asynccontextmanager
        async def _session(_tenant):
            yield _Session()

        def _mutable(_agent, _caller):
            if not mutable:
                raise HTTPException(403, "Only a tenant admin or the agent's owner can change this agent")

        monkeypatch.setattr(agents_api, "get_tenant_session", _session)
        monkeypatch.setattr(agents_api, "require_agent_mutable", _mutable)
        result = asyncio.run(
            agents_api.set_agent_output_schema(
                uuid.uuid4(),
                AgentOutputSchemaIn(schema=schema),
                tenant_id=str(uuid.uuid4()),
                user_domains=None,
                caller=None,
            )
        )
        return result, statements

    def _agent(self, **over):
        from types import SimpleNamespace

        base = {"status": "shadow", "config": {"temperature": 0.1}}
        base.update(over)
        return SimpleNamespace(**base)

    def test_a_schema_is_stored_beside_the_rest_of_the_config_under_a_row_lock(self, monkeypatch):
        agent = self._agent()
        result, statements = self._call(monkeypatch, agent, SCHEMA)
        assert agent.config == {"temperature": 0.1, osch.INLINE_KEY: SCHEMA}
        assert result["output_schema"] == SCHEMA and result["enforced"] is False
        assert "FOR UPDATE" in statements[0] and "agents.tenant_id" in statements[0]

    def test_null_removes_it(self, monkeypatch):
        agent = self._agent(config={"temperature": 0.1, osch.INLINE_KEY: SCHEMA})
        result, _ = self._call(monkeypatch, agent, None)
        assert agent.config == {"temperature": 0.1} and result["output_schema"] is None

    @pytest.mark.parametrize(
        ("agent_over", "schema", "mutable", "status", "detail"),
        [
            ({}, {"type": "array"}, True, 422, "must describe an object"),
            ({"status": "active"}, SCHEMA, True, 409, "locked on active agents"),
            ({}, SCHEMA, False, 403, "owner"),
            (None, SCHEMA, True, 404, "Agent not found"),
        ],
    )
    def test_refusals(self, monkeypatch, agent_over, schema, mutable, status, detail):
        from fastapi import HTTPException

        agent = None if agent_over is None else self._agent(**agent_over)
        before = None if agent is None else dict(agent.config)
        with pytest.raises(HTTPException) as refused:
            self._call(monkeypatch, agent, schema, mutable=mutable)
        assert refused.value.status_code == status and detail in str(refused.value.detail)
        if agent is not None:
            assert agent.config == before
