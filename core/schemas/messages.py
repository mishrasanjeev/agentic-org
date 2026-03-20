"""Message protocol v2 — Agent <-> Orchestrator."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

class TargetAgent(BaseModel):
    agent_id: str
    agent_type: str
    agent_token: str

class TaskInput(BaseModel):
    action: str
    inputs: dict[str, Any] = {}
    context: dict[str, Any] = {}

class ExecutionPolicy(BaseModel):
    timeout_seconds: int = 120
    max_retries: int = 3
    retry_backoff: str = "exponential"
    retry_delay_seconds: int = 5
    on_timeout: str = "escalate"
    on_failure: str = "retry"

class HITLPolicy(BaseModel):
    enabled: bool = True
    threshold_expression: str = ""
    assignee_role: str = ""
    timeout_hours: int = 4
    on_hitl_timeout: str = "escalate"

class TaskMetadata(BaseModel):
    priority: str = "normal"
    idempotency_key: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "2"

class TaskAssignment(BaseModel):
    """Orchestrator -> Agent message."""
    message_id: str
    correlation_id: str
    workflow_run_id: str
    workflow_definition_id: str
    step_id: str
    step_index: int
    total_steps: int
    target_agent: TargetAgent
    task: TaskInput
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    hitl_policy: HITLPolicy = Field(default_factory=HITLPolicy)
    metadata: TaskMetadata = Field(default_factory=TaskMetadata)

class ToolCallRecord(BaseModel):
    tool_name: str
    input_hash: str = ""
    output_hash: str = ""
    status: str
    http_status: int | None = None
    latency_ms: int = 0
    idempotency_key: str = ""

class PerformanceMetrics(BaseModel):
    total_latency_ms: int = 0
    llm_tokens_used: int = 0
    llm_cost_usd: float = 0.0

class DecisionOption(BaseModel):
    id: str
    label: str
    action: str

class DecisionRequired(BaseModel):
    question: str
    options: list[DecisionOption]
    default_on_timeout: str = "defer"
    timeout_hours: int = 4

class HITLContext(BaseModel):
    summary: str = ""
    recommendation: str = ""
    recommendation_reasoning: str = ""
    supporting_data: dict[str, Any] = {}
    agent_confidence: float = 0.0

class HITLAssignee(BaseModel):
    role: str
    notify_channels: list[str] = []
    escalation_chain: list[str] = []

class HITLRequest(BaseModel):
    """Embedded in TaskResult when status=hitl_triggered."""
    hitl_id: str
    trigger_condition: str
    trigger_type: str
    decision_required: DecisionRequired
    context: HITLContext = Field(default_factory=HITLContext)
    assignee: HITLAssignee = Field(default_factory=HITLAssignee)

class TaskResult(BaseModel):
    """Agent -> Orchestrator message."""
    message_id: str
    correlation_id: str
    workflow_run_id: str
    step_id: str
    agent_id: str
    status: str  # completed|failed|hitl_triggered
    output: dict[str, Any] = {}
    confidence: float = 0.0
    reasoning_trace: list[str] = []
    tool_calls: list[ToolCallRecord] = []
    hitl_request: HITLRequest | None = None
    error: dict[str, Any] | None = None
    performance: PerformanceMetrics = Field(default_factory=PerformanceMetrics)
    completed_at: datetime = Field(default_factory=datetime.utcnow)
