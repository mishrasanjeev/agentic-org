# SPDX-License-Identifier: Apache-2.0
"""Operator override endpoints: place, list and release a halt or throttle.

Tenant administrators only. Each change writes a signed audit row
(``core.governance.operator_override``); enforcement happens at the model router,
the agent runner, the workflow engine and the tool dispatch boundary, and the run
endpoints refuse early with 423 while a matching override is active.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import operator_override as overrides
from core.models.operator_override import MODES, TARGET_KINDS, OperatorOverride
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter(prefix="/operator-overrides", tags=["Operator Overrides"], dependencies=[require_tenant_admin])

_SINGLE_TARGET_KINDS = frozenset(k for k in TARGET_KINDS if k not in ("all_agents", "tool_pipeline"))


class OverrideIn(BaseModel):
    target_kind: str = Field(
        ..., description="provider, model, agent, all_agents, workflow, connector, tool or tool_pipeline"
    )
    target_id: str = Field("", max_length=255)
    mode: str = Field("halt", description="halt or throttle")
    limit_per_minute: int | None = Field(None, ge=0, le=1_000_000)
    reason: str = Field(..., min_length=3, max_length=2000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> OverrideIn:
        if self.target_kind not in TARGET_KINDS:
            raise ValueError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if self.target_kind in _SINGLE_TARGET_KINDS and not self.target_id.strip():
            raise ValueError(f"target_kind {self.target_kind} needs a target_id")
        if self.mode == "throttle" and self.limit_per_minute is None:
            raise ValueError("a throttle needs limit_per_minute")
        if self.expires_at is not None:
            expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
            if expires <= datetime.now(UTC):
                raise ValueError("expires_at must be in the future")
            self.expires_at = expires
        return self


class OverrideOut(BaseModel):
    id: uuid.UUID
    target_kind: str
    target_id: str
    mode: str
    limit_per_minute: int | None
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime | None
    released_at: datetime | None
    released_by: str | None
    active: bool


def _actor(caller: Caller | None) -> str:
    user_id = getattr(caller, "user_id", None)
    return str(user_id) if user_id else "admin"


def _out(row: OperatorOverride) -> OverrideOut:
    now = datetime.now(UTC)
    active = row.released_at is None and (row.expires_at is None or row.expires_at > now)
    return OverrideOut(
        id=row.id,
        target_kind=row.target_kind,
        target_id=row.target_id or "",
        mode=row.mode,
        limit_per_minute=row.limit_per_minute,
        reason=row.reason,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        released_at=row.released_at,
        released_by=row.released_by,
        active=active,
    )


@router.get("", response_model=list[OverrideOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.operator_override.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="operator_overrides.list",
)
async def list_overrides(
    include_released: bool = False,
    tenant_id: str = Depends(get_current_tenant),
) -> list[OverrideOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(OperatorOverride).where(OperatorOverride.tenant_id == tid)
        if not include_released:
            query = query.where(OperatorOverride.released_at.is_(None))
        rows = (await session.execute(query.order_by(OperatorOverride.created_at.desc()))).scalars().all()
        return [_out(row) for row in rows]


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.operator_override.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="operator_overrides.status",
)
async def override_status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, object]:
    """Whether the control is on for this tenant and which overrides are active."""
    tid = uuid.UUID(tenant_id)
    enabled = await overrides.enabled(tid)
    active = await overrides.active_overrides(tid) if enabled else []
    return {"enabled": enabled, "active": [o.to_dict() for o in active]}


@router.post("", response_model=OverrideOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.operator_override.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="operator_overrides.set",
)
async def set_override(
    body: OverrideIn,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> OverrideOut:
    tid = uuid.UUID(tenant_id)
    try:
        placed = await overrides.set_override(
            tid,
            target_kind=body.target_kind,
            target_id=body.target_id,
            mode=body.mode,
            limit_per_minute=body.limit_per_minute,
            reason=body.reason,
            actor_id=_actor(caller),
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(select(OperatorOverride).where(OperatorOverride.id == uuid.UUID(placed.id)))
        ).scalar_one()
        return _out(row)


@router.post("/{override_id}/release", response_model=OverrideOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="feature_flags.operator_override.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-lifecycle-state",
    audit_event="operator_overrides.release",
)
async def release_override(
    override_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> OverrideOut:
    tid = uuid.UUID(tenant_id)
    released = await overrides.release_override(tid, override_id, actor_id=_actor(caller))
    if released is None:
        raise HTTPException(404, "No active override with that id")
    async with get_tenant_session(tid) as session:
        row = (await session.execute(select(OperatorOverride).where(OperatorOverride.id == override_id))).scalar_one()
        return _out(row)
