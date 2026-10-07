# SPDX-License-Identifier: Apache-2.0
"""FinOps: cost attribution by use case, application, business unit, department and cost centre."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.database import get_tenant_session
from core.finops import attribution, thresholds

logger = structlog.get_logger()
router = APIRouter()


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "finops_attribution_disabled",
            "message": "Cost attribution is off for this deployment (AGENTICORG_FINOPS_ATTRIBUTION_ENABLED).",
        },
    )


@router.get("/finops/attribution", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="finops.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="finops.attribution.read",
)
async def cost_attribution(
    days: int = Query(30, ge=1, le=attribution.MAX_DAYS),
    group_by: str = Query("use_case", max_length=32),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The attributed cost ledger folded by one dimension over the window, with totals and the unattributed share."""
    if not attribution.enabled():
        raise _off()
    if group_by not in attribution.DIMENSIONS:
        raise HTTPException(
            422,
            detail={"error": "unknown_dimension", "message": f"group_by is one of {', '.join(attribution.DIMENSIONS)}"},
        )
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        return await attribution.summary(session, tid, days=days, group_by=group_by)


# ── Thresholds ────────────────────────────────────────────────────────────────


class ThresholdIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=120)
    scope_kind: str = Field(..., max_length=16)
    scope_value: str | None = Field(None, max_length=64)
    period: str = Field("monthly", max_length=8)
    threshold_usd: float = Field(..., gt=0)
    action: str = Field("alert", max_length=16)
    throttle_seconds: int = Field(thresholds.DEFAULT_THROTTLE_SECONDS, ge=1, le=thresholds.MAX_THROTTLE_SECONDS)
    enabled: bool = True
    notify_channels: list[str] = Field(default_factory=lambda: ["email"], max_length=4)


class ThresholdPatch(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(None, min_length=1, max_length=120)
    scope_kind: str | None = Field(None, max_length=16)
    scope_value: str | None = Field(None, max_length=64)
    period: str | None = Field(None, max_length=8)
    threshold_usd: float | None = Field(None, gt=0)
    action: str | None = Field(None, max_length=16)
    throttle_seconds: int | None = Field(None, ge=1, le=thresholds.MAX_THROTTLE_SECONDS)
    enabled: bool | None = None
    notify_channels: list[str] | None = Field(None, max_length=4)
    lifted_until: str | None = Field(None, max_length=40)


def _thresholds_off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "finops_thresholds_disabled",
            "message": "Cost thresholds are off for this deployment (AGENTICORG_FINOPS_THRESHOLDS_ENABLED).",
        },
    )


def _threshold_refused(exc: thresholds.ThresholdError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


async def _flush_threshold(session: Any, name: str | None) -> None:
    """Flush a created or changed threshold; a name the tenant already uses is a 409, not a 500."""
    from sqlalchemy.exc import IntegrityError

    try:
        await session.flush()
    except IntegrityError as exc:
        if "ux_finops_thresholds_tenant_name" not in str(getattr(exc, "orig", exc)):
            raise
        raise HTTPException(
            409,
            detail={"error": "duplicate_name", "message": f"A threshold named {name!r} already exists"},
        ) from None


async def _threshold_row(session: Any, tid: uuid.UUID, threshold_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.finops_threshold import FinopsThreshold

    row = (
        await session.execute(
            select(FinopsThreshold).where(FinopsThreshold.tenant_id == tid, FinopsThreshold.id == threshold_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such threshold"})
    return row


@router.get("/finops/thresholds", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="finops.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="finops.thresholds.list",
)
async def list_thresholds(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Every threshold with its spend, share and breach state over its period, and the policy's bounds."""
    if not thresholds.enabled():
        raise _thresholds_off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        rows = await thresholds.status(session, tid)
    return {
        "thresholds": rows,
        "total": len(rows),
        "breached": sum(1 for r in rows if r["breached"]),
        "scopes": list(thresholds.SCOPES),
        "actions": list(thresholds.ACTIONS),
        "periods": list(thresholds.PERIODS),
    }


@router.post("/finops/thresholds", dependencies=[require_tenant_admin], status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="finops.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="finops.thresholds.create",
)
async def create_threshold(
    body: ThresholdIn,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a threshold: a scope, a period, an amount and an action."""
    if not thresholds.enabled():
        raise _thresholds_off()
    from core.models.finops_threshold import FinopsThreshold

    try:
        fields = thresholds.parse_fields(body.model_dump())
    except thresholds.ThresholdError as exc:
        raise _threshold_refused(exc) from None
    tid = uuid.UUID(tenant_id)
    actor = _user_uuid_from_claims(user)
    async with get_tenant_session(tid) as session:
        if await thresholds.count_rows(session, tid) >= thresholds.MAX_THRESHOLDS:
            raise HTTPException(
                409,
                detail={
                    "error": "threshold_limit",
                    "message": f"A tenant holds at most {thresholds.MAX_THRESHOLDS} thresholds; remove one first",
                },
            )
        row = FinopsThreshold(tenant_id=tid, created_by=actor, updated_by=actor, **fields)
        session.add(row)
        await _flush_threshold(session, fields.get("name"))
        return thresholds.row_dict(row)


@router.put("/finops/thresholds/{threshold_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="finops.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-partial-update",
    audit_event="finops.thresholds.update",
)
async def update_threshold(
    threshold_id: uuid.UUID,
    body: ThresholdPatch,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Change a threshold's fields, disable it, or lift a suspension until a time."""
    if not thresholds.enabled():
        raise _thresholds_off()
    try:
        fields = thresholds.parse_fields(body.model_dump(exclude_unset=True), partial=True)
    except thresholds.ThresholdError as exc:
        raise _threshold_refused(exc) from None
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        row = await _threshold_row(session, tid, threshold_id)
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_by = _user_uuid_from_claims(user)
        await _flush_threshold(session, fields.get("name", row.name))
        return thresholds.row_dict(row)


@router.delete("/finops/thresholds/{threshold_id}", dependencies=[require_tenant_admin], status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="finops.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-delete",
    audit_event="finops.thresholds.delete",
)
async def delete_threshold(
    threshold_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> None:
    """Remove a threshold."""
    if not thresholds.enabled():
        raise _thresholds_off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        row = await _threshold_row(session, tid, threshold_id)
        await session.delete(row)
        await session.flush()
    logger.info("finops_threshold_deleted", threshold_id=str(threshold_id), by=str(_user_uuid_from_claims(user)))
    return None
