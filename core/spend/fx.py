# SPDX-License-Identifier: Apache-2.0
"""FX rates: the reference rate of a currency to INR per reporting date.

A rate is kept per ``(currency, rate_date)``; ``put_rate`` and the import
upsert by that key under the currency's advisory lock (so two writers of a
new rate never both insert it) and audit the before and after rate and
source. INR is
the reporting currency and has no row. ``rate_on`` answers the rate of the
date, else the latest earlier one; the pricing engine marks the second
``fx_estimated`` (``core/spend/pricing.py``).

Every new or changed rate queues an FX settlement for the days it governs
(from its date to the day before the next rate, or 31 days without one), so
records that used an earlier rate, or none, are converted with it. Changing
the value of a rate that settled records already use (``fx_in_use``) needs
``restate``; the settlement then re-converts those records too.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select

from core.spend import audit, clock, imports, locks, vocab
from core.spend.errors import SpendError, require_actor
from core.spend.pricing import FxRate

logger = structlog.get_logger()

IMPORT_REQUIRED = ("rate_date", "currency", "rate_to_inr")
IMPORT_OPTIONAL = ("source",)


def _currency(value: Any) -> str:
    currency = vocab.norm_currency(value)
    if currency == vocab.REPORTING_CURRENCY:
        raise SpendError(422, "invalid_currency", "INR is the reporting currency and has no rate to itself")
    return currency


def check_rate(body: dict[str, Any], *, default_source: str = "manual") -> dict[str, Any]:
    """A rate's fields, checked: ``rate_date``, ``currency``, ``rate_to_inr`` and ``source``."""
    source = body.get("source")
    return {
        "rate_date": vocab.parse_date(body.get("rate_date"), field="rate_date"),
        "currency": _currency(body.get("currency")),
        "rate_to_inr": vocab.parse_decimal(
            body.get("rate_to_inr"),
            field="rate_to_inr",
            minimum=0,
            strict_minimum=True,
            maximum=vocab.MAX_FX_RATE,
            places=vocab.FX_PLACES,
        ),
        "source": vocab.choice(
            source if source not in (None, "") else default_source, vocab.FX_SOURCES, field="source"
        ),
    }


def _rate_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "currency": str(row.currency).strip(),
        "rate_date": row.rate_date.isoformat(),
        "rate_to_inr": vocab.dec_str(row.rate_to_inr),
        "source": row.source,
        "updated_by": row.updated_by or "",
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


def _audit_fields(row: Any) -> dict[str, Any]:
    return {"rate_to_inr": row.rate_to_inr, "source": row.source}


async def _row(session: Any, tenant_id: uuid.UUID, currency: str, rate_date: date, *, lock: bool = False) -> Any:
    from core.models.spend import SpendFxRate

    statement = select(SpendFxRate).where(
        SpendFxRate.tenant_id == tenant_id, SpendFxRate.currency == currency, SpendFxRate.rate_date == rate_date
    )
    if lock:
        statement = statement.with_for_update()
    rows = (await session.execute(statement)).scalars().all()
    return rows[0] if rows else None


async def _lock_currencies(session: Any, tenant_id: uuid.UUID, currencies: set[str]) -> None:
    """Take the currencies' advisory locks, in sorted order, before any rate of them is read or written.

    ``SELECT ... FOR UPDATE`` locks nothing while the ``(currency, rate_date)``
    row does not exist yet, so without this two writers of a new rate would
    both insert it and one would fail on the unique index.
    """
    for currency in sorted(currencies):
        await locks.xact_lock(session, locks.fx_rate(tenant_id, currency))


async def _upsert(
    session: Any, tenant_id: uuid.UUID, fields: dict[str, Any], *, who: str, now: datetime
) -> tuple[str, Any, audit.Change | None, Any]:
    """Write one rate; ``(outcome, row, change, previous_rate)`` with outcome created, updated or unchanged.

    The caller holds the currency's lock (``_lock_currencies``).
    """
    from core.models.spend import SpendFxRate

    row = await _row(session, tenant_id, fields["currency"], fields["rate_date"], lock=True)
    key = f"{fields['currency']}:{fields['rate_date'].isoformat()}"
    if row is None:
        row = SpendFxRate(
            id=uuid.uuid4(), tenant_id=tenant_id, updated_by=who, created_at=now, updated_at=now, **fields
        )
        session.add(row)
        await session.flush()
        return "created", row, audit.Change(key, None, _audit_fields(row)), None
    previous = row.rate_to_inr
    if row.rate_to_inr == fields["rate_to_inr"] and row.source == fields["source"]:
        return "unchanged", row, None, previous
    before = _audit_fields(row)
    row.rate_to_inr = fields["rate_to_inr"]
    row.source = fields["source"]
    row.updated_by = who
    row.updated_at = now
    await session.flush()
    return "updated", row, audit.Change(key, before, _audit_fields(row)), previous


