# SPDX-License-Identifier: Apache-2.0
"""The dependency graph: what an agent is made of and depends on, as nodes and edges without content."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_registry import dependencies
from core.evals import runs
from core.governance.guardrails.schema import Rule

TENANT = uuid.uuid4()


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(
            scalar_one_or_none=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: list(value))
        )


def _agent(**over):
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Claims decider",
        "agent_type": "claims",
        "domain": "ops",
        "status": "active",
        "llm_model": "gpt-4o",
        "llm_provider": "openai",
        "llm_fallback": "gpt-4o-mini",
        "llm_config": {"routing": {"policy": "cost"}},
        "system_prompt_ref": "claims/v3",
        "system_prompt_text": "Decide the claim.",
        "authorized_tools": ["knowledge_base_search", "zoho_books:list_invoices", "composio:salesforce:get_claim"],
        "connector_ids": [],
        "hitl_condition": "confidence < 0.8",
        "output_schema": None,
        "config": {},
        "parent_agent_id": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _rule(name, **over) -> Rule:
    base = {"id": uuid.uuid4().hex[:8], "name": name, "stage": "input", "detector": "injection"}
    base.update(over)
    return Rule(**base)


@pytest.fixture
def rules(monkeypatch):
    holder: dict[str, list] = {"rules": []}

    async def _active(_tenant):
        return list(holder["rules"])

    import core.governance.guardrails.engine as engine

    monkeypatch.setattr(engine, "active_rules", _active)
    return holder


def _graph(agent, entry=None, teams=(), rules_holder=None, load_agent=None):
    session = _Session(entry, list(teams))
    return asyncio.run(dependencies.graph(session, TENANT, agent, load_agent=load_agent)), session


class TestGraph:
    def test_the_agent_is_linked_to_its_models_prompt_tools_connectors_and_knowledge(self, rules):
        agent = _agent()
        result, _ = _graph(agent)
        by_id = {node["id"]: node for node in result["nodes"]}
        root = f"agent:{agent.id}"
        assert by_id[root]["state"] == "draft" and by_id[root]["routing"] == {"policy": "cost"}
        edges = {(edge["source"], edge["relation"], edge["target"]) for edge in result["edges"]}
        assert (root, "calls", "model:gpt-4o") in edges and (root, "falls_back_to", "model:gpt-4o-mini") in edges
        assert by_id["model:gpt-4o"]["provider"] == "openai"
        assert (root, "uses_prompt", "prompt:claims/v3") in edges
        own = [node for node in result["nodes"] if node["kind"] == "prompt" and node["source"] == "own"]
        assert len(own) == 1 and own[0]["label"] == f"own text {runs.prompt_hash('Decide the claim.')[:12]}"
        assert "Decide the claim." not in str(result)
        assert (root, "uses_tool", "tool:knowledge_base_search") in edges
        assert ("tool:knowledge_base_search", "reads", "knowledge:ops") in edges
        assert ("tool:zoho_books:list_invoices", "through", "connector:zoho_books") in edges
        assert ("tool:composio:salesforce:get_claim", "through", "connector:salesforce") in edges
        assert (root, "reviewed_when", f"policy:hitl-{agent.id}") in edges

    def test_policies_are_the_rules_that_apply_the_schema_and_the_gate(self, rules):
        agent = _agent(output_schema="Invoice", config={"eval_gate": {"dataset_id": str(uuid.uuid4()), "version": 2}})
        entry = SimpleNamespace(state="approved", use_case="claims", risk_tier="high")
        rules["rules"] = [
            _rule("everyone"),
            _rule("this agent", agent_id=str(agent.id)),
            _rule("another agent", agent_id=str(uuid.uuid4())),
            _rule("claims", use_case="claims"),
            _rule("mortgages", use_case="mortgages"),
            _rule("high risk", risk_tier="high"),
            _rule("low risk", risk_tier="low"),
        ]
        result, _ = _graph(agent, entry)
        policies = {node["label"]: node for node in result["nodes"] if node["kind"] == "policy"}
        assert set(policies) == {"everyone", "this agent", "claims", "high risk", "review condition", "schema Invoice"}
        assert policies["this agent"]["scope"] == "agent" and policies["everyone"]["scope"] == "all"
        datasets = [node for node in result["nodes"] if node["kind"] == "dataset"]
        assert len(datasets) == 1 and datasets[0]["version"] == 2
        assert {node["id"]: node for node in result["nodes"]}[f"agent:{agent.id}"]["state"] == "approved"

    def test_related_agents_and_teams_are_named(self, rules):
        parent = _agent(name="Claims decider v1")
        target = _agent(name="Claims decider canary")
        agent = _agent(
            parent_agent_id=parent.id,
            config={
                "traffic_split": {"to_agent_id": str(target.id), "percent": 10},
                "output_schema_json": {"type": "object"},
            },
        )
        team = SimpleNamespace(id=uuid.uuid4(), name="Claims desk")

        async def _load(agent_id):
            return {parent.id: parent, target.id: target}.get(agent_id)

        result, session = _graph(agent, None, teams=[team], load_agent=_load)
        edges = {(edge["source"], edge["relation"], edge["target"]) for edge in result["edges"]}
        root = f"agent:{agent.id}"
        assert (root, "cloned_from", f"agent:{parent.id}") in edges
        assert (root, "splits_10pct_to", f"agent:{target.id}") in edges
        assert (root, "member_of", f"team:{team.id}") in edges
        labels = {node["id"]: node["label"] for node in result["nodes"]}
        assert labels[f"agent:{parent.id}"] == "Claims decider v1" and labels[f"team:{team.id}"] == "Claims desk"
        assert (root, "held_to", f"policy:schema-own-{agent.id}") in edges
        assert (
            "agent_team_members.agent_id" in session.statements[1] and "agent_teams.tenant_id" in session.statements[1]
        )

    def test_without_a_loader_related_agents_keep_their_ids(self, rules):
        parent_id = uuid.uuid4()
        result, _ = _graph(_agent(parent_agent_id=parent_id))
        labels = {node["id"]: node["label"] for node in result["nodes"]}
        assert labels[f"agent:{parent_id}"] == str(parent_id)

    def test_every_node_has_a_kind_the_graph_knows(self, rules):
        result, _ = _graph(_agent())
        assert {node["kind"] for node in result["nodes"]} <= set(dependencies.KINDS)
        assert all(set(edge) == {"source", "relation", "target"} for edge in result["edges"])
