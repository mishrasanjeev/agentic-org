# SPDX-License-Identifier: Apache-2.0
"""GPU node hours of in-house model serving, spread across the calls each hour served.

One in-house endpoint (ollama, vllm) serves every tenant, so a node hour is a
deployment cost held in the platform table ``spend_gpu_pool_hours``: standing
pools come from ``spend_gpu_pools_json`` (``materialise_config_hours``), and
metric-derived or manual hours from the operator command
``python -m core.spend.gpu_cli``. There is no HTTP write: no platform-operator
guard exists and a tenant administrator must not set a shared cost.

``allocate_hour`` spreads one pool hour once it has closed (75 minutes after
its start, so the writer and its spill have caught up):

1. **Claim** the hour (``pending``, or ``allocating`` with a claim older than
   an hour, so a crashed run is resumed) and freeze it: records created after
   ``frozen_at`` are never counted, so the totals stay stable on a rerun.
2. **Tokens per tenant**: the hour's in-house-priced token records of the
   pool's models. A call a tenant's own card priced already carries its cost
   and is counted as skipped, so in-house cost is never counted twice. Each
   tenant with tokens gets a ``frozen`` allocation row.
3. **Shares** by largest remainder: the tenants' node hours sum to the hour's
   exactly. An hour no tenant used is ``idle``: idle capacity stays with the
   platform, never on a tenant's attribution.
4. **Per tenant**, in one transaction: the share is priced once with the
   tenant's ``gpu_hours`` card (``gpu_node_hour``, the pool as model, or the
   provider default; unpriced without one), then its node hours, amount and
   INR amount are split over the tenant's calls by tokens, so money is
   conserved as well as hours; each call's share is a record flagged
   ``allocated`` with the call's attribution, keyed
   ``gpu:{pool_hour_id}:{call}``; the allocation row becomes ``written``.
5. **Close** the hour with its totals.

Re-running is idempotent: ``written`` allocations are not rewritten and the
record keys drop any repeat. In-house calls written after their hour was
frozen are not reallocated.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import structlog

from core.spend import vocab
from core.spend.errors import SpendError

logger = structlog.get_logger()

ALLOCATION_DELAY = timedelta(minutes=75)
MAX_HOURS_PER_RUN = 48
CONFIG_LOOKBACK = timedelta(days=7)
STALE_CLAIM = timedelta(hours=1)
MAX_WINDOW_DAYS = 31
MAX_RECORD_HOURS = 744  # 31 days of hours per operator command
MAX_NODE_HOURS = Decimal("10000")
HOUR = timedelta(hours=1)
PROVIDERS = ("ollama", "vllm")
SOURCES = ("config", "metrics", "manual")
USAGE_TYPE = "gpu_hours"
UNIT = "gpu_node_hour"
INSERT_CHUNK = 500


@dataclass(frozen=True)
class PoolHour:
    """A claimed pool hour."""

    id: uuid.UUID
    provider: str
    node_pool: str
    models: tuple[str, ...]
    hour_start: datetime
    node_hours: Decimal
    frozen_at: datetime


# ---------------------------------------------------------------- pure helpers


def largest_remainder(
    total: Decimal, weights: Sequence[tuple[str, Decimal]], quant: Decimal
) -> list[tuple[str, Decimal]]:
    """Split ``total`` by ``weights`` in steps of ``quant``: shares sum to ``total`` exactly, none negative.

    Each share is ``total * w / sum(w)`` rounded down to ``quant``; the units
    left over go one each to the largest fractional parts (ties by key). A
    zero or negative weight gets nothing. ``total`` is taken to ``quant``.
    """
    if not weights:
        return []
    with localcontext() as ctx:
        ctx.prec = 38
        amount = Decimal(total).quantize(quant, ROUND_HALF_EVEN)
        positive = [max(Decimal(0), Decimal(w)) for _key, w in weights]
        weight_sum = sum(positive, Decimal(0))
        if weight_sum <= 0 or amount <= 0:
            return [(key, Decimal(0).quantize(quant)) for key, _w in weights]
        floors: list[Decimal] = []
        remainders: list[Decimal] = []
        for weight in positive:
            exact = amount * weight / weight_sum
            floor = exact.quantize(quant, ROUND_DOWN)
            floors.append(floor)
            remainders.append(exact - floor)
        left = int(((amount - sum(floors, Decimal(0))) / quant).to_integral_value(ROUND_HALF_EVEN))
        order = sorted(range(len(weights)), key=lambda i: (-remainders[i], weights[i][0]))
        for index in order[:left]:
            floors[index] += quant
    return [(key, floors[i]) for i, (key, _w) in enumerate(weights)]


def whole_hour(value: datetime, *, field: str) -> datetime:
    """A whole UTC hour (an instant without an offset is read as UTC); 422 ``invalid_period`` otherwise."""
    moment = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if moment.minute or moment.second or moment.microsecond:
        raise SpendError(422, "invalid_period", f"{field} is a whole UTC hour")
    return moment


def _ceil_hour(moment: datetime) -> datetime:
    floor = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return floor if floor == moment else floor + HOUR


def config_hours(pools: Sequence[Any], *, now: datetime) -> list[tuple[Any, datetime]]:
    """``(pool, hour_start)`` of every configured hour from seven days back that has closed for allocation."""
    out: list[tuple[Any, datetime]] = []
    earliest = _ceil_hour(now - CONFIG_LOOKBACK)
    for pool in pools:
        hour = max(pool.start, earliest)
        while hour + ALLOCATION_DELAY <= now and (pool.end is None or hour < pool.end):
            out.append((pool, hour))
            hour += HOUR
    return out


# ---------------------------------------------------------------- pool hours (platform table)


async def materialise_config_hours(*, now: datetime) -> int:
    """Insert a ``pending`` row for each configured pool hour that has none (never overwriting one)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.config import parse_spend_gpu_pools, settings
    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour

    hours = config_hours(parse_spend_gpu_pools(settings.spend_gpu_pools_json), now=now)
    if not hours:
        return 0
    table = SpendGpuPoolHour.__table__
    rows = [
        {
            "id": uuid.uuid4(),
            "provider": pool.provider,
            "node_pool": pool.node_pool,
            "models": list(pool.models),
            "hour_start": hour,
            "node_hours": pool.nodes,
            "source": "config",
            "status": "pending",
            "recorded_by": "config",
        }
        for pool, hour in hours
    ]
    created = 0
    async with async_session_factory() as session:
        for start in range(0, len(rows), INSERT_CHUNK):
            statement = (
                pg_insert(table)
                .values(rows[start : start + INSERT_CHUNK])
                .on_conflict_do_nothing(index_elements=["provider", "node_pool", "hour_start"])
                .returning(table.c.id)
            )
            created += len((await session.execute(statement)).all())
        await session.commit()
    return created