SETTLE_DAYS_WITHOUT_NEXT = 31
SETTLE_MAX_DAYS = 92


async def fx_in_use(session: Any, tenant_id: uuid.UUID, currency: str, rate_date: date) -> bool:
    """Whether settled usage records (converted at this exact date's rate) use the rate."""
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend.rollups import day_window

    start, end = day_window(rate_date)
    rows = (
        await session.execute(
            select(R.id)
            .where(
                R.tenant_id == tenant_id,
                R.currency == currency,
                R.fx_rate_date == rate_date,
                R.fx_estimated.is_(False),
                R.event_time >= start,
                R.event_time < end,
            )
            .limit(1)
        )
    ).all()
    return bool(rows)


async def settle_window(session: Any, tenant_id: uuid.UUID, currency: str, rate_date: date) -> tuple[date, date]:
    """The reporting days a rate governs: its date to the day before the next rate (31 days without one)."""
    from core.models.spend import SpendFxRate

    following = (
        await session.execute(
            select(func.min(SpendFxRate.rate_date)).where(
                SpendFxRate.tenant_id == tenant_id,
                SpendFxRate.currency == currency,
                SpendFxRate.rate_date > rate_date,
            )
        )
    ).scalar()
    if following is None:
        return rate_date, rate_date + timedelta(days=SETTLE_DAYS_WITHOUT_NEXT)
    return rate_date, max(rate_date, following - timedelta(days=1))


async def _settle_job(
    tenant_id: uuid.UUID, window: tuple[date, date] | None, forced: list[tuple[str, date]], *, actor: str
) -> str | None:
    """Queue the settlement a committed rate change calls for; the job id (or the active one it joins)."""
    if window is None:
        return None
    from core.spend import jobs

    out = await jobs.enqueue_followup(
        tenant_id,
        kind="settle_fx",
        params={
            "start": window[0].isoformat(),
            "end": window[1].isoformat(),
            "force_dates": [[c, d.isoformat()] for c, d in forced],
        },
        actor=actor,
    )
    return out["job_id"] if out else None


