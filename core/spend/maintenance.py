# SPDX-License-Identifier: Apache-2.0
"""The audited jobs that revise a usage record's derived fields: FX settlement, restatement, re-attribution.

A usage record is never deleted and its event fields (time, quantity, unit,
provider, model, the call's identifiers) never change. Derived fields
change only here and in the commitment recompute (``core/spend/commitments.py``),
each job writing ``revised_at`` and moving the record's rollup contribution
(the old one out, the new one in) in the same transaction, under the shared
lock of each affected reporting day, 1000 records per transaction.

* **FX settlement** converts records that used an earlier rate (or none)
  once their date's rate, or a closer one, exists; a corrected rate that
  settled records used is re-applied when forced.
* **Restatement** re-prices records with the active cards as known now,
  after a correction, a backdated card or an administrator's request. A
  record priced by the deployment's fallback list that still has no card
  keeps its stored price: the fallback tables carry no dates.
* **Re-attribution** resolves records still unattributed again (a mapping
  or node added after the usage) and changes only those that now resolve.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import and_, or_, select, text

from core.spend import audit, clock, locks, vocab
from core.spend.errors import SpendError

logger = structlog.get_logger()

CHUNK = 1000
MAX_DAYS = 92
FALLBACK_SOURCES = ("fallback_list", "fallback_override")
_STATEMENT_TIMEOUT = text("SELECT set_config('statement_timeout', :v, true)")
FX_FIELDS = ("fx_rate", "fx_rate_date", "amount_inr", "fx_estimated", "unconverted")
PRICE_FIELDS = (
    "rate_card_id",
    "blend_card_id",
    "price_source",
    "unit_price",
    "amount",
    "currency",
    "price_estimated",
    "unpriced",
)


def record_dict(row: Any) -> dict[str, Any]:
    """A snapshot of a usage record's columns (for its rollup contribution)."""
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


async def job_timeouts(session: Any) -> None:
    """A job waits up to 30 s for a lock and 120 s for a statement (``SET LOCAL``)."""
    await locks.set_lock_timeout(session, 30_000)
    await session.execute(_STATEMENT_TIMEOUT, {"v": "120s"})


async def lock_days(session: Any, tenant_id: uuid.UUID, days: Sequence[date]) -> None:
    """Shared rollup-day locks, ascending."""
    for day in sorted(set(days)):
        await locks.xact_lock_shared(session, locks.rollup_day(tenant_id, day))


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _check(start: date, end: date) -> None:
    from core.spend.rollups import check_range

    check_range(start, end, max_days=MAX_DAYS)


def _after(record_cls: Any, last: tuple[datetime, uuid.UUID] | None) -> Any:
    if last is None:
        return None
    return or_(record_cls.event_time > last[0], and_(record_cls.event_time == last[0], record_cls.id > last[1]))


async def _chunk(session: Any, conditions: list[Any], last: tuple[datetime, uuid.UUID] | None) -> list[Any]:
    from core.models.spend_usage import SpendUsageRecord as R

    keyset = _after(R, last)
    where = [*conditions, keyset] if keyset is not None else conditions
    statement = select(R).where(*where).order_by(R.event_time, R.id).limit(CHUNK)
    return list((await session.execute(statement)).scalars().all())


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()  # CHAR(3) currencies
    return bool(a == b)


def _changed(row: Any, values: dict[str, Any]) -> bool:
    return any(not _same(getattr(row, name), value) for name, value in values.items())


async def _mark_commitments(tenant_id: uuid.UUID, providers: set[str], *, kind: str = "", session: Any = None) -> None:
    from core.database import get_tenant_session
    from core.spend.meter import mark_commitments_for_replay

    if not providers:
        return
    if session is not None:
        await mark_commitments_for_replay(session, tenant_id, sorted(providers), kind=kind)
        return
    async with get_tenant_session(tenant_id) as own:
        await mark_commitments_for_replay(own, tenant_id, sorted(providers), kind=kind)


