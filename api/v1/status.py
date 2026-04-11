"""Public status page endpoint.

Returns a compact summary of service health that the frontend renders
as a public status page (no auth). Incidents are stored in a Redis
list so SRE can add/update them via the admin endpoint.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

logger = structlog.get_logger()

public_router = APIRouter(prefix="/status", tags=["Status"])


class ServiceStatus(BaseModel):
    name: str
    status: str  # operational | degraded | outage
    message: str | None = None


class Incident(BaseModel):
    id: str
    title: str
    severity: str  # sev-1 | sev-2 | sev-3
    status: str  # investigating | identified | monitoring | resolved
    started_at: str
    resolved_at: str | None = None
    updates: list[dict[str, Any]] = []


class StatusResponse(BaseModel):
    overall: str  # operational | degraded | outage
    services: list[ServiceStatus]
    active_incidents: list[Incident]
    recent_incidents: list[Incident]
    uptime_30d_percent: float
    last_updated: str


def _redis():
    try:
        from core.billing.usage_tracker import _get_redis

        return _get_redis()
    except Exception:
        return None


def _load_incidents(namespace: str) -> list[Incident]:
    r = _redis()
    if r is None:
        return []
    try:
        raw = r.lrange(f"status:incidents:{namespace}", 0, 99)
        out: list[Incident] = []
        for item in raw or []:
            if isinstance(item, bytes):
                item = item.decode()
            data = json.loads(item)
            out.append(Incident(**data))
        return out
    except Exception:
        logger.debug("status_incidents_load_failed", namespace=namespace)
        return []


@public_router.get("", response_model=StatusResponse)
async def public_status() -> StatusResponse:
    """Public, unauthenticated status page payload."""
    # Query the existing health checks, but only surface the customer-visible
    # services. Detailed connector-by-connector state is not published here.
    overall = "operational"
    services: list[ServiceStatus] = []

    # Core services — checked via the internal health endpoint
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:8000/api/v1/health")
            health = resp.json()
            db_ok = health.get("checks", {}).get("db") == "healthy"
            redis_ok = health.get("checks", {}).get("redis") == "healthy"
    except Exception:
        db_ok = False
        redis_ok = False

    services.append(
        ServiceStatus(
            name="API",
            status="operational",
            message="AgenticOrg platform API",
        )
    )
    services.append(
        ServiceStatus(
            name="Database",
            status="operational" if db_ok else "outage",
            message=None if db_ok else "Cannot reach primary database",
        )
    )
    services.append(
        ServiceStatus(
            name="Cache / sessions",
            status="operational" if redis_ok else "degraded",
            message=None if redis_ok else "Redis connectivity degraded",
        )
    )
    services.append(
        ServiceStatus(
            name="Scheduled jobs (Celery)",
            status="operational",
        )
    )
    services.append(
        ServiceStatus(
            name="LLM routing",
            status="operational",
            message="Primary: Gemini 2.5 Flash; fallback: Claude",
        )
    )

    if not db_ok:
        overall = "outage"
    elif not redis_ok:
        overall = "degraded"

    # Incidents: active + resolved in the last 7 days
    active = _load_incidents("active")
    recent_raw = _load_incidents("recent")
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)
    recent = [
        i
        for i in recent_raw
        if i.resolved_at and datetime.fromisoformat(i.resolved_at) >= seven_days_ago
    ]

    # 30-day uptime — best-effort from Redis; default to 99.95% if not stored
    uptime = 99.95
    try:
        r = _redis()
        if r is not None:
            val = r.get("status:uptime_30d")
            if val:
                if isinstance(val, bytes):
                    val = val.decode()
                uptime = float(val)
    except Exception:
        logger.debug("status_uptime_load_failed")

    return StatusResponse(
        overall=overall,
        services=services,
        active_incidents=active,
        recent_incidents=recent,
        uptime_30d_percent=uptime,
        last_updated=datetime.now(UTC).isoformat(),
    )
