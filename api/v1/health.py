"""Health check endpoint — verifies DB, Redis, and connector connectivity."""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter
from sqlalchemy import text

from connectors.registry import ConnectorRegistry
from core.config import settings
from core.database import async_session_factory

logger = structlog.get_logger()

router = APIRouter()

APP_VERSION = "2.1.0"

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
async def health_check():
    """Full health check — verifies DB, Redis, and connectors are reachable."""
    checks: dict[str, str | dict] = {"db": "unknown", "redis": "unknown"}

    # Check PostgreSQL
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "healthy"
    except Exception as e:
        checks["db"] = f"unhealthy: {type(e).__name__}"

    # Check Redis
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        await r.close()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {type(e).__name__}"

    # Check registered connectors
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

    # Overall status: degraded if any core check fails or any connector is unhealthy
    core_healthy = checks["db"] == "healthy" and checks["redis"] == "healthy"
    connectors_healthy = healthy_count == total_count
    if core_healthy and connectors_healthy:
        overall = "healthy"
    elif core_healthy:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return {
        "status": overall,
        "version": APP_VERSION,
        "env": settings.env,
        "checks": checks,
    }


@router.get("/health/liveness")
async def liveness():
    """Lightweight liveness probe — just confirms the process is running."""
    return {"status": "alive"}
