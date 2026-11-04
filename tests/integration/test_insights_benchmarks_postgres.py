# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z117 opt-in benchmarks against real Postgres, called by a NOBYPASSRLS login role.

``insights_benchmark_cohorts`` reads opted-in, live tenants' production rollups
that were measured, though the caller reads none of those rows directly under
forced row-level security. A tenant that did not opt in, a deleted tenant, a
tenant under 100 runs, demo-seeded rows and shadow runs are not counted; a
cohort under 20 tenants is not returned, whatever floors the caller passes.
``compute`` publishes the cohorts without tenant ids; an opted-in tenant's card
places its own figures against them; a tenant that did not opt in sees why.
"""

# Table and function names in the SQL below are fixed literals of this module; values are bound.
# ruff: noqa: S608

from __future__ import annotations

import asyncio
import os
import random
import statistics
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

REVISION = "v6z117_insights_benchmarks"
FUNCTIONS = (
    "public.insights_benchmark_cohorts(date, date, integer, integer)",
    "public.insights_benchmark_participation()",
)
COHORTS = text("SELECT * FROM public.insights_benchmark_cohorts(:f, :t, :n, :r)")
LATENCY = "ARRAY[" + ",".join(["0"] * 12) + "]"

pytestmark = pytest.mark.timeout(900)


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema():
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from alembic import command;"
        "from scripts.alembic_migrate import _alembic_cfg;"
        f"command.upgrade(_alembic_cfg(), '{REVISION}')"
    )
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr[-4000:]


async def _runtime_role(admin) -> tuple[str, object]:
    role = "bench_runtime_" + uuid.uuid4().hex[:12]
    async with admin.begin() as conn:
        # Identifier is a fixed prefix plus UUID hex; never client input.
        await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
        await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        for function in FUNCTIONS:
            await conn.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {function} TO "{role}"')
    engine = create_async_engine(admin.url.set(username=role, password="local-test-password"), poolclass=NullPool)
    return role, engine


async def _drop_role(admin, role: str) -> None:
    async with admin.begin() as conn:
        if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
            await conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            await conn.exec_driver_sql(f'DROP ROLE "{role}"')


async def _tenant(conn, plan: str, *, deleted: bool = False) -> uuid.UUID:
    tid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO tenants(id,name,slug,plan,data_region,settings,deleted_at) "
            "VALUES (:id,:name,:slug,:plan,'IN','{}',:deleted)"
        ),
        {"id": tid, "name": f"Bench {tid.hex[:6]}", "slug": "bench-" + tid.hex, "plan": plan,
         "deleted": datetime.now(UTC) if deleted else None},
    )
    return tid


async def _day(conn, tid: uuid.UUID, day: date, *, runs: int, ok: int, cost: float, hitl: int, ms: int,
               environment: str = "production", quality: str = "measured") -> None:
    for status, n in (("completed", ok), ("failed", runs - ok)):
        if n <= 0:
            continue
        await conn.execute(
            text(
                "INSERT INTO insights_daily(id,tenant_id,day,dims_hash,entrypoint,status,environment,data_quality,"
                "runs,hitl_required,cost_usd,duration_ms_sum,duration_n,latency_buckets) VALUES "
                f"(:id,:t,:day,:h,'agents',:s,:env,:q,:n,:hitl,:cost,:ms,:n,{LATENCY})"
            ),
            {"id": uuid.uuid4(), "t": tid, "day": day, "h": uuid.uuid4().hex + uuid.uuid4().hex, "s": status,
             "env": environment, "q": quality, "n": n, "hitl": hitl if status == "completed" else 0,
             "cost": cost * n / runs, "ms": ms * n},
        )


@pytest.mark.asyncio
async def test_cohorts_count_only_opted_in_measured_production_tenants_and_hold_the_floors(monkeypatch):
    from core import database
    from core.insights import benchmarks

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    # A random far-future window, so the figures are this test's rows only.
    end = date(2090, 1, 1) + timedelta(days=random.randint(0, 3000))
    start, day = end - timedelta(days=29), end - timedelta(days=3)
    role, runtime = None, None
    success: list[float] = []
    try:
        async with admin.begin() as conn:
            await conn.execute(text("DELETE FROM insights_benchmark_optins"))
            free: list[uuid.UUID] = []
            for i in range(21):
                tid = await _tenant(conn, "free")
                ok = 100 + i  # of 200 runs
                await _day(conn, tid, day, runs=200, ok=ok, cost=2.0 + i, hitl=10, ms=1000)
                await conn.execute(text("INSERT INTO insights_benchmark_optins(tenant_id) VALUES (:t)"), {"t": tid})
                free.append(tid)
                success.append(ok / 200 * 100)
            pro = []
            for _ in range(3):
                tid = await _tenant(conn, "pro")
                await _day(conn, tid, day, runs=500, ok=500, cost=1.0, hitl=0, ms=500)
                await conn.execute(text("INSERT INTO insights_benchmark_optins(tenant_id) VALUES (:t)"), {"t": tid})
                pro.append(tid)
            # Not counted: never opted in; deleted; under 100 runs; demo data only; shadow runs only.
            outsider = await _tenant(conn, "free")
            await _day(conn, outsider, day, runs=1000, ok=0, cost=999, hitl=999, ms=99999)
            gone = await _tenant(conn, "free", deleted=True)
            small = await _tenant(conn, "free")
            seeded = await _tenant(conn, "free")
            shadow = await _tenant(conn, "free")
            await _day(conn, gone, day, runs=1000, ok=0, cost=999, hitl=0, ms=1)
            await _day(conn, small, day, runs=99, ok=0, cost=999, hitl=0, ms=1)
            await _day(conn, seeded, day, runs=1000, ok=0, cost=999, hitl=0, ms=1, quality="seeded")
            await _day(conn, shadow, day, runs=1000, ok=0, cost=999, hitl=0, ms=1, environment="shadow")
            # Outside the window.
            await _day(conn, free[0], start - timedelta(days=1), runs=1000, ok=0, cost=999, hitl=0, ms=1)
            for tid in (gone, small, seeded, shadow):
                await conn.execute(text("INSERT INTO insights_benchmark_optins(tenant_id) VALUES (:t)"), {"t": tid})

        role, runtime = await _runtime_role(admin)
        async with runtime.connect() as conn:
            direct = await conn.execute(text("SELECT count(*) FROM insights_daily WHERE tenant_id = :t"),
                                        {"t": free[0]})
            assert direct.scalar_one() == 0
            # A caller asking for smaller floors still gets 20 tenants and 100 runs.
            rows = (await conn.execute(COHORTS, {"f": start, "t": end, "n": 1, "r": 1})).mappings().all()
            participating = (await conn.execute(text("SELECT public.insights_benchmark_participation()"))).scalar()
            with pytest.raises(DBAPIError, match="at most 92 days"):
                await conn.execute(COHORTS, {"f": start - timedelta(days=100), "t": end, "n": 20, "r": 100})
        figures = {(r["cohort"], r["metric"]): r for r in rows}
        assert {cohort for cohort, _ in figures} == {"all", "plan:free"}  # plan:pro has 3 tenants
        everyone = figures[("all", "success_rate")]
        assert everyone["tenants"] == 24
        assert figures[("plan:free", "success_rate")]["tenants"] == 21
        quartiles = statistics.quantiles(success, n=4, method="inclusive")
        free_rate = figures[("plan:free", "success_rate")]
        assert float(free_rate["p50"]) == pytest.approx(quartiles[1])
        assert float(free_rate["p25"]) == pytest.approx(quartiles[0])
        assert float(figures[("plan:free", "approvals_per_100_runs")]["p50"]) == pytest.approx(5.0)
        assert float(figures[("plan:free", "avg_run_ms")]["p50"]) == pytest.approx(1000.0)
        # Every live opted-in tenant, counted or not: 21 + 3 + small, seeded and shadow (not the deleted one).
        assert participating == 27

        # Publish them, then read the cards in tenant sessions.
        factory = async_sessionmaker(runtime, expire_on_commit=False)
        monkeypatch.setattr(database, "async_session_factory", factory)
        monkeypatch.setattr(database, "current_session_factory", lambda: factory)
        summary = await benchmarks.compute(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC))
        assert summary["cohorts"] == 2 and summary["window"] == {"from": start.isoformat(), "to": end.isoformat()}
        async with admin.connect() as conn:
            stored = (await conn.execute(text("SELECT cohort, metric, tenants FROM insights_benchmarks"))).all()
        assert {r[0] for r in stored} == {"all", "plan:free"} and min(r[2] for r in stored) >= 20
        columns = (await _columns(admin))
        assert "tenant_id" not in columns

        now = datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC)
        async with database.get_tenant_session(free[20]) as session:
            mine = await benchmarks.card(session, free[20], now)
        assert mine["opted_in"] is True and mine["counted"] is True and mine["runs"] == 200
        rate = next(m for m in mine["metrics"] if m["key"] == "success_rate")
        assert rate["value"] == 60.0 and [b["cohort"] for b in rate["bands"]] == ["all", "plan:free"]
        assert rate["bands"][1]["position"] == "above_p75"
        async with database.get_tenant_session(outsider) as session:
            theirs = await benchmarks.card(session, outsider, now)
        assert theirs["opted_in"] is False and theirs["metrics"] == [] and "opt" in theirs["hint"]
        async with database.get_tenant_session(pro[0]) as session:
            pro_card = await benchmarks.card(session, pro[0], now)
        pro_rate = next(m for m in pro_card["metrics"] if m["key"] == "success_rate")
        assert [b["cohort"] for b in pro_rate["bands"]] == ["all"]  # its plan's cohort is too small to publish
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role is not None:
            await _drop_role(admin, role)
        await admin.dispose()


async def _columns(admin) -> set[str]:
    async with admin.connect() as conn:
        rows = await conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'insights_benchmarks'")
        )
        return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_opting_in_and_out_is_the_tenants_own_and_audited(monkeypatch):
    from core import database
    from core.insights import benchmarks

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role, runtime = None, None
    try:
        async with admin.begin() as conn:
            alpha, bravo = await _tenant(conn, "free"), await _tenant(conn, "free")
        role, runtime = await _runtime_role(admin)
        factory = async_sessionmaker(runtime, expire_on_commit=False)
        monkeypatch.setattr(database, "async_session_factory", factory)
        monkeypatch.setattr(database, "current_session_factory", lambda: factory)
        actor = uuid.uuid4()
        async with database.get_tenant_session(alpha) as session:
            assert await benchmarks.set_opt_in(session, alpha, actor, True) == {"opted_in": True, "changed": True}
        async with database.get_tenant_session(alpha) as session:
            assert await benchmarks.set_opt_in(session, alpha, actor, True) == {"opted_in": True, "changed": False}
        async with database.get_tenant_session(bravo) as session:
            # Bravo's session sees no other tenant's choice.
            assert await benchmarks.opt_in_row(session, alpha) is None
        async with database.get_tenant_session(alpha) as session:
            assert await benchmarks.set_opt_in(session, alpha, actor, False) == {"opted_in": False, "changed": True}
        async with admin.connect() as conn:
            events = (await conn.execute(text(
                "SELECT event_type FROM audit_log WHERE tenant_id = :t AND event_type LIKE 'insights.benchmarks.%' "
                "ORDER BY created_at"), {"t": alpha})).scalars().all()
            left = (await conn.execute(text("SELECT count(*) FROM insights_benchmark_optins WHERE tenant_id = :t"),
                                       {"t": alpha})).scalar_one()
        assert events == ["insights.benchmarks.opt_in", "insights.benchmarks.opt_out"] and left == 0
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role is not None:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_the_migration_rolls_back_and_reapplies():
    def run(target: str, verb: str) -> subprocess.CompletedProcess:
        script = (
            "import sys; sys.path.insert(0, '.');"
            "from alembic import command;"
            "from scripts.alembic_migrate import _alembic_cfg;"
            f"command.{verb}(_alembic_cfg(), '{target}')"
        )
        return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        down = await asyncio.to_thread(run, "v6z116_insights_export_formats", "downgrade")
        assert down.returncode == 0, down.stderr[-4000:]
        async with admin.connect() as conn:
            gone = (await conn.execute(text(
                "SELECT to_regclass('public.insights_benchmarks') IS NULL "
                "AND to_regprocedure('public.insights_benchmark_participation()') IS NULL"))).scalar()
        assert gone is True
        up = await asyncio.to_thread(run, REVISION, "upgrade")
        assert up.returncode == 0, up.stderr[-4000:]
        async with admin.connect() as conn:
            owner = (await conn.execute(text(
                "SELECT r.rolname, p.prosecdef FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
                "WHERE p.oid = to_regprocedure('public.insights_benchmark_cohorts(date, date, integer, integer)')"
            ))).one()
            public = (await conn.execute(text(
                "SELECT has_function_privilege('public', "
                "'public.insights_benchmark_cohorts(date, date, integer, integer)', 'EXECUTE')"))).scalar()
        assert tuple(owner) == ("agenticorg_insights_platform", True) and public is False
    finally:
        await admin.dispose()
