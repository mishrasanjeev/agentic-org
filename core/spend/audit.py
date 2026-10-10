# SPDX-License-Identifier: Apache-2.0
"""Signed audit rows for every change to spend reference data.

Each change is written in the same tenant transaction as the change itself,
as signed ``audit_log`` rows (``event_type = spend.<action>``, actor type
``user``). A single write records one row with its changes; an import
records a manifest: one summary row (counts, the uploaded file's sha256, the
number of parts) and one row per 200 changed rows, every changed row with
its before and after values, with no cap. Details carry identifiers, codes,
counts and reference-data values only, never a document, a prompt or
customer data.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, NamedTuple

from core.config import settings

CHANGES_PER_PART = 200


class Change(NamedTuple):
    """One changed row: its key, and its recorded fields before (``None`` = created) and after."""

    key: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


def jsonable(value: Any) -> Any:
    """Decimals as plain text, dates and instants as ISO text, ids as text; containers recursively."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def change_dict(change: Change) -> dict[str, Any]:
    return {"key": change.key, "before": jsonable(change.before), "after": jsonable(change.after)}


def audit_entry(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any],
    now: datetime | None = None,
) -> Any:
    """One signed ``audit_log`` row for a spend change (not added to any session)."""
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record
    from observability import tracing

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": f"spend.{action}"[:100],
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


def audit_change(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    changes: Sequence[Change],
    extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Any:
    """One row for a single write and the rows it changed (a create, an update, a supersede)."""
    details: dict[str, Any] = {"changes": [change_dict(c) for c in changes]}
    if extra:
        details.update(extra)
    return audit_entry(
        tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        now=now,
    )


def audit_changes(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    changes: Sequence[Change],
    summary: dict[str, Any],
    file_sha256: str | None,
    resource_id: str | None = None,
    now: datetime | None = None,
) -> list[Any]:
    """A manifest: one summary row, then one row per ``CHANGES_PER_PART`` changes, every change recorded."""
    parts = (len(changes) + CHANGES_PER_PART - 1) // CHANGES_PER_PART
    resource_id = resource_id or file_sha256 or "manifest"
    rows = [
        audit_entry(
            tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details={**summary, "file_sha256": file_sha256, "parts": parts, "changed": len(changes)},
            now=now,
        )
    ]
    for index in range(parts):
        chunk = changes[index * CHANGES_PER_PART : (index + 1) * CHANGES_PER_PART]
        rows.append(
            audit_entry(
                tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details={"part": index + 1, "parts": parts, "changes": [change_dict(c) for c in chunk]},
                now=now,
            )
        )
    return rows
