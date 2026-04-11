"""Celery tasks for the budget alert evaluator."""

from __future__ import annotations

import asyncio

import structlog

from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.tasks.budget_tasks.run_budget_evaluator")
def run_budget_evaluator() -> dict:
    """Run the budget alert evaluator end-to-end.

    Scheduled by Celery Beat every 5 minutes — see celery_app.beat_schedule.
    """
    from core.billing.budget_evaluator import evaluate_budget_alerts

    return asyncio.run(evaluate_budget_alerts())
