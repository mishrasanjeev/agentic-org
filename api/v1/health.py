"""Health check endpoint — verifies DB, Redis, and connector connectivity."""

from __future__ import annotations

import asyncio
import os

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
