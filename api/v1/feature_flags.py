"""Feature flag management endpoints.

CRUD for per-tenant feature flag overrides. Global defaults (tenant_id
NULL) are seeded via SQL / migrations — this API only exposes the
tenant-scoped view.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.feature_flags import clear_cache, is_enabled
from core.models.feature_flag import FeatureFlag

logger = structlog.get_logger()
router = APIRouter(prefix="/feature-flags", tags=["Feature Flags"], dependencies=[require_tenant_admin])


class FlagIn(BaseModel):
    flag_key: str = Field(..., min_length=1, max_length=100)
    enabled: bool = True
    rollout_percentage: int = Field(100, ge=0, le=100)
    description: str | None = Field(None, max_length=500)


class FlagOut(BaseModel):
    id: uuid.UUID
    flag_key: str
    enabled: bool
    rollout_percentage: int
    description: str | None


class FlagEvaluation(BaseModel):
    flag_key: str
    enabled: bool


@router.post("", response_model=FlagOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.runtime_control.sensitive.write",
    rate_limit="feature-flag-write",
    idempotency="idempotent-upsert-by-flag-key",
    audit_event="feature_flags.upsert",
)
async def upsert_flag(
    body: FlagIn,
    tenant_id: str = Depends(get_current_tenant),
) -> FlagOut:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(FeatureFlag).where(
                FeatureFlag.tenant_id == tid,
                FeatureFlag.flag_key == body.flag_key,
            )
        )
        flag = result.scalar_one_or_none()
        if flag is None:
            flag = FeatureFlag(
                tenant_id=tid,
                flag_key=body.flag_key,
                enabled=body.enabled,
                rollout_percentage=body.rollout_percentage,
                description=body.description,
            )
            session.add(flag)
        else:
            flag.enabled = body.enabled
            flag.rollout_percentage = body.rollout_percentage
            flag.description = body.description
        await session.flush()

    clear_cache()
    logger.info(
        "feature_flag_updated",
        tenant_id=tenant_id,
        flag_key=body.flag_key,
        enabled=body.enabled,
        rollout=body.rollout_percentage,
    )
    return FlagOut(
        id=flag.id,
        flag_key=flag.flag_key,
        enabled=flag.enabled,
        rollout_percentage=flag.rollout_percentage,
        description=flag.description,
    )


@router.get("", response_model=list[FlagOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.runtime_control.sensitive.list",
    rate_limit="feature-flag-read",
    idempotency="read-only",
    audit_event="feature_flags.list",
)
async def list_flags(
    tenant_id: str = Depends(get_current_tenant),
) -> list[FlagOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(FeatureFlag).where(FeatureFlag.tenant_id == tid)
        )
        rows = result.scalars().all()
        return [
            FlagOut(
                id=f.id,
                flag_key=f.flag_key,
                enabled=f.enabled,
                rollout_percentage=f.rollout_percentage,
                description=f.description,
            )
            for f in rows
        ]


@router.get("/{flag_key}/evaluate", response_model=FlagEvaluation)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.runtime_control.evaluate",
    rate_limit="feature-flag-evaluate",
    idempotency="read-only",
    audit_event="feature_flags.evaluate",
)
async def evaluate_flag(
    flag_key: str,
    user_id: uuid.UUID | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> FlagEvaluation:
    """Cheap client-side evaluation — used by frontends to render UI."""
    tid = uuid.UUID(tenant_id)
    enabled = await is_enabled(flag_key, tenant_id=tid, user_id=user_id)
    return FlagEvaluation(flag_key=flag_key, enabled=enabled)


@router.delete("/{flag_key}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.runtime_control.sensitive.write",
    rate_limit="feature-flag-write",
    idempotency="idempotent-delete-by-flag-key",
    audit_event="feature_flags.delete",
)
async def delete_flag(
    flag_key: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(FeatureFlag).where(
                FeatureFlag.tenant_id == tid, FeatureFlag.flag_key == flag_key
            )
        )
        flag = result.scalar_one_or_none()
        if flag is None:
            raise HTTPException(404, "Flag not found")
        await session.delete(flag)
    clear_cache()
    logger.info("feature_flag_deleted", tenant_id=tenant_id, flag_key=flag_key)
