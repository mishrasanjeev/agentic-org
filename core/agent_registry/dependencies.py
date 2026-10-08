# SPDX-License-Identifier: Apache-2.0
"""The dependency graph of an agent: what it is made of and what it depends on, as nodes and edges.

Assembled from the agent's configuration and the registry, never from a run:

* **models**: the model it calls, its fallback and the routing policy;
* **prompt**: the template reference and, when it has one, its own text (as a hash);
* **tools** and the **connectors** behind them (a ``connector:tool`` name
  points at its connector; the knowledge base search points at the
  tenant's knowledge base for the agent's domain);
* **policies**: the guardrail rules that apply to it, its evaluation gate's
  dataset, its output schema and its review condition. A rule ``governs``
  the agent only when execution selects it: the runner scopes guardrails to
  the agent and to the run's use case (``agent_run`` or ``agent_resume``),
  and names no risk tier, so a rule scoped to the card's use case or risk
  tier is shown as ``scoped_to_card`` and marked as not applied at run time;
* **agents**: the agent it was cloned from, the agent its traffic split
  sends runs to, and the teams it belongs to.

Every node carries a kind, an id and a label; every edge a relation. Labels
are names and references, never prompt text or rule reasons.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from core.agent_registry import lifecycle, traffic
from core.evals import gates as eval_gates
from core.evals import runs as eval_runs
from core.models.agent import AgentTeam, AgentTeamMember

KINDS: tuple[str, ...] = ("agent", "model", "prompt", "tool", "connector", "knowledge", "policy", "dataset", "team")
KNOWLEDGE_TOOLS: frozenset[str] = frozenset({"knowledge_base_search", "search_knowledge", "knowledge_search"})
# The guardrail use cases an agent's execution binds (core/langgraph/runner.py: a run and a resume).
# Execution names no risk tier, so a risk-tier-scoped rule is never selected for an agent run.
RUNTIME_USE_CASES: tuple[str, ...] = ("agent_run", "agent_resume")


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, str]] = []

    def node(self, kind: str, key: str, label: str, **detail: Any) -> str:
        node_id = f"{kind}:{key}"
        if node_id not in self.nodes:
            self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label, **detail}
        return node_id

    def edge(self, source: str, relation: str, target: str) -> None:
        edge = {"source": source, "relation": relation, "target": target}
        if edge not in self.edges:
            self.edges.append(edge)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges)}


def _connector_of(tool: str) -> str | None:
    """The connector behind a tool reference, read the way execution reads it.

    Uses the runtime's own normaliser, so every persisted spelling
    (``gmail:send_email``, ``gmail.send_email``, ``gmail__send_email`` and the
    Grantex scope ``tool:gmail:<permission>:send_email``) names the same
    connector here as at run time. A Composio reference
    (``composio:<app>:<action>``) names its app.
    """
    from core.langgraph.tool_adapter import _parse_authorized_tool_ref

    ref = str(tool or "").strip()
    head, _, rest = ref.partition(":")
    if head.lower() == "composio" and ":" in rest:
        return rest.split(":", 1)[0].strip().lower() or None
    parsed = _parse_authorized_tool_ref(ref)
    if parsed is None:
        return None
    connector, _name = parsed
    return connector or None


def _selected_at_runtime(rule: Any, agent_id: str) -> bool:
    """Whether execution selects ``rule`` for the agent: its scope as the runner binds it."""
    return any(
        rule.matches(rule.stage, agent_id=agent_id, use_case=use_case, risk_tier=None) for use_case in RUNTIME_USE_CASES
    )


async def _rules_for(tenant_id: uuid.UUID, agent: Any, entry: Any) -> list[tuple[Any, bool]]:
    """The rules execution selects for the agent (True), then those that name only its card's scope (False)."""
    from core.governance.guardrails.engine import active_rules

    rules = await active_rules(tenant_id)
    agent_id = str(agent.id)
    use_case = getattr(entry, "use_case", None) if entry is not None else None
    risk_tier = getattr(entry, "risk_tier", None) if entry is not None else None
    applying: list[tuple[Any, bool]] = []
    for rule in rules:
        if _selected_at_runtime(rule, agent_id):
            applying.append((rule, True))
        elif (use_case or risk_tier) and any(
            rule.matches(rule.stage, agent_id=agent_id, use_case=candidate, risk_tier=risk_tier)
            for candidate in (use_case, *RUNTIME_USE_CASES)
        ):
            applying.append((rule, False))
    return applying


