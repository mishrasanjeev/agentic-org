# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Refund orchestration on the UCP order entity: a fresh ``refund`` decision, then the executor, then the order.

``refund_order`` never reuses the order's ``session_complete`` decision:

1. In one transaction it reads the order and its payment state
   (``payment_records.payment_state``: captured less every refund requested
   and not failed), asks the commerce arbiter for a ``refund`` decision
   (rule ``refund_within_captured``) on the refund payload, and seals it.
2. After that commit the decision is offered to Grantex for countersignature
   (``arbiter_grantex.countersign_decision``; skipped unless posting is on).
   The decision names the buyer agent of the order's checkout session (the
   authenticated platform recorded at create time), so Grantex can bind its
   grant to it.
3. In a second transaction the ``allow`` is consumed
   (``executors.consume_decision(..., action="refund")``, which runs
   ``require_allow_decision``; with ``AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT``
   on it needs the verified grant).
4. The order's executor refunds through the provider outside any transaction;
   it checks the bounds again before calling (the captured amount, the order
   total and the handler allowance recorded on the order at completion).
5. A succeeded refund is appended to the order as a ``refund`` adjustment
   (``completed``; ``pending`` while the provider outcome is unknown), which
   sends the full order snapshot to every subscriber.

Partial refunds are allowed and bounded by the captured amount; nothing here
waits for a person, and a refusal is final for that request (a new request
gets a new decision).

Orders paid on a Plural hosted payment page (executor ``plural_hosted``) are
refunded with the merchant's own Plural credentials
(``core.commerce.hosted_refunds``): captured is the order total, refunded is
every refund already recorded on the order (completed or pending) or
journalled for it, and no agent payment allowance applies (a merchant
administrator asks for the refund). The arbiter decision, the same-currency
check and the ledger events are the same as for an executed order.

Idempotency: the provider ``merchant_order_reference`` is derived from the
order id and the request id (``executors.plural.refund_reference``). A
repeated ``request_id`` for the same order returns the earlier refund (and
completes its order adjustment if a crash left it unrecorded) without a new
decision or a second provider call. Without a ``request_id`` every request is
a new refund.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from core.commerce.ledger import commerce_payload_hash, json_safe, record_commerce_event

logger = structlog.get_logger()

REFUND_EVENT = "commerce.ucp.order.refund"


