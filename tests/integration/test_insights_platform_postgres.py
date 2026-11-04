# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z107 platform aggregates against real Postgres, called by a NOBYPASSRLS login role.

The runtime role gets what ``scripts/create_app_role.sql`` gives the
application (row DML on every table, EXECUTE on the allow-listed functions)
and no BYPASSRLS: under forced row-level security and without a tenant
context it reads none of the tenants' ``insights_daily`` rows directly, yet
the two SECURITY DEFINER functions return every tenant's aggregates. The
aggregate role reads only the granted columns; nobody else may execute the
functions; ranges over 92 days, reversed or open are refused. The installer
test creates the functions as a non-superuser, replays, rolls back and
re-applies, and proves the installer keeps no membership of the role.
"""

# Table and function names in the SQL below are fixed literals of this module; values are bound.
# ruff: noqa: S608

from __future__ import annotations

import asyncio
import importlib
import os
import random
import subprocess
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

REVISION = "v6z107_insights_platform"
AGGREGATE_ROLE = "agenticorg_insights_platform"
FUNCTIONS = ("public.insights_platform_daily(date, date)", "public.insights_platform_tenants(date, date)")
LATENCY = "{0,0,0,0,0,0,0,0,0,0,0,0}"


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema():
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    # Upgrade to this revision at least (a database already past it is left as it is).
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


def _base_day() -> date:
    """A random far-future day, so the platform totals are this test's rows only."""
    return date(2040, 1, 1) + timedelta(days=random.randint(0, 20000))


async def _runtime_role(admin) -> tuple[str, object]:
    """A NOBYPASSRLS login role with the application's grants."""
    role = "plat_runtime_" + uuid.uuid4().hex[:12]
    async with admin.begin() as conn:
        # Identifier is a fixed prefix plus UUID hex; never client input.
        await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
        await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        for fn in FUNCTIONS:
            await conn.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {fn} TO "{role}"')
    engine = create_async_engine(admin.url.set(username=role, password="local-test-password"), poolclass=NullPool)
    return role, engine


async def _drop_role(admin, role: str) -> None:
    async with admin.begin() as conn:
        if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
            await conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            await conn.exec_driver_sql(f'DROP ROLE "{role}"')


async def _daily(conn, tenant, day, status, runs, tokens, cost) -> None:
    await conn.execute(
        text(
            "INSERT INTO insights_daily(id,tenant_id,day,dims_hash,entrypoint,status,environment,data_quality,"
            "runs,tokens_total,cost_usd,duration_ms_sum,duration_n,latency_buckets,agent_ref) "
            "VALUES (:id,:t,:day,:h,'agents',:status,'production','measured',:runs,:tokens,:cost,1000,:runs,"
            f"'{LATENCY}','private-agent-ref')"
        ),
        {"id": uuid.uuid4(), "t": tenant, "day": day, "h": uuid.uuid4().hex, "status": status, "runs": runs,
         "tokens": tokens, "cost": cost},
    )


async def _rollup(conn, tenant, day, status) -> None:
    await conn.execute(
        text("INSERT INTO insights_rollup_state(id,tenant_id,day,status,detail) VALUES (:id,:t,:day,:s,'private')"),
        {"id": uuid.uuid4(), "t": tenant, "day": day, "s": status},
    )


