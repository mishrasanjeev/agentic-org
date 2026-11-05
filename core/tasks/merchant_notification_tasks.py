# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Celery entry points for the merchant commerce email outbox (``core.commerce.merchant_notifications``).

``deliver_merchant_notifications(tenant_id)`` is kicked right after a new order
or a payment that needs the merchant's attention commits. The beat sweep
(``sweep_merchant_notifications``, every 60 s on the maintenance queue) sends
every tenant's due rows: a lost kick, a crash mid-send and failed sends are
retried from there with backoff until the row is sent or dead.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.tasks.merchant_notification_tasks.deliver_merchant_notifications")
def deliver_merchant_notifications(tenant_id: str) -> dict[str, Any]:
    from core.commerce.merchant_notifications import deliver_tenant

    return run_async(deliver_tenant(uuid.UUID(str(tenant_id)))).as_dict()


@app.task(name="core.tasks.merchant_notification_tasks.sweep_merchant_notifications")
def sweep_merchant_notifications() -> dict[str, Any]:
    from core.commerce.merchant_notifications import sweep

    return run_async(sweep())
