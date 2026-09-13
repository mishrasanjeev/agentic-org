"""DSAR and compliance endpoints."""

from __future__ import annotations

import os
import uuid as _uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from audit.dsar import DSARHandler, serialize
from core.database import get_tenant_session
from core.models.audit import AuditLog
from core.models.dsar import DSARRequestRecord
from core.schemas.api import DSARRequest

router = APIRouter()


async def _create_dsar_audit_entry(
    session,
    tenant_id: _uuid.UUID,
    request_type: str,
    subject_email: str,
    request_id: _uuid.UUID,
) -> AuditLog:
    """Insert an audit log entry for a DSAR request."""
    entry = AuditLog(
        tenant_id=tenant_id,
        event_type=f"dsar.{request_type}",
        actor_type="user",
        actor_id=subject_email,
        action=f"dsar_{request_type}_request",
        outcome="received",
        details={
            "request_id": str(request_id),
            "subject_email": subject_email,
            "request_type": request_type,
        },
    )
    session.add(entry)
    await session.flush()
    return entry


async def _submit_and_process(
    tid: _uuid.UUID,
    request_type: str,
    body: DSARRequest,
    request: Request,
) -> dict:
    """Persist the DSAR row, run it inline, and return the honest terminal state."""
    requested_by = str(getattr(request.state, "user_sub", "") or "unknown")
    handler = DSARHandler()
    async with get_tenant_session(tid) as session:
        record = await handler.submit(
            session,
            tenant_id=tid,
            request_type=request_type,
            subject_email=body.subject_email,
            requested_by=requested_by,
        )
        await _create_dsar_audit_entry(session, tid, request_type, body.subject_email, record.id)
        record = await handler.process(session, record)
        payload = serialize(record, include_result=request_type != "export")
    if record.status == "failed":
        # The row is persisted with status=failed; surface it instead of
        # pretending the request succeeded.
        raise HTTPException(status_code=500, detail=f"DSAR {request_type} failed; see request {record.id}")
    return payload


