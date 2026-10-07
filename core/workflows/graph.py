# SPDX-License-Identifier: Apache-2.0
"""A workflow definition as a graph, and the checks the visual builder and the API apply to it.

A definition is a list of steps (``workflows/parser.py``): each has an id, a
type, what it depends on (``depends_on``), and, by type, an agent and action,
a condition with its paths, a human checkpoint with its decision options, or
a failure directive (``on_failure``: ``halt``, ``continue``, ``retry(N)``,
``retry(N) then continue``, or ``fallback(step)``). ``to_graph`` draws it:
one node per step and one edge per dependency, condition path and fallback,
each edge labelled with its kind, so the console shows branching and
fallback as drawn. ``validate`` names every problem in plain words: a
dependency, path or fallback that points nowhere, a condition without paths,
a human checkpoint without decision options, a failure directive outside the
grammar, a cycle.

While ``AGENTICORG_WORKFLOW_BUILDER_V2_ENABLED`` is on, ``POST /workflows``
refuses a definition with problems (``422``); ``POST /workflows/validate``
and ``GET /workflows/{id}/graph`` answer whether or not it is on.
"""

from __future__ import annotations

import re
from typing import Any

from core.config import settings

STEP_TYPES: tuple[str, ...] = (
    "agent",
    "case_agent",
    "condition",
    "human_in_loop",
    "parallel",
    "loop",
    "transform",
    "connector_tool",
    "notify",
    "sub_workflow",
    "wait",
    "wait_for_event",
    "collaboration",
)
EDGE_KINDS: tuple[str, ...] = ("then", "true", "false", "rule", "fallback")
MAX_STEPS = 200
_FAILURE_GRAMMAR = re.compile(
    r"^(?:halt|continue|ignore|optional|retry\(\d{1,2}\)(?:\s*(?:then|,)\s*(?:continue|ignore|optional))?"
    r"|fallback\(\s*[A-Za-z0-9_.-]+\s*\))$"
)
_FALLBACK = re.compile(r"fallback\(\s*([A-Za-z0-9_.-]+)\s*\)")


def enabled() -> bool:
    return bool(settings.workflow_builder_v2_enabled)


def step_id(step: Any, index: int) -> str:
    raw = step.get("id") if isinstance(step, dict) else None
    if raw in (None, ""):
        raw = step.get("step") if isinstance(step, dict) else None
    return str(raw) if raw not in (None, "") else f"step_{index + 1}"


def fallback_target(step: dict[str, Any]) -> str | None:
    match = _FALLBACK.search(str(step.get("on_failure") or ""))
    return match.group(1) if match else None


def _paths(step: dict[str, Any]) -> list[tuple[str, str]]:
    """The (kind, target) pairs of a condition step."""
    out: list[tuple[str, str]] = []
    if step.get("true_path"):
        out.append(("true", str(step["true_path"])))
    if step.get("false_path"):
        out.append(("false", str(step["false_path"])))
    for rule in step.get("rules") or []:
        if isinstance(rule, dict) and rule.get("path"):
            out.append(("rule", str(rule["path"])))
    return out


def to_graph(definition: Any) -> dict[str, Any]:
    """Nodes and edges for the console: one node per step, one edge per dependency, path and fallback."""
    steps = list((definition or {}).get("steps") or []) if isinstance(definition, dict) else []
    nodes = []
    edges = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        sid = step_id(step, index)
        kind = str(step.get("type") or "agent")
        nodes.append(
            {
                "id": sid,
                "type": kind,
                "name": str(step.get("name") or sid),
                "summary": _summary(step, kind),
                "on_failure": str(step.get("on_failure") or "halt"),
            }
        )
        for dep in step.get("depends_on") or []:
            edges.append({"source": str(dep), "target": sid, "kind": "then", "label": ""})
        for path_kind, target in _paths(step):
            label = path_kind
            if path_kind == "rule":
                rule = next(
                    (r for r in step.get("rules") or [] if isinstance(r, dict) and str(r.get("path")) == target), {}
                )
                label = str(rule.get("label") or rule.get("expression") or "rule")[:40]
            edges.append({"source": sid, "target": target, "kind": path_kind, "label": label})
        fallback = fallback_target(step)
        if fallback:
            edges.append({"source": sid, "target": fallback, "kind": "fallback", "label": "on failure"})
    return {"nodes": nodes, "edges": edges}


