"""Health check endpoint — verifies DB, Redis, and connector connectivity."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter
from sqlalchemy import text

from api.deps import require_scope
from connectors.registry import ConnectorRegistry
from core.config import settings
from core.database import async_session_factory

logger = structlog.get_logger()

router = APIRouter()

# Version is derived from pyproject.toml via product_facts — the one source
# of truth. Keeping a hardcoded constant here previously led to README,
# Landing, health, and pyproject all drifting apart.
from api.v1.product_facts import _version_from_pyproject as _pv  # noqa: E402

APP_VERSION = _pv()


def _deployed_commit() -> str:
    """Resolve the git commit SHA of the running deploy.

    Codex 2026-04-23 prod re-verification: /health exposed only version
    (e.g. "4.8.0"), which can't prove which commit SHA is live. Helm +
    Cloud Run can inject this via AGENTICORG_GIT_SHA / K_REVISION env
    vars at deploy time. Fall back to "unknown" when not provided.
    """
    for key in ("AGENTICORG_GIT_SHA", "GIT_SHA", "GIT_COMMIT", "K_REVISION"):
        v = os.getenv(key, "").strip()
        if v:
            return v
    return "unknown"

# Timeout for individual connector health checks (seconds)
_CONNECTOR_HC_TIMEOUT = 5.0


async def _check_connector(connector_name: str) -> dict:
    """Run a single connector's health check with a timeout."""
    connector_cls = ConnectorRegistry.get(connector_name)
    if not connector_cls:
        return {"status": "not_found"}
    try:
        instance = connector_cls({})
        await asyncio.wait_for(instance.connect(), timeout=_CONNECTOR_HC_TIMEOUT)
        result = await asyncio.wait_for(instance.health_check(), timeout=_CONNECTOR_HC_TIMEOUT)
        await instance.disconnect()
        return result
    except TimeoutError:
        return {"status": "unhealthy", "error": "timeout"}
    except Exception as e:
        return {"status": "unhealthy", "error": f"{type(e).__name__}: {e}"}


@router.get("/health")
async def health_readiness():
    """Readiness probe — checks local critical dependencies (DB + Redis).

    Does NOT check connectors (external) or expose environment details.
    K8s readiness probes should point here so external outages don't
    flap pods.
    """
    checks: dict[str, str] = {"db": "unknown", "redis": "unknown"}

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "healthy"
    except Exception as e:
        checks["db"] = f"unhealthy: {type(e).__name__}"

    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        await r.close()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {type(e).__name__}"

    core_healthy = checks["db"] == "healthy" and checks["redis"] == "healthy"
    return {
        "status": "healthy" if core_healthy else "unhealthy",
        "version": APP_VERSION,
        "commit": _deployed_commit(),
        "checks": checks,
    }


@router.get("/health/liveness")
async def liveness():
    """Lightweight liveness probe — just confirms the process is running."""
    return {"status": "alive"}


# ── GET /health/checks ─────────────────────────────────────────────────────
#
# 2026-04-30 enterprise gap analysis flagged that the SLA Monitor page
# calls ``/health/checks`` and ``/health/uptime`` but the routes didn't
# exist. Phase 1 (PR #399) added them as honest live-snapshot stubs with
# a ``data_source: "live_snapshot"`` field. Phase 5 (this PR) wires them
# to the new ``health_check_history`` table populated every 5 minutes by
# ``core.tasks.health_snapshot.record_health_snapshot``.
#
# When history is present → ``data_source: "persisted"`` and the response
# returns real rows from the last N hours. When the table is empty (e.g.
# right after a deploy before the first snapshot lands, or in a dev env
# without the Celery beat running) → fall back to ``"live_snapshot"`` so
# the UI's existing data-source-aware banner still renders an honest
# explanation instead of a misleading empty chart.

# Default window for the chart endpoints. The SLA monitor renders a
# 24-hour view; tweak via the ``hours`` query parameter.
_DEFAULT_HISTORY_HOURS = 24
_MAX_HISTORY_HOURS = 7 * 24  # match the Celery task's RETENTION_DAYS


async def _fetch_history(hours: int) -> list[dict]:
    """Pull the last ``hours`` of recorded health snapshots, newest first.

    Returns an empty list when the table doesn't exist yet (fresh env
    without the migration), when no snapshots have landed yet, or when
    the query fails for any reason. Callers fall back to the live
    snapshot in those cases.
    """
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT recorded_at, status, checks, version, commit "
                    "FROM health_check_history "
                    "WHERE recorded_at >= :cutoff "
                    "ORDER BY recorded_at DESC"
                ),
                {"cutoff": datetime.now(UTC) - timedelta(hours=hours)},
            )
            rows = result.all()
    except Exception as exc:  # noqa: BLE001 — table absent / db blip → live fallback
        logger.warning("health_history_query_failed", error=str(exc))
        return []

    return [
        {
            "timestamp": r.recorded_at.isoformat() if r.recorded_at else None,
            "status": r.status,
            "checks": r.checks or {},
            "version": r.version,
            "commit": r.commit,
        }
        for r in rows
    ]


