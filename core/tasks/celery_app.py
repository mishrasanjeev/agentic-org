"""Celery application — AgenticOrg scheduled report engine.

Broker and result backend default to Redis.  Override with the
``AGENTICORG_REDIS_URL`` environment variable (same key the rest of
the platform uses via ``core.config.Settings``).
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

_redis_url: str = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")

app = Celery(
    "agenticorg",
    broker=_redis_url,
    backend=_redis_url,
)

# ── Celery configuration ────────────────────────────────────────────
app.conf.update(
    # Serialisation
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone (IST for India-market customers)
    timezone="Asia/Kolkata",
    enable_utc=True,
    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # Result expiry (24 h)
    result_expires=86_400,
    # Task routing
    task_routes={
        "core.tasks.report_tasks.generate_scheduled_reports": {"queue": "reports"},
        "core.tasks.report_tasks.generate_report": {"queue": "reports"},
        "core.tasks.report_tasks.deliver_report": {"queue": "delivery"},
        "core.tasks.report_tasks.cleanup_old_reports": {"queue": "maintenance"},
        "core.tasks.workflow_tasks.*": {"queue": "workflows"},
        # RPA scheduler (feat/rpa-framework-rbi): runs go to a
        # dedicated queue so long scrapes don't starve the short
        # report jobs.
        "core.tasks.rpa_tasks.*": {"queue": "rpa"},
    },
)

# ── Beat schedule — periodic tasks ──────────────────────────────────
app.conf.beat_schedule = {
    "generate-scheduled-reports": {
        "task": "core.tasks.report_tasks.generate_scheduled_reports",
        "schedule": 300.0,  # every 5 minutes
        "options": {"queue": "reports"},
    },
    "cleanup-old-reports": {
        "task": "core.tasks.report_tasks.cleanup_old_reports",
        "schedule": crontab(hour=2, minute=0),  # daily at 2:00 AM IST
        "options": {"queue": "maintenance"},
    },
    "run-budget-evaluator": {
        "task": "core.tasks.budget_tasks.run_budget_evaluator",
        "schedule": 300.0,  # every 5 minutes
        "options": {"queue": "maintenance"},
    },
    "refresh-expiring-tokens": {
        "task": "core.tasks.token_refresh.refresh_expiring_tokens",
        "schedule": 900.0,  # every 15 minutes
        "options": {"queue": "maintenance"},
    },
    "generate-monthly-invoices": {
        "task": "core.tasks.invoice_tasks.generate_monthly_invoices",
        "schedule": crontab(hour=1, minute=0, day_of_month="1"),
        "options": {"queue": "maintenance"},
    },
    "dispatch-due-rpa-schedules": {
        # Poll every 5 minutes for RPA schedules whose next_run_at has
        # passed. Each dispatch enqueues a ``run_rpa_schedule`` per
        # due row; the worker handles backoff + last_run_* updates.
        "task": "core.tasks.rpa_tasks.dispatch_due_rpa_schedules",
        "schedule": 300.0,
        "options": {"queue": "rpa"},
    },
    "shadow-reconciliation-report": {
        "task": "core.tasks.report_tasks.generate_report",
        "schedule": crontab(hour=8, minute=0, day_of_week="monday"),  # weekly Mon 8 AM IST
        "args": [
            {
                "report_type": "shadow_reconciliation",
                "params": {},
                "company_id": "__all__",
                "tenant_id": "__system__",
                "delivery_channels": [],
                "format": "pdf",
            },
        ],
        "options": {"queue": "reports"},
    },
}

# ── Auto-discover tasks from the core.tasks package ─────────────────
app.autodiscover_tasks(["core.tasks"])


# ── Foundation #7 PR-E: hermetic-CI seam ────────────────────────────
# Eager mode + invocation capture are NOT activated at module
# import time — that latches the config on the singleton ``app``
# even after the env var is later cleared, which silently turns
# integration tests into eager runs. Activation is delegated to
# ``core.test_doubles.fake_celery.activate(app)``, which the test
# conftest calls explicitly. Tests (or the integration-tests CI
# job) can call ``fake_celery.deactivate(app)`` to opt back to
# real broker dispatch without leaving eager-mode latched.
