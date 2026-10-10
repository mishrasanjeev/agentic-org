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
2. **Tokens per tenant**: every token record of the pool's models in the
   hour, whatever priced it, so the hour is spread over everything the pool
   served and no tenant's own rate card can move its share onto other
   tenants. Calls priced at zero (in-house, or a card at zero) are charged
   their share; a call a card priced above zero already carries its cost, so
   its share stays with the platform (``skipped_node_hours``) and it is
   counted in ``priced_calls_skipped``: in-house cost is never counted twice.
   The calls of a tenant deleted since the hour began count too, and their
   share also stays with the platform. Each active tenant with charged
   tokens gets a ``frozen`` allocation row.
3. **Shares** by largest remainder over the tenants and the platform's part:
   they sum to the hour's node hours exactly. An hour no call used is
   ``idle``: idle capacity stays with the platform, never on a tenant's
   attribution.
4. **Per tenant**, in one transaction: the share is priced once with the
   tenant's ``gpu_hours`` card (``gpu_node_hour``, the pool as model, or the
   provider default; unpriced without one), then its node hours, amount and
   INR amount are split over the tenant's calls by tokens, so money is
   conserved as well as hours; each call's share is a record flagged
   ``allocated`` with the call's attribution, keyed
   ``gpu:{pool_hour_id}:{call}``; the allocation row becomes ``written``.
   A tenant whose share rounds to nothing is marked ``written`` with no
   hours and no records.
5. **Close** the hour with its totals.

Re-running is idempotent: ``written`` allocations are not rewritten and the
record keys drop any repeat. In-house calls written after their hour was
frozen are not reallocated. Fresh hours are allocated before resumed ones,
so an hour that keeps failing never holds back newer hours.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import structlog

from core.config import SPEND_GPU_POOL_MAX_CHARS
from core.spend import vocab
from core.spend.errors import SpendError

logger = structlog.get_logger()

ALLOCATION_DELAY = timedelta(minutes=75)
MAX_HOURS_PER_RUN = 48
CONFIG_LOOKBACK = timedelta(days=7)
STALE_CLAIM = timedelta(hours=1)
MAX_WINDOW_DAYS = 31
MAX_RECORD_HOURS = 744  # 31 days of hours per operator command
# How far back the operator command records hours: the window configured pools are materialised in.
RECORD_LOOKBACK = CONFIG_LOOKBACK
MAX_NODE_HOURS = Decimal("10000")
HOUR = timedelta(hours=1)
PROVIDERS = ("ollama", "vllm")
SOURCES = ("config", "metrics", "manual")
USAGE_TYPE = "gpu_hours"
UNIT = "gpu_node_hour"
INSERT_CHUNK = 500
POOL_MAX_CHARS = SPEND_GPU_POOL_MAX_CHARS
# The platform's part of an hour in the largest-remainder split. It sorts before every tenant id,
# so a leftover unit tied between the platform and a tenant stays with the platform.
PLATFORM_KEY = "-platform"


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


def _claimable() -> Any:
    """A pool hour a run may take: pending, or a claim the database clock says is over an hour old."""
    from sqlalchemy import and_, func, or_

    from core.models.spend_gpu import SpendGpuPoolHour as H

    return or_(H.status == "pending", and_(H.status == "allocating", H.claimed_at < func.now() - STALE_CLAIM))