@router.get("/health/checks")
async def health_checks(hours: int = _DEFAULT_HISTORY_HOURS):
    """Recent health-check history (last ``hours`` of recorded snapshots).

    Returns ``data_source: "persisted"`` + real rows when the periodic
    Celery task has populated ``health_check_history``. Falls back to
    a single ``data_source: "live_snapshot"`` row + an honest banner
    note when the table is empty (e.g. before the first snapshot
    lands after deploy).
    """
    hours = max(1, min(hours, _MAX_HISTORY_HOURS))
    history = await _fetch_history(hours)
    if history:
        return {
            "data_source": "persisted",
            "window_hours": hours,
            "items": history,
        }

    snapshot = await health_readiness()
    now = datetime.now(UTC).isoformat()
    return {
        "data_source": "live_snapshot",
        "note": (
            "No persisted snapshots in the last "
            f"{hours}h yet. The periodic recorder may not have run "
            "since deploy, or the health_check_history table is "
            "empty. Showing live probe only."
        ),
        "window_hours": hours,
        "items": [
            {
                "timestamp": now,
                "status": snapshot["status"],
                "checks": snapshot["checks"],
                "version": snapshot.get("version"),
                "commit": snapshot.get("commit"),
            }
        ],
    }


@router.get("/health/uptime")
async def health_uptime(hours: int = _DEFAULT_HISTORY_HOURS):
    """Uptime aggregate over the last ``hours``.

    Returns ``data_source: "persisted"`` with real bucketed series and a
    computed ``uptime_pct`` when ``health_check_history`` has rows.
    Falls back to ``data_source: "live_snapshot"`` when empty.
    """
    hours = max(1, min(hours, _MAX_HISTORY_HOURS))
    history = await _fetch_history(hours)
    snapshot = await health_readiness()

    if history:
        up_count = sum(1 for r in history if r["status"] == "healthy")
        uptime_pct = round(100.0 * up_count / len(history), 2)
        return {
            "data_source": "persisted",
            "window_hours": hours,
            "uptime_pct": uptime_pct,
            "samples": len(history),
            "current_status": snapshot["status"],
            "items": [
                {
                    "timestamp": r["timestamp"],
                    "up": r["status"] == "healthy",
                    "status": r["status"],
                }
                for r in history
            ],
        }

    now = datetime.now(UTC).isoformat()
    is_up = snapshot["status"] == "healthy"
    return {
        "data_source": "live_snapshot",
        "note": (
            "No persisted snapshots in the last "
            f"{hours}h yet — uptime percentage will be available "
            "once the periodic recorder has run."
        ),
        "window_hours": hours,
        "uptime_pct": None,
        "samples": 0,
        "current_status": snapshot["status"],
        "items": [
            {
                "timestamp": now,
                "up": is_up,
                "status": snapshot["status"],
            }
        ],
    }


@router.get(
    "/health/diagnostics",
    dependencies=[require_scope("agenticorg:admin")],
)
async def diagnostics():
    """Full diagnostics — requires admin auth. Includes connector details + env.

    Not used by K8s probes. Called by the SRE dashboard.
    """
    checks: dict[str, str | dict] = {"db": "unknown", "redis": "unknown"}

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "healthy"
    except Exception as e:
        checks["db"] = f"unhealthy: {type(e).__name__}"

    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        await r.close()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {type(e).__name__}"

    connector_names = ConnectorRegistry.all_names()
    connector_checks: dict[str, dict] = {}
    if connector_names:
        tasks = {name: _check_connector(name) for name in connector_names}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for name, result in zip(tasks.keys(), results, strict=False):
            if isinstance(result, Exception):
                connector_checks[name] = {"status": "unhealthy", "error": str(result)}
            else:
                connector_checks[name] = result

    healthy_count = sum(1 for v in connector_checks.values() if v.get("status") == "healthy")
    total_count = len(connector_checks)
    checks["connectors"] = {
        "registered": total_count,
        "healthy": healthy_count,
        "unhealthy": total_count - healthy_count,
        "details": connector_checks,
    }

    try:
        import composio  # noqa: F401

        composio_sdk_loaded = True
    except ImportError:
        composio_sdk_loaded = False
    checks["composio"] = {
        "sdk_loaded": composio_sdk_loaded,
        "api_key_configured": bool(os.getenv("COMPOSIO_API_KEY", "")),
    }

    core_healthy = checks["db"] == "healthy" and checks["redis"] == "healthy"
    return {
        "status": "healthy" if core_healthy and healthy_count == total_count else (
            "degraded" if core_healthy else "unhealthy"
        ),
        "version": APP_VERSION,
        "env": settings.env,
        "checks": checks,
    }
