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


class AuditLogger:
    """Write tamper-evident audit log entries."""

    def __init__(self, db_session_factory=None):
        self._db = db_session_factory
        self._secret = settings.secret_key.encode()

    def _sign(self, data: dict[str, Any]) -> str:
        """Compute HMAC-SHA256 signature for an audit entry."""
        payload = json.dumps(data, sort_keys=True, default=str)
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()

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
    ) -> None:
        """Write an audit log entry. Never update or delete."""
        from core.tool_gateway.pii_masker import mask_pii

        entry = {
            "id": str(uuid.uuid4()),
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
            "details": mask_pii(details or {}),
            "trace_id": trace_id or "",
            "created_at": datetime.now(UTC).isoformat(),
        }
        entry["signature"] = self._sign(entry)

        # Log to structured logger (and DB if available)
        logger.info("audit_log", **entry)

        if self._db:
            try:
                from core.models.audit import AuditLog
                async with self._db() as session:
                    log_entry = AuditLog(**{
                        k: v for k, v in entry.items()
                        if k not in ("id",)  # Let DB generate ID
                    })
                    session.add(log_entry)
                    await session.commit()
            except Exception as e:
                logger.error("audit_log_db_error", error=str(e))
