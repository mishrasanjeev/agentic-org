"""RPA Schedule API — CRUD + run-now + list available scripts.

Exposes tenant-scheduled RPA runs on top of the generic registry in
``rpa/scripts/_registry.py``. Mirrors the shape of /report-schedules
(validators, tenant isolation, cron discipline) so operators can use
it the same way.

Contract:
- Only tenant admins can create / update / delete schedules because
  some RPA scripts are ``admin_only`` (SSRF-capable) and admin-gating
  at the router level is the simpler control.
- ``cron_expression`` supports presets (daily/weekly/monthly/...) and
  full 5-field crons, mirroring the report-schedules validator.
- ``next_run_at`` is computed at write time using croniter when
  available, falling back to a coarse "now + 1 day" so the UI has a
  value to display.
"""

from __future__ import annotations

import re
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.rpa_schedule import RPASchedule

logger = structlog.get_logger()

# Admin gate at the router level — all endpoints below require admin.
# The generic RPA executor has an ``admin_only`` flag per-script too,
# but schedule creation is a privileged configuration op regardless.
router = APIRouter(prefix="/rpa-schedules", tags=["RPA"], dependencies=[require_tenant_admin])

_CRON_PRESETS = {
    "every_5_minutes",
    "every_15_minutes",
    "hourly",
    "daily",
    "weekly",
    "monthly",
}
_CRON_FIELD_RE = re.compile(r"^[\d*/,\-]+$")


def _is_valid_cron(expr: str) -> bool:
    expr = expr.strip()
    if expr in _CRON_PRESETS:
        return True
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        return croniter.is_valid(expr)
    except ImportError:
        pass
    parts = expr.split()
    if len(parts) != 5:
        return False
    return all(_CRON_FIELD_RE.match(p) for p in parts)


def _compute_next_run(cron_expression: str) -> datetime:
    now = datetime.now(UTC)
    presets = {
        "every_5_minutes": timedelta(minutes=5),
        "every_15_minutes": timedelta(minutes=15),
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }
    if cron_expression in presets:
        return now + presets[cron_expression]
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        return croniter(cron_expression, now).get_next(datetime)
    except (ImportError, ValueError, KeyError):
        return now + timedelta(days=1)


# ── Schemas ──────────────────────────────────────────────────────────


class RPAScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    script_key: str = Field(..., min_length=1, max_length=100)
    cron_expression: str = Field("daily", max_length=100)
    enabled: bool = True
    company_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, v: str) -> str:
        v = (v or "").strip()
        if not _is_valid_cron(v):
            raise ValueError(
                "cron_expression must be a preset "
                f"({', '.join(sorted(_CRON_PRESETS))}) or a valid "
                "5-field cron expression"
            )
        return v

    @field_validator("script_key")
    @classmethod
    def _validate_script_key(cls, v: str) -> str:
        v = v.strip()
        from rpa.scripts._registry import discover_scripts

        available = set(discover_scripts().keys())
        if v not in available:
            raise ValueError(
                f"unknown RPA script {v!r}. Available: "
                f"{sorted(available)}"
            )
        return v


class RPAScheduleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    cron_expression: str | None = Field(None, max_length=100)
    enabled: bool | None = None
    params: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    company_id: str | None = None

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not _is_valid_cron(v):
            raise ValueError(
                "cron_expression must be a preset or a valid 5-field "
                "cron expression"
            )
        return v


