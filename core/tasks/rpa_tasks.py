"""Celery tasks for RPA schedules.

``run_rpa_schedule`` is the main task: it loads a schedule row,
resolves the script via the generic registry, runs it, pushes the
emitted chunks through the 4.8/5 quality gate, embeds the survivors
via ``core.embeddings.embed_one``, persists them into
``knowledge_documents`` with the tenant's ``tenant_id``, and updates
the schedule's ``last_run_*`` telemetry.

``dispatch_due_rpa_schedules`` is the beat task: it runs every 5
minutes (see ``core/tasks/celery_app.py``) and enqueues one
``run_rpa_schedule`` per schedule whose ``next_run_at`` has passed.

Design notes
============

- **Synchronous body inside a sync Celery task.** Celery tasks are
  sync by default; the existing report / workflow tasks already call
  async coroutines via ``asyncio.run`` and we follow the same pattern
  here. The tradeoff is higher worker CPU usage during the event-loop
  setup, but scheduling cadence is O(minutes), not O(requests), so
  it's fine.
- **Quality gate runs BEFORE embedding.** Embedding rejected chunks
  would waste compute and pollute retrieval; the registry spec says
  4.8+/5 is the target, and we refuse to publish anything below 4.5
  (QUALITY_REJECT_BELOW) at all.
- **Idempotent publish.** A schedule that runs twice in the same
  window would otherwise produce duplicate chunks. We dedupe by
  (tenant_id, source_url, first_200_chars_hash) so a re-run with the
  same source content is a no-op.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid as _uuid
from datetime import UTC, datetime
from decimal import Decimal
from statistics import mean
from typing import Any

from sqlalchemy import select, text

from core.tasks.celery_app import app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run ``coro`` to completion from a sync Celery task body."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# run_rpa_schedule — per-schedule execution
# ---------------------------------------------------------------------------


async def _execute_schedule(tenant_id: str, schedule_id: str) -> dict[str, Any]:
    """Async body that runs a single schedule end-to-end."""
    from core.database import async_session_factory
    from core.models.rpa_schedule import RPASchedule
    from core.rpa.executor import execute_rpa_script
    from core.rpa.quality import QUALITY_TARGET, filter_chunks
    from rpa.scripts._registry import discover_scripts

    try:
        tid = _uuid.UUID(tenant_id)
        sid = _uuid.UUID(schedule_id)
    except ValueError as exc:
        logger.warning("rpa_task_invalid_id tenant=%s schedule=%s err=%s", tenant_id, schedule_id, exc)
        return {"ok": False, "error": "invalid id"}

    # 1. Load schedule
    async with async_session_factory() as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid, RPASchedule.tenant_id == tid
            )
        )
        schedule = result.scalar_one_or_none()
    if schedule is None:
        logger.info("rpa_task_schedule_missing schedule_id=%s", schedule_id)
        return {"ok": False, "error": "schedule not found"}
    if not schedule.enabled:
        return {"ok": False, "error": "schedule disabled"}

    # 2. Resolve script meta
    scripts = discover_scripts()
    meta = scripts.get(schedule.script_key)
    if not meta:
        await _record_failure(tid, sid, "script_not_registered", 0, 0, None)
        return {"ok": False, "error": f"script {schedule.script_key!r} not registered"}

    target_quality = float(
        schedule.config.get("target_quality")
        or meta.get("target_quality")
        or QUALITY_TARGET
    )
    timeout_s = int(meta.get("estimated_duration_s") or 60) * 2
    http_only = bool(meta.get("http_only"))

    # 3. Run the script
    try:
        if http_only:
            # HTTP-only scripts can be called directly — skip Playwright
            # entirely to avoid the headless-browser cost.
            import importlib

            mod = importlib.import_module(f"rpa.scripts.{schedule.script_key}")
            raw_result = await mod.run(None, dict(schedule.params or {}))
        else:
            raw_result = await execute_rpa_script(
                script_name=schedule.script_key,
                params=dict(schedule.params or {}),
                timeout_s=timeout_s,
            )
    except Exception as exc:
        logger.exception("rpa_script_execution_failed schedule_id=%s", schedule_id)
        await _record_failure(tid, sid, f"script_error: {type(exc).__name__}", 0, 0, None)
        return {"ok": False, "error": str(exc)}

    if not raw_result.get("success", False):
        detail = str(raw_result.get("error") or "script reported failure")[:200]
        await _record_failure(tid, sid, detail, 0, 0, None)
        return {"ok": False, "error": detail}

    chunks = raw_result.get("chunks") or []
    if not chunks:
        await _record_success(tid, sid, 0, 0, None)
        return {"ok": True, "published": 0, "rejected": 0, "avg_quality": None}

    # 4. Quality gate
    published, flagged, rejected = filter_chunks(chunks, target=target_quality)
    all_published = published + flagged  # flagged still get persisted
    scores = [c["quality"]["score"] for c in all_published] + [
        c["quality"]["score"] for c in rejected
    ]
    avg_quality = round(mean(scores), 3) if scores else None

    # 5. Embed survivors + persist
    persisted = 0
    if all_published:
        persisted = await _embed_and_store(tid, schedule, all_published)

    # 6. Record telemetry
    await _record_success(
        tid,
        sid,
        published=persisted,
        rejected=len(rejected),
        avg_quality=avg_quality,
    )
    return {
        "ok": True,
        "published": persisted,
        "flagged": len(flagged),
        "rejected": len(rejected),
        "avg_quality": avg_quality,
    }


async def _record_success(
    tid: _uuid.UUID,
    sid: _uuid.UUID,
    published: int,
    rejected: int,
    avg_quality: float | None,
) -> None:
    from core.database import async_session_factory
    from core.models.rpa_schedule import RPASchedule

    async with async_session_factory() as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid, RPASchedule.tenant_id == tid
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.last_run_at = datetime.now(UTC)
        row.last_run_status = "success"
        row.last_run_chunks_published = published
        row.last_run_chunks_rejected = rejected
        row.last_quality_avg = (
            Decimal(str(avg_quality)) if avg_quality is not None else None
        )
        # Advance next_run_at by the cron cadence — same helper the API uses.
        from api.v1.rpa_schedules import _compute_next_run

        if row.enabled:
            try:
                row.next_run_at = _compute_next_run(row.cron_expression)
            except Exception as exc:
                logger.info(
                    "rpa_schedule_next_run_compute_failed schedule_id=%s err=%s",
                    row.id, exc,
                )


async def _record_failure(
    tid: _uuid.UUID,
    sid: _uuid.UUID,
    reason: str,
    published: int,
    rejected: int,
    avg_quality: float | None,
) -> None:
    from core.database import async_session_factory
    from core.models.rpa_schedule import RPASchedule

    async with async_session_factory() as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid, RPASchedule.tenant_id == tid
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.last_run_at = datetime.now(UTC)
        row.last_run_status = f"failed: {reason}"[:30]
        row.last_run_chunks_published = published
        row.last_run_chunks_rejected = rejected
        row.last_quality_avg = (
            Decimal(str(avg_quality)) if avg_quality is not None else None
        )


def _source_hash(chunk: dict[str, Any]) -> str:
    """Stable dedup key for idempotent re-runs."""
    source = str(chunk.get("source_url") or chunk.get("title") or "")
    content_prefix = str(chunk.get("content") or "")[:200]
    raw = f"{source}\x1f{content_prefix}".encode()
    return hashlib.sha256(raw).hexdigest()


async def _embed_and_store(
    tid: _uuid.UUID, schedule, chunks: list[dict[str, Any]]
) -> int:
    """Embed each chunk with the platform embedding model + insert into
    ``knowledge_documents`` with dedup.

    Returns the number of rows inserted.
    """
    try:
        from core.embeddings import embed_one
    except ImportError:
        logger.warning("rpa_embed_skip: core.embeddings not available")
        return 0

    from core.database import async_session_factory

    inserted = 0
    async with async_session_factory() as session:
        for ch in chunks:
            content = (ch.get("content") or "")[:2000]
            if not content:
                continue
            try:
                vector = embed_one(content)
            except Exception as exc:
                logger.info("rpa_embed_failed err=%s", exc)
                continue

            # vector is a list[float] — format for pgvector as JSON-array literal
            vector_literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
            title = str(ch.get("title") or schedule.script_key)[:480]
            source = str(ch.get("source_url") or "")[:500]
            category = str(ch.get("category") or "rpa")
            dedup = _source_hash(ch)

            # Insert IF NOT EXISTS via a SELECT gate. Keeps this
            # portable across Postgres versions that may not have the
            # same partial-unique constraints configured.
            exists_q = await session.execute(
                text(
                    "SELECT 1 FROM knowledge_documents "
                    "WHERE tenant_id = :tid AND source = :source "
                    "LIMIT 1"
                ),
                {"tid": str(tid), "source": f"{source}#{dedup[:12]}"},
            )
            if exists_q.scalar_one_or_none() is not None:
                continue

            await session.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "  (tenant_id, title, content, category, source, "
                    "   file_type, status, embedding, created_at) "
                    "VALUES (:tid, :title, :content, :category, "
                    "        :source, 'rpa', 'ready', "
                    "        CAST(:vector AS vector), now())"
                ),
                {
                    "tid": str(tid),
                    "title": title,
                    "content": content,
                    "category": category,
                    # Encode dedup into source so the unique index is
                    # effective without a schema change.
                    "source": f"{source}#{dedup[:12]}",
                    "vector": vector_literal,
                },
            )
            inserted += 1
    return inserted


@app.task(bind=True, name="core.tasks.rpa_tasks.run_rpa_schedule", max_retries=2)
def run_rpa_schedule(self, tenant_id: str, schedule_id: str) -> dict[str, Any]:
    """Execute a single RPA schedule end-to-end."""
    try:
        return _run_async(_execute_schedule(tenant_id, schedule_id))
    except Exception as exc:
        logger.exception("run_rpa_schedule_failed tenant=%s schedule=%s", tenant_id, schedule_id)
        try:
            raise self.retry(exc=exc, countdown=60) from exc
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# dispatch_due_rpa_schedules — beat sweeper
# ---------------------------------------------------------------------------


async def _dispatch_async() -> dict[str, Any]:
    from api.v1.rpa_schedules import due_schedule_ids

    pairs = await due_schedule_ids()
    enqueued = 0
    for tenant_id, schedule_id in pairs:
        run_rpa_schedule.delay(tenant_id, schedule_id)
        enqueued += 1
    return {"due": len(pairs), "enqueued": enqueued}


@app.task(name="core.tasks.rpa_tasks.dispatch_due_rpa_schedules")
def dispatch_due_rpa_schedules() -> dict[str, Any]:
    """Beat-driven sweeper that enqueues overdue schedules."""
    try:
        return _run_async(_dispatch_async())
    except Exception as exc:
        logger.exception("dispatch_due_rpa_schedules_failed")
        return {"error": str(exc)[:200]}
