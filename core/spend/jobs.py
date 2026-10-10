# SPDX-License-Identifier: Apache-2.0
"""Spend maintenance jobs: a ``spend_jobs`` row and a Celery task on the ``maintenance`` queue.

Long operations never run inside an HTTP request: a route enqueues a job
(202 with its id) and a worker runs it. At most one job of a kind is queued
or running per tenant (the partial unique index ``ux_spend_jobs_active``); a
second request is refused with 409 ``job_running`` naming the first. A job
started automatically after a reference-data change (a restatement after a
rate-card correction, an FX settlement after a new rate, a commitment
recompute) is folded into the job already active instead: the change has
committed, and the response names the active job.

A worker claims a job atomically; a job left ``running`` for two hours by a
lost worker is taken over by the redelivered task. Each job stores its
result (counts) and status; a failure stores the exception's type name only,
never its message.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from core.spend import audit, clock, vocab
from core.spend.errors import SpendError

logger = structlog.get_logger()

STALE_RUNNING_HOURS = 2
SYSTEM_ACTOR = "system:spend"
_CLAIM_SQL = text(
    """
    UPDATE spend_jobs SET status = 'running', started_at = now()
    WHERE tenant_id = :tid AND id = :id
      AND (status = 'queued' OR (status = 'running' AND started_at < now() - interval '2 hours'))
    RETURNING kind, params, requested_by
    """
)


def _job_dict(row: Any) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": str(row.id),
        "kind": row.kind,
        "params": dict(row.params or {}),
        "status": row.status,
        "result": dict(row.result or {}),
        "error_code": row.error_code or "",
        "requested_by": row.requested_by,
        "created_at": iso(getattr(row, "created_at", None)),
        "started_at": iso(row.started_at),
        "finished_at": iso(row.finished_at),
    }


async def _active(session: Any, tenant_id: uuid.UUID, kind: str) -> Any:
    from core.models.spend_usage import SpendJob

    rows = (
        (
            await session.execute(
                select(SpendJob).where(
                    SpendJob.tenant_id == tenant_id,
                    SpendJob.kind == kind,
                    SpendJob.status.in_(("queued", "running")),
                )
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


def _dispatch(tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Send the job to a worker (``core.tasks.spend_tasks.run_job`` on the maintenance queue)."""
    from core.tasks.spend_tasks import run_job

    run_job.apply_async(args=[str(tenant_id), str(job_id)], queue="maintenance")


async def _mark_undispatched(tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            update(SpendJob)
            .where(SpendJob.tenant_id == tenant_id, SpendJob.id == job_id, SpendJob.status == "queued")
            .values(status="failed", error_code="dispatch_failed", finished_at=func.now())
        )


