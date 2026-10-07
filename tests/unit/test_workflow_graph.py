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
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session(None))
        with pytest.raises(HTTPException) as refused:
            await api.workflow_graph_view(uuid.uuid4(), tenant_id=str(uuid.uuid4()))
        assert refused.value.status_code == 404

    def test_creation_refuses_a_broken_definition_only_while_on(self):
        src = (ROOT / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
        create = src[src.index("async def create_workflow(") :]
        create = create[: create.index("\n@router.")]
        assert (
            "if workflow_graph.enabled():" in create
            and 'detail={"error": "workflow_definition", "errors": problems}' in create
        )
