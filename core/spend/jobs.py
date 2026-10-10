# SPDX-License-Identifier: Apache-2.0
"""Spend maintenance jobs: a ``spend_jobs`` row and a Celery task on the ``maintenance`` queue.

Long operations never run inside an HTTP request: a route enqueues a job
(202 with its id) and a worker runs it. One job of a kind runs at a time per
tenant (the partial unique index ``ux_spend_jobs_running``).

* A route request is refused with 409 ``job_running``, naming the job, while
  a job of the kind is queued or running (checked under a per-tenant,
  per-kind advisory lock, so two requests cannot both pass).
* A job a committed reference-data change calls for (a restatement after a
  rate-card correction, an FX settlement after a new or corrected rate, a
  commitment recompute) is never dropped and never reported as covered by a
  job that does not cover it. It is merged into a queued job of the kind
  whose parameters can be widened to cover both (the range joined, the cards
  and forced dates added; a restatement only for the same provider), or it is
  queued as a job of its own, which runs after the one running.

A worker claims a job atomically, and only while no other job of its kind is
running. A running job writes a heartbeat every minute. When a job ends, the
next queued job of its kind is sent to a worker. A job whose heartbeat has
stopped for ten minutes (its worker was lost) can be taken over by a
redelivered task, and the job sweep queues it again and resends queued jobs
nothing is running for; the jobs are idempotent, so running one again is
safe. A transient database failure (a lock or statement timeout, a deadlock,
a dropped connection) queues the job again with a backoff, up to three
attempts. Each job stores its result (counts) and status; a failure stores
the exception's type name only, never its message.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from core.spend import audit, clock, locks, vocab
from core.spend.errors import SpendError, retryable

logger = structlog.get_logger()

STALE_MINUTES = 10
HEARTBEAT_S = 60.0
MAX_ATTEMPTS = 3
RETRY_COUNTDOWN_S = 60
REASON_MAX = 500
SYSTEM_ACTOR = "system:spend"
_CLAIM_SQL = text(
    """
    UPDATE spend_jobs AS j SET status = 'running', started_at = now(), heartbeat_at = now()
    WHERE j.tenant_id = :tid AND j.id = :id
      AND (j.status = 'queued'
           OR (j.status = 'running' AND COALESCE(j.heartbeat_at, j.started_at) < now() - interval '10 minutes'))
      AND NOT EXISTS (
          SELECT 1 FROM spend_jobs o
          WHERE o.tenant_id = j.tenant_id AND o.kind = j.kind AND o.status = 'running' AND o.id <> j.id)
    RETURNING j.kind, j.params, j.requested_by, j.result
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
        "heartbeat_at": iso(getattr(row, "heartbeat_at", None)),
        "finished_at": iso(row.finished_at),
    }


async def _active(session: Any, tenant_id: uuid.UUID, kind: str) -> Any:
    """The kind's running job, else its oldest queued one, else ``None``."""
    from core.models.spend_usage import SpendJob

    rows = (
        (
            await session.execute(
                select(SpendJob)
                .where(
                    SpendJob.tenant_id == tenant_id,
                    SpendJob.kind == kind,
                    SpendJob.status.in_(("queued", "running")),
                )
                .order_by(SpendJob.created_at, SpendJob.id)
            )
        )
        .scalars()
        .all()
    )
    running = [row for row in rows if row.status == "running"]
    return (running or rows or [None])[0]


async def _queued(session: Any, tenant_id: uuid.UUID, kind: str, *, lock: bool = False) -> list[Any]:
    """The kind's queued jobs, oldest first (row-locked when ``lock``)."""
    from core.models.spend_usage import SpendJob

    statement = (
        select(SpendJob)
        .where(SpendJob.tenant_id == tenant_id, SpendJob.kind == kind, SpendJob.status == "queued")
        .order_by(SpendJob.created_at, SpendJob.id)
    )
    if lock:
        statement = statement.with_for_update()
    return list((await session.execute(statement)).scalars().all())


def _dispatch(tenant_id: uuid.UUID, job_id: uuid.UUID, **options: Any) -> None:
    """Send the job to a worker (``core.tasks.spend_tasks.run_job`` on the maintenance queue)."""
    from core.tasks.spend_tasks import run_job

    run_job.apply_async(args=[str(tenant_id), str(job_id)], queue="maintenance", **options)


def _send(tenant_id: uuid.UUID, job_id: uuid.UUID, kind: str, **options: Any) -> bool:
    """Dispatch, logging a failure; a job left queued is resent by the next job's end or the sweep."""
    try:
        _dispatch(tenant_id, job_id, **options)
    # enterprise-gate: broad-except-ok reason=dispatch-failure-is-logged-the-queued-job-keeps-for-the-sweep
    except Exception as exc:
        logger.warning("spend_job_dispatch_failed", kind=kind, error_type=type(exc).__name__)
        return False
    return True


