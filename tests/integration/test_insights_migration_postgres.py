# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z106 against real Postgres: upgrade, downgrade, upgrade again, both build paths, RLS.

The migration runs in a scratch schema holding only a ``tenants`` table, so the
test needs neither the full revision chain nor its extensions and leaves the
database's own schema untouched. It checks the two ways a database reaches the
tables (the migration on an existing database; the ORM baseline of an empty
database followed by the migration), that the result matches the ORM models
apart from the migration-owned partitions, that facts land in their month's
partition (or the default one past the horizon), that the unique index makes a
re-written fact a no-op, that the CHECK constraints refuse unknown values, and
that a role without BYPASSRLS sees and writes only its own tenant's rows.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, Table, create_engine, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.models.insights import (
    RUN_FACTS_STATIC_PARTITIONS,
    InsightsDaily,
    InsightsRollupState,
    RunFact,
)

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z106_insights_foundation.py"
TABLES = ("run_facts", "insights_daily", "insights_rollup_state")

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"
)


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_pg", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metadata() -> MetaData:
    """The three insights tables plus a minimal ``tenants`` for their foreign keys."""
    md = MetaData()
    Table("tenants", md, Column("id", UUID(as_uuid=True), primary_key=True))
    for model in (RunFact, InsightsDaily, InsightsRollupState):
        model.__table__.to_metadata(md)
    return md


@pytest.fixture
def scratch() -> Iterator[tuple[object, str]]:
    engine = create_engine(_sync_url())
    schema = "insights_mig_" + uuid.uuid4().hex[:12]
    with engine.begin() as conn:
        # Identifier is a fixed prefix plus UUID hex.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'CREATE TABLE "{schema}".tenants (id UUID PRIMARY KEY)')
    try:
        yield engine, schema
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def _in_schema(conn: Connection, schema: str) -> None:
    conn.exec_driver_sql(f'SET search_path TO "{schema}"')


def _run(engine, schema: str, action: str) -> None:
    migration = _migration()
    with engine.begin() as conn:
        _in_schema(conn, schema)
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, action)()


