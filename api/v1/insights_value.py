# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Money saved: the baselines and rates behind the estimate, edited and confirmed by a tenant administrator.

``GET /insights/value`` returns the tenant's baselines and rates (seeding the
defaults it lacks), the ROI floor and, for the period, the estimate's summary
(administrators and the CFO). Changes are for an active human administrator
(API keys and agent tokens are refused): ``POST /insights/value/baselines``
adds one (an agent type the defaults do not price, or a task type of one),
``PATCH /insights/value/baselines/{id}`` and ``PATCH /insights/value/rates/{id}``
change one, ``POST /insights/value/rates`` adds a role, ``POST
/insights/value/confirm`` confirms seeded rows as they are, and ``PUT
/insights/value/settings`` sets the ROI floor. An administrator's own figure
counts as confirmed by them. Every change writes one signed
``insights.value.change`` audit row with the old and new figures. While
Insights is off for the tenant every route answers 404 ``insights_disabled``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field, field_validator

from api.deps import ActiveHumanAdmin, get_active_human_admin, get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import FromDay, ToDay, _context, _require_enabled, _tenant
from core.database import get_tenant_session
from core.insights import value
from core.insights.audit import audit_entry
from core.insights.metrics import money
from core.ownership import caller_from_request

router = APIRouter(prefix="/insights/value", tags=["Insights"])

Currency = Literal["INR", "USD"]
_NAME = r"^[a-z][a-z0-9_]{0,49}$"
_TYPE = r"^[A-Za-z0-9_.:-]{1,100}$"


class BaselineIn(BaseModel):
    agent_type: str = Field(pattern=_TYPE)
    task_type: str = Field(default=value.ANY_TASK, pattern=r"^(\*|[A-Za-z0-9_.:-]{1,100})$")
    baseline_minutes: Decimal = Field(gt=0, le=1440, max_digits=8, decimal_places=2)
    role: str = Field(pattern=_NAME)
    setup_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)


class BaselinePatch(BaseModel):
    baseline_minutes: Decimal | None = Field(default=None, gt=0, le=1440, max_digits=8, decimal_places=2)
    role: str | None = Field(default=None, pattern=_NAME)
    setup_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)


class RateIn(BaseModel):
    role: str = Field(pattern=_NAME)
    hourly_cost: Decimal = Field(ge=0, le=Decimal("1000000"), max_digits=12, decimal_places=2)
    currency: Currency


class RatePatch(BaseModel):
    hourly_cost: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"), max_digits=12, decimal_places=2)
    currency: Currency | None = None


class ConfirmIn(BaseModel):
    baseline_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    rate_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class SettingsIn(BaseModel):
    roi_floor: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000"))

    @field_validator("roi_floor")
    @classmethod
    def _whole(cls, v: Decimal | None) -> Decimal | None:
        return v.quantize(Decimal("1")) if v is not None else None


Admin = Annotated[ActiveHumanAdmin, Depends(get_active_human_admin)]
RowId = Annotated[uuid.UUID, Path()]


def _readable(request: Request) -> None:
    caller = caller_from_request(request)
    if not (caller.is_admin or (caller.role or "").lower() == "cfo"):
        raise HTTPException(403, "insights_value_forbidden")


def _baseline(b: Any) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "agent_type": b.agent_type,
        "task_type": b.task_type,
        "baseline_minutes": float(b.baseline_minutes),
        "role": b.role,
        "setup_cost": float(b.setup_cost) if b.setup_cost is not None else None,
        "source": b.source,
        "confirmed": b.confirmed_at is not None,
        "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


