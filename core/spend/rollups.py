# SPDX-License-Identifier: Apache-2.0
"""The daily rollup of usage records: incremental upkeep, rebuild, queries and coverage.

A rollup row sums the records of one reporting day (``event_date``) and one
combination of ``ROLLUP_DIMS``, keyed by ``dims_hash``. The writer adds the
contribution of every record it inserts in the same transaction, with one
sorted, additive multi-row upsert, under a shared lock on each affected
``(tenant, day)``; a maintenance job moves a revised record's contribution
(the old one out, the new one in) under the same locks and drops rows left
with no records. A rebuild takes one day's lock exclusively, deletes the
day's rows and re-sums the records as they are (it never re-prices or
re-resolves), so a rebuilt day equals the incrementally kept one and running
it twice changes nothing.

**Coverage** is the Gate 1 attribution measure: per day and for the period,
the INR amount and record count with and without an organisation node, the
share attributed to countable node kinds (a ``group`` node is reported, not
counted), the unattributed share by amount and by count, unpriced and
unconverted volume, FX conversions still pending, gaps by reason, and the
unpriced keys that matter most. Shares are exact in every verdict and shown
rounded so the display never looks better than the value: attributed shares
round toward zero, unattributed shares away from it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Context, Decimal, localcontext
from typing import Any

import structlog
from sqlalchemy import case, delete, func, select

from core.spend import vocab
from core.spend.access import ReadView
from core.spend.errors import SpendError

logger = structlog.get_logger()

ROLLUP_DIMS = (
    "billing_date",
    "org_node_id",
    "business_unit_node_id",
    "attribution_path",
    "unattributed_reason",
    "product_line",
    "use_case",
    "application",
    "agent_id",
    "provider",
    "model",
    "usage_type",
    "unit",
    "currency",
    "rate_card_id",
    "price_source",
    "commitment_id",
    "billing_account",
    "region",
    "environment",
    "risk_tier",
)
GROUP_BYS = (*ROLLUP_DIMS, "day")
SUM_FIELDS = ("quantity", "amount", "amount_inr", "unconverted_amount", "unpriced_quantity", "overage_quantity")
COUNT_FIELDS = (
    "record_count",
    "call_count",
    "unpriced_count",
    "unconverted_count",
    "fx_estimated_count",
    "overage_count",
    "allocated_count",
    "estimated_count",
)
UPSERT_CHUNK = 500
MAX_REBUILD_DAYS = 31
MAX_QUERY_DAYS = 366
TOP_UNPRICED = 20
_ZERO = Decimal("0")


@dataclass(frozen=True)
class Delta:
    quantity: Decimal
    amount: Decimal
    amount_inr: Decimal
    unconverted_amount: Decimal
    unpriced_quantity: Decimal
    overage_quantity: Decimal
    record_count: int
    call_count: int
    unpriced_count: int
    unconverted_count: int
    fx_estimated_count: int
    overage_count: int
    allocated_count: int
    estimated_count: int

    def negate(self) -> Delta:
        return Delta(**{f.name: -getattr(self, f.name) for f in fields(self)})

    def plus(self, other: Delta) -> Delta:
        return Delta(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})

    def is_zero(self) -> bool:
        return all(not getattr(self, f.name) for f in fields(self))


ZERO_DELTA = Delta(*(_ZERO,) * len(SUM_FIELDS), *(0,) * len(COUNT_FIELDS))


def _get(record: Any, name: str) -> Any:
    return record.get(name) if isinstance(record, Mapping) else getattr(record, name)


def _canon(value: Any) -> Any:
    """A dimension value as stored: text trimmed (``CHAR(3)`` currencies), everything else as is."""
    if isinstance(value, str):
        return value.strip()
    return value


def dims_of(record: Any) -> tuple[Any, ...]:
    """The record's values of ``ROLLUP_DIMS``."""
    return tuple(_canon(_get(record, name)) for name in ROLLUP_DIMS)


def dims_hash(dims: tuple[Any, ...]) -> str:
    """sha256 over the dimension values (``~`` for a missing value), hex."""
    return hashlib.sha256("|".join("~" if v is None else str(v) for v in dims).encode("utf-8")).hexdigest()


