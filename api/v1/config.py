"""Fleet configuration endpoints."""

from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.tenant import Tenant
from core.schemas.api import FleetLimits

router = APIRouter(dependencies=[require_tenant_admin])

_FLEET_LIMITS_KEY = "fleet_limits"


# ── GET /config/fleet_limits ─────────────────────────────────────────────────
@router.get("/config/fleet_limits")
async def get_fleet_limits(tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(404, "Tenant not found")

        stored = (tenant.settings or {}).get(_FLEET_LIMITS_KEY)

    if stored:
        return FleetLimits(**stored).model_dump()
    # Return defaults if nothing is stored yet
    return FleetLimits().model_dump()


# ── PUT /config/fleet_limits ─────────────────────────────────────────────────
@router.put("/config/fleet_limits")
async def update_fleet_limits(
    body: FleetLimits,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(404, "Tenant not found")

        # Merge into existing settings JSONB
        settings = dict(tenant.settings or {})
        settings[_FLEET_LIMITS_KEY] = body.model_dump()
        tenant.settings = settings

    return body.model_dump()
