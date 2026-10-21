# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Payment execution records: the executor's journal of provider calls, refund bounds and RBI AFA evidence.

``DbPaymentJournal`` is the journal the Plural executor writes before and after
every provider call (``commerce_payment_operations``, append-only): a
``requested`` row first (unique per idempotency key, operation and phase, so a
second attempt with the same consumed decision is refused before any HTTP
call), then the outcome row and its commerce ledger event in one transaction.
Every call therefore leaves a sealed ledger row naming the ``decision_id``.

``payment_state`` reads what a refund may still return: the ``succeeded``
captures less every refund that was requested and has not failed (a refund
with an unknown outcome still counts, so an ambiguous refund never lets a
second one exceed the capture).

AFA evidence (``commerce_afa_evidence``, append-only): ``record_afa_requested``
when the completion returns the AFA step, ``afa_evidence_ref`` for the arbiter
input, and ``record_afa_authorized`` when a verified Plural event confirms the
mandate. Nothing here waits for the buyer.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from core.commerce.ledger import record_commerce_event

logger = structlog.get_logger()

PROVIDER_PLURAL = "plural_pine"
AFA_REQUESTED_EVENT = "commerce.ucp.payment.afa_requested"
AFA_AUTHORIZED_EVENT = "commerce.plural.afa_authorized"


def _tenant(tenant_id: Any) -> uuid.UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def ref_digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


@dataclass(frozen=True)
class OperationRecord:
    """One provider call: what it is, under which decision, for which session or order, and how much."""

    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    checkout_session_id: str
    decision_id: str
    operation: str
    idempotency_key: str
    amount_minor_units: int | None
    currency: str | None
    order_id: str | None = None
    provider: str = PROVIDER_PLURAL
    mandate_hashes: Mapping[str, str | None] = field(default_factory=dict)

    @property
    def ledger_event(self) -> str:
        return f"commerce.plural.{self.operation}"

    def ledger_payload(self, phase: str, provider_ref: str | None) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "operation": self.operation,
            "phase": phase,
            "idempotency_key": self.idempotency_key,
            "checkout_session_id": self.checkout_session_id,
            "order_id": self.order_id,
            "amount": self.amount_minor_units,
            "currency": self.currency,
            "provider_ref_sha256": ref_digest(provider_ref),
            "mandate_hashes": dict(self.mandate_hashes),
        }


@dataclass(frozen=True)
class PaymentState:
    provider_order_ref: str | None
    currency: str | None
    captured_minor_units: int
    refunded_minor_units: int

    @property
    def refundable_minor_units(self) -> int:
        return max(0, self.captured_minor_units - self.refunded_minor_units)


@dataclass(frozen=True)
class JournalEntry:
    """One ``commerce_payment_operations`` row as reconciliation reads it."""

    operation: str
    phase: str
    idempotency_key: str
    checkout_session_id: str
    decision_id: str
    merchant_id: str
    amount_minor_units: int | None
    currency: str | None
    provider_ref: str | None
    mandate_hashes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> JournalEntry:
        return cls(
            operation=row.operation,
            phase=row.phase,
            idempotency_key=row.idempotency_key,
            checkout_session_id=row.checkout_session_id,
            decision_id=row.decision_id,
            merchant_id=row.merchant_id,
            amount_minor_units=row.amount_minor_units,
            currency=row.currency,
            provider_ref=row.provider_ref,
            mandate_hashes=dict(row.mandate_hashes or {}),
        )


class PaymentJournal(Protocol):
    async def begin(self, op: OperationRecord) -> bool:
        """Record the ``requested`` row; False when this key and operation were already requested."""
        ...

    async def finish(
        self, op: OperationRecord, phase: str, *, provider_ref: str | None, details: Mapping[str, Any]
    ) -> None: ...

    async def blocked(self, op: OperationRecord, reason: str) -> None:
        """Ledger a call refused before it was sent (no row: nothing was requested)."""
        ...

    async def state(self, tenant_id: str, checkout_session_id: str) -> PaymentState: ...

    async def operations(self, tenant_id: str, idempotency_key: str) -> list[JournalEntry]: ...

    async def record_once(
        self, op: OperationRecord, phase: str, *, provider_ref: str | None, details: Mapping[str, Any]
    ) -> bool: ...

    async def session_seller(self, tenant_id: str, checkout_session_id: str) -> str | None: ...


