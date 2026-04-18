"""Per-tenant governance configuration API.

GET  /api/v1/governance/config   — read the tenant's governance settings.
PUT  /api/v1/governance/config   — update them (admin only, audit-logged).

See Enterprise Readiness Plan Phase 4 and
docs/mcp-product-model.md for context. Before PR-B the Settings page's
PII/region/retention controls were UI-only React state — this module
persists them and closes the compliance story.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.audit import AuditLog
from core.models.governance_config import GovernanceConfig

router = APIRouter(prefix="/governance", tags=["Governance"])
logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
DataRegion = Literal["IN", "EU", "US"]


class GovernanceConfigOut(BaseModel):
    pii_masking: bool
    data_region: DataRegion
    audit_retention_years: int = Field(ge=1, le=10)
    updated_by: str | None = None
    updated_at: str | None = None


class GovernanceConfigUpdate(BaseModel):
    """Partial update — any omitted field keeps its current value."""

    pii_masking: bool | None = None
    data_region: DataRegion | None = None
    audit_retention_years: int | None = Field(default=None, ge=1, le=10)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/config", response_model=GovernanceConfigOut)
async def get_config(tenant_id: str = Depends(get_current_tenant)) -> GovernanceConfigOut:
    """Return this tenant's governance config. Creates a defaulted row on
    first read so every tenant has a stable persisted baseline."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        row = await session.get(GovernanceConfig, tid)
        if row is None:
            row = GovernanceConfig(tenant_id=tid)
            session.add(row)
            await session.flush()
            # server_default fires on the actual INSERT — pull the populated
            # values back so the response is accurate on first read.
            await session.refresh(row)
        return GovernanceConfigOut(
            pii_masking=row.pii_masking,
            data_region=row.data_region,  # type: ignore[arg-type]
            audit_retention_years=row.audit_retention_years,
            updated_by=str(row.updated_by) if row.updated_by else None,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )


@router.put(
    "/config",
    response_model=GovernanceConfigOut,
    dependencies=[require_tenant_admin],
)
async def put_config(
    body: GovernanceConfigUpdate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> GovernanceConfigOut:
    """Update this tenant's governance config. Admin-only.

    Each changed field writes an AuditLog row (event_type=
    'governance_config.change') with old + new values in ``details``, in
    the same transaction as the update itself.
    """
    tid = _uuid.UUID(tenant_id)
    claims = getattr(request.state, "claims", {}) or {}
    actor_id = str(claims.get("sub") or claims.get("user_id") or "unknown")

    async with get_tenant_session(tid) as session:
        row = await session.get(GovernanceConfig, tid)
        if row is None:
            row = GovernanceConfig(tenant_id=tid)
            session.add(row)
            await session.flush()

        old_values: dict[str, Any] = {
            "pii_masking": row.pii_masking,
            "data_region": row.data_region,
            "audit_retention_years": row.audit_retention_years,
        }

        new_values = old_values.copy()
        if body.pii_masking is not None:
            new_values["pii_masking"] = body.pii_masking
        if body.data_region is not None:
            new_values["data_region"] = body.data_region
        if body.audit_retention_years is not None:
            new_values["audit_retention_years"] = body.audit_retention_years

        changed = {k: (old_values[k], new_values[k]) for k in new_values if old_values[k] != new_values[k]}
        if not changed:
            # No-op; still refresh + return current state.
            return GovernanceConfigOut(
                pii_masking=row.pii_masking,
                data_region=row.data_region,  # type: ignore[arg-type]
                audit_retention_years=row.audit_retention_years,
                updated_by=str(row.updated_by) if row.updated_by else None,
                updated_at=row.updated_at.isoformat() if row.updated_at else None,
            )

        # Apply + audit in the same transaction so a failed audit write
        # rolls back the config change.
        row.pii_masking = new_values["pii_masking"]
        row.data_region = new_values["data_region"]
        row.audit_retention_years = new_values["audit_retention_years"]
        try:
            row.updated_by = _uuid.UUID(actor_id)
        except (ValueError, TypeError):
            row.updated_by = None

        session.add(
            AuditLog(
                tenant_id=tid,
                event_type="governance_config.change",
                actor_type="user",
                actor_id=actor_id,
                resource_type="governance_config",
                resource_id=str(tid),
                action="update",
                outcome="success",
                details={"changes": {k: {"old": v[0], "new": v[1]} for k, v in changed.items()}},
            )
        )
        await session.flush()
        await session.refresh(row)

    logger.info(
        "governance_config_updated",
        tenant_id=str(tid),
        actor_id=actor_id,
        changed_fields=list(changed.keys()),
    )

    return GovernanceConfigOut(
        pii_masking=row.pii_masking,
        data_region=row.data_region,  # type: ignore[arg-type]
        audit_retention_years=row.audit_retention_years,
        updated_by=str(row.updated_by) if row.updated_by else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )
