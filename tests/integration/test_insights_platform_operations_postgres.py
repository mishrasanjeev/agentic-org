# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z114 platform operations figures against real Postgres, called by a NOBYPASSRLS login role.

``insights_platform_operations`` counts every tenant's export jobs by status
and the late rollups, though the caller reads none of those rows directly
under forced row-level security. The aggregate role reads only the three
granted export columns; ranges over 92 days, reversed or open are refused.
The installer test creates the function as a non-superuser, replays, rolls
back (refused while an ``insights_landing`` check exists) and re-applies,
and proves the installer keeps no membership of the role.
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
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

REVISION = "v6z114_insights_operations"
AGGREGATE_ROLE = "agenticorg_insights_platform"
FUNCTION = "public.insights_platform_operations(date, date)"
OPERATIONS_SQL = text("SELECT * FROM public.insights_platform_operations(:f,:t)")


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
    """A NOBYPASSRLS login role with the application's grants."""
    role = "ops_runtime_" + uuid.uuid4().hex[:12]
    async with admin.begin() as conn:
        # Identifier is a fixed prefix plus UUID hex; never client input.
        await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
        await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        await conn.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {FUNCTION} TO "{role}"')
    engine = create_async_engine(admin.url.set(username=role, password="local-test-password"), poolclass=NullPool)
    return role, engine


async def _drop_role(admin, role: str) -> None:
    async with admin.begin() as conn:
        if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
            await conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            await conn.exec_driver_sql(f'DROP ROLE "{role}"')