async def _teams_for(session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> list[Any]:
    statement = (
        select(AgentTeam)
        .join(AgentTeamMember, AgentTeamMember.team_id == AgentTeam.id)
        .where(AgentTeamMember.agent_id == agent_id, AgentTeam.tenant_id == tenant_id)
    )
    return list((await session.execute(statement)).scalars().all())


async def graph(session: Any, tenant_id: uuid.UUID, agent: Any, *, load_agent: Any = None) -> dict[str, Any]:
    """The agent's dependency graph. ``load_agent(id)`` returns a related agent row or None (awaited)."""
    g = Graph()
    root = g.node("agent", str(agent.id), agent.name, agent_type=agent.agent_type, status=agent.status)
    entry = await lifecycle.get_entry(session, tenant_id, agent.id)
    g.nodes[root]["state"] = entry.state if entry is not None else "draft"

    model = getattr(agent, "llm_model", None)
    if model:
        provider = getattr(agent, "llm_provider", None)
        g.edge(root, "calls", g.node("model", model, model, provider=provider))
    fallback = getattr(agent, "llm_fallback", None)
    if fallback and fallback != model:
        g.edge(root, "falls_back_to", g.node("model", fallback, fallback))
    routing = (getattr(agent, "llm_config", None) or {}).get("routing")
    if routing:
        g.nodes[root]["routing"] = routing

    ref = getattr(agent, "system_prompt_ref", None)
    text = str(getattr(agent, "system_prompt_text", None) or "")
    if ref:
        g.edge(root, "uses_prompt", g.node("prompt", ref, ref, source="template"))
    if text.strip():
        prompt_hash = eval_runs.prompt_hash(text)
        g.edge(root, "uses_prompt", g.node("prompt", prompt_hash[:12], f"own text {prompt_hash[:12]}", source="own"))

    domain = getattr(agent, "domain", None) or "ops"
    for tool in getattr(agent, "authorized_tools", None) or []:
        tool_node = g.node("tool", tool, tool)
        g.edge(root, "uses_tool", tool_node)
        if tool in KNOWLEDGE_TOOLS:
            g.edge(tool_node, "reads", g.node("knowledge", domain, f"knowledge base ({domain})", domain=domain))
        connector = _connector_of(tool)
        if connector:
            g.edge(tool_node, "through", g.node("connector", connector, connector))
    for connector_id in getattr(agent, "connector_ids", None) or []:
        g.edge(root, "linked_to", g.node("connector", str(connector_id), str(connector_id), linked=True))

    for rule, at_runtime in await _rules_for(tenant_id, agent, entry):
        scope = "agent" if rule.agent_id else "use_case" if rule.use_case else "risk_tier" if rule.risk_tier else "all"
        g.edge(
            root,
            "governed_by" if at_runtime else "scoped_to_card",
            g.node(
                "policy",
                f"rule-{rule.id}",
                rule.name,
                stage=rule.stage,
                action=rule.action,
                scope=scope,
                applied_at_runtime=at_runtime,
            ),
        )
    hitl = getattr(agent, "hitl_condition", None)
    if hitl:
        g.edge(root, "reviewed_when", g.node("policy", f"hitl-{agent.id}", "review condition", condition=hitl))
    schema = getattr(agent, "output_schema", None)
    config = getattr(agent, "config", None) or {}
    if schema:
        g.edge(root, "held_to", g.node("policy", f"schema-{schema}", f"schema {schema}", registered=True))
    elif config.get("output_schema_json"):
        g.edge(root, "held_to", g.node("policy", f"schema-own-{agent.id}", "own output schema", registered=False))
    gate = eval_gates.declared(agent)
    if gate:
        g.edge(
            root,
            "gated_by",
            g.node("dataset", gate["dataset_id"], f"dataset {gate['dataset_id'][:8]}", version=gate.get("version")),
        )

    parent = getattr(agent, "parent_agent_id", None)
    if parent:
        parent_row = await load_agent(parent) if load_agent else None
        label = parent_row.name if parent_row is not None else str(parent)
        g.edge(root, "cloned_from", g.node("agent", str(parent), label))
    split = traffic.declared(agent)
    if split:
        target = await load_agent(uuid.UUID(split["to_agent_id"])) if load_agent else None
        label = target.name if target is not None else split["to_agent_id"]
        g.edge(root, f"splits_{split['percent']}pct_to", g.node("agent", split["to_agent_id"], label))
    for team in await _teams_for(session, tenant_id, agent.id):
        g.edge(root, "member_of", g.node("team", str(team.id), team.name))
    return g.to_dict()
