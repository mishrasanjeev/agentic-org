# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Scheduled Insights reports: a view PDF, the finance pack or the period evidence pack, on a report schedule.

A schedule of type ``insights_view``, ``insights_finance_pack`` or
``insights_evidence_pack`` runs as the user who made it (an administrator of
the tenant for schedules made before owners were recorded), with that user's
access read from their record at run time: a schedule never reads more than
its owner may. The period is a preset counted in the tenant's time zone
(``last_7_days``, ``last_30_days``, ``last_week``, ``last_month``,
``month_to_date``). The file is made as an Insights export job, so it is kept
seven days on the Downloads page; the schedule's email recipients get a link
(and may download it when they sign in to the organisation), and Slack and
WhatsApp channels get the file. A view whose every figure has no data is not
delivered: the run is ``blocked`` with the reason. Every run is recorded in
``report_schedule_runs``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger()

REPORT_TYPES: dict[str, str] = {
    "insights_view": "view_pdf",
    "insights_finance_pack": "finance_pack",
    "insights_evidence_pack": "evidence_pack",
}
FORMATS: dict[str, str] = {"insights_view": "pdf", "insights_finance_pack": "zip", "insights_evidence_pack": "zip"}
PERIODS = ("last_7_days", "last_30_days", "last_week", "last_month", "month_to_date")
DEFAULT_PERIOD = "last_7_days"


class ScheduleParamsError(ValueError):
    """Schedule params an Insights report type cannot run with."""


def period_range(preset: str, today: date) -> tuple[date, date]:
    """The days a preset covers, ending before ``today`` except month-to-date (which includes it)."""
    yesterday = today - timedelta(days=1)
    if preset == "last_7_days":
        return today - timedelta(days=7), yesterday
    if preset == "last_30_days":
        return today - timedelta(days=30), yesterday
    if preset == "last_week":
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if preset == "last_month":
        first = today.replace(day=1)
        last = first - timedelta(days=1)
        return last.replace(day=1), last
    if preset == "month_to_date":
        return today.replace(day=1), today
    raise ScheduleParamsError(f"period must be one of {', '.join(PERIODS)}")


def validate_params(report_type: str, params: dict[str, Any], role: str, *, is_admin: bool) -> dict[str, Any]:
    """The params an Insights schedule keeps; refuses a view or pack the role may not read."""
    from core.insights import packs, registry

    period = str(params.get("period") or DEFAULT_PERIOD)
    if period not in PERIODS:
        raise ScheduleParamsError(f"period must be one of {', '.join(PERIODS)}")
    effective = "admin" if is_admin else (role or "").lower()
    if report_type == "insights_view":
        view = str(params.get("view") or "")
        if view not in registry.VIEWS:
            raise ScheduleParamsError(f"no Insights view {view!r}")
        if view not in registry.allowed_views(role, is_admin=is_admin):
            raise PermissionError("insights_view_forbidden")
        return {"view": view, "period": period}
    if report_type == "insights_finance_pack":
        if not (is_admin or effective in packs.FINANCE_ROLES):
            raise PermissionError("insights_pack_forbidden")
        return {"period": period}
    if report_type == "insights_evidence_pack":
        if not (is_admin or effective in packs.EVIDENCE_ROLES):
            raise PermissionError("insights_pack_forbidden")
        return {"period": period}
    raise ScheduleParamsError(f"not an Insights report type: {report_type}")