def _summary(step: dict[str, Any], kind: str) -> str:
    if kind in ("agent", "case_agent"):
        return f"{step.get('agent_type') or step.get('agent') or 'agent'}: {step.get('action') or 'run'}"
    if kind == "condition":
        return str(step.get("expression") or f"{len(step.get('rules') or [])} rules")[:80]
    if kind == "human_in_loop":
        options = step.get("decision_options") or []
        who = step.get("assignee_role") or step.get("assignee") or step.get("role_required") or "a person"
        return f"{who} decides: {', '.join(str(o) for o in options)}"[:80]
    if kind == "collaboration":
        return f"{len(step.get('agents') or [])} agents, {step.get('aggregation') or 'merge'}"
    if kind in ("wait", "wait_for_event"):
        return str(step.get("duration") or step.get("event") or step.get("event_type") or "wait")[:80]
    return str(step.get("action") or step.get("name") or kind)[:80]


def validate(definition: Any) -> list[str]:
    """Every problem with a definition, in plain words; empty when it can be drawn and run."""
    problems: list[str] = []
    if not isinstance(definition, dict) or not isinstance(definition.get("steps"), list):
        return ["the definition must hold a list of steps"]
    steps = [s for s in definition["steps"] if isinstance(s, dict)]
    if not steps:
        return ["the workflow needs at least one step"]
    if len(steps) > MAX_STEPS:
        return [f"a workflow has at most {MAX_STEPS} steps"]
    ids = [step_id(s, i) for i, s in enumerate(steps)]
    known = set(ids)
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            problems.append(f"step id {sid!r} is used more than once")
        seen.add(sid)
    for index, step in enumerate(steps):
        sid = ids[index]
        kind = str(step.get("type") or "agent")
        if kind not in STEP_TYPES:
            problems.append(f"step {sid!r}: unknown type {kind!r}")
        for dep in step.get("depends_on") or []:
            if str(dep) not in known:
                problems.append(f"step {sid!r}: depends on {dep!r}, which is not a step")
            elif str(dep) == sid:
                problems.append(f"step {sid!r}: depends on itself")
        if kind == "condition":
            paths = _paths(step)
            if not paths:
                problems.append(f"step {sid!r}: a condition needs a true_path and false_path, or rules with paths")
            if not step.get("expression") and not step.get("rules"):
                problems.append(f"step {sid!r}: a condition needs an expression or rules")
            for _kind, target in paths:
                if target not in known:
                    problems.append(f"step {sid!r}: path {target!r} is not a step")
        if kind == "human_in_loop":
            options = step.get("decision_options")
            if not isinstance(options, list) or not options:
                problems.append(f"step {sid!r}: a human checkpoint needs decision_options")
            if not (step.get("assignee") or step.get("assignee_role") or step.get("role_required")):
                problems.append(f"step {sid!r}: a human checkpoint names who decides (assignee or assignee_role)")
        if kind in ("agent", "case_agent") and not (step.get("agent_type") or step.get("agent")):
            problems.append(f"step {sid!r}: an agent step names its agent_type")
        directive = str(step.get("on_failure") or "").strip().lower()
        if directive and not _FAILURE_GRAMMAR.match(directive):
            problems.append(
                f"step {sid!r}: on_failure is halt, continue, retry(N), retry(N) then continue, or fallback(step)"
            )
        fallback = fallback_target(step)
        if fallback:
            if fallback not in known:
                problems.append(f"step {sid!r}: fallback {fallback!r} is not a step")
            elif fallback == sid:
                problems.append(f"step {sid!r}: a step cannot be its own fallback")
    cycle = _cycle(steps, ids)
    if cycle:
        problems.append(f"the dependencies form a cycle through {cycle!r}")
    return problems


def _cycle(steps: list[dict[str, Any]], ids: list[str]) -> str | None:
    graph = {ids[i]: [str(d) for d in (s.get("depends_on") or [])] for i, s in enumerate(steps)}
    visited: set[str] = set()
    stack: set[str] = set()

    def walk(node: str) -> bool:
        if node in stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        stack.add(node)
        if any(walk(dep) for dep in graph.get(node, []) if dep in graph):
            return True
        stack.discard(node)
        return False

    for node in graph:
        if walk(node):
            return node
    return None
