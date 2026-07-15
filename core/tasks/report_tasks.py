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
# In-memory schedule store (replaced by DB in production)
# ---------------------------------------------------------------------------
# Populated at import-time by the API layer — see api/v1/report_schedules.py.
# Each entry: {id, report_type, cron_expression, delivery_channels, recipients,
#              format, is_active, tenant_id, company_id, last_run_at, next_run_at, ...}
_schedule_store: dict[str, dict[str, Any]] = {}


def get_schedule_store() -> dict[str, dict[str, Any]]:
    """Return the global in-memory schedule store (shared with API layer)."""
    return _schedule_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_schedule_due(schedule: dict[str, Any]) -> bool:
    """Return True when a schedule's *next_run_at* is in the past or now."""
    if not schedule.get("is_active", True):
        return False
    next_run = schedule.get("next_run_at")
    if next_run is None:
        return True  # never run -> run immediately
    if isinstance(next_run, str):
        next_run = datetime.fromisoformat(next_run)
    now = datetime.now(UTC)
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=UTC)
    return now >= next_run


def _advance_next_run(schedule: dict[str, Any]) -> None:
    """Compute the next run time from the cron expression (simplified).

    Full cron parsing would use ``croniter``; we ship a lightweight
    approximation that handles daily / weekly / monthly keywords and
    simple cron-like strings so the system works out-of-the-box without
    an extra dependency.
    """
    cron = schedule.get("cron_expression", "daily")
    now = datetime.now(UTC)

    interval_map: dict[str, timedelta] = {
        "every_5_minutes": timedelta(minutes=5),
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }

    delta = interval_map.get(cron)
    if delta is not None:
        schedule["next_run_at"] = (now + delta).isoformat()
        return

    # Attempt croniter if available (optional dependency).
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        base = now
        cron_iter = croniter(cron, base)
        schedule["next_run_at"] = cron_iter.get_next(datetime).isoformat()
    # enterprise-gate: broad-except-ok reason=optional-cron-parser-fallbacks-to-daily
    except Exception:
        # Fall back to daily if cron expression is unrecognised.
        schedule["next_run_at"] = (now + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.task(name="core.tasks.report_tasks.generate_scheduled_reports", bind=True, max_retries=2)
def generate_scheduled_reports(self: Any) -> dict[str, Any]:
    """Poll all report schedules and fan out generation for those that are due."""
    fired: list[str] = []
    errors: list[str] = []

    for schedule_id, schedule in _schedule_store.items():
        try:
            if not _is_schedule_due(schedule):
                continue

            report_config: dict[str, Any] = {
                "report_type": schedule["report_type"],
                "params": schedule.get("params", {}),
                "company_id": schedule.get("company_id", "default"),
                "tenant_id": schedule.get("tenant_id", "default"),
                "delivery_channels": schedule.get("delivery_channels", []),
                "format": schedule.get("format", "pdf"),
                "schedule_id": schedule_id,
            }

            generate_report.delay(report_config)

            schedule["last_run_at"] = datetime.now(UTC).isoformat()
            _advance_next_run(schedule)
            fired.append(schedule_id)

            log.info(
                "report_schedule_fired",
                schedule_id=schedule_id,
                report_type=schedule["report_type"],
            )
        # enterprise-gate: broad-except-ok reason=report-schedule-poller-isolates-per-schedule-failures
        except Exception as exc:
            errors.append(f"{schedule_id}: {exc!s}")
            log.error("report_schedule_error", schedule_id=schedule_id, error=str(exc))

    return {"fired": fired, "errors": errors, "checked": len(_schedule_store)}


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