def contribution(record: Any) -> tuple[date, tuple[Any, ...], Delta]:
    """What one record adds to its day's rollup row."""
    quantity = Decimal(_get(record, "quantity") or 0)
    amount = _get(record, "amount")
    amount_inr = _get(record, "amount_inr")
    unpriced = bool(_get(record, "unpriced"))
    unconverted = bool(_get(record, "unconverted"))
    overage = bool(_get(record, "overage"))
    delta = Delta(
        quantity=quantity,
        amount=Decimal(amount) if amount is not None else _ZERO,
        amount_inr=Decimal(amount_inr) if amount_inr is not None else _ZERO,
        unconverted_amount=Decimal(amount) if unconverted and amount is not None else _ZERO,
        unpriced_quantity=quantity if unpriced else _ZERO,
        overage_quantity=Decimal(_get(record, "overage_quantity") or 0),
        record_count=1,
        call_count=int(_get(record, "calls") or 0),
        unpriced_count=int(unpriced),
        unconverted_count=int(unconverted),
        fx_estimated_count=int(bool(_get(record, "fx_estimated"))),
        overage_count=int(overage),
        allocated_count=int(bool(_get(record, "allocated"))),
        estimated_count=int(bool(_get(record, "quantity_estimated")) or bool(_get(record, "price_estimated"))),
    )
    return _get(record, "event_date"), dims_of(record), delta


Deltas = dict[tuple[date, str], tuple[tuple[Any, ...], Delta]]


def add_delta(deltas: Deltas, day: date, dims: tuple[Any, ...], delta: Delta) -> None:
    key = (day, dims_hash(dims))
    previous = deltas.get(key)
    deltas[key] = (dims, delta if previous is None else previous[1].plus(delta))


def aggregate(records: Iterable[Any]) -> Deltas:
    """The summed contributions of ``records`` by ``(day, dims_hash)``."""
    out: Deltas = {}
    for record in records:
        day, dims, delta = contribution(record)
        add_delta(out, day, dims, delta)
    return out


def move(deltas: Deltas, before: Any, after: Any) -> None:
    """Add to ``deltas`` the move of one revised record: its old contribution out, its new one in."""
    day, dims, delta = contribution(before)
    add_delta(deltas, day, dims, delta.negate())
    day, dims, delta = contribution(after)
    add_delta(deltas, day, dims, delta)


def _row_values(tenant_id: uuid.UUID, day: date, digest: str, dims: tuple[Any, ...], delta: Delta) -> dict[str, Any]:
    values: dict[str, Any] = {"id": uuid.uuid4(), "tenant_id": tenant_id, "day": day, "dims_hash": digest}
    values.update(dict(zip(ROLLUP_DIMS, dims, strict=True)))
    if values.get("use_case") is None:
        values["use_case"] = ""
    if values.get("model") is None:
        values["model"] = ""
    if values.get("environment") is None:
        values["environment"] = ""
    for name in (*SUM_FIELDS, *COUNT_FIELDS):
        values[name] = getattr(delta, name)
    return values


