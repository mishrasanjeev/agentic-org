"""Workflow step type implementations."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from core.config import external_keys, is_relaxed_env, is_strict_runtime_env, settings
from workflows.condition_evaluator import evaluate_condition
from workflows.event_waits import WorkflowEventWaitStore
from workflows.parallel_executor import execute_parallel
from workflows.step_results import (
    ALLOWED_STEP_STATUSES,
    AgentExecutionError,
    MissingAgentConfigError,
    MissingLLMProviderConfigError,
    NotifySideEffectNotConfiguredError,
    ParallelChildError,
    UnknownStepStatusError,
    UnsupportedTransformConfigError,
    failure_result,
    is_success_status,
    stubbed_result,  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
)

TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUTHY_ENV_VALUES


# enterprise-gate: stub-ok reason=relaxed-env-only-not-production
def _stub_steps_allowed() -> bool:
    return _env_flag(  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        "AGENTICORG_WORKFLOW_ALLOW_STUB_STEPS"
    ) and is_relaxed_env(settings.env)


# enterprise-gate: stub-ok reason=hermetic-test-only-not-production
def _fake_llm_allowed() -> bool:
    return _env_flag("AGENTICORG_TEST_FAKE_LLM") and is_relaxed_env(settings.env)


def _real_llm_provider_configured() -> bool:
    return bool(
        external_keys.google_gemini_api_key
        or external_keys.anthropic_api_key
        or external_keys.openai_api_key
    )


def _llm_available_for_workflow() -> bool:
    if _env_flag("AGENTICORG_TEST_FAKE_LLM") and is_strict_runtime_env(settings.env):
        return False
    if _real_llm_provider_configured():
        return True
    if settings.llm_mode.lower().strip() == "local":
        return bool(os.getenv("OLLAMA_HOST") or os.getenv("VLLM_BASE_URL"))
    return _fake_llm_allowed()


def _missing_agent_result(step: dict, action: str) -> dict[str, Any]:
    step_id = step.get("id", "")
    if _stub_steps_allowed():  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        return stubbed_result(  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
            step_id=step_id,
            step_type="agent",
            code="missing_agent_config",
            message="Agent execution uses relaxed-env placeholder because no agent config was provided.",
            agent="",
            action=action,
        )
    return failure_result(
        step_id=step_id,
        step_type="agent",
        failure=MissingAgentConfigError(step_id=step_id),
    )


def _missing_llm_result(step: dict, agent_type: str, action: str) -> dict[str, Any]:
    step_id = step.get("id", "")
    if _stub_steps_allowed():  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        return stubbed_result(  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
            step_id=step_id,
            step_type="agent",
            code="missing_llm_provider_config",
            message="Agent execution uses relaxed-env placeholder because no LLM/provider config is available.",
            agent=agent_type,
            action=action,
        )
    return failure_result(
        step_id=step_id,
        step_type="agent",
        failure=MissingLLMProviderConfigError(agent=agent_type, step_id=step_id),
    )


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
        "collaboration": _execute_collaboration,
    }
    handler = handlers.get(step_type, _execute_agent)
    return await handler(step, state)


async def _execute_collaboration(step: dict, state: dict) -> dict[str, Any]:
    from workflows.collaboration import execute_collaboration_step

    return await execute_collaboration_step(step, state)


async def _execute_agent(step: dict, state: dict) -> dict[str, Any]:
    """Execute an agent step by instantiating and running the configured agent."""
    agent_type = step.get("agent", step.get("agent_type", ""))
    agent_id = step.get("agent_id", "")
    action = step.get("action", "process")
    inputs = step.get("inputs", state.get("trigger_payload", {}))

    if not agent_type and not agent_id:
        return _missing_agent_result(step, action)

    if not _llm_available_for_workflow():
        return _missing_llm_result(step, agent_type, action)

    try:
        import uuid

        import core.agents  # noqa: F401 - triggers registration
        from core.agents.registry import AgentRegistry
        from core.schemas.messages import (
            HITLPolicy,
            TargetAgent,
            TaskAssignment,
            TaskInput,
            TaskMetadata,
        )

        tenant_id = state.get("tenant_id", "")
        config = {
            "id": agent_id or f"wf_agent_{step['id']}",
            "tenant_id": tenant_id,
            "agent_type": agent_type,
            "authorized_tools": step.get("authorized_tools", []),
            "system_prompt_text": step.get("system_prompt_text", ""),
            "llm_model": step.get("llm_model"),
        }

        if _fake_llm_allowed() and not _real_llm_provider_configured():
            from core.agents.base import BaseAgent

            agent_instance = BaseAgent(
                agent_id=config["id"],
                tenant_id=config["tenant_id"],
                authorized_tools=config.get("authorized_tools", []),
                llm_model=config.get("llm_model"),
            )
            agent_instance.confidence_floor = 0.0
        else:
            agent_instance = AgentRegistry.create_from_config(config)

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
        result_status = result.status
        if result_status == "hitl_triggered":
            result_status = "waiting_hitl"
        if result_status not in ALLOWED_STEP_STATUSES:
            return failure_result(
                step_id=step["id"],
                step_type="agent",
                failure=UnknownStepStatusError(step_id=step["id"], status=result_status),
                output=result.output,
            )

        if result_status == "failed":
            error = result.error
            cause = (
                str(error.get("message"))
                if isinstance(error, dict) and error.get("message")
                else "Agent returned failed status."
            )
            failure = AgentExecutionError(
                agent=agent_type,
                step_id=step["id"],
                cause=cause,
            )
            if isinstance(error, dict) and error.get("code"):
                failure.code = str(error["code"])
            return failure_result(
                step_id=step["id"],
                step_type="agent",
                failure=failure,
                output=result.output,
            )

        return {
            "step_id": step["id"],
            "type": "agent",
            "status": result_status,
            "output": result.output,
            "confidence": result.confidence,
            "reasoning_trace": result.reasoning_trace,
            "tool_calls": [tc.model_dump() for tc in result.tool_calls],
            "agent": agent_type,
            "action": action,
        }
    # enterprise-gate: broad-except-ok reason=agent-step-boundary-returns-typed-failure-result
    except Exception as exc:
        return failure_result(
            step_id=step["id"],
            step_type="agent",
            failure=AgentExecutionError(
                agent=agent_type,
                step_id=step["id"],
                cause=str(exc),
                exception_type=type(exc).__name__,
            ),
        )


async def _execute_condition(step: dict, state: dict) -> dict[str, Any]:
    condition = step.get("condition", step.get("expression", "true"))
    context = state.get("context", {})
    result = evaluate_condition(condition, context)
    path = step.get("true_path") if result else step.get("false_path")
    return {
        "step_id": step["id"],
        "type": "condition",
        "status": "completed",
        "result": result,
        "next_path": path,
    }


async def _execute_hitl(step: dict, state: dict) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "type": "human_in_loop",
        "status": "waiting_hitl",
        "assignee_role": step.get("assignee_role", ""),
        "timeout_hours": step.get("timeout_hours", 4),
    }


def _parallel_child_step(child: Any) -> dict[str, Any]:
    if isinstance(child, dict):
        child_step = dict(child)
        child_step.setdefault("type", "agent")
        return child_step
    child_id = str(child)
    return {"id": child_id, "type": "agent", "agent": child_id}


def _parallel_failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [result for result in results if not is_success_status(result.get("status"))]


async def _execute_parallel(step: dict, state: dict) -> dict[str, Any]:
    sub_steps = step.get("steps", [])
    wait_for = str(step.get("wait_for", "all"))
    task_factories = [
        (lambda child=child: execute_step(_parallel_child_step(child), state))
        for child in sub_steps
    ]
    results = await execute_parallel(task_factories, wait_for=wait_for)
    failures = _parallel_failures(results)

    should_fail = bool(failures) if wait_for != "any" else not any(
        is_success_status(result.get("status")) for result in results
    )
    if should_fail:
        return failure_result(
            step_id=step["id"],
            step_type="parallel",
            failure=ParallelChildError(step_id=step["id"], failed_children=failures),
            output={"results": results, "failed_children": failures},
        )

    return {
        "step_id": step["id"],
        "type": "parallel",
        "status": "completed",
        "results": results,
    }


async def _execute_loop(step: dict, state: dict) -> dict[str, Any]:
    items = step.get("items", [])
    results: list[dict[str, Any]] = []
    for item in items:
        child_step = step.get(
            "step",
            {
                "id": f"{step['id']}_item",
                "type": "agent",
                "agent": f"{step['id']}_item",
            },
        )
        child_step = {**child_step, "id": f"{step['id']}_{len(results)}"}
        child_state = {**state, "loop_item": item}
        results.append(await execute_step(child_step, child_state))

    failures = _parallel_failures(results)
    if failures:
        return failure_result(
            step_id=step["id"],
            step_type="loop",
            failure=ParallelChildError(step_id=step["id"], failed_children=failures),
            output={"results": results, "failed_children": failures},
        )
    return {"step_id": step["id"], "type": "loop", "status": "completed", "results": results}


def _context_value(state: dict, key: str, default: Any = None) -> Any:
    context = state.get("context", {})
    if key in context:
        return context[key]
    trigger_payload = state.get("trigger_payload", {})
    if isinstance(trigger_payload, dict) and key in trigger_payload:
        return trigger_payload[key]
    return default


async def _execute_transform(step: dict, state: dict) -> dict[str, Any]:
    config = step.get("transform") or step.get("config") or {}
    operation = step.get("operation") or config.get("operation") or step.get("action")
    operation = str(operation or "").strip().lower()

    if operation in {"identity", "copy", "passthrough"}:
        return {
            "step_id": step["id"],
            "type": "transform",
            "status": "completed",
            "output": step.get("inputs", state.get("context", {})),
        }

    if operation == "pick":
        fields = config.get("fields") or step.get("fields") or []
        output = {field: _context_value(state, field) for field in fields}
        return {  # enterprise-gate: stub-ok reason=real-transform-output-before-relaxed-env-fallback
            "step_id": step["id"],
            "type": "transform",
            "status": "completed",  # enterprise-gate: stub-ok reason=real-transform-output-before-relaxed-env-fallback
            "output": output,
        }

    if operation == "currency_convert":
        amount_field = config.get("amount_field", "invoice_amount_usd")
        rate_field = config.get("rate_field", "exchange_rate")
        output_field = config.get("output_field", "converted_amount")
        amount = float(_context_value(state, amount_field, 0))
        rate = float(_context_value(state, rate_field, 0))
        output = {
            output_field: amount * rate,
            "amount_field": amount_field,
            "rate_field": rate_field,
        }
        return {
            "step_id": step["id"],
            "type": "transform",
            "status": "completed",  # enterprise-gate: stub-ok reason=real-transform-output-before-relaxed-env-fallback
            "output": output,
        }

    if _stub_steps_allowed():  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        return stubbed_result(  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
            step_id=step["id"],
            step_type="transform",
            code="unsupported_transform_configuration",
            message="Transform execution uses relaxed-env placeholder because no supported transform is configured.",
            operation=operation or None,
        )

    return failure_result(
        step_id=step["id"],
        step_type="transform",
        failure=UnsupportedTransformConfigError(
            step_id=step["id"],
            operation=operation or None,
        ),
    )


async def _execute_notify(step: dict, state: dict) -> dict[str, Any]:
    connector = str(step.get("connector", step.get("channel", ""))).lower()
    to = step.get("to") or step.get("target")
    subject = step.get("subject") or "AgenticOrg workflow notification"
    html = step.get("html") or step.get("message") or step.get("body") or ""

    if connector in {"email", "sendgrid", "smtp"} and to and html:
        from core.email import send_email

        if send_email(str(to), str(subject), str(html)):
            return {
                "step_id": step["id"],
                "type": "notify",
                "status": "completed",  # enterprise-gate: stub-ok reason=real-notify-side-effect
                "output": {
                    "status": "sent",  # enterprise-gate: stub-ok reason=real-notify-side-effect
                    "connector": connector,
                    "target": to,
                    "side_effect": "email_send",
                },
            }

    if _stub_steps_allowed():  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        return stubbed_result(  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
            step_id=step["id"],
            step_type="notify",
            code="notify_side_effect_not_configured",
            message="Notify execution uses relaxed-env placeholder because no delivery path is configured.",
            connector=connector,
        )

    return failure_result(
        step_id=step["id"],
        step_type="notify",
        failure=NotifySideEffectNotConfiguredError(step_id=step["id"], connector=connector),
    )


async def _execute_sub_workflow(step: dict, state: dict) -> dict[str, Any]:
    from workflows.engine import WorkflowEngine

    sub_definition = step.get("definition")

    if sub_definition is None:
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

    state_store = state.get("_state_store")
    if state_store is None:
        from workflows.state_store import WorkflowStateStore

        state_store = WorkflowStateStore()

    sub_engine = WorkflowEngine(state_store=state_store)
    sub_run_id = await sub_engine.start_run(
        sub_definition,
        trigger_payload=step.get("input", {}),
        tenant_id=state.get("tenant_id"),
    )
    result = await sub_engine.execute(sub_run_id)
    status = result.get("status", "failed")
    if status not in ALLOWED_STEP_STATUSES:
        status = "failed"

    return {
        "step_id": step["id"],
        "type": "sub_workflow",
        "status": status,
        "sub_run_id": sub_run_id,
        "output": result.get("step_results", {}),
    }


async def _execute_wait(step: dict, state: dict) -> dict[str, Any]:
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
        return {"step_id": step["id"], "type": "wait", "status": "completed"}

    try:
        from core.tasks.workflow_tasks import resume_workflow_wait

        run_id = state.get("id", "")
        resume_workflow_wait.apply_async(
            args=[run_id, step["id"]],
            eta=resume_at,
        )
    # enterprise-gate: broad-except-ok reason=wait-resume-scheduling-failure-leaves-step-waiting
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "workflow_wait_resume_schedule_failed",
            extra={"run_id": state.get("id", ""), "step_id": step["id"], "error": str(exc)},
        )

    return {
        "step_id": step["id"],
        "type": "wait",
        "status": "waiting_delay",
        "resume_at": resume_at.isoformat(),
    }


async def _execute_wait_for_event(step: dict, state: dict) -> dict[str, Any]:
    event_type = step.get("event_type", "")
    timeout_hours = step.get("timeout_hours", 48)
    match_criteria = step.get("match", {})
    run_id = state.get("id", "")
    timeout_at = datetime.now(UTC) + timedelta(hours=timeout_hours)
    trigger_payload = state.get("trigger_payload")
    if not isinstance(trigger_payload, dict):
        trigger_payload = {}
    tenant_id = (
        state.get("tenant_id")
        or trigger_payload.get("tenant_id")
        or trigger_payload.get("agenticorg:tenant_id")
    )
    workflow_run_id = (
        state.get("workflow_run_id")
        or state.get("db_workflow_run_id")
        or state.get("database_workflow_run_id")
    )

    event_wait_store = state.get("_event_wait_store")
    created_store = event_wait_store is None
    if event_wait_store is None:
        event_wait_store = WorkflowEventWaitStore()
        await event_wait_store.init()
    try:
        await event_wait_store.register(
            engine_run_id=run_id,
            step_id=step["id"],
            event_type=event_type,
            match_criteria=match_criteria,
            timeout_at=timeout_at,
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            connector=step.get("connector"),
            provider=step.get("provider"),
        )
    finally:
        if created_store:
            await event_wait_store.close()

    try:
        from core.tasks.workflow_tasks import timeout_workflow_event

        timeout_workflow_event.apply_async(
            args=[run_id, step["id"]],
            eta=timeout_at,
        )
    # enterprise-gate: broad-except-ok reason=event-timeout-scheduling-failure-leaves-durable-listener
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "workflow_event_timeout_schedule_failed",
            extra={"run_id": run_id, "step_id": step["id"], "error": str(exc)},
        )

    return {
        "step_id": step["id"],
        "type": "wait_for_event",
        "status": "waiting_event",
        "event_type": event_type,
        "timeout_at": timeout_at.isoformat(),
    }
