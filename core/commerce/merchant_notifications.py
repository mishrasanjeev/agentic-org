# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The durable outbox of merchant commerce emails (``commerce_merchant_notifications``).

A new order and a Plural payment that needs the merchant's attention (no order
matches it, or the order was made while stock was short) each get one row,
written in the transaction of the business change (``enqueue``). The email is
sent from the row, never from the request:

* ``deliver_tenant`` leases one due row at a time (``pending``, or ``failed``
  whose ``next_attempt_at`` has come) ``FOR UPDATE SKIP LOCKED``, moves its
  ``next_attempt_at`` past a lease that covers one send, and commits before
  sending, like the order webhook dispatcher (``core.commerce.ucp.webhooks``).
  It then sends that email outside any transaction and records the outcome: ``sent`` when at least one
  administrator accepted it, otherwise ``failed`` with exponential backoff, and
  ``dead`` after ``MAX_ATTEMPTS``. An SMTP failure for every recipient (or no
  active administrator) is a failed attempt, so the row is tried again.
* The kick after commit (``order_notifications.kick_*``) asks a worker to run
  ``deliver_tenant`` at once; the beat sweep (``sweep``, every minute on the
  maintenance queue) sends whatever a lost kick, a crash or a failure left.

One row per kind and payload reference (unique), so a repeated payment
confirmation never queues a second email. Rows hold references and amounts,
never an email address.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

logger = structlog.get_logger()

KIND_ORDER = "order"
KIND_PAYMENT_ATTENTION = "payment_attention"
KINDS = (KIND_ORDER, KIND_PAYMENT_ATTENTION)
MAX_ATTEMPTS = 8
BATCH_SIZE = 25
LEASE = timedelta(minutes=5)
BASE_DELAY_SECONDS = 60.0
MAX_DELAY_SECONDS = 6 * 3600.0

Sender = Callable[[str, str, str, Mapping[str, Any]], Awaitable[int]]


def _tenant(tenant_id: Any) -> uuid.UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def retry_delay(attempt: int, row_id: uuid.UUID) -> timedelta:
    """Backoff after the failed attempt ``attempt`` (1-based): 1, 2, 4 ... minutes up to 6 hours, +/-20% jitter."""
    base = min(BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1)), MAX_DELAY_SECONDS)
    draw = int.from_bytes(hashlib.sha256(f"{row_id}:{attempt}".encode()).digest()[:4], "big") / 2**32
    return timedelta(seconds=base * (0.8 + 0.4 * draw))


async def enqueue(
    session: Any,
    *,
    tenant_id: Any,
    merchant_id: str,
    kind: str,
    payload_ref: str,
    details: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Add the email's row to ``session`` (the business change's transaction); False when it is already queued."""
    from sqlalchemy.dialects.postgresql import insert

    from core.commerce.ledger import json_safe
    from core.models.commerce_payments import CommerceMerchantNotificationRow as Row

    if kind not in KINDS:
        raise ValueError(f"unknown merchant notification kind {kind!r}")
    moment = now or datetime.now(UTC)
    inserted = (
        await session.execute(
            insert(Row)
            .values(
                id=uuid.uuid4(),
                tenant_id=_tenant(tenant_id),
                merchant_id=str(merchant_id)[:160],
                kind=kind,
                payload_ref=str(payload_ref)[:160],
                details=json_safe(dict(details or {})),
                status="pending",
                attempts=0,
                next_attempt_at=moment,
                created_at=moment,
                updated_at=moment,
            )
            .on_conflict_do_nothing(constraint="uq_commerce_merchant_notifications_ref")
            .returning(Row.id)
        )
    ).first()
    return inserted is not None


async def send_notification(tenant_id: str, kind: str, payload_ref: str, details: Mapping[str, Any]) -> int:
    """Send one row's email; returns how many administrators accepted it."""
    from core.commerce import order_notifications as notes

    if kind == KIND_ORDER:
        return await notes.notify_merchant_of_order(tenant_id, payload_ref)
    amount = details.get("amount_minor_units")
    return await notes.notify_merchant_of_unmatched_payment(
        tenant_id,
        str(details.get("checkout_session_id") or ""),
        result=str(details.get("result") or ""),
        amount_minor_units=amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
        currency=details.get("currency"),
        provider_order_id=str(details.get("provider_order_id") or ""),
        order_id=details.get("order_id"),
        lines=details.get("lines"),
    )


