"""Celery Beat schedule configuration for periodic tasks.

Usage:
    celery -A core.cron.celery_beat worker --beat --loglevel=info

Or via Cloud Scheduler (GCP):
    POST /api/v1/cron/compliance-alerts
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------

redis_url = os.environ.get("AGENTICORG_REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "agenticorg",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)

# ---------------------------------------------------------------------------
# Beat schedule
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    "compliance-alerts-daily": {
        "task": "core.cron.celery_beat.run_compliance_alerts",
        "schedule": crontab(hour=6, minute=0),  # 6:00 AM IST daily
        "args": (),
    },
}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@celery_app.task(name="core.cron.celery_beat.run_compliance_alerts")
def run_compliance_alerts():
    """Celery task wrapper for the async compliance alert cron."""
    import asyncio

    from core.cron.compliance_alerts import run_compliance_alert_cron

    return asyncio.run(run_compliance_alert_cron())
