"""API request/response Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ── Agent schemas ──


class LLMConfig(BaseModel):
    model: str = "claude-3-5-sonnet-20241022"
    fallback_model: str = "gpt-4o-2024-11-20"
    temperature: float = 0.1
    context_strategy: str = "sliding_16k"


class HITLPolicyConfig(BaseModel):
    condition: str
    assignee_role: str = "cfo"
    timeout_hours: int = 4
    on_timeout: str = "escalate"
    escalation_chain: list[str] = []


class ScalingConfig(BaseModel):
    min_replicas: int = 1
    max_replicas: int = 5
    scale_metric: str = "queue_depth"
    scale_up_threshold: int = 50
    scale_down_threshold: int = 5
    cooldown_seconds: int = 120


class CostControlConfig(BaseModel):
    daily_token_budget: int = 500_000
    monthly_cost_cap_usd: float = 200.0
    on_budget_exceeded: str = "pause_and_alert"


class AgentCreate(BaseModel):
    name: str
    agent_type: str
    domain: str
    llm: LLMConfig = Field(default_factory=LLMConfig)
    system_prompt: str = ""
    system_prompt_text: str | None = None
    prompt_variables: dict[str, str] = {}
    authorized_tools: list[str] = []
    hitl_policy: HITLPolicyConfig = Field(
        default_factory=lambda: HITLPolicyConfig(condition="confidence < 0.88")
    )
    confidence_floor: float = 0.88
    max_retries: int = 3
    output_schema: str | None = None
    initial_status: str = "shadow"
    shadow_comparison_agent: str | None = None
    shadow_min_samples: int = 100
    shadow_accuracy_floor: float = 0.95
    scaling: ScalingConfig = Field(default_factory=ScalingConfig)
    cost_controls: CostControlConfig = Field(default_factory=CostControlConfig)
    ttl_hours: int | None = None
    # Virtual employee persona fields
    employee_name: str | None = None
    avatar_url: str | None = None
    designation: str | None = None
    specialization: str | None = None
    routing_filter: dict[str, str] = {}
    parent_agent_id: str | None = None
    reporting_to: str | None = None
    org_level: int | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    system_prompt_text: str | None = None
    prompt_variables: dict[str, str] | None = None
    authorized_tools: list[str] | None = None
    hitl_policy: HITLPolicyConfig | None = None
    confidence_floor: float | None = None
    llm: LLMConfig | None = None
    # Virtual employee persona fields
    employee_name: str | None = None
    avatar_url: str | None = None
    designation: str | None = None
    specialization: str | None = None
    routing_filter: dict[str, str] | None = None
    parent_agent_id: str | None = None
    reporting_to: str | None = None
    org_level: int | None = None
    change_reason: str | None = None


class AgentResponse(BaseModel):
    id: UUID
    name: str
    agent_type: str
    domain: str
    status: str
    version: str
    confidence_floor: float
    shadow_sample_count: int = 0
    shadow_accuracy_current: float | None = None
    created_at: datetime


class AgentCloneRequest(BaseModel):
    name: str
    agent_type: str
    overrides: dict[str, Any] = {}
    initial_status: str = "shadow"
    shadow_comparison_agent: str | None = None


# ── Workflow schemas ──


class WorkflowCreate(BaseModel):
    name: str
    version: str = "1.0"
    description: str | None = None
    domain: str | None = None
    definition: dict[str, Any]
    trigger_type: str | None = None
    trigger_config: dict[str, Any] | None = None


class WorkflowRunTrigger(BaseModel):
    payload: dict[str, Any] = {}


class WorkflowResponse(BaseModel):
    id: UUID
    name: str
    version: str
    is_active: bool
    trigger_type: str | None
    created_at: datetime


# ── HITL schemas ──


class HITLDecision(BaseModel):
    decision: str  # approve|reject|defer
    notes: str = ""


class HITLItemResponse(BaseModel):
    id: UUID
    title: str
    trigger_type: str
    priority: str
    status: str
    assignee_role: str
    decision_options: dict[str, Any]
    context: dict[str, Any]
    expires_at: datetime
    created_at: datetime


# ── Connector schemas ──


class ConnectorCreate(BaseModel):
    name: str
    category: str
    base_url: str | None = None
    auth_type: str
    auth_config: dict[str, Any] = {}
    secret_ref: str | None = None
    tool_functions: list[dict[str, Any]] = []
    data_schema_ref: str | None = None
    rate_limit_rpm: int = 60


# ── Schema registry ──


class SchemaCreate(BaseModel):
    name: str
    version: str = "1"
    description: str | None = None
    json_schema: dict[str, Any]
    is_default: bool = False


# ── DSAR ──


class DSARRequest(BaseModel):
    subject_email: str
    request_type: str = "access"  # access|erase|export


# ── Pagination ──


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int = 1
    per_page: int = 20
    pages: int = 1


# ── Fleet limits ──


class FleetLimits(BaseModel):
    max_active_agents: int = 35
    max_agents_per_domain: dict[str, int] = {}
    max_shadow_agents: int = 10
    max_replicas_global_ceiling: int = 20


# ── Prompt template schemas ──


class PromptTemplateCreate(BaseModel):
    name: str
    agent_type: str
    domain: str
    template_text: str
    variables: list[dict[str, str]] = []
    description: str | None = None


class PromptTemplateUpdate(BaseModel):
    name: str | None = None
    template_text: str | None = None
    variables: list[dict[str, str]] | None = None
    description: str | None = None


class PromptTemplateResponse(BaseModel):
    id: UUID
    name: str
    agent_type: str
    domain: str
    template_text: str
    variables: list[dict[str, str]]
    description: str | None
    is_builtin: bool
    is_active: bool
    created_at: datetime


class PromptEditHistoryResponse(BaseModel):
    id: UUID
    agent_id: UUID
    edited_by: UUID | None
    prompt_before: str | None
    prompt_after: str
    change_reason: str | None
    created_at: datetime
