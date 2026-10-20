# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The commerce ledger: every commerce write leaves a signed ``audit_log`` row in its own transaction.

``record_commerce_event`` adds one row to the caller's session, so the row
commits or rolls back with the business write it records. The row is signed
by the platform audit key (``core.tool_gateway.audit_logger``) and is linked
into the tenant's hash chain by the sealing task (``core.governance.audit_chain``);
``audit_log`` itself is append-only.

Each row carries ``details.ledger``: the commerce action, the merchant, the
seller and buyer agents and ``payload_hash``, the lowercase hex SHA-256 of the
RFC 8785 canonical JSON of the event payload (the same canonicalisation the
OACP artifact JWS profile uses, ``core.commerce.oacp_jws.oacp_payload_hash``).
The payload itself is not stored: whoever holds it can prove which row it is.

Route enforcement: ``api.route_enforcement.enforce_route_metadata`` opens a
request scope (``open_request_scope``) for every commerce write route that
declares ``route_meta(audit_event=...)``; ``record_commerce_event`` marks the
event in that scope, and a write route that returns without having recorded
its declared event is a server error in enforce mode (logged in log mode).

Seams for the ledger read/export work: ``list_commerce_events`` reads a
tenant's ledger rows in chain order (sealed rows first, then unsealed in write
order) and ``ledger_view`` turns one row into the exportable shape.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from core.commerce.oacp_jws import oacp_payload_hash

logger = structlog.get_logger()

LEDGER_VERSION = "commerce-ledger-v1"
LEDGER_RESOURCE_TYPE = "commerce_ledger"
LEDGER_EVENT_PREFIXES: tuple[str, ...] = ("commerce.", "a2a.")
OUTCOMES = frozenset({"success", "blocked", "refused", "allow", "refuse", "consumed", "escalated"})


class CommerceLedgerError(ValueError):
    """A ledger row could not be built (bad tenant, event name or payload): the write must not commit."""


@dataclass
class RequestLedgerScope:
    """What one request recorded, and who made it (filled by route enforcement)."""

    declared_event: str | None = None
    actor_type: str = "system"
    actor_id: str = "system"
    recorded: set[str] = field(default_factory=set)


# Evidence values that are digests, not personal data: a hex SHA-256 (optionally prefixed
# ``sha256:``) or an opaque id whose suffix is hex (``arbdec_<hex>``, ``aoc-<hex>``). The PII
# masker's phone and account patterns match digit runs inside them and would corrupt the
# evidence the ledger seals (a mandate hash that cannot be matched to its mandate).
_DIGEST_VALUE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$|^[a-z][a-z0-9]{1,15}[_-][0-9a-f]{24,64}$")


def _mask_details(value: Any) -> Any:
    """``mask_pii`` for ledger details, keeping digest values intact."""
    from core.tool_gateway.pii_masker import mask_pii

    if isinstance(value, str):
        return value if _DIGEST_VALUE.match(value) else mask_pii(value)
    if isinstance(value, Mapping):
        return {k: _mask_details(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_details(item) for item in value]
    return value


_scope: ContextVar[RequestLedgerScope | None] = ContextVar("commerce_ledger_scope", default=None)


def open_request_scope(*, declared_event: str | None, actor_type: str, actor_id: str) -> tuple[RequestLedgerScope, Any]:
    """Start recording which ledger events this request writes; returns the scope and the reset token."""
    scope = RequestLedgerScope(declared_event=declared_event, actor_type=actor_type, actor_id=actor_id)
    return scope, _scope.set(scope)


def close_request_scope(token: Any) -> None:
    try:
        _scope.reset(token)
    except ValueError:  # finalised in another context: just clear this one
        _scope.set(None)


def current_request_scope() -> RequestLedgerScope | None:
    return _scope.get()


def json_safe(value: Any) -> Any:
    """``value`` as plain JSON types (tuples become arrays, datetimes ISO strings)."""
    return json.loads(json.dumps(value, default=_default, sort_keys=True))


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, set | frozenset):
        return sorted(value)
    if hasattr(value, "__dict__"):
        return vars(value)
    return str(value)


def commerce_payload_hash(payload: Any) -> str:
    """Lowercase hex SHA-256 of the RFC 8785 canonical JSON of ``payload`` (after ``json_safe``)."""
    return oacp_payload_hash(json_safe(payload))


def _tenant_uuid(tenant_id: Any) -> uuid.UUID:
    try:
        return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except (TypeError, ValueError) as exc:
        raise CommerceLedgerError("commerce ledger rows need the tenant UUID") from exc


@dataclass(frozen=True)
class CommerceLedgerEntry:
    audit_id: uuid.UUID
    event: str
    payload_hash: str
    outcome: str
    created_at: datetime

    def to_ref(self) -> dict[str, Any]:
        return {
            "ledger_ref": str(self.audit_id),
            "event": self.event,
            "payload_hash": self.payload_hash,
            "outcome": self.outcome,
        }


