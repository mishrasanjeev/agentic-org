# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Celery entry points that deliver UCP order webhooks (``core.commerce.ucp.webhooks``).

``dispatch_order_webhooks(tenant_id)`` is kicked right after an order change
commits. The periodic sweep (every 30 s) covers a missed kick, retries and
lease expiry, and prunes expired ``Idempotency-Key`` records; it runs only
while ``AGENTICORG_UCP_CHECKOUT_ENABLED`` or ``AGENTICORG_ACP_CHECKOUT_ENABLED``
is true (ACP sessions share the order entity, the outbox and the records).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _dispatch(tenant_id: str | None) -> dict[str, Any]:
    from core.commerce.ucp.webhooks import OrderWebhookDispatcher

    dispatcher = OrderWebhookDispatcher()
    if tenant_id:
        report = await dispatcher.dispatch_tenant(uuid.UUID(tenant_id))
    else:
        report = await dispatcher.dispatch_all()
    return {"delivered": report.delivered, "retried": report.retried, "dead_lettered": report.dead_lettered}


async def _sweep() -> dict[str, Any]:
    from core.commerce.ucp.idempotency import prune_expired

    result = await _dispatch(None)
    result["idempotency_pruned"] = await prune_expired()
    return result


@app.task(name="core.tasks.ucp_order_webhook_tasks.dispatch_order_webhooks")
def dispatch_order_webhooks(tenant_id: str | None = None) -> dict[str, Any]:
    return run_async(_dispatch(tenant_id))


@app.task(name="core.tasks.ucp_order_webhook_tasks.sweep_order_webhooks")
def sweep_order_webhooks() -> dict[str, Any]:
    from core.config import settings

    if not (settings.ucp_checkout_enabled or settings.acp_checkout_enabled):
        return {"skipped": "checkout_disabled"}
    return run_async(_sweep())