class DbPaymentJournal:
    """The journal on the tenant database; each write is its own short transaction (no HTTP inside one)."""

    async def begin(self, op: OperationRecord) -> bool:
        from sqlalchemy.dialects.postgresql import insert

        from core.database import get_tenant_session
        from core.models.commerce_payments import CommercePaymentOperationRow as Row

        async with get_tenant_session(_tenant(op.tenant_id)) as session:
            inserted = (
                await session.execute(
                    insert(Row)
                    .values(**self._values(op, "requested", None, {}))
                    .on_conflict_do_nothing(constraint="uq_commerce_payment_operations")
                    .returning(Row.id)
                )
            ).first()
            if inserted is None:
                await record_commerce_event(
                    session,
                    tenant_id=op.tenant_id,
                    event=op.ledger_event,
                    outcome="refused",
                    merchant_id=op.merchant_id,
                    seller_agent_id=op.seller_agent_id,
                    buyer_agent_id=None,
                    payload=op.ledger_payload("replayed", None),
                    resource_id=op.idempotency_key,
                    details={"decision_id": op.decision_id, "reason": "operation_already_requested"},
                )
                return False
            return True

    async def finish(
        self, op: OperationRecord, phase: str, *, provider_ref: str | None, details: Mapping[str, Any]
    ) -> None:
        from core.database import get_tenant_session
        from core.models.commerce_payments import CommercePaymentOperationRow as Row

        async with get_tenant_session(_tenant(op.tenant_id)) as session:
            session.add(Row(id=uuid.uuid4(), **self._values(op, phase, provider_ref, details)))
            await record_commerce_event(
                session,
                tenant_id=op.tenant_id,
                event=op.ledger_event,
                outcome="success" if phase == "succeeded" else ("blocked" if phase == "unknown" else "refused"),
                merchant_id=op.merchant_id,
                seller_agent_id=op.seller_agent_id,
                buyer_agent_id=None,
                payload=op.ledger_payload(phase, provider_ref),
                resource_id=op.idempotency_key,
                details={"decision_id": op.decision_id, "phase": phase, **dict(details)},
            )

    async def blocked(self, op: OperationRecord, reason: str) -> None:
        from core.commerce.ledger import record_commerce_event_in_own_transaction

        await record_commerce_event_in_own_transaction(
            tenant_id=op.tenant_id,
            event=op.ledger_event,
            outcome="blocked",
            merchant_id=op.merchant_id,
            seller_agent_id=op.seller_agent_id,
            buyer_agent_id=None,
            payload=op.ledger_payload("blocked", None),
            resource_id=op.idempotency_key,
            details={"decision_id": op.decision_id, "reason": reason},
        )

    async def state(self, tenant_id: str, checkout_session_id: str) -> PaymentState:
        from core.database import get_tenant_session

        async with get_tenant_session(_tenant(tenant_id)) as session:
            return await payment_state(session, tenant_id=tenant_id, checkout_session_id=checkout_session_id)

    async def operations(self, tenant_id: str, idempotency_key: str) -> list[JournalEntry]:
        """Every journal row of one merchant reference in the tenant (reconciliation reads these)."""
        from sqlalchemy import select

        from core.database import get_tenant_session
        from core.models.commerce_payments import CommercePaymentOperationRow as Row

        async with get_tenant_session(_tenant(tenant_id)) as session:
            rows = (
                (
                    await session.execute(
                        select(Row)
                        .where(Row.tenant_id == _tenant(tenant_id), Row.idempotency_key == idempotency_key)
                        .order_by(Row.created_at, Row.id)
                    )
                )
                .scalars()
                .all()
            )
            return [JournalEntry.from_row(row) for row in rows]

    async def record_once(
        self, op: OperationRecord, phase: str, *, provider_ref: str | None, details: Mapping[str, Any]
    ) -> bool:
        """Append an outcome row learned from a verified provider event; False when that phase is already recorded."""
        from sqlalchemy.dialects.postgresql import insert

        from core.database import get_tenant_session
        from core.models.commerce_payments import CommercePaymentOperationRow as Row

        async with get_tenant_session(_tenant(op.tenant_id)) as session:
            inserted = (
                await session.execute(
                    insert(Row)
                    .values(id=uuid.uuid4(), **self._values(op, phase, provider_ref, details))
                    .on_conflict_do_nothing(constraint="uq_commerce_payment_operations")
                    .returning(Row.id)
                )
            ).first()
            if inserted is None:
                return False
            await record_commerce_event(
                session,
                tenant_id=op.tenant_id,
                event=op.ledger_event,
                outcome="success" if phase == "succeeded" else "refused",
                merchant_id=op.merchant_id,
                seller_agent_id=op.seller_agent_id or None,
                buyer_agent_id=None,
                payload=op.ledger_payload(phase, provider_ref),
                resource_id=op.idempotency_key,
                details={"decision_id": op.decision_id, "phase": phase, **dict(details)},
            )
            return True

    async def session_seller(self, tenant_id: str, checkout_session_id: str) -> str | None:
        from sqlalchemy import select

        from core.database import get_tenant_session
        from core.models.commerce_ucp import CommerceCheckoutSessionRow as Session

        async with get_tenant_session(_tenant(tenant_id)) as session:
            return (
                await session.execute(
                    select(Session.seller_agent_id).where(
                        Session.tenant_id == _tenant(tenant_id), Session.id == checkout_session_id
                    )
                )
            ).scalar_one_or_none()

    @staticmethod
    def _values(op: OperationRecord, phase: str, provider_ref: str | None, details: Mapping[str, Any]) -> dict:
        return {
            "tenant_id": _tenant(op.tenant_id),
            "merchant_id": op.merchant_id,
            "checkout_session_id": op.checkout_session_id,
            "order_id": op.order_id,
            "decision_id": op.decision_id,
            "provider": op.provider,
            "operation": op.operation,
            "phase": phase,
            "idempotency_key": op.idempotency_key,
            "provider_ref": provider_ref,
            "amount_minor_units": op.amount_minor_units,
            "currency": op.currency,
            "mandate_hashes": dict(op.mandate_hashes),
            "details": dict(details),
        }


