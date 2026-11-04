# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Refunds of Plural hosted payments with the merchant's own Plural credentials.

Two cases, both started by a merchant administrator:

* An order created from a Plural hosted hand-off (executor ``plural_hosted``):
  ``core.commerce.order_refunds.refund_order`` decides and consumes the
  ``refund`` decision as for any order, then calls ``refund_hosted_order``
  here. What may be refunded is the order total less every refund already
  recorded on the order (an adjustment of type ``refund`` that is ``completed``
  or ``pending``) and every refund call journalled for the order that did not
  fail, whichever is larger. No agent payment allowance applies: the buyer paid
  on the merchant's own Plural page.
* A Plural payment no order matches (``commerce_unmatched_payments``):
  ``refund_unmatched_payment`` refunds the full paid amount of that Plural
  order once. A payment whose order was created while stock was short is
  refunded through its order instead (the order then shows the refund).

The Plural call follows the executor's refund call
(``core.commerce.executors.plural.PluralExecutor.refund``): ``POST
/pay/v1/refunds/{plural order id}`` with ``merchant_order_reference``,
``refund_amount {value, currency}`` and ``merchant_metadata``; ``Request-ID``
derives from the reference. The reference is deterministic (the order id and
the caller's request id, or the unmatched payment and its attempt), and the
journal (``commerce_payment_operations``, provider ``plural_hosted``) records
the call before it is sent, unique per reference: a repeated request finds
the first one and never calls Plural twice. The journal row is written in the
transaction that locks the order and re-reads what was already refunded, so
two different requests cannot both pass the bound.

Outcomes: only an explicit refusal (a 4xx business error other than 408, 409,
425 and 429, or a refund status FAILED, REJECTED, DECLINED or CANCELLED) is
``failed`` and frees the amount. A 5xx, a timeout, a conflict, a pending or an
unrecognised status is ``unknown``: it counts as refunded and is not retried.
The reconciliation (``reconcile_all``, a beat task every 5 minutes, and the
admin "check again" action) reads the Plural order (``GET /pay/v1/orders/{id}``)
for refunds unknown for at least ``RECONCILE_AFTER`` and records ``succeeded``
or ``failed`` when the answer names the refund's reference (or the order is
fully refunded by a whole-order refund). When it cannot tell, the refund stays
unknown and shows as pending.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from core.commerce.ledger import commerce_payload_hash, json_safe, record_commerce_event

logger = structlog.get_logger()

HOSTED_EXECUTOR = "plural_hosted"
PROVIDER = "plural_hosted"
UNMATCHED_REFUND_EVENT = "commerce.ucp.payment.unmatched_refund"
SUCCEEDED = frozenset({"PROCESSED", "CAPTURED", "SUCCESS", "REFUNDED"})
PENDING = frozenset({"PENDING", "INITIATED", "CREATED"})
# Refund states Plural reports for a refund it did not make.
REFUND_FAILED = frozenset({"FAILED", "REJECTED", "DECLINED", "CANCELLED"})
# HTTP answers that do not prove Plural refused the refund (it may have taken it): the outcome is unknown.
AMBIGUOUS_HTTP = frozenset({408, 409, 425, 429})


def refund_phase(status: str) -> str:
    """The journal phase of a refund state: ``succeeded``, ``failed`` only for an explicit failure, else ``unknown``."""
    value = str(status or "").upper()
    if value in SUCCEEDED:
        return "succeeded"
    if value in REFUND_FAILED:
        return "failed"
    return "unknown"


def refused_phase(http_status: int) -> str:
    """A Plural HTTP error: ``failed`` for a business refusal (4xx), ``unknown`` for 5xx and timeouts or conflicts."""
    return "failed" if 400 <= int(http_status) < 500 and int(http_status) not in AMBIGUOUS_HTTP else "unknown"


REFUNDABLE_STATUSES = ("none", "failed")


def _tenant(tenant_id: Any) -> uuid.UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def adjustment_id_for(reference: str) -> str:
    """The order adjustment id of the refund with ``reference`` (deterministic, so a retry finds it)."""
    return "adj_" + hashlib.sha256(f"refund|{reference}".encode()).hexdigest()[:16]


def unmatched_reference(payment_id: str, attempt: int) -> str:
    """The Plural ``merchant_order_reference`` of a refund of an unmatched payment (one per attempt)."""
    return "aou-" + hashlib.sha256(f"{payment_id}|{attempt}".encode()).hexdigest()[:44]


def recorded_refunds(
    snapshot: Mapping[str, Any] | None, *, exclude_ids: frozenset[str] | set[str] = frozenset()
) -> int:
    """The refunds recorded on an order snapshot: ``refund`` adjustments ``completed`` or ``pending``.

    ``exclude_ids`` leaves out adjustments a journalled refund call already accounts for.
    """
    total = 0
    for entry in (snapshot or {}).get("adjustments") or []:
        if not isinstance(entry, Mapping) or entry.get("type") != "refund":
            continue
        if entry.get("id") in exclude_ids:
            continue
        if entry.get("status") not in ("completed", "pending"):
            continue
        for line in entry.get("totals") or []:
            amount = line.get("amount") if isinstance(line, Mapping) else None
            if isinstance(line, Mapping) and line.get("type") == "total" and isinstance(amount, int):
                total += abs(amount)
    return total


