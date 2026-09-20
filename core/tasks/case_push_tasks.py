# SPDX-License-Identifier: Apache-2.0
"""Celery entry points that deliver the governed case push outbox (``core.cases.push``).

``dispatch_case_pushes(tenant_id)`` is queued right after a case change commits, so a completed
memo normally reaches the receiver within seconds. The periodic sweep (every 30 s) covers a
missed kick, retries and lease expiry; it runs only when ``AGENTICORG_CASE_PUSH_SWEEP_ENABLED`` is
true, so deployments that do not use case push do no extra work.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _dispatch(tenant_id: str | None) -> dict[str, Any]:
    from core.cases.push import CasePushDispatcher

    dispatcher = CasePushDispatcher()
    if tenant_id:
        report = await dispatcher.dispatch_tenant(uuid.UUID(tenant_id))
    else:
        report = await dispatcher.dispatch_all()
    return {"delivered": report.delivered, "retried": report.retried, "dead_lettered": report.dead_lettered}


@app.task(name="core.tasks.case_push_tasks.dispatch_case_pushes")
def dispatch_case_pushes(tenant_id: str | None = None) -> dict[str, Any]:
    return run_async(_dispatch(tenant_id))


@app.task(name="core.tasks.case_push_tasks.sweep_case_pushes")
def sweep_case_pushes() -> dict[str, Any]:
    from core.config import settings

    if not settings.case_push_sweep_enabled:
        return {"skipped": "case_push_sweep_disabled"}
    return run_async(_dispatch(None))
