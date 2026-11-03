# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights background exports and the Downloads page.

``POST /insights/exports`` asks for a file the worker makes: a metric or an
explorer table larger than an inline export allows (up to 500,000 rows), a run
evidence bundle, a view PDF, the finance pack or a period evidence pack.
``GET /insights/exports`` lists the caller's files and the scheduled reports
sent to their email (an administrator's list is the tenant's),
``GET /insights/exports/{id}`` one of them, and
``GET /insights/exports/{id}/download`` streams the file through the API with
one signed ``insights.export`` audit row; there are no signed URLs.
Files are kept seven days. ``POST /insights/exports/{id}/sign`` records an
auditor's or administrator's sign-off of a ready evidence pack.

Every route needs ``insights:read`` and Insights on for the tenant. What a job
may export is checked when it is made, against the caller's views and
explorers, and the worker builds it with the caller's scope as read then.
``include_pii`` (raw values where the tenant masks PII) is for administrators
and auditors only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import _actor, _require_enabled, _tenant
from core.database import get_tenant_session
from core.insights import export_jobs, storage
from core.insights.audit import audit_entry
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter(prefix="/insights/exports", tags=["Insights"])
LIST_LIMIT = 50


class ExportRequest(BaseModel):
    kind: str = Field(max_length=20)
    format: str = Field(max_length=10)
    params: dict[str, Any] = Field(default_factory=dict)
    include_pii: bool = False


def _visible(caller: Caller, email: str | None = None) -> Any:
    """Whose jobs the caller sees: an administrator the tenant's; anyone else their own and the scheduled reports
    sent to their email address."""
    from sqlalchemy import or_

    from core.models.insights import InsightsExport as X

    if caller.is_admin:
        return X.id.is_not(None)
    if caller.user_id is None:
        return X.id.is_(None)
    if email:
        return or_(X.user_id == caller.user_id, X.params["shared_with"].contains([email.lower()]))
    return X.user_id == caller.user_id


async def _email_of(tid: uuid.UUID, caller: Caller) -> str | None:
    from sqlalchemy import select

    from core.models.user import User

    if caller.user_id is None:
        return None
    async with get_tenant_session(tid) as session:
        return (
            await session.execute(select(User.email).where(User.tenant_id == tid, User.id == caller.user_id))
        ).scalar_one_or_none()


def _refuse(exc: export_jobs.ExportJobError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


def _enqueue(tid: uuid.UUID, job_id: uuid.UUID) -> bool:
    try:
        from core.tasks.insights_tasks import build_export

        build_export.delay(str(tid), str(job_id))
        return True
    # enterprise-gate: broad-except-ok reason=a-queue-failure-marks-the-job-failed-so-the-person-is-told-logged
    except Exception as exc:
        logger.warning("insights_export_enqueue_failed", export_id=str(job_id), error_type=type(exc).__name__)
        return False


@router.post("", status_code=202)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-export",
    idempotency="non-idempotent-create",
    audit_event="insights.export.requested",
)
async def create_export(
    body: ExportRequest, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Queue a file; the response is the job (status ``queued``). The Downloads page shows it when it is ready."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    if not storage.configured():
        raise HTTPException(
            503,
            detail={
                "error": "export_storage_unconfigured",
                "message": "Background exports are not set up for this deployment; inline exports still work.",
            },
        )
    caller = caller_from_request(request)
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        try:
            job = await export_jobs.create(
                session,
                tid,
                caller,
                kind=body.kind,
                fmt=body.format,
                params=body.params,
                include_pii=body.include_pii,
                now=now,
            )
        except export_jobs.ExportJobError as exc:
            raise _refuse(exc) from None
        session.add(
            audit_entry(
                tid,
                event_type="insights.export.requested",
                actor_id=_actor(request),
                action="request",
                resource_type="insights_export",
                resource_id=str(job.id),
                details={
                    "kind": job.kind,
                    "format": job.format,
                    "key": job.params.get("key"),
                    "include_pii": job.include_pii,
                },
            )
        )
        await session.flush()
        out = export_jobs.serialize(job)
    if not _enqueue(tid, job.id):
        async with get_tenant_session(tid) as session:
            from core.models.insights import InsightsExport

            row = await session.get(InsightsExport, job.id)
            row.status = "failed"
            row.error = "queue_unavailable: the export could not be started; try again"
            row.finished_at = datetime.now(UTC)
            out = export_jobs.serialize(row)
    return {"export": out}


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.exports.read",
)
async def list_exports(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT)] = LIST_LIMIT,
) -> dict[str, Any]:
    """The caller's export files, newest first (an administrator's list covers the tenant)."""
    from sqlalchemy import select

    from core.models.insights import InsightsExport as X

    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    email = await _email_of(tid, caller)
    async with get_tenant_session(tid) as session:
        rows = (
            await session.execute(
                select(X).where(X.tenant_id == tid, _visible(caller, email)).order_by(X.created_at.desc()).limit(limit)
            )
        ).scalars()
        return {"exports": [export_jobs.serialize(job) for job in rows], "storage": storage.configured()}


