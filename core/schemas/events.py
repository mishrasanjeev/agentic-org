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
WORKFLOW_STARTED = "agentflow.workflow.started"
WORKFLOW_COMPLETED = "agentflow.workflow.completed"
WORKFLOW_FAILED = "agentflow.workflow.failed"
HITL_CREATED = "agentflow.hitl.created"
HITL_DECIDED = "agentflow.hitl.decided"
HITL_EXPIRED = "agentflow.hitl.expired"
AGENT_PAUSED = "agentflow.agent.paused"
AGENT_RESUMED = "agentflow.agent.resumed"
AGENT_PROMOTED = "agentflow.agent.promoted"
AGENT_SCALED = "agentflow.agent.scaled"
AGENT_BUDGET_WARNING = "agentflow.agent.budget_warning"
AGENT_COST_CAP_EXCEEDED = "agentflow.agent.cost_cap_exceeded"
TOOL_CALLED = "agentflow.tool.called"
TOOL_CAP_EXCEEDED = "agentflow.tool.cap_exceeded"
SECURITY_VIOLATION = "agentflow.security.violation"
SCHEMA_UPDATED = "agentflow.schema.updated"
TENANT_PROVISIONED = "agentflow.tenant.provisioned"
DSAR_COMPLETED = "agentflow.dsar.completed"
# Inbound connector events
INVOICE_RECEIVED = "connector.oracle_fusion.invoice_received"
EMPLOYEE_JOINED = "connector.darwinbox.employee_joined"
EMPLOYEE_RESIGNED = "connector.darwinbox.employee_resigned"
TICKET_CREATED = "connector.zendesk.ticket_created"
FORM_SUBMITTED = "connector.hubspot.form_submitted"
ALERT_TRIGGERED = "connector.pagerduty.alert_triggered"