def _add(totals: dict[str, dict[str, Any]], key: str, *, before: Decimal | None, after: Decimal | None) -> None:
    entry = totals.setdefault(key, {"records": 0, "before": Decimal("0"), "after": Decimal("0")})
    entry["records"] += 1
    entry["before"] += before or Decimal("0")
    entry["after"] += after or Decimal("0")


# ---------------------------------------------------------------- FX settlement


async def settle_fx(
    tenant_id: uuid.UUID,
    *,
    start: date,
    end: date,
    force_dates: Sequence[tuple[str, date]] = (),
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Convert pending records of each reporting day in ``[start, end]`` with the rate of their day as known now."""
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import fx, pricing, rollups

    _check(start, end)
    stamp = now or clock.now_utc()
    forced = sorted({(vocab.norm_currency(c), d) for c, d in force_dates})
    per_currency: dict[str, dict[str, Any]] = {}
    providers: set[str] = set()
    scanned = changed = 0
    for day in _days(start, end):
        window_start, window_end = rollups.day_window(day)
        pending = or_(R.fx_estimated, R.unconverted)  # the predicate of ix_spend_usage_records_fx_pending
        if forced:
            pending = or_(pending, *(and_(R.currency == c, R.fx_rate_date == d) for c, d in forced))
        conditions = [
            R.tenant_id == tenant_id,
            R.event_date == day,
            R.event_time >= window_start,
            R.event_time < window_end,
            pending,
        ]
        last: tuple[datetime, uuid.UUID] | None = None
        while True:
            async with get_tenant_session(tenant_id) as session:
                await job_timeouts(session)
                await lock_days(session, tenant_id, [day])
                rows = await _chunk(session, conditions, last)
                if not rows:
                    break
                rates: dict[str, Any] = {}
                deltas: rollups.Deltas = {}
                for row in rows:
                    scanned += 1
                    if row.amount is None or row.currency is None:
                        continue
                    currency = str(row.currency).strip()
                    if currency not in rates:
                        rates[currency] = await fx.rate_on(session, tenant_id, currency, day)
                    inr, rate, rate_date, estimated, unconverted = pricing.convert(
                        Decimal(row.amount), currency, day, rates[currency]
                    )
                    values = {
                        "amount_inr": inr,
                        "fx_rate": rate,
                        "fx_rate_date": rate_date,
                        "fx_estimated": estimated,
                        "unconverted": unconverted,
                    }
                    if not _changed(row, values):
                        continue
                    before = record_dict(row)
                    for name, value in values.items():
                        setattr(row, name, value)
                    row.revised_at = stamp
                    rollups.move(deltas, before, record_dict(row))
                    _add(per_currency, currency, before=before["amount_inr"], after=inr)
                    providers.add(row.provider)
                    changed += 1
                await session.flush()
                await rollups.apply_deltas(session, tenant_id, deltas)
                last = (rows[-1].event_time, rows[-1].id)
            if len(rows) < CHUNK:
                break
    result = {"days": len(_days(start, end)), "scanned": scanned, "changed": changed}
    async with get_tenant_session(tenant_id) as session:
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=actor,
                action="fx.settle",
                resource_type="spend_usage_record",
                resource_id=f"{start.isoformat()}..{end.isoformat()}",
                details={**result, "force_dates": forced, "by_currency": per_currency},
                now=stamp,
            )
        )
        await _mark_commitments(tenant_id, providers, kind="money", session=session)
    logger.info("spend_fx_settled", **result)
    return result


# ---------------------------------------------------------------- restatement


def report_days(provider: str, billing_day: date) -> list[date]:
    start, end = clock.day_bounds(billing_day, clock.billing_zone(provider))
    first = clock.event_date_of(start)
    last = clock.event_date_of(end - timedelta(microseconds=1))
    return _days(first, last)


async def restate(
    tenant_id: uuid.UUID,
    *,
    provider: str,
    start: date,
    end: date,
    card_ids: Sequence[uuid.UUID] = (),
    include_unpriced: bool = True,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-price a provider's records of billing days ``[start, end]`` (at most 92) with the cards as known now."""
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import pricing, rollups

    _check(start, end)
    name = vocab.norm_provider(provider)
    why = vocab.free_text(reason, field="reason", max_len=500)
    stamp = now or clock.now_utc()
    cards = sorted({uuid.UUID(str(c)) for c in card_ids}, key=str)
    selectors: list[Any] = []
    if cards:
        selectors += [R.rate_card_id.in_(cards), R.blend_card_id.in_(cards)]
    if include_unpriced:
        selectors.append(R.price_source.in_(("none", *FALLBACK_SOURCES)))
    zone = clock.billing_zone(name)
    sums: dict[str, dict[str, Any]] = {}
    scanned = changed = 0
    for billing_day in _days(start, end):
        day_start, day_end = clock.day_bounds(billing_day, zone)
        conditions = [
            R.tenant_id == tenant_id,
            R.provider == name,
            R.event_time >= day_start,
            R.event_time < day_end,
        ]
        if selectors:
            conditions.append(or_(*selectors))
        last: tuple[datetime, uuid.UUID] | None = None
        while True:
            async with get_tenant_session(tenant_id) as session:
                await job_timeouts(session)
                await lock_days(session, tenant_id, report_days(name, billing_day))
                rows = await _chunk(session, conditions, last)
                if not rows:
                    break
                priced = await pricing.price_many(
                    session,
                    tenant_id,
                    [
                        pricing.Usage(
                            provider=row.provider,
                            usage_type=row.usage_type,
                            unit=row.unit,
                            quantity=Decimal(row.quantity),
                            model=row.model or "",
                            on=row.billing_date,
                            fx_on=row.event_date,
                        )
                        for row in rows
                    ],
                )
                deltas: rollups.Deltas = {}
                for row, new in zip(rows, priced, strict=True):
                    scanned += 1
                    if new.rate_card_id is None and row.price_source in FALLBACK_SOURCES:
                        continue  # the stored fallback price is the price of its day
                    values = {name_: getattr(new, name_) for name_ in PRICE_FIELDS}
                    values.update({name_: getattr(new, name_) for name_ in FX_FIELDS})
                    if not _changed(row, values):
                        continue
                    before = record_dict(row)
                    for field_name, value in values.items():
                        setattr(row, field_name, value)
                    row.revised_at = stamp
                    rollups.move(deltas, before, record_dict(row))
                    old_key = f"{row.billing_date.isoformat()}:{before['rate_card_id'] or 'none'}"
                    new_key = f"{row.billing_date.isoformat()}:{new.rate_card_id or 'none'}"
                    _add(sums, old_key, before=before["amount"], after=None)
                    _add(sums, new_key, before=None, after=new.amount)
                    changed += 1
                await session.flush()
                await rollups.apply_deltas(session, tenant_id, deltas)
                last = (rows[-1].event_time, rows[-1].id)
            if len(rows) < CHUNK:
                break
    result = {"provider": name, "days": len(_days(start, end)), "scanned": scanned, "changed": changed}
    async with get_tenant_session(tenant_id) as session:
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=actor,
                action="usage.restate",
                resource_type="spend_usage_record",
                resource_id=f"{name}:{start.isoformat()}..{end.isoformat()}",
                details={
                    **result,
                    "reason": why,
                    "card_ids": [str(c) for c in cards],
                    "include_unpriced": include_unpriced,
                    "amounts": sums,
                },
                now=stamp,
            )
        )
        await _mark_commitments(tenant_id, {name}, session=session)
    logger.info("spend_usage_restated", scanned=scanned, changed=changed)
    return result


