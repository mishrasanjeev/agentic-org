# SPDX-License-Identifier: Apache-2.0
"""The workflow graph: drawn from a definition, validated in plain words, with fallback as a failure directive."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.v1 import workflows as api
from core.config import settings
from core.workflows import graph
from workflows.engine import WorkflowEngine

ROOT = Path(__file__).resolve().parents[2]


def _definition():
    return {
        "steps": [
            {
                "id": "extract",
                "type": "agent",
                "agent_type": "ap_processor",
                "action": "extract",
                "on_failure": "retry(2) then continue",
            },
            {
                "id": "check",
                "type": "condition",
                "depends_on": ["extract"],
                "expression": "extract.total > 1000",
                "true_path": "approve",
                "false_path": "post",
            },
            {
                "id": "approve",
                "type": "human_in_loop",
                "depends_on": ["check"],
                "assignee_role": "finance_lead",
                "decision_options": ["approve", "reject"],
            },
            {
                "id": "post",
                "type": "agent",
                "agent_type": "ap_processor",
                "action": "post",
                "depends_on": ["check"],
                "on_failure": "fallback(manual)",
            },
            {
                "id": "manual",
                "type": "human_in_loop",
                "assignee": "ops",
                "decision_options": ["done"],
                "depends_on": ["post"],
            },
        ]
    }


class TestGraph:
    def test_the_graph_has_one_node_per_step_and_one_edge_per_dependency_path_and_fallback(self):
        drawn = graph.to_graph(_definition())
        assert [n["id"] for n in drawn["nodes"]] == ["extract", "check", "approve", "post", "manual"]
        assert (
            drawn["nodes"][1]["summary"] == "extract.total > 1000"
            and drawn["nodes"][2]["summary"] == "finance_lead decides: approve, reject"
        )
        assert (
            drawn["nodes"][0]["on_failure"] == "retry(2) then continue"
            and drawn["nodes"][0]["summary"] == "ap_processor: extract"
        )
        kinds = [(e["source"], e["target"], e["kind"]) for e in drawn["edges"]]
        assert ("extract", "check", "then") in kinds and ("check", "approve", "true") in kinds
        assert ("check", "post", "false") in kinds and ("post", "manual", "fallback") in kinds
        assert next(e for e in drawn["edges"] if e["kind"] == "fallback")["label"] == "on failure"

    def test_rules_paths_are_labelled_and_odd_definitions_draw_nothing(self):
        rules = {
            "steps": [
                {"id": "score", "type": "condition", "rules": [{"expression": "x > 1", "label": "High", "path": "a"}]},
                {"id": "a", "agent_type": "x"},
            ]
        }
        drawn = graph.to_graph(rules)
        assert drawn["edges"] == [{"source": "score", "target": "a", "kind": "rule", "label": "High"}]
        assert graph.to_graph(None) == {"nodes": [], "edges": []} and graph.to_graph({"steps": ["x"]})["nodes"] == []
        numbered = graph.to_graph({"steps": [{"step": 1, "agent_type": "a"}, {"name": "second", "agent_type": "b"}]})
        assert [n["id"] for n in numbered["nodes"]] == ["1", "step_2"]

    def test_a_sound_definition_has_no_problems(self):
        assert graph.validate(_definition()) == []
        assert settings.workflow_builder_v2_enabled is False and graph.enabled() is False

    @pytest.mark.parametrize(
        ("steps", "needle"),
        [
            ([], "at least one step"),
            ([{"id": "a", "agent_type": "x"}, {"id": "a", "agent_type": "y"}], "used more than once"),
            ([{"id": "a", "type": "magic"}], "unknown type"),
            ([{"id": "a", "agent_type": "x", "depends_on": ["zz"]}], "which is not a step"),
            ([{"id": "a", "agent_type": "x", "depends_on": ["a"]}], "depends on itself"),
            ([{"id": "c", "type": "condition", "expression": "x"}], "needs a true_path and false_path"),
            (
                [{"id": "c", "type": "condition", "true_path": "a", "false_path": "a"}, {"id": "a", "agent_type": "x"}],
                "needs an expression",
            ),
            (
                [{"id": "c", "type": "condition", "expression": "x", "true_path": "nowhere", "false_path": "nowhere"}],
                "is not a step",
            ),
            ([{"id": "h", "type": "human_in_loop", "assignee": "ops"}], "needs decision_options"),
            ([{"id": "h", "type": "human_in_loop", "decision_options": ["ok"]}], "names who decides"),
            ([{"id": "a", "type": "agent"}], "names its agent_type"),
            ([{"id": "a", "agent_type": "x", "on_failure": "explode"}], "on_failure is halt"),
            ([{"id": "a", "agent_type": "x", "on_failure": "fallback(zz)"}], "fallback 'zz' is not a step"),
            ([{"id": "a", "agent_type": "x", "on_failure": "fallback(a)"}], "its own fallback"),
            (
                [
                    {"id": "a", "agent_type": "x", "depends_on": ["b"]},
                    {"id": "b", "agent_type": "y", "depends_on": ["a"]},
                ],
                "form a cycle",
            ),
        ],
    )
    def test_each_problem_is_named(self, steps, needle):
        problems = graph.validate({"steps": steps})
        assert any(needle in p for p in problems), problems

    def test_the_failure_grammar_accepts_each_directive(self):
        for directive in (
            "halt",
            "continue",
            "retry(3)",
            "retry(3) then continue",
            "retry(2), ignore",
            "fallback(manual)",
        ):
            steps = [{"id": "a", "agent_type": "x", "on_failure": directive}, {"id": "manual", "agent_type": "y"}]
            assert graph.validate({"steps": steps}) == [], directive
        assert graph.validate("nope") == ["the definition must hold a list of steps"]
        assert graph.validate({"steps": [{"id": str(i), "agent_type": "x"} for i in range(graph.MAX_STEPS + 1)]}) == [
            f"a workflow has at most {graph.MAX_STEPS} steps"
        ]


class TestEngineFallback:
    def test_a_fallback_directive_is_parsed_and_allows_the_failure(self):
        assert WorkflowEngine._fallback_target({"on_failure": "fallback(manual)"}) == "manual"
        assert WorkflowEngine._fallback_target({"on_failure": "retry(2) then fallback( manual )"}) == "manual"
        assert WorkflowEngine._fallback_target({"on_failure": "halt"}) is None
        assert WorkflowEngine._step_allows_failure({"on_failure": "fallback(manual)"}) is True
        assert WorkflowEngine._step_allows_failure({"on_failure": "retry(2)"}) is False

    def test_the_fallback_runs_only_when_its_dependency_failed(self):
        fallback = {"id": "manual", "depends_on": ["post"]}
        failed = {"step_results": {"post": {"status": "failed", "fallback_target": "manual"}}}
        assert WorkflowEngine._check_dependencies(fallback, failed) is None
        succeeded = {"step_results": {"post": {"status": "completed", "fallback_target": "manual"}}}
        assert WorkflowEngine._check_dependencies(fallback, succeeded) == "fallback_not_needed"
        other = {"id": "notify", "depends_on": ["post"]}
        assert WorkflowEngine._check_dependencies(other, failed).startswith("Dependency 'post' did not complete")
        assert WorkflowEngine._check_dependencies(other, succeeded) is None

    def test_the_engine_records_the_fallback_target_on_the_step_result(self):
        src = (ROOT / "workflows" / "engine.py").read_text(encoding="utf-8")
        assert src.count('state["step_results"][step_id]["fallback_target"] = fallback_target') == 2


class TestRuntimeCompatibleValidation:
    @pytest.mark.parametrize(
        ("steps", "needle"),
        [
            ([{"agent_type": "x"}], "step 1 needs an id"),
            ([{"id": "", "agent_type": "x"}], "step 1 needs an id"),
            ([{"id": 7, "agent_type": "x"}], "step 1 needs an id"),
            ([{"id": "a", "agent_type": "x"}, "b"], "step 2 is not an object"),
            ([{"id": "a", "agent_type": "x", "depends_on": "b"}], "depends_on is a list"),
            ([{"id": "a", "agent_type": "x", "depends_on": [1]}], "which is not a step"),
        ],
    )
    def test_entries_the_runtime_parser_rejects_are_refused(self, steps, needle):
        problems = graph.validate({"steps": steps})
        assert any(needle in p for p in problems), problems

    def test_a_definition_validate_accepts_is_one_the_runtime_parser_accepts(self):
        from workflows.parser import WorkflowParser

        for steps in ([{"agent_type": "x"}], [{"id": "a", "agent_type": "x"}, "b"]):
            assert graph.validate({"steps": steps}) != []
            with pytest.raises((ValueError, TypeError)):
                WorkflowParser().parse({"steps": steps})
        assert graph.validate(_definition()) == []
        WorkflowParser().parse(_definition())

    def test_conditions_with_rules_are_refused_until_the_runtime_branches_on_them(self):
        rules_only = [
            {"id": "score", "type": "condition", "rules": [{"expression": "x > 1", "path": "a"}]},
            {"id": "a", "agent_type": "x", "depends_on": ["score"]},
        ]
        problems = graph.validate({"steps": rules_only})
        assert any("rules are not supported at run time" in p for p in problems), problems
        assert any("needs an expression" in p for p in problems), problems
        assert any("needs a true_path and false_path" in p for p in problems), problems
        mixed = [
            {
                "id": "c",
                "type": "condition",
                "expression": "x",
                "true_path": "a",
                "false_path": "b",
                "rules": [{"path": "b"}],
            },
            {"id": "a", "agent_type": "x", "depends_on": ["c"]},
            {"id": "b", "agent_type": "y", "depends_on": ["c"]},
        ]
        assert any("rules are not supported" in p for p in graph.validate({"steps": mixed}))

    def test_a_condition_may_carry_its_expression_under_condition(self):
        steps = [
            {"id": "c", "type": "condition", "condition": "x > 1", "true_path": "a", "false_path": "b"},
            {"id": "a", "agent_type": "x", "depends_on": ["c"]},
            {"id": "b", "agent_type": "y", "depends_on": ["c"]},
        ]
        assert graph.validate({"steps": steps}) == []

    def test_a_fallback_back_to_an_ancestor_is_a_cycle(self):
        steps = [
            {"id": "a", "agent_type": "x"},
            {"id": "b", "agent_type": "y", "depends_on": ["a"], "on_failure": "fallback(a)"},
        ]
        assert any("form a cycle" in p for p in graph.validate({"steps": steps}))
        with pytest.raises(ValueError):
            WorkflowEngine._topological_sort(steps)


class TestFallbackWithoutADeclaredDependency:
    def _steps(self):
        return [
            {"id": "manual", "type": "agent", "agent_type": "ops", "action": "handle"},
            {"id": "post", "type": "agent", "agent_type": "ap", "action": "post", "on_failure": "fallback(manual)"},
        ]

    def test_the_fallback_is_ordered_after_its_source(self):
        assert WorkflowEngine._topological_sort(self._steps()) == ["post", "manual"]
        assert graph.validate({"steps": self._steps()}) == []

    def test_the_fallback_is_gated_on_its_source_from_the_definition(self):
        state = {"definition": {"steps": self._steps()}, "step_results": {}}
        manual = self._steps()[0]
        assert WorkflowEngine._check_dependencies(manual, state) == "Fallback source 'post' has not been executed"
        state["step_results"]["post"] = {"status": "completed"}
        assert WorkflowEngine._check_dependencies(manual, state) == "fallback_not_needed"
        state["step_results"]["post"] = {"status": "skipped", "reason": "branch_not_taken"}
        assert WorkflowEngine._check_dependencies(manual, state) == "fallback_not_needed"
        state["step_results"]["post"] = {"status": "failed"}
        assert WorkflowEngine._check_dependencies(manual, state) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("post_status", "manual_runs"), [("completed", False), ("failed", True)])
    async def test_the_engine_runs_the_fallback_only_when_its_source_failed(self, post_status, manual_runs):
        from unittest.mock import AsyncMock, patch

        from workflows.state_store import WorkflowStateStore

        store = AsyncMock(spec=WorkflowStateStore)
        engine = WorkflowEngine(state_store=store)
        state = {
            "id": "wfr_fallback",
            "status": "running",
            "definition": engine.parser.parse({"steps": self._steps()}),
            "trigger_payload": {},
            "steps_total": 2,
            "steps_completed": 0,
            "step_results": {},
            "started_at": "2026-10-07T00:00:00+00:00",
        }
        store.load.return_value = state
        ran: list[str] = []

        async def _step(step, _state):
            ran.append(step["id"])
            if step["id"] == "post":
                return {"status": post_status, "output": {}}
            return {"status": "completed", "output": {}}

        with (
            patch("workflows.engine.execute_step", side_effect=_step),
            patch.object(WorkflowEngine, "_operator_halt", AsyncMock(return_value=None)),
        ):
            result = await engine.execute("wfr_fallback")
        assert ran == (["post", "manual"] if manual_runs else ["post"])
        assert result["status"] == "completed"
        if not manual_runs:
            assert result["step_results"]["manual"]["reason"] == "fallback_not_needed"


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _Session:
    def __init__(self, row=None):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, _statement):
        return _Result(self.row)


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_validate_answers_problems_and_the_graph(self):
        answer = await api.validate_workflow(
            api.WorkflowValidateIn(definition=_definition()), tenant_id=str(uuid.uuid4())
        )
        assert answer["valid"] is True and answer["errors"] == [] and len(answer["graph"]["nodes"]) == 5
        bad = await api.validate_workflow(
            api.WorkflowValidateIn(definition={"steps": [{"id": "a", "type": "magic"}]}), tenant_id=str(uuid.uuid4())
        )
        assert bad["valid"] is False and "unknown type" in bad["errors"][0]

    @pytest.mark.asyncio
    async def test_the_graph_route_draws_a_stored_definition(self, monkeypatch):
        from types import SimpleNamespace

        row = SimpleNamespace(id=uuid.uuid4(), definition=_definition())
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session(row))
        drawn = await api.workflow_graph_view(row.id, tenant_id=str(uuid.uuid4()))
        assert len(drawn["graph"]["edges"]) == 7 and drawn["errors"] == [] and drawn["workflow_id"] == str(row.id)
        assert drawn["enabled"] is False
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session(None))
        with pytest.raises(HTTPException) as refused:
            await api.workflow_graph_view(uuid.uuid4(), tenant_id=str(uuid.uuid4()))
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_builder_status_reports_the_flag(self, monkeypatch):
        assert await api.workflow_builder_status(tenant_id=str(uuid.uuid4())) == {"enabled": False}
        monkeypatch.setattr(settings, "workflow_builder_v2_enabled", True)
        assert await api.workflow_builder_status(tenant_id=str(uuid.uuid4())) == {"enabled": True}

    def test_the_builder_status_route_is_registered_before_the_workflow_id_route(self):
        paths = [getattr(r, "path", "") for r in api.router.routes]
        assert paths.index("/workflows/builder") < paths.index("/workflows/{wf_id}")

    def test_creation_refuses_a_broken_definition_only_while_on(self):
        src = (ROOT / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
        create = src[src.index("async def create_workflow(") :]
        create = create[: create.index("\n@router.")]
        assert (
            "if workflow_graph.enabled():" in create
            and 'detail={"error": "workflow_definition", "errors": problems}' in create
        )