async def _owner(session: Any, tenant_id: uuid.UUID, created_by: uuid.UUID | None) -> Any | None:
    from sqlalchemy import select

    from core.models.user import User

    if created_by is not None:
        user = (
            await session.execute(
                select(User).where(User.tenant_id == tenant_id, User.id == created_by, User.status == "active")
            )
        ).scalar_one_or_none()
        if user is not None:
            return user
        return None
    return (
        await session.execute(
            select(User)
            .where(User.tenant_id == tenant_id, User.role == "admin", User.status == "active")
            .order_by(User.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _caller_for(user: Any) -> Any:
    from core.ownership import Caller
    from core.rbac import get_allowed_domains

    role = str(user.role or "")
    return Caller(
        user_id=user.id,
        role=role,
        domains=get_allowed_domains(role, getattr(user, "domain", None)),
        is_admin=role == "admin",
        is_machine=False,
    )


async def _record(tenant_id: uuid.UUID, schedule_id: uuid.UUID, **fields: Any) -> None:
    from core.database import get_tenant_session
    from core.models.report_schedule import ReportScheduleRun

    async with get_tenant_session(tenant_id) as session:
        session.add(
            ReportScheduleRun(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                finished_at=datetime.now(UTC),
                **fields,
            )
        )


async def record_run(tenant_id: str, schedule_id: str | None, result: dict[str, Any], trigger: str) -> None:
    """Record a legacy report run from ``generate_report``'s result (best effort; the run itself already ended)."""
    if not schedule_id:
        return
    status = str(result.get("status") or "")
    status = status if status in ("failed", "blocked") else "generated"
    try:
        await _record(
            uuid.UUID(tenant_id),
            uuid.UUID(schedule_id),
            trigger=trigger,
            status=status,
            reason=str(result.get("reason") or result.get("error") or "")[:500] or None,
        )
    # enterprise-gate: broad-except-ok reason=run-history-is-best-effort-after-the-run-and-its-failure-is-logged
    except Exception as exc:
        logger.warning("report_schedule_run_record_failed", error_type=type(exc).__name__)


async def _view_has_data(session: Any, tenant_id: uuid.UUID, caller: Any, view: str, start: date, end: date) -> bool:
    from api.v1.insights import _context, view_domains
    from core.insights import registry

    ctx = await _context(session, tenant_id, caller, start=start, end=end, compare="none", company=None)
    ctx.domains = view_domains(view, caller.domains)
    keys = registry.VIEWS[view].keys
    # A snapshot ("now", such as agents by status) says nothing about the period; it counts only for a view made
    # of snapshots alone.
    period_keys = [key for key in keys if registry.METRICS[key].kind != "snapshot"] or list(keys)
    for key in period_keys:
        if (await registry.resolve(registry.METRICS[key], ctx)).get("data_quality") != "no_data":
            return True
    return False


async def _deliver(
    job: Any, channels: list[dict[str, Any]], content: bytes, schedule_name: str
) -> list[dict[str, Any]]:
    from core.config import settings
    from core.email import send_email

    base = str(getattr(settings, "app_base_url", "") or "https://app.agenticorg.ai").rstrip("/")
    link = f"{base}/dashboard/insights/downloads"
    subject = f"Insights report: {schedule_name}"
    results: list[dict[str, Any]] = []
    file_channels = []
    for channel in channels:
        kind, target = str(channel.get("type") or ""), str(channel.get("target") or "")
        if kind == "email" and target:
            from html import escape

            name = escape(job.filename or schedule_name)
            html = (
                f"<p>Your scheduled Insights report <strong>{name}</strong> is ready.</p>"
                f'<p><a href="{escape(link)}">Open Downloads</a> '
                "(sign in to your organisation; the file is kept for seven days).</p>"
                f"<p>SHA-256 {escape(job.sha256 or '')}</p>"
            )
            sent = await asyncio.to_thread(send_email, target, subject, html)
            results.append({"type": "email", "target": target, "status": "sent" if sent else "failed"})
        elif kind in ("slack", "whatsapp") and target:
            file_channels.append({"type": kind, "target": target, "subject": subject, "message": subject})
    if file_channels:
        from core.reports.delivery import deliver

        folder = tempfile.mkdtemp(prefix="insights-report-")
        path = os.path.join(folder, job.filename or f"insights-{job.id}.{job.format}")
        try:
            with open(path, "wb") as handle:
                handle.write(content)
            for result in await deliver(path, file_channels):
                results.append({k: v for k, v in result.items() if k in ("channel", "status", "reason")})
        finally:
            try:
                os.remove(path)
                os.rmdir(folder)
            except OSError:
                logger.debug("insights_report_temp_cleanup_failed")
    return results


async def run(report_config: dict[str, Any], *, trigger: str = "schedule") -> dict[str, Any]:
    """Run one Insights report schedule; returns the run's status and records it."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.insights import export_jobs, storage
    from core.insights.flags import insights_enabled_for
    from core.insights.metrics import local_today
    from core.insights.reporting_settings import load_reporting_settings
    from core.models.insights import InsightsExport
    from core.models.report_schedule import ReportSchedule

    tenant_id = uuid.UUID(str(report_config["tenant_id"]))
    schedule_id = uuid.UUID(str(report_config["schedule_id"]))
    report_type = str(report_config["report_type"])
    started = datetime.now(UTC)

    async def finish(status: str, reason: str | None = None, **extra: Any) -> dict[str, Any]:
        await _record(
            tenant_id, schedule_id, trigger=trigger, status=status, reason=reason, started_at=started, **extra
        )
        logger.info("insights_report_run", schedule_id=str(schedule_id), status=status, reason=reason)
        return {"status": status, "reason": reason, "schedule_id": str(schedule_id), **extra}

    if not await insights_enabled_for(tenant_id):
        return await finish("blocked", "insights_disabled: Insights is off for this organisation")
    if not storage.configured():
        return await finish("failed", "export_storage_unconfigured: background files are not set up")
    async with get_tenant_session(tenant_id) as session:
        schedule = (
            await session.execute(
                select(ReportSchedule).where(ReportSchedule.tenant_id == tenant_id, ReportSchedule.id == schedule_id)
            )
        ).scalar_one_or_none()
        if schedule is None:
            return {"status": "failed", "reason": "schedule_not_found"}
        owner = await _owner(session, tenant_id, schedule.created_by)
        if owner is None:
            return await finish("failed", "no_owner: the user who made this schedule is not active")
        caller = _caller_for(owner)
        params = dict((schedule.config or {}).get("params") or {})
        try:
            kept = validate_params(report_type, params, caller.role, is_admin=caller.is_admin)
        except PermissionError as exc:
            return await finish("blocked", f"{exc}: the schedule's owner may no longer read this")
        except ScheduleParamsError as exc:
            return await finish("failed", f"invalid_params: {exc}")
        reporting = await load_reporting_settings(session, tenant_id)
        start, end = period_range(kept["period"], local_today(reporting.zone()))
        if report_type == "insights_view" and not await _view_has_data(
            session, tenant_id, caller, kept["view"], start, end
        ):
            return await finish("blocked", "no_data: every figure of the view has no data for the period")
        try:
            job = await export_jobs.create(
                session,
                tenant_id,
                caller,
                kind=REPORT_TYPES[report_type],
                fmt=FORMATS[report_type],
                params={
                    **({"view": kept["view"]} if "view" in kept else {}),
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "compare": "previous",
                },
                include_pii=False,
                now=started,
            )
        except export_jobs.ExportJobError as exc:
            return await finish("failed", f"{exc.error}: {exc.message}")
        # Recipients who sign in to the organisation may download the file from Downloads.
        emails = sorted(
            {
                str(c.get("target")).lower()
                for c in (schedule.recipients or [])
                if isinstance(c, dict) and c.get("type") == "email"
            }
        )
        job.params = {**job.params, "shared_with": emails, "schedule_id": str(schedule_id)}
        job_id = job.id
        channels = [c for c in (schedule.recipients or []) if isinstance(c, dict)]
        name = f"{schedule.name} ({kept['period'].replace('_', ' ')})"
    status = await export_jobs.build(tenant_id, job_id)
    async with get_tenant_session(tenant_id) as session:
        job = await session.get(InsightsExport, job_id)
    if status != "ready" or job is None:
        return await finish("failed", (job.error if job is not None else None) or f"export_{status}", export_id=job_id)
    content = await asyncio.to_thread(storage.get, job.storage_key)
    results = await _deliver(job, channels, content, name)
    delivered = any(r.get("status") in ("sent", "delivered", "success", "ok") for r in results)
    return await finish(
        "delivered" if delivered else "failed",
        None if delivered else "delivery_failed: no channel accepted the report",
        export_id=job_id,
        channels=results,
    )