@pytest.mark.asyncio
async def test_operations_count_every_tenants_jobs_for_a_nobypassrls_role():
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    # A random far-past window, so the export counts are this test's rows only.
    start = date(2001, 1, 1) + timedelta(days=random.randint(0, 5000))
    made = datetime(start.year, start.month, start.day, 9, tzinfo=UTC)
    role, runtime = None, None
    try:
        async with admin.begin() as conn:
            for tenant, name in ((tenant_a, "Synthetic Ops Alpha"), (tenant_b, "Synthetic Ops Beta")):
                await conn.execute(text(
                    "INSERT INTO tenants(id,name,slug,plan,data_region,settings) "
                    "VALUES (:id,:name,:slug,'free','IN','{}')"
                ), {"id": tenant, "name": name, "slug": "ops-" + tenant.hex})
            jobs = [
                (tenant_a, "ready", 0), (tenant_a, "ready", 1), (tenant_a, "failed", 1), (tenant_a, "queued", 0),
                (tenant_b, "ready", 0), (tenant_b, "running", 1), (tenant_b, "expired", 1),
                # Outside the range below.
                (tenant_b, "failed", 5),
            ]
            for tenant, status, offset in jobs:
                await conn.execute(text(
                    "INSERT INTO insights_exports(id,tenant_id,kind,format,params,status,storage_key,created_at,"
                    "expires_at) VALUES (:id,:t,'explorer','csv','{}',:s,'private/key',:at,:at)"
                ), {"id": uuid.uuid4(), "t": tenant, "s": status, "at": made + timedelta(days=offset)})
            # Tenant A's newest rollup finished five hours ago; tenant B's a minute ago.
            day = date(2042, 1, 1) + timedelta(days=random.randint(0, 5000))
            for tenant, hours in ((tenant_a, 5), (tenant_a, 30), (tenant_b, 0.02)):
                await conn.execute(text(
                    "INSERT INTO insights_rollup_state(id,tenant_id,day,status,rolled_at,detail) "
                    "VALUES (:id,:t,:day,'rolled',now() - make_interval(secs => :s),'private')"
                ), {"id": uuid.uuid4(), "t": tenant, "day": day + timedelta(days=hours), "s": hours * 3600})

        role, runtime = await _runtime_role(admin)
        async with runtime.connect() as conn:
            direct = await conn.execute(text("SELECT count(*) FROM insights_exports WHERE tenant_id IN (:a,:b)"),
                                        {"a": tenant_a, "b": tenant_b})
            assert direct.scalar_one() == 0
            row = (await conn.execute(OPERATIONS_SQL, {"f": start, "t": start + timedelta(days=1)})).mappings().one()
        assert (row["exports_queued"], row["exports_running"], row["exports_ready"], row["exports_failed"],
                row["exports_expired"]) == (1, 1, 3, 1, 1)
        # The oldest open job (A's queued one) was made at the window's start, decades ago.
        assert row["oldest_open_minutes"] > 60 * 24 * 365
        # Other tests' tenants may be late too; this one is, by about five hours (its newer rollup counts).
        assert row["tenants_late"] >= 1 and float(row["max_lag_hours"]) >= 4.9

        async with runtime.connect() as conn:
            far = {"f": date(1990, 1, 1), "t": date(1990, 1, 2)}
            empty = (await conn.execute(OPERATIONS_SQL, far)).mappings().one()
        assert (empty["exports_ready"], empty["oldest_open_minutes"]) == (0, None)
    finally:
        async with admin.begin() as conn:
            for table in ("insights_exports", "insights_rollup_state"):
                await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"),
                                   {"a": tenant_a, "b": tenant_b})
            await conn.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
        if runtime is not None:
            await runtime.dispose()
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_the_aggregate_role_reads_only_its_export_columns_and_nobody_else_executes():
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role, runtime = None, None
    try:
        async with admin.connect() as conn:
            for column, allowed in (("tenant_id", True), ("status", True), ("created_at", True),
                                    ("storage_key", False), ("params", False), ("filename", False),
                                    ("user_id", False), ("signature", False), ("error", False)):
                granted = (await conn.execute(text("SELECT has_column_privilege(:r,'public.insights_exports',:c,"
                                                   "'SELECT')"), {"r": AGGREGATE_ROLE, "c": column})).scalar()
                assert granted is allowed, column
            assert (await conn.execute(text(
                "SELECT has_column_privilege(:r,'public.insights_rollup_state','rolled_at','SELECT')"
            ), {"r": AGGREGATE_ROLE})).scalar() is True
            info = (await conn.execute(text(
                "SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
                "has_function_privilege('public', p.oid, 'EXECUTE') "
                "FROM pg_proc p WHERE p.oid = to_regprocedure(:fn)"), {"fn": FUNCTION})).one()
            assert tuple(info) == (AGGREGATE_ROLE, True, ["search_path=pg_catalog"], False)

        role = "ops_nogrant_" + uuid.uuid4().hex[:12]
        async with admin.begin() as conn:
            await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
            await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        runtime = create_async_engine(admin.url.set(username=role, password="local-test-password"), poolclass=NullPool)
        async with runtime.connect() as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(
                    "SELECT * FROM public.insights_platform_operations(CURRENT_DATE, CURRENT_DATE)"))
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_range_guard():
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role, runtime = None, None
    try:
        role, runtime = await _runtime_role(admin)
        day = date(2041, 6, 1)
        async with runtime.connect() as conn:
            await conn.execute(OPERATIONS_SQL, {"f": day, "t": day + timedelta(days=92)})
            for start, end in ((day, day + timedelta(days=93)), (day, day - timedelta(days=1)), (None, day),
                               (day, None)):
                with pytest.raises(DBAPIError, match="at most 92 days"):
                    await conn.execute(text(
                        "SELECT * FROM public.insights_platform_operations(CAST(:f AS date),CAST(:t AS date))"
                    ), {"f": start, "t": end})
                await conn.rollback()
    finally:
        if runtime is not None:
            await runtime.dispose()
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_the_synthetic_check_kind_accepts_insights_landing():
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            definition = (await conn.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_synthetic_checks_kind'"
            ))).scalar_one()
        assert "insights_landing" in definition and "eval_dataset" in definition
    finally:
        await admin.dispose()


async def _rights(conn) -> dict[str, bool]:
    return dict((await conn.execute(text(f"""
        SELECT pg_has_role(current_user,'{AGGREGATE_ROLE}','USAGE') AS inherits,
            pg_has_role(current_user,'{AGGREGATE_ROLE}','SET') AS can_set,
            has_schema_privilege('{AGGREGATE_ROLE}','public','CREATE') AS aggregate_creates
    """))).mappings().one())


async def _function(conn) -> tuple | None:
    row = (await conn.execute(text(
        "SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig FROM pg_proc p "
        "WHERE p.oid = to_regprocedure(:fn)"), {"fn": FUNCTION})).first()
    return tuple(row) if row else None


