# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Celery entry points for buyer payment hand-offs (``core.commerce.payment_handoff``).

``poll_plural_handoffs`` (beat, every 60 s) is the fallback to Plural's
webhook: it reads the order status of every open Plural hosted-checkout
hand-off once and applies it, and releases a reservation whose order creation
never finished. It runs only while ``AGENTICORG_UCP_CHECKOUT_ENABLED`` or
``AGENTICORG_ACP_CHECKOUT_ENABLED`` is true. ``reconcile_plural_refunds`` (beat, every
5 minutes, maintenance queue, same switch) reads from Plural the hosted refunds whose
outcome is still unknown (``core.commerce.hosted_refunds.reconcile_all``).

``notify_order`` and ``notify_unmatched_payment`` send one email directly. New
code no longer queues them: merchant emails go through the durable outbox
(``core.tasks.merchant_notification_tasks``). They stay registered so messages
queued before the outbox existed are still sent during a rolling deploy.
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


@app.task(name="core.tasks.payment_handoff_tasks.reconcile_plural_refunds")
def reconcile_plural_refunds() -> dict[str, Any]:
    """Read from Plural the hosted refunds whose outcome is still unknown, and record what Plural says."""
    from core.commerce.hosted_refunds import reconcile_all
    from core.config import settings

    if not (settings.ucp_checkout_enabled or settings.acp_checkout_enabled):
        return {"skipped": "checkout_disabled"}
    return run_async(reconcile_all())


@app.task(name="core.tasks.payment_handoff_tasks.notify_order")
def notify_order(tenant_id: str, order_id: str) -> dict[str, Any]:
    from core.commerce.order_notifications import notify_merchant_of_order

    return {"sent": run_async(notify_merchant_of_order(tenant_id, order_id))}


@app.task(name="core.tasks.payment_handoff_tasks.notify_unmatched_payment")
def notify_unmatched_payment(tenant_id: str, checkout_session_id: str, details: dict[str, Any]) -> dict[str, Any]:
    from core.commerce.order_notifications import notify_merchant_of_unmatched_payment

    sent = notify_merchant_of_unmatched_payment(
        tenant_id,
        checkout_session_id,
        result=str(details.get("result") or ""),
        amount_minor_units=details.get("amount_minor_units"),
        currency=details.get("currency"),
        provider_order_id=str(details.get("provider_order_id") or ""),
    )
    return {"sent": run_async(sent)}