@dataclass
class DeliveryReport:
    sent: int = 0
    retried: int = 0
    dead: int = 0

    def add(self, other: DeliveryReport) -> None:
        self.sent += other.sent
        self.retried += other.retried
        self.dead += other.dead

    def as_dict(self) -> dict[str, int]:
        return {"sent": self.sent, "retried": self.retried, "dead": self.dead}


async def deliver_tenant(
    tenant_id: Any, *, now: datetime | None = None, sender: Sender | None = None, limit: int = BATCH_SIZE
) -> DeliveryReport:
    """Send the tenant's due merchant emails once each: lease one row, send it outside a transaction, record.

    Each row is leased just before its own send (``LEASE`` covers one send, never a batch), so a slow
    batch cannot outlast a lease and have another worker send the same email again.
    """
    from core.database import get_tenant_session
    from core.models.commerce_payments import CommerceMerchantNotificationRow as Row

    tenant = _tenant(tenant_id)
    send = sender or send_notification
    report = DeliveryReport()
    seen: set[uuid.UUID] = set()
    for _ in range(limit):
        moment = now or datetime.now(UTC)
        async with get_tenant_session(tenant) as session:
            query = (
                select(Row)
                .where(Row.tenant_id == tenant, Row.status.in_(("pending", "failed")), Row.next_attempt_at <= moment)
                .order_by(Row.next_attempt_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if seen:
                query = query.where(Row.id.not_in(seen))
            row = (await session.scalars(query)).first()
            if row is None:
                break
            row.attempts = row.attempts + 1
            row.next_attempt_at = moment + LEASE
            row.updated_at = moment
            item: dict[str, Any] = {
                "id": row.id,
                "kind": row.kind,
                "payload_ref": row.payload_ref,
                "details": dict(row.details or {}),
                "attempts": row.attempts,
            }
        seen.add(item["id"])
        error: str | None = None
        sent = 0
        try:
            sent = await send(str(tenant), item["kind"], item["payload_ref"], item["details"])
        # enterprise-gate: broad-except-ok reason=a-failed-send-is-recorded-on-the-outbox-row-and-retried
        except Exception as exc:
            error = f"send_failed:{type(exc).__name__}"
            logger.warning("merchant_notification_send_failed", kind=item["kind"], error_type=type(exc).__name__)
        if not error and sent < 1:
            error = "no_email_accepted"
        finished = datetime.now(UTC) if now is None else moment
        async with get_tenant_session(tenant) as session:
            stored = await session.get(Row, item["id"])
            if stored is None:
                continue
            stored.updated_at = finished
            if error is None:
                stored.status, stored.sent_at, stored.sent_count, stored.last_error = "sent", finished, sent, None
                report.sent += 1
            elif item["attempts"] >= MAX_ATTEMPTS:
                stored.status, stored.last_error = "dead", f"max_attempts_exceeded:{error}"[:120]
                report.dead += 1
                logger.error("merchant_notification_dead", kind=item["kind"], attempts=item["attempts"])
            else:
                stored.status, stored.last_error = "failed", error[:120]
                stored.next_attempt_at = finished + retry_delay(item["attempts"], item["id"])
                report.retried += 1
    return report


async def sweep(*, now: datetime | None = None, sender: Sender | None = None) -> dict[str, int]:
    """The beat sweep: every tenant's due merchant emails (covers lost kicks, crashes and failed sends)."""
    from core.commerce.ucp.webhooks import _tenants_with_pending

    total = DeliveryReport()
    for tenant in await _tenants_with_pending():
        try:
            total.add(await deliver_tenant(tenant, now=now, sender=sender))
        # enterprise-gate: broad-except-ok reason=one-tenant-failure-never-stops-the-sweep-rows-stay-due
        except Exception as exc:
            logger.warning("merchant_notification_sweep_tenant_failed", error_type=type(exc).__name__)
    return total.as_dict()


def _send_kick(tenant_id: str) -> None:
    try:
        from core.tasks.merchant_notification_tasks import deliver_merchant_notifications

        deliver_merchant_notifications.delay(str(tenant_id))
    # enterprise-gate: broad-except-ok reason=broker-failure-falls-back-to-the-periodic-sweep-durable-outbox
    except Exception as exc:
        logger.warning("merchant_notification_kick_failed", error_type=type(exc).__name__)


def kick(tenant_id: str) -> None:
    """Ask a worker to send the tenant's due emails now (off the event loop); the sweep sends them anyway."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_kick(tenant_id)
        return
    loop.run_in_executor(None, _send_kick, tenant_id)
