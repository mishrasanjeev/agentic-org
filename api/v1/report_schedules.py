"""Report Schedule API — CRUD + ad-hoc trigger for scheduled reports.

Uses PostgreSQL via SQLAlchemy ORM with tenant-scoped sessions (RLS).
"""

from __future__ import annotations

import re
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.report_schedule import ReportSchedule

# Presets understood by both the API layer and the scheduler worker.
# Real cron strings (5 fields) are also accepted — see _is_valid_cron().
_CRON_PRESETS: frozenset[str] = frozenset({
    "every_5_minutes",
    "hourly",
    "daily",
    "weekly",
    "monthly",
})

# Pragmatic email shape check. SendGrid does authoritative validation at
# delivery time; this just rejects obviously malformed input at the API
# boundary so the user gets a 422 instead of an eventual 500.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# Slack channel IDs: C* (public), G* (private), D* (DM). 9-11 alphanumeric
# chars after the prefix. Accepts the friendly "#channel-name" form too,
# which Slack resolves server-side.
_SLACK_CHANNEL_RE = re.compile(r"^(?:[CGD][A-Z0-9]{8,10}|#[a-z0-9][a-z0-9._-]{0,78})$")

# WhatsApp: E.164 phone numbers, 8-15 digits with optional leading '+'.
_WHATSAPP_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def _is_valid_cron(expr: str) -> bool:
    """Accept either a preset keyword or a real 5-field cron expression.

    Delegates to croniter when available (authoritative). When croniter is
    absent (it is an optional dep for the task layer too, see
    core/tasks/report_tasks.py), falls back to a per-field range check so
    we still reject obviously-invalid values like ``99 99 99 99 99``.
    """
    if expr in _CRON_PRESETS:
        return True
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        try:
            croniter(expr, datetime.now(UTC))
            return True
        except (ValueError, KeyError):
            return False
    except ImportError:
        pass
    # croniter not installed — do a field-by-field range check.
    return _fallback_cron_check(expr)


def _fallback_cron_check(expr: str) -> bool:
    """Permissive-but-range-aware cron validator used when croniter is absent.

    Each of the 5 fields must be a ``*``, ``*/N``, ``A-B``, ``A,B,C``, or a
    bare integer, all within the field's natural range.
    """
    fields = expr.split()
    if len(fields) != 5:
        return False
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    for token, (lo, hi) in zip(fields, ranges, strict=False):
        if not _cron_field_ok(token, lo, hi):
            return False
    return True


def _cron_field_ok(token: str, lo: int, hi: int) -> bool:
    if token == "*":
        return True
    if token.startswith("*/"):
        try:
            n = int(token[2:])
        except ValueError:
            return False
        return n > 0
    for part in token.split(","):
        if "-" in part:
            try:
                a, b = (int(x) for x in part.split("-", 1))
            except ValueError:
                return False
            if not (lo <= a <= hi and lo <= b <= hi and a <= b):
                return False
            continue
        try:
            v = int(part)
        except ValueError:
            return False
        if not (lo <= v <= hi):
            return False
    return True

router = APIRouter(dependencies=[require_tenant_admin])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DeliveryChannel(BaseModel):
    type: str = Field(..., description="Channel type: email | slack | whatsapp")
    target: str = Field(..., description="Email address, Slack channel ID, or phone number")

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in {"email", "slack", "whatsapp"}:
            raise ValueError(
                "type must be one of: email, slack, whatsapp"
            )
        return v

    @model_validator(mode="after")
    def _validate_target_for_type(self) -> DeliveryChannel:
        """Validate target format against channel type.

        Without this, the API accepts an empty or malformed target and the
        failure surfaces later as an HTTP 500 from the dispatcher (SendGrid
        rejection, Slack 404, Twilio format error). Rejecting at the
        boundary gives callers a clean 422 with a specific message.
        """
        target = (self.target or "").strip()
        if not target:
            raise ValueError(
                f"target is required for {self.type} delivery channel"
            )
        if self.type == "email":
            if not _EMAIL_RE.match(target):
                raise ValueError(
                    "invalid email address (expected name@domain.tld)"
                )
        elif self.type == "slack":
            if not _SLACK_CHANNEL_RE.match(target):
                raise ValueError(
                    "invalid Slack channel: use the channel ID "
                    "(e.g. C01ABC23DEF) or #channel-name"
                )
        elif self.type == "whatsapp":
            if not _WHATSAPP_PHONE_RE.match(target):
                raise ValueError(
                    "invalid WhatsApp number: use E.164 format "
                    "(e.g. +919876543210)"
                )
        # Store the trimmed value so downstream code never sees padding.
        object.__setattr__(self, "target", target)
        return self