def _rate(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "role": r.role,
        "hourly_cost": float(r.hourly_cost),
        "currency": r.currency,
        "source": r.source,
        "citation": r.citation,
        "confirmed": r.confirmed_at is not None,
        "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _summary(est: value.ValueEstimate) -> dict[str, Any]:
    confirmed = est.confirmed
    baselines, rates = est.unconfirmed_ids()
    return {
        "currency": est.currency,
        "confirmed": confirmed,
        "confidence": est.confidence,
        "completed_tasks": sum(line.completed for line in est.priced),
        "failed_tasks": sum(line.failed for line in est.priced),
        "unpriced_task_types": sorted({line.agent_type for line in est.unpriced}),
        "hours_saved": round(float(est.hours_saved), 1) if confirmed else None,
        "waterfall": value.waterfall(est) if confirmed else None,
        "roi_percent": round(float(est.roi) * 100, 1) if confirmed and est.roi is not None else None,
        "review_items": est.review_items,
        "spend": money(est.spend) if est.spend is not None else None,
        "hints": est.hints,
        # The baselines and rates the period uses that are not confirmed yet.
        "unconfirmed": {"baseline_ids": baselines, "rate_ids": rates},
    }


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="idempotent-read",
    audit_event="insights.value.read",
)
async def get_value(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    from_: FromDay = None,
    to: ToDay = None,
) -> dict[str, Any]:
    """The baselines, rates, ROI floor and the period's estimate summary (administrators and the CFO)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    _readable(request)
    caller = caller_from_request(request)
    async with get_tenant_session(tid) as session:
        ctx = await _context(session, tid, caller, start=from_, end=to, compare="none", company=None)
        est = await value.estimate_for(ctx, ctx.period)
        assumptions = ctx.cache["value_assumptions"]
        return {
            "period": ctx.period.as_dict(),
            "currency": est.currency,
            "baselines": [_baseline(b) for b in assumptions.baselines],
            "rates": [_rate(r) for r in assumptions.rates.values()],
            "roi_floor": float(assumptions.roi_floor),
            "roi_floor_custom": assumptions.roi_floor_custom,
            "summary": _summary(est),
            "can_edit": bool(caller.is_admin and not caller.is_machine),
            "lines": value.lines_as_rows(est),
        }


def _change(admin: ActiveHumanAdmin, kind: str, row_id: Any, before: Any, after: Any) -> Any:
    return audit_entry(
        admin.tenant_id,
        event_type="insights.value.change",
        actor_id=str(admin.user_id),
        action=kind,
        resource_type="insights_value",
        resource_id=str(row_id),
        details={"change": kind, "before": before, "after": after},
    )


async def _require_tenant(admin: ActiveHumanAdmin, tenant_id: str) -> uuid.UUID:
    tid = _tenant(tenant_id)
    if tid != admin.tenant_id:
        raise HTTPException(403, "insights_value_forbidden")
    await _require_enabled(tid)
    return tid


@router.post("/baselines", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.value.write",
    rate_limit="reporting-admin",
    idempotency="conflict-on-duplicate",
    audit_event="insights.value.change",
)
async def add_baseline(body: BaselineIn, admin: Admin, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Add a baseline (409 if the agent type and task type already have one). It counts as confirmed by you."""
    from sqlalchemy import select

    from core.models.insights import ValueBaseline

    tid = await _require_tenant(admin, tenant_id)
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        exists = (
            await session.execute(
                select(ValueBaseline.id).where(
                    ValueBaseline.tenant_id == tid,
                    ValueBaseline.agent_type == body.agent_type,
                    ValueBaseline.task_type == body.task_type,
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise HTTPException(409, detail={"error": "baseline_exists", "id": str(exists)})
        row = ValueBaseline(
            id=uuid.uuid4(),
            tenant_id=tid,
            agent_type=body.agent_type,
            task_type=body.task_type,
            baseline_minutes=body.baseline_minutes,
            role=body.role,
            setup_cost=body.setup_cost,
            source="tenant",
            confirmed_by=admin.user_id,
            confirmed_at=now,
            updated_by=admin.user_id,
            updated_at=now,
        )
        session.add(row)
        session.add(_change(admin, "baseline.add", row.id, None, body.model_dump(mode="json")))
        await session.flush()
        return _baseline(row)


@router.patch("/baselines/{baseline_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.value.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-update",
    audit_event="insights.value.change",
)
async def edit_baseline(
    baseline_id: RowId, body: BaselinePatch, admin: Admin, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Change a baseline's minutes, role or setup cost. Your figure counts as confirmed by you."""
    from sqlalchemy import select

    from core.models.insights import ValueBaseline

    tid = await _require_tenant(admin, tenant_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, detail={"error": "nothing_to_change", "message": "send minutes, role or setup cost"})
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(
                select(ValueBaseline)
                .where(ValueBaseline.tenant_id == tid, ValueBaseline.id == baseline_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "baseline_not_found")
        before = _baseline(row)
        if "baseline_minutes" in changes and changes["baseline_minutes"] is None:
            raise HTTPException(422, detail={"error": "invalid_minutes", "message": "minutes cannot be empty"})
        if "role" in changes and changes["role"] is None:
            raise HTTPException(422, detail={"error": "invalid_role", "message": "role cannot be empty"})
        for name, new in changes.items():
            setattr(row, name, new)
        now = datetime.now(UTC)
        row.source = "tenant"
        row.confirmed_by = admin.user_id
        row.confirmed_at = now
        row.updated_by = admin.user_id
        row.updated_at = now
        await session.flush()
        after = _baseline(row)
        session.add(_change(admin, "baseline.edit", row.id, before, after))
        return after


@router.post("/rates", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.value.write",
    rate_limit="reporting-admin",
    idempotency="conflict-on-duplicate",
    audit_event="insights.value.change",
)
async def add_rate(body: RateIn, admin: Admin, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Add a role's hourly cost (409 if the role has one). It counts as confirmed by you."""
    from sqlalchemy import select

    from core.models.insights import ValueRate

    tid = await _require_tenant(admin, tenant_id)
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        exists = (
            await session.execute(select(ValueRate.id).where(ValueRate.tenant_id == tid, ValueRate.role == body.role))
        ).scalar_one_or_none()
        if exists is not None:
            raise HTTPException(409, detail={"error": "rate_exists", "id": str(exists)})
        row = ValueRate(
            id=uuid.uuid4(),
            tenant_id=tid,
            role=body.role,
            hourly_cost=body.hourly_cost,
            currency=body.currency,
            source="tenant",
            citation=None,
            confirmed_by=admin.user_id,
            confirmed_at=now,
            updated_by=admin.user_id,
            updated_at=now,
        )
        session.add(row)
        session.add(_change(admin, "rate.add", row.id, None, body.model_dump(mode="json")))
        await session.flush()
        return _rate(row)


@router.patch("/rates/{rate_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.value.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-update",
    audit_event="insights.value.change",
)
async def edit_rate(
    rate_id: RowId, body: RatePatch, admin: Admin, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Change a role's hourly cost or currency. Your figure replaces the cited default and counts as confirmed."""
    from sqlalchemy import select

    from core.models.insights import ValueRate

    tid = await _require_tenant(admin, tenant_id)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(422, detail={"error": "nothing_to_change", "message": "send the hourly cost or currency"})
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(
                select(ValueRate).where(ValueRate.tenant_id == tid, ValueRate.id == rate_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "rate_not_found")
        before = _rate(row)
        for name, new in changes.items():
            setattr(row, name, new)
        now = datetime.now(UTC)
        row.source = "tenant"
        row.citation = None
        row.confirmed_by = admin.user_id
        row.confirmed_at = now
        row.updated_by = admin.user_id
        row.updated_at = now
        await session.flush()
        after = _rate(row)
        session.add(_change(admin, "rate.edit", row.id, before, after))
        return after


@router.post("/confirm")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.value.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-first-write-wins",
    audit_event="insights.value.change",
)
async def confirm(body: ConfirmIn, admin: Admin, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Confirm baselines and rates as they are. Rows already confirmed keep their first confirmation."""
    from sqlalchemy import update

    from core.models.insights import ValueBaseline, ValueRate

    tid = await _require_tenant(admin, tenant_id)
    if not body.baseline_ids and not body.rate_ids:
        raise HTTPException(422, detail={"error": "nothing_to_confirm", "message": "send baseline or rate ids"})
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        confirmed_baselines: list[str] = []
        confirmed_rates: list[str] = []
        if body.baseline_ids:
            result = await session.execute(
                update(ValueBaseline)
                .where(
                    ValueBaseline.tenant_id == tid,
                    ValueBaseline.id.in_(body.baseline_ids),
                    ValueBaseline.confirmed_at.is_(None),
                )
                .values(confirmed_by=admin.user_id, confirmed_at=now)
                .returning(ValueBaseline.id)
            )
            confirmed_baselines = [str(row[0]) for row in result]
        if body.rate_ids:
            result = await session.execute(
                update(ValueRate)
                .where(ValueRate.tenant_id == tid, ValueRate.id.in_(body.rate_ids), ValueRate.confirmed_at.is_(None))
                .values(confirmed_by=admin.user_id, confirmed_at=now)
                .returning(ValueRate.id)
            )
            confirmed_rates = [str(row[0]) for row in result]
        if confirmed_baselines or confirmed_rates:
            session.add(
                _change(
                    admin,
                    "confirm",
                    "assumptions",
                    None,
                    {"baseline_ids": confirmed_baselines, "rate_ids": confirmed_rates},
                )
            )
    return {"confirmed_baselines": confirmed_baselines, "confirmed_rates": confirmed_rates}


@router.put("/settings")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.value.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-update",
    audit_event="insights.value.change",
)
async def put_settings(body: SettingsIn, admin: Admin, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Set the ROI floor in the reporting currency (null restores the default: INR 5,000 or USD 60)."""
    from sqlalchemy import select

    from core.models.tenant import Tenant

    tid = await _require_tenant(admin, tenant_id)
    async with get_tenant_session(tid) as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tid).with_for_update())).scalar_one()
        current = dict(tenant.settings or {})
        before = current.get(value.ROI_FLOOR_KEY)
        if body.roi_floor is None:
            current.pop(value.ROI_FLOOR_KEY, None)
        else:
            current[value.ROI_FLOOR_KEY] = str(body.roi_floor)
        tenant.settings = current
        session.add(_change(admin, "settings.roi_floor", tid, before, current.get(value.ROI_FLOOR_KEY)))
    return {"roi_floor": float(body.roi_floor) if body.roi_floor is not None else None}
