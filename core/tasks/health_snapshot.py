"""Periodic health snapshot recorder — Phase 5 SLA telemetry persistence.

Wired into the Celery beat schedule at 5-minute intervals (see
``core/tasks/celery_app.py:beat_schedule``). Each tick:

1. Runs the same DB + Redis liveness probes as ``GET /health``.
2. INSERTs a row into ``health_check_history`` with the result.
3. Trims rows older than ``RETENTION_DAYS`` so the table stays bounded.

The endpoints ``GET /health/checks`` and ``GET /health/uptime`` query
this table to render real history on the SLA Monitor page. Without
this task running, those endpoints fall back to the live snapshot
(see ``api/v1/health.py``) — so a missing-task condition is visible
to operators (the SLA page banner will still show ``data_source:
live_snapshot``) rather than silently producing a stale chart.
"""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
import structlog
from sqlalchemy import text

from core.config import settings
from core.database import async_session_factory
from core.tasks.celery_app import app

logger = structlog.get_logger()

# Keep ~7 days of snapshots @ one every 5 minutes ⇒ ~2,016 rows. Tiny.
RETENTION_DAYS = 7


def _deployed_commit() -> str:
    for key in ("AGENTICORG_GIT_SHA", "GIT_SHA", "GIT_COMMIT", "K_REVISION"):
        v = os.getenv(key, "").strip()
        if v:
            return v
    return "unknown"


async def _probe_db() -> str:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return "healthy"
    except Exception as e:  # noqa: BLE001 — capture the failure shape, not crash the snapshot
        return f"unhealthy: {type(e).__name__}"


async def _probe_redis() -> str:
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await r.ping()
        finally:
            await r.close()
        return "healthy"
    except Exception as e:  # noqa: BLE001
        return f"unhealthy: {type(e).__name__}"


async def _record_snapshot_async() -> dict:
    """Probe + insert + trim. Returns the inserted row's payload for logs."""
    db_status = await _probe_db()
    redis_status = await _probe_redis()
    overall = "healthy" if (db_status == "healthy" and redis_status == "healthy") else "unhealthy"

    snapshot_id = str(_uuid.uuid4())
    recorded_at = datetime.now(UTC)
    checks = {"db": db_status, "redis": redis_status}

    # Lazy version pull — same source the /health endpoint uses.
    try:
        from api.v1.health import APP_VERSION  # noqa: PLC0415
        version = APP_VERSION
    except Exception:  # noqa: BLE001 — version is best-effort metadata
        version = None

    commit = _deployed_commit()

    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO health_check_history "
                "(id, recorded_at, status, checks, version, commit) "
                "VALUES (:id, :recorded_at, :status, CAST(:checks AS jsonb), :version, :commit)"
            ),
            {
                "id": snapshot_id,
                "recorded_at": recorded_at,
                "status": overall,
                "checks": __import__("json").dumps(checks),
                "version": version,
                "commit": commit,
            },
        )
        # Trim. ``DELETE WHERE recorded_at < NOW() - INTERVAL`` is bounded
        # by the index on recorded_at DESC + the small absolute volume.
        await session.execute(
            text(
                "DELETE FROM health_check_history "
                "WHERE recorded_at < :cutoff"
            ),
            {"cutoff": recorded_at - timedelta(days=RETENTION_DAYS)},
        )
        await session.commit()

    return {
        "id": snapshot_id,
        "recorded_at": recorded_at.isoformat(),
        "status": overall,
        "checks": checks,
        "version": version,
        "commit": commit,
    }


@app.task(name="core.tasks.health_snapshot.record_health_snapshot")
def record_health_snapshot() -> dict:
    """Periodic: record one /health snapshot + trim old rows.

    Scheduled every 5 minutes by Celery Beat (see celery_app.beat_schedule).
    """
    try:
        result = asyncio.run(_record_snapshot_async())
        logger.info(
            "health_snapshot_recorded",
            status=result["status"],
            checks=result["checks"],
        )
        return result
    except Exception as exc:  # noqa: BLE001
        # Don't propagate — a failed snapshot is observable through
        # the resulting gap in the chart, not by killing the worker.
        logger.error("health_snapshot_failed", error=str(exc))
        return {"status": "snapshot_failed", "error": str(exc)}