async def apply_deltas(session: Any, tenant_id: uuid.UUID, deltas: Mapping[tuple[date, str], Any]) -> None:
    """Add ``deltas`` with sorted additive upserts, then drop the touched days' rows left with no records."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.models.spend_usage import SpendUsageRollup

    live = [(key, value) for key, value in deltas.items() if not value[1].is_zero()]
    if not live:
        return
    table = SpendUsageRollup.__table__
    ordered = sorted(live, key=lambda item: (item[0][0], item[0][1]))
    for start in range(0, len(ordered), UPSERT_CHUNK):
        chunk = ordered[start : start + UPSERT_CHUNK]
        statement = pg_insert(table).values(
            [_row_values(tenant_id, day, digest, dims, delta) for (day, digest), (dims, delta) in chunk]
        )
        additive = {name: table.c[name] + statement.excluded[name] for name in (*SUM_FIELDS, *COUNT_FIELDS)}
        statement = statement.on_conflict_do_update(
            index_elements=["tenant_id", "day", "dims_hash"], set_={**additive, "updated_at": func.now()}
        )
        await session.execute(statement)
    days = sorted({day for (day, _digest), _value in live})
    await session.execute(
        delete(table).where(table.c.tenant_id == tenant_id, table.c.day.in_(days), table.c.record_count == 0)
    )


# ---------------------------------------------------------------- rebuild


def _aggregate_statement(tenant_id: uuid.UUID, day: date, start: datetime, end: datetime) -> Any:
    from core.models.spend_usage import SpendUsageRecord as R

    dims = [getattr(R, name) for name in ROLLUP_DIMS]
    return (
        select(
            *dims,
            func.sum(R.quantity),
            func.sum(func.coalesce(R.amount, 0)),
            func.sum(func.coalesce(R.amount_inr, 0)),
            func.sum(case((R.unconverted, R.amount), else_=0)),
            func.sum(case((R.unpriced, R.quantity), else_=0)),
            func.sum(R.overage_quantity),
            func.count(),
            func.sum(R.calls),
            func.sum(case((R.unpriced, 1), else_=0)),
            func.sum(case((R.unconverted, 1), else_=0)),
            func.sum(case((R.fx_estimated, 1), else_=0)),
            func.sum(case((R.overage, 1), else_=0)),
            func.sum(case((R.allocated, 1), else_=0)),
            func.sum(case((R.quantity_estimated | R.price_estimated, 1), else_=0)),
        )
        .where(R.tenant_id == tenant_id, R.event_date == day, R.event_time >= start, R.event_time < end)
        .group_by(*dims)
    )


def _delta_of(values: Sequence[Any]) -> Delta:
    sums = [Decimal(v or 0) for v in values[: len(SUM_FIELDS)]]
    counts = [int(v or 0) for v in values[len(SUM_FIELDS) :]]
    return Delta(*sums, *counts)


def day_window(day: date) -> tuple[datetime, datetime]:
    """The UTC instants of ``day`` in the reporting zone, widened by a day on each side.

    Every reader turns a reporting date into an ``event_time`` range so the
    partitions prune and the time index applies; the extra day each side means
    a changed zone setting cannot drop a record from its day.
    """
    from core.spend import clock

    start, end = clock.day_bounds(day, clock.reporting_zone())
    return start - timedelta(days=1), end + timedelta(days=1)


async def rebuild_day(tenant_id: uuid.UUID, day: date) -> dict[str, int]:
    """Re-sum one reporting day's records into its rollup rows, holding the day's lock exclusively."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.database import get_tenant_session
    from core.models.spend_usage import SpendUsageRollup
    from core.spend import locks

    table = SpendUsageRollup.__table__
    start, end = day_window(day)
    async with get_tenant_session(tenant_id) as session:
        await locks.set_lock_timeout(session, 30_000)
        await locks.xact_lock(session, locks.rollup_day(tenant_id, day))
        await session.execute(delete(table).where(table.c.tenant_id == tenant_id, table.c.day == day))
        rows = (await session.execute(_aggregate_statement(tenant_id, day, start, end))).all()
        values = []
        records = 0
        for row in rows:
            dims = tuple(_canon(v) for v in row[: len(ROLLUP_DIMS)])
            delta = _delta_of(row[len(ROLLUP_DIMS) :])
            records += delta.record_count
            values.append(_row_values(tenant_id, day, dims_hash(dims), dims, delta))
        values.sort(key=lambda v: v["dims_hash"])
        for index in range(0, len(values), UPSERT_CHUNK):
            await session.execute(pg_insert(table).values(values[index : index + UPSERT_CHUNK]))
    return {"rows": len(values), "records": records}


def check_range(start: date, end: date, *, max_days: int) -> None:
    """422 when ``end`` is before ``start`` or the inclusive range is longer than ``max_days``."""
    if end < start:
        raise SpendError(422, "invalid_period", "end is on or after start")
    if (end - start).days + 1 > max_days:
        raise SpendError(422, "range_too_long", f"a range is at most {max_days} days")


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def rebuild(tenant_id: uuid.UUID, *, start: date, end: date, actor: str, now: datetime) -> dict[str, Any]:
    """Rebuild every day of ``[start, end]`` (at most 31), oldest first, one transaction per day; audited."""
    from core.database import get_tenant_session
    from core.spend import audit

    check_range(start, end, max_days=MAX_REBUILD_DAYS)
    totals = {"days": 0, "rows": 0, "records": 0}
    for day in _days(start, end):
        result = await rebuild_day(tenant_id, day)
        totals["days"] += 1
        totals["rows"] += result["rows"]
        totals["records"] += result["records"]
    async with get_tenant_session(tenant_id) as session:
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=actor,
                action="rollups.rebuild",
                resource_type="spend_usage_rollup",
                resource_id=f"{start.isoformat()}..{end.isoformat()}",
                details={**totals, "start": start, "end": end},
                now=now,
            )
        )
    logger.info("spend_rollups_rebuilt", **totals)
    return totals