async def _claim(pool_hour_id: uuid.UUID, now: datetime) -> PoolHour | None:
    """Take the hour for this run (pending, or a claim older than an hour) and freeze its token window."""
    from sqlalchemy import and_, func, or_, update

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    statement = (
        update(H)
        .where(
            H.id == pool_hour_id,
            or_(H.status == "pending", and_(H.status == "allocating", H.claimed_at < now - STALE_CLAIM)),
        )
        .values(status="allocating", claimed_at=now, frozen_at=func.coalesce(H.frozen_at, func.now()), updated_at=now)
        .returning(H.provider, H.node_pool, H.models, H.hour_start, H.node_hours, H.frozen_at)
        .execution_options(synchronize_session=False)
    )
    async with async_session_factory() as session:
        row = (await session.execute(statement)).first()
        await session.commit()
    if row is None:
        return None
    return PoolHour(
        id=pool_hour_id,
        provider=str(row[0]),
        node_pool=str(row[1]),
        models=tuple(str(m) for m in (row[2] or [])),
        hour_start=row[3],
        node_hours=Decimal(row[4]),
        frozen_at=row[5],
    )


async def _close(pool_hour_id: uuid.UUID, values: dict[str, Any]) -> None:
    from sqlalchemy import update

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    async with async_session_factory() as session:
        await session.execute(
            update(H).where(H.id == pool_hour_id).values(**values).execution_options(synchronize_session=False)
        )
        await session.commit()


