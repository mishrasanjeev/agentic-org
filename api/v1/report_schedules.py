"""Report Schedule API — CRUD + ad-hoc trigger for scheduled reports.

Uses an in-memory store for now; will be migrated to the database model
once the ORM schema is finalised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_current_tenant

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory store (shared with Celery task layer)
# ---------------------------------------------------------------------------
# Import the canonical store from the task layer so both sides see the same data.
from core.tasks.report_tasks import get_schedule_store  # noqa: E402

_store = get_schedule_store()


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

def _compute_next_run(cron: str) -> str:
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
    return (now + delta).isoformat()


def _to_response(record: dict[str, Any]) -> ReportScheduleResponse:
    """Convert an internal store record to the API response model."""
    channels = record.get("delivery_channels", [])
    parsed_channels = [
        DeliveryChannel(**ch) if isinstance(ch, dict) else ch
        for ch in channels
    ]
    return ReportScheduleResponse(
        id=record["id"],
        report_type=record["report_type"],
        cron_expression=record["cron_expression"],
        delivery_channels=parsed_channels,
        format=record.get("format", "pdf"),
        is_active=record.get("is_active", True),
        company_id=record.get("company_id", "default"),
        params=record.get("params", {}),
        tenant_id=record["tenant_id"],
        last_run_at=record.get("last_run_at"),
        next_run_at=record.get("next_run_at"),
        created_at=record["created_at"],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/report-schedules", response_model=list[ReportScheduleResponse])
def list_report_schedules(
    tenant_id: str = Depends(get_current_tenant),
) -> list[ReportScheduleResponse]:
    """List all report schedules for the current tenant."""
    return [
        _to_response(rec)
        for rec in _store.values()
        if rec.get("tenant_id") == tenant_id
    ]


@router.post(
    "/report-schedules",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report_schedule(
    body: ReportScheduleCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> ReportScheduleResponse:
    """Create a new report schedule."""
    schedule_id = str(uuid.uuid4())
    now_iso = datetime.now(UTC).isoformat()

    record: dict[str, Any] = {
        "id": schedule_id,
        "report_type": body.report_type,
        "cron_expression": body.cron_expression,
        "delivery_channels": [ch.model_dump() for ch in body.delivery_channels],
        "format": body.format,
        "is_active": body.is_active,
        "company_id": body.company_id,
        "params": body.params,
        "tenant_id": tenant_id,
        "last_run_at": None,
        "next_run_at": _compute_next_run(body.cron_expression) if body.is_active else None,
        "created_at": now_iso,
    }

    _store[schedule_id] = record
    return _to_response(record)


@router.patch("/report-schedules/{schedule_id}", response_model=ReportScheduleResponse)
def update_report_schedule(
    schedule_id: str,
    body: ReportScheduleUpdate,
    tenant_id: str = Depends(get_current_tenant),
) -> ReportScheduleResponse:
    """Update an existing report schedule (partial update)."""
    record = _store.get(schedule_id)
    if record is None or record.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Schedule not found")

    updates = body.model_dump(exclude_unset=True)

    # Serialise delivery channels if provided.
    if "delivery_channels" in updates and updates["delivery_channels"] is not None:
        updates["delivery_channels"] = [
            ch.model_dump() if hasattr(ch, "model_dump") else ch
            for ch in updates["delivery_channels"]
        ]

    record.update(updates)

    # Recompute next_run if cron or active status changed.
    if "cron_expression" in updates or "is_active" in updates:
        if record.get("is_active"):
            record["next_run_at"] = _compute_next_run(record["cron_expression"])
        else:
            record["next_run_at"] = None

    return _to_response(record)


@router.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_report_schedule(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    """Delete a report schedule."""
    record = _store.get(schedule_id)
    if record is None or record.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    del _store[schedule_id]


@router.post("/report-schedules/{schedule_id}/run-now")
def run_report_now(
    schedule_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Trigger immediate generation for a schedule (ignores cron timing)."""
    record = _store.get(schedule_id)
    if record is None or record.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Schedule not found")

    from core.tasks.report_tasks import generate_report

    report_config: dict[str, Any] = {
        "report_type": record["report_type"],
        "params": record.get("params", {}),
        "company_id": record.get("company_id", "default"),
        "tenant_id": tenant_id,
        "delivery_channels": record.get("delivery_channels", []),
        "format": record.get("format", "pdf"),
        "schedule_id": schedule_id,
    }

    task = generate_report.delay(report_config)

    return {
        "message": "Report generation triggered",
        "task_id": task.id,
        "schedule_id": schedule_id,
    }