# ---------------------------------------------------------------- reads


def share_text(value: Decimal | None, *, rounding: str = ROUND_DOWN) -> str | None:
    """A share for display: 6 decimals, toward zero (attributed) or away from it (unattributed)."""
    if value is None:
        return None
    return format(value.quantize(vocab.SHARE_QUANT, rounding=rounding), "f")


def _ratio(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole == 0:
        return None
    with localcontext(Context(prec=38)):
        return part / whole


def _dec(value: Any) -> Decimal:
    return Decimal(value) if value is not None else _ZERO


ROLLUP_FILTERS = ("provider", "usage_type", "org_node_id", "application", "billing_account")
# Groupings that show contract terms (amount and quantity per card or per commitment): commercial reads.
COMMERCIAL_GROUP_BYS = ("rate_card_id", "commitment_id")


async def query(
    tenant_id: uuid.UUID,
    *,
    start: date,
    end: date,
    group_by: str,
    filters: Mapping[str, Any],
    view: ReadView,
) -> dict[str, Any]:
    """Rollups summed per ``group_by`` (a dimension or ``day``) over ``[start, end]`` (at most 366 days).

    Amounts are kept per currency (they are never added across currencies);
    INR is the one sum across all of them. Every grouping applies the caller's
    agent visibility (``agent_id`` is a rollup dimension): a reader who is not
    a tenant-wide reader (an administrator or auditor) sums only the rows of
    agents they may see and the rows with no agent, whatever they group by, as
    ``GET /spend/usage`` lists them.
    """
    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.spend_usage import SpendUsageRollup as U
    from core.spend import access

    check_range(start, end, max_days=MAX_QUERY_DAYS)
    if group_by not in GROUP_BYS:
        raise SpendError(422, "invalid_value", f"group_by is one of {', '.join(GROUP_BYS)}")
    if group_by in COMMERCIAL_GROUP_BYS and not view.commercial:
        raise SpendError(
            403, "commercial_read_refused", "grouping by rate card or commitment is for an administrator or auditor"
        )
    group = getattr(U, group_by)
    conditions = [U.tenant_id == tenant_id, U.day >= start, U.day <= end]
    for name in ROLLUP_FILTERS:
        value = filters.get(name)
        if value not in (None, ""):
            conditions.append(getattr(U, name) == value)
    if view.agent_clause is not None:
        # Every grouping, not only agent_id: a use-case, provider or node total would otherwise
        # carry the usage of personal or out-of-domain agents the record list hides.
        conditions.append(access.usage_filter(view, U.__table__, Agent.__table__, tenant_id=tenant_id))
    statement = (
        select(
            group,
            U.currency,
            func.sum(U.quantity),
            func.sum(U.amount),
            func.sum(U.amount_inr),
            func.sum(U.unconverted_amount),
            func.sum(U.unpriced_quantity),
            func.sum(U.record_count),
            func.sum(U.call_count),
            func.sum(U.unpriced_count),
            func.sum(U.unconverted_count),
        )
        .where(*conditions)
        .group_by(group, U.currency)
    )
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).all()
    grouped: dict[Any, dict[str, Any]] = {}
    totals = _empty_group()
    for row in rows:
        key = _canon(row[0])
        entry = grouped.setdefault(key, _empty_group())
        for target in (entry, totals):
            _fold(target, row)
    out_rows = [
        {group_by: _json_key(key), **_group_json(entry)}
        for key, entry in sorted(grouped.items(), key=lambda item: "" if item[0] is None else str(item[0]))
    ]
    return {"group_by": group_by, "rows": out_rows, "totals": _group_json(totals)}


def _empty_group() -> dict[str, Any]:
    return {
        "quantity": _ZERO,
        "amount_by_currency": {},
        "amount_inr": _ZERO,
        "unconverted_amount_by_currency": {},
        "unpriced_quantity": _ZERO,
        "records": 0,
        "calls": 0,
        "unpriced": 0,
        "unconverted": 0,
    }


