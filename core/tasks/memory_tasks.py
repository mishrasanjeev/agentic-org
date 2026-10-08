# SPDX-License-Identifier: Apache-2.0
"""Celery tasks for long-term memory retention (``core/memory/long_term.py``)."""

from __future__ import annotations

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.tasks.memory_tasks.prune_expired_memories")
def prune_expired_memories() -> dict:
    """Delete every tenant's expired memory entries.

    Scheduled by Celery Beat nightly — see celery_app.beat_schedule. Off
    (``AGENTICORG_RUNTIME_MEMORY_ENABLED``), nothing is read or deleted.
    """
    from core.memory import long_term

    if not long_term.enabled():
        return {"tenants": 0, "pruned": 0, "failed": 0, "skipped": "disabled"}
    return run_async(long_term.prune_all_tenants())
