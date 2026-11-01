"""Tenant configuration endpoints: fleet limits and reporting settings."""

from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.insights.audit import audit_entry
from core.insights.reporting_settings import (
    REPORTING_SETTINGS_KEY,
    ReportingSettings,
    settings_from_tenant,
    store_reporting_settings,
)
from core.models.tenant import Tenant
from core.schemas.api import FleetLimits

router = APIRouter(dependencies=[require_tenant_admin])

_FLEET_LIMITS_KEY = "fleet_limits"


# ── GET /config/fleet_limits ─────────────────────────────────────────────────
@router.get("/config/fleet_limits")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="config.fleet_limits.control_plane.sensitive.read",
    rate_limit="config-read",
    idempotency="read-only",
    audit_event="config.fleet_limits.read",
)
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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="config.fleet_limits.control_plane.sensitive.write",
    rate_limit="config-write",
    idempotency="idempotent-upsert-by-tenant",
    audit_event="config.fleet_limits.update",
)
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


# ── GET /config/reporting ────────────────────────────────────────────────────
@router.get("/config/reporting")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="config.reporting.control_plane.read",
    rate_limit="config-read",
    idempotency="read-only",
    audit_event="config.reporting.read",
)
async def get_reporting_settings(tenant_id: str = Depends(get_current_tenant)):
    """The tenant's reporting time zone, fiscal year start and currency (region defaults until set)."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        current = settings_from_tenant(tenant.settings, tenant.data_region)
        stored = isinstance((tenant.settings or {}).get(REPORTING_SETTINGS_KEY), dict)
    return {**current.model_dump(), "stored": stored}


# ── PUT /config/reporting ────────────────────────────────────────────────────
@router.put("/config/reporting")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="config.reporting.control_plane.write",
    rate_limit="config-write",
    idempotency="idempotent-upsert-by-tenant",
    audit_event="config.reporting.write",
)
async def update_reporting_settings(
    body: ReportingSettings,
    tenant_id: str = Depends(get_current_tenant),
    claims: dict = Depends(get_current_user),
):
    """Replace the reporting settings; the change is audited (``config.reporting.write``) in the same transaction."""
    tid = _uuid.UUID(tenant_id)
    actor = str(claims.get("agenticorg:user_id") or claims.get("sub") or "")
    async with get_tenant_session(tid) as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        before = settings_from_tenant(tenant.settings, tenant.data_region)
        tenant.settings = store_reporting_settings(tenant.settings, body)
        session.add(
            audit_entry(
                tid,
                event_type="config.reporting.write",
                actor_id=actor,
                action="update",
                resource_type="reporting_settings",
                resource_id=str(tid),
                details={"before": before.model_dump(), "after": body.model_dump()},
            )
        )
    return {**body.model_dump(), "stored": True}
