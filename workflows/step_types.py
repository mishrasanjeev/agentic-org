"""All 9 workflow step type implementations."""
from __future__ import annotations

import asyncio
from typing import Any

from workflows.condition_evaluator import evaluate_condition


async def execute_step(step: dict, state: dict) -> dict[str, Any]:
    step_type = step.get("type", "agent")
    handlers = {
        "agent": _execute_agent,
        "condition": _execute_condition,
        "human_in_loop": _execute_hitl,
        "parallel": _execute_parallel,
        "loop": _execute_loop,
        "transform": _execute_transform,
        "notify": _execute_notify,
        "sub_workflow": _execute_sub_workflow,
        "wait": _execute_wait,
    }
    handler = handlers.get(step_type, _execute_agent)
    return await handler(step, state)


async def _execute_agent(step, state):
    return {
        "step_id": step["id"],
        "type": "agent",
        "status": "completed",
        "agent": step.get("agent", ""),
        "action": step.get("action", ""),
    }


async def _execute_condition(step, state):
    condition = step.get("condition", "true")
    context = state.get("context", {})
    result = evaluate_condition(condition, context)
    path = step.get("true_path") if result else step.get("false_path")
    return {
        "step_id": step["id"],
        "type": "condition",
        "result": result,
        "next_path": path,
    }


async def _execute_hitl(step, state):
    return {
        "step_id": step["id"],
        "type": "human_in_loop",
        "status": "waiting_hitl",
        "assignee_role": step.get("assignee_role", ""),
        "timeout_hours": step.get("timeout_hours", 4),
    }


async def _execute_parallel(step, state):
    sub_steps = step.get("steps", [])
    wait_for = step.get("wait_for", "all")
    tasks = [execute_step({"id": s, "type": "agent"}, state) for s in sub_steps]
    if wait_for == "any":
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        results = [d.result() for d in done]
    else:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return {"step_id": step["id"], "type": "parallel", "results": results}


async def _execute_loop(step, state):
    items = step.get("items", [])
    results = []
    for item in items:
        r = await execute_step({"id": f"{step['id']}_item", "type": "agent"}, state)
        results.append(r)
    return {"step_id": step["id"], "type": "loop", "results": results}


async def _execute_transform(step, state):
    return {"step_id": step["id"], "type": "transform", "status": "completed"}


async def _execute_notify(step, state):
    return {
        "step_id": step["id"],
        "type": "notify",
        "status": "sent",
        "connector": step.get("connector", ""),
    }


async def _execute_sub_workflow(step: dict, state: dict) -> dict[str, Any]:
    """Execute a nested sub-workflow to completion.

    The step must provide either:
      - ``definition``: an inline workflow definition dict/YAML, or
      - ``workflow_definition_id``: an identifier that the engine resolves
        (falls back to looking in ``state["workflow_registry"]``).

    A new :class:`WorkflowEngine` instance is created so the sub-workflow has
    its own run lifecycle, state, and checkpoint trail.  The parent workflow
    blocks until the child completes (or fails).
    """
    # Lazy import to avoid circular dependency (engine imports step_types).
    from workflows.engine import WorkflowEngine  # noqa: F811

    sub_definition = step.get("definition")

    if sub_definition is None:
        # Try to resolve from a registry attached to state.
        definition_id = step.get("workflow_definition_id")
        registry = state.get("workflow_registry", {})
        if definition_id and definition_id in registry:
            sub_definition = registry[definition_id]
        elif definition_id:
            return {
                "step_id": step["id"],
                "type": "sub_workflow",
                "status": "failed",
                "error": f"Sub-workflow definition '{definition_id}' not found in registry",
            }
        else:
            return {
                "step_id": step["id"],
                "type": "sub_workflow",
                "status": "failed",
                "error": "No 'definition' or 'workflow_definition_id' provided for sub_workflow step",
            }

    # Reuse the same state store from the parent.  If `state_store` is not
    # attached (e.g. in tests), fall back to creating a fresh in-memory store.
    state_store = state.get("_state_store")
    if state_store is None:
        from workflows.state_store import WorkflowStateStore
        state_store = WorkflowStateStore()

    sub_engine = WorkflowEngine(state_store=state_store)

    # Build trigger payload from the parent step's input config.
    trigger_payload = step.get("input", {})

    sub_run_id = await sub_engine.start_run(sub_definition, trigger_payload=trigger_payload)
    result = await sub_engine.execute(sub_run_id)

    return {
        "step_id": step["id"],
        "type": "sub_workflow",
        "status": result.get("status", "completed"),
        "sub_run_id": sub_run_id,
        "output": result.get("step_results", {}),
    }


async def _execute_wait(step, state):
    return {"step_id": step["id"], "type": "wait", "status": "completed"}
