# SPDX-License-Identifier: Apache-2.0
"""Commitments: committed or prepaid volume over a billing period.

A **quantity** commitment names a provider, a usage type, optionally a
model, a canonical card unit and a quantity in card units (500 x
``1m_input_tokens``); it may carry an overage price per card unit. A
**money** commitment names a provider, optionally a usage type, and an
amount in a currency; it has no unit to price overage by. Periods are
half-open ranges of the provider's billing dates. Two active commitments
for the same ``(provider, usage_type, model_sku, unit, kind)`` may not
overlap; the check runs under one advisory lock on create and on every
change of the period or the status.

A storage commitment is entered in ``gb_day``: a GB-month has no fixed
number of GB-days. Drawdown (computed from usage records in a later part)
is in record units; every create or update marks the commitment for a full
recompute.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select

from core.spend import audit, clock, locks, vocab
from core.spend.errors import SpendError, require_actor

logger = structlog.get_logger()

AUDIT_FIELDS = (
    "provider",
    "kind",
    "usage_type",
    "model_sku",
    "unit",
    "committed_quantity",
    "committed_amount",
    "currency",
    "period_start",
    "period_end",
    "overage_unit_price",
    "overage_currency",
    "status",
    "reference",
)


def quantity_units(usage_type: str) -> tuple[str, ...]:
    """The canonical card units a quantity commitment of ``usage_type`` may be entered in (never ``gb_month``)."""
    canonical = set(vocab.CANONICAL_UNIT.values())
    return tuple(unit for unit in vocab.CARD_UNITS[usage_type] if unit in canonical and unit != "gb_month")


def check_commitment(body: dict[str, Any]) -> dict[str, Any]:
    """A commitment's fields, checked whole."""
    kind = vocab.choice(body.get("kind"), vocab.COMMITMENT_KINDS, field="kind")
    raw_type = body.get("usage_type")
    usage_type = (
        vocab.choice(raw_type, vocab.USAGE_TYPES, field="usage_type", code="invalid_unit")
        if raw_type not in (None, "")
        else None
    )
    model_sku = vocab.norm_sku(body.get("model_sku"), allow_empty=True)
    raw_unit = body.get("unit")
    unit = str(raw_unit).strip().lower() if raw_unit not in (None, "") else None
    period_start = vocab.parse_date(body.get("period_start"), field="period_start")
    period_end = vocab.parse_date(body.get("period_end"), field="period_end")
    if period_end <= period_start:
        raise SpendError(422, "invalid_period", "period_end is after period_start (it is exclusive)")
    fields: dict[str, Any] = {
        "provider": vocab.norm_provider(body.get("provider")),
        "kind": kind,
        "usage_type": usage_type,
        "model_sku": model_sku,
        "unit": None,
        "committed_quantity": None,
        "committed_amount": None,
        "currency": None,
        "period_start": period_start,
        "period_end": period_end,
        "overage_unit_price": None,
        "overage_currency": None,
        "reference": vocab.free_text(body.get("reference"), field="reference", max_len=200),
    }
    overage_price = body.get("overage_unit_price")
    overage_currency = body.get("overage_currency")
    if kind == "quantity":
        if usage_type is None:
            raise SpendError(422, "invalid_unit", "a quantity commitment names its usage type")
        allowed = quantity_units(usage_type)
        if unit not in allowed:
            raise SpendError(422, "invalid_unit", f"unit is one of {', '.join(allowed)} (storage is entered in gb_day)")
        if body.get("committed_amount") not in (None, "") or body.get("currency") not in (None, ""):
            raise SpendError(
                422, "invalid_number", "a quantity commitment has no amount or currency; its overage price has one"
            )
        fields["unit"] = unit
        fields["committed_quantity"] = vocab.parse_decimal(
            body.get("committed_quantity"),
            field="committed_quantity",
            minimum=0,
            strict_minimum=True,
            maximum=vocab.MAX_COMMITTED_QUANTITY,
            places=vocab.QUANTITY_PLACES,
        )
        if (overage_price in (None, "")) != (overage_currency in (None, "")):
            raise SpendError(422, "invalid_number", "an overage price comes with its currency")
        if overage_price not in (None, ""):
            fields["overage_unit_price"] = vocab.parse_decimal(
                overage_price,
                field="overage_unit_price",
                minimum=0,
                maximum=vocab.MAX_UNIT_PRICE,
                places=vocab.PRICE_PLACES,
            )
            fields["overage_currency"] = vocab.norm_currency(overage_currency)
    else:
        if unit is not None:
            raise SpendError(422, "invalid_unit", "a money commitment has no unit")
        if model_sku:
            raise SpendError(
                422, "invalid_sku", "a money commitment applies to a provider or a usage type, not a model"
            )
        if overage_price not in (None, "") or overage_currency not in (None, ""):
            raise SpendError(422, "invalid_number", "a money commitment has no unit to price overage by")
        if body.get("committed_quantity") not in (None, ""):
            raise SpendError(422, "invalid_number", "a money commitment commits an amount, not a quantity")
        fields["committed_amount"] = vocab.parse_decimal(
            body.get("committed_amount"),
            field="committed_amount",
            minimum=0,
            strict_minimum=True,
            maximum=vocab.MAX_COMMITTED_AMOUNT,
            places=vocab.PRICE_PLACES,
        )
        fields["currency"] = vocab.norm_currency(body.get("currency"))
    return fields


