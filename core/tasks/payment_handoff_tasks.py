# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Celery entry points for buyer payment hand-offs (``core.commerce.payment_handoff``).

``poll_plural_handoffs`` (beat, every 60 s) is the fallback to Plural's
webhook: it reads the order status of every open Plural hosted-checkout
hand-off once and applies it, and releases a reservation whose order creation
never finished. It runs only while ``AGENTICORG_UCP_CHECKOUT_ENABLED`` or
``AGENTICORG_ACP_CHECKOUT_ENABLED`` is true. ``notify_order`` emails the
merchant's administrators about a new order after its commit.
"""

from __future__ import annotations

from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.tasks.payment_handoff_tasks.poll_plural_handoffs")
def poll_plural_handoffs() -> dict[str, Any]:
    from core.commerce.plural_hosted import poll_open_handoffs
    from core.config import settings

    if not (settings.ucp_checkout_enabled or settings.acp_checkout_enabled):
        return {"skipped": "checkout_disabled"}
    return run_async(poll_open_handoffs())


@app.task(name="core.tasks.payment_handoff_tasks.notify_order")
def notify_order(tenant_id: str, order_id: str) -> dict[str, Any]:
    from core.commerce.order_notifications import notify_merchant_of_order

    return {"sent": run_async(notify_merchant_of_order(tenant_id, order_id))}
