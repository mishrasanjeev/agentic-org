"""Platform event envelope and catalogue."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventMetadata(BaseModel):
    retry_count: int = 0
    idempotency_key: str = ""


class PlatformEvent(BaseModel):
    """Standard event envelope for all event types."""

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