def _remaining(row: Any) -> Decimal | None:
    if row.kind == "quantity" and row.committed_quantity is not None and row.unit:
        drawn_card_units = Decimal(row.drawn_quantity or 0) / Decimal(vocab.CANONICAL_DIVISOR.get(row.unit, 1))
        return max(Decimal("0"), Decimal(row.committed_quantity) - drawn_card_units)
    if row.kind == "money" and row.committed_amount is not None:
        return max(Decimal("0"), Decimal(row.committed_amount) - Decimal(row.drawn_amount or 0))
    return None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _commitment_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "kind": row.kind,
        "usage_type": row.usage_type,
        "model_sku": row.model_sku or "",
        "unit": row.unit,
        "committed_quantity": vocab.dec_str(row.committed_quantity),
        "committed_amount": vocab.dec_str(row.committed_amount),
        "currency": str(row.currency).strip() if row.currency else None,
        "period_start": _iso(row.period_start),
        "period_end": _iso(row.period_end),
        "overage_unit_price": vocab.dec_str(row.overage_unit_price),
        "overage_currency": str(row.overage_currency).strip() if row.overage_currency else None,
        "drawn_quantity": vocab.dec_str(row.drawn_quantity),
        "drawn_amount": vocab.dec_str(row.drawn_amount),
        "remaining": vocab.dec_str(_remaining(row)),
        "undrawn_records": int(row.undrawn_records or 0),
        "recomputed_through": _iso(row.recomputed_through),
        "needs_full_recompute": bool(row.needs_full_recompute),
        "status": row.status,
        "reference": row.reference or "",
        "updated_by": row.updated_by or "",
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _audit_fields(row: Any) -> dict[str, Any]:
    return {name: getattr(row, name) for name in AUDIT_FIELDS}


def _periods_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start < b_end and b_start < a_end


async def _check_overlap(session: Any, tenant_id: uuid.UUID, fields: dict[str, Any], *, exclude_id: Any) -> None:
    from core.models.spend import SpendCommitment

    await locks.xact_lock(
        session,
        locks.commitment(
            tenant_id, fields["provider"], fields["usage_type"], fields["model_sku"], fields["unit"], fields["kind"]
        ),
    )
    statement = select(SpendCommitment).where(
        SpendCommitment.tenant_id == tenant_id,
        SpendCommitment.status == "active",
        SpendCommitment.provider == fields["provider"],
        SpendCommitment.kind == fields["kind"],
        SpendCommitment.model_sku == fields["model_sku"],
    )
    for other in (await session.execute(statement)).scalars().all():
        if other.id == exclude_id or other.usage_type != fields["usage_type"] or other.unit != fields["unit"]:
            continue
        if _periods_overlap(fields["period_start"], fields["period_end"], other.period_start, other.period_end):
            raise SpendError(
                409,
                "commitment_overlap",
                f"commitment {other.id} covers {other.period_start.isoformat()} to "
                f"{other.period_end.isoformat()} for the same provider, usage, model, unit and kind",
            )