async def journalled_refund_calls(session: Any, *, tenant: uuid.UUID, order_id: str) -> dict[str, tuple[set[str], int]]:
    """Every refund call journalled for the order: ``{reference: (phases, requested amount)}``."""
    from core.models.commerce_payments import CommercePaymentOperationRow as Row

    rows = (
        await session.execute(
            select(Row.idempotency_key, Row.phase, Row.amount_minor_units).where(
                Row.tenant_id == tenant, Row.order_id == order_id, Row.operation == "refund"
            )
        )
    ).all()
    calls: dict[str, tuple[set[str], int]] = {}
    for key, phase, amount in rows:
        phases, requested = calls.get(key, (set(), 0))
        phases.add(phase)
        calls[key] = (phases, int(amount or 0) if phase == "requested" else requested)
    return calls


async def journalled_refunds(session: Any, *, tenant: uuid.UUID, order_id: str) -> int:
    """Refund calls journalled for the order whose outcome is not ``failed`` (requested, unknown, succeeded)."""
    calls = await journalled_refund_calls(session, tenant=tenant, order_id=order_id)
    return sum(amount for phases, amount in calls.values() if "failed" not in phases)


async def hosted_refunded(session: Any, *, tenant: uuid.UUID, order: Any) -> int:
    """What is already refunded of a hosted order.

    Every journalled refund call that did not fail, plus every refund recorded on the order that no
    journalled call accounts for (for example one a merchant recorded by hand through the order-update
    route). A journalled call's adjustment has the id ``adjustment_id_for(reference)``, so it is never
    counted twice.
    """
    calls = await journalled_refund_calls(session, tenant=tenant, order_id=order.id)
    journalled = sum(amount for phases, amount in calls.values() if "failed" not in phases)
    represented = {adjustment_id_for(reference) for reference in calls}
    return journalled + recorded_refunds(order.snapshot, exclude_ids=represented)


def credential_ref_for_order(state: Mapping[str, Any], provider_order_ref: str | None) -> str | None:
    """The vault credential the checkout's Plural link was issued under (``handoff_orders[ref].credential_ref``)."""
    orders = state.get("handoff_orders") if isinstance(state.get("handoff_orders"), Mapping) else {}
    records = [r for r in orders.values() if isinstance(r, Mapping)]
    matching = [r for r in records if provider_order_ref and r.get("provider_order_id") == provider_order_ref]
    for record in [*matching, *records]:
        if record.get("credential_ref"):
            return str(record["credential_ref"])
    handoff = state.get("handoff") if isinstance(state.get("handoff"), Mapping) else {}
    return str(handoff.get("credential_ref") or "") or None


@dataclass(frozen=True)
class HostedRefundResult:
    status: str  # "succeeded", "unknown", "failed", "refused", "replayed"
    reason: str
    provider_refund_ref: str | None = None


def _operation(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    checkout_session_id: str,
    decision_id: str,
    reference: str,
    amount: int,
    currency: str,
    order_id: str | None,
) -> Any:
    from core.commerce.payment_records import OperationRecord

    return OperationRecord(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        checkout_session_id=checkout_session_id,
        decision_id=decision_id,
        operation="refund",
        idempotency_key=reference,
        amount_minor_units=amount,
        currency=currency.upper(),
        order_id=order_id,
        provider=PROVIDER,
    )


async def _begin(session: Any, op: Any) -> bool:
    """Journal the ``requested`` row in ``session``; False when this reference was already requested."""
    from sqlalchemy.dialects.postgresql import insert

    from core.commerce.payment_records import DbPaymentJournal
    from core.models.commerce_payments import CommercePaymentOperationRow as Row

    inserted = (
        await session.execute(
            insert(Row)
            .values(id=uuid.uuid4(), **DbPaymentJournal._values(op, "requested", None, {}))
            .on_conflict_do_nothing(constraint="uq_commerce_payment_operations")
            .returning(Row.id)
        )
    ).first()
    return inserted is not None


async def _journalled(session: Any, *, tenant: uuid.UUID, reference: str) -> bool:
    from core.models.commerce_payments import CommercePaymentOperationRow as Row

    found = (
        await session.execute(
            select(Row.id)
            .where(Row.tenant_id == tenant, Row.idempotency_key == reference, Row.operation == "refund")
            .limit(1)
        )
    ).first()
    return found is not None


