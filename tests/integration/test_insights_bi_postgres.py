# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
# ruff: noqa: S608 - SQL is built from fixed view, table and test role names only
"""Per-tenant BI database access against real Postgres, connected AS the BI logins.

Migration ``v6z108_insights_bi`` is installed by a role that is NOT a superuser
(CREATEROLE, owner of the database and of the tables the migration changes),
the way production's Cloud SQL migration user runs it: upgrade, upgrade again
(idempotent), downgrade, upgrade. The logins are then created and revoked by a
second non-superuser role that holds only EXECUTE on the two functions (and
SELECT on the mapping table), standing in for ``agenticorg_app``, through the
application's own ``core.insights.bi_access`` calls. So PostgreSQL 16's
CREATEROLE and ADMIN OPTION rules are what is exercised, not superuser bypass.

Each tenant's login then connects with password authentication (asyncpg) using
the password whose SCRAM verifier the application computed, and the tests show
that it reads only its own tenant's rows in every view, also after setting
``agenticorg.tenant_id`` (or the platform context) to something else; that it
holds no privilege on any table, so ``EXPLAIN`` and ``pg_stats`` reveal nothing
about other tenants' volumes or values; that it cannot read ``users``,
``api_keys``, ``tenants``, the mapping table, any other public table, any
``run_facts`` partition or any unexposed column; that it can write nothing;
that it reads nothing on a primary unless ``insights_bi_settings`` allows it;
and that once revoked it cannot log in and a session it still holds, even
inside a transaction opened before the revoke, reads nothing.

The test database is a primary, so the module sets ``allow_primary`` on.

Roles are cluster-wide: every role a test creates has a unique name and is
dropped at the end, and the database is returned to the superuser-installed
head.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.insights import bi_access

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "v6_z108_insights_bi.py"
PREVIOUS = "v6z107_insights_platform"
HEAD = "v6z108_insights_bi"
BI_ROLES = ("agenticorg_bi", "agenticorg_bi_lookup", "agenticorg_bi_admin", "agenticorg_bi_reader")
SOURCES = ("run_facts", "insights_daily", "spend_usage_rollups", "agents", "hitl_queue")
VIEWS = ("run_facts", "daily", "spend_daily", "agents", "approvals")
READER_FUNCTIONS = {f"{view}_rows" for view in VIEWS}
SEARCH_PATH_CONFIG = ["search_path=pg_catalog, pg_temp"]
# What the installer must own to run v6z108; in production it owns the whole schema.
INSTALLER_OWNS = ("tenants", *SOURCES, "alembic_version")
PASSWORD = "local-test-password-" + uuid.uuid4().hex[:12]
CREATE_FN = "public.insights_bi_create_login(uuid, text, text, uuid, integer)"
REVOKE_FN = "public.insights_bi_revoke_login(uuid, text)"

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"
)


# ---------------------------------------------------------------- connection helpers


def _base_url():
    return make_url(os.environ["AGENTICORG_DB_URL"])


def _sync_url(user: str | None = None, password: str | None = None) -> str:
    url = _base_url().set(drivername="postgresql+psycopg2")
    if user is not None:
        url = url.set(username=user, password=password)
    return url.render_as_string(hide_password=False)


def _async_url(user: str, password: str):
    return _base_url().set(drivername="postgresql+asyncpg", username=user, password=password)


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z108_insights_bi_pg", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(url: str, fn: str, version: str) -> list[str]:
    """Run the migration's ``upgrade``/``downgrade`` as the URL's role, and record the version, in one transaction.

    Returns the server notices the run raised (SQLAlchemy's psycopg2 dialect
    logs each one at INFO and clears the driver's list).
    """
    notices: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            notices.append(record.getMessage())

    dialect_logger = logging.getLogger("sqlalchemy.dialects.postgresql")
    handler, level = _Collect(level=logging.INFO), dialect_logger.level
    dialect_logger.addHandler(handler)
    dialect_logger.setLevel(logging.INFO)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                getattr(_migration(), fn)()
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": version})
        return notices
    finally:
        engine.dispose()
        dialect_logger.removeHandler(handler)
        dialect_logger.setLevel(level)


def _exists(conn, role: str) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar())


def _allow_primary(admin: Engine, allowed: bool) -> None:
    with admin.begin() as conn:
        conn.execute(text("UPDATE insights_bi_settings SET allow_primary = :a"), {"a": allowed})


async def _bi_connect(role: str, password: str) -> asyncpg.Connection:
    url = _base_url()
    return await asyncpg.connect(
        host=url.host, port=url.port, user=role, password=password, database=url.database, ssl=False
    )


async def _as_app(app: dict[str, str], tenant: uuid.UUID, call: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    """``call(session)`` in a tenant session of the stand-in application role (EXECUTE only)."""
    engine = create_async_engine(_async_url(app["role"], app["password"]), poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": str(tenant)})
            return await call(session)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------- installation as a non-superuser


def _restore(admin: Engine, db: str, installer: str) -> None:
    with admin.begin() as conn:
        for rel in INSTALLER_OWNS:
            conn.exec_driver_sql(f"ALTER TABLE public.{rel} OWNER TO CURRENT_USER")
        conn.exec_driver_sql(f'ALTER DATABASE "{db}" OWNER TO CURRENT_USER')
        if _exists(conn, installer):
            conn.exec_driver_sql(f'DROP OWNED BY "{installer}"')
            conn.exec_driver_sql(f'DROP ROLE "{installer}"')


@pytest.fixture(scope="module")
def installed() -> Iterator[dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "scripts/alembic_migrate.py"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr[-4000:]
    admin = create_engine(_sync_url(), poolclass=NullPool)
    with admin.connect() as conn:
        if not conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
            pytest.skip("setting up the installer role needs a superuser connection")
        db = conn.execute(text("SELECT current_database()")).scalar_one()
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
    # Fixed prefix plus UUID hex; never "bi_" (the BI login name space).
    installer = "t_biinst_" + uuid.uuid4().hex[:12]
    _apply(_sync_url(), "downgrade", PREVIOUS)
    try:
        with admin.begin() as conn:
            conn.exec_driver_sql(
                f"CREATE ROLE \"{installer}\" LOGIN NOSUPERUSER CREATEROLE NOCREATEDB NOBYPASSRLS PASSWORD '{PASSWORD}'"
            )
            conn.exec_driver_sql(f'ALTER DATABASE "{db}" OWNER TO "{installer}"')
            for rel in INSTALLER_OWNS:
                conn.exec_driver_sql(f'ALTER TABLE public.{rel} OWNER TO "{installer}"')
            for role in BI_ROLES:
                # Kept because another database on the cluster still uses it: on
                # PostgreSQL 16 the role that created it holds exactly this grant.
                if _exists(conn, role):
                    conn.exec_driver_sql(f'GRANT {role} TO "{installer}" WITH ADMIN OPTION, INHERIT FALSE, SET FALSE')
        installer_url = _sync_url(installer, PASSWORD)
        _apply(installer_url, "upgrade", HEAD)
        _apply(installer_url, "upgrade", HEAD)  # idempotent
        _apply(installer_url, "downgrade", PREVIOUS)
        _apply(installer_url, "upgrade", HEAD)
        yield {"admin": admin, "db": db, "installer": installer, "installer_url": installer_url}
        _apply(installer_url, "downgrade", PREVIOUS)
    finally:
        try:
            _restore(admin, db, installer)
        finally:
            # Back to the ordinary superuser-installed head for the other tests.
            with admin.connect() as conn:
                version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if version != HEAD or not _exists_any(admin):
                _apply(_sync_url(), "upgrade", HEAD)
            admin.dispose()


def _exists_any(admin: Engine) -> bool:
    with admin.connect() as conn:
        return all(_exists(conn, role) for role in BI_ROLES) and bool(
            conn.execute(
                text(
                    "SELECT to_regprocedure('public.insights_bi_tenant()') IS NOT NULL "
                    "AND to_regclass('public.insights_bi_logins') IS NOT NULL "
                    "AND to_regclass('insights_bi.run_facts') IS NOT NULL"
                )
            ).scalar()
        )


# ---------------------------------------------------------------- tenants, data, the application role and logins


def _seed(conn, tenant: uuid.UUID, runs: int, daily: int, spend: int, agents: int) -> dict[str, int]:
    conn.execute(
        text(
            "INSERT INTO tenants(id, name, slug, plan, data_region, settings) "
            "VALUES (:id, 'BI isolation test', :slug, 'free', 'IN', '{}')"
        ),
        {"id": tenant, "slug": "bi-iso-" + tenant.hex},
    )
    for i in range(runs):
        # The last fact lands in the default partition (past the monthly horizon).
        started = datetime(2031, 1, 5, tzinfo=UTC) if i == runs - 1 else datetime(2026, 11, 2 + i, tzinfo=UTC)
        conn.execute(
            text(
                "INSERT INTO run_facts(id, tenant_id, run_key, entrypoint, status, started_at, correlation_id, "
                "trace_id) VALUES (:id, :t, :k, 'agents', 'completed', :s, 'corr-private', 'trace-private')"
            ),
            {"id": uuid.uuid4(), "t": tenant, "k": f"agents:{uuid.uuid4().hex}", "s": started},
        )
    for i in range(daily):
        conn.execute(
            text(
                "INSERT INTO insights_daily(id, tenant_id, day, dims_hash, entrypoint, status, environment, "
                "data_quality, latency_buckets, runs) VALUES (:id, :t, :d, :h, 'agents', 'completed', 'production', "
                "'measured', ARRAY[0,0,0,0,0,0,0,0,0,0,0,0], 1)"
            ),
            {"id": uuid.uuid4(), "t": tenant, "d": date(2026, 11, 1 + i), "h": uuid.uuid4().hex},
        )
    for i in range(spend):
        conn.execute(
            text(
                "INSERT INTO spend_usage_rollups(id, tenant_id, day, dims_hash, billing_date, application, provider, "
                "usage_type, unit, price_source, record_count) VALUES (:id, :t, :d, :h, :d, 'agenticorg', 'gemini', "
                "'llm_tokens', 'token', 'rate_card', 1)"
            ),
            {"id": uuid.uuid4(), "t": tenant, "d": date(2026, 11, 1 + i), "h": uuid.uuid4().hex * 2},
        )
    agent_ids = [_insert_agent(conn, tenant, f"BI agent {i}", f"1.{i}") for i in range(agents)]
    conn.execute(
        text(
            "INSERT INTO hitl_queue(id, tenant_id, agent_id, title, trigger_type, priority, status, assignee_role, "
            "decision_options, context, expires_at) VALUES (:id, :t, :a, 'Private approval title', 'threshold', "
            "'normal', 'pending', 'cfo', '{}', '{\"customer\": \"private\"}', now() + interval '1 day')"
        ),
        {"id": uuid.uuid4(), "t": tenant, "a": agent_ids[0]},
    )
    return {"run_facts": runs, "daily": daily, "spend_daily": spend, "agents": agents, "approvals": 1}


def _insert_agent(conn, tenant: uuid.UUID, name: str, version: str) -> uuid.UUID:
    agent_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO agents(id, tenant_id, name, agent_type, domain, system_prompt_ref, prompt_variables, "
            "llm_model, llm_config, confidence_floor, hitl_condition, max_retries, retry_backoff, "
            "authorized_tools, status, version, shadow_sample_count, shadow_scored_sample_count, "
            "shadow_feedback_count, cost_controls, scaling, tags, config, routing_filter, is_builtin, org_level) "
            "VALUES (:id, :t, :n, 'bi_test', 'finance', 'prompt', '{}', 'model', '{}', 0.8, '', 1, 'exponential', "
            "'[]', 'active', :v, 0, 0, 0, '{}', '{}', '{}', '{}', '{}', false, 0)"
        ),
        {"id": agent_id, "t": tenant, "n": name, "v": version},
    )
    return agent_id


def _unseed(conn, tenant: uuid.UUID) -> None:
    for table in ("hitl_queue", "agents", "spend_usage_rollups", "insights_daily", "run_facts"):
        conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant})
    if conn.execute(text("SELECT to_regclass('public.insights_bi_logins') IS NOT NULL")).scalar():
        conn.execute(text("DELETE FROM insights_bi_logins WHERE tenant_id = :t"), {"t": tenant})
    # The tenant row stays, as in the other Postgres tests (other tables may reference it).


@pytest.fixture(scope="module")
def world(installed) -> Iterator[dict[str, Any]]:
    admin: Engine = installed["admin"]
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    app = {"role": "t_biapp_" + uuid.uuid4().hex[:12], "password": PASSWORD}
    try:
        with admin.begin() as conn:
            counts = {tenant_a: _seed(conn, tenant_a, 3, 2, 1, 2), tenant_b: _seed(conn, tenant_b, 2, 1, 2, 1)}
            # The application role: no superuser, no CREATEROLE, EXECUTE on the two functions and a
            # read of the mapping table, as scripts/create_app_role.sql leaves agenticorg_app.
            conn.exec_driver_sql(
                f'CREATE ROLE "{app["role"]}" LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS '
                f"PASSWORD '{PASSWORD}'"
            )
            conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{app["role"]}"')
            conn.exec_driver_sql(f'GRANT SELECT ON public.insights_bi_logins TO "{app["role"]}"')
            conn.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {CREATE_FN}, {REVOKE_FN} TO "{app["role"]}"')
        # The test database is a writable primary: let the logins read on it.
        _allow_primary(admin, True)

        async def _create(tenant: uuid.UUID) -> bi_access.CreatedLogin:
            return await _as_app(app, tenant, lambda s: bi_access.create_login(s, tenant, created_by=None, limit=2))

        login_a = asyncio.run(_create(tenant_a))
        login_b = asyncio.run(_create(tenant_b))
        yield {
            "admin": admin,
            "app": app,
            "a": tenant_a,
            "b": tenant_b,
            "counts": counts,
            "login_a": login_a,
            "login_b": login_b,
            "installer": installed["installer"],
            "installer_url": installed["installer_url"],
        }
    finally:
        with admin.begin() as conn:
            if conn.execute(text("SELECT to_regclass('public.insights_bi_logins') IS NOT NULL")).scalar():
                leftovers = (
                    conn.execute(
                        text("SELECT role_name FROM insights_bi_logins WHERE tenant_id IN (:a, :b)"),
                        {"a": tenant_a, "b": tenant_b},
                    )
                    .scalars()
                    .all()
                )
            else:
                leftovers = []
            for role in leftovers:
                if _exists(conn, role):
                    conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
                    conn.exec_driver_sql(f'DROP ROLE "{role}"')
            for tenant in (tenant_a, tenant_b):
                _unseed(conn, tenant)
            if _exists(conn, app["role"]):
                conn.exec_driver_sql(f'DROP OWNED BY "{app["role"]}"')
                conn.exec_driver_sql(f'DROP ROLE "{app["role"]}"')
            if conn.execute(text("SELECT to_regclass('public.insights_bi_settings') IS NOT NULL")).scalar():
                conn.execute(text("UPDATE insights_bi_settings SET allow_primary = false"))


# ---------------------------------------------------------------- the installation


def test_installed_by_a_non_superuser_that_keeps_no_access(world) -> None:
    installer = world["installer"]
    with world["admin"].connect() as conn:
        attrs = conn.execute(
            text("SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": installer}
        ).one()
        assert attrs == (False, True, False)
        for role in BI_ROLES:
            for mode in ("USAGE", "SET"):
                assert not conn.execute(
                    text("SELECT pg_has_role(:u, :r, :m)"), {"u": installer, "r": role, "m": mode}
                ).scalar(), (role, mode)
        roles = {
            row.rolname: row
            for row in conn.execute(
                text(
                    "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolinherit "
                    "FROM pg_roles WHERE rolname LIKE 'agenticorg\\_bi%'"
                )
            )
        }
        assert set(roles) == set(BI_ROLES)
        for name, row in roles.items():
            assert not row.rolcanlogin and not row.rolsuper and not row.rolbypassrls, name
            assert row.rolcreaterole == (name == "agenticorg_bi_admin"), name
        # Nobody (but a superuser) can use the reader's privileges.
        assert not conn.execute(
            text(
                "SELECT string_agg(r.rolname, ', ') FROM pg_roles r WHERE NOT r.rolsuper "
                "AND r.rolname <> 'agenticorg_bi_reader' AND (pg_has_role(r.oid, 'agenticorg_bi_reader', 'USAGE') "
                "OR pg_has_role(r.oid, 'agenticorg_bi_reader', 'SET'))"
            )
        ).scalar()
        functions = {
            row.proname: row
            for row in conn.execute(
                text(
                    "SELECT p.proname, n.nspname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef, p.proconfig, "
                    "p.pronargs, has_function_privilege('public', p.oid, 'EXECUTE') AS public_exec, "
                    "has_function_privilege('agenticorg_bi', p.oid, 'EXECUTE') AS bi_exec, "
                    "has_function_privilege('agenticorg_bi_reader', p.oid, 'EXECUTE') AS reader_exec "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE p.proname LIKE 'insights_bi_%' OR n.nspname = 'insights_bi'"
                )
            )
        }
        assert set(functions) == {
            "insights_bi_tenant",
            "insights_bi_create_login",
            "insights_bi_revoke_login",
            *READER_FUNCTIONS,
        }
        for name, row in functions.items():
            assert row.prosecdef and row.proconfig == SEARCH_PATH_CONFIG, name
            assert not row.public_exec, name
            if name in READER_FUNCTIONS:
                assert (row.nspname, row.owner, row.pronargs) == ("insights_bi", "agenticorg_bi_reader", 0), name
                assert row.bi_exec, name
            else:
                assert row.nspname == "public", name
                assert row.owner == ("agenticorg_bi_lookup" if name == "insights_bi_tenant" else "agenticorg_bi_admin")
                assert not row.bi_exec, name
                # Only the reader resolves the tenant (from its functions and policies).
                assert row.reader_exec == (name == "insights_bi_tenant"), name
        # Both policies on every source, for the reader only, and none on any partition.
        policies = conn.execute(
            text(
                "SELECT c.relname, p.polname, p.polpermissive, p.polroles::regrole[]::text[] AS roles "
                "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid WHERE p.polname LIKE 'insights_bi_%' "
                "AND c.relname NOT IN ('insights_bi_logins', 'insights_bi_settings')"
            )
        ).all()
        assert {(r.relname, r.polname, r.polpermissive) for r in policies} == {
            (t, name, permissive)
            for t in SOURCES
            for name, permissive in (("insights_bi_read", True), ("insights_bi_only_own", False))
        }
        assert all(r.roles == ["agenticorg_bi_reader"] for r in policies)
        # The BI group holds no privilege on any table or column anywhere (it reads through the functions).
        tables = conn.execute(
            text(
                "SELECT string_agg(c.oid::regclass::text, ', ') FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p', 'm', 'f') "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND has_any_column_privilege('agenticorg_bi', c.oid, 'SELECT')"
            )
        ).scalar()
        assert tables is None
        # The primary/replica switch: off by default, read by the lookup role only.
        assert (
            conn.execute(
                text(
                    "SELECT has_table_privilege('agenticorg_bi', 'public.insights_bi_settings', 'SELECT') "
                    "OR has_any_column_privilege('agenticorg_bi_reader', 'public.insights_bi_settings', 'SELECT') "
                    "OR has_table_privilege('agenticorg_bi_lookup', 'public.insights_bi_settings', 'UPDATE')"
                )
            ).scalar()
            is False
        )


# ---------------------------------------------------------------- isolation, connected as the logins


async def _rows(conn: asyncpg.Connection, view: str) -> list[asyncpg.Record]:
    return await conn.fetch(f"SELECT * FROM insights_bi.{view}")


async def _assert_only_own(conn: asyncpg.Connection, counts: dict[str, int], tenant: uuid.UUID) -> None:
    for view in VIEWS:
        rows = await _rows(conn, view)
        assert len(rows) == counts[view], (view, len(rows))
        if "tenant_id" in rows[0].keys():
            assert {row["tenant_id"] for row in rows} == {tenant}, view
    # Unqualified names resolve to the views (search_path = insights_bi).
    assert await conn.fetchval("SELECT count(*) FROM run_facts") == counts["run_facts"]
    # The functions under the views return the same rows when called directly.
    for view in VIEWS:
        assert await conn.fetchval(f"SELECT count(*) FROM insights_bi.{view}_rows()") == counts[view], view


def test_each_login_sees_only_its_own_tenant_in_every_view(world) -> None:
    async def scenario() -> None:
        for key, other in (("a", "b"), ("b", "a")):
            tenant, login = world[key], world[f"login_{key}"]
            conn = await _bi_connect(login.role_name, login.password)
            try:
                assert await conn.fetchval("SELECT session_user") == login.role_name
                await _assert_only_own(conn, world["counts"][tenant], tenant)
                # The tenant setting the application's policies read does not widen a BI session ...
                await conn.execute(f"SET agenticorg.tenant_id = '{world[other]}'")
                await _assert_only_own(conn, world["counts"][tenant], tenant)
                # ... nor does the platform context some tables OR into their policies (v6z87).
                await conn.execute("SET agenticorg.platform_ctx = '1'")
                await _assert_only_own(conn, world["counts"][tenant], tenant)
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL agenticorg.tenant_id = '{world[other]}'")
                    await _assert_only_own(conn, world["counts"][tenant], tenant)
                await conn.execute("RESET ALL")
                # The source tables are closed to it altogether, and so is the tenant lookup.
                for sql in ("SELECT tenant_id FROM public.run_facts", "SELECT public.insights_bi_tenant()"):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.fetch(sql)
                # The login's own settings, as the function set them.
                assert await conn.fetchval("SHOW statement_timeout") == "30s"
                assert await conn.fetchval("SHOW default_transaction_read_only") == "on"
            finally:
                await conn.close()

    asyncio.run(scenario())


def test_the_login_is_created_from_the_verifier_only(world) -> None:
    login = world["login_a"]
    with world["admin"].connect() as conn:
        row = conn.execute(
            text(
                "SELECT a.rolpassword, a.rolconnlimit, a.rolcanlogin, a.rolinherit, a.rolsuper, a.rolcreaterole, "
                "a.rolbypassrls, a.rolreplication, a.rolcreatedb FROM pg_authid a WHERE a.rolname = :r"
            ),
            {"r": login.role_name},
        ).one()
        assert row.rolpassword.startswith("SCRAM-SHA-256$4096:") and login.password not in row.rolpassword
        assert row.rolconnlimit == 3 and row.rolcanlogin and row.rolinherit
        assert not (row.rolsuper or row.rolcreaterole or row.rolbypassrls or row.rolreplication or row.rolcreatedb)
        config = set(
            conn.execute(
                text(
                    "SELECT unnest(setconfig) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                    "WHERE r.rolname = :r"
                ),
                {"r": login.role_name},
            ).scalars()
        )
        assert config == {"search_path=insights_bi", "statement_timeout=30s", "default_transaction_read_only=on"}
        grant = conn.execute(
            text(
                "SELECT m.admin_option, m.inherit_option, m.set_option, g.rolname FROM pg_auth_members m "
                "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = :r"
            ),
            {"r": login.role_name},
        ).all()
        assert [(g.rolname, g.admin_option, g.inherit_option, g.set_option) for g in grant] == [
            ("agenticorg_bi", False, True, False)
        ]
        mapping = conn.execute(
            text("SELECT tenant_id, revoked_at FROM insights_bi_logins WHERE role_name = :r"), {"r": login.role_name}
        ).one()
        assert mapping == (world["a"], None)


def test_a_login_reads_nothing_else(world) -> None:
    async def scenario() -> None:
        login = world["login_a"]
        conn = await _bi_connect(login.role_name, login.password)
        try:
            # Catalogue sweep: no table-level or column-level SELECT anywhere in public.
            rows = await conn.fetch(
                "SELECT c.relname, c.relkind, has_table_privilege(c.oid, 'SELECT') AS whole, "
                "has_any_column_privilege(c.oid, 'SELECT') AS any_col FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
            )
            assert len(rows) > 50
            assert not [r["relname"] for r in rows if r["whole"] or r["any_col"]]
            sequences = await conn.fetch(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'S' "
                "AND (has_sequence_privilege(c.oid, 'SELECT') OR has_sequence_privilege(c.oid, 'USAGE'))"
            )
            assert not sequences
            # The only restricted (SECURITY DEFINER) functions it may call are the reader functions.
            callable_definers = await conn.fetch(
                "SELECT n.nspname, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND p.prosecdef "
                "AND has_function_privilege(p.oid, 'EXECUTE')"
            )
            assert {(r["nspname"], r["proname"]) for r in callable_definers} == {
                ("insights_bi", name) for name in READER_FUNCTIONS
            }
            schema_rels = await conn.fetch(
                "SELECT c.relname, has_table_privilege(c.oid, 'SELECT') AS can FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'insights_bi'"
            )
            assert {(r["relname"], r["can"]) for r in schema_rels} == {(v, True) for v in VIEWS}

            denied = [
                "SELECT * FROM public.users LIMIT 1",
                "SELECT id FROM public.users LIMIT 1",
                "SELECT * FROM public.api_keys LIMIT 1",
                "SELECT * FROM public.tenants LIMIT 1",
                "SELECT id FROM public.tenants LIMIT 1",
                "SELECT * FROM public.insights_bi_logins",
                "SELECT role_name FROM public.insights_bi_logins",
                "SELECT * FROM public.insights_bi_settings",
                "SELECT allow_primary FROM public.insights_bi_settings",
                "SELECT * FROM public.audit_log LIMIT 1",
                "SELECT * FROM public.agent_task_results LIMIT 1",
                "SELECT * FROM public.llm_daily_spend LIMIT 1",
                "SELECT * FROM public.insights_rollup_state LIMIT 1",
                "SELECT * FROM public.spend_usage_records LIMIT 1",
                "SELECT * FROM public.run_facts_y2026m11",
                "SELECT tenant_id FROM public.run_facts_y2026m11",
                "SELECT * FROM public.run_facts_default",
                # The source tables, even the columns the views expose, and their plans.
                "SELECT id FROM public.run_facts",
                "SELECT tenant_id FROM public.run_facts",
                "SELECT name FROM public.agents",
                "SELECT status FROM public.hitl_queue",
                "SELECT day FROM public.insights_daily",
                "SELECT provider FROM public.spend_usage_rollups",
                "EXPLAIN SELECT id FROM public.run_facts",
                "EXPLAIN SELECT id FROM public.agents WHERE name < 'B'",
                # Columns no view exposes.
                "SELECT correlation_id FROM public.run_facts",
                "SELECT trace_id FROM public.run_facts",
                "SELECT run_key FROM public.run_facts",
                "SELECT * FROM public.run_facts",
                "SELECT title FROM public.hitl_queue",
                "SELECT context FROM public.hitl_queue",
                "SELECT system_prompt_ref FROM public.agents",
                "SELECT amount FROM public.spend_usage_rollups",
                # The tenant lookup and the provisioning functions.
                "SELECT public.insights_bi_tenant()",
                f"SELECT public.insights_bi_revoke_login('{world['b']}', '{world['login_b'].role_name}')",
                "SELECT public.insights_bi_create_login(gen_random_uuid(), 'bi_aaaaaaaaaa', 'x', NULL, 3)",
                "SELECT public.gemini_daily_platform_spend()",
            ]
            for sql in denied:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetch(sql)
            # Membership is for inheritance only: it cannot become the group (PostgreSQL 16 SET FALSE),
            # and it is no member of the reader at all.
            for role in ("agenticorg_bi", "agenticorg_bi_reader", "agenticorg_bi_lookup", "agenticorg_bi_admin"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(f"SET ROLE {role}")
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_a_login_writes_nothing(world) -> None:
    async def scenario() -> None:
        login = world["login_a"]
        conn = await _bi_connect(login.role_name, login.password)
        try:
            # Read-only by default (even a temporary table, which PUBLIC's TEMP privilege allows) ...
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await conn.execute("CREATE TEMP TABLE bi_scratch(id int)")
            # ... and refused by privilege once the login turns that off.
            await conn.execute("SET default_transaction_read_only = off")
            # The views select from a function, so they are not updatable at all.
            for sql in (
                "DELETE FROM insights_bi.agents",
                "UPDATE insights_bi.agents SET name = 'x'",
                "INSERT INTO insights_bi.daily(id) VALUES (gen_random_uuid())",
            ):
                with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.ObjectNotInPrerequisiteStateError)):
                    await conn.execute(sql)
            writes = [
                "DELETE FROM public.run_facts",
                "UPDATE public.agents SET status = 'paused'",
                "INSERT INTO public.agents(id) VALUES (gen_random_uuid())",
                "UPDATE public.insights_bi_logins SET revoked_at = NULL",
                "DELETE FROM public.insights_bi_logins",
                f"INSERT INTO public.insights_bi_logins(id, tenant_id, role_name) "
                f"VALUES (gen_random_uuid(), '{world['b']}', 'bi_forgedforged')",
                "UPDATE public.insights_bi_settings SET allow_primary = true",
                "TRUNCATE public.agents",
                "CREATE TABLE public.bi_planted(id int)",
                "CREATE TABLE insights_bi.bi_planted(id int)",
                "CREATE FUNCTION public.bi_planted() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                "CREATE FUNCTION insights_bi.bi_planted() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                "CREATE VIEW insights_bi.bi_planted AS SELECT 1",
                "CREATE SCHEMA bi_planted",
                "ALTER FUNCTION insights_bi.run_facts_rows() SECURITY INVOKER",
            ]
            for sql in writes:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------- statistics side channel (M1)


async def _estimate(conn: asyncpg.Connection, sql: str) -> float:
    plan = json.loads(await conn.fetchval(f"EXPLAIN (FORMAT JSON) {sql}"))
    return float(plan[0]["Plan"]["Plan Rows"])


def test_planner_statistics_reveal_nothing_about_other_tenants(world) -> None:
    """Other tenants' volumes and values do not move a BI login's row estimates, and pg_stats is empty for it.

    Tenant B gets thousands of facts with a value only it uses and dozens of
    agents with confidential names, and the tables are analysed. Login A's
    estimates for B's tenant id, B's value and a range over B's names must equal
    those for values that exist nowhere.
    """
    admin, tenant_a, tenant_b = world["admin"], world["a"], world["b"]
    secret, nowhere = "acme_secret_merger", "no_such_use_case_value"
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO run_facts(id, tenant_id, run_key, entrypoint, status, started_at, use_case) "
                "SELECT gen_random_uuid(), :t, 'stat:' || g, 'agents', 'completed', "
                "timestamptz '2026-11-02' + (g % 20) * interval '1 day', :uc FROM generate_series(1, 4000) g"
            ),
            {"t": tenant_b, "uc": secret},
        )
        for i in range(60):
            _insert_agent(conn, tenant_b, f"B-confidential-agent-{i:03d}", "9.9")
        conn.exec_driver_sql("ANALYZE run_facts")
        conn.exec_driver_sql("ANALYZE agents")
        # The statistics really do separate these values (so the comparison below means something).
        assert (
            conn.execute(
                text("SELECT count(*) FROM pg_stats WHERE tablename LIKE 'run_facts%' AND attname = 'use_case'")
            ).scalar_one()
        ) > 0

    async def scenario() -> None:
        login = world["login_a"]
        conn = await _bi_connect(login.role_name, login.password)
        try:
            unknown = uuid.uuid4()
            pairs = [
                (f"SELECT id FROM insights_bi.run_facts WHERE tenant_id = '{tenant_b}'",
                 f"SELECT id FROM insights_bi.run_facts WHERE tenant_id = '{unknown}'"),
                (f"SELECT id FROM insights_bi.run_facts WHERE tenant_id >= '{tenant_b}'",
                 f"SELECT id FROM insights_bi.run_facts WHERE tenant_id >= '{unknown}'"),
                (f"SELECT id FROM insights_bi.run_facts WHERE use_case = '{secret}'",
                 f"SELECT id FROM insights_bi.run_facts WHERE use_case = '{nowhere}'"),
                (f"SELECT id FROM run_facts WHERE use_case = '{secret}'",
                 f"SELECT id FROM run_facts WHERE use_case = '{nowhere}'"),
                ("SELECT id FROM insights_bi.agents WHERE name < 'B-confidential-agent-030'",
                 "SELECT id FROM insights_bi.agents WHERE name < '!'"),
                ("SELECT id FROM insights_bi.agents WHERE name = 'B-confidential-agent-007'",
                 "SELECT id FROM insights_bi.agents WHERE name = 'nobody at all'"),
                (f"SELECT id FROM insights_bi.run_facts_rows() WHERE use_case = '{secret}'",
                 f"SELECT id FROM insights_bi.run_facts_rows() WHERE use_case = '{nowhere}'"),
            ]  # fmt: skip
            for leaky, blank in pairs:
                assert await _estimate(conn, leaky) == await _estimate(conn, blank), leaky
            # Even the unfiltered estimate is the constant for a function scan, not the table's size.
            assert await _estimate(conn, "SELECT id FROM insights_bi.run_facts") == await _estimate(
                conn, "SELECT id FROM insights_bi.approvals"
            )
            # The plan shows the function scan only, never the tables or indexes behind it.
            plan = "\n".join(
                r[0] for r in await conn.fetch(f"EXPLAIN VERBOSE SELECT id FROM run_facts WHERE use_case = '{secret}'")
            )
            assert "Function Scan" in plan and "run_facts_rows" in plan
            assert "public.run_facts" not in plan and "Index" not in plan and "Seq Scan" not in plan
            # pg_stats shows a column's statistics only to roles that may read the column.
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM pg_stats WHERE schemaname = 'public' AND tablename IN "
                    "('run_facts', 'run_facts_y2026m11', 'run_facts_default', 'agents', 'insights_daily', "
                    "'spend_usage_rollups', 'hitl_queue')"
                )
                == 0
            )
            # And the extra rows are B's alone: A still reads its own.
            assert await conn.fetchval(f"SELECT count(*) FROM run_facts WHERE use_case = '{secret}'") == 0
            assert await conn.fetchval("SELECT count(*) FROM agents") == world["counts"][tenant_a]["agents"]
        finally:
            await conn.close()

    try:
        asyncio.run(scenario())
    finally:
        with admin.begin() as conn:
            conn.execute(
                text("DELETE FROM run_facts WHERE tenant_id = :t AND use_case = :uc"), {"t": tenant_b, "uc": secret}
            )
            conn.execute(
                text("DELETE FROM agents WHERE tenant_id = :t AND name LIKE 'B-confidential-agent-%'"), {"t": tenant_b}
            )


def test_run_facts_reach_back_400_days(world) -> None:
    admin, tenant_a = world["admin"], world["a"]
    now = datetime.now(UTC)
    ids = {"old": uuid.uuid4(), "recent": uuid.uuid4()}
    with admin.begin() as conn:
        for key, started in (("old", now - timedelta(days=401)), ("recent", now - timedelta(days=399))):
            conn.execute(
                text(
                    "INSERT INTO run_facts(id, tenant_id, run_key, entrypoint, status, started_at) "
                    "VALUES (:id, :t, :k, 'agents', 'completed', :s)"
                ),
                {"id": ids[key], "t": tenant_a, "k": f"window:{key}:{uuid.uuid4().hex}", "s": started},
            )

    async def scenario() -> None:
        login = world["login_a"]
        conn = await _bi_connect(login.role_name, login.password)
        try:
            seen = {r["id"] for r in await conn.fetch("SELECT id FROM insights_bi.run_facts")}
            assert ids["recent"] in seen and ids["old"] not in seen
            assert len(seen) == world["counts"][tenant_a]["run_facts"] + 1
        finally:
            await conn.close()

    try:
        asyncio.run(scenario())
    finally:
        with admin.begin() as conn:
            conn.execute(text("DELETE FROM run_facts WHERE id IN (:a, :b)"), {"a": ids["old"], "b": ids["recent"]})


# ---------------------------------------------------------------- primary or replica (L2)


def test_logins_read_nothing_on_a_primary_unless_allowed(world) -> None:
    admin, tenant_a = world["admin"], world["a"]
    login = world["login_a"]

    async def counts() -> dict[str, int]:
        conn = await _bi_connect(login.role_name, login.password)
        try:
            assert await conn.fetchval("SELECT pg_is_in_recovery()") is False
            return {view: await conn.fetchval(f"SELECT count(*) FROM insights_bi.{view}") for view in VIEWS}
        finally:
            await conn.close()

    try:
        _allow_primary(admin, False)
        assert asyncio.run(counts()) == dict.fromkeys(VIEWS, 0)
    finally:
        _allow_primary(admin, True)
    assert asyncio.run(counts()) == world["counts"][tenant_a]
    with admin.connect() as conn:
        # One row, and only that one.
        with pytest.raises(Exception, match="ck_insights_bi_settings_single_row|duplicate key"):
            with conn.begin():
                conn.execute(text("INSERT INTO insights_bi_settings (id, allow_primary) VALUES (false, true)"))


# ---------------------------------------------------------------- schema public writable (L1)


@pytest.mark.parametrize("grantee", ["PUBLIC", "agenticorg_bi"])
def test_creation_is_refused_while_schema_public_is_writable(world, grantee: str) -> None:
    admin, app, tenant_a = world["admin"], world["app"], world["a"]
    with admin.begin() as conn:
        conn.exec_driver_sql(f"GRANT CREATE ON SCHEMA public TO {grantee}")
    try:
        with pytest.raises(bi_access.BiAccessUnavailableError):
            asyncio.run(_as_app(app, tenant_a, lambda s: bi_access.create_login(s, tenant_a, created_by=None, limit=5)))
        # The migration itself goes through. A grant to PUBLIC it leaves alone (other users of the
        # schema may rely on it) and says why logins will be refused; the BI group's own grant it revokes.
        notices = _apply(world["installer_url"], "upgrade", HEAD)
        if grantee == "PUBLIC":
            assert any("Schema public is writable" in notice for notice in notices), notices
        else:
            assert not any("Schema public is writable" in notice for notice in notices), notices
            with admin.connect() as conn:
                assert not conn.execute(
                    text("SELECT has_schema_privilege('agenticorg_bi', 'public', 'CREATE')")
                ).scalar_one()
    finally:
        with admin.begin() as conn:
            conn.exec_driver_sql(f"REVOKE CREATE ON SCHEMA public FROM {grantee}")
    with admin.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM insights_bi_logins WHERE tenant_id = :t AND revoked_at IS NULL"),
                {"t": tenant_a},
            ).scalar_one()
            == 1
        )


# ---------------------------------------------------------------- re-running the upgrade (M2)


def test_the_upgrade_runs_again_with_logins_on_the_cluster(world) -> None:
    admin, login = world["admin"], world["login_a"]
    with admin.connect() as conn:
        # PostgreSQL 16 made the admin role (the login's creator) an ADMIN-only member of the login.
        assert conn.execute(
            text(
                "SELECT m.admin_option AND NOT m.inherit_option AND NOT m.set_option FROM pg_auth_members m "
                "WHERE m.member = 'agenticorg_bi_admin'::regrole AND m.roleid = CAST(:r AS regrole)"
            ),
            {"r": login.role_name},
        ).scalar_one()
    notices = _apply(world["installer_url"], "upgrade", HEAD)
    assert not any("Schema public is writable" in notice for notice in notices)

    async def still_reads() -> int:
        conn = await _bi_connect(login.role_name, login.password)
        try:
            return int(await conn.fetchval("SELECT count(*) FROM insights_bi.agents"))
        finally:
            await conn.close()

    assert asyncio.run(still_reads()) == world["counts"][world["a"]]["agents"]


@pytest.mark.parametrize(
    ("grant", "message"),
    [
        ("GRANT {probe} TO agenticorg_bi_lookup", "must not be members of other roles"),
        ("GRANT {probe} TO agenticorg_bi", "must not be members of other roles"),
        ("GRANT agenticorg_bi_reader TO {probe}", "hold access to the BI reader"),
    ],
)
def test_the_upgrade_still_refuses_an_unsafe_membership(world, grant: str, message: str) -> None:
    admin = world["admin"]
    probe = "t_biprobe_" + uuid.uuid4().hex[:12]
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE ROLE "{probe}" NOLOGIN')
        conn.exec_driver_sql(grant.format(probe=f'"{probe}"'))
    try:
        with pytest.raises(Exception, match=message):
            _apply(world["installer_url"], "upgrade", HEAD)
    finally:
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP ROLE "{probe}"')


# ---------------------------------------------------------------- limits, tenant binding and revocation


def test_limit_tenant_binding_and_revocation(world) -> None:
    app, tenant_a, tenant_b = world["app"], world["a"], world["b"]
    login_a = world["login_a"]

    async def scenario() -> None:
        # The limit (2 here) is per tenant: tenant A's second login is created, its third refused.
        second = await _as_app(app, tenant_a, lambda s: bi_access.create_login(s, tenant_a, created_by=None, limit=2))
        with pytest.raises(bi_access.BiLoginLimitError):
            await _as_app(app, tenant_a, lambda s: bi_access.create_login(s, tenant_a, created_by=None, limit=2))
        # The function is bound to the session's tenant: a tenant B session cannot create one for A ...
        with pytest.raises(bi_access.BiAccessError) as mismatch:
            await _as_app(app, tenant_b, lambda s: bi_access.create_login(s, tenant_a, created_by=None, limit=3))
        assert str(mismatch.value) == "42501"
        # ... nor revoke A's login, even naming tenant B's own id.
        with pytest.raises(bi_access.BiLoginNotFoundError):
            await _as_app(app, tenant_b, lambda s: bi_access.revoke_login(s, tenant_b, login_a.role_name))
        with pytest.raises(bi_access.BiAccessError):
            await _as_app(app, tenant_b, lambda s: bi_access.revoke_login(s, tenant_a, login_a.role_name))
        # The application lists only its tenant's logins (forced RLS on the mapping table).
        listed = await _as_app(app, tenant_a, lambda s: bi_access.list_logins(s, tenant_a))
        assert {row["role_name"] for row in listed} == {login_a.role_name, second.role_name}
        listed_b = await _as_app(app, tenant_b, lambda s: bi_access.list_logins(s, tenant_a))
        assert listed_b == []

        held = await _bi_connect(login_a.role_name, login_a.password)
        try:
            assert await held.fetchval("SELECT count(*) FROM insights_bi.agents") == 2
            result = await _as_app(app, tenant_a, lambda s: bi_access.revoke_login(s, tenant_a, login_a.role_name))
            assert result == "dropped"
            # A session opened before the revoke now reads nothing: its role, and with it the
            # membership of the BI group, is gone (and the mapping is revoked as well).
            for view in VIEWS:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await held.fetchval(f"SELECT count(*) FROM insights_bi.{view}")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await held.fetchval("SELECT count(*) FROM public.run_facts WHERE tenant_id IS NOT NULL")
        finally:
            await held.close()
        # And the login can no longer connect.
        with pytest.raises((asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError)):
            await _bi_connect(login_a.role_name, login_a.password)
        # Revoking again is idempotent; a name that is not a BI login is not found.
        assert await _as_app(app, tenant_a, lambda s: bi_access.revoke_login(s, tenant_a, login_a.role_name)) == (
            "dropped"
        )
        with pytest.raises(bi_access.BiLoginNotFoundError):
            await _as_app(app, tenant_a, lambda s: bi_access.revoke_login(s, tenant_a, "agenticorg_bi_admin"))
        # Tenant B's login is untouched.
        login_b = world["login_b"]
        conn_b = await _bi_connect(login_b.role_name, login_b.password)
        try:
            assert await conn_b.fetchval("SELECT count(*) FROM insights_bi.spend_daily") == 2
        finally:
            await conn_b.close()
        # With A's first login revoked, a new one fits under the limit again.
        third = await _as_app(app, tenant_a, lambda s: bi_access.create_login(s, tenant_a, created_by=None, limit=2))
        for created in (second, third):
            assert await _as_app(app, tenant_a, lambda s, r=created.role_name: bi_access.revoke_login(s, tenant_a, r))

    asyncio.run(scenario())
    with world["admin"].connect() as conn:
        assert not _exists(conn, login_a.role_name)
        revoked_at = conn.execute(
            text("SELECT revoked_at FROM insights_bi_logins WHERE role_name = :r"), {"r": login_a.role_name}
        ).scalar_one()
        assert revoked_at is not None


def test_a_revoked_login_that_owns_a_large_object_loses_access_inside_its_open_transaction(world) -> None:
    """DROP ROLE fails while the login owns a large object (any login can create one).

    The revoke then leaves the role, unable to log in, and takes away its BI
    group membership. A REPEATABLE READ transaction the login opened before the
    revoke still sees the mapping row as it was, so only the membership revoke
    (privilege checks read the current catalogue) ends its access there: from
    its next statement that takes a lock it does not hold yet, and from then
    on for every view, including the ones it had already read.
    """
    app, tenant_b, admin = world["app"], world["b"], world["admin"]

    async def scenario() -> str:
        extra = await _as_app(app, tenant_b, lambda s: bi_access.create_login(s, tenant_b, created_by=None, limit=2))
        held = await _bi_connect(extra.role_name, extra.password)
        try:
            await held.execute("SET default_transaction_read_only = off")
            assert await held.fetchval("SELECT lo_create(0)")
            await held.execute("RESET default_transaction_read_only")
            transaction = held.transaction(isolation="repeatable_read", readonly=True)
            await transaction.start()
            assert await held.fetchval("SELECT count(*) FROM insights_bi.spend_daily") == 2
            result = await _as_app(app, tenant_b, lambda s: bi_access.revoke_login(s, tenant_b, extra.role_name))
            assert result == "nologin"
            # Same transaction, same snapshot (the mapping still reads as active there), but the
            # group membership is gone: a view it has not touched yet is refused ...
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with held.transaction():
                    await held.fetchval("SELECT count(*) FROM insights_bi.agents")
            # ... and so, from then on, is the one it already read.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with held.transaction():
                    await held.fetchval("SELECT count(*) FROM insights_bi.spend_daily")
            await transaction.rollback()
            for view in VIEWS:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await held.fetchval(f"SELECT count(*) FROM insights_bi.{view}")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await held.fetchval(f"SELECT count(*) FROM insights_bi.{view}_rows()")
        finally:
            await held.close()
        with pytest.raises(asyncpg.InvalidAuthorizationSpecificationError):
            await _bi_connect(extra.role_name, extra.password)
        return extra.role_name

    role = asyncio.run(scenario())
    with admin.begin() as conn:
        assert not conn.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar_one()
        assert not conn.execute(
            text("SELECT pg_has_role(CAST(:r AS regrole), 'agenticorg_bi', 'MEMBER')"), {"r": role}
        ).scalar_one()
        assert conn.execute(
            text("SELECT revoked_at IS NOT NULL FROM insights_bi_logins WHERE role_name = :r"), {"r": role}
        ).scalar_one()
        # Revoking it again changes nothing and still answers nologin.
        conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": str(tenant_b)})
        assert (
            conn.execute(
                text("SELECT public.insights_bi_revoke_login(:t, :r)"), {"t": tenant_b, "r": role}
            ).scalar_one()
            == "nologin"
        )
        conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
        conn.exec_driver_sql(f'DROP ROLE "{role}"')


def test_downgrade_refuses_while_a_login_is_active(world) -> None:
    # Tenant B's login is still active: the installer's downgrade must refuse and change nothing.
    with pytest.raises(Exception, match="still active"):
        _apply(_sync_url(world["installer"], PASSWORD), "downgrade", PREVIOUS)
    with world["admin"].connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
        assert conn.execute(text("SELECT to_regclass('insights_bi.run_facts') IS NOT NULL")).scalar()
    # Revoke it so the module teardown can downgrade.
    tenant_b, login_b = world["b"], world["login_b"]
    assert (
        asyncio.run(_as_app(world["app"], tenant_b, lambda s: bi_access.revoke_login(s, tenant_b, login_b.role_name)))
        == "dropped"
    )