async def _mark_undispatched(tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            update(SpendJob)
            .where(SpendJob.tenant_id == tenant_id, SpendJob.id == job_id, SpendJob.status == "queued")
            .values(status="failed", error_code="dispatch_failed", finished_at=func.now())
        )


def _new_job(
    session: Any, tenant_id: uuid.UUID, *, kind: str, params: dict[str, Any], who: str, stamp: datetime
) -> uuid.UUID:
    from core.models.spend_usage import SpendJob

    job_id = uuid.uuid4()
    session.add(
        SpendJob(
            id=job_id,
            tenant_id=tenant_id,
            kind=kind,
            params=params,
            status="queued",
            result={},
            error_code="",
            requested_by=who,
            created_at=stamp,
        )
    )
    return job_id


def _audit_enqueue(
    session: Any, tenant_id: uuid.UUID, job_id: uuid.UUID, *, kind: str, params: dict, who: str, stamp: datetime
) -> None:
    session.add(
        audit.audit_entry(
            tenant_id,
            actor_id=who,
            action="job.enqueue",
            resource_type="spend_job",
            resource_id=str(job_id),
            details={"kind": kind, "params": params},
            now=stamp,
        )
    )


def _actor(actor: str) -> str:
    return str(actor or "").strip()[:128] or SYSTEM_ACTOR