async def _call_plural(credentials: Any, *, provider_order_id: str, op: Any, metadata: Mapping[str, str]) -> Any:
    """The refund call and its journal outcome row; returns ``(phase, provider refund ref)``."""
    from core.commerce import plural_hosted
    from core.commerce.executors.plural import PluralApiClient, PluralRequestError, PluralUnavailableError, request_id
    from core.commerce.payment_records import DbPaymentJournal

    journal = DbPaymentJournal()
    body = {
        "merchant_order_reference": op.idempotency_key,
        "refund_amount": {"value": int(op.amount_minor_units), "currency": str(op.currency).upper()},
        "merchant_metadata": {k: str(v)[:255] for k, v in metadata.items()},
    }
    client = PluralApiClient(credentials, transport_factory=plural_hosted.transport_override)
    try:
        data = await client.call(
            "POST", f"/pay/v1/refunds/{provider_order_id}", body, request_id=request_id(op.idempotency_key, "refund")
        )
    except PluralRequestError as exc:
        # Only a 4xx business refusal proves no refund was made; a 5xx, a timeout or a conflict may have
        # been processed, so it stays unknown (counted as refunded) until the reconciliation reads it.
        phase = refused_phase(exc.status_code)
        await journal.finish(
            op, phase, provider_ref=provider_order_id, details={"http_status": exc.status_code, "code": exc.code}
        )
        return phase, None
    except PluralUnavailableError as exc:
        logger.warning("plural_hosted_refund_outcome_unknown", error_type=type(exc).__name__)
        await journal.finish(op, "unknown", provider_ref=provider_order_id, details={"error": "unreachable"})
        return "unknown", None
    status = str(data.get("status") or "").upper()
    ref = str(data.get("refund_id") or data.get("order_id") or provider_order_id or "") or None
    phase = refund_phase(status)
    await journal.finish(op, phase, provider_ref=ref, details={"status": status[:40]})
    return phase, ref


async def load_order_credentials(session: Any, *, tenant: uuid.UUID, order: Any) -> Any:
    """The merchant's Plural credentials the order's checkout was paid under, or None."""
    from core.commerce import plural_hosted
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Checkout

    state = (
        await session.execute(
            select(Checkout.state).where(Checkout.tenant_id == tenant, Checkout.id == order.checkout_session_id)
        )
    ).scalar_one_or_none()
    credential_ref = credential_ref_for_order(dict(state or {}), order.provider_order_ref)
    if credential_ref is None:
        return None
    return await plural_hosted.load_credentials(
        session, tenant_id=tenant, credential_ref=credential_ref, merchant_id=order.merchant_id
    )


