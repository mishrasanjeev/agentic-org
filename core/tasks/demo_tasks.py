# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Durable marketing worker entries; queue payloads contain identifiers only."""
from uuid import UUID

from core.demo_delivery import dispatch_one
from core.demo_delivery import sweep as sweep_pending
from core.tasks.async_runner import run_async
from core.tasks.celery_app import app


@app.task(name="core.tasks.demo_tasks.dispatch", acks_late=True)
def dispatch(tenant_id: str, job_id: str) -> dict[str, str]:
    return run_async(dispatch_one(UUID(tenant_id), UUID(job_id)))


@app.task(name="core.tasks.demo_tasks.sweep")
def sweep() -> int:
    return run_async(sweep_pending())
