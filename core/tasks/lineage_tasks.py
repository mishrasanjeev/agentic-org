# SPDX-License-Identifier: Apache-2.0
"""The periodic sweep of sync sources (``core/lineage/sync.py``): every tenant's due sources, run in turn.

A no-op unless ``AGENTICORG_LINEAGE_ENABLED`` and
``AGENTICORG_LINEAGE_SYNC_SWEEP_ENABLED`` are both on. Beat fires it
every five minutes (``core.tasks.celery_app.beat_schedule``).
"""

from __future__ import annotations

from typing import Any

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app


@app.task(name="core.tasks.lineage_tasks.sweep_sync_sources")
def sweep_sync_sources() -> dict[str, Any]:
    from core.lineage import sync

    if not sync.sweep_enabled():
        return {"skipped": "lineage_sync_sweep_disabled"}
    return run_async(sync.sweep())