async def refund_hosted_order(
    *,
    tenant_id: str,
    order_id: str,
    reference: str,
    decision_id: str,
    payload_hash: str,
    amount_minor_units: int,
    currency: str,
) -> HostedRefundResult:
    """Refund ``amount`` of a hosted order under a consumed decision: bound, journal, then Plural.

    The order row is locked while what was already refunded is re-read and the ``requested`` row is
    written, so concurrent refunds of one order are serialised on the bound.
    """
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    tenant = _tenant(tenant_id)
    async with get_tenant_session(tenant) as session:
        order = (
            await session.execute(
                select(CommerceOrderRow)
                .where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        problem: str | None = None
        credentials = None
        if order is not None and await _journalled(session, tenant=tenant, reference=reference):
            # The same request is already in flight or done: never re-check its own amount against the bound.
            return HostedRefundResult(status="replayed", reason="operation_already_requested")
        if order is None or order.executor != HOSTED_EXECUTOR:
            problem = "order_not_hosted"
        elif not order.provider_order_ref:
            problem = "order_not_captured"
        elif currency.upper() != str(order.currency).upper():
            problem = "refund_currency_mismatch"
        else:
            refunded = await hosted_refunded(session, tenant=tenant, order=order)
            if refunded + amount_minor_units > int(order.total_minor_units):
                problem = "refund_exceeds_captured"
        if problem is None:
            credentials = await load_order_credentials(session, tenant=tenant, order=order)
            if credentials is None:
                problem = "plural_credentials_missing"
        if problem is not None or order is None or credentials is None:
            return HostedRefundResult(status="refused", reason=problem or "not_refundable")
        op = _operation(
            tenant_id=str(tenant),
            merchant_id=order.merchant_id,
            seller_agent_id=order.seller_agent_id,
            checkout_session_id=order.checkout_session_id,
            decision_id=decision_id,
            reference=reference,
            amount=amount_minor_units,
            currency=currency,
            order_id=order.id,
        )
        if not await _begin(session, op):
            return HostedRefundResult(status="replayed", reason="operation_already_requested")
        provider_order_id = str(order.provider_order_ref)
    phase, ref = await _call_plural(
        credentials.credentials,
        provider_order_id=provider_order_id,
        op=op,
        metadata={"oacp_decision_id": decision_id, "oacp_order_id": order_id, "oacp_payload_hash": payload_hash},
    )
    reason = {"succeeded": "refund_succeeded", "unknown": "refund_unknown"}.get(phase, "refund_failed")
    return HostedRefundResult(status=phase, reason=reason, provider_refund_ref=ref)


# --- payments no order matches -----------------------------------------------------------------------


@dataclass(frozen=True)
class UnmatchedRefundOutcome:
    status: str  # "succeeded", "pending", "refused", "failed"
    reason: str
    payment: dict[str, Any]
    decision: dict[str, Any] | None = None


UNREPORTED_PREFIX = "unreported:"


def provider_order_known(provider_order_id: Any) -> bool:
    """Whether Plural named the order the payment is on (rows without one key on the hand-off reference)."""
    return bool(provider_order_id) and not str(provider_order_id).startswith(UNREPORTED_PREFIX)


def unmatched_view(row: Any) -> dict[str, Any]:
    """What the merchant may see of a payment that needs it (never a credential)."""
    from core.commerce.order_notifications import UNMATCHED_REASONS, money

    def _iso(value: Any) -> str | None:
        return value.isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else None

    refundable = row.refund_status in REFUNDABLE_STATUSES and (
        row.order_id is not None
        or (
            isinstance(row.amount_minor_units, int)
            and row.amount_minor_units > 0
            and provider_order_known(row.provider_order_id)
        )
    )
    return {
        "id": str(row.id),
        "merchant_id": row.merchant_id,
        "checkout_id": row.checkout_session_id,
        "order_id": row.order_id,
        "reason": row.reason,
        "reason_text": UNMATCHED_REASONS.get(row.reason, "it does not match the checkout"),
        "amount_minor_units": row.amount_minor_units,
        "currency": row.currency,
        "amount": money(row.amount_minor_units, row.currency) or None,
        "provider_order_ref_present": provider_order_known(row.provider_order_id),
        "refund_checkable": row.refund_status in ("requested", "pending"),
        "short_lines": list((row.details or {}).get("lines") or []),
        "refund_status": row.refund_status,
        "refund_error": row.refund_error,
        "refundable": bool(refundable),
        "refunded_at": _iso(row.refunded_at),
        "created_at": _iso(row.created_at),
    }


async def _record_unmatched(
    *, tenant: uuid.UUID, row_view: Mapping[str, Any], outcome: str, reason: str, decision_id: str | None, **extra: Any
) -> None:
    from core.database import get_tenant_session

    payload = json_safe({"payment_id": row_view.get("id"), "reason": reason, **extra})
    async with get_tenant_session(tenant) as session:
        await record_commerce_event(
            session,
            tenant_id=str(tenant),
            event=UNMATCHED_REFUND_EVENT,
            outcome=outcome,
            merchant_id=row_view.get("merchant_id"),
            seller_agent_id=row_view.get("seller_agent_id"),
            buyer_agent_id=None,
            payload_hash=commerce_payload_hash(payload),
            resource_id=str(row_view.get("id") or ""),
            details={"decision_id": decision_id, "reason": reason[:120], **extra},
        )


class UnmatchedRefundError(ValueError):
    """An unmatched payment that cannot be refunded here (404 unknown, 409 not refundable)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


async def _load_unmatched(session: Any, tenant: uuid.UUID, payment_id: str, *, lock: bool) -> Any:
    from core.models.commerce_payments import CommerceUnmatchedPaymentRow as Unmatched

    try:
        key = uuid.UUID(str(payment_id))
    except ValueError:
        return None
    query = select(Unmatched).where(Unmatched.tenant_id == tenant, Unmatched.id == key)
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def refund_unmatched_payment(
    *,
    tenant_id: str,
    payment_id: str,
    reason: str | None,
    now: datetime | None = None,
    grantex_client: Any = None,
) -> UnmatchedRefundOutcome:
    """Refund the full paid amount of a Plural payment no order matches, once.

    A ``refund`` arbiter decision (captured: the paid amount; refunded: nothing yet) is sealed and
    consumed first, as for an order refund. The row is locked while its refund state moves to
    ``requested`` and the journal row is written, so a second request (or a concurrent one) is
    refused without calling Plural. A refund Plural refuses returns the row to ``failed`` (a new
    attempt gets a new reference); one with an unknown outcome stays ``pending`` and counts as
    refunded. A payment whose order was created (stock short) is refunded through the order.
    """
    from core.commerce import executors, plural_hosted
    from core.commerce.arbiter import ArbiterContext, RefundEvidence, decide
    from core.commerce.arbiter_grantex import countersign_decision
    from core.commerce.arbiter_store import DecisionRefusedError, persist_decision
    from core.database import get_tenant_session

    tenant = _tenant(tenant_id)
    moment = now or datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        row = await _load_unmatched(session, tenant, payment_id, lock=False)
        if row is None:
            raise UnmatchedRefundError(404, "payment_not_found")
        view = {**unmatched_view(row), "seller_agent_id": row.seller_agent_id}
        order_id = row.order_id
    if row.refund_status not in REFUNDABLE_STATUSES:
        await _record_unmatched(
            tenant=tenant, row_view=view, outcome="refused", reason="refund_already_requested", decision_id=None
        )
        return UnmatchedRefundOutcome(status="refused", reason="refund_already_requested", payment=view)
    if order_id is not None:
        return await _refund_through_order(
            tenant=tenant, payment_id=str(row.id), order_id=order_id, reason=reason, attempt=int(row.refund_attempts)
        )
    if not provider_order_known(row.provider_order_id):
        await _record_unmatched(
            tenant=tenant, row_view=view, outcome="refused", reason="provider_order_unknown", decision_id=None
        )
        return UnmatchedRefundOutcome(status="refused", reason="provider_order_unknown", payment=view)
    amount, currency = row.amount_minor_units, str(row.currency or "").upper()
    if not isinstance(amount, int) or amount < 1 or not currency:
        await _record_unmatched(
            tenant=tenant, row_view=view, outcome="refused", reason="amount_unknown", decision_id=None
        )
        return UnmatchedRefundOutcome(status="refused", reason="amount_unknown", payment=view)

    payload = json_safe(
        {
            "action": "refund",
            "unmatched_payment_id": str(row.id),
            "checkout_session_id": row.checkout_session_id,
            "merchant_id": row.merchant_id,
            "seller_agent_id": row.seller_agent_id,
            "amount": amount,
            "currency": currency,
            "captured_minor_units": amount,
            "refunded_minor_units": 0,
            "attempt": int(row.refund_attempts) + 1,
        }
    )
    decision = decide(
        "refund",
        ArbiterContext(
            tenant_id=str(tenant),
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            now=moment,
            payload=payload,
            amount_minor_units=amount,
            currency=currency,
            quantity=None,
            refund=RefundEvidence(
                order_id=str(row.id), currency=currency, captured_minor_units=amount, refunded_minor_units=0
            ),
        ),
    )
    async with get_tenant_session(tenant) as session:
        await persist_decision(session, decision)
    public = decision.to_public()
    if not decision.allowed:
        await _record_unmatched(
            tenant=tenant,
            row_view=view,
            outcome="refused",
            reason=str(decision.failing_rule),
            decision_id=decision.decision_id,
        )
        return UnmatchedRefundOutcome(
            status="refused", reason=str(decision.failing_rule), payment=view, decision=public
        )
    await countersign_decision(decision, client=grantex_client)
    try:
        async with get_tenant_session(tenant) as session:
            await executors.consume_decision(
                session,
                tenant_id=str(tenant),
                merchant_id=row.merchant_id,
                seller_agent_id=row.seller_agent_id,
                decision_id=decision.decision_id,
                payload_hash=decision.payload_hash,
                action=executors.REFUND,
            )
    except DecisionRefusedError as exc:
        await _record_unmatched(
            tenant=tenant, row_view=view, outcome="refused", reason=exc.code, decision_id=decision.decision_id
        )
        return UnmatchedRefundOutcome(status="refused", reason=exc.code, payment=view, decision=public)

    async with get_tenant_session(tenant) as session:
        locked = await _load_unmatched(session, tenant, payment_id, lock=True)
        problem: str | None = None
        credentials = None
        if locked is None or locked.refund_status not in REFUNDABLE_STATUSES:
            problem = "refund_already_requested"
        elif not locked.credential_ref:
            problem = "plural_credentials_missing"
        else:
            credentials = await plural_hosted.load_credentials(
                session, tenant_id=tenant, credential_ref=locked.credential_ref, merchant_id=locked.merchant_id
            )
            if credentials is None:
                problem = "plural_credentials_missing"
        op = None
        if problem is None and locked is not None:
            attempt = int(locked.refund_attempts) + 1
            reference = unmatched_reference(str(locked.id), attempt)
            op = _operation(
                tenant_id=str(tenant),
                merchant_id=locked.merchant_id,
                seller_agent_id=locked.seller_agent_id,
                checkout_session_id=locked.checkout_session_id,
                decision_id=decision.decision_id,
                reference=reference,
                amount=amount,
                currency=currency,
                order_id=None,
            )
            if not await _begin(session, op):
                problem = "refund_already_requested"
            else:
                locked.refund_attempts = attempt
                locked.refund_status = "requested"
                locked.refund_reference = reference
                locked.refund_error = None
                locked.updated_at = moment
        provider_order_id = str(locked.provider_order_id) if locked is not None else ""
    if problem is not None or op is None or credentials is None:
        await _record_unmatched(
            tenant=tenant,
            row_view=view,
            outcome="refused",
            reason=problem or "not_refundable",
            decision_id=decision.decision_id,
        )
        return UnmatchedRefundOutcome(
            status="refused", reason=problem or "not_refundable", payment=view, decision=public
        )

    phase, _ref = await _call_plural(
        credentials.credentials,
        provider_order_id=provider_order_id,
        op=op,
        metadata={
            "oacp_decision_id": decision.decision_id,
            "oacp_unmatched_payment_id": str(row.id),
            "oacp_payload_hash": decision.payload_hash,
        },
    )
    status = {"succeeded": "refunded", "unknown": "pending"}.get(phase, "failed")
    finished = datetime.now(UTC) if now is None else moment
    async with get_tenant_session(tenant) as session:
        locked = await _load_unmatched(session, tenant, payment_id, lock=True)
        if locked is not None:
            locked.refund_status = status
            locked.refund_error = None if status != "failed" else "plural_refused_the_refund"
            locked.refunded_at = finished if status == "refunded" else None
            locked.updated_at = finished
            view = {**unmatched_view(locked), "seller_agent_id": locked.seller_agent_id}
        await record_commerce_event(
            session,
            tenant_id=str(tenant),
            event=UNMATCHED_REFUND_EVENT,
            outcome={"refunded": "success", "pending": "blocked"}.get(status, "refused"),
            merchant_id=view.get("merchant_id"),
            seller_agent_id=view.get("seller_agent_id"),
            buyer_agent_id=None,
            payload_hash=decision.payload_hash,
            resource_id=str(row.id),
            details={"decision_id": decision.decision_id, "reason": f"refund_{phase}", "refund_status": status},
        )
    view.pop("seller_agent_id", None)
    outcome_status = {"refunded": "succeeded", "pending": "pending"}.get(status, "failed")
    return UnmatchedRefundOutcome(status=outcome_status, reason=f"refund_{phase}", payment=view, decision=public)


def through_order_request_id(payment_id: str, attempt: int) -> str:
    """The order refund request id of an attempt to refund a stock-short payment (a refusal frees a new one)."""
    return f"unmatched-{payment_id}-{attempt}"


async def _refund_through_order(
    *, tenant: uuid.UUID, payment_id: str, order_id: str, reason: str | None, attempt: int
) -> UnmatchedRefundOutcome:
    """A payment whose order was made while stock was short: refund what is left of the order, once.

    The order refund's request id carries the attempt: a refund Plural refused moves the payment to
    ``failed`` with the next attempt, so the next click is a new refund, not a replay of the refusal.
    """
    from core.commerce.executors.plural import refund_reference
    from core.commerce.order_refunds import RefundRequestError, refund_order
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    async with get_tenant_session(tenant) as session:
        order = (
            await session.execute(
                select(CommerceOrderRow).where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
            )
        ).scalar_one_or_none()
        remaining = 0
        if order is not None:
            remaining = int(order.total_minor_units) - await hosted_refunded(session, tenant=tenant, order=order)
    if order is None or remaining < 1:
        raise UnmatchedRefundError(409, "order_already_refunded" if order is not None else "order_not_found")
    try:
        outcome = await refund_order(
            tenant_id=str(tenant),
            order_id=order_id,
            amount_minor_units=remaining,
            reason=reason or "Refund: stock short at payment",
            now=datetime.now(UTC),
            request_id=through_order_request_id(payment_id, attempt),
        )
    except RefundRequestError as exc:
        raise UnmatchedRefundError(exc.status, exc.code) from exc
    status = {"succeeded": "refunded", "pending": "pending", "failed": "failed"}.get(outcome.status)
    async with get_tenant_session(tenant) as session:
        row = await _load_unmatched(session, tenant, payment_id, lock=True)
        if row is not None and status is not None:
            row.refund_status = status
            # The order refund's journal reference, which the reconciliation follows.
            row.refund_reference = refund_reference(order_id, through_order_request_id(payment_id, attempt))
            row.refund_error = "plural_refused_the_refund" if status == "failed" else None
            if status == "failed":
                row.refund_attempts = attempt + 1
            row.refunded_at = datetime.now(UTC) if status == "refunded" else None
            row.updated_at = datetime.now(UTC)
        view = unmatched_view(row) if row is not None else {"id": payment_id}
        seller = row.seller_agent_id if row is not None else None
    await _record_unmatched(
        tenant=tenant,
        row_view={**view, "seller_agent_id": seller},
        outcome={"succeeded": "success", "pending": "blocked"}.get(outcome.status, "refused"),
        reason=outcome.reason,
        decision_id=(outcome.decision or {}).get("decision_id"),
        order_id=order_id,
    )
    return UnmatchedRefundOutcome(
        status=outcome.status,
        reason=outcome.reason,
        payment={**view, "adjustment_id": outcome.adjustment_id},
        decision=outcome.decision,
    )


# --- reconciliation: refunds whose outcome is not known yet ---------------------------------------------

# A refund call whose outcome is unknown is read from Plural once it is this old (the admin "check again"
# action too), and no longer after RECONCILE_WINDOW.
RECONCILE_AFTER = timedelta(minutes=5)
RECONCILE_WINDOW = timedelta(days=30)
RECONCILE_BATCH = 50
_REFUND_REFERENCE_KEYS = ("merchant_order_reference", "merchant_refund_reference", "merchant_refund_id")
_ORDER_REFUNDED = frozenset({"REFUNDED", "FULLY_REFUNDED"})


@dataclass(frozen=True)
class OutstandingRefund:
    """A journalled refund call with no ``succeeded`` or ``failed`` outcome yet."""

    reference: str
    order_id: str | None
    checkout_session_id: str
    merchant_id: str
    decision_id: str
    amount_minor_units: int
    currency: str
    requested_at: datetime


def _refund_entries(value: Any) -> list[Mapping[str, Any]]:
    """Every object in a Plural order answer that names a merchant refund reference and a status."""
    out: list[Mapping[str, Any]] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            if item.get("status") and any(item.get(key) for key in _REFUND_REFERENCE_KEYS):
                out.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return out


def refund_state_from_order(order: Mapping[str, Any], *, reference: str, amount: int) -> str | None:
    """What a Plural order read (``GET /pay/v1/orders/{id}``) says of the refund ``reference``.

    ``succeeded`` or ``failed`` when a refund entry carries our reference; ``succeeded`` too when the
    order itself is fully refunded and the refund was for the whole order amount; None when the
    answer does not say (the refund stays unknown). The refund fields of the order read are not
    documented for hosted orders, so only these unambiguous signs are used.
    """
    for entry in _refund_entries(order):
        if any(str(entry.get(key) or "") == reference for key in _REFUND_REFERENCE_KEYS):
            phase = refund_phase(str(entry.get("status") or ""))
            return phase if phase != "unknown" else None
    nested = order.get("order") if isinstance(order.get("order"), Mapping) else order
    order_amount = nested.get("order_amount") if isinstance(nested.get("order_amount"), Mapping) else {}
    if str(nested.get("status") or "").upper() in _ORDER_REFUNDED and order_amount.get("value") == amount:
        return "succeeded"
    return None


async def outstanding_refunds(
    session: Any,
    *,
    tenant: uuid.UUID,
    now: datetime,
    order_id: str | None = None,
    reference: str | None = None,
    limit: int = RECONCILE_BATCH,
) -> list[OutstandingRefund]:
    """Hosted refund calls journalled as ``requested`` (or ``unknown``) at least ``RECONCILE_AFTER`` ago."""
    from core.models.commerce_payments import CommercePaymentOperationRow as Row

    query = select(Row).where(
        Row.tenant_id == tenant,
        Row.provider == PROVIDER,
        Row.operation == "refund",
        Row.created_at >= now - RECONCILE_WINDOW,
    )
    if order_id is not None:
        query = query.where(Row.order_id == order_id)
    if reference is not None:
        query = query.where(Row.idempotency_key == reference)
    rows = (await session.execute(query.order_by(Row.created_at).limit(limit * 4))).scalars().all()
    by_key: dict[str, list[Any]] = {}
    for row in rows:
        by_key.setdefault(row.idempotency_key, []).append(row)
    out: list[OutstandingRefund] = []
    for key, entries in by_key.items():
        phases = {e.phase for e in entries}
        requested = next((e for e in entries if e.phase == "requested"), None)
        if requested is None or phases & {"succeeded", "failed"} or requested.created_at > now - RECONCILE_AFTER:
            continue
        out.append(
            OutstandingRefund(
                reference=key,
                order_id=requested.order_id,
                checkout_session_id=requested.checkout_session_id,
                merchant_id=requested.merchant_id,
                decision_id=requested.decision_id,
                amount_minor_units=int(requested.amount_minor_units or 0),
                currency=str(requested.currency or ""),
                requested_at=requested.created_at,
            )
        )
    return out[:limit]


async def _refund_target(session: Any, tenant: uuid.UUID, item: OutstandingRefund) -> tuple[Any, str, str]:
    """``(credentials, Plural order id, seller agent id)`` of an outstanding refund (credentials None when absent)."""
    from core.commerce import plural_hosted
    from core.models.commerce_payments import CommerceUnmatchedPaymentRow as Unmatched
    from core.models.commerce_ucp import CommerceOrderRow

    if item.order_id is not None:
        order = (
            await session.execute(
                select(CommerceOrderRow).where(
                    CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == item.order_id
                )
            )
        ).scalar_one_or_none()
        if order is None:
            return None, "", ""
        credentials = await load_order_credentials(session, tenant=tenant, order=order)
        return credentials, str(order.provider_order_ref or ""), order.seller_agent_id
    unmatched = (
        await session.execute(
            select(Unmatched).where(Unmatched.tenant_id == tenant, Unmatched.refund_reference == item.reference)
        )
    ).scalar_one_or_none()
    if unmatched is None or not unmatched.credential_ref:
        return None, "", ""
    credentials = await plural_hosted.load_credentials(
        session, tenant_id=tenant, credential_ref=unmatched.credential_ref, merchant_id=unmatched.merchant_id
    )
    return credentials, str(unmatched.provider_order_id), unmatched.seller_agent_id


async def reconcile_refund(tenant_id: Any, item: OutstandingRefund, *, now: datetime | None = None) -> str:
    """Read one outstanding refund from Plural and record what it says.

    Returns ``succeeded`` or ``failed`` (recorded in the journal, on the order adjustment and on the
    unmatched payment), ``still_unknown`` (the answer does not say), ``status_unavailable`` (Plural
    could not be read) or ``credentials_missing``. An unknown refund stays counted as refunded and
    visible as pending.
    """
    from core.commerce import plural_hosted
    from core.commerce.executors.plural import PluralRequestError, PluralUnavailableError
    from core.commerce.payment_records import DbPaymentJournal
    from core.commerce.ucp.handlers import apply_order_change, set_order_adjustment_status
    from core.database import get_tenant_session
    from core.models.commerce_payments import CommerceUnmatchedPaymentRow as Unmatched

    tenant = _tenant(tenant_id)
    moment = now or datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        credentials, provider_order_id, seller = await _refund_target(session, tenant, item)
    if credentials is None or not provider_order_known(provider_order_id):
        return "credentials_missing"
    try:
        data = await plural_hosted.fetch_order(credentials.credentials, provider_order_id)
    except (PluralRequestError, PluralUnavailableError) as exc:
        logger.warning("plural_hosted_refund_status_unavailable", error_type=type(exc).__name__)
        return "status_unavailable"
    phase = refund_state_from_order(data, reference=item.reference, amount=item.amount_minor_units)
    if phase is None:
        return "still_unknown"
    op = _operation(
        tenant_id=str(tenant),
        merchant_id=item.merchant_id,
        seller_agent_id=seller,
        checkout_session_id=item.checkout_session_id,
        decision_id=item.decision_id,
        reference=item.reference,
        amount=item.amount_minor_units,
        currency=item.currency or "INR",
        order_id=item.order_id,
    )
    await DbPaymentJournal().record_once(
        op, phase, provider_ref=provider_order_id, details={"source": "reconcile", "phase": phase}
    )
    if item.order_id is not None:
        adjustment_id = adjustment_id_for(item.reference)
        changed = await set_order_adjustment_status(
            tenant_id=str(tenant),
            order_id=item.order_id,
            adjustment_id=adjustment_id,
            status="completed" if phase == "succeeded" else "failed",
            now=moment,
        )
        if changed is None and phase == "succeeded":
            # Plural took it but no adjustment was recorded (a crash): record it now, under its fixed id.
            await apply_order_change(
                tenant_id=str(tenant),
                order_id=item.order_id,
                fulfillment_event=None,
                adjustment={
                    "type": "refund",
                    "status": "completed",
                    "totals": [{"type": "total", "amount": -item.amount_minor_units}],
                    "description": "Refund",
                },
                now=moment,
                adjustment_id=adjustment_id,
            )
    async with get_tenant_session(tenant) as session:
        row = (
            await session.execute(
                select(Unmatched)
                .where(Unmatched.tenant_id == tenant, Unmatched.refund_reference == item.reference)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None and row.refund_status in ("requested", "pending"):
            row.refund_status = "refunded" if phase == "succeeded" else "failed"
            row.refund_error = None if phase == "succeeded" else "plural_reported_the_refund_failed"
            row.refunded_at = moment if phase == "succeeded" else None
            if phase == "failed" and row.order_id is not None:
                row.refund_attempts = int(row.refund_attempts) + 1
            row.updated_at = moment
    return phase


async def check_refunds(
    tenant_id: Any,
    *,
    order_id: str | None = None,
    reference: str | None = None,
    now: datetime | None = None,
    limit: int = RECONCILE_BATCH,
) -> dict[str, int]:
    """Reconcile the tenant's outstanding hosted refunds (optionally one order's, or one reference); counts."""
    from core.database import get_tenant_session

    tenant = _tenant(tenant_id)
    moment = now or datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        items = await outstanding_refunds(
            session, tenant=tenant, now=moment, order_id=order_id, reference=reference, limit=limit
        )
    counts: dict[str, int] = {}
    for item in items:
        result = await reconcile_refund(tenant, item, now=now)
        counts[result] = counts.get(result, 0) + 1
    return counts


async def reconcile_all(*, now: datetime | None = None) -> dict[str, int]:
    """The beat sweep: every tenant's outstanding hosted refunds, read from Plural once each."""
    from core.commerce.ucp.webhooks import _tenants_with_pending

    totals: dict[str, int] = {}
    for tenant in await _tenants_with_pending():
        try:
            counts = await check_refunds(tenant, now=now)
        # enterprise-gate: broad-except-ok reason=one-tenant-failure-never-stops-the-refund-reconciliation
        except Exception as exc:
            logger.warning("plural_hosted_refund_reconcile_failed", error_type=type(exc).__name__)
            continue
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value
    return totals


REFUND_CHECK_EVENT = "commerce.ucp.order.refund_check"
UNMATCHED_REFUND_CHECK_EVENT = "commerce.ucp.payment.unmatched_refund_check"


async def check_unmatched_refund(
    tenant_id: Any, payment_id: str, *, now: datetime | None = None
) -> tuple[str, dict[str, Any]]:
    """The admin "check again" action for an unmatched payment's refund: ``(result, payment view)``.

    Results: ``nothing_to_check`` (no refund waiting), ``too_recent`` (asked less than
    ``RECONCILE_AFTER`` ago), else the reconciliation's (``succeeded``, ``failed``, ``still_unknown``,
    ``status_unavailable``, ``credentials_missing``). Raises ``UnmatchedRefundError`` 404 for an unknown id.
    """
    from core.database import get_tenant_session

    tenant = _tenant(tenant_id)
    moment = now or datetime.now(UTC)
    async with get_tenant_session(tenant) as session:
        row = await _load_unmatched(session, tenant, payment_id, lock=False)
        if row is None:
            raise UnmatchedRefundError(404, "payment_not_found")
        status, reference, updated = row.refund_status, row.refund_reference, row.updated_at
    if status not in ("requested", "pending") or not reference:
        result = "nothing_to_check"
    elif updated is not None and updated > moment - RECONCILE_AFTER:
        result = "too_recent"
    else:
        counts = await check_refunds(tenant, reference=reference, now=now)
        result = next(iter(counts), "nothing_to_check")
    async with get_tenant_session(tenant) as session:
        row = await _load_unmatched(session, tenant, payment_id, lock=False)
        view = {**unmatched_view(row), "seller_agent_id": row.seller_agent_id} if row is not None else {}
    return result, view