async def record_commerce_event(
    session: Any,
    *,
    tenant_id: Any,
    event: str,
    outcome: str,
    merchant_id: str | None,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
    payload: Any = None,
    payload_hash: str | None = None,
    resource_id: str | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    details: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> CommerceLedgerEntry:
    """Add the signed ``audit_log`` row for one commerce event to ``session`` (no commit here).

    ``payload_hash`` is computed from ``payload`` unless the caller already holds
    the hash (an arbiter decision is bound to its handoff payload hash). The
    actor defaults to the authenticated principal of the current request.
    Raises ``CommerceLedgerError`` on a bad tenant, event or outcome, so the
    business write fails with it rather than committing unrecorded.
    """
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import build_signed_audit_entry
    tenant = _tenant_uuid(tenant_id)
    if not isinstance(event, str) or not event.startswith(LEDGER_EVENT_PREFIXES) or len(event) > 100:
        raise CommerceLedgerError("commerce ledger events are named commerce.* or a2a.* (at most 100 characters)")
    if outcome not in OUTCOMES:
        raise CommerceLedgerError(f"unknown commerce ledger outcome {outcome!r}")
    digest = payload_hash if payload_hash is not None else commerce_payload_hash(payload)
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise CommerceLedgerError("payload_hash must be lowercase hex SHA-256")
    scope = current_request_scope()
    created_at = now or datetime.now(UTC)
    ledger = {
        "version": LEDGER_VERSION,
        "event": event,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "payload_hash": digest,
        "payload_canonicalization": "RFC8785-SHA256",
    }
    entry = build_signed_audit_entry(
        tenant_id=tenant,
        event_type=event,
        actor_type=(actor_type or (scope.actor_type if scope else "system"))[:20],
        actor_id=(actor_id or (scope.actor_id if scope else "system"))[:255],
        action=event,
        outcome=outcome,
        details={**_mask_details(json_safe(dict(details or {}))), "ledger": ledger},
        created_at=created_at,
        resource_type=LEDGER_RESOURCE_TYPE,
        resource_id=(resource_id or digest)[:255],
    )
    row = AuditLog(id=uuid.uuid4(), **entry)
    session.add(row)
    if scope is not None:
        scope.recorded.add(event)
    logger.info("commerce_ledger_recorded", ledger_event=event, outcome=outcome, payload_hash=digest)
    return CommerceLedgerEntry(
        audit_id=row.id, event=event, payload_hash=digest, outcome=outcome, created_at=created_at
    )


async def record_commerce_event_in_own_transaction(*, tenant_id: Any, **kwargs: Any) -> CommerceLedgerEntry:
    """``record_commerce_event`` for a route outcome with no business write (a refusal, a blocked call)."""
    from core.database import get_tenant_session

    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        return await record_commerce_event(session, tenant_id=tenant_id, **kwargs)


def ledger_view(row: Any) -> dict[str, Any]:
    """One ledger row in its exportable shape: the signed columns, the chain link and the ledger fields."""
    details = dict(getattr(row, "details", None) or {})
    created_at = getattr(row, "created_at", None)
    sealed_at = getattr(row, "sealed_at", None)
    return {
        "audit_id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "event": row.event_type,
        "outcome": row.outcome,
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "resource_id": row.resource_id,
        "ledger": details.get("ledger") or {},
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
        "signature": row.signature,
        "chain_seq": row.chain_seq,
        "chain_prev": row.chain_prev,
        "chain_hash": row.chain_hash,
        "sealed_at": sealed_at.isoformat() if isinstance(sealed_at, datetime) else sealed_at,
    }


async def list_commerce_events(
    session: Any,
    tenant_id: Any,
    *,
    after_seq: int = 0,
    limit: int = 500,
    include_unsealed: bool = True,
) -> list[Any]:
    """A tenant's ledger rows: sealed rows after ``after_seq`` in chain order, then unsealed rows in write order."""
    from sqlalchemy import select

    from core.models.audit import AuditLog

    tenant = _tenant_uuid(tenant_id)
    size = max(1, min(int(limit), 5000))
    base = select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.resource_type == LEDGER_RESOURCE_TYPE)
    sealed = list(
        (
            await session.scalars(
                base.where(AuditLog.chain_seq.is_not(None), AuditLog.chain_seq > after_seq)
                .order_by(AuditLog.chain_seq.asc())
                .limit(size)
            )
        ).all()
    )
    if not include_unsealed or len(sealed) >= size:
        return sealed
    unsealed = list(
        (
            await session.scalars(
                base.where(AuditLog.chain_seq.is_(None))
                .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                .limit(size - len(sealed))
            )
        ).all()
    )
    return sealed + unsealed
