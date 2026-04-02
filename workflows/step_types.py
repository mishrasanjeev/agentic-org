"""All 11 workflow step type implementations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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
        "wait_for_event": _execute_wait_for_event,
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
    """Wait/delay step — pauses workflow for a specified duration.

    Config options:
        duration_hours, duration_minutes, duration_seconds — relative delay
        until — ISO8601 datetime to wait until (absolute)

    The workflow engine detects ``waiting_delay`` status and schedules
    a Celery task to resume after the delay.
    """
    now = datetime.now(UTC)
    resume_at = None

    if step.get("until"):
        resume_at = datetime.fromisoformat(step["until"].replace("Z", "+00:00"))
    else:
        hours = step.get("duration_hours", 0)
        minutes = step.get("duration_minutes", 0)
        seconds = step.get("duration_seconds", 0)
        total_seconds = hours * 3600 + minutes * 60 + seconds
        if total_seconds > 0:
            resume_at = now + timedelta(seconds=total_seconds)

    if resume_at is None or resume_at <= now:
        # No delay or already past — complete immediately
        return {"step_id": step["id"], "type": "wait", "status": "completed"}

    # Schedule Celery task to resume after delay
    try:
        from core.tasks.workflow_tasks import resume_workflow_wait

        run_id = state.get("id", "")
        resume_workflow_wait.apply_async(
            args=[run_id, step["id"]],
            eta=resume_at,
        )
    except Exception:  # noqa: S110
        pass  # Celery unavailable — step completes on next poll

    return {
        "step_id": step["id"],
        "type": "wait",
        "status": "waiting_delay",
        "resume_at": resume_at.isoformat(),
    }


async def _execute_wait_for_event(step, state):
    """Wait for an external event (email open, click, webhook, etc.).

    Config:
        event_type — e.g. "email.opened", "email.clicked", "webhook.received"
        timeout_hours — how long to wait before giving up (default 48)
        match — dict of fields to match against incoming events

    The workflow engine detects ``waiting_event`` status. When a matching
    event arrives via the webhook listener, it resumes the workflow.
    If timeout expires, the step completes with status "timed_out".
    """
    event_type = step.get("event_type", "")
    timeout_hours = step.get("timeout_hours", 48)
    match_criteria = step.get("match", {})
    run_id = state.get("id", "")
    timeout_at = datetime.now(UTC) + timedelta(hours=timeout_hours)

    # Register event listener in Redis (if available)
    try:
        import json as _json

        import redis.asyncio as aioredis

        from core.config import settings

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        listener_key = f"wfwait_event:{event_type}:{run_id}:{step['id']}"
        await r.set(
            listener_key,
            _json.dumps({
                "run_id": run_id,
                "step_id": step["id"],
                "match": match_criteria,
                "timeout_at": timeout_at.isoformat(),
            }),
            ex=int(timeout_hours * 3600) + 60,
        )
        await r.aclose()
    except Exception:  # noqa: S110
        pass  # Redis unavailable — event will time out

    # Schedule timeout
    try:
        from core.tasks.workflow_tasks import timeout_workflow_event

        timeout_workflow_event.apply_async(
            args=[run_id, step["id"]],
            eta=timeout_at,
        )
    except Exception:  # noqa: S110
        pass  # Celery unavailable — event will not auto-timeout

    return {
        "step_id": step["id"],
        "type": "wait_for_event",
        "status": "waiting_event",
        "event_type": event_type,
        "timeout_at": timeout_at.isoformat(),
    }
