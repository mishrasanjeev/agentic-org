# SPDX-License-Identifier: Apache-2.0
"""Storage GB-days: a daily sample of what each tenant keeps, one usage record per store and day.

The scheduled job (``sample_all_tenants``, the 23:30 IST beat) measures four
stores per tenant and writes each non-empty one as a ``storage`` record in
``gb_day`` (GB means GiB, 2^30 bytes; the unit keeps the spec's name):

* ``knowledge``: the text and vectors of ``knowledge_documents`` (soft-deleted
  rows included, they still occupy storage);
* ``documents``: the kept metadata of ``documents`` (upload bytes are not kept);
* ``idp``: the files ``idp_documents`` keeps;
* ``speech``: the recordings ``speech_recordings`` keeps.

A missing table is skipped. Records are priced by a ``platform_storage`` card
(``gb_day``, or ``gb_month`` over the days of the month) and unpriced without
one. Each record is keyed ``storage:{day}:{store}`` and stamped at the end of
its reporting day minus 30 minutes, so a day is written once whatever the
number of runs. The day a run records is the reporting day six hours before
it ran, so a run delayed past midnight still records the day it belongs to.

Before measuring, a tenant's keys for the day and the seven days before are
read; nothing is measured when the day is already written and no earlier day
is missing. A day missing between the tenant's first sampled day in that
window and today (a missed beat) is written with the same measurement and
flagged ``quantity_estimated``; days before the first sample are never filled,
so a new tenant or a newly enabled deployment is not charged for days nothing
sampled. Only the scheduled job writes: the route measures and audits a
preview, so a manual run cannot pre-empt the day's scheduled figure.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import structlog

from core.spend import vocab

logger = structlog.get_logger()

STORES = ("knowledge", "documents", "idp", "speech")
# Store -> the application its records carry.
STORE_APPLICATIONS = MappingProxyType(
    {"knowledge": "knowledge", "documents": "knowledge", "idp": "documents", "speech": "speech"}
)
SAMPLE_GRACE = timedelta(hours=6)
GAP_FILL_DAYS = 7
BYTES_PER_GIB = Decimal(1024**3)
USAGE_TYPE = "storage"
UNIT = "gb_day"

# Store -> (table, the bytes it keeps for one tenant). Fixed text, one bound parameter.
_STORE_SQL = MappingProxyType(
    {
        "knowledge": (
            "knowledge_documents",
            "SELECT COALESCE(SUM(octet_length(content)), 0) + COALESCE(SUM(pg_column_size(embedding)), 0) "
            "+ COALESCE(SUM(pg_column_size(embedding_bge_m3)), 0) FROM knowledge_documents WHERE tenant_id = :t",
        ),
        "documents": (
            "documents",
            "SELECT COALESCE(SUM(pg_column_size(metadata)), 0) FROM documents WHERE tenant_id = :t",
        ),
        "idp": ("idp_documents", "SELECT COALESCE(SUM(size_bytes), 0) FROM idp_documents WHERE tenant_id = :t"),
        "speech": (
            "speech_recordings",
            "SELECT COALESCE(SUM(size_bytes), 0) FROM speech_recordings WHERE tenant_id = :t",
        ),
    }
)


def intended_day(now: datetime) -> date:
    """The reporting day a run at ``now`` records: the day six hours earlier."""
    from core.spend import clock

    return clock.event_date_of(now - SAMPLE_GRACE)


def sample_time(day: date) -> datetime:
    """The instant a day's sample is stamped at: the end of the reporting day minus 30 minutes, in UTC."""
    from core.spend import clock

    return clock.day_bounds(day, clock.reporting_zone())[1] - timedelta(minutes=30)


def storage_key(day: date, store: str) -> str:
    return f"storage:{day.isoformat()}:{store}"


def gib(size_bytes: int) -> Decimal:
    """Bytes as GiB to six places."""
    return (Decimal(max(0, int(size_bytes))) / BYTES_PER_GIB).quantize(vocab.QTY_QUANT)


async def measure(session: Any, tenant_id: uuid.UUID) -> dict[str, int]:
    """Bytes each store keeps for the tenant; a store whose table does not exist is left out."""
    from sqlalchemy import text

    out: dict[str, int] = {}
    for store in STORES:
        table, sql = _STORE_SQL[store]
        found = (await session.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table}"})).scalar()
        if found is None:
            continue
        value = (await session.execute(text(sql), {"t": str(tenant_id)})).scalar()
        out[store] = int(value or 0)
    return out


def _window(day: date) -> list[date]:
    return [day - timedelta(days=offset) for offset in range(GAP_FILL_DAYS, -1, -1)]