async def enqueue(
    tenant_id: uuid.UUID,
    *,
    kind: str,
    params: dict[str, Any],
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue a job and send it to a worker; 409 ``job_running`` while one of the kind is queued or running."""
    from core.database import get_tenant_session

    kind = vocab.choice(kind, vocab.JOB_KINDS, field="kind")
    who = _actor(actor)
    stamp = now or clock.now_utc()
    clean = audit.jsonable(params)
    try:
        async with get_tenant_session(tenant_id) as session:
            await locks.xact_lock(session, locks.job_kind(tenant_id, kind))
            existing = await _active(session, tenant_id, kind)
            if existing is not None:
                raise SpendError(409, "job_running", f"job {existing.id} ({kind}) is {existing.status}")
            job_id = _new_job(session, tenant_id, kind=kind, params=clean, who=who, stamp=stamp)
            await session.flush()
            _audit_enqueue(session, tenant_id, job_id, kind=kind, params=clean, who=who, stamp=stamp)
    except IntegrityError:
        raise SpendError(409, "job_running", f"a {kind} job is already queued or running") from None
    if not _send(tenant_id, job_id, kind):
        # An administrator's request answers ``failed`` at once, so it can be sent again.
        await _mark_undispatched(tenant_id, job_id)
        return {"job_id": str(job_id), "status": "failed", "kind": kind}
    logger.info("spend_job_enqueued", kind=kind)
    return {"job_id": str(job_id), "status": "queued", "kind": kind}


# ---------------------------------------------------------------- follow-up jobs


def _span(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, str] | None:
    """The joined ``[start, end]`` of two jobs, or ``None`` when it is longer than a job may run."""
    from core.spend.maintenance import JOB_MAX_DAYS

    start = min(vocab.parse_date(a.get("start"), field="start"), vocab.parse_date(b.get("start"), field="start"))
    end = max(vocab.parse_date(a.get("end"), field="end"), vocab.parse_date(b.get("end"), field="end"))
    if (end - start).days + 1 > JOB_MAX_DAYS:
        return None
    return start.isoformat(), end.isoformat()


def _reasons(*values: Any) -> str:
    """The reasons of merged restatements, each once, joined and bounded to 500 characters."""
    seen: list[str] = []
    for value in values:
        for part in str(value or "").split("; "):
            part = part.strip()
            if part and part not in seen:
                seen.append(part)
    return "; ".join(seen)[:REASON_MAX].rstrip()


def merge_params(kind: str, queued: dict[str, Any], new: dict[str, Any]) -> dict[str, Any] | None:
    """Parameters that cover both a queued job's work and a new request's, or ``None`` when one job cannot.

    * ``recompute_commitments``: one provider when both name the same one, otherwise every provider.
    * ``settle_fx``: the joined range and both jobs' forced dates.
    * ``restate``: the same provider only; the joined range; the cards of both, or every record of the
      provider when either job restates every record; unpriced records when either includes them.
    * Other kinds are never merged unless their parameters are equal.

    A joined range longer than a job may run (``maintenance.JOB_MAX_DAYS``) is not merged. Widening a
    range only re-checks records that are already right: every job recomputes values from what is
    known now and leaves an unchanged record alone.
    """
    if kind == "recompute_commitments":
        a, b = queued.get("provider") or None, new.get("provider") or None
        return {"provider": a} if a and a == b else {}
    if kind == "settle_fx":
        span = _span(queued, new)
        if span is None:
            return None
        forced = sorted(
            {(str(c), str(d)) for c, d in [*(queued.get("force_dates") or []), *(new.get("force_dates") or [])]}
        )
        return {"start": span[0], "end": span[1], "force_dates": [[c, d] for c, d in forced]}
    if kind == "restate":
        if (queued.get("provider") or "") != (new.get("provider") or ""):
            return None
        span = _span(queued, new)
        if span is None:
            return None
        everything = any(not p.get("card_ids") and not p.get("include_unpriced") for p in (queued, new))
        cards = [] if everything else sorted({str(c) for p in (queued, new) for c in p.get("card_ids") or []})
        return {
            "provider": queued.get("provider"),
            "start": span[0],
            "end": span[1],
            "card_ids": cards,
            "include_unpriced": False
            if everything
            else bool(queued.get("include_unpriced") or new.get("include_unpriced")),
            "reason": _reasons(queued.get("reason"), new.get("reason")),
        }
    return dict(queued) if queued == new else None


async def enqueue_followup(
    tenant_id: uuid.UUID, *, kind: str, params: dict[str, Any], actor: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """Queue the job a committed change calls for. Never raises.

    The work is merged into a queued job of the kind that can cover it, or
    queued as its own job (to run after a running one). Returns
    ``{"job_id", "status", "kind", "merged"}``, or ``None`` when nothing could
    be queued (logged).
    """
    from core.database import get_tenant_session

    target: Any = None
    job_id: uuid.UUID | None = None
    try:
        kind = vocab.choice(kind, vocab.JOB_KINDS, field="kind")
        who = _actor(actor)
        stamp = now or clock.now_utc()
        clean = audit.jsonable(params)
        async with get_tenant_session(tenant_id) as session:
            await locks.xact_lock(session, locks.job_kind(tenant_id, kind))
            merged: dict[str, Any] | None = None
            for job in await _queued(session, tenant_id, kind, lock=True):
                merged = merge_params(kind, dict(job.params or {}), clean)
                if merged is not None:
                    target = job
                    break
            if target is not None and merged is not None:
                job_id = target.id
                before = dict(target.params or {})
                if merged != before:
                    target.params = merged
                    session.add(
                        audit.audit_entry(
                            tenant_id,
                            actor_id=who,
                            action="job.merge",
                            resource_type="spend_job",
                            resource_id=str(job_id),
                            details={"kind": kind, "added": clean, "before": before, "after": merged},
                            now=stamp,
                        )
                    )
            else:
                job_id = _new_job(session, tenant_id, kind=kind, params=clean, who=who, stamp=stamp)
                await session.flush()
                _audit_enqueue(session, tenant_id, job_id, kind=kind, params=clean, who=who, stamp=stamp)
    except SpendError as exc:
        logger.warning("spend_followup_refused", kind=kind, code=exc.code)
        return None
    # enterprise-gate: broad-except-ok reason=a-followup-job-failure-is-logged-the-committed-change-keeps
    except Exception as exc:
        logger.warning("spend_followup_failed", kind=kind, error_type=type(exc).__name__)
        return None
    if job_id is None:
        return None
    if target is not None:
        logger.info("spend_job_merged", kind=kind)
        return {"job_id": str(job_id), "status": "queued", "kind": kind, "merged": True}
    # A job of the kind may be running: then the claim waits, and that job's end sends this one.
    _send(tenant_id, job_id, kind)
    logger.info("spend_job_enqueued", kind=kind)
    return {"job_id": str(job_id), "status": "queued", "kind": kind, "merged": False}


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
        return await commitments.recompute(tenant_id, provider=params.get("provider") or None, now=now, actor=actor)
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


async def _requeue(tenant_id: uuid.UUID, job_id: uuid.UUID, *, attempts: int, error_code: str) -> None:
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            update(SpendJob)
            .where(SpendJob.tenant_id == tenant_id, SpendJob.id == job_id, SpendJob.status == "running")
            .values(
                status="queued",
                started_at=None,
                heartbeat_at=None,
                result={"attempts": attempts, "last_error": error_code[:64]},
            )
        )


async def _send_next(tenant_id: uuid.UUID, kind: str) -> str | None:
    """Send the oldest queued job of ``kind`` to a worker; its id, or ``None``. Never raises."""
    from core.database import get_tenant_session

    try:
        async with get_tenant_session(tenant_id) as session:
            waiting = await _queued(session, tenant_id, kind)
    # enterprise-gate: broad-except-ok reason=a-next-job-lookup-failure-is-logged-the-sweep-resends-it
    except Exception as exc:
        logger.warning("spend_job_next_lookup_failed", kind=kind, error_type=type(exc).__name__)
        return None
    if not waiting:
        return None
    _send(tenant_id, waiting[0].id, kind)
    return str(waiting[0].id)


async def _heartbeat(tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Write ``heartbeat_at`` every ``HEARTBEAT_S`` while the job runs (cancelled when it ends)."""
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    while True:
        await asyncio.sleep(HEARTBEAT_S)
        try:
            async with get_tenant_session(tenant_id) as session:
                await session.execute(
                    update(SpendJob)
                    .where(SpendJob.tenant_id == tenant_id, SpendJob.id == job_id, SpendJob.status == "running")
                    .values(heartbeat_at=func.now())
                )
        # enterprise-gate: broad-except-ok reason=a-missed-heartbeat-is-logged-the-job-keeps-running
        except Exception as exc:
            logger.warning("spend_job_heartbeat_failed", error_type=type(exc).__name__)


async def run(tenant_id: uuid.UUID, job_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, Any]:
    """Claim and run a job (the Celery body); ``{"skipped": ...}`` when it cannot be claimed now."""
    from core.database import get_tenant_session

    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (await session.execute(_CLAIM_SQL, {"tid": tenant_id, "id": job_id})).all()
    except IntegrityError:
        # Another job of the kind became running at the same moment; its end sends this one.
        return {"skipped": "blocked", "job_id": str(job_id)}
    if not rows:
        return {"skipped": "not_claimable", "job_id": str(job_id)}
    kind, params, actor = rows[0][0], dict(rows[0][1] or {}), str(rows[0][2] or SYSTEM_ACTOR)
    previous = dict(rows[0][3] or {}) if len(rows[0]) > 3 else {}
    stamp = now or clock.now_utc()
    beat = asyncio.create_task(_heartbeat(tenant_id, job_id))
    failure: Exception | None = None
    result: dict[str, Any] = {}
    try:
        result = await _execute(tenant_id, kind, params, actor, stamp)
    # enterprise-gate: broad-except-ok reason=job-failure-records-the-error-type-only-and-logs
    except Exception as exc:
        failure = exc
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
    if failure is not None:
        code = failure.code if isinstance(failure, SpendError) else type(failure).__name__
        attempts = int(previous.get("attempts") or 0) + 1
        logger.warning("spend_job_failed", kind=kind, error_type=type(failure).__name__, attempt=attempts)
        if retryable(failure) and attempts < MAX_ATTEMPTS:
            await _requeue(tenant_id, job_id, attempts=attempts, error_code=code)
            _send(tenant_id, job_id, kind, countdown=RETRY_COUNTDOWN_S * attempts)
            return {"job_id": str(job_id), "status": "queued", "error_code": code, "attempts": attempts}
        await _finish(tenant_id, job_id, status="failed", result={"attempts": attempts}, error_code=code)
        await _send_next(tenant_id, kind)
        return {"job_id": str(job_id), "status": "failed", "error_code": code}
    if previous.get("attempts"):
        result = {**result, "attempts": int(previous["attempts"]) + 1}
    await _finish(tenant_id, job_id, status="succeeded", result=result, error_code="")
    logger.info("spend_job_succeeded", kind=kind)
    await _send_next(tenant_id, kind)
    return {"job_id": str(job_id), "status": "succeeded", "result": audit.jsonable(result)}


# ---------------------------------------------------------------- sweep


async def sweep(tenant_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, int]:
    """Queue again the jobs a lost worker left running, and send each kind's oldest queued job to a
    worker when nothing of its kind runs (a dispatch that failed, a job's end that never came)."""
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendJob

    cutoff = (now or clock.now_utc()) - timedelta(minutes=STALE_MINUTES)
    requeued = 0
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SpendJob)
                    .where(SpendJob.tenant_id == tenant_id, SpendJob.status.in_(("queued", "running")))
                    .order_by(SpendJob.created_at, SpendJob.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            beat = row.heartbeat_at or row.started_at
            if row.status == "running" and (beat is None or beat < cutoff):
                row.status, row.started_at, row.heartbeat_at = "queued", None, None
                requeued += 1
                logger.warning("spend_job_requeued_stale", kind=row.kind)
        await session.flush()
        running = {row.kind for row in rows if row.status == "running"}
        oldest: dict[str, uuid.UUID] = {}
        for row in rows:
            if row.status == "queued" and row.kind not in running:
                oldest.setdefault(row.kind, row.id)
    sent = sum(1 for kind, job_id in sorted(oldest.items()) if _send(tenant_id, job_id, kind))
    return {"requeued": requeued, "sent": sent}