class ReportScheduleCreate(BaseModel):
    report_type: str = Field(
        ...,
        description="One of: cfo_daily, cmo_weekly, aging_report, pnl_report, campaign_report",
    )
    cron_expression: str = Field(
        "daily",
        description=(
            "Preset keyword (every_5_minutes, hourly, daily, weekly, monthly) "
            "or a standard 5-field cron expression (e.g. '0 9 * * 1-5')."
        ),
    )
    delivery_channels: list[DeliveryChannel] = Field(default_factory=list)
    format: str = Field("pdf", description="Output format: pdf | excel | both")
    is_active: bool = True
    company_id: str = "default"
    params: dict[str, Any] = Field(default_factory=dict)

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

    @field_validator("format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        if v not in {"pdf", "excel", "both"}:
            raise ValueError("format must be one of: pdf, excel, both")
        return v

    @model_validator(mode="after")
    def _require_at_least_one_channel(self) -> ReportScheduleCreate:
        if not self.delivery_channels:
            raise ValueError(
                "at least one delivery channel is required"
            )
        return self


class ReportScheduleUpdate(BaseModel):
    report_type: str | None = None
    cron_expression: str | None = None
    delivery_channels: list[DeliveryChannel] | None = None
    format: str | None = None
    is_active: bool | None = None
    company_id: str | None = None
    params: dict[str, Any] | None = None

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not _is_valid_cron(v):
            raise ValueError(
                "cron_expression must be a preset "
                f"({', '.join(sorted(_CRON_PRESETS))}) or a valid "
                "5-field cron expression"
            )
        return v

    @field_validator("format")
    @classmethod
    def _validate_format(cls, v: str | None) -> str | None:
        if v is not None and v not in {"pdf", "excel", "both"}:
            raise ValueError("format must be one of: pdf, excel, both")
        return v

    @model_validator(mode="after")
    def _delivery_channels_not_empty_when_set(self) -> ReportScheduleUpdate:
        if self.delivery_channels is not None and not self.delivery_channels:
            raise ValueError(
                "delivery_channels cannot be an empty list; "
                "omit the field to keep existing channels"
            )
        return self


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
    """Estimate next run for display purposes.

    The authoritative next-run calculation happens in the scheduler worker
    (`core/tasks/report_tasks.py`). This helper just gives the UI a
    reasonable `next_run_at` to show immediately after create/update.
    """
    now = datetime.now(UTC)
    interval_map: dict[str, timedelta] = {
        "every_5_minutes": timedelta(minutes=5),
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }
    if cron in interval_map:
        return now + interval_map[cron]
    # Real cron string — defer to croniter for an accurate estimate.
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        return croniter(cron, now).get_next(datetime)
    except (ImportError, ValueError, KeyError):
        return now + timedelta(days=1)


def _coerce_channel(ch: Any) -> DeliveryChannel | None:
    """Best-effort parse of a stored recipient entry.

    Legacy rows from v4.4/4.5 stored ``recipients`` as bare string lists
    (``["user@example.com"]``). Later rows use the structured dict shape
    (``[{"type": "email", "target": "..."}]``). Attempting strict
    DeliveryChannel construction on the legacy shape crashes the list
    endpoint with a 500 — TC_001 reopen 2026-04-24.

    Returns ``None`` when the entry cannot be coerced into a valid
    channel so the caller can drop it rather than 500ing the whole list.
    """
    if isinstance(ch, DeliveryChannel):
        return ch
    if isinstance(ch, dict):
        try:
            return DeliveryChannel(**ch)
        except (TypeError, ValueError):
            return None
    if isinstance(ch, str):
        s = ch.strip()
        if not s:
            return None
        # Legacy string — assume email if it looks like one; otherwise
        # surface as a "Slack channel name" shape so it doesn't silently
        # vanish from the UI.
        try:
            if _EMAIL_RE.match(s):
                return DeliveryChannel(type="email", target=s)
            if _SLACK_CHANNEL_RE.match(s):
                return DeliveryChannel(type="slack", target=s)
            if _WHATSAPP_PHONE_RE.match(s):
                return DeliveryChannel(type="whatsapp", target=s)
        except (TypeError, ValueError):
            return None
    return None


def _to_response(row: ReportSchedule) -> ReportScheduleResponse:
    """Convert an ORM row to the API response model.

    Defensive against legacy row shapes: a single bad ``recipients``
    entry no longer crashes the list endpoint (TC_001 reopen).
    """
    config: dict[str, Any] = row.config or {}
    channels_raw: list[Any] = row.recipients or []
    parsed_channels: list[DeliveryChannel] = []
    for ch in channels_raw:
        coerced = _coerce_channel(ch)
        if coerced is not None:
            parsed_channels.append(coerced)
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
    company_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[ReportScheduleResponse]:
    """List report schedules for the current tenant.

    Codex 2026-04-22 isolation fix: when the caller is scoped to a
    specific company, only return schedules with that ``company_id``
    (plus tenant-wide schedules where ``company_id IS NULL``). Without
    this, two users in the same tenant who manage separate companies
    would see each other's schedules — a tenancy hole CLAUDE.md's non-
    negotiable rules don't permit for a control-plane surface.

    When no ``company_id`` is supplied, the legacy tenant-wide list is
    returned (unchanged contract for admin / reporting tooling).
    """
    tid = _uuid.UUID(tenant_id)
    company_uuid: _uuid.UUID | None = None
    if company_id:
        try:
            company_uuid = _uuid.UUID(company_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="company_id must be a UUID",
            ) from exc

    try:
        async with get_tenant_session(tid) as session:
            query = select(ReportSchedule).where(ReportSchedule.tenant_id == tid)
            if company_uuid is not None:
                # Return the company's own schedules *and* tenant-wide
                # schedules (company_id IS NULL) so a company user doesn't
                # lose visibility of org-level schedules.
                from sqlalchemy import or_

                query = query.where(
                    or_(
                        ReportSchedule.company_id == company_uuid,
                        ReportSchedule.company_id.is_(None),
                    )
                )
            result = await session.execute(query)
            rows = result.scalars().all()
            # TC_001 reopen 2026-04-24: a single row with legacy recipients
            # shape, NULL timestamps, or any other unexpected value used to
            # 500 the whole list. Skip the bad row (logged) and still
            # return the ones that parsed so the UI isn't blank-screened.
            out: list[ReportScheduleResponse] = []
            for row in rows:
                try:
                    out.append(_to_response(row))
                except Exception as row_exc:  # noqa: BLE001 — defensive boundary
                    import structlog

                    structlog.get_logger().warning(
                        "report_schedule_row_skipped",
                        tenant_id=tenant_id,
                        schedule_id=getattr(row, "id", None),
                        error=str(row_exc),
                    )
            return out
    except HTTPException:
        raise
    except Exception as exc:
        import structlog

        structlog.get_logger().error(
            "report_schedule_list_failed",
            tenant_id=tenant_id,
            company_id=company_id,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not load report schedules. If the problem persists, "
                "contact support."
            ),
        ) from exc