async def _existing_keys(session: Any, tenant_id: uuid.UUID, days: list[date]) -> set[str]:
    from sqlalchemy import select

    from core.models.spend_usage import SpendUsageRecord as R

    keys = [storage_key(d, store) for d in days for store in STORES]
    times = [sample_time(d) for d in days]
    rows = (
        await session.execute(
            select(R.idempotency_key).where(
                R.tenant_id == tenant_id, R.idempotency_key.in_(keys), R.event_time.in_(times)
            )
        )
    ).all()
    return {str(row[0]) for row in rows}


def plan_days(day: date, existing: set[str]) -> tuple[bool, list[date]]:
    """``(the day is written, the earlier days to fill)`` from the keys already written in the window."""
    sampled = {d for d in _window(day) if any(storage_key(d, store) in existing for store in STORES)}
    earlier = sorted(d for d in sampled if d < day)
    if not earlier:
        return day in sampled, []
    first = earlier[0]
    gaps = [first + timedelta(days=n) for n in range(1, (day - first).days)]
    return day in sampled, [d for d in gaps if d not in sampled]


def _hints(store: str) -> Any:
    from core.spend.resolver import Hints

    return Hints(
        agent_id=None,
        agent_version=None,
        application=STORE_APPLICATIONS[store],
        default_use_case=f"storage.{store}",
        workflow_id=None,
        workflow_run_id=None,
        run_id=None,
        initiating_user_id=None,
    )


def storage_event(tenant_id: uuid.UUID, store: str, day: date, quantity: Decimal, *, estimated: bool) -> Any:
    """The usage event of one store's sample for one day."""
    from core.spend.meter import UsageEvent

    return UsageEvent(
        tenant_id=str(tenant_id),
        usage_type=USAGE_TYPE,
        unit=UNIT,
        quantity=quantity,
        provider=vocab.STORAGE_PROVIDER,
        model=store,
        event_time=sample_time(day),
        idempotency_key=storage_key(day, store),
        source_ref=store,
        correlation_ref="",
        hints=_hints(store),
        quantity_estimated=estimated,
        billing_account="in_house",
    )


async def sample_tenant(
    tenant_id: uuid.UUID, *, day: date, now: datetime, write: bool, actor: str | None = None
) -> dict[str, Any]:
    """Sample one tenant's storage for ``day``.

    ``write=True`` (the scheduled job): reads the window's keys first, measures
    only when something is missing, and writes the day and any missed day.
    ``write=False`` (the preview): measures, writes no record, and audits the
    preview under ``actor``.
    """
    from core.database import get_tenant_session
    from core.spend import audit, meter

    async with get_tenant_session(tenant_id) as session:
        existing: set[str] = set()
        done = False
        gaps: list[date] = []
        if write:
            existing = await _existing_keys(session, tenant_id, _window(day))
            done, gaps = plan_days(day, existing)
            if done and not gaps:
                return {"day": day.isoformat(), "measured": False, "stores": {}, "written": 0}
        sizes = await measure(session, tenant_id)
        stores = {store: gib(size) for store, size in sizes.items()}
        shown = {store: vocab.dec_str(quantity) for store, quantity in stores.items()}
        if not write:
            if actor:
                session.add(
                    audit.audit_entry(
                        tenant_id,
                        actor_id=actor,
                        action="storage.preview",
                        resource_type="spend_storage",
                        resource_id=day.isoformat(),
                        details={"day": day.isoformat(), "stores": shown, "written": 0},
                        now=now,
                    )
                )
            return {"day": day.isoformat(), "measured": True, "stores": shown, "written": 0}
        events = [
            storage_event(tenant_id, store, target, quantity, estimated=target != day)
            for target in ([] if done else [day]) + gaps
            for store, quantity in stores.items()
            if quantity > 0 and storage_key(target, store) not in existing
        ]
        written = (await meter.write_events(session, tenant_id, events, lock="wait", now=now)).written if events else 0
    return {
        "day": day.isoformat(),
        "measured": True,
        "stores": shown,
        "written": written,
        "filled": [d.isoformat() for d in gaps],
    }


async def sample_all_tenants(*, now: datetime | None = None) -> dict[str, Any]:
    """The scheduled sample: every active tenant's storage for the intended day, one tenant at a time."""
    from core.spend import clock, tenants

    stamp = now or clock.now_utc()
    day = intended_day(stamp)
    totals: dict[str, Any] = {"day": day.isoformat(), "tenants": 0, "measured": 0, "written": 0, "failed": 0}
    for tenant_id in await tenants.active_tenant_ids():
        totals["tenants"] += 1
        try:
            out = await sample_tenant(tenant_id, day=day, now=stamp, write=True)
        # enterprise-gate: broad-except-ok reason=storage-sample-isolates-per-tenant-failures-logged-continues-to-next
        except Exception as exc:
            logger.warning("spend_storage_sample_failed", error_type=type(exc).__name__)
            totals["failed"] += 1
            continue
        totals["measured"] += 1 if out["measured"] else 0
        totals["written"] += out["written"]
    logger.info("spend_storage_sampled", **{k: v for k, v in totals.items() if k != "day"})
    return totals
