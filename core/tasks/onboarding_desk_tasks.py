# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Celery entry points for the merchant onboarding desk: outreach and the case hand-off.

The beat sweep runs every minute and does nothing while
``AGENTICORG_ONBOARDING_DESK_ENABLED`` is off. When it is on, it contacts the
due leads of each tenant whose branding turns on automatic contact, inside the
contact rules: consent, permitted hours, and the attempt and reminder limits.

``open_case_task`` is queued when an applicant submits and the tenant's case
settings turn on ``auto_open``: it opens the governed case and, with
``investigate`` on, starts the checks. A refusal is returned, not retried; the
reason stays on the application for an operator to see.
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


@app.task(name="core.tasks.onboarding_desk_tasks.open_case")
def open_case_task(tenant_id: str, application_id: str) -> dict[str, Any]:
    import uuid

    from core.config import settings

    if not settings.onboarding_desk_enabled:
        return {"skipped": "onboarding_desk_disabled"}
    from core.onboarding_desk import cases
    from core.onboarding_desk.store import DeskError

    try:
        return run_async(cases.open_and_investigate(uuid.UUID(tenant_id), uuid.UUID(application_id)))
    except DeskError as exc:
        return {"opened": False, "reason": exc.code}