async def _job(tid: uuid.UUID, caller: Caller, export_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.insights import InsightsExport as X

    email = await _email_of(tid, caller)
    async with get_tenant_session(tid) as session:
        job = (
            await session.execute(select(X).where(X.tenant_id == tid, X.id == export_id, _visible(caller, email)))
        ).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, detail={"error": "export_not_found", "message": "no such export for you"})
    return job


@router.get("/{export_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.exports.read",
)
async def get_export(
    request: Request,
    export_id: Annotated[uuid.UUID, Path()],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """One export job and its state."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    return {"export": export_jobs.serialize(await _job(tid, caller_from_request(request), export_id))}


@router.get("/{export_id}/download")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-export",
    idempotency="read-only",
    audit_event="insights.export",
)
async def download_export(
    request: Request,
    export_id: Annotated[uuid.UUID, Path()],
    tenant_id: str = Depends(get_current_tenant),
) -> Response:
    """The file, through the API, with one signed audit row (404 when it is not ready, 410 once it expired)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    job = await _job(tid, caller_from_request(request), export_id)
    if job.status == "expired":
        raise HTTPException(410, detail={"error": "export_expired", "message": "files are kept for seven days"})
    if job.status != "ready" or not job.storage_key:
        raise HTTPException(404, detail={"error": "export_not_ready", "message": f"the export is {job.status}"})
    try:
        content = await asyncio.to_thread(storage.get, job.storage_key)
    except storage.ExportStorageError as exc:
        raise HTTPException(404, detail={"error": str(exc), "message": "the file is no longer available"}) from None
    async with get_tenant_session(tid) as session:
        session.add(
            audit_entry(
                tid,
                event_type="insights.export",
                actor_id=_actor(request),
                action="download",
                resource_type="insights_export",
                resource_id=str(job.id),
                details={
                    "kind": job.kind,
                    "format": job.format,
                    "key": (job.params or {}).get("key"),
                    "row_count": job.row_count,
                    "sha256": job.sha256,
                    "include_pii": job.include_pii,
                    "masked": (job.params or {}).get("masked"),
                },
            )
        )
    return Response(
        content=content,
        media_type=export_jobs.MEDIA_TYPES.get(job.format, "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{job.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": job.sha256 or "",
        },
    )


class SignRequest(BaseModel):
    statement: str = Field(min_length=1, max_length=1000)


@router.post("/{export_id}/sign")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-admin",
    idempotency="idempotent-first-write-wins",
    audit_event="insights.evidence_pack.signed",
)
async def sign_export(
    body: SignRequest,
    request: Request,
    export_id: Annotated[uuid.UUID, Path()],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """An auditor or administrator signs a ready evidence pack once: who, the statement and the manifest's SHA-256
    go on the pack and on a signed ``insights.evidence_pack.signed`` audit row."""
    from sqlalchemy import select

    from core.insights import packs
    from core.models.insights import InsightsExport as X
    from core.models.user import User

    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        job = (
            await session.execute(
                select(X).where(X.tenant_id == tid, X.id == export_id, _visible(caller)).with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(404, detail={"error": "export_not_found", "message": "no such export for you"})
        name = None
        if caller.user_id is not None:
            name = (
                await session.execute(select(User.name).where(User.tenant_id == tid, User.id == caller.user_id))
            ).scalar_one_or_none()
        try:
            signature = packs.sign(job, caller, name=name or "", statement=body.statement, now=now)
        except export_jobs.ExportJobError as exc:
            raise _refuse(exc) from None
        job.signature = signature
        job.signed_by = caller.user_id
        job.signed_at = now
        session.add(
            audit_entry(
                tid,
                event_type="insights.evidence_pack.signed",
                actor_id=_actor(request),
                action="sign",
                resource_type="insights_export",
                resource_id=str(job.id),
                details={k: v for k, v in signature.items() if k != "user_id"},
            )
        )
        out = export_jobs.serialize(job)
    return {"export": out}
