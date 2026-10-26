# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Celery entry point for merchant onboarding desk outreach (``core.onboarding_desk.outreach``).

The beat sweep runs every minute and does nothing while
``AGENTICORG_ONBOARDING_DESK_ENABLED`` is off. When it is on, it contacts the
due leads of each tenant whose branding turns on automatic contact, inside the
contact rules: consent, permitted hours, and the attempt and reminder limits.
"""

from __future__ import annotations

from typing import Any

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app


@app.task(name="core.tasks.onboarding_desk_tasks.sweep_outreach")
def sweep_outreach() -> dict[str, Any]:
    from core.config import settings

    if not settings.onboarding_desk_enabled:
        return {"skipped": "onboarding_desk_disabled"}
    from core.onboarding_desk import outreach

    return run_async(outreach.sweep())
