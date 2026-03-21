"""E-series error taxonomy — all 50 error codes."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorSeverity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"

class ErrorCode(str, Enum):
    # Tool errors (E1xxx)
    TOOL_CALL_FAILED = "E1001"
    TOOL_TIMEOUT = "E1002"
    TOOL_RATE_LIMIT = "E1003"
    TOOL_AUTH_FAILED = "E1004"
    TOOL_NOT_FOUND = "E1005"
    TOOL_INVALID_RESPONSE = "E1006"
    TOOL_SCOPE_DENIED = "E1007"
    TOOL_CAP_EXCEEDED = "E1008"
    CONNECTOR_UNAVAILABLE = "E1009"
    DUPLICATE_TOOL_CALL = "E1010"
    # Validation errors (E2xxx)
    SCHEMA_VALIDATION_FAILED = "E2001"
    REQUIRED_FIELD_MISSING = "E2002"
    INVALID_FIELD_TYPE = "E2003"
    FIELD_OUT_OF_RANGE = "E2004"
    GSTIN_INVALID = "E2005"
    DUPLICATE_DETECTED = "E2006"
    PO_NOT_FOUND = "E2007"
    BUDGET_EXCEEDED = "E2008"
    CONFIDENCE_BELOW_FLOOR = "E2009"
    AMBIGUOUS_MATCH = "E2010"
    # Workflow errors (E3xxx)
    HITL_TIMEOUT = "E3001"
    HITL_REJECTED = "E3002"
    WORKFLOW_INVALID = "E3003"
    STEP_DEPENDENCY_UNRESOLVED = "E3004"
    WORKFLOW_TIMEOUT = "E3005"
    CIRCULAR_DEPENDENCY = "E3006"
    MAX_RETRIES_EXCEEDED = "E3007"
    AGENT_UNAVAILABLE = "E3008"
    STATE_CORRUPTION = "E3009"
    PARALLEL_STEP_FAILED = "E3010"
    # Auth errors (E4xxx)
    TOKEN_EXPIRED = "E4001"
    TOKEN_INVALID_SIGNATURE = "E4002"
    INSUFFICIENT_SCOPE = "E4003"
    TENANT_MISMATCH = "E4004"
    MFA_REQUIRED = "E4005"
    # LLM errors (E5xxx)
    LLM_API_ERROR = "E5001"
    LLM_CONTEXT_OVERFLOW = "E5002"
    LLM_REFUSAL = "E5003"
    LLM_OUTPUT_UNPARSEABLE = "E5004"
    LLM_HALLUCINATION_DETECTED = "E5005"

ERROR_META: dict[str, dict] = {
    "E1001": {"name": "TOOL_CALL_FAILED", "severity": "error", "retryable": True, "max_retries": 3, "escalate_after_retries": True},
    "E1002": {"name": "TOOL_TIMEOUT", "severity": "error", "retryable": True, "max_retries": 3, "escalate_after_retries": True},
    "E1003": {"name": "TOOL_RATE_LIMIT", "severity": "warn", "retryable": True, "max_retries": 1, "escalate_after_retries": False},
    "E1004": {"name": "TOOL_AUTH_FAILED", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E1005": {"name": "TOOL_NOT_FOUND", "severity": "error", "retryable": False, "escalate_after_retries": True},
    "E1006": {"name": "TOOL_INVALID_RESPONSE", "severity": "error", "retryable": True, "max_retries": 1, "escalate_after_retries": True},
    "E1007": {"name": "TOOL_SCOPE_DENIED", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E1008": {"name": "TOOL_CAP_EXCEEDED", "severity": "warn", "retryable": False, "escalate_after_retries": True},
    "E1009": {"name": "CONNECTOR_UNAVAILABLE", "severity": "error", "retryable": True, "max_retries": 3, "escalate_after_retries": True},
    "E1010": {"name": "DUPLICATE_TOOL_CALL", "severity": "info", "retryable": False, "escalate_after_retries": False},
    "E2001": {"name": "SCHEMA_VALIDATION_FAILED", "severity": "error", "retryable": False, "escalate_after_retries": False},
    "E2002": {"name": "REQUIRED_FIELD_MISSING", "severity": "error", "retryable": False, "escalate_after_retries": False},
    "E2003": {"name": "INVALID_FIELD_TYPE", "severity": "error", "retryable": False, "escalate_after_retries": False},
    "E2004": {"name": "FIELD_OUT_OF_RANGE", "severity": "error", "retryable": False, "escalate_after_retries": False},
    "E2005": {"name": "GSTIN_INVALID", "severity": "warn", "retryable": False, "escalate_after_retries": False},
    "E2006": {"name": "DUPLICATE_DETECTED", "severity": "info", "retryable": False, "escalate_after_retries": False},
    "E2007": {"name": "PO_NOT_FOUND", "severity": "error", "retryable": False, "escalate_after_retries": True},
    "E2008": {"name": "BUDGET_EXCEEDED", "severity": "warn", "retryable": False, "escalate_after_retries": True},
    "E2009": {"name": "CONFIDENCE_BELOW_FLOOR", "severity": "info", "retryable": False, "escalate_after_retries": True},
    "E2010": {"name": "AMBIGUOUS_MATCH", "severity": "warn", "retryable": False, "escalate_after_retries": True},
    "E3001": {"name": "HITL_TIMEOUT", "severity": "warn", "retryable": False, "escalate_after_retries": True},
    "E3002": {"name": "HITL_REJECTED", "severity": "info", "retryable": False, "escalate_after_retries": False},
    "E3003": {"name": "WORKFLOW_INVALID", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E3004": {"name": "STEP_DEPENDENCY_UNRESOLVED", "severity": "error", "retryable": False, "escalate_after_retries": True},
    "E3005": {"name": "WORKFLOW_TIMEOUT", "severity": "error", "retryable": False, "escalate_after_retries": True},
    "E3006": {"name": "CIRCULAR_DEPENDENCY", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E3007": {"name": "MAX_RETRIES_EXCEEDED", "severity": "error", "retryable": False, "escalate_after_retries": True},
    "E3008": {"name": "AGENT_UNAVAILABLE", "severity": "error", "retryable": True, "max_retries": 3, "escalate_after_retries": True},
    "E3009": {"name": "STATE_CORRUPTION", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E3010": {"name": "PARALLEL_STEP_FAILED", "severity": "error", "retryable": False, "escalate_after_retries": False},
    "E4001": {"name": "TOKEN_EXPIRED", "severity": "warn", "retryable": True, "max_retries": 1, "escalate_after_retries": False},
    "E4002": {"name": "TOKEN_INVALID_SIGNATURE", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E4003": {"name": "INSUFFICIENT_SCOPE", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E4004": {"name": "TENANT_MISMATCH", "severity": "critical", "retryable": False, "escalate_after_retries": True},
    "E4005": {"name": "MFA_REQUIRED", "severity": "info", "retryable": False, "escalate_after_retries": False},
    "E5001": {"name": "LLM_API_ERROR", "severity": "error", "retryable": True, "max_retries": 3, "escalate_after_retries": True},
    "E5002": {"name": "LLM_CONTEXT_OVERFLOW", "severity": "warn", "retryable": True, "max_retries": 1, "escalate_after_retries": False},
    "E5003": {"name": "LLM_REFUSAL", "severity": "warn", "retryable": False, "escalate_after_retries": True},
    "E5004": {"name": "LLM_OUTPUT_UNPARSEABLE", "severity": "error", "retryable": True, "max_retries": 1, "escalate_after_retries": True},
    "E5005": {"name": "LLM_HALLUCINATION_DETECTED", "severity": "critical", "retryable": False, "escalate_after_retries": True},
}

class ErrorContext(BaseModel):
    agent_id: str | None = None
    workflow_run_id: str | None = None
    step_id: str | None = None

class ErrorDetail(BaseModel):
    code: str
    series: str = ""
    name: str = ""
    message: str
    severity: str = "error"
    retryable: bool = False
    retry_after_seconds: int | None = None
    escalate: bool = False
    details: dict[str, Any] = {}
    context: ErrorContext = Field(default_factory=ErrorContext)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    trace_id: str = ""

class ErrorEnvelope(BaseModel):
    """Standard error response envelope."""
    error: ErrorDetail

def make_error(code: ErrorCode, message: str, **kwargs) -> ErrorEnvelope:
    meta = ERROR_META.get(code.value, {})
    return ErrorEnvelope(error=ErrorDetail(
        code=code.value,
        series=code.value[:2] + "xxx",
        name=meta.get("name", ""),
        message=message,
        severity=meta.get("severity", "error"),
        retryable=meta.get("retryable", False),
        escalate=meta.get("escalate_after_retries", False),
        **kwargs,
    ))