# ---------------------------------------------------------------- per tenant


def _call_filter(tenant_id: uuid.UUID, hour: PoolHour) -> list[Any]:
    from core.models.spend_usage import SpendUsageRecord as R

    return [
        R.tenant_id == tenant_id,
        R.usage_type == "llm_tokens",
        R.provider == hour.provider,
        R.model.in_(list(hour.models)),
        R.allocated.is_(False),
        R.event_time >= hour.hour_start,
        R.event_time < hour.hour_start + HOUR,
        R.created_at <= hour.frozen_at,
    ]


async def _freeze_tenant(tenant_id: uuid.UUID, hour: PoolHour, *, now: datetime) -> tuple[Decimal, int, str | None]:
    """Pass 1 for one tenant: ``(in-house tokens, card-priced calls skipped, allocation status)``.

    A tenant with tokens gets a ``frozen`` allocation row; a ``written`` one
    keeps the tokens it was written with.
    """
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.models.spend_gpu import SpendGpuAllocation as A
    from core.models.spend_usage import SpendUsageRecord as R

    async with get_tenant_session(tenant_id) as session:
        conditions = _call_filter(tenant_id, hour)
        summed = (
            await session.execute(select(func.sum(R.quantity)).where(*conditions, R.price_source == "in_house"))
        ).scalar()
        tokens = Decimal(summed or 0)
        skipped = len(
            (
                await session.execute(select(R.source_ref).where(*conditions, R.price_source != "in_house").distinct())
            ).all()
        )
        allocation = (
            await session.execute(select(A).where(A.tenant_id == tenant_id, A.pool_hour_id == hour.id))
        ).scalar_one_or_none()
        if allocation is not None and allocation.status == "written":
            return Decimal(allocation.tokens), skipped, "written"
        if allocation is not None:
            allocation.tokens = tokens
            allocation.updated_at = now
        elif tokens > 0:
            session.add(
                A(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    pool_hour_id=hour.id,
                    provider=hour.provider,
                    node_pool=hour.node_pool,
                    hour_start=hour.hour_start,
                    tokens=tokens,
                    status="frozen",
                    records=0,
                )
            )
    return tokens, skipped, ("frozen" if tokens > 0 else None)


_ATTRIBUTION = (
    "org_node_id",
    "business_unit_node_id",
    "attribution_path",
    "unattributed_reason",
    "product_line",
    "use_case",
    "agent_id",
    "agent_version",
    "risk_tier",
    "region",
    "environment",
    "initiating_user_id",
    "workflow_id",
    "run_id",
    "application",
)


def _resolved_of(row: Any) -> Any:
    from core.spend.resolver import Resolved

    values = dict(zip(_ATTRIBUTION, row, strict=True))
    values["use_case"] = values["use_case"] or ""
    values["environment"] = values["environment"] or ""
    values["application"] = values["application"] or "system"
    return Resolved(**values)


def _hints_of(resolved: Any) -> Any:
    from core.spend.resolver import Hints

    return Hints(
        agent_id=str(resolved.agent_id) if resolved.agent_id else None,
        agent_version=resolved.agent_version,
        application=resolved.application,
        default_use_case=resolved.use_case,
        workflow_id=str(resolved.workflow_id) if resolved.workflow_id else None,
        workflow_run_id=None,
        run_id=resolved.run_id,
        initiating_user_id=str(resolved.initiating_user_id) if resolved.initiating_user_id else None,
    )


def split_share(share: Decimal, calls: Sequence[tuple[str, Decimal]], priced: Any) -> list[tuple[str, Decimal, Any]]:
    """``(call, node hours, priced)`` per call: the share's hours, amount and INR amount split by tokens.

    The amount is split only over the calls whose hours share is above zero,
    so a call left with no hours carries no money and nothing is lost.
    """
    hours = [(ref, q) for ref, q in largest_remainder(share, calls, vocab.QTY_QUANT) if q > 0]
    tokens = dict(calls)
    weights = [(ref, tokens[ref]) for ref, _q in hours]
    amounts = dict(largest_remainder(priced.amount, weights, vocab.AMOUNT_QUANT)) if priced.amount is not None else {}
    inrs = (
        dict(largest_remainder(priced.amount_inr, weights, vocab.AMOUNT_QUANT)) if priced.amount_inr is not None else {}
    )
    return [
        (
            ref,
            quantity,
            replace(
                priced,
                amount=amounts.get(ref) if priced.amount is not None else None,
                amount_inr=inrs.get(ref) if priced.amount_inr is not None else None,
            ),
        )
        for ref, quantity in hours
    ]


