"""Celery tasks for ``core.cron`` jobs, registered on the real worker app.

``core.cron.celery_beat`` used to define a second ``Celery("agenticorg")``
instance with its own beat schedule; no deployment entrypoint ever ran it
(``scripts/run_beat.py`` / ``run_worker.py`` load ``core.tasks.celery_app``),
so the compliance cron only fired when Cloud Scheduler hit
``POST /api/v1/cron/compliance-alerts``. The task now lives on the shared
app and its beat entry is in ``core.tasks.celery_app.beat_schedule``.
"""

from __future__ import annotations

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.cron.tasks.run_compliance_alerts")
def run_compliance_alerts() -> dict:
    """Run the daily compliance deadline alert job (6:00 AM IST via beat)."""
    from core.cron.compliance_alerts import run_compliance_alert_cron

    return run_async(run_compliance_alert_cron())
