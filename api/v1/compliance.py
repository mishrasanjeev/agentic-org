"""DSAR and compliance endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.audit import AuditLog
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
        outcome="processing",
        details={
            "request_id": str(request_id),
            "subject_email": subject_email,
            "request_type": request_type,
        },
    )
    session.add(entry)
    await session.flush()
    return entry


# ── POST /dsar/access ────────────────────────────────────────────────────────
@router.post("/dsar/access")
async def dsar_access(
    body: DSARRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    request_id = _uuid.uuid4()
    async with get_tenant_session(tid) as session:
        await _create_dsar_audit_entry(session, tid, "access", body.subject_email, request_id)

    return {
        "request_id": str(request_id),
        "type": "access",
        "status": "processing",
        "subject_email": body.subject_email,
        "created_at": datetime.now(UTC).isoformat(),
    }


# ── POST /dsar/erase ────────────────────────────────────────────────────────
@router.post("/dsar/erase")
async def dsar_erase(
    body: DSARRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    request_id = _uuid.uuid4()
    deadline = datetime.now(UTC) + timedelta(days=30)
    async with get_tenant_session(tid) as session:
        await _create_dsar_audit_entry(session, tid, "erase", body.subject_email, request_id)

    return {
        "request_id": str(request_id),
        "type": "erase",
        "status": "processing",
        "subject_email": body.subject_email,
        "deadline": deadline.isoformat(),
        "deadline_days": 30,
        "created_at": datetime.now(UTC).isoformat(),
    }


# ── POST /dsar/export ───────────────────────────────────────────────────────
@router.post("/dsar/export")
async def dsar_export(
    body: DSARRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    request_id = _uuid.uuid4()
    async with get_tenant_session(tid) as session:
        await _create_dsar_audit_entry(session, tid, "export", body.subject_email, request_id)

        # Estimate data size: count audit entries for this subject
        count_result = await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == tid, AuditLog.actor_id == body.subject_email)
        )
        record_count = count_result.scalar() or 0
        estimated_size_mb = round(record_count * 0.002, 2)  # rough estimate

    return {
        "request_id": str(request_id),
        "type": "export",
        "status": "processing",
        "subject_email": body.subject_email,
        "format": "json",
        "estimated_records": record_count,
        "estimated_size_mb": estimated_size_mb,
        "created_at": datetime.now(UTC).isoformat(),
    }


# ── GET /compliance/evidence-package ─────────────────────────────────────────
@router.get("/compliance/evidence-package")
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
                "provider": "GCP Cloud SQL TDE + GCS GMEK",
                "algorithm": "AES-256",
                "status": "collected",
            },
            "encryption_in_transit": {
                "control_id": "CC6.7-transit",
                "protocol": "TLS 1.3",
                "mtls_internal": True,
                "status": "collected",
            },
            "change_management": {
                "control_id": "CC8.1",
                "ci_cd": "GitHub Actions",
                "checks": ["ruff", "mypy", "pytest", "playwright"],
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
                "token_expiry_minutes": 60,
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
                "lockout_threshold": 5,
                "lockout_cooldown_minutes": 15,
                "status": "collected",
            },
        },
    }