def _fold(entry: dict[str, Any], row: Sequence[Any]) -> None:
    currency = _canon(row[1])
    entry["quantity"] += _dec(row[2])
    if currency:
        entry["amount_by_currency"][currency] = entry["amount_by_currency"].get(currency, _ZERO) + _dec(row[3])
        if _dec(row[5]):
            by_currency = entry["unconverted_amount_by_currency"]
            by_currency[currency] = by_currency.get(currency, _ZERO) + _dec(row[5])
    entry["amount_inr"] += _dec(row[4])
    entry["unpriced_quantity"] += _dec(row[6])
    entry["records"] += int(row[7] or 0)
    entry["calls"] += int(row[8] or 0)
    entry["unpriced"] += int(row[9] or 0)
    entry["unconverted"] += int(row[10] or 0)


def _group_json(entry: Mapping[str, Any]) -> dict[str, Any]:
    no_inr = entry["records"] > 0 and entry["unpriced"] + entry["unconverted"] >= entry["records"]
    return {
        "quantity": vocab.dec_str(entry["quantity"]),
        "amount_by_currency": {k: vocab.dec_str(v) for k, v in sorted(entry["amount_by_currency"].items())},
        "amount_inr": None if no_inr else vocab.dec_str(entry["amount_inr"]),
        "unconverted_amount_by_currency": {
            k: vocab.dec_str(v) for k, v in sorted(entry["unconverted_amount_by_currency"].items())
        },
        "unpriced_quantity": vocab.dec_str(entry["unpriced_quantity"]),
        "records": entry["records"],
        "calls": entry["calls"],
        "unpriced": entry["unpriced"],
        "unconverted": entry["unconverted"],
    }


def _json_key(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (uuid.UUID, date)):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    return value


# ---------------------------------------------------------------- coverage


@dataclass
class _Tally:
    records: int = 0
    calls: int = 0
    attributed_records: int = 0
    countable_records: int = 0
    amount_inr: Decimal = _ZERO
    attributed_amount_inr: Decimal = _ZERO
    countable_amount_inr: Decimal = _ZERO
    group_amount_inr: Decimal = _ZERO
    unpriced_count: int = 0
    unconverted_count: int = 0
    fx_estimated_count: int = 0
    fx_pending_count: int = 0
    unpriced_quantity: dict[str, Decimal] = field(default_factory=dict)
    unconverted_amount: dict[str, Decimal] = field(default_factory=dict)
    gaps: dict[str, int] = field(default_factory=dict)


def _tally_json(tally: _Tally) -> dict[str, Any]:
    no_inr = tally.records > 0 and tally.unpriced_count + tally.unconverted_count >= tally.records
    attributed = _ratio(tally.attributed_amount_inr, tally.amount_inr)
    countable = _ratio(tally.countable_amount_inr, tally.amount_inr)
    group = _ratio(tally.group_amount_inr, tally.amount_inr)
    count_share = _ratio(Decimal(tally.attributed_records), Decimal(tally.records))
    return {
        "records": tally.records,
        "calls": tally.calls,
        "attributed_records": tally.attributed_records,
        "countable_records": tally.countable_records,
        "amount_inr": None if no_inr else vocab.dec_str(tally.amount_inr),
        "attributed_amount_inr": vocab.dec_str(tally.attributed_amount_inr),
        "countable_amount_inr": vocab.dec_str(tally.countable_amount_inr),
        "attributed_share": share_text(countable),
        "group_share": share_text(group),
        "unattributed_share": share_text(None if attributed is None else 1 - attributed, rounding=ROUND_UP),
        "attributed_count_share": share_text(count_share),
        "unattributed_count_share": share_text(None if count_share is None else 1 - count_share, rounding=ROUND_UP),
        "unpriced_count": tally.unpriced_count,
        "unpriced_quantity": {k: vocab.dec_str(v) for k, v in sorted(tally.unpriced_quantity.items())},
        "unconverted_count": tally.unconverted_count,
        "unconverted_amount": {k: vocab.dec_str(v) for k, v in sorted(tally.unconverted_amount.items())},
        "fx_estimated_count": tally.fx_estimated_count,
        "fx_pending_count": tally.fx_pending_count,
        "gaps": dict(sorted(tally.gaps.items())),
    }