async def _write_tenant(tenant_id: uuid.UUID, hour: PoolHour, share: Decimal, *, now: datetime) -> int:
    """Pass 2 for one tenant: price its share once, split it over its calls, write the records. One transaction."""
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.models.spend_gpu import SpendGpuAllocation as A
    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import clock, meter, pricing

    async with get_tenant_session(tenant_id) as session:
        conditions = _call_filter(tenant_id, hour)
        grouped = (
            await session.execute(
                select(R.source_ref, func.sum(R.quantity))
                .where(*conditions, R.price_source == "in_house")
                .group_by(R.source_ref)
                .order_by(R.source_ref)
            )
        ).all()
        calls = sorted((str(row[0]), Decimal(row[1] or 0)) for row in grouped)
        calls = [(ref, tokens) for ref, tokens in calls if tokens > 0]
        refs = [ref for ref, _tokens in calls]
        columns = [getattr(R, name) for name in _ATTRIBUTION]
        found = (
            await session.execute(
                select(R.source_ref, *columns)
                .where(
                    R.tenant_id == tenant_id,
                    R.usage_type == "llm_tokens",
                    R.source_ref.in_(refs),
                    R.event_time >= hour.hour_start,
                    R.event_time < hour.hour_start + HOUR,
                )
                .order_by(R.source_ref, R.id)
            )
        ).all()
        attribution: dict[str, Any] = {}
        for row in found:
            attribution.setdefault(str(row[0]), _resolved_of(row[1:]))
        priced = await pricing.price(
            session,
            tenant_id,
            pricing.Usage(
                provider=hour.provider,
                usage_type=USAGE_TYPE,
                unit=UNIT,
                quantity=share,
                model=hour.node_pool,
                on=clock.billing_date_of(hour.provider, hour.hour_start),
                fx_on=clock.event_date_of(hour.hour_start),
            ),
        )
        events = []
        for ref, quantity, call_priced in split_share(share, calls, priced):
            resolved = attribution.get(ref)
            if resolved is None:
                continue
            events.append(
                meter.UsageEvent(
                    tenant_id=str(tenant_id),
                    usage_type=USAGE_TYPE,
                    unit=UNIT,
                    quantity=quantity,
                    provider=hour.provider,
                    model=hour.node_pool,
                    event_time=hour.hour_start,
                    idempotency_key=f"gpu:{hour.id}:{ref}",
                    source_ref=ref,
                    correlation_ref="",
                    hints=_hints_of(resolved),
                    allocated=True,
                    allocated_from=ref,
                    billing_account="in_house",
                    resolved=resolved,
                    priced=call_priced,
                )
            )
        if events:
            await meter.write_events(session, tenant_id, events, lock="wait", now=now)
        allocation = (
            await session.execute(select(A).where(A.tenant_id == tenant_id, A.pool_hour_id == hour.id))
        ).scalar_one_or_none()
        if allocation is not None:
            allocation.status = "written"
            allocation.node_hours = share
            allocation.amount = priced.amount
            allocation.currency = priced.currency
            allocation.records = len(events)
            allocation.updated_at = now
    return len(events)


# ---------------------------------------------------------------- allocation


