"""Celery tasks for the scheduled report engine.

Tasks
-----
- ``generate_scheduled_reports``  — periodic poller: finds due schedules and
  fans out ``generate_report`` for each.
- ``generate_report``            — main pipeline: generate -> render -> deliver.
- ``deliver_report``             — multi-channel delivery (email / Slack / WhatsApp).
- ``cleanup_old_reports``        — housekeeping: delete reports older than *n* days.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from core.tasks.celery_app import app

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPORTS_DIR = Path(os.getenv("AGENTICORG_REPORTS_DIR", "/tmp/agenticorg_reports"))  # noqa: S108  # nosec B108
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_run_after(cron: str, now: datetime | None = None) -> datetime:
    """Compute the next run time from the cron expression (simplified).

    Handles the interval keywords the API accepts and real cron strings via
    ``croniter`` when installed; unrecognised expressions fall back to daily.
    """
    now = now or datetime.now(UTC)
    interval_map: dict[str, timedelta] = {
        "every_5_minutes": timedelta(minutes=5),
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }
    delta = interval_map.get(cron or "daily")
    if delta is not None:
        return now + delta
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        return croniter(cron, now).get_next(datetime)
    # enterprise-gate: broad-except-ok reason=optional-cron-parser-fallbacks-to-daily
    except Exception:
        return now + timedelta(days=1)


def _advance_next_run(schedule: dict[str, Any]) -> None:
    """Dict-shaped compatibility wrapper around ``_next_run_after``."""
    schedule["next_run_at"] = _next_run_after(schedule.get("cron_expression", "daily")).isoformat()


def _is_demo_or_fallback(content_data: dict[str, Any]) -> bool:
    """True when the generator could not produce measured data."""
    if not isinstance(content_data, dict):
        return True
    if content_data.get("demo") is True:
        return True
    return str(content_data.get("source") or "") == "report_generator_fallback"


def _schedule_report_config(row: Any) -> dict[str, Any]:
    """Build the ``generate_report`` payload from a ``ReportSchedule`` row.

    Mirrors ``api/v1/report_schedules.py`` manual-run so both entry points
    produce the same report for the same schedule.
    """
    config = row.config or {}
    company_id = row.company_id or config.get("company_id")
    return {
        "report_type": row.report_type,
        "params": config.get("params", {}),
        "company_id": str(company_id) if company_id else "default",
        "tenant_id": str(row.tenant_id),
        "delivery_channels": list(row.recipients or []),
        "format": row.format or "pdf",
        "schedule_id": str(row.id),
    }


async def _claim_due_schedules() -> list[dict[str, Any]]:
    """Claim due ``report_schedules`` rows and advance ``next_run_at``.

    ``FOR UPDATE SKIP LOCKED`` makes concurrent beat/worker sweeps safe: a row
    is claimed by exactly one sweeper, and ``last_run_at``/``next_run_at`` are
    advanced in the same transaction as the claim, so a crash between claim
    and enqueue re-runs at most that one schedule on the next sweep rather
    than double-firing it.
    """
    from sqlalchemy import select

    from core.database import async_session_factory
    from core.models.report_schedule import ReportSchedule

    now = datetime.now(UTC)
    claimed: list[dict[str, Any]] = []
    async with async_session_factory() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(ReportSchedule)
                    .where(
                        ReportSchedule.enabled.is_(True),
                        (ReportSchedule.next_run_at.is_(None)) | (ReportSchedule.next_run_at <= now),
                    )
                    .order_by(ReportSchedule.next_run_at.asc().nullsfirst())
                    .limit(500)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for row in rows:
                row.last_run_at = now
                row.next_run_at = _next_run_after(row.cron_expression, now)
                claimed.append(_schedule_report_config(row))
    return claimed


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.task(name="core.tasks.report_tasks.generate_scheduled_reports", bind=True, max_retries=2)
def generate_scheduled_reports(self: Any) -> dict[str, Any]:
    """Poll ``report_schedules`` (the table the API writes) and fan out
    ``generate_report`` for every due, enabled schedule.

    Pre-fix this iterated a module-level dict the API never populated, so
    beat reported ``checked: 0`` forever and no scheduled report was ever
    generated.
    """
    from core.tasks.async_runner import run_async

    fired: list[str] = []
    errors: list[str] = []

    try:
        due = run_async(_claim_due_schedules())
    # enterprise-gate: broad-except-ok reason=report-schedule-poller-returns-structured-error-for-retry
    except Exception as exc:
        log.error("report_schedule_claim_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60) from exc

    for report_config in due:
        schedule_id = report_config["schedule_id"]
        try:
            generate_report.delay(report_config)
            fired.append(schedule_id)
            log.info(
                "report_schedule_fired",
                schedule_id=schedule_id,
                report_type=report_config["report_type"],
            )
        # enterprise-gate: broad-except-ok reason=report-schedule-poller-isolates-per-schedule-failures
        except Exception as exc:
            errors.append(f"{schedule_id}: {exc!s}")
            log.error("report_schedule_error", schedule_id=schedule_id, error=str(exc))

    return {"fired": fired, "errors": errors, "checked": len(due)}


@app.task(name="core.tasks.report_tasks.generate_report", bind=True, max_retries=3)
def generate_report(self: Any, report_config: dict[str, Any]) -> dict[str, Any]:
    """Main report pipeline: generate -> render -> deliver.

    Parameters
    ----------
    report_config : dict
        Keys: report_type, params, company_id, tenant_id, delivery_channels,
        format, schedule_id (optional).
    """
    report_id = str(uuid.uuid4())
    report_type = report_config["report_type"]
    company_id = report_config.get("company_id", "default")
    tenant_id = report_config.get("tenant_id", "default")
    fmt = report_config.get("format", "pdf")
    channels = report_config.get("delivery_channels", [])

    log.info(
        "report_generation_start",
        report_id=report_id,
        report_type=report_type,
        company_id=company_id,
        tenant_id=tenant_id,
    )

    start_ts = time.monotonic()

    try:
        # 1. Generate report data + HTML
        from core.reports.generator import ReportGenerator

        generator = ReportGenerator()
        output = generator.generate(
            report_type=report_type,
            params=report_config.get("params", {}),
            company_id=company_id,
            tenant_id=tenant_id,
        )
        gate = output.content_data.get("report_quality_gate")
        if channels and gate is not None:
            from core.marketing.report_quality import cmo_report_trusted_delivery_allowed

            if not cmo_report_trusted_delivery_allowed(gate):
                log.warning(
                    "report_delivery_blocked_by_quality_gate",
                    report_id=report_id,
                    report_type=report_type,
                    safe_report_mode=gate.get("safe_report_mode"),
                    status=gate.get("status"),
                    next_action_cta=gate.get("next_action_cta"),
                )
                return {
                    "report_id": report_id,
                    "report_type": report_type,
                    "paths": [],
                    "elapsed_sec": round(time.monotonic() - start_ts, 2),
                    "status": "blocked",
                    "safe_report_mode": gate.get("safe_report_mode", "draft_only"),
                    "next_action_cta": gate.get("next_action_cta", "review_report_quality"),
                    "blocked_reasons": gate.get("blocked_reasons", []),
                }

        # 1b. Never deliver demo / fallback content to customers. The
        # generator marks data it could not measure with ``demo: True`` or
        # ``source: report_generator_fallback`` (KPI compute failed, no
        # tenant scope). Pre-fix that all-zero report was rendered and
        # emailed as if it were real.
        if channels and _is_demo_or_fallback(output.content_data):
            reason = "report_content_is_demo_or_fallback"
            log.error(
                "report_delivery_blocked_demo_content",
                report_id=report_id,
                report_type=report_type,
                tenant_id=tenant_id,
                source=output.content_data.get("source"),
            )
            return {
                "report_id": report_id,
                "report_type": report_type,
                "paths": [],
                "elapsed_sec": round(time.monotonic() - start_ts, 2),
                "status": "failed",
                "reason": reason,
                "source": output.content_data.get("source"),
            }

        # 2. Render to requested format(s)
        from core.reports.renderer import render_excel, render_pdf

        report_dir = _REPORTS_DIR / tenant_id / report_id
        report_dir.mkdir(parents=True, exist_ok=True)

        rendered_paths: list[str] = []

        if fmt in ("pdf", "both"):
            pdf_path = str(report_dir / f"{report_type}_{report_id}.pdf")
            render_pdf(output, pdf_path)
            rendered_paths.append(pdf_path)

        if fmt in ("excel", "both"):
            xlsx_path = str(report_dir / f"{report_type}_{report_id}.xlsx")
            render_excel(output, xlsx_path)
            rendered_paths.append(xlsx_path)

        elapsed = round(time.monotonic() - start_ts, 2)

        # 3. Deliver via each configured channel
        for path in rendered_paths:
            for channel_cfg in channels:
                deliver_report.delay(
                    report_path=path,
                    channel=channel_cfg.get("type", "email"),
                    recipient=channel_cfg.get("target", ""),
                    subject=f"AgenticOrg Report: {report_type} ({company_id})",
                )

        log.info(
            "report_generation_complete",
            report_id=report_id,
            report_type=report_type,
            paths=rendered_paths,
            elapsed_sec=elapsed,
        )

        pilot_proof_summary: dict[str, Any] | None = None
        if report_type in {"cmo_weekly", "weekly_marketing_report"}:
            from core.marketing.weekly_report_pilot_persistence import (
                persist_weekly_report_pilot_proof_from_report_output_sync,
            )

            pilot_proof_summary = persist_weekly_report_pilot_proof_from_report_output_sync(
                tenant_id=tenant_id,
                company_id=company_id,
                report_id=report_id,
                report_data=output.content_data,
                rendered_paths=rendered_paths,
            )
            if pilot_proof_summary is not None:
                log.info(
                    "weekly_report_pilot_proof_persisted",
                    report_id=report_id,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    proof_status=pilot_proof_summary.get("proof_status"),
                    production_claim_allowed=pilot_proof_summary.get(
                        "production_claim_allowed"
                    ),
                    readiness_score=pilot_proof_summary.get("readiness_score"),
                )

        result: dict[str, Any] = {
            "report_id": report_id,
            "report_type": report_type,
            "paths": rendered_paths,
            "elapsed_sec": elapsed,
            "status": "completed",
        }
        if pilot_proof_summary is not None:
            result["weekly_report_pilot_proof"] = pilot_proof_summary
        return result

    # enterprise-gate: broad-except-ok reason=report-generation-task-retries-failed-pipeline
    except Exception as exc:
        log.error(
            "report_generation_failed",
            report_id=report_id,
            report_type=report_type,
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=30) from exc


@app.task(name="core.tasks.report_tasks.deliver_report", bind=True, max_retries=3)
def deliver_report(
    self: Any,
    report_path: str,
    channel: str,
    recipient: str,
    subject: str = "",
) -> dict[str, Any]:
    """Deliver a rendered report file via the specified channel.

    Parameters
    ----------
    report_path : str
        Filesystem path to the rendered PDF / Excel file.
    channel : str
        One of ``email``, ``slack``, ``whatsapp``.
    recipient : str
        Email address, Slack channel ID, or phone number depending on *channel*.
    subject : str
        Email subject line (used for email channel).
    """
    log.info(
        "report_delivery_start",
        report_path=report_path,
        channel=channel,
        recipient=recipient,
    )

    try:
        from core.reports.delivery import deliver

        delivery_cfg: list[dict[str, Any]] = [
            {
                "type": channel,
                "target": recipient,
                "subject": subject,
                "message": subject,
                "caption": subject,
            },
        ]

        # delivery.deliver is async; reuse the worker process event loop so
        # pooled async clients stay bound to one loop across tasks.
        from core.tasks.async_runner import run_async

        run_async(deliver(report_path, delivery_cfg))

        log.info(
            "report_delivery_complete",
            report_path=report_path,
            channel=channel,
            recipient=recipient,
        )
        return {"status": "delivered", "channel": channel, "recipient": recipient}

    # enterprise-gate: broad-except-ok reason=report-delivery-task-retries-failed-delivery
    except Exception as exc:
        log.error(
            "report_delivery_failed",
            report_path=report_path,
            channel=channel,
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=60) from exc


@app.task(name="core.tasks.report_tasks.cleanup_old_reports", bind=True)
def cleanup_old_reports(self: Any, days: int = 30) -> dict[str, Any]:
    """Delete rendered report files older than *days* days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    deleted = 0
    errors: list[str] = []

    log.info("report_cleanup_start", cutoff=cutoff.isoformat(), base_dir=str(_REPORTS_DIR))

    for path in _REPORTS_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                path.unlink()
                deleted += 1
        # enterprise-gate: broad-except-ok reason=report-cleanup-isolates-per-file-errors
        except Exception as exc:
            errors.append(f"{path}: {exc!s}")

    # Remove empty directories left behind.
    for dirpath in sorted(_REPORTS_DIR.rglob("*"), reverse=True):
        if dirpath.is_dir():
            try:
                dirpath.rmdir()  # only succeeds if empty
            except OSError:
                pass

    log.info("report_cleanup_complete", deleted=deleted, errors_count=len(errors))
    return {"deleted": deleted, "errors": errors}
