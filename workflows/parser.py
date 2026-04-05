"""Parse and validate workflow definitions (YAML/JSON)."""

from __future__ import annotations

from typing import Any

import yaml


class WorkflowParser:
    VALID_STEP_TYPES = {
        "agent",
        "condition",
        "human_in_loop",
        "parallel",
        "loop",
        "transform",
        "notify",
        "sub_workflow",
        "wait",
        "wait_for_event",
        "collaboration",
    }

    def parse(self, definition: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(definition, str):
            definition = yaml.safe_load(definition)
        self._validate(definition)
        return definition

    def _validate(self, defn: dict) -> None:
        if "steps" not in defn:
            raise ValueError("Workflow must have steps")
        step_ids = set()
        for step in defn["steps"]:
            if "id" not in step:
                raise ValueError("Every step must have an id")
            if step["id"] in step_ids:
                raise ValueError(f"Duplicate step id: {step['id']}")
            step_ids.add(step["id"])
            step_type = step.get("type", "agent")
            if step_type not in self.VALID_STEP_TYPES:
                raise ValueError(f"Invalid step type: {step_type}")
        self._check_circular(defn["steps"])

    def _check_circular(self, steps: list[dict]) -> None:
        graph: dict[str, list[str]] = {}
        for step in steps:
            deps = step.get("depends_on", [])
            graph[step["id"]] = deps
        visited: set[str] = set()
        in_stack: set[str] = set()
        for node in graph:
            if self._has_cycle(node, graph, visited, in_stack):
                raise ValueError(f"E3006: Circular dependency detected involving {node}")

    def _has_cycle(self, node, graph, visited, in_stack) -> bool:
        if node in in_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in graph.get(node, []):
            if self._has_cycle(dep, graph, visited, in_stack):
                return True
        in_stack.discard(node)
        return False