async def allocate_hour(pool_hour_id: uuid.UUID, *, now: datetime) -> dict[str, Any]:
    """Spread one closed pool hour across every tenant's in-house calls of the pool's models."""
    from core.spend import tenants

    hour = await _claim(pool_hour_id, now)
    if hour is None:
        return {"id": str(pool_hour_id), "claimed": False}
    frozen: dict[uuid.UUID, tuple[Decimal, str | None]] = {}
    skipped = 0
    for tenant_id in await tenants.active_tenant_ids():
        tokens, priced_calls, status = await _freeze_tenant(tenant_id, hour, now=now)
        skipped += priced_calls
        if tokens > 0:
            frozen[tenant_id] = (tokens, status)
    total = sum((tokens for tokens, _status in frozen.values()), Decimal(0))
    out: dict[str, Any] = {
        "id": str(pool_hour_id),
        "claimed": True,
        "tenants": len(frozen),
        "total_tokens": vocab.dec_str(total),
        "priced_calls_skipped": skipped,
        "records": 0,
        "failed": 0,
        "idle": total == 0,
    }
    if total > 0:
        weights = sorted((str(tenant_id), tokens) for tenant_id, (tokens, _status) in frozen.items())
        for key, share in largest_remainder(hour.node_hours, weights, vocab.QTY_QUANT):
            tenant_id = uuid.UUID(key)
            if share <= 0 or frozen[tenant_id][1] == "written":
                continue
            try:
                out["records"] += await _write_tenant(tenant_id, hour, share, now=now)
            # enterprise-gate: broad-except-ok reason=gpu-allocation-isolates-a-tenant-failure-logged-hour-resumed-later
            except Exception as exc:
                logger.warning("spend_gpu_allocation_failed", error_type=type(exc).__name__)
                out["failed"] += 1
    if out["failed"]:
        return out
    await _close(
        pool_hour_id,
        {
            "status": "allocated",
            "total_tokens": total,
            "tenant_count": len(frozen),
            "priced_calls_skipped": skipped,
            "idle": total == 0,
            "allocated_at": now,
            "updated_at": now,
        },
    )
    return out


async def allocate_pending(*, now: datetime) -> dict[str, Any]:
    """Materialise configured hours, then spread the closed pending hours, oldest first, at most 48 per run."""
    from sqlalchemy import and_, or_, select

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    materialised = await materialise_config_hours(now=now)
    async with async_session_factory() as session:
        due = (
            await session.execute(
                select(H.id)
                .where(
                    or_(H.status == "pending", and_(H.status == "allocating", H.claimed_at < now - STALE_CLAIM)),
                    H.hour_start <= now - ALLOCATION_DELAY,
                )
                .order_by(H.hour_start, H.id)
                .limit(MAX_HOURS_PER_RUN)
            )
        ).all()
    totals: dict[str, Any] = {"materialised": materialised, "hours": 0, "idle": 0, "records": 0, "failed": 0}
    for row in due:
        try:
            out = await allocate_hour(row[0], now=now)
        # enterprise-gate: broad-except-ok reason=gpu-allocation-isolates-a-failed-hour-logged-resumed-later
        except Exception as exc:
            logger.warning("spend_gpu_hour_failed", error_type=type(exc).__name__)
            totals["failed"] += 1
            continue
        if not out.get("claimed"):
            continue
        totals["hours"] += 1
        totals["idle"] += 1 if out["idle"] else 0
        totals["records"] += out["records"]
        totals["failed"] += out["failed"]
    return totals


# ---------------------------------------------------------------- reads and the operator command


def _window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
    end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
    if end <= start:
        raise SpendError(422, "invalid_period", "end is after start")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise SpendError(422, "range_too_long", f"a window is at most {MAX_WINDOW_DAYS} days")
    return start, end


def allocation_dict(row: Any) -> dict[str, Any]:
    return {
        "hour_start": row.hour_start.isoformat(),
        "provider": row.provider,
        "node_pool": row.node_pool,
        "tokens": vocab.dec_str(row.tokens),
        "node_hours": vocab.dec_str(row.node_hours),
        "amount": vocab.dec_str(row.amount),
        "currency": row.currency,
        "records": row.records,
        "status": row.status,
    }