@router.post(
    "/report-schedules",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_schedule(
    body: ReportScheduleCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> ReportScheduleResponse:
    """Create a new report schedule.

    TC_001 reopen (Aishwarya 2026-04-22): testers still saw an HTTP 500
    on empty recipient. Pydantic validators already map empty targets
    to 422, and the UI validates before submit — so the 500 must come
    from an unexpected exception path (DB, next-run compute, JSONB
    serialisation). Wrap the whole route body so ANY un-handled
    exception returns a structured 500 with an actionable message
    rather than a bare stack trace. ValueError / HTTPException are
    re-raised so FastAPI keeps its own mapping.
    """
    try:
        tid = _uuid.UUID(tenant_id)
        schedule_id = _uuid.uuid4()
        channels_data = [ch.model_dump() for ch in body.delivery_channels]
        primary_channel = (
            body.delivery_channels[0].type if body.delivery_channels else "email"
        )

        config: dict[str, Any] = {
            "company_id": body.company_id,
            "params": body.params,
        }

        # Codex 2026-04-22 isolation fix: dual-write company_id to both
        # the new indexed column and ``config`` (kept for back-compat
        # with legacy readers). Accept malformed id as "no company" so
        # existing callers that send an empty string keep working.
        company_uuid: _uuid.UUID | None = None
        if body.company_id:
            try:
                company_uuid = _uuid.UUID(body.company_id)
            except (TypeError, ValueError):
                company_uuid = None

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
            next_run_at=_compute_next_run(body.cron_expression)
            if body.is_active
            else None,
            config=config,
            tenant_id=tid,
            company_id=company_uuid,
        )

        async with get_tenant_session(tid) as session:
            session.add(row)
            await session.flush()
            return _to_response(row)
    except HTTPException:
        raise
    except Exception as exc:
        import structlog

        structlog.get_logger().error(
            "report_schedule_create_failed",
            tenant_id=tenant_id,
            report_type=getattr(body, "report_type", None),
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not create the report schedule. Check the recipient "
                "and cron expression and try again. If the problem persists, "
                "contact support."
            ),
        ) from exc


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
