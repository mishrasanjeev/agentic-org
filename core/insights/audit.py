# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Signed ``audit_log`` rows for Insights: every export and every change to the reporting settings.

The same row shape and signature as ``core/spend/audit.py::audit_entry``,
with the event type given whole (``insights.export``,
``config.reporting.write``). Details carry the metric key, format, filters,
counts and settings values only, never the exported figures.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from core.config import settings
from core.spend.audit import jsonable


def audit_entry(
    tenant_id: uuid.UUID,
    *,
    event_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any],
    now: datetime | None = None,
) -> Any:
    """One signed ``audit_log`` row (not added to any session)."""
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record
    from observability import tracing

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": event_type[:100],
        "actor_type": "user",
        "actor_id": str(actor_id)[:255],
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": resource_type[:100],
        "resource_id": str(resource_id)[:255],
        "action": action,
        "outcome": "success",
        "details": jsonable(details),
        "trace_id": tracing.audit_trace_id(),
        "created_at": now or datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)