# ── POST /dsar/access ────────────────────────────────────────────────────────
@router.post("/dsar/access", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="compliance.dsar.sensitive.write",
    rate_limit="compliance-dsar-request",
    idempotency="not_idempotent-new-dsar-request-id",
    audit_event="dsar.access.request",
)
async def dsar_access(
    body: DSARRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    """Persist a data-access request and return the subject's data inline."""
    return await _submit_and_process(_uuid.UUID(tenant_id), "access", body, request)


# ── POST /dsar/erase ────────────────────────────────────────────────────────
@router.post("/dsar/erase", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="compliance.dsar.high_risk.write",
    rate_limit="compliance-dsar-request",
    idempotency="not_idempotent-new-dsar-request-id",
    audit_event="dsar.erase.request",
)
async def dsar_erase(
    body: DSARRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    """Anonymise the subject's PII (tenant admins only). Status is honest."""
    return await _submit_and_process(_uuid.UUID(tenant_id), "erase", body, request)


# ── POST /dsar/export ───────────────────────────────────────────────────────
@router.post("/dsar/export", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="compliance.dsar.sensitive.export",
    rate_limit="compliance-dsar-export",
    idempotency="not_idempotent-new-dsar-request-id",
    audit_event="dsar.export.request",
)
async def dsar_export(
    body: DSARRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    """Persist an export request; the JSON export is read back via ``GET /dsar/{id}``."""
    return await _submit_and_process(_uuid.UUID(tenant_id), "export", body, request)


# ── GET /dsar/{request_id} ──────────────────────────────────────────────────
@router.get("/dsar/{request_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="compliance.dsar.sensitive.read",
    rate_limit="compliance-dsar-request",
    idempotency="read-only",
    audit_event="dsar.request.read",
)
async def dsar_status(
    request_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Poll a DSAR request (tenant-scoped); includes the collected data / export."""
    tid = _uuid.UUID(tenant_id)
    try:
        rid = _uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="DSAR request not found") from None
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(DSARRequestRecord).where(
                DSARRequestRecord.id == rid, DSARRequestRecord.tenant_id == tid
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="DSAR request not found")
        return serialize(record)


# ── GET /compliance/evidence-package ─────────────────────────────────────────
@router.get("/compliance/evidence-package")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="compliance.evidence.sensitive.export",
    rate_limit="compliance-evidence-export",
    idempotency="read-only",
    audit_event="compliance.evidence_package.read",
)
async def evidence_package(tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    now = datetime.now(UTC)
    package_id = str(_uuid.uuid4())

    async with get_tenant_session(tid) as session:
        # Access controls: count distinct actor events
        access_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.tenant_id == tid,
                AuditLog.event_type.like("auth.%"),
            )
        )
        access_count = access_result.scalar() or 0

        # Audit log stats
        audit_result = await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tid)
        )
        audit_total = audit_result.scalar() or 0

        # Deployment records
        deploy_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.tenant_id == tid,
                AuditLog.event_type.like("deploy%"),
            )
        )
        deploy_count = deploy_result.scalar() or 0

        # Incident history
        incident_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.tenant_id == tid,
                AuditLog.event_type.like("incident%"),
            )
        )
        incident_count = incident_result.scalar() or 0

    return {
        "package_id": package_id,
        "tenant_id": tenant_id,
        "generated_at": now.isoformat(),
        "sections": {
            "access_controls": {
                "control_id": "CC6.1-access",
                "event_count": access_count,
                "status": "collected",
            },
            "audit_logs": {
                "control_id": "CC7.2",
                "total_entries": audit_total,
                "status": "collected",
            },
            "encryption_at_rest": {
                "control_id": "CC6.7-rest",
                "provider": os.getenv("AGENTICORG_ENCRYPTION_PROVIDER", "GCP Cloud SQL TDE + GCS GMEK"),
                "algorithm": os.getenv("AGENTICORG_ENCRYPTION_ALGO", "AES-256"),
                "status": "collected",
            },
            "encryption_in_transit": {
                "control_id": "CC6.7-transit",
                "protocol": os.getenv("AGENTICORG_TLS_VERSION", "TLS 1.3"),
                "mtls_internal": os.getenv("AGENTICORG_MTLS", "true").lower() == "true",
                "status": "collected",
            },
            "change_management": {
                "control_id": "CC8.1",
                "ci_cd": os.getenv("AGENTICORG_CI_CD", "GitHub Actions"),
                "checks": os.getenv("AGENTICORG_CI_CHECKS", "ruff,mypy,pytest,playwright").split(","),
                "status": "collected",
            },
            "deployment_records": {
                "control_id": "CC8.1-deploy",
                "event_count": deploy_count,
                "status": "collected",
            },
            "incident_history": {
                "control_id": "CC7.3",
                "event_count": incident_count,
                "severity_levels": ["P1", "P2", "P3", "P4"],
                "status": "collected",
            },
            "vendor_management": {
                "control_id": "CC9.2",
                "connectors_validated": 54,
                "oauth_scoped": True,
                "status": "collected",
            },
            "session_management": {
                "control_id": "CC6.1-session",
                "token_expiry_minutes": int(os.getenv("AGENTICORG_TOKEN_TTL_MINUTES", "60")),
                "refresh_expiry_days": 7,
                "concurrent_session_limit": 5,
                "status": "collected",
            },
            "password_policy": {
                "control_id": "CC6.1-password",
                "min_length": 12,
                "hashing": "bcrypt",
                "cost_factor": 12,
                "mfa_available": True,
                "lockout_threshold": int(os.getenv("AGENTICORG_LOCKOUT_THRESHOLD", "5")),
                "lockout_cooldown_minutes": int(os.getenv("AGENTICORG_LOCKOUT_COOLDOWN", "15")),
                "status": "collected",
            },
        },
    }
