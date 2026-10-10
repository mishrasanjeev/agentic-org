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
number of GB-days. Drawdown is computed from usage records in record units
(``recompute``, run as the ``recompute_commitments`` job); every create or
update marks the commitment for a full recompute and queues the job.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    await enqueue_recompute(tenant_id, fields["provider"], actor=who)
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
    if changed:
        await enqueue_recompute(tenant_id, out["provider"], actor=who)
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


# ---------------------------------------------------------------- drawdown (recompute)

APPEND_GRACE = timedelta(hours=2)
RECOMPUTE_CHUNK = 1000


@dataclass
class Drawn:
    """A commitment's running drawdown during a recompute."""

    quantity: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    undrawn: int = 0


def match_rank(record: Any, commitment: Any) -> int | None:
    """How specifically ``commitment`` covers ``record`` (0 = quantity with model ... 3 = money without usage
    type), or ``None`` when it does not cover it."""
    if commitment.status != "active" or commitment.provider != record.provider:
        return None
    if not (commitment.period_start <= record.billing_date < commitment.period_end):
        return None
    if commitment.kind == "quantity":
        if commitment.usage_type != record.usage_type:
            return None
        if commitment.unit != vocab.CANONICAL_UNIT.get(record.unit):
            return None
        model = (record.model or "").strip()
        if commitment.model_sku and commitment.model_sku != model:
            return None
        return 0 if commitment.model_sku else 1
    if commitment.usage_type is None:
        return 3
    return 2 if commitment.usage_type == record.usage_type else None


def best_commitment(record: Any, commitments: Sequence[Any]) -> Any:
    """The most specific active commitment covering ``record``, or ``None``."""
    ranked = [(rank, str(c.id), c) for c in commitments if (rank := match_rank(record, c)) is not None]
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _overage(before: Decimal, drawn: Decimal, capacity: Decimal) -> Decimal:
    """``max(0, before + drawn - max(capacity, before))``: the part of this draw past the capacity."""
    return max(Decimal("0"), before + drawn - max(capacity, before))


def draw(
    record: Any, commitment: Any, state: Drawn, rate_to_inr: Decimal | None
) -> tuple[uuid.UUID | None, bool, Decimal]:
    """Draw ``record`` from ``commitment``; ``(commitment_id, overage, overage_quantity)`` for the record.

    Quantity commitments draw the record's quantity in record units (the
    capacity is the committed card units times the unit's divisor); an
    unpriced record still draws its quantity. Money commitments draw the
    record's amount in the commitment's currency, through INR at the
    reporting-date rate when the currencies differ; a priced record that
    cannot be converted does not draw and is counted as undrawn, an unpriced
    one does not draw. A money overage is reported as the matching share of
    the record's quantity.
    """
    quantity = Decimal(record.quantity or 0)
    if commitment.kind == "quantity":
        capacity = Decimal(commitment.committed_quantity) * vocab.CANONICAL_DIVISOR.get(commitment.unit, 1)
        over = _overage(state.quantity, quantity, capacity)
        state.quantity += quantity
        return commitment.id, over > 0, over.quantize(vocab.QTY_QUANT)
    if record.amount is None or record.currency is None:
        return None, False, Decimal("0")
    target = str(commitment.currency).strip()
    if str(record.currency).strip() == target:
        amount = Decimal(record.amount)
    elif record.amount_inr is not None and target == vocab.REPORTING_CURRENCY:
        amount = Decimal(record.amount_inr)
    elif record.amount_inr is not None and rate_to_inr:
        amount = (Decimal(record.amount_inr) / Decimal(rate_to_inr)).quantize(vocab.AMOUNT_QUANT)
    else:
        state.undrawn += 1
        return None, False, Decimal("0")
    over = _overage(state.amount, amount, Decimal(commitment.committed_amount))
    state.amount += amount
    if over <= 0 or amount <= 0:
        return commitment.id, False, Decimal("0")
    share = min(quantity, (quantity * over / amount).quantize(vocab.QTY_QUANT))
    return commitment.id, share > 0, share


async def _provider_commitments(session: Any, tenant_id: uuid.UUID, provider: str) -> list[Any]:
    from core.models.spend import SpendCommitment

    statement = select(SpendCommitment).where(
        SpendCommitment.tenant_id == tenant_id, SpendCommitment.provider == provider
    )
    return list((await session.execute(statement)).scalars().all())


def _billing_days(provider: str, start: datetime, end: datetime) -> list[date]:
    first = clock.billing_date_of(provider, start)
    last = clock.billing_date_of(provider, end - timedelta(microseconds=1))
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


async def _money_rate(session: Any, tenant_id: uuid.UUID, rates: dict, currency: str, on: date) -> Decimal | None:
    from core.spend import fx

    key = (currency, on)
    if key not in rates:
        found = await fx.rate_on(session, tenant_id, currency, on)
        rates[key] = found.rate_to_inr if found is not None else None
    return rates[key]