async def coverage(tenant_id: uuid.UUID, *, start: date, end: date, now: datetime) -> dict[str, Any]:
    """Attribution coverage per reporting day and for ``[start, end]`` (at most 366 days).

    A tenant-wide figure (the Gate 1 measure sums every agent's usage, and gaps carry no agent): the route
    answers it to tenant-wide readers only (``access.require_tenant_wide``).
    """
    from core.database import get_tenant_session
    from core.models.spend import SpendOrgNode as N
    from core.models.spend_usage import SpendMeterGap as G
    from core.models.spend_usage import SpendUsageRollup as U
    from core.spend import fx

    check_range(start, end, max_days=MAX_QUERY_DAYS)
    rollup_statement = (
        select(
            U.day,
            U.org_node_id,
            U.attribution_path,
            U.unattributed_reason,
            U.provider,
            U.model,
            U.usage_type,
            U.unit,
            U.currency,
            func.sum(U.record_count),
            func.sum(U.call_count),
            func.sum(U.amount_inr),
            func.sum(U.unpriced_count),
            func.sum(U.unpriced_quantity),
            func.sum(U.unconverted_count),
            func.sum(U.unconverted_amount),
            func.sum(U.fx_estimated_count),
        )
        .where(U.tenant_id == tenant_id, U.day >= start, U.day <= end)
        .group_by(
            U.day,
            U.org_node_id,
            U.attribution_path,
            U.unattributed_reason,
            U.provider,
            U.model,
            U.usage_type,
            U.unit,
            U.currency,
        )
    )
    gap_statement = (
        select(G.day, G.reason, func.sum(G.count))
        .where(G.tenant_id == tenant_id, G.day >= start, G.day <= end)
        .group_by(G.day, G.reason)
    )
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(rollup_statement)).all()
        node_ids = sorted({r[1] for r in rows if r[1] is not None}, key=str)
        kinds: dict[Any, str] = {}
        if node_ids:
            node_rows = (
                await session.execute(select(N.id, N.kind).where(N.tenant_id == tenant_id, N.id.in_(node_ids)))
            ).all()
            kinds = {r[0]: r[1] for r in node_rows}
        newest = await fx.latest_rate_dates(session, tenant_id)
        gap_rows = (await session.execute(gap_statement)).all()
    return summarise(rows, kinds, newest, gap_rows, start=start, end=end, now=now)