async def enqueue(
    tenant_id: uuid.UUID,
    *,
    kind: str,
    params: dict[str, Any],
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue a job and send it to a worker; 409 ``job_running`` while one of the kind is active."""
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    kind = vocab.choice(kind, vocab.JOB_KINDS, field="kind")
    who = str(actor or "").strip()[:128] or SYSTEM_ACTOR
    stamp = now or clock.now_utc()
    job_id = uuid.uuid4()
    clean = audit.jsonable(params)
    try:
        async with get_tenant_session(tenant_id) as session:
            existing = await _active(session, tenant_id, kind)
            if existing is not None:
                raise SpendError(409, "job_running", f"job {existing.id} ({kind}) is {existing.status}")
            session.add(
                SpendJob(
                    id=job_id,
                    tenant_id=tenant_id,
                    kind=kind,
                    params=clean,
                    status="queued",
                    result={},
                    error_code="",
                    requested_by=who,
                    created_at=stamp,
                )
            )
            await session.flush()
            session.add(
                audit.audit_entry(
                    tenant_id,
                    actor_id=who,
                    action="job.enqueue",
                    resource_type="spend_job",
                    resource_id=str(job_id),
                    details={"kind": kind, "params": clean},
                    now=stamp,
                )
            )
    except IntegrityError:
        raise SpendError(409, "job_running", f"a {kind} job is already queued or running") from None
    try:
        _dispatch(tenant_id, job_id)
    # enterprise-gate: broad-except-ok reason=dispatch-failure-fails-the-job-row-so-the-kind-is-not-blocked-and-logs
    except Exception as exc:
        logger.warning("spend_job_dispatch_failed", kind=kind, error_type=type(exc).__name__)
        await _mark_undispatched(tenant_id, job_id)
        return {"job_id": str(job_id), "status": "failed", "kind": kind}
    logger.info("spend_job_enqueued", kind=kind)
    return {"job_id": str(job_id), "status": "queued", "kind": kind}


async def enqueue_followup(
    tenant_id: uuid.UUID, *, kind: str, params: dict[str, Any], actor: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """Queue a job a committed change calls for; fold it into the active job of the kind. Never raises.

    Returns ``{"job_id", "status", "coalesced"}``, or ``None`` when nothing could be queued.
    """
    from core.database import get_tenant_session

    try:
        out = await enqueue(tenant_id, kind=kind, params=params, actor=actor, now=now)
        return {**out, "coalesced": False}
    except SpendError as exc:
        if exc.code != "job_running":
            logger.warning("spend_followup_refused", kind=kind, code=exc.code)
            return None
    # enterprise-gate: broad-except-ok reason=a-followup-job-failure-is-logged-the-committed-change-keeps
    except Exception as exc:
        logger.warning("spend_followup_failed", kind=kind, error_type=type(exc).__name__)
        return None
    try:
        async with get_tenant_session(tenant_id) as session:
            existing = await _active(session, tenant_id, kind)
    # enterprise-gate: broad-except-ok reason=a-followup-lookup-failure-is-logged-the-committed-change-keeps
    except Exception as exc:
        logger.warning("spend_followup_lookup_failed", kind=kind, error_type=type(exc).__name__)
        return None
    if existing is None:
        return None
    logger.warning("spend_job_coalesced", kind=kind)
    return {"job_id": str(existing.id), "status": existing.status, "kind": kind, "coalesced": True}


async def get_job(tenant_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (await session.execute(select(SpendJob).where(SpendJob.tenant_id == tenant_id, SpendJob.id == job_id)))
            .scalars()
            .all()
        )
    if not rows:
        raise SpendError(404, "not_found", "no such job")
    return _job_dict(rows[0])


async def list_jobs(tenant_id: uuid.UUID, *, kind: str | None = None, limit: int = 50) -> dict[str, Any]:
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    conditions = [SpendJob.tenant_id == tenant_id]
    if kind:
        conditions.append(SpendJob.kind == vocab.choice(kind, vocab.JOB_KINDS, field="kind"))
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SpendJob)
                    .where(*conditions)
                    .order_by(SpendJob.created_at.desc())
                    .limit(max(1, min(int(limit), 50)))
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_job_dict(row) for row in rows]}


# ---------------------------------------------------------------- running


def _date(params: dict[str, Any], name: str) -> date:
    return vocab.parse_date(params.get(name), field=name)


async def _execute(tenant_id: uuid.UUID, kind: str, params: dict[str, Any], actor: str, now: datetime) -> dict:
    """Dispatch one job by kind."""
    from core.spend import commitments, ledgers, maintenance, rollups

    if kind == "rebuild":
        return await rollups.rebuild(
            tenant_id, start=_date(params, "start"), end=_date(params, "end"), actor=actor, now=now
        )
    if kind == "backfill":
        return await ledgers.backfill_model_calls(
            tenant_id, start=_date(params, "start"), end=_date(params, "end"), actor=actor, now=now
        )
    if kind == "restate":
        return await maintenance.restate(
            tenant_id,
            provider=str(params.get("provider") or ""),
            start=_date(params, "start"),
            end=_date(params, "end"),
            card_ids=[uuid.UUID(str(c)) for c in params.get("card_ids") or []],
            include_unpriced=bool(params.get("include_unpriced")),
            actor=actor,
            reason=str(params.get("reason") or ""),
            now=now,
        )
    if kind == "settle_fx":
        forced = [(str(c), vocab.parse_date(d, field="force_dates")) for c, d in params.get("force_dates") or []]
        return await maintenance.settle_fx(
            tenant_id, start=_date(params, "start"), end=_date(params, "end"), force_dates=forced, actor=actor, now=now
        )
    if kind == "reattribute":
        return await maintenance.reattribute(
            tenant_id, start=_date(params, "start"), end=_date(params, "end"), actor=actor, now=now
        )
    if kind == "recompute_commitments":
        return await commitments.recompute(tenant_id, provider=params.get("provider") or None, now=now)
    raise SpendError(422, "invalid_value", f"unknown job kind {kind}")


async def _finish(tenant_id: uuid.UUID, job_id: uuid.UUID, *, status: str, result: dict, error_code: str) -> None:
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            update(SpendJob)
            .where(SpendJob.tenant_id == tenant_id, SpendJob.id == job_id)
            .values(status=status, result=audit.jsonable(result), error_code=error_code[:64], finished_at=func.now())
        )


async def run(tenant_id: uuid.UUID, job_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, Any]:
    """Claim and run a job (the Celery body); ``{"skipped": "not_claimable"}`` when another run has it."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(_CLAIM_SQL, {"tid": tenant_id, "id": job_id})).all()
    if not rows:
        return {"skipped": "not_claimable", "job_id": str(job_id)}
    kind, params, actor = rows[0][0], dict(rows[0][1] or {}), str(rows[0][2] or SYSTEM_ACTOR)
    stamp = now or clock.now_utc()
    try:
        result = await _execute(tenant_id, kind, params, actor, stamp)
    # enterprise-gate: broad-except-ok reason=job-failure-records-the-error-type-only-and-logs
    except Exception as exc:
        logger.warning("spend_job_failed", kind=kind, error_type=type(exc).__name__)
        code = exc.code if isinstance(exc, SpendError) else type(exc).__name__
        await _finish(tenant_id, job_id, status="failed", result={}, error_code=code)
        return {"job_id": str(job_id), "status": "failed", "error_code": code}
    await _finish(tenant_id, job_id, status="succeeded", result=result, error_code="")
    logger.info("spend_job_succeeded", kind=kind)
    return {"job_id": str(job_id), "status": "succeeded", "result": audit.jsonable(result)}