async def create_commitment(
    tenant_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """A new active commitment, refused when it overlaps another of the same key."""
    from core.database import get_tenant_session
    from core.models.spend import SpendCommitment

    who = require_actor(actor)
    fields = check_commitment(body)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        await _check_overlap(session, tenant_id, fields, exclude_id=None)
        row = SpendCommitment(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            status="active",
            needs_full_recompute=True,
            drawn_quantity=Decimal("0"),
            drawn_amount=Decimal("0"),
            undrawn_records=0,
            created_by=who,
            updated_by=who,
            created_at=stamp,
            updated_at=stamp,
            **fields,
        )
        session.add(row)
        await session.flush()
        session.add(
            audit.audit_change(
                tenant_id,
                actor_id=who,
                action="commitments.create",
                resource_type="spend_commitment",
                resource_id=str(row.id),
                changes=[audit.Change(str(row.id), None, _audit_fields(row))],
                now=stamp,
            )
        )
        out = _commitment_dict(row)
    logger.info("spend_commitment_created", kind=fields["kind"], provider=fields["provider"])
    return out


async def update_commitment(
    tenant_id: uuid.UUID,
    commitment_id: uuid.UUID,
    body: dict[str, Any],
    *,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Change ``period_end``, close it (``status='closed'``), or change its reference."""
    from core.database import get_tenant_session
    from core.models.spend import SpendCommitment

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SpendCommitment)
                    .where(SpendCommitment.tenant_id == tenant_id, SpendCommitment.id == commitment_id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise SpendError(404, "not_found", "no such commitment")
        row = rows[0]
        before = _audit_fields(row)
        period_end = row.period_end
        if body.get("period_end") is not None:
            period_end = vocab.parse_date(body["period_end"], field="period_end")
            if period_end <= row.period_start:
                raise SpendError(422, "invalid_period", "period_end is after period_start (it is exclusive)")
        status = row.status
        if body.get("status") is not None:
            status = vocab.choice(body["status"], ("closed",), field="status")
            if row.status == "closed":
                status = "closed"
        reference = row.reference
        if "reference" in body:
            reference = vocab.free_text(body.get("reference"), field="reference", max_len=200)
        if status == "active" and row.status == "active" and period_end != row.period_end:
            fields = {
                "provider": row.provider,
                "usage_type": row.usage_type,
                "model_sku": row.model_sku or "",
                "unit": row.unit,
                "kind": row.kind,
                "period_start": row.period_start,
                "period_end": period_end,
            }
            await _check_overlap(session, tenant_id, fields, exclude_id=row.id)
        changed = (period_end, status, reference) != (row.period_end, row.status, row.reference)
        if changed:
            row.period_end = period_end
            row.status = status
            row.reference = reference
            row.needs_full_recompute = True
            row.updated_by = who
            row.updated_at = stamp
            await session.flush()
            session.add(
                audit.audit_change(
                    tenant_id,
                    actor_id=who,
                    action="commitments.update",
                    resource_type="spend_commitment",
                    resource_id=str(row.id),
                    changes=[audit.Change(str(row.id), before, _audit_fields(row))],
                    now=stamp,
                )
            )
        out = _commitment_dict(row)
    return out


async def list_commitments(
    tenant_id: uuid.UUID,
    *,
    provider: str | None = None,
    active: bool | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Commitments with their drawdown so far and what remains."""
    from core.database import get_tenant_session
    from core.models.spend import SpendCommitment

    conditions = [SpendCommitment.tenant_id == tenant_id]
    if provider:
        conditions.append(SpendCommitment.provider == vocab.norm_provider(provider))
    if active is not None:
        conditions.append(SpendCommitment.status == ("active" if active else "closed"))
    async with get_tenant_session(tenant_id) as session:
        total = (await session.execute(select(func.count()).select_from(SpendCommitment).where(*conditions))).scalar()
        rows = (
            (
                await session.execute(
                    select(SpendCommitment)
                    .where(*conditions)
                    .order_by(SpendCommitment.provider, SpendCommitment.period_start.desc())
                    .limit(max(1, min(limit, 500)))
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_commitment_dict(row) for row in rows], "total": int(total or 0)}