def summarise(
    rows: Sequence[Sequence[Any]],
    kinds: Mapping[Any, str],
    newest_rates: Mapping[str, date],
    gap_rows: Sequence[Sequence[Any]],
    *,
    start: date,
    end: date,
    now: datetime,
) -> dict[str, Any]:
    """The coverage report from rollup sums, node kinds, newest FX dates and gaps (pure)."""
    days: dict[date, _Tally] = {}
    period = _Tally()
    by_reason: dict[str, dict[str, Any]] = {}
    by_path: dict[tuple[str, str], dict[str, Any]] = {}
    unpriced_keys: dict[tuple[str, str, str, str], int] = {}
    for row in rows:
        day, node_id, path, reason, provider, model, usage_type, unit, currency = row[:9]
        records, calls, amount_inr, unpriced, unpriced_qty, unconverted, unconverted_amount, fx_estimated = row[9:]
        records, calls = int(records or 0), int(calls or 0)
        unpriced, unconverted, fx_estimated = int(unpriced or 0), int(unconverted or 0), int(fx_estimated or 0)
        amount = _dec(amount_inr)
        currency = _canon(currency)
        kind = kinds.get(node_id) if node_id is not None else None
        pending = 0
        if fx_estimated and currency:
            newest = newest_rates.get(currency)
            pending = fx_estimated if newest is None or day > newest else 0
        for tally in (days.setdefault(day, _Tally()), period):
            tally.records += records
            tally.calls += calls
            tally.amount_inr += amount
            tally.unpriced_count += unpriced
            tally.unconverted_count += unconverted
            tally.fx_estimated_count += fx_estimated
            tally.fx_pending_count += pending
            if node_id is not None:
                tally.attributed_records += records
                tally.attributed_amount_inr += amount
                if kind in vocab.GATE_NODE_KINDS:
                    tally.countable_records += records
                    tally.countable_amount_inr += amount
                elif kind == "group":
                    tally.group_amount_inr += amount
            if unpriced:
                tally.unpriced_quantity[unit] = tally.unpriced_quantity.get(unit, _ZERO) + _dec(unpriced_qty)
            if unconverted and currency:
                tally.unconverted_amount[currency] = tally.unconverted_amount.get(currency, _ZERO) + _dec(
                    unconverted_amount
                )
        if node_id is None:
            entry = by_reason.setdefault(reason or "unknown", {"records": 0, "amount_inr": _ZERO})
        else:
            entry = by_path.setdefault((path or "", kind or ""), {"records": 0, "amount_inr": _ZERO})
        entry["records"] += records
        entry["amount_inr"] += amount
        if unpriced:
            key = (provider, model or "", usage_type, unit)
            unpriced_keys[key] = unpriced_keys.get(key, 0) + unpriced
    for gap_day, gap_reason, count in gap_rows:
        for tally in (days.setdefault(gap_day, _Tally()), period):
            tally.gaps[gap_reason] = tally.gaps.get(gap_reason, 0) + int(count or 0)
    top = sorted(unpriced_keys.items(), key=lambda item: (-item[1], item[0]))[:TOP_UNPRICED]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "computed_at": now.isoformat(),
        "reporting_timezone": _zone_name(),
        "days": [{"day": day.isoformat(), **_tally_json(days[day])} for day in sorted(days)],
        "period": _tally_json(period),
        "by_reason": [
            {"reason": reason, "records": v["records"], "amount_inr": vocab.dec_str(v["amount_inr"])}
            for reason, v in sorted(by_reason.items())
        ],
        "by_path": [
            {
                "attribution_path": path,
                "node_kind": kind,
                "records": v["records"],
                "amount_inr": vocab.dec_str(v["amount_inr"]),
            }
            for (path, kind), v in sorted(by_path.items())
        ],
        "top_unpriced": [
            {"provider": k[0], "model": k[1], "usage_type": k[2], "unit": k[3], "unpriced_count": count}
            for k, count in top
        ],
    }


def _zone_name() -> str:
    from core.config import settings

    return str(settings.spend_reporting_timezone or "Asia/Kolkata")


# ---------------------------------------------------------------- usage records and gaps

MAX_USAGE_DAYS = 31
MAX_GAP_DAYS = 92
FLAG_FIELDS = (
    "unpriced",
    "fx_estimated",
    "unconverted",
    "overage",
    "allocated",
    "quantity_estimated",
    "price_estimated",
)


def parse_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    """``"<event_time iso>,<id>"`` as a keyset position; 422 ``invalid_value`` when malformed."""
    if not cursor:
        return None
    try:
        moment, _sep, ident = str(cursor).rpartition(",")
        when = datetime.fromisoformat(moment)
        return when, uuid.UUID(ident)
    except ValueError:
        raise SpendError(422, "invalid_value", "cursor is <event_time>,<id> from a previous page") from None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None


def record_json(row: Any, view: ReadView) -> dict[str, Any]:
    """A usage record for a reader: amounts as decimal text, flags as a list, the user id only when allowed.

    The contract terms on a record (its rate card, unit price, commitment and
    overage) are commercial reads: a reader the rate-card and commitment routes
    refuse (a machine credential, a domain role) gets them as ``null`` and no
    ``overage`` flag. Amounts stay: they are the spend being reported.
    """
    from core.spend import access

    commercial = view.commercial
    flags = [name for name in FLAG_FIELDS if getattr(row, name) and (commercial or name != "overage")]
    return {
        "id": str(row.id),
        "event_time": _iso(row.event_time),
        "event_date": _iso(row.event_date),
        "billing_date": _iso(row.billing_date),
        "usage_type": row.usage_type,
        "unit": row.unit,
        "quantity": vocab.dec_str(row.quantity),
        "provider": row.provider,
        "model": row.model or "",
        "rate_card_id": _text(row.rate_card_id) if commercial else None,
        "price_source": row.price_source,
        "unit_price": vocab.dec_str(row.unit_price) if commercial else None,
        "amount": vocab.dec_str(row.amount),
        "currency": _canon(row.currency),
        "fx_rate": vocab.dec_str(row.fx_rate),
        "fx_rate_date": _iso(row.fx_rate_date),
        "amount_inr": vocab.dec_str(row.amount_inr),
        "flags": flags,
        "commitment_id": _text(row.commitment_id) if commercial else None,
        "overage_quantity": vocab.dec_str(row.overage_quantity) if commercial else None,
        "agent_id": _text(row.agent_id),
        "agent_version": row.agent_version,
        "org_node_id": _text(row.org_node_id),
        "business_unit_node_id": _text(row.business_unit_node_id),
        "attribution_path": row.attribution_path,
        "unattributed_reason": row.unattributed_reason,
        "product_line": row.product_line,
        "use_case": row.use_case,
        "application": row.application,
        "region": row.region,
        "workflow_id": _text(row.workflow_id),
        "run_id": row.run_id,
        "initiating_user_id": access.redact_user(view, row.initiating_user_id),
        "environment": row.environment,
        "risk_tier": row.risk_tier,
        "billing_account": row.billing_account,
        "correlation_ref": _canon(row.correlation_ref),
        "revised_at": _iso(row.revised_at),
    }


