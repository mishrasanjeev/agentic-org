"""Health check endpoint — verifies DB and Redis connectivity."""
from __future__ import annotations
from fastapi import APIRouter
from sqlalchemy import text
from core.config import settings
from core.database import async_session_factory
import redis.asyncio as aioredis

router = APIRouter()

APP_VERSION = "2.1.0"


@router.get("/health")
async def health_check():
    """Full health check — verifies DB and Redis are reachable."""
    checks = {"db": "unknown", "redis": "unknown"}

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

    overall = "healthy" if all(v == "healthy" for v in checks.values()) else "degraded"

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