@pytest.mark.asyncio
async def test_non_superuser_install_replay_and_rollback():
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role = "ops_installer_" + uuid.uuid4().hex[:12]
    db = "ops_install_" + uuid.uuid4().hex[:12]
    installer = None
    created = False
    migration = importlib.import_module("migrations.versions.v6_z114_insights_operations")
    unchanged = {"inherits": False, "can_set": False, "aggregate_creates": False}
    owned = (AGGREGATE_ROLE, True, ["search_path=pg_catalog"])

    def apply(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            action()

    try:
        async with admin.connect() as connection:
            conn = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN CREATEROLE NOBYPASSRLS '
                                      "PASSWORD 'local-test-password'")
            await conn.exec_driver_sql(f'GRANT {AGGREGATE_ROLE} TO "{role}" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE')
            await conn.exec_driver_sql(f'CREATE DATABASE "{db}" OWNER "{role}"')
            created = True
        installer = create_async_engine(admin.url.set(username=role, password="local-test-password", database=db),
                                        poolclass=NullPool)
        async with installer.begin() as conn:
            await conn.exec_driver_sql("""
                CREATE TABLE insights_exports(id UUID PRIMARY KEY, tenant_id UUID, status TEXT,
                    created_at TIMESTAMPTZ, storage_key TEXT)
            """)
            await conn.exec_driver_sql("CREATE TABLE insights_rollup_state(tenant_id UUID, rolled_at TIMESTAMPTZ)")
            await conn.exec_driver_sql("""
                CREATE TABLE synthetic_checks(id UUID PRIMARY KEY, kind TEXT,
                    CONSTRAINT ck_synthetic_checks_kind CHECK (kind IN ('model','knowledge','audit_chain',
                        'guardrail','adversarial','eval_dataset')))
            """)
            for table in ("insights_exports", "insights_rollup_state"):
                await conn.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                await conn.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
                await conn.exec_driver_sql(f"""
                    CREATE POLICY tenant_scope ON {table}
                    USING (tenant_id::text=current_setting('agenticorg.tenant_id',true))
                """)
            # v6z107 gave the aggregate role this policy on the rollup state.
            await conn.exec_driver_sql(f"CREATE POLICY insights_platform_read ON insights_rollup_state FOR SELECT "
                                       f"TO {AGGREGATE_ROLE} USING (true)")
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            assert await _function(conn) == owned
            assert await _rights(conn) == unchanged
            tenant = uuid.uuid4()
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id',:t,true)"), {"t": str(tenant)})
            await conn.execute(text(
                "INSERT INTO insights_exports VALUES (:id,:t,'failed','2041-01-02T10:00:00Z','private')"
            ), {"id": uuid.uuid4(), "t": tenant})
            await conn.execute(text("INSERT INTO insights_rollup_state VALUES (:t, now() - interval '3 hours')"),
                               {"t": tenant})
            await conn.execute(text("INSERT INTO synthetic_checks VALUES (:id,'insights_landing')"),
                               {"id": uuid.uuid4()})
        async with installer.begin() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM insights_exports"))).scalar() == 0
            failed, late = (await conn.execute(text(
                "SELECT exports_failed, tenants_late "
                "FROM public.insights_platform_operations('2041-01-01','2041-01-03')"
            ))).one()
            assert (failed, late) == (1, 1)
        # The downgrade refuses while an insights_landing check exists.
        async with installer.connect() as conn:
            with pytest.raises(DBAPIError, match="insights_landing checks exist"):
                await conn.run_sync(lambda c: apply(c, migration.downgrade))
            await conn.rollback()
        async with installer.begin() as conn:
            await conn.execute(text("DELETE FROM synthetic_checks"))
            await conn.run_sync(lambda c: apply(c, migration.downgrade))
            assert await _function(conn) is None
            assert await _rights(conn) == unchanged
            assert (await conn.execute(text(
                "SELECT count(*) FROM pg_policies WHERE policyname='insights_platform_read' "
                "AND tablename='insights_exports'"))).scalar() == 0
            assert (await conn.execute(text(
                f"SELECT has_column_privilege('{AGGREGATE_ROLE}','public.insights_exports','status','SELECT')"
            ))).scalar() is False
            with pytest.raises(DBAPIError, match="ck_synthetic_checks_kind"):
                async with conn.begin_nested():
                    await conn.execute(text("INSERT INTO synthetic_checks VALUES (:id,'insights_landing')"),
                                       {"id": uuid.uuid4()})
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            assert await _function(conn) == owned
            assert await _rights(conn) == unchanged
    finally:
        if installer:
            await installer.dispose()
        async with admin.connect() as connection:
            conn = await connection.execution_options(isolation_level="AUTOCOMMIT")
            if created:
                await conn.exec_driver_sql(f'DROP DATABASE "{db}" WITH (FORCE)')
            if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
                await conn.exec_driver_sql(f'DROP ROLE "{role}"')
        await admin.dispose()