async def _claim(pool_hour_id: uuid.UUID, now: datetime) -> PoolHour | None:
    """Take the hour for this run (pending, or a claim older than an hour) and freeze its token window.

    ``claimed_at`` is the database clock at the claim, not the run's start, so
    a run that spends more than an hour on earlier hours never makes a later
    claim look stale to the next run.
    """
    from sqlalchemy import func, update

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    statement = (
        update(H)
        .where(H.id == pool_hour_id, _claimable())
        .values(
            status="allocating",
            claimed_at=func.now(),
            frozen_at=func.coalesce(H.frozen_at, func.now()),
            updated_at=now,
        )
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
    """The token records of the pool's models in the hour, as they stood when the hour was frozen."""
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


def _priced_above_zero() -> Any:
    """A record a card (or the deployment's price override) priced above zero: it carries its own cost."""
    from sqlalchemy import func

    from core.models.spend_usage import SpendUsageRecord as R

    return func.coalesce(R.amount, 0) > 0


def _charged() -> Any:
    """A record priced at zero (in-house, or a card at zero): its call is charged a share of the hour."""
    from sqlalchemy import func

    from core.models.spend_usage import SpendUsageRecord as R

    return func.coalesce(R.amount, 0) <= 0


@dataclass(frozen=True)
class TenantTokens:
    """One tenant's calls in a pool hour (pass 1)."""

    charged: Decimal  # tokens of calls priced at zero: they share the hour (a written row: the tokens it kept)
    own_cost: Decimal  # tokens of calls a card priced above zero: their share stays with the platform
    own_cost_calls: int  # those calls, counted in ``priced_calls_skipped``
    status: str | None  # the tenant's allocation row: None, "frozen" or "written"
    written_hours: Decimal  # the node hours a written row carries


def _settle(allocation: Any, *, now: datetime) -> None:
    """Mark an allocation row done with no share: no node hours, no records."""
    allocation.status = "written"
    allocation.node_hours = Decimal(0)
    allocation.amount = None
    allocation.currency = None
    allocation.records = 0
    allocation.updated_at = now


async def _freeze_tenant(tenant_id: uuid.UUID, hour: PoolHour, *, now: datetime, active: bool) -> TenantTokens:
    """Pass 1 for one tenant: its charged and card-priced tokens, and its allocation row.

    An active tenant with charged tokens gets a ``frozen`` allocation row; a
    ``written`` one keeps the tokens and hours it was written with. A tenant
    deleted since the hour gets no share: a row left ``frozen`` by an earlier
    run is settled with none.
    """
    from sqlalchemy import case, func, select

    from core.database import get_tenant_session
    from core.models.spend_gpu import SpendGpuAllocation as A
    from core.models.spend_usage import SpendUsageRecord as R

    async with get_tenant_session(tenant_id) as session:
        conditions = _call_filter(tenant_id, hour)
        above = _priced_above_zero()
        sums = (
            await session.execute(
                select(
                    func.sum(case((above, 0), else_=R.quantity)),
                    func.sum(case((above, R.quantity), else_=0)),
                ).where(*conditions)
            )
        ).first()
        charged = Decimal((sums[0] if sums else None) or 0)
        own_cost = Decimal((sums[1] if sums else None) or 0)
        own_cost_calls = len((await session.execute(select(R.source_ref).where(*conditions, above).distinct())).all())
        allocation = (
            await session.execute(select(A).where(A.tenant_id == tenant_id, A.pool_hour_id == hour.id))
        ).scalar_one_or_none()
        if allocation is not None and allocation.status == "written":
            return TenantTokens(
                Decimal(allocation.tokens), own_cost, own_cost_calls, "written", Decimal(allocation.node_hours or 0)
            )
        if not active:
            if allocation is not None:
                _settle(allocation, now=now)
            return TenantTokens(charged, own_cost, own_cost_calls, None, Decimal(0))
        if allocation is not None:
            allocation.tokens = charged
            allocation.updated_at = now
            if charged <= 0:
                _settle(allocation, now=now)
        elif charged > 0:
            session.add(
                A(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    pool_hour_id=hour.id,
                    provider=hour.provider,
                    node_pool=hour.node_pool,
                    hour_start=hour.hour_start,
                    tokens=charged,
                    status="frozen",
                    records=0,
                )
            )
    return TenantTokens(charged, own_cost, own_cost_calls, "frozen" if charged > 0 else None, Decimal(0))


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


async def _share_events(session: Any, tenant_id: uuid.UUID, hour: PoolHour, share: Decimal) -> tuple[list[Any], Any]:
    """The tenant's share priced once and split over its charged calls: ``(events, priced share)``."""
    from sqlalchemy import func, select

    from core.models.spend_usage import SpendUsageRecord as R
    from core.spend import clock, meter, pricing

    grouped = (
        await session.execute(
            select(R.source_ref, func.sum(R.quantity))
            .where(*_call_filter(tenant_id, hour), _charged())
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
    return events, priced


async def _write_tenant(tenant_id: uuid.UUID, hour: PoolHour, share: Decimal, *, now: datetime) -> int:
    """Pass 2 for one tenant, in one transaction: write its share's records and mark its row ``written``.

    A share that rounds to nothing writes no record and settles the row with
    no hours, so no row is left ``frozen`` once the hour closes.
    """
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.spend_gpu import SpendGpuAllocation as A
    from core.spend import meter

    async with get_tenant_session(tenant_id) as session:
        events: list[Any] = []
        priced = None
        if share > 0:
            events, priced = await _share_events(session, tenant_id, hour, share)
        if events:
            await meter.write_events(session, tenant_id, events, lock="wait", now=now)
        allocation = (
            await session.execute(select(A).where(A.tenant_id == tenant_id, A.pool_hour_id == hour.id))
        ).scalar_one_or_none()
        if allocation is not None:
            if priced is None:
                _settle(allocation, now=now)
            else:
                allocation.status = "written"
                allocation.node_hours = share
                allocation.amount = priced.amount
                allocation.currency = priced.currency
                allocation.records = len(events)
                allocation.updated_at = now
    return len(events)


# ---------------------------------------------------------------- allocation


async def allocate_hour(pool_hour_id: uuid.UUID, *, now: datetime) -> dict[str, Any]:
    """Spread one closed pool hour over every call of the pool's models; charge the zero-priced calls' tenants.

    A tenant ``spend.metering_paused`` holds for keeps its allocation ``frozen``
    and the hour is not closed, so it is resumed, with the same totals, once
    metering resumes.
    """
    from core.spend import tenants, writer

    hour = await _claim(pool_hour_id, now)
    if hour is None:
        return {"id": str(pool_hour_id), "claimed": False}
    charged: dict[uuid.UUID, Decimal] = {}  # tenant -> tokens charged a share of the hour
    written: dict[uuid.UUID, Decimal] = {}  # tenant -> node hours an earlier run already wrote
    kept = Decimal(0)  # tokens whose share stays with the platform
    skipped = 0
    for tenant_id, active in await tenants.tenants_since(hour.hour_start):
        found = await _freeze_tenant(tenant_id, hour, now=now, active=active)
        skipped += found.own_cost_calls
        kept += found.own_cost
        if found.status == "written":
            charged[tenant_id] = found.charged
            written[tenant_id] = found.written_hours
        elif not active:
            kept += found.charged
        elif found.charged > 0:
            charged[tenant_id] = found.charged
    total = sum(charged.values(), Decimal(0)) + kept
    out: dict[str, Any] = {
        "id": str(pool_hour_id),
        "claimed": True,
        "tenants": len(charged),
        "total_tokens": vocab.dec_str(total),
        "priced_calls_skipped": skipped,
        "records": 0,
        "failed": 0,
        "paused": 0,
        "idle": total == 0,
    }
    carried = Decimal(0)  # node hours tenant records carry
    if total > 0:
        weights = sorted((str(tenant_id), tokens) for tenant_id, tokens in charged.items())
        for key, share in largest_remainder(hour.node_hours, [*weights, (PLATFORM_KEY, kept)], vocab.QTY_QUANT):
            if key == PLATFORM_KEY:
                continue
            tenant_id = uuid.UUID(key)
            if tenant_id in written:
                carried += written[tenant_id]
                continue
            if await writer.metering_paused(tenant_id):
                out["paused"] += 1  # its row stays frozen; the hour stays claimed and is resumed later
                continue
            try:
                out["records"] += await _write_tenant(tenant_id, hour, share, now=now)
                carried += share
            # enterprise-gate: broad-except-ok reason=gpu-allocation-isolates-a-tenant-failure-logged-hour-resumed-later
            except Exception as exc:
                logger.warning("spend_gpu_allocation_failed", error_type=type(exc).__name__)
                out["failed"] += 1
    if out["paused"]:
        logger.info("spend_gpu_allocation_paused", pool_hour_id=str(pool_hour_id), tenants=out["paused"])
    if out["failed"] or out["paused"]:
        return out
    # Exact for one run; a resumed run keeps the hours its first run wrote, so bound it to the hour.
    platform_hours = min(hour.node_hours, max(Decimal(0), Decimal(hour.node_hours) - carried))
    out["skipped_node_hours"] = vocab.dec_str(platform_hours)
    await _close(
        pool_hour_id,
        {
            "status": "allocated",
            "total_tokens": total,
            "tenant_count": len(charged),
            "priced_calls_skipped": skipped,
            "skipped_node_hours": platform_hours,
            "idle": total == 0,
            "allocated_at": now,
            "updated_at": now,
        },
    )
    return out


async def allocate_pending(*, now: datetime) -> dict[str, Any]:
    """Materialise configured hours, then spread the closed hours, fresh before resumed, at most 48 per run."""
    from sqlalchemy import case, select

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    totals: dict[str, Any] = {"materialised": 0, "hours": 0, "idle": 0, "records": 0, "failed": 0}
    try:
        totals["materialised"] = await materialise_config_hours(now=now)
    # enterprise-gate: broad-except-ok reason=gpu-config-hours-failure-is-logged-recorded-hours-still-allocated
    except Exception as exc:
        logger.warning("spend_gpu_config_hours_failed", error_type=type(exc).__name__)
        totals["failed"] += 1
    async with async_session_factory() as session:
        due = (
            await session.execute(
                select(H.id)
                .where(_claimable(), H.hour_start <= now - ALLOCATION_DELAY)
                .order_by(case((H.status == "pending", 0), else_=1), H.hour_start, H.id)
                .limit(MAX_HOURS_PER_RUN)
            )
        ).all()
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
        if out.get("paused"):
            totals["paused"] = totals.get("paused", 0) + out["paused"]
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
    if len(pool) > POOL_MAX_CHARS:
        raise SpendError(422, "invalid_sku", f"a node pool is at most {POOL_MAX_CHARS} characters")
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


def check_record_window(hours: Sequence[datetime], *, now: datetime) -> None:
    """Refuse hours that have not ended by ``now`` or that start more than ``RECORD_LOOKBACK`` back (422).

    A future hour would be allocated as if it had been metered, and an old one
    would land late records in days already reported on.
    """
    current = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if hours[-1] + HOUR > current:
        raise SpendError(
            422,
            "invalid_period",
            f"hour_end is at most the current whole hour ({current.isoformat()}): an hour is recorded once over",
        )
    earliest = _ceil_hour(now - RECORD_LOOKBACK)
    if hours[0] < earliest:
        raise SpendError(
            422,
            "invalid_period",
            f"hour_start is at most {RECORD_LOOKBACK.days} days back (from {earliest.isoformat()})",
        )


async def record_hours(*, now: datetime, **body: Any) -> dict[str, Any]:
    """Upsert ``pending`` node-hour rows (the operator command); an hour being allocated or allocated is left alone.

    Only hours that have ended, at most ``RECORD_LOOKBACK`` back. In one
    transaction the hours' rows are read ``FOR UPDATE`` (for the log), then
    each hour is one ``INSERT ... ON CONFLICT DO UPDATE ... WHERE status =
    'pending'``: an hour the allocator claims, or another command inserts,
    meanwhile is never overwritten and never fails the command, and a
    conflict that updates nothing is reported ``already_allocated``. Node
    hours are a platform input, logged rather than audited (the audit log
    needs a tenant), so the log names who recorded what, and the values each
    overwritten hour had before.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.database import async_session_factory
    from core.models.spend_gpu import SpendGpuPoolHour as H

    checked = check_record(**body)
    hours: list[datetime] = checked["hours"]
    check_record_window(hours, now=now)
    table = H.__table__
    created, updated, allocated = 0, 0, []
    overwritten: list[datetime] = []
    async with async_session_factory() as session:
        found = await session.execute(
            select(H.hour_start, H.node_hours, H.source, H.recorded_by)
            .where(H.provider == checked["provider"], H.node_pool == checked["node_pool"], H.hour_start.in_(hours))
            .with_for_update()
        )
        before = {row[0]: tuple(row[1:]) for row in found.all()}
        for hour in hours:
            new_id = uuid.uuid4()
            insert = pg_insert(table).values(
                id=new_id,
                provider=checked["provider"],
                node_pool=checked["node_pool"],
                models=checked["models"],
                hour_start=hour,
                node_hours=checked["node_hours"],
                source=checked["source"],
                status="pending",
                recorded_by=checked["actor"],
            )
            statement = insert.on_conflict_do_update(
                index_elements=["provider", "node_pool", "hour_start"],
                set_={
                    "models": insert.excluded.models,
                    "node_hours": insert.excluded.node_hours,
                    "source": insert.excluded.source,
                    "recorded_by": insert.excluded.recorded_by,
                    "updated_at": now,
                },
                where=table.c.status == "pending",
            ).returning(table.c.id)
            row = (await session.execute(statement)).first()
            if row is None:  # the row exists and is no longer pending
                allocated.append(hour.isoformat())
            elif row[0] == new_id:
                created += 1
            else:
                updated += 1
                overwritten.append(hour)
        await session.commit()
    logger.info(
        "spend_gpu_hours_recorded",
        actor=checked["actor"],
        provider=checked["provider"],
        node_pool=checked["node_pool"],
        models=checked["models"],
        first_hour=hours[0].isoformat(),
        last_hour=hours[-1].isoformat(),
        node_hours=vocab.dec_str(checked["node_hours"]),
        source=checked["source"],
        created=created,
        updated=updated,
        already_allocated=len(allocated),
    )
    for hour in overwritten:
        # None: another command inserted the hour after the read above, so its values were not seen.
        previous = before.get(hour, (None, None, None))
        logger.info(
            "spend_gpu_hour_overwritten",
            actor=checked["actor"],
            provider=checked["provider"],
            node_pool=checked["node_pool"],
            hour_start=hour.isoformat(),
            node_hours=vocab.dec_str(checked["node_hours"]),
            source=checked["source"],
            previous_node_hours=vocab.dec_str(previous[0]),
            previous_source=previous[1],
            previous_recorded_by=previous[2],
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
        "skipped_node_hours": vocab.dec_str(row.skipped_node_hours),
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
