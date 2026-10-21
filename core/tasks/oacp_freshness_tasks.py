# SPDX-License-Identifier: Apache-2.0
"""The optional OACP artifact freshness sweep (core/commerce/artifact_freshness.py).

Every 5 minutes on the existing beat and worker: a no-op unless both
AGENTICORG_OACP_ON_DEMAND_REFRESH_ENABLED and AGENTICORG_OACP_REFRESH_SWEEP_ENABLED
are true; then only merchant scopes read in the last 15 minutes are refreshed.
"""

from __future__ import annotations

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _sweep_async() -> dict:
    from core.commerce.artifact_freshness import sweep

    result = await sweep()
    if result.get("enabled"):
        logger.info("oacp_freshness_sweep", scopes=result["scopes"], refreshed=result["refreshed"])
    return result


@app.task(name="core.tasks.oacp_freshness_tasks.sweep_artifact_freshness")
def sweep_artifact_freshness() -> dict:
    """Refresh recently read merchant scopes whose slower artifacts expire before the next sweep (behind flags)."""
    return run_async(_sweep_async())