async def put_rate(
    tenant_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Upsert the rate of ``(currency, rate_date)``; answers the row with the rate it replaced.

    A new or changed rate queues an FX settlement for the days it governs.
    Changing a rate settled records use needs ``restate`` (409 ``fx_in_use``
    otherwise); its settlement then re-converts them.
    """
    from core.database import get_tenant_session

    who = require_actor(actor)
    fields = check_rate(body)
    restate = bool(body.get("restate"))
    stamp = now or clock.now_utc()
    window: tuple[date, date] | None = None
    forced: list[tuple[str, date]] = []
    async with get_tenant_session(tenant_id) as session:
        await _lock_currencies(session, tenant_id, {fields["currency"]})
        existing = await _row(session, tenant_id, fields["currency"], fields["rate_date"])
        if (
            existing is not None
            and existing.rate_to_inr != fields["rate_to_inr"]
            and await fx_in_use(session, tenant_id, fields["currency"], fields["rate_date"])
        ):
            if not restate:
                raise SpendError(
                    409,
                    "fx_in_use",
                    "settled usage records use this rate; send restate=true to change it and re-convert them",
                )
            forced = [(fields["currency"], fields["rate_date"])]
        outcome, row, change, previous = await _upsert(session, tenant_id, fields, who=who, now=stamp)
        if outcome != "unchanged":
            window = await settle_window(session, tenant_id, fields["currency"], fields["rate_date"])
        if change is not None:
            session.add(
                audit.audit_change(
                    tenant_id,
                    actor_id=who,
                    action=f"fx_rates.{'create' if outcome == 'created' else 'update'}",
                    resource_type="spend_fx_rate",
                    resource_id=str(row.id),
                    changes=[change],
                    now=stamp,
                )
            )
        out = _rate_dict(row)
    logger.info("spend_fx_rate_kept", outcome=outcome, currency=fields["currency"])
    job = await _settle_job(tenant_id, window, forced, actor=who)
    return {**out, "previous_rate": vocab.dec_str(previous), "outcome": outcome, "settle_job_id": job}


async def import_rates(
    tenant_id: uuid.UUID,
    rows: list[dict[str, str]],
    *,
    actor: str,
    dry_run: bool,
    file_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Upsert every valid row by ``(currency, rate_date)``; a dry run reports the same and writes nothing."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    report = imports.new_report(dry_run=dry_run, received=len(rows))
    checked: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[str, date]] = set()
    for index, raw in enumerate(rows, start=2):
        key = f"{raw.get('currency', '')}:{raw.get('rate_date', '')}"
        try:
            fields = check_rate(raw, default_source="import")
        except SpendError as exc:
            imports.reject(report, row=index, key=key, reason=exc.code)
            continue
        if (fields["currency"], fields["rate_date"]) in seen:
            imports.reject(report, row=index, key=key, reason="duplicate_in_file")
            continue
        seen.add((fields["currency"], fields["rate_date"]))
        checked.append((index, fields))
    changes: list[audit.Change] = []
    windows: list[tuple[date, date]] = []
    async with get_tenant_session(tenant_id) as session:
        await _lock_currencies(session, tenant_id, {fields["currency"] for _index, fields in checked})
        outer = await session.begin_nested() if dry_run else None
        for index, fields in checked:
            existing = await _row(session, tenant_id, fields["currency"], fields["rate_date"])
            if (
                existing is not None
                and existing.rate_to_inr != fields["rate_to_inr"]
                and await fx_in_use(session, tenant_id, fields["currency"], fields["rate_date"])
            ):
                key = f"{fields['currency']}:{fields['rate_date'].isoformat()}"
                imports.reject(report, row=index, key=key, reason="fx_in_use")
                continue
            outcome, _row_obj, change, _previous = await _upsert(session, tenant_id, fields, who=who, now=stamp)
            report[outcome] += 1
            if change is not None:
                changes.append(change)
                windows.append(await settle_window(session, tenant_id, fields["currency"], fields["rate_date"]))
        report["rejected"].sort(key=lambda item: item["row"])
        if outer is not None:
            await outer.rollback()
        elif changes:
            for entry in audit.audit_changes(
                tenant_id,
                actor_id=who,
                action="fx_rates.import",
                resource_type="spend_fx_rate",
                changes=changes,
                summary=_summary(report),
                file_sha256=file_sha256,
                now=stamp,
            ):
                session.add(entry)
    if windows and not dry_run:
        start = min(w[0] for w in windows)
        end = min(max(w[1] for w in windows), start + timedelta(days=SETTLE_MAX_DAYS - 1))
        report["settle_job_id"] = await _settle_job(tenant_id, (start, end), [], actor=who)
    return report


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in ("received", "created", "updated", "unchanged")} | {
        "rejected": len(report["rejected"])
    }


async def list_rates(
    tenant_id: uuid.UUID,
    *,
    currency: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Rates newest first, by currency and date range."""
    from core.database import get_tenant_session
    from core.models.spend import SpendFxRate

    conditions = [SpendFxRate.tenant_id == tenant_id]
    if currency:
        conditions.append(SpendFxRate.currency == vocab.norm_currency(currency))
    if start is not None:
        conditions.append(SpendFxRate.rate_date >= start)
    if end is not None:
        conditions.append(SpendFxRate.rate_date <= end)
    async with get_tenant_session(tenant_id) as session:
        total = (await session.execute(select(func.count()).select_from(SpendFxRate).where(*conditions))).scalar()
        rows = (
            (
                await session.execute(
                    select(SpendFxRate)
                    .where(*conditions)
                    .order_by(SpendFxRate.rate_date.desc(), SpendFxRate.currency)
                    .limit(max(1, min(limit, 500)))
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_rate_dict(row) for row in rows], "total": int(total or 0)}


async def rate_on(session: Any, tenant_id: uuid.UUID, currency: str, on: date) -> FxRate | None:
    """The rate of ``currency`` on ``on``, else the latest earlier one; ``None`` when there is none."""
    from core.models.spend import SpendFxRate

    rows = (
        (
            await session.execute(
                select(SpendFxRate)
                .where(
                    SpendFxRate.tenant_id == tenant_id,
                    SpendFxRate.currency == currency,
                    SpendFxRate.rate_date <= on,
                )
                .order_by(SpendFxRate.rate_date.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    row = rows[0]
    return FxRate(
        currency=str(row.currency).strip(),
        rate_date=row.rate_date,
        rate_to_inr=row.rate_to_inr,
        updated_at=getattr(row, "updated_at", None),
    )


async def latest_rate_dates(session: Any, tenant_id: uuid.UUID) -> dict[str, date]:
    """The newest rate date of every currency the tenant keeps."""
    from core.models.spend import SpendFxRate

    rows = (
        await session.execute(
            select(SpendFxRate.currency, func.max(SpendFxRate.rate_date))
            .where(SpendFxRate.tenant_id == tenant_id)
            .group_by(SpendFxRate.currency)
        )
    ).all()
    return {str(row[0]).strip(): row[1] for row in rows}
