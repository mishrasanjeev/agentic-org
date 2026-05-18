"""Shared workflow step result contracts and failure helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALLOWED_STEP_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "skipped",
        "stubbed",  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        "waiting_hitl",
        "waiting_delay",
        "waiting_event",
        "timed_out",
        "cancelled",
        "compensated",
    }
)

SUCCESSFUL_STEP_STATUSES = frozenset({"completed"})
PAUSED_STEP_STATUSES = frozenset({"waiting_hitl", "waiting_delay", "waiting_event"})
FAILED_STEP_STATUSES = frozenset({"failed", "cancelled"})


@dataclass(slots=True)
class WorkflowStepError(Exception):
    """Typed workflow step failure with a stable machine-readable code."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def to_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return error


class MissingAgentConfigError(WorkflowStepError):
    def __init__(self, *, step_id: str) -> None:
        super().__init__(
            "missing_agent_config",
            "Agent step is missing an agent or agent_id configuration.",
            {"step_id": step_id},
        )


class MissingLLMProviderConfigError(WorkflowStepError):
    def __init__(self, *, agent: str, step_id: str) -> None:
        super().__init__(
            "missing_llm_provider_config",
            "Agent step cannot run because no usable LLM/provider configuration is available.",
            {"agent": agent, "step_id": step_id},
        )


class AgentExecutionError(WorkflowStepError):
    def __init__(self, *, agent: str, step_id: str, cause: str, exception_type: str | None = None) -> None:
        details: dict[str, Any] = {"agent": agent, "step_id": step_id, "cause": cause}
        if exception_type:
            details["exception_type"] = exception_type
        super().__init__(
            "agent_execution_failed",
            "Agent execution failed.",
            details,
        )


class UnsupportedTransformConfigError(WorkflowStepError):
    def __init__(self, *, step_id: str, operation: str | None = None) -> None:
        details: dict[str, Any] = {"step_id": step_id}
        if operation:
            details["operation"] = operation
        super().__init__(
            "unsupported_transform_configuration",
            "Transform step is missing a supported transform configuration.",
            details,
        )


class NotifySideEffectNotConfiguredError(WorkflowStepError):
    def __init__(self, *, step_id: str, connector: str) -> None:
        super().__init__(
            "notify_side_effect_not_configured",
            "Notify step is missing a configured delivery path.",
            {"step_id": step_id, "connector": connector},
        )


class ParallelChildError(WorkflowStepError):
    def __init__(self, *, step_id: str, failed_children: list[dict[str, Any]]) -> None:
        super().__init__(
            "parallel_child_failed",
            "One or more parallel child steps failed.",
            {"step_id": step_id, "failed_children": failed_children},
        )


class UnknownStepStatusError(WorkflowStepError):
    def __init__(self, *, step_id: str, status: str | None) -> None:
        super().__init__(
            "unknown_step_status",
            "Workflow step returned an unsupported status.",
            {"step_id": step_id, "status": status},
        )


def failure_result(
    *,
    step_id: str,
    step_type: str,
    failure: WorkflowStepError,
    output: Any | None = None,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "type": step_type,
        "status": "failed",
        "output": output,
        "error": failure.to_error(),
    }


# enterprise-gate: stub-ok reason=relaxed-env-only-not-production
def stubbed_result(
    *,
    step_id: str,
    step_type: str,
    code: str,
    message: str,
    output: Any | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "step_id": step_id,
        "type": step_type,
        "status": "stubbed",  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        "stubbed": True,  # enterprise-gate: stub-ok reason=relaxed-env-only-not-production
        "code": code,
        "reason": message,
        "output": output if output is not None else {},
    }
    result.update(extra)
    return result


def exception_child_result(exc: BaseException, *, child_id: str | None = None) -> dict[str, Any]:
    return {
        "step_id": child_id or "",
        "status": "failed",
        "output": None,
        "error": {
            "code": "parallel_child_exception",
            "message": "Parallel child raised an exception.",
            "details": {
                "exception_type": type(exc).__name__,
                "cause": str(exc),
            },
        },
    }


def normalize_child_result(value: Any, *, child_id: str | None = None) -> dict[str, Any]:
    if isinstance(value, BaseException):
        return exception_child_result(value, child_id=child_id)
    if isinstance(value, dict):
        normalized = dict(value)
        normalized.setdefault("step_id", child_id or normalized.get("step_id", ""))
        status = normalized.get("status")
        if status not in ALLOWED_STEP_STATUSES:
            return failure_result(
                step_id=str(normalized.get("step_id") or child_id or ""),
                step_type=str(normalized.get("type") or "parallel_child"),
                failure=UnknownStepStatusError(
                    step_id=str(normalized.get("step_id") or child_id or ""),
                    status=str(status) if status is not None else None,
                ),
                output=normalized,
            )
        normalized.setdefault("output", {})
        return normalized
    return {
        "step_id": child_id or "",
        "status": "completed",
        "output": value,
    }


def is_success_status(status: str | None) -> bool:
    return status in SUCCESSFUL_STEP_STATUSES


def is_failure_status(status: str | None) -> bool:
    return status in FAILED_STEP_STATUSES
