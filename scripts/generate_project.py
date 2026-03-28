#!/usr/bin/env python3
"""Generate all AgenticOrg project files.

Run from repo root: python scripts/generate_project.py
"""

import json
import os
import textwrap

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def w(rel_path: str, content: str) -> None:
    full = os.path.join(BASE, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    print(f"  wrote {rel_path}")


def touch(rel_path: str) -> None:
    full = os.path.join(BASE, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "a").close()


# ─────────────────────── MODELS ───────────────────────


def gen_models():
    w(
        "core/models/__init__.py",
        '''
    """ORM models for AgenticOrg."""
    from core.models.base import BaseModel, TimestampMixin, TenantMixin
    from core.models.tenant import Tenant
    from core.models.user import User
    from core.models.agent import (
        Agent, AgentVersion, AgentLifecycleEvent,
        AgentTeam, AgentTeamMember, AgentCostLedger, ShadowComparison,
    )
    from core.models.workflow import WorkflowDefinition, WorkflowRun, StepExecution
    from core.models.tool_call import ToolCall
    from core.models.hitl import HITLQueue
    from core.models.connector import Connector
    from core.models.audit import AuditLog
    from core.models.document import Document
    from core.models.schema_registry import SchemaRegistry
    ''',
    )

    w(
        "core/models/workflow.py",
        '''
    """Workflow ORM models."""
    from __future__ import annotations
    import uuid
    from datetime import datetime
    from decimal import Decimal
    from typing import Optional
    from sqlalchemy import (
        TIMESTAMP, Boolean, ForeignKey, Index, Integer, Numeric,
        SmallInteger, String, Text, UniqueConstraint, func,
    )
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Mapped, mapped_column, relationship
    from core.models.base import BaseModel

    class WorkflowDefinition(BaseModel):
        __tablename__ = "workflow_definitions"
        __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
        name: Mapped[str] = mapped_column(String(255), nullable=False)
        version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
        description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        domain: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
        definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
        trigger_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
        trigger_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

        runs = relationship("WorkflowRun", back_populates="workflow_def")

    class WorkflowRun(BaseModel):
        __tablename__ = "workflow_runs"
        __table_args__ = (
            Index("idx_wf_runs_tenant_status", "tenant_id", "status"),
            Index("idx_wf_runs_created", "created_at"),
        )

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
        workflow_def_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False)
        status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
        trigger_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
        result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        error: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        steps_total: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
        steps_completed: Mapped[int] = mapped_column(SmallInteger, default=0)
        started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
        completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
        timeout_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

        workflow_def = relationship("WorkflowDefinition", back_populates="runs")
        steps = relationship("StepExecution", back_populates="workflow_run")

    class StepExecution(BaseModel):
        __tablename__ = "step_executions"
        __table_args__ = (Index("idx_step_exec_run", "workflow_run_id"),)

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
        workflow_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
        step_id: Mapped[str] = mapped_column(String(100), nullable=False)
        step_type: Mapped[str] = mapped_column(String(50), nullable=False)
        agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
        status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
        input: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        output: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3), nullable=True)
        reasoning_trace: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        error: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
        retry_count: Mapped[int] = mapped_column(SmallInteger, default=0)
        latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
        completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

        workflow_run = relationship("WorkflowRun", back_populates="steps", foreign_keys=[workflow_run_id])
    ''',
    )

    w(
        "core/models/tool_call.py",
        '''
    """ToolCall ORM model."""
    from __future__ import annotations
    import uuid
    from datetime import datetime
    from typing import Optional
    from sqlalchemy import TIMESTAMP, ForeignKey, Integer, SmallInteger, String, func
    from sqlalchemy.dialects.postgresql import UUID
    from sqlalchemy.orm import Mapped, mapped_column
    from core.models.base import BaseModel

    class ToolCall(BaseModel):
        __tablename__ = "tool_calls"

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
        step_exec_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
        agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
        tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
        connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
        input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        output_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        status: Mapped[str] = mapped_column(String(20), nullable=False)
        http_status: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
        error_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
        idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        llm_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        called_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    ''',
    )

    w(
        "core/models/hitl.py",
        '''
    """HITL Queue ORM model."""
    from __future__ import annotations
    import uuid
    from datetime import datetime
    from typing import Optional
    from sqlalchemy import TIMESTAMP, ForeignKey, String, Text, func
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Mapped, mapped_column
    from core.models.base import BaseModel

    class HITLQueue(BaseModel):
        __tablename__ = "hitl_queue"

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
        workflow_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
        agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
        title: Mapped[str] = mapped_column(String(500), nullable=False)
        trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
        priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
        status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
        assignee_role: Mapped[str] = mapped_column(String(100), nullable=False)
        decision_options: Mapped[dict] = mapped_column(JSONB, nullable=False)
        context: Mapped[dict] = mapped_column(JSONB, nullable=False)
        decision: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
        decision_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
        decision_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
        decision_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    ''',
    )

    w(
        "core/models/connector.py",
        '''
    """Connector ORM model."""
    from __future__ import annotations
    import uuid
    from datetime import datetime
    from typing import Optional
    from sqlalchemy import TIMESTAMP, ForeignKey, Integer, String, Text, UniqueConstraint, func
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Mapped, mapped_column
    from core.models.base import BaseModel

    class Connector(BaseModel):
        __tablename__ = "connectors"
        __table_args__ = (UniqueConstraint("tenant_id", "name"),)

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
        name: Mapped[str] = mapped_column(String(100), nullable=False)
        category: Mapped[str] = mapped_column(String(50), nullable=False)
        description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
        auth_type: Mapped[str] = mapped_column(String(50), nullable=False)
        auth_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
        secret_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        tool_functions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
        data_schema_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
        rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
        timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
        status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
        health_check_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    ''',
    )

    w(
        "core/models/audit.py",
        '''
    """Audit log ORM model — append-only with HMAC signature."""
    from __future__ import annotations
    import uuid
    from datetime import datetime
    from typing import Optional
    from sqlalchemy import TIMESTAMP, String, func
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Mapped, mapped_column
    from core.models.base import BaseModel

    class AuditLog(BaseModel):
        __tablename__ = "audit_log"

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
        event_type: Mapped[str] = mapped_column(String(100), nullable=False)
        actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
        actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
        agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
        workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
        resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
        resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
        action: Mapped[str] = mapped_column(String(100), nullable=False)
        outcome: Mapped[str] = mapped_column(String(50), nullable=False)
        details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
        signature: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
        trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    ''',
    )

    w(
        "core/models/document.py",
        '''
    """Document ORM model with pgvector embedding."""
    from __future__ import annotations
    import uuid
    from datetime import date, datetime
    from typing import Optional
    from sqlalchemy import TIMESTAMP, Date, ForeignKey, String, func
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Mapped, mapped_column
    from core.models.base import BaseModel

    class Document(BaseModel):
        __tablename__ = "documents"

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
        name: Mapped[str] = mapped_column(String(500), nullable=False)
        doc_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
        s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
        s3_key: Mapped[str] = mapped_column(String(1000), nullable=False)
        # embedding column handled via raw SQL / pgvector extension
        metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
        retention_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    ''',
    )

    w(
        "core/models/schema_registry.py",
        '''
    """Schema registry ORM model."""
    from __future__ import annotations
    import uuid
    from datetime import datetime
    from typing import Optional
    from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, Text, UniqueConstraint, func
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Mapped, mapped_column
    from core.models.base import BaseModel

    class SchemaRegistry(BaseModel):
        __tablename__ = "schema_registry"
        __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

        id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
        name: Mapped[str] = mapped_column(String(100), nullable=False)
        version: Mapped[str] = mapped_column(String(20), nullable=False, default="1")
        description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        json_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
        is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
        created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    ''',
    )

    print("[OK] Models")


# ─────────────────────── PYDANTIC SCHEMAS ───────────────────────


def gen_pydantic_schemas():
    w(
        "core/schemas/__init__.py",
        '''
    """Pydantic schemas for AgenticOrg."""
    from core.schemas.messages import TaskAssignment, TaskResult, HITLRequest
    from core.schemas.errors import ErrorCode, ErrorEnvelope
    from core.schemas.api import *
    from core.schemas.events import PlatformEvent
    ''',
    )

    w(
        "core/schemas/messages.py",
        '''
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
    ''',
    )

    w(
        "core/schemas/errors.py",
        '''
    """E-series error taxonomy — all 50 error codes."""
    from __future__ import annotations
    from datetime import datetime
    from enum import Enum
    from typing import Any, Optional
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
    ''',
    )

    w(
        "core/schemas/events.py",
        '''
    """Platform event envelope and catalogue."""
    from __future__ import annotations
    from datetime import datetime
    from typing import Any
    from pydantic import BaseModel, Field

    class EventMetadata(BaseModel):
        retry_count: int = 0
        idempotency_key: str = ""

    class PlatformEvent(BaseModel):
        """Standard event envelope for all 24 event types."""
        event_id: str
        event_type: str
        event_version: str = "1"
        tenant_id: str
        source: str
        timestamp: datetime = Field(default_factory=datetime.utcnow)
        trace_id: str = ""
        correlation_id: str = ""
        payload: dict[str, Any] = {}
        metadata: EventMetadata = Field(default_factory=EventMetadata)

    # Event type constants
    WORKFLOW_STARTED = "agenticorg.workflow.started"
    WORKFLOW_COMPLETED = "agenticorg.workflow.completed"
    WORKFLOW_FAILED = "agenticorg.workflow.failed"
    HITL_CREATED = "agenticorg.hitl.created"
    HITL_DECIDED = "agenticorg.hitl.decided"
    HITL_EXPIRED = "agenticorg.hitl.expired"
    AGENT_PAUSED = "agenticorg.agent.paused"
    AGENT_RESUMED = "agenticorg.agent.resumed"
    AGENT_PROMOTED = "agenticorg.agent.promoted"
    AGENT_SCALED = "agenticorg.agent.scaled"
    AGENT_BUDGET_WARNING = "agenticorg.agent.budget_warning"
    AGENT_COST_CAP_EXCEEDED = "agenticorg.agent.cost_cap_exceeded"
    TOOL_CALLED = "agenticorg.tool.called"
    TOOL_CAP_EXCEEDED = "agenticorg.tool.cap_exceeded"
    SECURITY_VIOLATION = "agenticorg.security.violation"
    SCHEMA_UPDATED = "agenticorg.schema.updated"
    TENANT_PROVISIONED = "agenticorg.tenant.provisioned"
    DSAR_COMPLETED = "agenticorg.dsar.completed"
    # Inbound connector events
    INVOICE_RECEIVED = "connector.oracle_fusion.invoice_received"
    EMPLOYEE_JOINED = "connector.darwinbox.employee_joined"
    EMPLOYEE_RESIGNED = "connector.darwinbox.employee_resigned"
    TICKET_CREATED = "connector.zendesk.ticket_created"
    FORM_SUBMITTED = "connector.hubspot.form_submitted"
    ALERT_TRIGGERED = "connector.pagerduty.alert_triggered"
    ''',
    )

    w(
        "core/schemas/api.py",
        '''
    """API request/response Pydantic schemas."""
    from __future__ import annotations
    from datetime import datetime
    from typing import Any, Optional
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
        system_prompt: str
        prompt_variables: dict[str, str] = {}
        authorized_tools: list[str] = []
        hitl_policy: HITLPolicyConfig
        confidence_floor: float = 0.88
        max_retries: int = 3
        output_schema: str | None = None
        initial_status: str = "shadow"
        shadow_comparison_agent: str | None = None
        shadow_min_samples: int = 10
        shadow_accuracy_floor: float = 0.95
        scaling: ScalingConfig = Field(default_factory=ScalingConfig)
        cost_controls: CostControlConfig = Field(default_factory=CostControlConfig)
        ttl_hours: int | None = None

    class AgentUpdate(BaseModel):
        name: str | None = None
        system_prompt: str | None = None
        prompt_variables: dict[str, str] | None = None
        authorized_tools: list[str] | None = None
        hitl_policy: HITLPolicyConfig | None = None
        confidence_floor: float | None = None
        llm: LLMConfig | None = None

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
        max_active_agents: int = 50
        max_agents_per_domain: dict[str, int] = {}
        max_shadow_agents: int = 10
        max_replicas_global_ceiling: int = 20
    ''',
    )

    print("[OK] Pydantic schemas")


# ─────────────────────── JSON SCHEMAS ───────────────────────


def gen_json_schemas():
    schemas = {
        "employee": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Employee",
            "type": "object",
            "required": [
                "employee_id",
                "full_name",
                "email",
                "department",
                "designation",
                "date_of_joining",
                "status",
            ],
            "properties": {
                "employee_id": {"type": "string"},
                "full_name": {"type": "string"},
                "email": {"type": "string", "format": "email"},
                "phone": {"type": "string"},
                "department": {"type": "string"},
                "designation": {"type": "string"},
                "level": {
                    "type": "string",
                    "enum": ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"],
                },
                "employment_type": {
                    "type": "string",
                    "enum": ["full_time", "contract", "intern", "advisor"],
                },
                "date_of_joining": {"type": "string", "format": "date"},
                "date_of_leaving": {"type": ["string", "null"], "format": "date"},
                "ctc": {"type": "number"},
                "variable_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "esop_units": {"type": "integer"},
                "manager_id": {"type": ["string", "null"]},
                "location": {"type": "string"},
                "work_mode": {"type": "string", "enum": ["office", "remote", "hybrid"]},
                "probation_end_date": {"type": ["string", "null"], "format": "date"},
                "status": {
                    "type": "string",
                    "enum": ["active", "probation", "on_leave", "notice_period", "terminated"],
                },
                "custom_fields": {"type": "object", "additionalProperties": True},
            },
        },
        "invoice": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Invoice",
            "type": "object",
            "required": [
                "invoice_id",
                "vendor_id",
                "invoice_date",
                "due_date",
                "line_items",
                "total",
                "status",
            ],
            "properties": {
                "invoice_id": {"type": "string"},
                "vendor_id": {"type": "string"},
                "vendor_name": {"type": "string"},
                "gstin": {"type": "string", "minLength": 15, "maxLength": 15},
                "pan": {"type": "string", "minLength": 10, "maxLength": 10},
                "invoice_date": {"type": "string", "format": "date"},
                "due_date": {"type": "string", "format": "date"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "hsn_sac": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "gst_rate": {"type": "number", "enum": [0, 5, 12, 18, 28]},
                            "igst": {"type": "number"},
                            "cgst": {"type": "number"},
                            "sgst": {"type": "number"},
                            "amount": {"type": "number"},
                        },
                    },
                },
                "subtotal": {"type": "number"},
                "gst_amount": {"type": "number"},
                "tds_amount": {"type": "number"},
                "total": {"type": "number"},
                "currency": {"type": "string"},
                "po_reference": {"type": ["string", "null"]},
                "grn_reference": {"type": ["string", "null"]},
                "bank_details": {
                    "type": "object",
                    "properties": {
                        "account_no": {"type": "string"},
                        "ifsc": {"type": "string"},
                        "bank_name": {"type": "string"},
                    },
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "matched", "mismatch", "approved", "paid", "disputed"],
                },
                "agent_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "custom_fields": {"type": "object", "additionalProperties": True},
            },
        },
        "contract": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Contract",
            "type": "object",
            "required": [
                "contract_id",
                "title",
                "counterparty",
                "contract_type",
                "start_date",
                "end_date",
                "status",
            ],
            "properties": {
                "contract_id": {"type": "string"},
                "title": {"type": "string"},
                "counterparty": {"type": "string"},
                "contract_type": {
                    "type": "string",
                    "enum": ["MSA", "NDA", "SLA", "SOW", "lease", "employment", "purchase"],
                },
                "value": {"type": "number"},
                "currency": {"type": "string"},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "auto_renewal": {"type": "boolean"},
                "notice_period_days": {"type": "number"},
                "key_obligations": {"type": "array", "items": {"type": "string"}},
                "sla_terms": {"type": "object"},
                "penalty_clauses": {"type": "array", "items": {"type": "string"}},
                "governing_law": {"type": "string"},
                "non_standard_clauses": {"type": "array", "items": {"type": "string"}},
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "expiring", "expired", "terminated"],
                },
                "custom_fields": {"type": "object", "additionalProperties": True},
            },
        },
        "job_requisition": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Job Requisition",
            "type": "object",
            "required": ["job_id", "title", "department", "hiring_manager_id", "status"],
            "properties": {
                "job_id": {"type": "string"},
                "title": {"type": "string"},
                "department": {"type": "string"},
                "hiring_manager_id": {"type": "string"},
                "level": {"type": "string"},
                "location": {"type": "array", "items": {"type": "string"}},
                "employment_type": {"type": "string", "enum": ["full_time", "contract", "intern"]},
                "ctc_band": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number"},
                        "max": {"type": "number"},
                        "currency": {"type": "string"},
                    },
                },
                "must_have": {"type": "array", "items": {"type": "string"}},
                "nice_to_have": {"type": "array", "items": {"type": "string"}},
                "jd_text": {"type": "string"},
                "open_date": {"type": "string", "format": "date"},
                "target_close_date": {"type": "string", "format": "date"},
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "on_hold", "filled", "cancelled"],
                },
            },
        },
        "campaign": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Campaign",
            "type": "object",
            "required": ["campaign_id", "name", "channel", "status"],
            "properties": {
                "campaign_id": {"type": "string"},
                "name": {"type": "string"},
                "channel": {
                    "type": "string",
                    "enum": ["google", "meta", "linkedin", "email", "organic"],
                },
                "objective": {
                    "type": "string",
                    "enum": ["awareness", "consideration", "conversion", "retention"],
                },
                "budget_total": {"type": "number"},
                "budget_spent": {"type": "number"},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "target_audience": {
                    "type": "object",
                    "properties": {
                        "industries": {"type": "array", "items": {"type": "string"}},
                        "company_size": {"type": "string"},
                        "seniority": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "metrics": {
                    "type": "object",
                    "properties": {
                        "impressions": {"type": "number"},
                        "clicks": {"type": "number"},
                        "conversions": {"type": "number"},
                        "roas": {"type": "number"},
                    },
                },
                "status": {"type": "string", "enum": ["draft", "active", "paused", "completed"]},
                "custom_fields": {"type": "object", "additionalProperties": True},
            },
        },
        "vendor": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Vendor",
            "type": "object",
            "required": ["vendor_id", "name", "status"],
            "properties": {
                "vendor_id": {"type": "string"},
                "name": {"type": "string"},
                "gstin": {"type": "string"},
                "pan": {"type": "string"},
                "bank": {
                    "type": "object",
                    "properties": {"account": {"type": "string"}, "ifsc": {"type": "string"}},
                },
                "risk_score": {"type": "number", "minimum": 0, "maximum": 10},
                "sanctions_clear": {"type": "boolean"},
                "sanctions_checked_at": {"type": "string", "format": "date-time"},
                "category": {"type": "string"},
                "approved_spend_limit": {"type": "number"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "approved", "blacklisted", "suspended"],
                },
            },
        },
        "lead": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Lead",
            "type": "object",
            "required": ["lead_id", "email", "status"],
            "properties": {
                "lead_id": {"type": "string"},
                "email": {"type": "string", "format": "email"},
                "name": {"type": "string"},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "industry": {"type": "string"},
                "company_size": {"type": "string"},
                "source": {"type": "string"},
                "lead_score": {"type": "number", "minimum": 0, "maximum": 100},
                "lifecycle_stage": {"type": "string"},
                "intent_signals": {"type": "array", "items": {"type": "string"}},
                "assigned_to": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["new", "contacted", "qualified", "mql", "sql", "won", "lost"],
                },
            },
        },
        "ticket": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Support Ticket",
            "type": "object",
            "required": ["ticket_id", "channel", "priority", "status"],
            "properties": {
                "ticket_id": {"type": "string"},
                "channel": {
                    "type": "string",
                    "enum": ["email", "chat", "phone", "portal", "social"],
                },
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "category": {"type": "string"},
                "customer_id": {"type": "string"},
                "customer_tier": {"type": "string"},
                "sentiment_score": {"type": "number", "minimum": -1, "maximum": 1},
                "classification": {"type": "string", "enum": ["L1", "L2", "L3"]},
                "assigned_to": {"type": ["string", "null"]},
                "status": {
                    "type": "string",
                    "enum": ["open", "pending", "escalated", "resolved", "closed"],
                },
                "csat_score": {"type": ["number", "null"]},
            },
        },
        "incident": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "IT Incident",
            "type": "object",
            "required": ["incident_id", "severity", "service", "title", "status"],
            "properties": {
                "incident_id": {"type": "string"},
                "severity": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
                "service": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "runbook_ref": {"type": ["string", "null"]},
                "assigned_team": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["triggered", "acknowledged", "resolved", "postmortem"],
                },
                "triggered_at": {"type": "string", "format": "date-time"},
                "resolved_at": {"type": ["string", "null"], "format": "date-time"},
                "rca": {"type": ["string", "null"]},
            },
        },
        "payment": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Payment",
            "type": "object",
            "required": ["payment_id", "amount", "currency", "method", "status"],
            "properties": {
                "payment_id": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": ["neft", "rtgs", "imps", "upi", "card", "wallet"],
                },
                "beneficiary": {
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "ifsc": {"type": "string"},
                        "name": {"type": "string"},
                    },
                },
                "reference_number": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["queued", "processing", "completed", "failed", "reversed"],
                },
                "executed_at": {"type": ["string", "null"], "format": "date-time"},
                "error_code": {"type": ["string", "null"]},
            },
        },
        "journal_entry": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "GL Journal Entry",
            "type": "object",
            "required": [
                "entry_id",
                "period",
                "posting_date",
                "debit_account",
                "credit_account",
                "amount",
                "status",
            ],
            "properties": {
                "entry_id": {"type": "string"},
                "period": {"type": "string"},
                "posting_date": {"type": "string", "format": "date"},
                "debit_account": {"type": "string"},
                "credit_account": {"type": "string"},
                "amount": {"type": "number"},
                "cost_center": {"type": "string"},
                "narration": {"type": "string"},
                "reference_doc": {"type": "string"},
                "posted_by": {"type": "string"},
                "status": {"type": "string", "enum": ["draft", "posted", "reversed"]},
                "idempotency_key": {"type": "string"},
            },
        },
        "asset": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Fixed Asset",
            "type": "object",
            "required": ["asset_id", "name", "category", "purchase_date", "cost", "status"],
            "properties": {
                "asset_id": {"type": "string"},
                "name": {"type": "string"},
                "category": {"type": "string"},
                "location": {"type": "string"},
                "purchase_date": {"type": "string", "format": "date"},
                "cost": {"type": "number"},
                "useful_life_years": {"type": "number"},
                "depreciation_method": {"type": "string", "enum": ["slm", "wdv"]},
                "book_value": {"type": "number"},
                "accumulated_depreciation": {"type": "number"},
                "disposal_date": {"type": ["string", "null"], "format": "date"},
                "status": {"type": "string", "enum": ["active", "disposed", "fully_depreciated"]},
            },
        },
        "payroll_run": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Payroll Run",
            "type": "object",
            "required": ["run_id", "period", "run_date", "employee_count", "status"],
            "properties": {
                "run_id": {"type": "string"},
                "period": {"type": "string"},
                "run_date": {"type": "string", "format": "date"},
                "employee_count": {"type": "integer"},
                "total_gross": {"type": "number"},
                "total_deductions": {"type": "number"},
                "total_net": {"type": "number"},
                "pf_total": {"type": "number"},
                "esi_total": {"type": "number"},
                "tds_total": {"type": "number"},
                "status": {
                    "type": "string",
                    "enum": ["draft", "computed", "approved", "disbursed"],
                },
                "approved_by": {"type": ["string", "null"]},
                "disbursed_at": {"type": ["string", "null"], "format": "date-time"},
            },
        },
        "training_record": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Training Record",
            "type": "object",
            "required": ["record_id", "employee_id", "course_name", "completion_status"],
            "properties": {
                "record_id": {"type": "string"},
                "employee_id": {"type": "string"},
                "course_name": {"type": "string"},
                "provider": {"type": "string"},
                "course_type": {
                    "type": "string",
                    "enum": ["mandatory", "optional", "certification"],
                },
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "completion_status": {
                    "type": "string",
                    "enum": ["enrolled", "in_progress", "completed", "expired", "failed"],
                },
                "score": {"type": ["number", "null"]},
                "certificate_url": {"type": ["string", "null"]},
                "cost": {"type": "number"},
            },
        },
        "compliance_filing": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Compliance Filing",
            "type": "object",
            "required": ["filing_id", "regulation", "filing_type", "period", "due_date", "status"],
            "properties": {
                "filing_id": {"type": "string"},
                "regulation": {
                    "type": "string",
                    "enum": ["GST", "TDS", "MCA", "EPFO", "SEBI", "RBI", "Labour"],
                },
                "filing_type": {"type": "string"},
                "period": {"type": "string"},
                "due_date": {"type": "string", "format": "date"},
                "filed_date": {"type": ["string", "null"], "format": "date"},
                "filed_by": {"type": ["string", "null"]},
                "acknowledgement_number": {"type": ["string", "null"]},
                "late_fee": {"type": "number"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "draft_ready", "filed", "late", "notice_received"],
                },
                "attachment_s3_key": {"type": ["string", "null"]},
            },
        },
        "order": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Sales Order",
            "type": "object",
            "required": ["order_id", "type", "customer_id", "line_items", "total"],
            "properties": {
                "order_id": {"type": "string"},
                "type": {"type": "string", "enum": ["sales", "purchase"]},
                "customer_id": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "amount": {"type": "number"},
                        },
                    },
                },
                "subtotal": {"type": "number"},
                "tax": {"type": "number"},
                "total": {"type": "number"},
                "payment_status": {
                    "type": "string",
                    "enum": ["unpaid", "partial", "paid", "refunded"],
                },
                "fulfilment_status": {
                    "type": "string",
                    "enum": ["pending", "processing", "shipped", "delivered", "cancelled"],
                },
            },
        },
        "product": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Product Catalogue",
            "type": "object",
            "required": ["product_id", "name", "sku", "price", "status"],
            "properties": {
                "product_id": {"type": "string"},
                "name": {"type": "string"},
                "sku": {"type": "string"},
                "category": {"type": "string"},
                "price": {"type": "number"},
                "tax_rate": {"type": "number"},
                "inventory_qty": {"type": "integer"},
                "unit": {"type": "string"},
                "hsn_sac_code": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "discontinued", "draft"]},
                "attributes": {"type": "object", "additionalProperties": True},
            },
        },
        "custom_fields_extension": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Custom Fields Extension",
            "description": "Append to any schema for org-specific fields without breaking the base schema version.",
            "type": "object",
            "additionalProperties": True,
        },
    }
    for name, schema in schemas.items():
        path = os.path.join(BASE, "schemas", f"{name}.schema.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2)
        print(f"  wrote schemas/{name}.schema.json")
    print("[OK] JSON Schemas")


# ─────────────────────── RUN ALL ───────────────────────

if __name__ == "__main__":
    print("Generating AgenticOrg project files...")
    gen_models()
    gen_pydantic_schemas()
    gen_json_schemas()
    print("Done with batch 1!")