async def _replay_window(
    tenant_id: uuid.UUID,
    provider: str,
    commitments: list[Any],
    states: dict[uuid.UUID, Drawn],
    *,
    start: datetime,
    end: datetime,
    now: datetime,
) -> dict[str, int]:
    """Walk the provider's records in ``[start, end)`` in ``(event_time, id)`` order and (re)assign them."""
    from sqlalchemy import and_, or_

    from core.database import get_tenant_session
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import maintenance, rollups

    counts = {"scanned": 0, "changed": 0}
    if end <= start:
        return counts
    zone = clock.billing_zone(provider)
    for billing_day in _billing_days(provider, start, end):
        day_start, day_end = clock.day_bounds(billing_day, zone)
        low, high = max(day_start, start), min(day_end, end)
        if high <= low:
            continue
        last: tuple[datetime, uuid.UUID] | None = None
        while True:
            async with get_tenant_session(tenant_id) as session:
                await maintenance.job_timeouts(session)
                await locks.xact_lock(session, locks.commitment_recompute(tenant_id, provider))
                await maintenance.lock_days(session, tenant_id, maintenance.report_days(provider, billing_day))
                conditions = [
                    R.tenant_id == tenant_id,
                    R.provider == provider,
                    R.event_time >= low,
                    R.event_time < high,
                ]
                if last is not None:
                    conditions.append(or_(R.event_time > last[0], and_(R.event_time == last[0], R.id > last[1])))
                statement = select(R).where(*conditions).order_by(R.event_time, R.id).limit(RECOMPUTE_CHUNK)
                rows = list((await session.execute(statement)).scalars().all())
                if not rows:
                    break
                deltas: rollups.Deltas = {}
                rates: dict[tuple[str, date], Decimal | None] = {}
                for row in rows:
                    counts["scanned"] += 1
                    chosen = best_commitment(row, commitments)
                    assignment: tuple[uuid.UUID | None, bool, Decimal] = (None, False, Decimal("0"))
                    if chosen is not None:
                        target = str(chosen.currency or "").strip()
                        rate = None
                        if chosen.kind == "money" and target and target != vocab.REPORTING_CURRENCY:
                            rate = await _money_rate(session, tenant_id, rates, target, row.event_date)
                        assignment = draw(row, chosen, states[chosen.id], rate)
                    current = (row.commitment_id, bool(row.overage), Decimal(row.overage_quantity or 0))
                    if current == assignment:
                        continue
                    before = maintenance.record_dict(row)
                    row.commitment_id, row.overage, row.overage_quantity = assignment
                    row.revised_at = now
                    rollups.move(deltas, before, maintenance.record_dict(row))
                    counts["changed"] += 1
                await session.flush()
                await rollups.apply_deltas(session, tenant_id, deltas)
                last = (rows[-1].event_time, rows[-1].id)
            if len(rows) < RECOMPUTE_CHUNK:
                break
    return counts


async def _recompute_provider(tenant_id: uuid.UUID, provider: str, *, now: datetime) -> dict[str, Any]:
    from core.database import get_tenant_session

    cutoff = now - APPEND_GRACE
    async with get_tenant_session(tenant_id) as session:
        await locks.xact_lock(session, locks.commitment_recompute(tenant_id, provider))
        every = await _provider_commitments(session, tenant_id, provider)
    active = [c for c in every if c.status == "active"]
    if not every:
        return {"mode": "none", "scanned": 0, "changed": 0}
    zone = clock.billing_zone(provider)
    full = any(c.needs_full_recompute for c in every) or any(c.recomputed_through is None for c in active)
    if full:
        start = clock.day_bounds(min(c.period_start for c in every), zone)[0]
        end = min(cutoff, clock.day_bounds(max(c.period_end for c in every), zone)[0])
        states = {c.id: Drawn() for c in active}
    else:
        start = min(c.recomputed_through for c in active)
        end = cutoff
        states = {
            c.id: Drawn(Decimal(c.drawn_quantity or 0), Decimal(c.drawn_amount or 0), int(c.undrawn_records or 0))
            for c in active
        }
    through = max(start, end)
    counts = await _replay_window(tenant_id, provider, active, states, start=start, end=through, now=now)
    async with get_tenant_session(tenant_id) as session:
        await locks.xact_lock(session, locks.commitment_recompute(tenant_id, provider))
        for row in await _provider_commitments(session, tenant_id, provider):
            if row.id in states:
                state = states[row.id]
                row.drawn_quantity = state.quantity
                row.drawn_amount = state.amount
                row.undrawn_records = state.undrawn
                row.recomputed_through = through
                row.recomputed_at = now
            row.needs_full_recompute = False
        await session.flush()
    return {"mode": "full" if full else "append", **counts}


async def recompute(tenant_id: uuid.UUID, *, provider: str | None = None, now: datetime | None = None) -> dict:
    """Recompute drawdown and overage for each provider with commitments (or the given one).

    A full replay (after a commitment change, a late record, a restatement or
    a settlement) starts from the earliest period; otherwise only the window
    from the stored watermark to two hours ago is drawn, continuing the stored
    totals. Records are drawn in ``(event_time, id)`` order, so the result
    never depends on the order records arrived in.
    """
    from core.database import get_tenant_session
    from core.models.spend import SpendCommitment

    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        statement = select(SpendCommitment.provider).where(SpendCommitment.tenant_id == tenant_id)
        if provider:
            statement = statement.where(SpendCommitment.provider == vocab.norm_provider(provider))
        providers = sorted({str(row[0]) for row in (await session.execute(statement)).all()})
    out = {}
    for name in providers:
        out[name] = await _recompute_provider(tenant_id, name, now=stamp)
    logger.info("spend_commitments_recomputed", providers=len(out))
    return {"providers": out}


async def enqueue_recompute(tenant_id: uuid.UUID, provider: str, *, actor: str) -> str | None:
    """Queue the recompute a commitment change calls for (folded into an active one); the job id."""
    from core.spend import jobs

    out = await jobs.enqueue_followup(
        tenant_id, kind="recompute_commitments", params={"provider": provider}, actor=actor
    )
    return out["job_id"] if out else None