@pytest.mark.asyncio
async def test_functions_return_cross_tenant_aggregates_to_a_nobypassrls_role(monkeypatch):
    from core.models.agent import Agent

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    tenant_a, tenant_b, tenant_gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tenants = (tenant_a, tenant_b, tenant_gone)
    base = _base_day()
    role, runtime = None, None
    try:
        async with admin.begin() as conn:
            for tenant, name, plan in (
                (tenant_a, "Synthetic Alpha Traders", "enterprise"),
                (tenant_b, "Synthetic Beta Clinic", "free"),
                (tenant_gone, "Synthetic Closed Shop", "free"),
            ):
                await conn.execute(text(
                    "INSERT INTO tenants(id,name,slug,plan,data_region,settings) "
                    "VALUES (:id,:name,:slug,:plan,'IN','{}')"
                ), {"id": tenant, "name": name, "slug": "plat-" + tenant.hex, "plan": plan})
            await conn.execute(text("UPDATE tenants SET deleted_at=now() WHERE id=:t"), {"t": tenant_gone})
            # Tenant A: two days, one failure-bearing; tenant B: one day; the closed tenant: one day.
            await _daily(conn, tenant_a, base, "completed", 8, 800, Decimal("1.250000"))
            await _daily(conn, tenant_a, base, "failed", 2, 100, Decimal("0.250000"))
            await _daily(conn, tenant_a, base + timedelta(days=1), "completed", 5, 500, Decimal("0.500000"))
            await _daily(conn, tenant_b, base + timedelta(days=1), "completed", 3, 300, Decimal("0.300000"))
            await _daily(conn, tenant_b, base + timedelta(days=1), "failed", 1, 10, Decimal("0.010000"))
            await _daily(conn, tenant_gone, base, "completed", 4, 40, Decimal("0.040000"))
            # Out of range for the queries below.
            await _daily(conn, tenant_b, base + timedelta(days=40), "completed", 99, 990, Decimal("9.900000"))
            # Rollup history: A reconciled for 16 nights up to base+1; B reconciled for 3 nights,
            # then a gap, then reconciled nights before it; the latest of neither is a mismatch.
            for offset in range(-14, 2):
                await _rollup(conn, tenant_a, base + timedelta(days=offset), "reconciled")
            for offset in (-5, -4, -1, 0, 1):
                await _rollup(conn, tenant_b, base + timedelta(days=offset), "reconciled")
        async with async_sessionmaker(admin, expire_on_commit=False)() as session:
            for tenant, status in ((tenant_a, "active"), (tenant_a, "active"), (tenant_a, "paused"),
                                   (tenant_b, "active")):
                session.add(Agent(
                    id=uuid.uuid4(), tenant_id=tenant, name="Synthetic agent", agent_type="ap_processor",
                    domain="finance", system_prompt_ref="synthetic", hitl_condition="never", status=status,
                ))
            await session.commit()

        role, runtime = await _runtime_role(admin)
        start, end = base, base + timedelta(days=1)
        async with runtime.connect() as conn:
            # Forced RLS, no tenant context: the runtime reads no tenant's rows directly.
            direct = await conn.execute(text("SELECT count(*) FROM insights_daily WHERE tenant_id IN (:a,:b)"),
                                        {"a": tenant_a, "b": tenant_b})
            assert direct.scalar_one() == 0
            assert (await conn.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles "
                                             "WHERE rolname=current_user"))).scalar_one() is False
            daily = (await conn.execute(text("SELECT * FROM public.insights_platform_daily(:f,:t)"),
                                        {"f": start, "t": end})).mappings().all()
            rows = {
                r["tenant_id"]: r
                for r in (await conn.execute(text("SELECT * FROM public.insights_platform_tenants(:f,:t)"),
                                             {"f": start, "t": end})).mappings().all()
            }
        assert [(r["day"], r["tenants_active"], r["runs"], r["failed"], r["tokens"]) for r in daily] == [
            (base, 2, 14, 2, 940),
            (base + timedelta(days=1), 2, 9, 1, 810),
        ]
        assert [r["cost_usd"] for r in daily] == [Decimal("1.540000"), Decimal("0.810000")]

        a, b = rows[tenant_a], rows[tenant_b]
        assert tenant_gone not in rows  # a deleted tenant is not listed
        assert (a["tenant_name"], a["plan"], a["runs"], a["failed"], a["active_agents"]) == (
            "Synthetic Alpha Traders", "enterprise", 15, 2, 2)
        assert a["cost_usd"] == Decimal("2.000000")
        assert (a["last_active_day"], a["reconciled_nights"], a["last_rollup_status"]) == (end, 16, "reconciled")
        assert (b["runs"], b["failed"], b["active_agents"], b["cost_usd"]) == (4, 1, 1, Decimal("0.310000"))
        # B's last active day is on or before the range end, never the out-of-range day after it.
        assert b["last_active_day"] == end
        assert (b["reconciled_nights"], b["last_rollup_status"]) == (3, "reconciled")
        assert set(rows[tenant_a].keys()) == {
            "tenant_id", "tenant_name", "plan", "runs", "failed", "cost_usd", "active_agents",
            "last_active_day", "reconciled_nights", "last_rollup_status",
        }

        # Today (still being rolled) and yesterday (not yet due to reconcile) do not reset the streak;
        # a day older than that which never reconciled does.
        async with admin.begin() as conn:
            await _rollup(conn, tenant_b, end + timedelta(days=1), "rolled")
            await _rollup(conn, tenant_b, end + timedelta(days=2), "rolled")
        async with runtime.connect() as conn:
            streaks = [
                tuple((await conn.execute(text(
                    "SELECT reconciled_nights, last_rollup_status FROM public.insights_platform_tenants(:f,:t) "
                    "WHERE tenant_id=:b"), {"f": start, "t": end + timedelta(days=days), "b": tenant_b})).one())
                for days in (1, 2, 3)
            ]
        assert streaks == [(3, "reconciled"), (3, "reconciled"), (0, "rolled")]

        # A mismatch on the latest night resets the streak.
        async with admin.begin() as conn:
            await _rollup(conn, tenant_a, base + timedelta(days=2), "mismatch")
        async with runtime.connect() as conn:
            later = (await conn.execute(text(
                "SELECT reconciled_nights, last_rollup_status FROM public.insights_platform_tenants(:f,:t) "
                "WHERE tenant_id=:a"), {"f": start, "t": end + timedelta(days=1), "a": tenant_a})).one()
        assert tuple(later) == (0, "mismatch")

        # The application's own reader returns the same figures through a runtime session.
        from core.insights import platform

        async with async_sessionmaker(runtime, expire_on_commit=False)() as session:
            series = await platform.platform_daily(session, start, end)
            listed = await platform.platform_tenants(session, start, end)
        assert [s["runs"] for s in series] == [14, 9]
        mine = {row["tenant_id"]: row for row in listed if row["tenant_id"] in {str(tenant_a), str(tenant_b)}}
        assert mine[str(tenant_a)]["failure_rate"] == pytest.approx(2 / 15, abs=1e-4)
        assert mine[str(tenant_a)]["gate_met"] is True and mine[str(tenant_b)]["gate_met"] is False
        totals = platform.totals(series, list(mine.values()))
        assert (totals["runs"], totals["failed"], totals["tokens"], totals["tenants_active"]) == (23, 3, 1750, 2)
    finally:
        if runtime is not None:
            await runtime.dispose()
        async with admin.begin() as conn:
            for table in ("insights_daily", "insights_rollup_state", "agents"):
                await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ts)"), {"ts": list(tenants)})
            await conn.execute(text("DELETE FROM tenants WHERE id = ANY(:ts)"), {"ts": list(tenants)})
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_the_aggregate_role_reads_only_its_columns_and_nobody_else_executes():
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role, runtime = None, None
    try:
        async with admin.connect() as conn:
            attrs = (await conn.execute(text(
                "SELECT rolcanlogin, rolsuper, rolbypassrls, rolinherit, rolcreaterole FROM pg_roles WHERE rolname=:r"
            ), {"r": AGGREGATE_ROLE})).one()
            assert tuple(attrs) == (False, False, False, False, False)
            for table, column, allowed in (
                ("insights_daily", "runs", True),
                ("insights_daily", "cost_usd", True),
                ("insights_daily", "agent_ref", False),
                ("insights_daily", "agent_id", False),
                ("insights_rollup_state", "status", True),
                ("insights_rollup_state", "detail", False),
                ("tenants", "name", True),
                ("tenants", "settings", False),
                ("tenants", "byok_kek_resource", False),
                ("agents", "status", True),
                ("agents", "name", False),
                ("agents", "system_prompt_ref", False),
            ):
                granted = (await conn.execute(text("SELECT has_column_privilege(:r,:t,:c,'SELECT')"),
                                              {"r": AGGREGATE_ROLE, "t": f"public.{table}", "c": column})).scalar()
                assert granted is allowed, (table, column)
            for table in ("users", "api_keys", "run_facts", "audit_log", "llm_daily_spend"):
                assert (await conn.execute(text("SELECT has_table_privilege(:r,:t,'SELECT')"),
                                           {"r": AGGREGATE_ROLE, "t": f"public.{table}"})).scalar() is False, table
            for fn in FUNCTIONS:
                info = (await conn.execute(text(
                    "SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
                    "has_function_privilege('public', p.oid, 'EXECUTE') "
                    "FROM pg_proc p WHERE p.oid = to_regprocedure(:fn)"), {"fn": fn})).one()
                assert tuple(info) == (AGGREGATE_ROLE, True, ["search_path=pg_catalog"], False), fn

        # Reading a column outside the grant as the aggregate role is refused outright.
        async with admin.connect() as conn:
            await conn.execute(text(f"SET LOCAL ROLE {AGGREGATE_ROLE}"))
            await conn.execute(text("SELECT tenant_id, runs FROM public.insights_daily LIMIT 1"))
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text("SELECT agent_ref FROM public.insights_daily LIMIT 1"))
            await conn.rollback()

        # A login role without the grant cannot call the functions, nor become the aggregate role.
        role = "plat_nogrant_" + uuid.uuid4().hex[:12]
        async with admin.begin() as conn:
            await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
            await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        runtime = create_async_engine(admin.url.set(username=role, password="local-test-password"), poolclass=NullPool)
        async with runtime.connect() as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text("SELECT * FROM public.insights_platform_daily(CURRENT_DATE, CURRENT_DATE)"))
            await conn.rollback()
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(f"SET ROLE {AGGREGATE_ROLE}"))
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", ["insights_platform_daily", "insights_platform_tenants"])
async def test_range_guard(fn: str):
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role, runtime = None, None
    try:
        role, runtime = await _runtime_role(admin)
        day = date(2041, 6, 1)
        async with runtime.connect() as conn:
            await conn.execute(text(f"SELECT * FROM public.{fn}(:f,:t)"), {"f": day, "t": day + timedelta(days=92)})
            for start, end in ((day, day + timedelta(days=93)), (day, day - timedelta(days=1)), (None, day),
                               (day, None)):
                with pytest.raises(DBAPIError, match="at most 92 days"):
                    await conn.execute(text(f"SELECT * FROM public.{fn}(CAST(:f AS date),CAST(:t AS date))"),
                                       {"f": start, "t": end})
                await conn.rollback()
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_posture_shape_as_the_runtime_role():
    from core.insights import platform

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role, runtime = None, None
    try:
        role, runtime = await _runtime_role(admin)
        async with async_sessionmaker(runtime, expire_on_commit=False)() as session:
            result = await platform.posture(session)
        assert [c["key"] for c in result["checks"]] == ["schema", "rls", "role", "health"]
        assert result["role"] == {"ok": True, "role": role, "superuser": False, "bypass_rls": False}
        assert result["schema"]["database_heads"] and result["schema"]["code_heads"]
        assert result["rls"]["tenant_tables"] > 50
        assert result["rls"]["forced"] + len(result["rls"]["unforced"]) == result["rls"]["tenant_tables"]
        assert set(result["health"]) >= {"ok", "snapshots", "counts", "latest_status", "latest_version"}
        assert result["ok"] is all(c["ok"] for c in result["checks"])
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


