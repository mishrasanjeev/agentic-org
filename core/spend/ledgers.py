# SPDX-License-Identifier: Apache-2.0
"""How usage records relate to the existing ledgers: a read-only comparison, and backfill from gateway rows.

Usage records are a new, separate source; spend never writes the existing
ledgers. Quantities are the agreement contract: a day's LLM tokens of agent
runs should match ``agent_cost_ledger``, with the enumerated exceptions in
``NOTES``. Amounts differ by design (the ledgers carry a blended estimate,
usage records carry rate-card prices), so only usage amounts are reconciled.

**Backfill** recreates model-call records from ``model_gateway_records``
with the hook's own keys (the call hash is taken over fields the gateway row
stores). A call that already has any record is skipped whole: a fully cached
prompt, which the hook writes as one cached-input record, must not get a
second, uncached record from a gateway row that has no cache details.
Gateway rows exist only while the model gateway and its records are on, and
are pruned after their retention period; ``GET /spend/status`` says whether
backfill has a source.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import Date, and_, case, cast, func, or_, select

from core.spend import clock, vocab
from core.spend.access import ReadView

logger = structlog.get_logger()

MAX_DAYS = 31
PAGE = 1000
WRITE_BATCH = 200
AGENT_USE_CASES = ("agent_run", "agent_resume")
NOTES = (
    "Explainer, feedback-analyser, SOP-parser and workflow re-planner calls are metered but never counted "
    "in a run's tokens.",
    "A run that crosses midnight: each call is dated by its own time, the ledger by the run's end.",
    "A run that raises reports 0 tokens to the ledger while its finished calls are metered.",
    "A resumed run reports whole-thread usage but writes no ledger row.",
    "A2A, MCP, workflow and approval-resume runs write no ledger row.",
    "Providers that report extra output separately (thinking tokens on the router path) are metered in full.",
    "Ledgers bucket by the UTC date; usage records by the reporting date. Totals differ only at the edges.",
    "A router primary timeout writes an estimated input record that no ledger has.",
)


def backfill_source() -> str:
    """``model_gateway_records`` when gateway rows are being written, else ``none``."""
    from core.config import settings

    on = bool(getattr(settings, "model_gateway_enabled", False)) and bool(
        getattr(settings, "model_gateway_records_enabled", False)
    )
    return "model_gateway_records" if on else "none"


async def visible_agents(session: Any, tenant_id: uuid.UUID, view: ReadView) -> set[uuid.UUID] | None:
    """The agent ids ``view`` may see (``None`` = no filter)."""
    if view.agent_clause is None:
        return None
    from core.models.agent import Agent

    rows = (await session.execute(select(Agent.id).where(Agent.tenant_id == tenant_id, view.agent_clause))).all()
    return {row[0] for row in rows}


def _agent_filter(column: Any, visible: set[uuid.UUID] | None, *, as_text: bool = False) -> Any:
    if visible is None:
        return None
    ids = sorted((str(v) for v in visible) if as_text else visible, key=str)
    return or_(column.is_(None), column.in_(ids)) if ids else column.is_(None)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def _bucket(application: str) -> str:
    return application if application in ("agents", "chat") else "other"


async def compare(tenant_id: uuid.UUID, *, start: date, end: date, view: ReadView) -> dict[str, Any]:
    """Usage tokens, calls and USD amounts per day beside the existing ledgers (at most 31 days; read only)."""
    from core.database import get_tenant_session
    from core.models.agent import AgentCostLedger as A
    from core.models.finops_ledger import FinopsCostLedger as F
    from core.models.model_gateway_record import ModelGatewayRecord as G
    from core.models.spend_usage import SpendUsageRollup as U
    from core.spend.rollups import check_range

    check_range(start, end, max_days=MAX_DAYS)
    utc_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    utc_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    async with get_tenant_session(tenant_id) as session:
        visible = await visible_agents(session, tenant_id, view)
        usage_where = [U.tenant_id == tenant_id, U.day >= start, U.day <= end, U.usage_type == "llm_tokens"]
        agent_where = [A.tenant_id == tenant_id, A.period_date >= start, A.period_date <= end]
        finops_where = [F.tenant_id == tenant_id, F.period_date >= start, F.period_date <= end]
        gateway_where = [G.tenant_id == tenant_id, G.created_at >= utc_start, G.created_at < utc_end]
        for where, clause in (
            (usage_where, _agent_filter(U.agent_id, visible)),
            (agent_where, _agent_filter(A.agent_id, visible)),
            (finops_where, _agent_filter(F.agent_id, visible)),
            (gateway_where, _agent_filter(G.agent_id, visible, as_text=True)),
        ):
            if clause is not None:
                where.append(clause)
        usage = (
            await session.execute(
                select(
                    U.day,
                    U.application,
                    U.currency,
                    func.sum(U.quantity),
                    func.sum(U.call_count),
                    func.sum(U.amount),
                )
                .where(*usage_where)
                .group_by(U.day, U.application, U.currency)
            )
        ).all()
        agent_rows = (
            await session.execute(
                select(A.period_date, func.sum(A.token_count), func.sum(A.cost_usd), func.sum(A.task_count))
                .where(*agent_where)
                .group_by(A.period_date)
            )
        ).all()
        finops_rows = (
            await session.execute(
                select(F.period_date, func.sum(F.tokens), func.sum(F.cost_usd), func.sum(F.calls))
                .where(*finops_where)
                .group_by(F.period_date)
            )
        ).all()
        gateway_day = cast(func.timezone("UTC", G.created_at), Date)
        gateway_rows = (
            await session.execute(
                select(gateway_day, func.count(), func.sum(case((G.outcome == "completed", G.tokens), else_=0)))
                .where(*gateway_where)
                .group_by(gateway_day)
            )
        ).all()
    return comparison(usage, agent_rows, finops_rows, gateway_rows, start=start, end=end)


def _day(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


def comparison(
    usage: Sequence[Sequence[Any]],
    agent_rows: Sequence[Sequence[Any]],
    finops_rows: Sequence[Sequence[Any]],
    gateway_rows: Sequence[Sequence[Any]],
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """The per-day comparison (pure)."""
    days: dict[date, dict[str, Any]] = {}

    def entry(day: date) -> dict[str, Any]:
        return days.setdefault(
            day,
            {
                "tokens": {"agents": Decimal("0"), "chat": Decimal("0"), "other": Decimal("0")},
                "calls": 0,
                "amount_usd": Decimal("0"),
                "agent_cost_ledger": None,
                "finops_cost_ledger": None,
                "model_gateway_records": None,
            },
        )

    for day, application, currency, quantity, calls, amount in usage:
        item = entry(_day(day))
        item["tokens"][_bucket(application)] += _dec(quantity)
        item["calls"] += int(calls or 0)
        if (currency or "").strip() == "USD":
            item["amount_usd"] += _dec(amount)
    for day, tokens, cost, tasks in agent_rows:
        entry(_day(day))["agent_cost_ledger"] = {
            "tokens": int(tokens or 0),
            "cost_usd": _dec(cost),
            "tasks": int(tasks or 0),
        }
    for day, tokens, cost, calls in finops_rows:
        entry(_day(day))["finops_cost_ledger"] = {
            "tokens": int(tokens or 0),
            "cost_usd": _dec(cost),
            "calls": int(calls or 0),
        }
    for day, calls, tokens in gateway_rows:
        entry(_day(day))["model_gateway_records"] = {"calls": int(calls or 0), "completed_tokens": int(tokens or 0)}
    out = []
    for day in sorted(d for d in days if start <= d <= end):
        item = days[day]
        ledger = item["agent_cost_ledger"]
        ledger_tokens = ledger["tokens"] if ledger else 0
        ledger_cost = ledger["cost_usd"] if ledger else Decimal("0")
        ratio = (item["amount_usd"] / ledger_cost) if ledger_cost else None
        out.append(
            {
                "day": day.isoformat(),
                "usage": {
                    "tokens": {k: vocab.dec_str(v) for k, v in item["tokens"].items()},
                    "calls": item["calls"],
                    "amount_usd": vocab.dec_str(item["amount_usd"]),
                },
                "agent_cost_ledger": _ledger_json(ledger),
                "finops_cost_ledger": _ledger_json(item["finops_cost_ledger"]),
                "model_gateway_records": item["model_gateway_records"],
                "token_delta": vocab.dec_str(item["tokens"]["agents"] - ledger_tokens),
                "amount_ratio": vocab.dec_str(ratio.quantize(vocab.SHARE_QUANT)) if ratio is not None else None,
            }
        )
    return {"days": out, "notes": list(NOTES), "backfill_source": backfill_source()}


def _ledger_json(ledger: dict[str, Any] | None) -> dict[str, Any] | None:
    if ledger is None:
        return None
    return {k: vocab.dec_str(v) if isinstance(v, Decimal) else v for k, v in ledger.items()}


# ---------------------------------------------------------------- backfill


@dataclass(frozen=True)
class GatewayCall:
    """The fields of a ``model_gateway_records`` row a usage event is built from."""

    tenant_id: str
    correlation_id: str
    created_at: datetime
    provider: str
    model: str
    outcome: str
    tokens: int
    input_tokens: int | None
    output_tokens: int | None
    agent_id: str | None
    use_case: str


def gateway_call(row: Any) -> GatewayCall:
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    return GatewayCall(
        tenant_id=str(row.tenant_id),
        correlation_id=str(row.correlation_id),
        created_at=created,
        provider=str(row.provider),
        model=str(row.model),
        outcome=str(row.outcome),
        tokens=int(row.tokens or 0),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        agent_id=row.agent_id,
        use_case=str(row.use_case or ""),
    )


def backfill_events(call: GatewayCall) -> list[Any]:
    """The usage events a gateway row gives (the hook's keys; ``origin="backfill"`` hints)."""
    from core.spend.meter import plan_model_call
    from core.spend.resolver import Hints

    hints = Hints(
        agent_id=call.agent_id,
        agent_version=None,
        application="agents" if call.use_case in AGENT_USE_CASES else "api",
        default_use_case="completion",
        workflow_id=None,
        workflow_run_id=None,
        run_id=None,
        initiating_user_id=None,
        origin="backfill",
    )
    return plan_model_call(call, hints=hints).events


async def _page(session: Any, tenant_id: uuid.UUID, start: datetime, end: datetime, last: Any) -> list[Any]:
    from core.models.model_gateway_record import ModelGatewayRecord as G

    conditions = [G.tenant_id == tenant_id, G.created_at >= start, G.created_at < end]
    if last is not None:
        conditions.append(or_(G.created_at > last[0], and_(G.created_at == last[0], G.id > last[1])))
    statement = select(G).where(*conditions).order_by(G.created_at, G.id).limit(PAGE)
    return list((await session.execute(statement)).scalars().all())


async def _existing_refs(
    session: Any, tenant_id: uuid.UUID, refs: Sequence[str], first: datetime, last: datetime
) -> set[str]:
    from core.models.spend_usage import SpendUsageRecord as R

    if not refs:
        return set()
    statement = (
        select(R.source_ref)
        .where(
            R.tenant_id == tenant_id,
            R.event_time >= first,
            R.event_time <= last,
            R.source_ref.in_(sorted(set(refs))),
        )
        .distinct()
    )
    return {str(row[0]) for row in (await session.execute(statement)).all()}


async def backfill_model_calls(
    tenant_id: uuid.UUID, *, start: date, end: date, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Write the usage records gateway rows of reporting days ``[start, end]`` (at most 31) still lack."""
    from core.database import get_tenant_session
    from core.spend import audit, meter
    from core.spend.rollups import check_range

    check_range(start, end, max_days=MAX_DAYS)
    stamp = now or clock.now_utc()
    zone = clock.reporting_zone()
    window_start = clock.day_bounds(start, zone)[0]
    window_end = clock.day_bounds(end, zone)[1]
    totals = {"scanned": 0, "written": 0, "skipped_calls": 0, "duplicates": 0, "unpriced": 0}
    providers: set[str] = set()
    last: tuple[datetime, Any] | None = None
    while True:
        async with get_tenant_session(tenant_id) as session:
            rows = await _page(session, tenant_id, window_start, window_end, last)
            if not rows:
                break
            calls = [gateway_call(row) for row in rows]
            planned = [(call, backfill_events(call)) for call in calls]
            refs = [events[0].source_ref for _call, events in planned if events]
            first = min(c.created_at for c in calls)
            newest = max(c.created_at for c in calls)
            existing = await _existing_refs(session, tenant_id, refs, first, newest)
        totals["scanned"] += len(rows)
        events = []
        for _call, call_events in planned:
            if not call_events:
                continue
            if call_events[0].source_ref in existing:
                totals["skipped_calls"] += 1
                continue
            events.extend(call_events)
        for index in range(0, len(events), WRITE_BATCH):
            batch = events[index : index + WRITE_BATCH]
            async with get_tenant_session(tenant_id) as session:
                result = await meter.write_events(session, tenant_id, batch, lock="wait", now=stamp)
            totals["written"] += result.written
            totals["duplicates"] += result.duplicates
            totals["unpriced"] += result.unpriced
            providers.update(e.provider for e in batch)
        last = (rows[-1].created_at, rows[-1].id)
        if len(rows) < PAGE:
            break
    async with get_tenant_session(tenant_id) as session:
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=actor,
                action="usage.backfill",
                resource_type="spend_usage_record",
                resource_id=f"{start.isoformat()}..{end.isoformat()}",
                details={**totals, "start": start, "end": end},
                now=stamp,
            )
        )
        await meter.mark_commitments_for_replay(session, tenant_id, sorted(providers))
    logger.info("spend_usage_backfilled", **totals)
    return totals