class RefundRequestError(ValueError):
    """A refund that cannot be asked for (unknown order 404, no refund-capable executor 409, bad input 422)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class RefundOutcome:
    status: str  # "succeeded", "pending", "refused", "failed"
    reason: str
    decision: dict[str, Any] | None = None
    adjustment_id: str | None = None
    order_revision: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def refund_payload(
    *, order: Any, amount: int, currency: str, captured: int, refunded: int, request_id: str
) -> dict[str, Any]:
    """The binding payload of a ``refund`` decision (JSON-safe; the order, the amount and the state it read)."""
    return json_safe(
        {
            "action": "refund",
            "order_id": order.id,
            "checkout_session_id": order.checkout_session_id,
            "merchant_id": order.merchant_id,
            "seller_agent_id": order.seller_agent_id,
            "amount": amount,
            "currency": currency,
            "captured_minor_units": captured,
            "refunded_minor_units": refunded,
            "request_id": request_id,
        }
    )


async def refund_order(
    *,
    tenant_id: str,
    order_id: str,
    amount_minor_units: int,
    reason: str | None,
    now: datetime,
    request_id: str | None = None,
    grantex_client: Any = None,
) -> RefundOutcome:
    from sqlalchemy import select

    from core.commerce import executors
    from core.commerce import hosted_refunds as hosted
    from core.commerce.arbiter import ArbiterContext, RefundEvidence, decide
    from core.commerce.arbiter_grantex import countersign_decision
    from core.commerce.arbiter_store import DecisionRefusedError, persist_decision
    from core.commerce.executors.plural import PluralExecutor, RefundRequest, refund_reference
    from core.commerce.payment_records import payment_state
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    if isinstance(amount_minor_units, bool) or not isinstance(amount_minor_units, int) or amount_minor_units < 1:
        raise RefundRequestError(422, "amount_invalid")
    tenant = uuid.UUID(str(tenant_id))
    request_key = request_id or uuid.uuid4().hex
    reference = refund_reference(order_id, request_key)
    if request_id:
        earlier = await _earlier_refund(tenant, order_id, reference, now=now)
        if earlier is not None:
            return earlier
    executor: Any = None
    async with get_tenant_session(tenant) as session:
        order = (
            await session.execute(
                select(CommerceOrderRow).where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
            )
        ).scalar_one_or_none()
        if order is None:
            raise RefundRequestError(404, "order_not_found")
        is_hosted = order.executor == hosted.HOSTED_EXECUTOR
        if is_hosted:
            if not order.provider_order_ref:
                raise RefundRequestError(409, "order_not_captured")
            captured = int(order.total_minor_units)
            refunded = await hosted.hosted_refunded(session, tenant=tenant, order=order)
        else:
            executor = executors.get_executor(order.executor)
            if not isinstance(executor, PluralExecutor):
                raise RefundRequestError(409, "executor_unavailable")
            state = await payment_state(session, tenant_id=tenant, checkout_session_id=order.checkout_session_id)
            captured, refunded = state.captured_minor_units, state.refunded_minor_units
        buyer_agent_id = await _order_buyer_agent_id(session, tenant, order)
        currency = str(order.currency).upper()
        payload = refund_payload(
            order=order,
            amount=amount_minor_units,
            currency=currency,
            captured=captured,
            refunded=refunded,
            request_id=request_key,
        )
        decision = decide(
            "refund",
            ArbiterContext(
                tenant_id=str(tenant_id),
                merchant_id=order.merchant_id,
                seller_agent_id=order.seller_agent_id,
                buyer_agent_id=buyer_agent_id,
                now=now,
                payload=payload,
                amount_minor_units=amount_minor_units,
                currency=currency,
                quantity=None,
                refund=RefundEvidence(
                    order_id=order.id,
                    currency=currency,
                    captured_minor_units=captured,
                    refunded_minor_units=refunded,
                ),
            ),
        )
        await persist_decision(session, decision)
        scope = {
            "merchant_id": order.merchant_id,
            "seller_agent_id": order.seller_agent_id,
            "checkout_session_id": order.checkout_session_id,
            "total": int(order.total_minor_units),
            # A hosted order was paid on the merchant's own page and is refunded by a merchant administrator:
            # no agent payment allowance bounds it (the captured total and earlier refunds do).
            "allowance": None if is_hosted else _allowance(order.snapshot),
            "hosted": is_hosted,
        }
        if not decision.allowed:
            await _record(session, tenant_id, scope, order_id, "refused", decision.decision_id, payload, reason)
    public = decision.to_public()
    if not decision.allowed:
        return RefundOutcome(status="refused", reason=str(decision.failing_rule), decision=public)

    await countersign_decision(decision, client=grantex_client)
    try:
        async with get_tenant_session(tenant) as session:
            consumed = await executors.consume_decision(
                session,
                tenant_id=str(tenant_id),
                merchant_id=scope["merchant_id"],
                seller_agent_id=scope["seller_agent_id"],
                decision_id=decision.decision_id,
                payload_hash=decision.payload_hash,
                action=executors.REFUND,
            )
    except DecisionRefusedError as exc:
        async with get_tenant_session(tenant) as session:
            await _record(session, tenant_id, scope, order_id, "refused", decision.decision_id, payload, exc.code)
        return RefundOutcome(status="refused", reason=exc.code, decision=public)

    if scope["hosted"]:
        result: Any = await hosted.refund_hosted_order(
            tenant_id=str(tenant_id),
            order_id=order_id,
            reference=reference,
            decision_id=consumed.decision_id,
            payload_hash=decision.payload_hash,
            amount_minor_units=amount_minor_units,
            currency=currency,
        )
        replayed = result.status == "replayed"
    else:
        result = await executor.refund(
            RefundRequest(
                tenant_id=str(tenant_id),
                merchant_id=scope["merchant_id"],
                seller_agent_id=scope["seller_agent_id"],
                order_id=order_id,
                checkout_session_id=scope["checkout_session_id"],
                payload_hash=decision.payload_hash,
                amount_minor_units=amount_minor_units,
                currency=currency,
                order_total_minor_units=scope["total"],
                allowance_max_minor_units=scope["allowance"],
                reference=reference,
            ),
            consumed,
        )
        replayed = result.status == "refused" and result.reason == "operation_already_requested"
    if replayed:
        # A concurrent request with the same request id got there first: answer with that refund.
        earlier = await _earlier_refund(tenant, order_id, reference, now=now)
        if earlier is not None:
            return earlier
    if result.status not in ("succeeded", "unknown"):
        async with get_tenant_session(tenant) as session:
            await _record(session, tenant_id, scope, order_id, "refused", decision.decision_id, payload, result.reason)
        return RefundOutcome(status="failed" if result.status == "failed" else "refused", reason=result.reason,
                             decision=public)

    from core.commerce.ucp.handlers import apply_order_change

    adjustment = {
        "type": "refund",
        "status": "completed" if result.status == "succeeded" else "pending",
        "totals": [{"type": "total", "amount": -amount_minor_units}],
        "description": (reason or "Refund")[:200],
    }
    entry_id = hosted.adjustment_id_for(reference)
    revision, snapshot, _version = await apply_order_change(
        tenant_id=str(tenant_id),
        order_id=order_id,
        fulfillment_event=None,
        adjustment=adjustment,
        now=now,
        adjustment_id=entry_id,
    )
    entry = next(a for a in snapshot["adjustments"] if a.get("id") == entry_id)
    async with get_tenant_session(tenant) as session:
        await _record(
            session,
            tenant_id,
            scope,
            order_id,
            "success" if result.status == "succeeded" else "blocked",
            decision.decision_id,
            payload,
            result.reason,
            adjustment_id=entry["id"],
        )
    return RefundOutcome(
        status="succeeded" if result.status == "succeeded" else "pending",
        reason=result.reason,
        decision=public,
        adjustment_id=entry["id"],
        order_revision=revision,
    )


async def _earlier_refund(tenant: uuid.UUID, order_id: str, reference: str, *, now: datetime) -> RefundOutcome | None:
    """The refund already requested under ``reference`` for the order, or None when there is none.

    Read from the journal (``commerce_payment_operations``): the provider outcome, the amount and
    the decision. When the provider took the refund but the order adjustment is missing (a crash
    between the two), the adjustment is recorded now, under its deterministic id.
    """
    from sqlalchemy import select

    from core.commerce import hosted_refunds as hosted
    from core.commerce.payment_records import DbPaymentJournal
    from core.commerce.ucp.handlers import apply_order_change
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    entries = [e for e in await DbPaymentJournal().operations(str(tenant), reference) if e.operation == "refund"]
    if not entries:
        return None
    phases = {e.phase for e in entries}
    requested = next((e for e in entries if e.phase == "requested"), entries[0])
    amount = int(requested.amount_minor_units or 0)
    entry_id = hosted.adjustment_id_for(reference)
    async with get_tenant_session(tenant) as session:
        order = (
            await session.execute(
                select(CommerceOrderRow).where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
            )
        ).scalar_one_or_none()
        if order is None:
            return None
        adjustments = list((order.snapshot or {}).get("adjustments") or [])
        revision: int | None = int(order.revision)
        scope = {
            "merchant_id": order.merchant_id,
            "seller_agent_id": order.seller_agent_id,
        }
    entry = next((a for a in adjustments if isinstance(a, dict) and a.get("id") == entry_id), None)
    if "failed" in phases:
        status, why = "failed", "refund_failed"
    elif "succeeded" in phases:
        status, why = "succeeded", "refund_succeeded"
    elif "unknown" in phases:
        status, why = "pending", "refund_unknown"
    else:
        status, why = "pending", "refund_in_progress"
    if entry is None and status != "failed" and phases & {"succeeded", "unknown"}:
        revision, snapshot, _version = await apply_order_change(
            tenant_id=str(tenant),
            order_id=order_id,
            fulfillment_event=None,
            adjustment={
                "type": "refund",
                "status": "completed" if status == "succeeded" else "pending",
                "totals": [{"type": "total", "amount": -amount}],
                "description": "Refund",
            },
            now=now,
            adjustment_id=entry_id,
        )
        entry = next((a for a in snapshot["adjustments"] if a.get("id") == entry_id), None)
    async with get_tenant_session(tenant) as session:
        await _record(
            session,
            str(tenant),
            scope,
            order_id,
            "success" if status == "succeeded" else ("blocked" if status == "pending" else "refused"),
            requested.decision_id,
            {"order_id": order_id, "reference": reference, "replayed": True},
            "refund_already_requested",
            adjustment_id=entry["id"] if entry else None,
        )
    return RefundOutcome(
        status=status,
        reason=why,
        decision={"decision_id": requested.decision_id},
        adjustment_id=entry["id"] if entry else None,
        order_revision=revision,
        extra={"replayed": True, "amount": amount},
    )


async def _order_buyer_agent_id(session: Any, tenant: uuid.UUID, order: Any) -> str | None:
    """The buyer agent of the order's checkout session (recorded at create time), else of the order's platform."""
    from sqlalchemy import select

    from core.commerce.ucp.completion import platform_buyer_agent_id, session_buyer_agent_id
    from core.models.commerce_ucp import CommerceCheckoutSessionRow

    row = (
        await session.execute(
            select(CommerceCheckoutSessionRow).where(
                CommerceCheckoutSessionRow.tenant_id == tenant,
                CommerceCheckoutSessionRow.id == order.checkout_session_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return session_buyer_agent_id(row)
    return platform_buyer_agent_id(getattr(order, "platform_profile_url", None))


def _allowance(snapshot: Any) -> int | None:
    """The handler allowance recorded on the order at completion (None refuses: fail closed)."""
    allowance = (snapshot or {}).get("payment_allowance") if isinstance(snapshot, Mapping) else None
    value = allowance.get("max_amount_minor_units") if isinstance(allowance, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _record(
    session: Any,
    tenant_id: str,
    scope: Mapping[str, Any],
    order_id: str,
    outcome: str,
    decision_id: str,
    payload: Mapping[str, Any],
    reason: str | None,
    adjustment_id: str | None = None,
) -> None:
    await record_commerce_event(
        session,
        tenant_id=tenant_id,
        event=REFUND_EVENT,
        outcome=outcome,
        merchant_id=scope["merchant_id"],
        seller_agent_id=scope["seller_agent_id"],
        buyer_agent_id=None,
        payload_hash=commerce_payload_hash(payload),
        resource_id=order_id,
        details={"decision_id": decision_id, "reason": (reason or "")[:120], "adjustment_id": adjustment_id},
    )