async def list_allocations(tenant_id: uuid.UUID, *, start: datetime, end: datetime) -> dict[str, Any]:
    """The tenant's own shares of pool hours starting in ``[start, end)`` (at most 31 days)."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.spend_gpu import SpendGpuAllocation as A

    start, end = _window(start, end)
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(A)
                    .where(A.tenant_id == tenant_id, A.hour_start >= start, A.hour_start < end)
                    .order_by(A.hour_start, A.node_pool)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [allocation_dict(row) for row in rows]}


def check_record(
    *,
    provider: str,
    node_pool: str,
    models: Sequence[str],
    hour_start: datetime,
    hour_end: datetime | None,
    node_hours: Any,
    source: str,
    actor: str,
) -> dict[str, Any]:
    """The checked input of an operator's node-hour record; 422 naming the first problem."""
    provider_id = vocab.choice(provider, PROVIDERS, field="provider", code="invalid_reference")
    source_id = vocab.choice(source, ("metrics", "manual"), field="source", code="invalid_value")
    pool = vocab.norm_sku(node_pool)
    if len(pool) > 64:
        raise SpendError(422, "invalid_sku", "a node pool is at most 64 characters")
    names = tuple(dict.fromkeys(vocab.norm_sku(m) for m in models))
    if not 1 <= len(names) <= 50:
        raise SpendError(422, "invalid_sku", "models are 1 to 50 model names")
    first = whole_hour(hour_start, field="hour_start")
    last = whole_hour(hour_end, field="hour_end") if hour_end is not None else first + HOUR
    if last <= first:
        raise SpendError(422, "invalid_period", "hour_end is after hour_start")
    count = int((last - first) / HOUR)
    if count > MAX_RECORD_HOURS:
        raise SpendError(422, "range_too_long", f"one command records at most {MAX_RECORD_HOURS} hours")
    return {
        "provider": provider_id,
        "node_pool": pool,
        "models": list(names),
        "hours": [first + HOUR * n for n in range(count)],
        "node_hours": vocab.parse_decimal(
            node_hours, field="node_hours", minimum=0, maximum=MAX_NODE_HOURS, places=4, strict_minimum=True
        ),
        "source": source_id,
        "actor": vocab.free_text(actor, field="actor", max_len=128, required=True),
    }


async def record_hours(*, now: datetime, **body: Any) -> dict[str, Any]:
    """Upsert ``pending`` node-hour rows (the operator command); an hour already allocated is left alone."""
    from sqlalchemy import select

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    checked = check_record(**body)
    created, updated, allocated = 0, 0, []
    async with async_session_factory() as session:
        for hour in checked["hours"]:
            row = (
                await session.execute(
                    select(H).where(
                        H.provider == checked["provider"], H.node_pool == checked["node_pool"], H.hour_start == hour
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    H(
                        id=uuid.uuid4(),
                        provider=checked["provider"],
                        node_pool=checked["node_pool"],
                        models=checked["models"],
                        hour_start=hour,
                        node_hours=checked["node_hours"],
                        source=checked["source"],
                        status="pending",
                        recorded_by=checked["actor"],
                    )
                )
                created += 1
            elif row.status == "pending":
                row.models = checked["models"]
                row.node_hours = checked["node_hours"]
                row.source = checked["source"]
                row.recorded_by = checked["actor"]
                row.updated_at = now
                updated += 1
            else:
                allocated.append(hour.isoformat())
        await session.commit()
    logger.info(
        "spend_gpu_hours_recorded",
        provider=checked["provider"],
        source=checked["source"],
        created=created,
        updated=updated,
        already_allocated=len(allocated),
    )
    return {"created": created, "updated": updated, "already_allocated": allocated}


def pool_hour_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "node_pool": row.node_pool,
        "models": list(row.models or []),
        "hour_start": row.hour_start.isoformat(),
        "node_hours": vocab.dec_str(row.node_hours),
        "source": row.source,
        "status": row.status,
        "idle": bool(row.idle),
        "tenant_count": row.tenant_count,
        "total_tokens": vocab.dec_str(row.total_tokens),
        "priced_calls_skipped": row.priced_calls_skipped,
        "recorded_by": row.recorded_by,
    }


async def list_pool_hours(*, start: datetime, end: datetime) -> dict[str, Any]:
    """The pool hours starting in ``[start, end)`` (at most 31 days), for the operator."""
    from sqlalchemy import select

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    start, end = _window(start, end)
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(H)
                    .where(H.hour_start >= start, H.hour_start < end)
                    .order_by(H.hour_start, H.provider, H.node_pool)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [pool_hour_dict(row) for row in rows]}
