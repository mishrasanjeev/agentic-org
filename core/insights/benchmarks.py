# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Opt-in cross-tenant benchmarks: an organisation's own figures against cohorts of at least 20 that opted in.

An administrator opts the organisation in or out (``insights_benchmark_optins``;
audited). Nightly, ``compute`` asks ``public.insights_benchmark_cohorts``
(migration v6z117) for the last 30 complete UTC days and replaces the
published figures in ``insights_benchmarks``. That function counts only
opted-in, live tenants' production rollups that were measured (never demo
data), only tenants with at least 100 runs in the window, and returns only
cohorts of at least 20 tenants: the floors are enforced in the database. It
returns percentiles, never a tenant's figure or id.

An opted-in organisation sees, for each benchmark, its own figure over the same
window (computed from its own rollups with the same definitions) against the
25th, 50th and 75th percentiles of ``all`` and of its plan's cohort. An
organisation that has not opted in sees why the card is empty: benchmarks are
give to get.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text

logger = structlog.get_logger()

WINDOW_DAYS = 30
MIN_TENANTS = 20
MIN_RUNS = 100
STATEMENT_TIMEOUT = "30s"
COHORT_ALL = "all"
READERS = frozenset({"cfo", "coo"})

COHORTS_SQL = text(
    "SELECT cohort, metric, p25, p50, p75, tenants "
    "FROM public.insights_benchmark_cohorts(:p_from, :p_to, :p_min_tenants, :p_min_runs)"
)
PARTICIPATION_SQL = text("SELECT public.insights_benchmark_participation()")


@dataclass(frozen=True)
class Benchmark:
    key: str
    title: str
    unit: str
    # True when a higher figure is better, False when lower is, None when neither.
    higher_is_better: bool | None
    definition: str


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark("success_rate", "Run success rate", "percent", True, "Completed runs as a share of all production runs."),
    Benchmark(
        "cost_per_run_usd",
        "Cost per run",
        "usd",
        False,
        "Recorded model cost of production runs divided by their number, in USD.",
    ),
    Benchmark(
        "approvals_per_100_runs",
        "Approvals per 100 runs",
        "count",
        None,
        "Runs that asked a person to approve, per 100 production runs.",
    ),
    Benchmark(
        "avg_run_ms", "Average run time", "duration_ms", False, "Mean duration of production runs that recorded one."
    ),
)
KEYS = tuple(b.key for b in BENCHMARKS)


def may_read(role: str, *, is_admin: bool) -> bool:
    return is_admin or (role or "").lower() in READERS


def window(today: date) -> tuple[date, date]:
    """The last ``WINDOW_DAYS`` complete UTC days before ``today``."""
    end = today - timedelta(days=1)
    return end - timedelta(days=WINDOW_DAYS - 1), end


def plan_cohort(plan: str | None) -> str:
    return f"plan:{plan or 'none'}"


def position(value: float | None, p25: float, p50: float, p75: float) -> str | None:
    """Where a figure sits against the band: ``below_p25``, ``p25_p50``, ``p50_p75`` or ``above_p75``."""
    if value is None:
        return None
    if value < p25:
        return "below_p25"
    if value < p50:
        return "p25_p50"
    if value <= p75:
        return "p50_p75"
    return "above_p75"


# ------------------------------------------------------------------ nightly


async def compute(now: datetime | None = None) -> dict[str, Any]:
    """Replace the published cohort figures with the database function's answer for the last 30 complete days."""
    from sqlalchemy import delete

    from core.database import get_platform_session
    from core.models.insights import InsightsBenchmark

    moment = now or datetime.now(UTC)
    start, end = window(moment.astimezone(UTC).date())
    async with get_platform_session() as session:
        await session.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": STATEMENT_TIMEOUT})
        params = {"p_from": start, "p_to": end, "p_min_tenants": MIN_TENANTS, "p_min_runs": MIN_RUNS}
        rows = (await session.execute(COHORTS_SQL, params)).mappings().all()
        await session.execute(delete(InsightsBenchmark))
        for row in rows:
            if row["metric"] not in KEYS or int(row["tenants"]) < MIN_TENANTS:
                continue
            session.add(
                InsightsBenchmark(
                    id=uuid.uuid4(),
                    cohort=str(row["cohort"])[:64],
                    metric=str(row["metric"]),
                    window_from=start,
                    window_to=end,
                    p25=Decimal(row["p25"]),
                    p50=Decimal(row["p50"]),
                    p75=Decimal(row["p75"]),
                    tenants=int(row["tenants"]),
                    computed_at=moment,
                )
            )
        await session.commit()
    cohorts = sorted({str(r["cohort"]) for r in rows})
    logger.info("insights_benchmarks_computed", cohorts=len(cohorts), figures=len(rows))
    return {"window": {"from": start.isoformat(), "to": end.isoformat()}, "cohorts": len(cohorts), "figures": len(rows)}


async def participation(session: Any) -> int:
    """How many live organisations opted in (the database function; a count only)."""
    return int((await session.execute(PARTICIPATION_SQL)).scalar_one() or 0)


# ------------------------------------------------------------------ opt-in