def _relations(engine, schema: str) -> dict[str, tuple[str, bool, bool]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND c.relkind IN ('r', 'p') AND c.relname <> 'tenants'
            """),
            {"schema": schema},
        ).all()
    return {name: (kind, rls, forced) for name, kind, rls, forced in rows}


def _drift(engine, schema: str) -> list[object]:
    with engine.connect() as conn:
        _in_schema(conn, schema)
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        diffs = compare_metadata(context, _metadata())
    # As in test_alembic_e2e: the partitions (and the index copies the parent's
    # indexes create on them) are migration owned.
    partitions = set(RUN_FACTS_STATIC_PARTITIONS)
    return [
        d
        for d in diffs
        if not (d[0] == "remove_table" and d[1].name in partitions)
        and not (d[0] == "remove_index" and d[1].table.name in partitions)
    ]


def _assert_built(engine, schema: str) -> None:
    relations = _relations(engine, schema)
    assert set(relations) == {*TABLES, *RUN_FACTS_STATIC_PARTITIONS}
    assert relations["run_facts"][0] == "p"
    for name, (_kind, rls, forced) in relations.items():
        assert rls and forced, name
    with engine.connect() as conn:
        policies = conn.execute(
            text("SELECT tablename, qual, with_check FROM pg_policies WHERE schemaname = :schema"),
            {"schema": schema},
        ).all()
        attached = conn.execute(
            text("""
                SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent
                JOIN pg_namespace n ON n.oid = p.relnamespace
                WHERE n.nspname = :schema AND p.relname = 'run_facts'
            """),
            {"schema": schema},
        ).scalar()
    assert attached == len(RUN_FACTS_STATIC_PARTITIONS) == 28
    by_table = {row.tablename: row for row in policies}
    assert set(by_table) == set(relations)
    for row in by_table.values():
        assert "current_setting('agenticorg.tenant_id'::text, true)" in row.qual
        assert "current_setting('agenticorg.tenant_id'::text, true)" in row.with_check
    assert _drift(engine, schema) == []


def test_upgrade_downgrade_upgrade_on_an_existing_database(scratch) -> None:
    engine, schema = scratch
    _run(engine, schema, "upgrade")
    _assert_built(engine, schema)
    # Every statement is guarded, so a second run changes nothing.
    _run(engine, schema, "upgrade")
    _assert_built(engine, schema)
    _run(engine, schema, "downgrade")
    assert _relations(engine, schema) == {}
    _run(engine, schema, "upgrade")
    _assert_built(engine, schema)


def test_orm_baseline_then_migration_matches_the_migration_path(scratch) -> None:
    engine, schema = scratch
    with engine.begin() as conn:
        _in_schema(conn, schema)
        md = _metadata()
        md.create_all(conn, tables=[md.tables[name] for name in TABLES])
    assert _relations(engine, schema)["run_facts"][0] == "p"
    _run(engine, schema, "upgrade")
    _assert_built(engine, schema)


def _insert_fact(conn: Connection, tenant: uuid.UUID, run_key: str, started_at: datetime, **extra) -> int:
    values = {
        "id": uuid.uuid4(),
        "tenant_id": tenant,
        "run_key": run_key,
        "entrypoint": "agents",
        "status": "completed",
        "started_at": started_at,
        **extra,
    }
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    result = conn.execute(
        text(
            f"INSERT INTO run_facts ({columns}) VALUES ({params}) "  # noqa: S608 - fixed column names
            "ON CONFLICT (tenant_id, run_key, started_at) DO NOTHING"
        ),
        values,
    )
    return result.rowcount


def test_partition_routing_idempotency_checks_and_tenant_isolation(scratch) -> None:
    engine, schema = scratch
    _run(engine, schema, "upgrade")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    role = "insights_rls_" + uuid.uuid4().hex[:12]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (id) VALUES (:a), (:b)").bindparams(a=tenant_a, b=tenant_b))
        # Identifier is a fixed prefix plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS')
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    try:
        november = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        far_future = datetime(2031, 1, 1, tzinfo=UTC)
        with engine.begin() as conn:
            _in_schema(conn, schema)
            conn.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": str(tenant_a)})
            assert _insert_fact(conn, tenant_a, "agents:corr-1", november, tokens_total=120, cost_usd=0.012) == 1
            # The same run written again is a no-op.
            assert _insert_fact(conn, tenant_a, "agents:corr-1", november) == 0
            assert _insert_fact(conn, tenant_a, "agents:corr-2", far_future) == 1
            conn.execute(
                text("""
                    INSERT INTO insights_daily (id, tenant_id, day, dims_hash, entrypoint, status, environment,
                        data_quality, runs, latency_buckets)
                    VALUES (:id, :t, '2026-11-15', :h, 'agents', 'completed', 'production', 'measured', 1,
                        ARRAY[1,0,0,0,0,0,0,0,0,0,0,0])
                """),
                {"id": uuid.uuid4(), "t": tenant_a, "h": "a" * 64},
            )
            conn.execute(
                text("INSERT INTO insights_rollup_state (id, tenant_id, day, status) VALUES (:id, :t, :d, 'rolled')"),
                {"id": uuid.uuid4(), "t": tenant_a, "d": november.date()},
            )
            routed = dict(conn.execute(text("SELECT run_key, tableoid::regclass::text FROM run_facts")).all())
            assert routed == {"agents:corr-1": "run_facts_y2026m11", "agents:corr-2": "run_facts_default"}
            row = conn.execute(
                text(
                    "SELECT environment, data_quality, hitl_required, tokens_in, cost_usd FROM run_facts "
                    "WHERE run_key = 'agents:corr-1'"
                )
            ).one()
            assert tuple(row[:4]) == ("production", "measured", False, 0) and float(row.cost_usd) == 0.012

        # Tenant B sees none of tenant A's rows, and cannot write rows as tenant A.
        with engine.begin() as conn:
            _in_schema(conn, schema)
            conn.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": str(tenant_b)})
            for table in (*TABLES, "run_facts_y2026m11", "run_facts_default"):
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table  # noqa: S608
        with pytest.raises(DBAPIError, match="row-level security"):
            with engine.begin() as conn:
                _in_schema(conn, schema)
                conn.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": str(tenant_b)})
                _insert_fact(conn, tenant_a, "agents:corr-3", november)
        # No tenant context: nothing is visible.
        with engine.begin() as conn:
            _in_schema(conn, schema)
            conn.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert conn.execute(text("SELECT count(*) FROM run_facts")).scalar() == 0

        # CHECK constraints refuse values outside the allowed sets.
        for bad in ({"status": "exploded"}, {"entrypoint": "telepathy"}, {"environment": "staging"},
                    {"data_quality": "guessed"}):  # fmt: skip
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    _in_schema(conn, schema)
                    _insert_fact(conn, tenant_a, "agents:bad-" + uuid.uuid4().hex, november, **bad)
        with pytest.raises(IntegrityError, match="ck_insights_daily_latency_buckets"):
            with engine.begin() as conn:
                _in_schema(conn, schema)
                conn.execute(
                    text("""
                        INSERT INTO insights_daily (id, tenant_id, day, dims_hash, entrypoint, status,
                            environment, data_quality, latency_buckets)
                        VALUES (:id, :t, '2026-11-16', :h, 'agents', 'completed', 'production', 'measured',
                            ARRAY[1,2,3])
                    """),
                    {"id": uuid.uuid4(), "t": tenant_a, "h": "b" * 64},
                )
        with pytest.raises(IntegrityError, match="ck_insights_rollup_state_status"):
            with engine.begin() as conn:
                _in_schema(conn, schema)
                conn.execute(
                    text(
                        "INSERT INTO insights_rollup_state (id, tenant_id, day, status) "
                        "VALUES (:id, :t, '2026-11-17', 'pending')"
                    ),
                    {"id": uuid.uuid4(), "t": tenant_a},
                )
        with pytest.raises(IntegrityError, match="ux_insights_rollup_state_tenant_day"):
            with engine.begin() as conn:
                _in_schema(conn, schema)
                conn.execute(
                    text(
                        "INSERT INTO insights_rollup_state (id, tenant_id, day, status) VALUES (:id, :t, :d, 'rolled')"
                    ),
                    {"id": uuid.uuid4(), "t": tenant_a, "d": november.date()},
                )
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
