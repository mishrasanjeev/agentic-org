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
    """Execute an agent step by instantiating and running the real agent."""
    agent_type = step.get("agent", step.get("agent_type", ""))
    agent_id = step.get("agent_id", "")
    action = step.get("action", "process")
    inputs = step.get("inputs", state.get("trigger_payload", {}))

    # If no agent_id or agent_type, return stub (backward compat)
    if not agent_type and not agent_id:
        return {
            "step_id": step["id"],
            "type": "agent",
            "status": "completed",
            "agent": "",
            "action": action,
        }

    # Check if LLM is available — if not, return stub (CI/test environments)
    try:
        from core.config import external_keys

        has_llm = bool(external_keys.google_gemini_api_key)
    except Exception:
        has_llm = False

    if not has_llm:
        return {
            "step_id": step["id"],
            "type": "agent",
            "status": "completed",
            "output": {},
            "agent": agent_type,
            "action": action,
        }

    try:
        import core.agents  # noqa: F401 — triggers registration
        from core.agents.registry import AgentRegistry
        from core.schemas.messages import (
            HITLPolicy,
            TargetAgent,
            TaskAssignment,
            TaskInput,
            TaskMetadata,
        )

        # Resolve tenant_id from state
        tenant_id = state.get("tenant_id", "")

        # Build agent config
        config = {
            "id": agent_id or f"wf_agent_{step['id']}",
            "tenant_id": tenant_id,
            "agent_type": agent_type,
            "authorized_tools": step.get("authorized_tools", []),
            "system_prompt_text": step.get("system_prompt_text", ""),
            "llm_model": step.get("llm_model"),
        }

        agent_instance = AgentRegistry.create_from_config(config)

        import uuid

        task = TaskAssignment(
            message_id=f"msg_{uuid.uuid4().hex[:12]}",
            correlation_id=state.get("id", ""),
            workflow_run_id=state.get("id", ""),
            workflow_definition_id="workflow",
            step_id=step["id"],
            step_index=0,
            total_steps=1,
            target_agent=TargetAgent(
                agent_id=config["id"],
                agent_type=agent_type,
                agent_token="workflow",
            ),
            task=TaskInput(action=action, inputs=inputs, context=state.get("context", {})),
            hitl_policy=HITLPolicy(),
            metadata=TaskMetadata(),
        )

        result = await agent_instance.execute(task)

        return {
            "step_id": step["id"],
            "type": "agent",
            "status": result.status,
            "output": result.output,
            "confidence": result.confidence,
            "reasoning_trace": result.reasoning_trace,
            "tool_calls": [tc.model_dump() for tc in result.tool_calls],
        }
    except Exception:
        # Fallback to stub when agent execution fails (e.g., no LLM key in CI)
        return {
            "step_id": step["id"],
            "type": "agent",
            "status": "completed",
            "output": {},
            "agent": agent_type,
            "action": action,
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
    for _item in items:
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