async def opt_in_row(session: Any, tenant_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.insights import InsightsBenchmarkOptin

    query = select(InsightsBenchmarkOptin).where(InsightsBenchmarkOptin.tenant_id == tenant_id)
    return (await session.execute(query)).scalar_one_or_none()


async def set_opt_in(session: Any, tenant_id: uuid.UUID, actor: uuid.UUID, opt_in: bool) -> dict[str, Any]:
    """Opt the organisation in or out; a change writes a signed audit row (repeating the current choice does not)."""
    from core.insights.audit import audit_entry
    from core.models.insights import InsightsBenchmarkOptin

    row = await opt_in_row(session, tenant_id)
    changed = (row is None) == opt_in
    if changed:
        if opt_in:
            session.add(InsightsBenchmarkOptin(tenant_id=tenant_id, opted_in_by=actor))
        else:
            await session.delete(row)
        session.add(
            audit_entry(
                tenant_id,
                event_type="insights.benchmarks.opt_in" if opt_in else "insights.benchmarks.opt_out",
                actor_id=str(actor),
                action="update",
                resource_type="insights_benchmarks",
                resource_id=str(tenant_id),
                details={"opt_in": opt_in},
            )
        )
        await session.flush()
    return {"opted_in": opt_in, "changed": changed}


# ------------------------------------------------------------------ the card


async def own_figures(
    session: Any, tenant_id: uuid.UUID, start: date, end: date
) -> tuple[int, dict[str, float | None]]:
    """The organisation's runs and figures over the window, with the database function's definitions."""
    from sqlalchemy import func, select

    from core.models.insights import InsightsDaily as D

    query = select(
        func.coalesce(func.sum(D.runs), 0),
        func.coalesce(func.sum(D.runs).filter(D.status == "completed"), 0),
        func.coalesce(func.sum(D.cost_usd), 0),
        func.coalesce(func.sum(D.hitl_required), 0),
        func.coalesce(func.sum(D.duration_ms_sum), 0),
        func.coalesce(func.sum(D.duration_n), 0),
    ).where(
        D.tenant_id == tenant_id,
        D.day >= start,
        D.day <= end,
        D.environment == "production",
        D.data_quality == "measured",
    )
    runs, ok, cost, hitl, dur_sum, dur_n = (await session.execute(query)).one()
    runs = int(runs or 0)
    if runs <= 0:
        return 0, dict.fromkeys(KEYS)
    return runs, {
        "success_rate": round(float(ok) / runs * 100, 2),
        "cost_per_run_usd": round(float(cost) / runs, 6),
        "approvals_per_100_runs": round(float(hitl) / runs * 100, 2),
        "avg_run_ms": round(float(dur_sum) / float(dur_n), 1) if dur_n else None,
    }


async def card(session: Any, tenant_id: uuid.UUID, now: datetime | None = None) -> dict[str, Any]:
    from sqlalchemy import select

    from core.models.insights import InsightsBenchmark
    from core.models.tenant import Tenant

    moment = now or datetime.now(UTC)
    start, end = window(moment.astimezone(UTC).date())
    base: dict[str, Any] = {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "min_tenants": MIN_TENANTS,
        "min_runs": MIN_RUNS,
        "definitions": [
            {
                "key": b.key,
                "title": b.title,
                "unit": b.unit,
                "higher_is_better": b.higher_is_better,
                "definition": b.definition,
            }
            for b in BENCHMARKS
        ],
    }
    participating = await participation(session)
    if await opt_in_row(session, tenant_id) is None:
        return {
            **base,
            "opted_in": False,
            "participating": participating,
            "metrics": [],
            "hint": "Benchmarks are shared only between organisations that opt in. An administrator can opt "
            "this organisation in; its figures then count towards cohorts of at least "
            f"{MIN_TENANTS} organisations and it sees where it stands.",
        }
    plan = (await session.execute(select(Tenant.plan).where(Tenant.id == tenant_id))).scalar_one_or_none()
    cohorts = [COHORT_ALL, plan_cohort(plan)]
    published = (
        (await session.execute(select(InsightsBenchmark).where(InsightsBenchmark.cohort.in_(cohorts)))).scalars().all()
    )
    by_metric: dict[str, list[Any]] = {}
    for row in published:
        by_metric.setdefault(row.metric, []).append(row)
    runs, own = await own_figures(session, tenant_id, start, end)
    counted = runs >= MIN_RUNS
    metrics = []
    for bench in BENCHMARKS:
        bands = []
        for row in sorted(by_metric.get(bench.key, []), key=lambda r: cohorts.index(r.cohort)):
            p25, p50, p75 = float(row.p25), float(row.p50), float(row.p75)
            bands.append(
                {
                    "cohort": row.cohort,
                    "p25": p25,
                    "p50": p50,
                    "p75": p75,
                    "tenants": int(row.tenants),
                    "position": position(own[bench.key], p25, p50, p75),
                    "computed_at": row.computed_at.isoformat() if row.computed_at else None,
                }
            )
        metrics.append(
            {
                "key": bench.key,
                "title": bench.title,
                "unit": bench.unit,
                "higher_is_better": bench.higher_is_better,
                "value": own[bench.key],
                "bands": bands,
            }
        )
    hint = None
    if not published:
        hint = (
            f"No cohort has {MIN_TENANTS} organisations yet ({participating} opted in), so nothing is "
            "published. Your figures are shown on their own."
        )
    elif not counted:
        hint = (
            f"This organisation had {runs} production runs in the window; it counts towards a cohort from {MIN_RUNS}."
        )
    return {
        **base,
        "opted_in": True,
        "participating": participating,
        "runs": runs,
        "counted": counted,
        "metrics": metrics,
        "hint": hint,
        "data_quality": "measured",
    }
