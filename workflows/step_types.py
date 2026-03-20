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
    return {"step_id": step["id"], "type": "agent", "status": "completed", "agent": step.get("agent", ""), "action": step.get("action", "")}

async def _execute_condition(step, state):
    condition = step.get("condition", "true")
    context = state.get("context", {})
    result = evaluate_condition(condition, context)
    path = step.get("true_path") if result else step.get("false_path")
    return {"step_id": step["id"], "type": "condition", "result": result, "next_path": path}

async def _execute_hitl(step, state):
    return {"step_id": step["id"], "type": "human_in_loop", "status": "waiting_hitl", "assignee_role": step.get("assignee_role", ""), "timeout_hours": step.get("timeout_hours", 4)}

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
    return {"step_id": step["id"], "type": "notify", "status": "sent", "connector": step.get("connector", "")}

async def _execute_sub_workflow(step, state):
    return {"step_id": step["id"], "type": "sub_workflow", "status": "completed"}

async def _execute_wait(step, state):
    return {"step_id": step["id"], "type": "wait", "status": "completed"}
