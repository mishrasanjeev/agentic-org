"""Celery tasks for invoice generation."""

from __future__ import annotations

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.tasks.invoice_tasks.generate_monthly_invoices")
def generate_monthly_invoices() -> dict:
    """Run the monthly invoice generator.

    Scheduled for the 1st of each month at 01:00 IST — see celery_app.
    """
    from core.billing.invoice_generator import generate_invoices_for_period

    return run_async(generate_invoices_for_period())
