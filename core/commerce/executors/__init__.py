# SPDX-License-Identifier: Apache-2.0
"""Checkout executors: the only code that turns a completed UCP checkout into a provider order.

An executor implements ``CheckoutExecutor.execute(session, decision)``. It
cannot be called without a ``ConsumedDecision``, and the only way to get one
is ``consume_decision``, which calls ``require_allow_decision`` (action
``session_complete``): the decision must be an unexpired, unused ``allow`` of
the same tenant for the exact session payload hash, and it is consumed in the
caller's transaction (single use; of two concurrent completions exactly one
wins). No executor is registered by default: a merchant whose UCP settings
name no registered executor gets the business message ``executor_unavailable``
and the session stays ``ready_for_complete``. The Plural executor is
registered by the payment-rail work (``core/commerce/executors/plural.py``).

The credential the platform sent with Complete reaches the executor in memory
only (``ExecutionCheckout.instruments[*].credential``); nothing stores it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

SESSION_COMPLETE = "session_complete"
_GATE = object()


@dataclass(frozen=True)
class ConsumedDecision:
    """Proof that an ``allow`` decision was consumed for this payload (constructed by ``consume_decision`` only)."""

    decision_id: str
    payload_hash: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    _gate: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._gate is not _GATE:
            raise TypeError("ConsumedDecision is issued by consume_decision only")


@dataclass(frozen=True)
class ExecutionInstrument:
    id: str
    handler_id: str
    type: str
    credential: Mapping[str, Any] | None = field(repr=False, default=None)
    amount_minor_units: int | None = None


@dataclass(frozen=True)
class ExecutionCheckout:
    session_id: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    ucp_version: str
    currency: str
    total_minor_units: int
    line_items: Sequence[Mapping[str, Any]]
    instruments: Sequence[ExecutionInstrument]
    token_binding: Mapping[str, Any]
    payload_hash: str


@dataclass(frozen=True)
class ExecutionResult:
    """``completed``: the provider order exists. ``action_required``: the buyer must authenticate the payment
    (``actions`` for 2026-08-25, ``escalation_url`` for 2026-04-08). ``failed``: nothing was charged."""

    status: Literal["completed", "action_required", "failed"]
    provider_order_ref: str | None = None
    actions: Mapping[str, Sequence[Mapping[str, Any]]] | None = None
    escalation_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class CheckoutExecutor(Protocol):
    name: str

    async def execute(self, session: ExecutionCheckout, decision: ConsumedDecision) -> ExecutionResult: ...


_EXECUTORS: dict[str, CheckoutExecutor] = {}


def register_executor(executor: CheckoutExecutor) -> None:
    _EXECUTORS[executor.name] = executor


def unregister_executor(name: str) -> None:
    _EXECUTORS.pop(name, None)


def get_executor(name: str | None) -> CheckoutExecutor | None:
    return _EXECUTORS.get(name) if name else None


async def consume_decision(
    db_session: Any,
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    decision_id: str | None,
    payload_hash: str,
) -> ConsumedDecision:
    """Consume ``decision_id`` for ``session_complete`` on ``payload_hash``; raises ``DecisionRefusedError``."""
    from core.commerce.arbiter_store import require_allow_decision

    row = await require_allow_decision(
        db_session, tenant_id=tenant_id, decision_id=decision_id, payload_hash=payload_hash, action=SESSION_COMPLETE
    )
    return ConsumedDecision(
        decision_id=str(row.decision_id),
        payload_hash=payload_hash,
        tenant_id=str(tenant_id),
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        _gate=_GATE,
    )