class RPAScheduleOut(BaseModel):
    id: str
    name: str
    script_key: str
    cron_expression: str
    enabled: bool
    company_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_run_status: str | None = None
    last_run_chunks_published: int | None = None
    last_run_chunks_rejected: int | None = None
    last_quality_avg: float | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _to_out(row: RPASchedule) -> RPAScheduleOut:
    return RPAScheduleOut(
        id=str(row.id),
        name=row.name,
        script_key=row.script_key,
        cron_expression=row.cron_expression,
        enabled=row.enabled,
        company_id=str(row.company_id) if row.company_id else None,
        params=row.params or {},
        config=row.config or {},
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        next_run_at=row.next_run_at.isoformat() if row.next_run_at else None,
        last_run_status=row.last_run_status,
        last_run_chunks_published=row.last_run_chunks_published,
        last_run_chunks_rejected=row.last_run_chunks_rejected,
        last_quality_avg=float(row.last_quality_avg) if row.last_quality_avg is not None else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


# ── Registry probe ───────────────────────────────────────────────────


@router.get("/registry")
async def list_registry_scripts(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Return every RPA script the platform can schedule.

    Output shape matches the RPAScheduleCreate validator's expectations
    (same ``script_key`` strings). Lets the UI build a dropdown without
    having to hardcode a list.
    """
    del tenant_id  # admin-gated at the router level; no tenant data here
    from rpa.scripts._registry import discover_scripts

    items = [
        {
            "script_key": key,
            "name": meta.get("name"),
            "description": meta.get("description"),
            "category": meta.get("category"),
            "params_schema": meta.get("params_schema", {}),
            "estimated_duration_s": meta.get("estimated_duration_s"),
            "target_quality": meta.get("target_quality"),
            "admin_only": meta.get("admin_only", False),
            "produces_chunks": meta.get("produces_chunks", False),
        }
        for key, meta in sorted(discover_scripts().items())
    ]
    return {"items": items, "total": len(items)}


# ── CRUD ─────────────────────────────────────────────────────────────


@router.get("", response_model=list[RPAScheduleOut])
async def list_schedules(
    company_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[RPAScheduleOut]:
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(RPASchedule).where(RPASchedule.tenant_id == tid)
        if company_id:
            try:
                cid = _uuid.UUID(company_id)
            except ValueError as exc:
                raise HTTPException(400, "Invalid company_id format") from exc
            from sqlalchemy import or_

            query = query.where(
                or_(
                    RPASchedule.company_id == cid,
                    RPASchedule.company_id.is_(None),
                )
            )
        query = query.order_by(RPASchedule.created_at.desc())
        result = await session.execute(query)
        rows = result.scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=RPAScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: RPAScheduleCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> RPAScheduleOut:
    tid = _uuid.UUID(tenant_id)
    company_uuid: _uuid.UUID | None = None
    if body.company_id:
        try:
            company_uuid = _uuid.UUID(body.company_id)
        except ValueError as exc:
            raise HTTPException(400, "Invalid company_id format") from exc

    try:
        next_run = _compute_next_run(body.cron_expression) if body.enabled else None
    except Exception:
        next_run = None

    row = RPASchedule(
        id=_uuid.uuid4(),
        tenant_id=tid,
        company_id=company_uuid,
        name=body.name.strip(),
        script_key=body.script_key.strip(),
        cron_expression=body.cron_expression,
        enabled=body.enabled,
        params=body.params or {},
        config=body.config or {},
        next_run_at=next_run,
    )
    async with get_tenant_session(tid) as session:
        session.add(row)
        try:
            await session.flush()
        except Exception as exc:
            logger.warning("rpa_schedule_create_failed", error=str(exc))
            raise HTTPException(
                409,
                f"Schedule with name {row.name!r} already exists for this tenant",
            ) from exc
    return _to_out(row)


@router.get("/{schedule_id}", response_model=RPAScheduleOut)
async def get_schedule(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> RPAScheduleOut:
    tid = _uuid.UUID(tenant_id)
    try:
        sid = _uuid.UUID(schedule_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid schedule_id format") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid,
                RPASchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Schedule not found")
    return _to_out(row)


@router.patch("/{schedule_id}", response_model=RPAScheduleOut)
async def update_schedule(
    schedule_id: str,
    body: RPAScheduleUpdate,
    tenant_id: str = Depends(get_current_tenant),
) -> RPAScheduleOut:
    tid = _uuid.UUID(tenant_id)
    try:
        sid = _uuid.UUID(schedule_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid schedule_id format") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid,
                RPASchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Schedule not found")

        if body.name is not None:
            row.name = body.name.strip()
        if body.cron_expression is not None:
            row.cron_expression = body.cron_expression
            # Recompute next_run_at on cron change
            if row.enabled:
                row.next_run_at = _compute_next_run(row.cron_expression)
        if body.enabled is not None:
            row.enabled = body.enabled
            row.next_run_at = (
                _compute_next_run(row.cron_expression) if body.enabled else None
            )
        if body.params is not None:
            row.params = body.params
        if body.config is not None:
            row.config = body.config
        if body.company_id is not None:
            try:
                row.company_id = _uuid.UUID(body.company_id) if body.company_id else None
            except ValueError as exc:
                raise HTTPException(400, "Invalid company_id format") from exc
        await session.flush()
    return _to_out(row)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = _uuid.UUID(tenant_id)
    try:
        sid = _uuid.UUID(schedule_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid schedule_id format") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid,
                RPASchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Schedule not found")
        await session.delete(row)


@router.post("/{schedule_id}/run-now")
async def run_schedule_now(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue an immediate execution of the schedule.

    Delegates to ``core.tasks.rpa_tasks.run_rpa_schedule`` via Celery so
    the HTTP response is cheap and the run can take minutes without
    blocking the API pod.
    """
    tid = _uuid.UUID(tenant_id)
    try:
        sid = _uuid.UUID(schedule_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid schedule_id format") from exc

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(RPASchedule).where(
                RPASchedule.id == sid,
                RPASchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Schedule not found")

    try:
        from core.tasks.rpa_tasks import run_rpa_schedule

        async_result = run_rpa_schedule.delay(str(row.tenant_id), str(row.id))
        task_id = getattr(async_result, "id", None)
    except Exception as exc:
        logger.warning("rpa_schedule_enqueue_failed", error=str(exc))
        raise HTTPException(
            503,
            "Could not enqueue RPA run — worker queue is unavailable. "
            "Try again after the Celery worker is running.",
        ) from exc

    return {
        "schedule_id": str(row.id),
        "task_id": task_id,
        "queued_at": datetime.now(UTC).isoformat(),
    }


# ── Dispatcher helper (used by the Celery beat task) ─────────────────


async def due_schedule_ids(now: datetime | None = None) -> list[tuple[str, str]]:
    """Return ``[(tenant_id, schedule_id), ...]`` for schedules whose
    ``next_run_at`` is in the past and ``enabled`` is true.

    Exposed so ``core.tasks.rpa_tasks.dispatch_due_rpa_schedules`` (a
    Celery beat task) can walk the list without opening a DB session of
    its own. Works across tenant schemas by running in the default
    tenant context (the dispatcher does not need tenant-scoped RLS —
    it only fans out IDs).
    """
    from core.database import async_session_factory

    cutoff = now or datetime.now(UTC)
    async with async_session_factory() as session:
        result = await session.execute(
            select(RPASchedule.tenant_id, RPASchedule.id).where(
                RPASchedule.enabled.is_(True),
                RPASchedule.next_run_at.is_not(None),
                RPASchedule.next_run_at <= cutoff,
            )
        )
        rows = result.all()
    return [(str(tid), str(sid)) for tid, sid in rows]
