"""Workflow step type implementations."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from core.config import external_keys, is_relaxed_env, is_strict_runtime_env, settings
from core.marketing.approval_timeouts import timeout_policy_for_action
from core.marketing.external_writes import evaluate_marketing_external_write_result
from core.marketing.workflow_activation import EXTERNAL_WRITE_ACTIONS
from workflows.condition_evaluator import evaluate_condition
from workflows.event_waits import WorkflowEventWaitStore
from workflows.parallel_executor import execute_parallel
from workflows.step_results import (
    ALLOWED_STEP_STATUSES,
    AgentExecutionError,
    ConnectorToolConfigError,
    ConnectorToolExecutionError,
    ExternalWriteConfirmationMissingError,
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
MARKETING_AGENT_TYPES = {
    "abm_agent",
    "brand_monitor",
    "campaign_pilot",
    "competitive_intel",
    "content_factory",
    "crm_intelligence",
    "email_marketing",
    "seo_strategist",
    "social_media",
}
MARKETING_WRITE_ACTION_HINTS = (
    "activate",
    "add_to_drip",
    "launch",
    "mutate",
    "publish",
    "schedule",
    "send",
    "setup",
    "spend",
    "start_nurture",
    "update_crm",
)
CONNECTOR_TOOL_ALIASES: dict[str, tuple[str, str]] = {
    "fetch_hubspot_contacts": ("hubspot", "list_contacts"),
    "get_hubspot_contacts": ("hubspot", "list_contacts"),
    "list_hubspot_contacts": ("hubspot", "list_contacts"),
    "retrieve_hubspot_contacts": ("hubspot", "list_contacts"),
    "hubspot_contact_retrieval": ("hubspot", "list_contacts"),
    "fetch_hubspot_deals": ("hubspot", "list_deals"),
    "get_hubspot_deals": ("hubspot", "list_deals"),
    "list_hubspot_deals": ("hubspot", "list_deals"),
    "retrieve_hubspot_deals": ("hubspot", "list_deals"),
    "hubspot_deal_retrieval": ("hubspot", "list_deals"),
}


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


async def _load_workflow_agent_config(agent_id: str, tenant_id: str) -> dict[str, Any]:
    """Load stored agent defaults for workflow steps that reference a DB agent."""
    if not agent_id or not tenant_id:
        return {}
    try:
        agent_uuid = uuid.UUID(str(agent_id))
        tenant_uuid = uuid.UUID(str(tenant_id))
    except (TypeError, ValueError):
        return {}

    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.agent import Agent

    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_uuid, Agent.tenant_id == tenant_uuid)
        )
        agent = result.scalar_one_or_none()
    if agent is None or agent.status in {"deleted", "retired"}:
        return {}

    return {
        "id": str(agent.id),
        "tenant_id": str(agent.tenant_id),
        "agent_type": agent.agent_type,
        "authorized_tools": agent.authorized_tools or [],
        "prompt_variables": agent.prompt_variables or {},
        "hitl_condition": agent.hitl_condition or "",
        "output_schema": agent.output_schema,
        "llm_model": agent.llm_model,
        "cost_controls": agent.cost_controls or {},
        "system_prompt_text": agent.system_prompt_text or "",
    }


async def _load_workflow_connector_config(connector_name: str, tenant_id: str) -> dict[str, Any]:
    if not connector_name or not tenant_id:
        return {}
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except (TypeError, ValueError):
        return {}

    import json as _json

    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.connector_config import ConnectorConfig

    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tenant_uuid,
                ConnectorConfig.connector_name == connector_name,
            )
        )
        row = result.scalar_one_or_none()
    if row is None:
        return {}

    config = dict(row.config or {})
    creds = row.credentials_encrypted or {}
    if isinstance(creds, str):
        creds = _json.loads(creds)
    if isinstance(creds, dict) and "_encrypted" in creds:
        from core.crypto import decrypt_for_tenant

        creds = _json.loads(decrypt_for_tenant(creds["_encrypted"]))
    if isinstance(creds, dict):
        config.update(creds)
    return config


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _state_lookup(state: dict, *keys: str) -> Any:
    containers = [
        state,
        state.get("context") if isinstance(state.get("context"), dict) else {},
        state.get("definition") if isinstance(state.get("definition"), dict) else {},
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value is not None:
                return value
    return None


def _workflow_mode(step: dict, state: dict, output: dict[str, Any]) -> str:
    return _normalize_key(
        step.get("workflow_mode")
        or step.get("mode")
        or output.get("workflow_mode")
        or output.get("mode")
        or _state_lookup(state, "workflow_mode", "cmo_workflow_mode", "configured_mode", "mode")
    )


def _marketing_context(step: dict, state: dict, agent_type: str) -> bool:
    domain = _normalize_key(step.get("domain") or _state_lookup(state, "domain"))
    return domain == "marketing" or _normalize_key(agent_type) in MARKETING_AGENT_TYPES


def _requires_marketing_write_confirmation(
    step: dict,
    state: dict,
    *,
    agent_type: str,
    action: str,
) -> bool:
    if step.get("external_write_required") is False or step.get("requires_external_write") is False:
        return False
    if step.get("external_write_required") is True or step.get("requires_external_write") is True:
        return True
    if not _marketing_context(step, state, agent_type):
        return False
    normalized_action = _normalize_key(action)
    if normalized_action in EXTERNAL_WRITE_ACTIONS:
        return True
    return any(hint in normalized_action for hint in MARKETING_WRITE_ACTION_HINTS)


def _connector_key(step: dict, output: dict[str, Any]) -> str | None:
    value = (
        step.get("connector_key")
        or step.get("connector")
        or output.get("connector_key")
        or output.get("connector")
    )
    return str(value).strip().lower() if value else None


def _connector_tool_ref_from_step(
    step: dict,
    *,
    allow_action_tool: bool = True,
) -> tuple[str | None, str | None]:
    connector = step.get("connector") or step.get("connector_key")
    tool = step.get("tool") or step.get("tool_name")
    action = step.get("action")
    agent = step.get("agent") or step.get("agent_type")
    step_id = step.get("id")

    for value in (tool, action, agent, step_id):
        normalized = _normalize_key(value)
        if normalized in CONNECTOR_TOOL_ALIASES:
            return CONNECTOR_TOOL_ALIASES[normalized]

    for value in (tool, action):
        if isinstance(value, str) and ":" in value and not value.startswith("tool:"):
            maybe_connector, maybe_tool = value.split(":", 1)
            return _normalize_key(maybe_connector), maybe_tool.strip()

    if connector and tool:
        return _normalize_key(connector), str(tool).strip()
    if allow_action_tool and connector and action:
        return _normalize_key(connector), str(action).strip()
    return None, None


def _connector_step_inputs(step: dict, state: dict) -> dict[str, Any]:
    raw = step.get("params", step.get("arguments", step.get("inputs", {})))
    if not isinstance(raw, dict):
        return {}
    inputs = dict(raw)
    context_raw = state.get("context")
    context: dict[Any, Any] = context_raw if isinstance(context_raw, dict) else {}
    trigger_raw = state.get("trigger_payload")
    trigger: dict[Any, Any] = trigger_raw if isinstance(trigger_raw, dict) else {}
    for key, value in list(inputs.items()):
        if isinstance(value, str) and value.startswith("$"):
            lookup = value[1:]
            if lookup in context:
                inputs[key] = context[lookup]
            elif lookup in trigger:
                inputs[key] = trigger[lookup]
    return inputs


def _connector_contracts_from_state(state: dict) -> list[dict[str, Any]]:
    value = _state_lookup(
        state,
        "connector_contracts",
        "marketing_connector_contracts",
        "cmo_connector_contracts",
    )
    return value if isinstance(value, list) else []


def _external_write_decision(
    step: dict,
    state: dict,
    *,
    agent_type: str,
    action: str,
    output: dict[str, Any],
) -> dict[str, Any] | None:
    if not _requires_marketing_write_confirmation(
        step,
        state,
        agent_type=agent_type,
        action=action,
    ):
        return None

    mode = _workflow_mode(step, state, output)
    connector_key = _connector_key(step, output)
    return evaluate_marketing_external_write_result(
        _connector_contracts_from_state(state),
        connector_key=connector_key,
        action=action,
        workflow_mode=mode or "active",
        output=output,
        step=step,
        state=state,
    )


def _output_with_external_write_decision(
    output: Any,
    decision: dict[str, Any],
) -> dict[str, Any]:
    base = dict(output) if isinstance(output, dict) else {"result": output}
    base["external_write_state"] = decision.get("final_state")
    base["external_write_reason"] = decision.get("reason")
    base["external_write_next_action"] = decision.get("next_action")
    base["external_write_attempt"] = decision.get("attempt")
    base["external_write_confirmation"] = decision.get("confirmation")
    base["external_write_retry_plan"] = decision.get("retry_plan")
    base["external_write_audit"] = decision.get("audit_events") or []
    base["external_write_audit_reference"] = decision.get("audit_reference")
    base["decision_audit"] = decision.get("decision_audit")
    base["decision_audit_ref"] = decision.get("decision_audit_ref")
    if decision.get("escalation_decision") is not None:
        base["external_write_escalation_decision"] = decision.get("escalation_decision")
        base["external_write_escalation_evidence"] = decision.get("escalation_evidence")
    if decision.get("marketing_policy_decision") is not None:
        base["marketing_policy_decision"] = decision.get("marketing_policy_decision")
    return base


def _external_write_failure(
    step: dict,
    action: str,
    connector_key: str | None,
    mode: str,
    decision: dict[str, Any],
) -> ExternalWriteConfirmationMissingError:
    return ExternalWriteConfirmationMissingError(
        step_id=str(step.get("id", "")),
        action=action,
        connector=connector_key,
        mode=mode or None,
        reason=str(decision.get("reason") or "External write confirmation is missing."),
        final_state=str(decision.get("final_state") or ""),
        next_action=str(decision.get("next_action") or ""),
        audit_reference=str(decision.get("audit_reference") or ""),
        code=str(decision.get("error_code") or "external_write_confirmation_missing"),
    )


async def execute_step(step: dict, state: dict) -> dict[str, Any]:
    step_type = step.get("type", "agent")
    if step_type == "agent" and any(key in step for key in ("tool", "tool_name")):
        return await _execute_connector_tool_step(step, state)
    if step_type == "agent" and _connector_tool_ref_from_step(
        step,
        allow_action_tool=False,
    ) != (None, None):
        return await _execute_connector_tool_step(step, state)
    handlers = {
        "agent": _execute_agent,
        "condition": _execute_condition,
        "human_in_loop": _execute_hitl,
        "parallel": _execute_parallel,
        "loop": _execute_loop,
        "transform": _execute_transform,
        "connector_tool": _execute_connector_tool_step,
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


async def _execute_connector_tool_step(step: dict, state: dict) -> dict[str, Any]:
    connector, tool = _connector_tool_ref_from_step(step)
    if not connector or not tool:
        return failure_result(
            step_id=str(step.get("id", "")),
            step_type="connector_tool",
            failure=ConnectorToolConfigError(
                step_id=str(step.get("id", "")),
                connector=connector,
                tool=tool,
            ),
        )

    config = step.get("connector_config")
    if not isinstance(config, dict):
        state_config = _state_lookup(state, "connector_config", f"{connector}_connector_config")
        config = state_config if isinstance(state_config, dict) else {}
    if not config:
        config = await _load_workflow_connector_config(connector, str(state.get("tenant_id") or ""))

    from core.langgraph.tool_adapter import _execute_connector_tool

    result = await _execute_connector_tool(
        connector,
        tool,
        _connector_step_inputs(step, state),
        config,
    )
    if isinstance(result, dict) and result.get("error"):
        return failure_result(
            step_id=str(step.get("id", "")),
            step_type="connector_tool",
            failure=ConnectorToolExecutionError(
                step_id=str(step.get("id", "")),
                connector=connector,
                tool=tool,
                result=result,
            ),
            output=result,
        )
    return {
        "step_id": step["id"],
        "type": "connector_tool",
        "status": "completed",
        "output": result,
        "connector": connector,
        "tool": tool,
    }


async def _execute_agent(step: dict, state: dict) -> dict[str, Any]:
    """Execute an agent step by instantiating and running the configured agent."""
    agent_id = step.get("agent_id", "")
    tenant_id = state.get("tenant_id", "")
    stored_config = await _load_workflow_agent_config(agent_id, tenant_id)
    agent_type = step.get("agent", step.get("agent_type", "")) or stored_config.get("agent_type", "")
    action = step.get("action", "process")
    inputs = step.get("inputs", state.get("trigger_payload", {}))

    if not agent_type and not agent_id:
        return _missing_agent_result(step, action)

    if not _llm_available_for_workflow():
        return _missing_llm_result(step, agent_type, action)

    try:
        import core.agents  # noqa: F401 - triggers registration
        from core.agents.registry import AgentRegistry
        from core.schemas.messages import (
            HITLPolicy,
            TargetAgent,
            TaskAssignment,
            TaskInput,
            TaskMetadata,
        )

        config = {
            "id": stored_config.get("id") or agent_id or f"wf_agent_{step['id']}",
            "tenant_id": stored_config.get("tenant_id") or tenant_id,
            "agent_type": agent_type,
            "authorized_tools": (
                step["authorized_tools"]
                if "authorized_tools" in step
                else stored_config.get("authorized_tools", [])
            ),
            "prompt_variables": (
                step["prompt_variables"]
                if "prompt_variables" in step
                else stored_config.get("prompt_variables", {})
            ),
            "hitl_condition": (
                step["hitl_condition"]
                if "hitl_condition" in step
                else stored_config.get("hitl_condition", "")
            ),
            "output_schema": (
                step["output_schema"]
                if "output_schema" in step
                else stored_config.get("output_schema")
            ),
            "system_prompt_text": (
                step["system_prompt_text"]
                if "system_prompt_text" in step
                else stored_config.get("system_prompt_text", "")
            ),
            "llm_model": step.get("llm_model") or stored_config.get("llm_model"),
            "cost_controls": (
                step["cost_controls"]
                if "cost_controls" in step
                else stored_config.get("cost_controls")
            ),
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
        final_output = result.output
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

        if result_status == "completed":
            output = result.output if isinstance(result.output, dict) else {}
            write_decision = _external_write_decision(
                step,
                state,
                agent_type=agent_type,
                action=action,
                output=output,
            )
            if write_decision:
                augmented_output = _output_with_external_write_decision(result.output, write_decision)
                step_status = str(write_decision.get("step_status") or "failed")
                if step_status == "failed":
                    mode = _workflow_mode(step, state, output)
                    connector_key = _connector_key(step, output)
                    confirmation_failure = _external_write_failure(
                        step,
                        action,
                        connector_key,
                        mode,
                        write_decision,
                    )
                    return failure_result(
                        step_id=step["id"],
                        step_type="agent",
                        failure=confirmation_failure,
                        output=augmented_output,
                    )
                if step_status == "waiting_delay":
                    return {
                        "step_id": step["id"],
                        "type": "agent",
                        "status": "waiting_delay",
                        "output": augmented_output,
                        "resume_at": write_decision.get("resume_at"),
                        "confidence": result.confidence,
                        "reasoning_trace": result.reasoning_trace,
                        "tool_calls": [tc.model_dump() for tc in result.tool_calls],
                        "agent": agent_type,
                        "action": action,
                    }
                final_output = augmented_output

        return {
            "step_id": step["id"],
            "type": "agent",
            "status": result_status,
            "output": final_output,
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
    action = (
        step.get("approval_action")
        or step.get("action")
        or _state_lookup(state, "action", "approval_action", "blocked_action")
    )
    timeout_policy = timeout_policy_for_action(action, step) if action else None
    timeout_hours = (
        timeout_policy.get("default_sla_hours")
        if isinstance(timeout_policy, dict)
        else step.get("timeout_hours", 4)
    )
    return {
        "step_id": step["id"],
        "type": "human_in_loop",
        "status": "waiting_hitl",
        "assignee_role": step.get("assignee_role", ""),
        "timeout_hours": timeout_hours,
        "approval_action": action,
        "approval_timeout_policy": timeout_policy,
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
