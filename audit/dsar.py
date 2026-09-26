"""DSAR tools — GDPR/DPDP data subject requests.

Audit 2026-09-13: the previous handler answered ``status: processing`` with
a fabricated 30-day deadline while nothing was persisted or processed.

This module now does the actual work against a tenant-scoped session:

* ``submit``            — persist a ``dsar_requests`` row (status ``received``).
* ``collect_subject``   — access/export: gather the subject's rows from
                          ``users``, ``audit_log`` and ``agent_feedback``
                          (bounded; ``truncated`` is reported honestly).
* ``erase_subject``     — erasure: anonymise the subject's ``users`` PII
                          (e-mail, name, password) and pseudonymise the
                          ``actor_id`` on feedback rows with a one-way hash.
                          Sessions are revoked via the
                          ``sessions_invalid_before`` watermark. Audit rows
                          are kept unchanged and reported with a count (see
                          ``AUDIT_LOG_RETENTION_BASIS``).

Everything runs inline in the request that submitted it (no background
worker is wired), so a ``completed`` status means the work is done; a
``failed`` status carries the error. ``GET /api/v1/dsar/{id}`` reads back the
row, including the collected data for access/export requests.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.audit import AuditLog
from core.models.dsar import DSAR_REQUEST_TYPES, DSARRequestRecord
from core.models.feedback import AgentFeedback
from core.models.user import User

logger = structlog.get_logger()

# Hard caps so an access/export request cannot pull an unbounded audit
# history into one JSONB row. ``truncated`` tells the requester when a cap
# was hit so they can narrow the request or ask for an operator export.
AUDIT_ROW_CAP = 500
FEEDBACK_ROW_CAP = 500

# Erasure leaves the subject's ``audit_log`` rows as they are. The table is
# append-only: the ``audit_log_immutable`` trigger rejects every UPDATE and
# DELETE, so rewriting ``actor_id`` failed every erase request. The trail is
# kept for the controller's record-keeping obligations, which GDPR
# Art. 17(3)(b) exempts from erasure; the result reports how many rows were
# retained and on what basis.
AUDIT_LOG_RETENTION_BASIS = "GDPR Art. 17(3)(b): retained for compliance with a legal obligation"


def pseudonymise(subject_email: str) -> str:
    """One-way, non-reversible identifier used in place of the e-mail after erasure."""
    return "erased:" + hashlib.sha256(subject_email.strip().lower().encode()).hexdigest()[:16]


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class DSARHandler:
    """Persist and execute data-subject requests inside a tenant-scoped session."""

    async def submit(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        request_type: str,
        subject_email: str,
        requested_by: str,
    ) -> DSARRequestRecord:
        if request_type not in DSAR_REQUEST_TYPES:
            raise ValueError(f"unsupported DSAR request type: {request_type!r}")
        record = DSARRequestRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            request_type=request_type,
            subject_email=subject_email,
            status="received",
            requested_by=requested_by,
            result={},
        )
        session.add(record)
        await session.flush()
        logger.info("dsar_received", request_type=request_type, request_id=str(record.id))
        return record

    async def collect_subject(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, subject_email: str
    ) -> dict[str, Any]:
        """Return the subject's data held for this tenant (access / export)."""
        users_result = await session.execute(
            select(User).where(User.tenant_id == tenant_id, User.email == subject_email)
        )
        users = [
            {
                "id": str(u.id),
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "domain": u.domain,
                "status": u.status,
                "timezone": u.timezone,
                "locale": u.locale,
                "last_login_at": _isoformat(u.last_login_at),
                "created_at": _isoformat(u.created_at),
            }
            for u in users_result.scalars().all()
        ]

        audit_total = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.tenant_id == tenant_id, AuditLog.actor_id == subject_email)
            )
        ).scalar() or 0
        audit_result = await session.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id, AuditLog.actor_id == subject_email)
            .order_by(AuditLog.created_at.desc())
            .limit(AUDIT_ROW_CAP)
        )
        audit_rows = [
            {
                "id": str(a.id),
                "event_type": a.event_type,
                "action": a.action,
                "outcome": a.outcome,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "created_at": _isoformat(a.created_at),
            }
            for a in audit_result.scalars().all()
        ]

        feedback_total = (
            await session.execute(
                select(func.count())
                .select_from(AgentFeedback)
                .where(AgentFeedback.tenant_id == tenant_id, AgentFeedback.actor_id == subject_email)
            )
        ).scalar() or 0
        feedback_result = await session.execute(
            select(AgentFeedback)
            .where(AgentFeedback.tenant_id == tenant_id, AgentFeedback.actor_id == subject_email)
            .order_by(AgentFeedback.created_at.desc())
            .limit(FEEDBACK_ROW_CAP)
        )
        feedback_rows = [
            {
                "id": str(f.id),
                "agent_id": str(f.agent_id),
                "run_id": f.run_id,
                "feedback_type": f.feedback_type,
                "feedback_text": f.feedback_text,
                "decision": f.decision,
                "created_at": _isoformat(f.created_at),
            }
            for f in feedback_result.scalars().all()
        ]

        return {
            "subject_email": subject_email,
            "users": users,
            "audit_log": audit_rows,
            "agent_feedback": feedback_rows,
            "totals": {
                "users": len(users),
                "audit_log": int(audit_total),
                "agent_feedback": int(feedback_total),
            },
            "truncated": int(audit_total) > AUDIT_ROW_CAP or int(feedback_total) > FEEDBACK_ROW_CAP,
        }

    async def erase_subject(self, session: AsyncSession, *, tenant_id: uuid.UUID, subject_email: str) -> dict[str, Any]:
        """Anonymise the subject's PII; keep their audit rows and count them."""
        pseudo = pseudonymise(subject_email)
        now = datetime.now(UTC)

        users_result = await session.execute(
            update(User)
            .where(User.tenant_id == tenant_id, User.email == subject_email)
            .values(
                email=f"{pseudo}@erased.invalid",
                name=None,
                password_hash=None,
                mfa_enabled=False,
                status="inactive",
                sessions_invalid_before=now,
            )
        )
        feedback_result = await session.execute(
            update(AgentFeedback)
            .where(AgentFeedback.tenant_id == tenant_id, AgentFeedback.actor_id == subject_email)
            .values(actor_id=pseudo)
        )
        audit_retained = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.tenant_id == tenant_id, AuditLog.actor_id == subject_email)
            )
        ).scalar() or 0
        await session.flush()
        counts = {
            "users_anonymised": int(users_result.rowcount or 0),
            "agent_feedback_pseudonymised": int(feedback_result.rowcount or 0),
            "audit_log_retained": int(audit_retained),
        }
        logger.info("dsar_erase_applied", **counts)
        return {"pseudonym": pseudo, **counts, "audit_log_retention_basis": AUDIT_LOG_RETENTION_BASIS}

    async def process(self, session: AsyncSession, record: DSARRequestRecord) -> DSARRequestRecord:
        """Execute ``record`` inline and persist its terminal status."""
        record.status = "processing"
        await session.flush()
        try:
            # The savepoint confines a database error to this request's own
            # work. Without it Postgres aborts the whole transaction, the
            # ``failed`` status below cannot be written, and the caller gets
            # an unrecorded 500 instead of a persisted failure.
            async with session.begin_nested():
                if record.request_type == "erase":
                    result = await self.erase_subject(
                        session, tenant_id=record.tenant_id, subject_email=record.subject_email
                    )
                else:
                    result = await self.collect_subject(
                        session, tenant_id=record.tenant_id, subject_email=record.subject_email
                    )
                    if record.request_type == "export":
                        result["format"] = "json"
            record.result = result
            record.status = "completed"
            record.completed_at = datetime.now(UTC)
        # enterprise-gate: broad-except-ok reason=dsar-failure-is-persisted-as-failed-status-not-hidden
        except Exception as exc:
            logger.exception("dsar_processing_failed", request_id=str(record.id))
            record.status = "failed"
            record.error = type(exc).__name__
            record.completed_at = datetime.now(UTC)
        await session.flush()
        return record


def serialize(record: DSARRequestRecord, *, include_result: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": str(record.id),
        "type": record.request_type,
        "status": record.status,
        "subject_email": record.subject_email,
        "requested_by": record.requested_by,
        "created_at": _isoformat(record.created_at),
        "completed_at": _isoformat(record.completed_at),
        "error": record.error,
        "poll": f"/api/v1/dsar/{record.id}",
    }
    if include_result:
        payload["result"] = record.result or {}
    return payload