async def payment_state(session: Any, *, tenant_id: Any, checkout_session_id: str) -> PaymentState:
    """Captured and refunded amounts of a checkout's payment (pending and unknown refunds count as refunded)."""
    from sqlalchemy import select

    from core.models.commerce_payments import CommercePaymentOperationRow as Row

    rows = (
        await session.execute(
            select(Row).where(Row.tenant_id == _tenant(tenant_id), Row.checkout_session_id == checkout_session_id)
        )
    ).scalars().all()
    return payment_state_from(rows)


def payment_state_from(rows: Any) -> PaymentState:
    """The pure part of ``payment_state`` over operation records (``operation``, ``idempotency_key``, ``phase``,
    ``amount_minor_units``, ``currency``, ``provider_ref``)."""
    phases: dict[tuple[str, str], set[str]] = {}
    amounts: dict[tuple[str, str], int] = {}
    provider_order_ref = None
    currency = None
    for row in rows:
        key = (row.operation, row.idempotency_key)
        phases.setdefault(key, set()).add(row.phase)
        if row.phase == "requested":
            amounts[key] = int(row.amount_minor_units or 0)
            currency = currency or row.currency
        if row.operation == "create_order" and row.phase == "succeeded":
            provider_order_ref = row.provider_ref
    captured = sum(amounts.get(k, 0) for k, p in phases.items() if k[0] == "capture" and "succeeded" in p)
    refunded = sum(amounts.get(k, 0) for k, p in phases.items() if k[0] == "refund" and "failed" not in p)
    return PaymentState(
        provider_order_ref=provider_order_ref,
        currency=currency,
        captured_minor_units=captured,
        refunded_minor_units=refunded,
    )


# --- RBI AFA evidence -----------------------------------------------------------------------------


async def record_afa_requested(
    session: Any,
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    checkout_session_id: str,
    instrument_id: str,
    reference: str,
    terms: Mapping[str, Any],
    terms_sha256: str,
) -> None:
    """Record (once) that the AFA step was returned for this mandate reference and terms, in ``session``."""
    from sqlalchemy.dialects.postgresql import insert

    from core.models.commerce_payments import CommerceAfaEvidenceRow as Row

    inserted = (
        await session.execute(
            insert(Row)
            .values(
                id=uuid.uuid4(),
                tenant_id=_tenant(tenant_id),
                merchant_id=merchant_id,
                checkout_session_id=checkout_session_id,
                instrument_id=instrument_id,
                mandate_reference=reference,
                terms_sha256=terms_sha256,
                terms=dict(terms),
                phase="requested",
                provider=PROVIDER_PLURAL,
            )
            .on_conflict_do_nothing(constraint="uq_commerce_afa_evidence_phase")
            .returning(Row.id)
        )
    ).first()
    if inserted is not None:
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=AFA_REQUESTED_EVENT,
            outcome="blocked",
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=None,
            payload={"mandate_reference": reference, "terms_sha256": terms_sha256},
            resource_id=checkout_session_id,
            details={"mandate_reference": reference, "terms_sha256": terms_sha256},
        )