async def list_records(
    tenant_id: uuid.UUID,
    *,
    start: date,
    end: date,
    view: ReadView,
    usage_type: str | None = None,
    provider: str | None = None,
    org_node_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    unattributed: bool | None = None,
    unpriced: bool | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Usage records of reporting days ``[start, end]`` (at most 31) in ``(event_time, id)`` order, a page at a
    time; records of agents the caller may not see are left out."""
    from sqlalchemy import and_, or_

    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import access, clock

    check_range(start, end, max_days=MAX_USAGE_DAYS)
    zone = clock.reporting_zone()
    position = parse_cursor(cursor)
    conditions = [
        R.tenant_id == tenant_id,
        R.event_time >= clock.day_bounds(start, zone)[0],
        R.event_time < clock.day_bounds(end, zone)[1],
        access.usage_filter(view, R.__table__, Agent.__table__, tenant_id=tenant_id),
    ]
    if usage_type:
        conditions.append(R.usage_type == vocab.choice(usage_type, vocab.USAGE_TYPES, field="usage_type"))
    if provider:
        conditions.append(R.provider == vocab.norm_provider(provider))
    if org_node_id is not None:
        conditions.append(R.org_node_id == org_node_id)
    if agent_id is not None:
        conditions.append(R.agent_id == agent_id)
    if unattributed is not None:
        conditions.append(R.org_node_id.is_(None) if unattributed else R.org_node_id.is_not(None))
    if unpriced is not None:
        conditions.append(R.unpriced.is_(bool(unpriced)))
    if position is not None:
        conditions.append(or_(R.event_time > position[0], and_(R.event_time == position[0], R.id > position[1])))
    size = max(1, min(int(limit), 500))
    statement = select(R).where(*conditions).order_by(R.event_time, R.id).limit(size + 1)
    async with get_tenant_session(tenant_id) as session:
        rows = list((await session.execute(statement)).scalars().all())
    page = rows[:size]
    following = None
    if len(rows) > size and page:
        following = f"{page[-1].event_time.isoformat()},{page[-1].id}"
    return {"items": [record_json(row, view) for row in page], "next_cursor": following}


async def list_gaps(tenant_id: uuid.UUID, *, start: date, end: date) -> dict[str, Any]:
    """Meter gaps of ``[start, end]`` (at most 92 days) by day, usage type, reason and detail.

    Gaps carry no agent: the route answers them to tenant-wide readers only (``access.require_tenant_wide``).
    """
    from core.database import get_tenant_session
    from core.models.spend_usage import SpendMeterGap as G

    check_range(start, end, max_days=MAX_GAP_DAYS)
    statement = (
        select(G)
        .where(G.tenant_id == tenant_id, G.day >= start, G.day <= end)
        .order_by(G.day, G.usage_type, G.reason, G.detail)
    )
    async with get_tenant_session(tenant_id) as session:
        rows = list((await session.execute(statement)).scalars().all())
    return {
        "items": [
            {
                "day": row.day.isoformat(),
                "usage_type": row.usage_type,
                "reason": row.reason,
                "detail": row.detail or "",
                "count": int(row.count or 0),
            }
            for row in rows
        ]
    }
