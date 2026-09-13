"""Audit log writer — append-only with HMAC-SHA256 signature."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

# Columns covered by the row signature, in canonical order. ``id`` is
# excluded on purpose: the DB generates it, so signing a pre-insert random id
# (the previous behaviour) produced a signature nothing could ever verify.
SIGNED_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "event_type",
    "actor_type",
    "actor_id",
    "agent_id",
    "workflow_run_id",
    "resource_type",
    "resource_id",
    "action",
    "outcome",
    "details",
    "trace_id",
    "created_at",
)


def _canonical_value(key: str, value: Any) -> Any:
    """Normalise a column value so the same row hashes identically whether it
    comes from the in-memory entry (str/ISO) or an ORM row (UUID/datetime)."""
    if value is None:
        return None
    if key == "created_at":
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat()
        return str(value)
    if key == "details":
        return value if isinstance(value, dict) else {}
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def canonical_audit_payload(record: Any) -> str:
    """Serialise the signed columns of ``record`` (dict or ``AuditLog`` row)."""
    get = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
    canonical = {key: _canonical_value(key, get(key)) for key in SIGNED_COLUMNS}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)


def sign_audit_record(record: Any, secret: bytes) -> str:
    return hmac.new(secret, canonical_audit_payload(record).encode(), hashlib.sha256).hexdigest()


def verify_audit_row(row: Any, secret: bytes | None = None) -> bool:
    """Return True iff ``row.signature`` matches the HMAC of its persisted columns.

    ``row`` may be an ``AuditLog`` ORM instance or a dict with the same keys.
    Rows without a signature verify False.
    """
    secret = secret if secret is not None else settings.secret_key.encode()
    stored = row.get("signature") if isinstance(row, dict) else getattr(row, "signature", None)
    if not stored:
        return False
    return hmac.compare_digest(sign_audit_record(row, secret), str(stored))


class AuditLogger:
    """Write tamper-evident audit log entries."""

    def __init__(self, db_session_factory=None):
        self._db = db_session_factory
        self._secret = settings.secret_key.encode()

    def _sign(self, data: dict[str, Any]) -> str:
        """Compute HMAC-SHA256 over the canonical persisted columns."""
        return sign_audit_record(data, self._secret)

    async def log(
        self,
        tenant_id: str,
        agent_id: str | None = None,
        tool_name: str = "",
        action: str = "",
        outcome: str = "",
        details: dict[str, Any] | None = None,
        workflow_run_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_type: str = "agent",
        actor_id: str | None = None,
        trace_id: str | None = None,
        enforcement_action: str | None = None,
    ) -> None:
        """Write an audit log entry. Never update or delete."""
        from core.tool_gateway.pii_masker import mask_pii

        now = datetime.now(UTC)
        enriched_details = dict(details or {})

        # Include enforcement action details (scope denied, rate limited)
        if enforcement_action:
            enriched_details["enforcement_action"] = enforcement_action
            enriched_details["enforcement_at"] = now.isoformat()

        # Always include timestamp for freshness filtering
        enriched_details["logged_at"] = now.isoformat()

        entry = {
            "tenant_id": tenant_id,
            "event_type": f"tool.{tool_name}" if tool_name else action,
            "actor_type": actor_type,
            "actor_id": actor_id or agent_id or "",
            "agent_id": agent_id,
            "workflow_run_id": workflow_run_id,
            "resource_type": resource_type or "tool_call",
            "resource_id": resource_id or tool_name,
            "action": action,
            "outcome": outcome,
            "details": mask_pii(enriched_details),
            "trace_id": trace_id or "",
            "created_at": now.isoformat(),
        }
        entry["signature"] = self._sign(entry)

        # Log to structured logger (and DB if available)
        logger.info("audit_log", **entry)

        if self._db:
            try:
                from core.models.audit import AuditLog

                async with self._db() as session:
                    log_entry = AuditLog(**entry)
                    session.add(log_entry)
                    await session.commit()
            # enterprise-gate: broad-except-ok reason=audit-db-sidecar-failure-does-not-hide-structured-log
            except Exception as e:
                logger.error("audit_log_db_error", error=str(e))
