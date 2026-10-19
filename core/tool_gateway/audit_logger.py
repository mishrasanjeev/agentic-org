"""Audit log writer — append-only with HMAC-SHA256 signature."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings
from core.governance.audit_chain_link import SIGNED_COLUMNS, canonical_audit_payload, canonical_value

logger = structlog.get_logger()

# The signed columns and their serialisation live in core.governance.audit_chain_link,
# which has no dependencies, so the offline ledger verifier can load them by path.
_canonical_value = canonical_value
__all__ = [
    "SIGNED_COLUMNS",
    "AuditLogger",
    "build_signed_audit_entry",
    "canonical_audit_payload",
    "sign_audit_record",
    "verify_audit_row",
]


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


def build_signed_audit_entry(
    *,
    tenant_id: Any,
    event_type: str,
    actor_type: str,
    actor_id: str,
    action: str,
    outcome: str,
    details: dict[str, Any],
    created_at: datetime,
    resource_type: str | None = None,
    resource_id: str | None = None,
    agent_id: Any = None,
    workflow_run_id: Any = None,
    trace_id: str = "",
    secret: bytes | None = None,
) -> dict[str, Any]:
    """The column values of one ``audit_log`` row, with its signature.

    For a caller that adds the row to its own session so it commits in the same
    transaction as the write it records (``core.commerce.ledger``). ``details``
    is stored as given: mask anything free-form before passing it.
    """
    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "agent_id": agent_id,
        "workflow_run_id": workflow_run_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "action": action,
        "outcome": outcome,
        "details": details,
        "trace_id": trace_id,
        "created_at": created_at,
    }
    entry["signature"] = sign_audit_record(entry, secret if secret is not None else settings.secret_key.encode())
    return entry


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
                from core.database import bind_tenant_context
                from core.models.audit import AuditLog

                async with self._db() as session:
                    # audit_log is RLS-enforced (WITH CHECK): bind the
                    # entry's tenant so the INSERT is accepted under a
                    # NOBYPASSRLS application role.
                    await bind_tenant_context(session, tenant_id)
                    log_entry = AuditLog(**entry)
                    session.add(log_entry)
                    await session.commit()
            # enterprise-gate: broad-except-ok reason=audit-db-sidecar-failure-does-not-hide-structured-log
            except Exception as e:
                logger.error("audit_log_db_error", error=str(e))