# ---------------------------------------------------------------- re-attribution


def hints_of(row: Any) -> Any:
    """The attribution hints a stored record carries (the event fields; ``origin="reattribute"``)."""
    from core.spend.resolver import Hints

    return Hints(
        agent_id=str(row.agent_id) if row.agent_id else None,
        agent_version=row.agent_version,
        application=row.application,
        default_use_case=row.use_case or "",
        workflow_id=str(row.workflow_id) if row.workflow_id else None,
        workflow_run_id=None,
        run_id=row.run_id,
        initiating_user_id=str(row.initiating_user_id) if row.initiating_user_id else None,
        origin="reattribute",
    )


async def reattribute(
    tenant_id: uuid.UUID, *, start: date, end: date, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Resolve still-unattributed records of reporting days ``[start, end]`` (at most 92) again."""
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import resolver, rollups

    _check(start, end)
    stamp = now or clock.now_utc()
    resolver.invalidate(tenant_id)
    before_reasons: dict[str, int] = {}
    after_reasons: dict[str, int] = {}
    scanned = changed = 0
    for day in _days(start, end):
        window_start, window_end = rollups.day_window(day)
        conditions = [
            R.tenant_id == tenant_id,
            R.org_node_id.is_(None),
            R.event_date == day,
            R.event_time >= window_start,
            R.event_time < window_end,
        ]
        last: tuple[datetime, uuid.UUID] | None = None
        while True:
            async with get_tenant_session(tenant_id) as session:
                await job_timeouts(session)
                await lock_days(session, tenant_id, [day])
                rows = await _chunk(session, conditions, last)
                if not rows:
                    break
                deltas: rollups.Deltas = {}
                resolved_by_hints: dict[Any, Any] = {}
                for row in rows:
                    scanned += 1
                    old_reason = row.unattributed_reason or "unknown"
                    before_reasons[old_reason] = before_reasons.get(old_reason, 0) + 1
                    hints = hints_of(row)
                    if hints not in resolved_by_hints:
                        resolved_by_hints[hints] = await resolver.resolve(session, tenant_id, hints)
                    found = resolved_by_hints[hints]
                    if found.org_node_id is None:
                        after_reasons[old_reason] = after_reasons.get(old_reason, 0) + 1
                        continue
                    before = record_dict(row)
                    row.org_node_id = found.org_node_id
                    row.business_unit_node_id = found.business_unit_node_id
                    row.attribution_path = found.attribution_path
                    row.unattributed_reason = None
                    if row.product_line is None:
                        row.product_line = found.product_line
                    if (row.use_case or "") in ("", "unattributed"):
                        row.use_case = found.use_case
                    row.revised_at = stamp
                    rollups.move(deltas, before, record_dict(row))
                    after_reasons["attributed"] = after_reasons.get("attributed", 0) + 1
                    changed += 1
                await session.flush()
                await rollups.apply_deltas(session, tenant_id, deltas)
                last = (rows[-1].event_time, rows[-1].id)
            if len(rows) < CHUNK:
                break
    result = {"days": len(_days(start, end)), "scanned": scanned, "changed": changed}
    async with get_tenant_session(tenant_id) as session:
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=actor,
                action="usage.reattribute",
                resource_type="spend_usage_record",
                resource_id=f"{start.isoformat()}..{end.isoformat()}",
                details={**result, "before": before_reasons, "after": after_reasons},
                now=stamp,
            )
        )
    logger.info("spend_usage_reattributed", **result)
    return result


def today(now: datetime | None = None) -> date:
    """Today in the reporting zone."""
    return clock.today_in(clock.reporting_zone(), now or clock.now_utc())


def check_reason(reason: Any) -> str:
    """A restatement's reason: 10 to 500 characters of plain text."""
    text_value = str(reason or "").strip()
    if not 10 <= len(text_value) <= 500:
        raise SpendError(422, "reason_required", "a restatement gives its reason in 10 to 500 characters")
    return vocab.free_text(text_value, field="reason", max_len=500, required=True)