async def _platform_functions(conn) -> tuple[dict, dict]:
    rows = (await conn.execute(text("""
        SELECT p.proname AS name, r.rolname AS owner, p.prosecdef AS definer, p.proconfig AS config
        FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('insights_platform_daily','insights_platform_tenants')
    """))).mappings().all()
    rights = (await conn.execute(text(f"""
        SELECT pg_has_role(current_user,'{AGGREGATE_ROLE}','USAGE') AS inherits,
            pg_has_role(current_user,'{AGGREGATE_ROLE}','SET') AS can_set,
            has_schema_privilege('{AGGREGATE_ROLE}','public','CREATE') AS aggregate_creates
    """))).mappings().one()
    return {row["name"]: dict(row) for row in rows}, dict(rights)


@pytest.mark.asyncio
async def test_non_superuser_install_replay_and_rollback():
    """A non-superuser installer creates the functions as the aggregate role's and keeps no rights afterwards."""
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role = "plat_installer_" + uuid.uuid4().hex[:12]
    db = "plat_install_" + uuid.uuid4().hex[:12]
    installer = None
    created = False
    created_app_role = False
    migration = importlib.import_module("migrations.versions.v6_z107_insights_platform")
    unchanged = {"inherits": False, "can_set": False, "aggregate_creates": False}

    def apply(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            action()

    try:
        async with admin.connect() as connection:
            conn = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN CREATEROLE NOBYPASSRLS '
                                      "PASSWORD 'local-test-password'")
            # The role exists cluster-wide (the full chain created it); the installer holds
            # only the creator's admin-only management grant, as in the v6z85 proof.
            await conn.exec_driver_sql(f'GRANT {AGGREGATE_ROLE} TO "{role}" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE')
            await conn.exec_driver_sql(f'CREATE DATABASE "{db}" OWNER "{role}"')
            created = True
            if (await conn.execute(text("SELECT to_regrole('agenticorg_app')"))).scalar() is None:
                await conn.exec_driver_sql("CREATE ROLE agenticorg_app NOLOGIN NOBYPASSRLS")
                created_app_role = True
        installer = create_async_engine(admin.url.set(username=role, password="local-test-password", database=db),
                                        poolclass=NullPool)
        async with installer.begin() as conn:
            await conn.exec_driver_sql("""
                CREATE TABLE tenants(id UUID PRIMARY KEY, name TEXT, plan TEXT, created_at TIMESTAMPTZ,
                    deleted_at TIMESTAMPTZ, settings JSONB)
            """)
            await conn.exec_driver_sql("CREATE TABLE agents(tenant_id UUID, status TEXT, name TEXT)")
            await conn.exec_driver_sql("""
                CREATE TABLE insights_daily(tenant_id UUID, day DATE, status TEXT, runs INTEGER,
                    tokens_total BIGINT, cost_usd NUMERIC, duration_ms_sum BIGINT, duration_n INTEGER,
                    agent_ref TEXT)
            """)
            await conn.exec_driver_sql("CREATE TABLE insights_rollup_state(tenant_id UUID, day DATE, status TEXT)")
            for table in ("agents", "insights_daily", "insights_rollup_state"):
                await conn.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                await conn.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
                await conn.exec_driver_sql(f"""
                    CREATE POLICY tenant_scope ON {table}
                    USING (tenant_id::text=current_setting('agenticorg.tenant_id',true))
                """)
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            # A replay is a no-op.
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            functions, rights = await _platform_functions(conn)
            assert {name: (f["owner"], f["definer"], f["config"]) for name, f in functions.items()} == {
                name: (AGGREGATE_ROLE, True, ["search_path=pg_catalog"])
                for name in ("insights_platform_daily", "insights_platform_tenants")
            }
            assert rights == unchanged
            for fn in FUNCTIONS:
                assert (await conn.execute(text("SELECT has_function_privilege('agenticorg_app',:fn,'EXECUTE')"),
                                           {"fn": fn})).scalar() is True
                assert (await conn.execute(text("SELECT has_function_privilege('public',:fn,'EXECUTE')"),
                                           {"fn": fn})).scalar() is False
            tenant = uuid.uuid4()
            await conn.execute(text("INSERT INTO tenants(id,name,plan) VALUES (:id,'Synthetic','free')"),
                               {"id": tenant})
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id',:t,true)"), {"t": str(tenant)})
            await conn.execute(text(
                "INSERT INTO insights_daily VALUES (:t,'2041-01-02','completed',7,70,0.7,10,1,'private')"
            ), {"t": tenant})
        async with installer.begin() as conn:
            # Without tenant context the owner sees no rows; the function sums across tenants.
            assert (await conn.execute(text("SELECT count(*) FROM insights_daily"))).scalar() == 0
            runs = (await conn.execute(text(
                "SELECT runs FROM public.insights_platform_daily('2041-01-01','2041-01-03')"))).scalar()
            assert runs == 7
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.downgrade))
            functions, rights = await _platform_functions(conn)
            assert functions == {} and rights == unchanged
            policies = (await conn.execute(text(
                "SELECT count(*) FROM pg_policies WHERE policyname='insights_platform_read'"))).scalar()
            assert policies == 0
            assert (await conn.execute(text(
                f"SELECT has_column_privilege('{AGGREGATE_ROLE}','public.insights_daily','runs','SELECT')"
            ))).scalar() is False
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            functions, rights = await _platform_functions(conn)
            assert set(functions) == {"insights_platform_daily", "insights_platform_tenants"}
            assert rights == unchanged
    finally:
        if installer:
            await installer.dispose()
        async with admin.connect() as connection:
            conn = await connection.execution_options(isolation_level="AUTOCOMMIT")
            if created:
                await conn.exec_driver_sql(f'DROP DATABASE "{db}" WITH (FORCE)')
            if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
                await conn.exec_driver_sql(f'DROP ROLE "{role}"')
            if created_app_role:
                await conn.exec_driver_sql("DROP ROLE agenticorg_app")
        await admin.dispose()
