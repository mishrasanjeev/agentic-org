"""Report Schedule API — CRUD + ad-hoc trigger for scheduled reports.

Uses PostgreSQL via SQLAlchemy ORM with tenant-scoped sessions (RLS).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.report_schedule import ReportSchedule

router = APIRouter(dependencies=[require_tenant_admin])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DeliveryChannel(BaseModel):
    type: str = Field(..., description="Channel type: email | slack | whatsapp")
    target: str = Field(..., description="Email address, Slack channel ID, or phone number")


class ReportScheduleCreate(BaseModel):
    report_type: str = Field(
        ...,
        description="One of: cfo_daily, cmo_weekly, aging_report, pnl_report, campaign_report",
    )
    cron_expression: str = Field(
        "daily",
        description="Cron or keyword: every_5_minutes, hourly, daily, weekly, monthly",
    )
    delivery_channels: list[DeliveryChannel] = Field(default_factory=list)
    format: str = Field("pdf", description="Output format: pdf | excel | both")
    is_active: bool = True
    company_id: str = "default"
    params: dict[str, Any] = Field(default_factory=dict)


class ReportScheduleUpdate(BaseModel):
    report_type: str | None = None
    cron_expression: str | None = None
    delivery_channels: list[DeliveryChannel] | None = None
    format: str | None = None
    is_active: bool | None = None
    company_id: str | None = None
    params: dict[str, Any] | None = None


class ReportScheduleResponse(BaseModel):
    id: str
    report_type: str
    cron_expression: str
    delivery_channels: list[DeliveryChannel]
    format: str
    is_active: bool
    company_id: str
    params: dict[str, Any]
    tenant_id: str
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_next_run(cron: str) -> datetime:
    """Lightweight next-run estimation (matches task-layer logic)."""
    now = datetime.now(UTC)
    interval_map: dict[str, timedelta] = {
        "every_5_minutes": timedelta(minutes=5),
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }
    delta = interval_map.get(cron, timedelta(days=1))
    return now + delta


def _to_response(row: ReportSchedule) -> ReportScheduleResponse:
    """Convert an ORM row to the API response model."""
    config: dict[str, Any] = row.config or {}
    channels_raw: list[Any] = row.recipients or []
    parsed_channels = [
        DeliveryChannel(**ch) if isinstance(ch, dict) else ch
        for ch in channels_raw
    ]
    return ReportScheduleResponse(
        id=str(row.id),
        report_type=row.report_type,
        cron_expression=row.cron_expression,
        delivery_channels=parsed_channels,
        format=row.format or "pdf",
        is_active=row.enabled,
        company_id=config.get("company_id", "default"),
        params=config.get("params", {}),
        tenant_id=str(row.tenant_id),
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        next_run_at=row.next_run_at.isoformat() if row.next_run_at else None,
        created_at=row.created_at.isoformat() if row.created_at else datetime.now(UTC).isoformat(),
    )


def _parse_schedule_id(schedule_id: str) -> _uuid.UUID:
    """Parse schedule ids while preserving 404 semantics for bad ids."""
    try:
        return _uuid.UUID(schedule_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/report-schedules", response_model=list[ReportScheduleResponse])
async def list_report_schedules(
    tenant_id: str = Depends(get_current_tenant),
) -> list[ReportScheduleResponse]:
    """List all report schedules for the current tenant."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(ReportSchedule).where(ReportSchedule.tenant_id == tid)
        )
        rows = result.scalars().all()
        return [_to_response(row) for row in rows]


@router.post(
    "/report-schedules",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_schedule(
    body: ReportScheduleCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> ReportScheduleResponse:
    """Create a new report schedule."""
    tid = _uuid.UUID(tenant_id)
    schedule_id = _uuid.uuid4()
    channels_data = [ch.model_dump() for ch in body.delivery_channels]
    primary_channel = body.delivery_channels[0].type if body.delivery_channels else "email"

    config: dict[str, Any] = {
        "company_id": body.company_id,
        "params": body.params,
    }

    row = ReportSchedule(
        id=schedule_id,
        name=body.report_type,
        report_type=body.report_type,
        cron_expression=body.cron_expression,
        recipients=channels_data,
        delivery_channel=primary_channel,
        format=body.format,
        enabled=body.is_active,
        last_run_at=None,
        next_run_at=_compute_next_run(body.cron_expression) if body.is_active else None,
        config=config,
        tenant_id=tid,
    )

    async with get_tenant_session(tid) as session:
        session.add(row)
        await session.flush()
        return _to_response(row)


@router.patch("/report-schedules/{schedule_id}", response_model=ReportScheduleResponse)
async def update_report_schedule(
    schedule_id: str,
    body: ReportScheduleUpdate,
    tenant_id: str = Depends(get_current_tenant),
) -> ReportScheduleResponse:
    """Update an existing report schedule (partial update)."""
    tid = _uuid.UUID(tenant_id)
    sid = _parse_schedule_id(schedule_id)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(ReportSchedule).where(
                ReportSchedule.id == sid,
                ReportSchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found")

        updates = body.model_dump(exclude_unset=True)

        if "report_type" in updates:
            row.report_type = updates["report_type"]
            row.name = updates["report_type"]

        if "cron_expression" in updates:
            row.cron_expression = updates["cron_expression"]

        if "delivery_channels" in updates and updates["delivery_channels"] is not None:
            channels_data = [
                ch.model_dump() if hasattr(ch, "model_dump") else ch
                for ch in updates["delivery_channels"]
            ]
            row.recipients = channels_data
            if channels_data:
                first = channels_data[0]
                row.delivery_channel = first.get("type", "email") if isinstance(first, dict) else "email"

        if "format" in updates:
            row.format = updates["format"]

        if "is_active" in updates:
            row.enabled = updates["is_active"]

        # Update config fields (company_id, params)
        config: dict[str, Any] = dict(row.config or {})
        if "company_id" in updates:
            config["company_id"] = updates["company_id"]
        if "params" in updates:
            config["params"] = updates["params"]
        row.config = config

        # Recompute next_run if cron or active status changed.
        if "cron_expression" in updates or "is_active" in updates:
            if row.enabled:
                row.next_run_at = _compute_next_run(row.cron_expression)
            else:
                row.next_run_at = None

        await session.flush()
        return _to_response(row)


@router.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_report_schedule(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    """Delete a report schedule."""
    tid = _uuid.UUID(tenant_id)
    sid = _parse_schedule_id(schedule_id)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(ReportSchedule).where(
                ReportSchedule.id == sid,
                ReportSchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        await session.delete(row)


@router.post("/report-schedules/{schedule_id}/run-now")
async def run_report_now(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Trigger immediate generation for a schedule (ignores cron timing)."""
    tid = _uuid.UUID(tenant_id)
    sid = _parse_schedule_id(schedule_id)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(ReportSchedule).where(
                ReportSchedule.id == sid,
                ReportSchedule.tenant_id == tid,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found")

        config: dict[str, Any] = row.config or {}

        from core.tasks.report_tasks import generate_report

        report_config: dict[str, Any] = {
            "report_type": row.report_type,
            "params": config.get("params", {}),
            "company_id": config.get("company_id", "default"),
            "tenant_id": tenant_id,
            "delivery_channels": row.recipients or [],
            "format": row.format or "pdf",
            "schedule_id": schedule_id,
        }

        task = generate_report.delay(report_config)

        return {
            "message": "Report generation triggered",
            "task_id": task.id,
            "schedule_id": schedule_id,
        }