async def afa_evidence_ref(session: Any, *, tenant_id: str, reference: str, terms_sha256: str) -> str | None:
    """The evidence ref of an ``authorized`` AFA row for exactly these terms, or None."""
    from sqlalchemy import select

    from core.models.commerce_payments import CommerceAfaEvidenceRow as Row

    return (
        await session.execute(
            select(Row.evidence_ref).where(
                Row.tenant_id == _tenant(tenant_id),
                Row.mandate_reference == reference,
                Row.terms_sha256 == terms_sha256,
                Row.phase == "authorized",
            )
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class AfaRecordResult:
    status: str  # "authorized", "replayed", "unknown_reference", "failed"
    checkout_session_id: str | None = None
    evidence_ref: str | None = None


async def record_afa_outcome(
    *,
    tenant_id: str,
    reference: str,
    authorized: bool,
    provider_mandate_ref: str | None,
    event_sha256: str,
    now: datetime | None = None,
) -> AfaRecordResult:
    """Record a verified provider outcome for an AFA step that was requested in this tenant.

    ``authorized``: an ``authorized`` row with the evidence ref, and a checkout
    session that was waiting only on this step returns to
    ``ready_for_complete`` (the next Complete gets a fresh arbiter decision,
    which now passes ``afa_evidence_present``). Anything else: a ``failed``
    row; the session stays blocked. An unknown reference records nothing.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from core.commerce.india_rails import evidence_ref as make_ref
    from core.database import get_tenant_session
    from core.models.commerce_payments import CommerceAfaEvidenceRow as Row

    moment = now or datetime.now(UTC)
    async with get_tenant_session(_tenant(tenant_id)) as session:
        requested = (
            await session.execute(
                select(Row)
                .where(
                    Row.tenant_id == _tenant(tenant_id), Row.mandate_reference == reference, Row.phase == "requested"
                )
                .order_by(Row.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if requested is None:
            return AfaRecordResult(status="unknown_reference")
        phase = "authorized" if authorized and provider_mandate_ref else "failed"
        ref = make_ref(PROVIDER_PLURAL, provider_mandate_ref or "", reference) if phase == "authorized" else None
        inserted = (
            await session.execute(
                insert(Row)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=_tenant(tenant_id),
                    merchant_id=requested.merchant_id,
                    checkout_session_id=requested.checkout_session_id,
                    instrument_id=requested.instrument_id,
                    mandate_reference=reference,
                    terms_sha256=requested.terms_sha256,
                    terms=dict(requested.terms),
                    phase=phase,
                    provider=PROVIDER_PLURAL,
                    evidence_ref=ref,
                    provider_event_sha256=event_sha256,
                )
                .on_conflict_do_nothing(constraint="uq_commerce_afa_evidence_phase")
                .returning(Row.id)
            )
        ).first()
        if inserted is None:
            return AfaRecordResult(status="replayed", checkout_session_id=requested.checkout_session_id)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=AFA_AUTHORIZED_EVENT,
            outcome="success" if phase == "authorized" else "refused",
            merchant_id=requested.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"mandate_reference": reference, "phase": phase, "event_sha256": event_sha256},
            resource_id=requested.checkout_session_id,
            details={"phase": phase, "terms_sha256": requested.terms_sha256},
            now=moment,
        )
        if phase == "authorized":
            await _release_waiting_session(session, tenant_id, requested.checkout_session_id, reference, moment)
        return AfaRecordResult(status=phase, checkout_session_id=requested.checkout_session_id, evidence_ref=ref)


async def _release_waiting_session(
    session: Any, tenant_id: str, checkout_session_id: str, reference: str, now: datetime
) -> None:
    from sqlalchemy import select

    from core.commerce.india_rails import AFA_ERROR_CODE, UCP_AFA_ACTION
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Session

    row = (
        await session.execute(
            select(Session)
            .where(Session.tenant_id == _tenant(tenant_id), Session.id == checkout_session_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status not in ("incomplete", "requires_escalation"):
        return
    state = dict(row.state or {})
    if (state.get("afa_pending") or {}).get("mandate_reference") != reference:
        return
    actions = dict(state.get("actions") or {})
    actions.pop(UCP_AFA_ACTION, None)
    if actions:
        state["actions"] = actions
    else:
        state.pop("actions", None)
    state["messages"] = [m for m in state.get("messages") or [] if m.get("code") != AFA_ERROR_CODE]
    state.pop("afa_pending", None)
    if row.status == "requires_escalation":
        state.pop("continue_url", None)
    from core.commerce.ucp.checkout import status_for

    row.state = state
    row.status = status_for(state["messages"], state.get("continue_url"))
    row.revision = row.revision + 1
    row.updated_at = now
