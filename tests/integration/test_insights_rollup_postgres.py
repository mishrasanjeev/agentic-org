# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights rollups and reconciliation against real Postgres, as a role without BYPASSRLS.

Each test builds the v6z106 tables in a scratch schema (with a minimal
``tenants`` table) and runs the rollup code through the real
``get_tenant_session`` on an engine that logs in as a NOBYPASSRLS role, so row
security is in force. Facts are seeded on both sides of an IST day's edges
(the day 2026-11-01 IST starts at 18:30 UTC on 31 October, so it spans the
October and November partitions). The tests check the rolled totals, groups
and latency buckets, that a re-roll gives the same rows, reconciliation, the
tamper -> re-roll -> reconciled path, a persisting mismatch, tenant isolation,
and the closed-day and reconcile sweeps.
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.insights import buckets, rollup
from core.tasks import insights_tasks

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z106_insights_foundation.py"
IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 1)  # 2026-10-31T18:30Z .. 2026-11-01T18:30Z
PASSWORD = "local-test-password"  # noqa: S105 - a throwaway role in a disposable test database

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"
)


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_rollup", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_schema(conn: Connection, schema: str) -> None:
    conn.exec_driver_sql(f'SET search_path TO "{schema}"')


class Scratch:
    """A scratch schema with the insights tables, an admin engine and a NOBYPASSRLS runtime role."""

    def __init__(self, admin: Engine, schema: str, role: str) -> None:
        self.admin = admin
        self.schema = schema
        self.role = role

    def tenant(self, data_region: str = "IN", settings: dict | None = None) -> uuid.UUID:
        tenant = uuid.uuid4()
        with self.admin.begin() as conn:
            _in_schema(conn, self.schema)
            conn.execute(
                text("INSERT INTO tenants (id, data_region, settings) VALUES (:id, :region, CAST(:settings AS JSONB))"),
                {"id": tenant, "region": data_region, "settings": json.dumps(settings or {})},
            )
        return tenant

    def fact(self, tenant: uuid.UUID, started_at: datetime, **extra) -> None:
        values = {
            "id": uuid.uuid4(),
            "tenant_id": tenant,
            "run_key": "agents:" + uuid.uuid4().hex,
            "entrypoint": "agents",
            "status": "completed",
            "started_at": started_at,
            "created_at": started_at,
            **extra,
        }
        columns = ", ".join(values)
        params = ", ".join(f":{name}" for name in values)
        with self.admin.begin() as conn:
            _in_schema(conn, self.schema)
            conn.execute(text(f"INSERT INTO run_facts ({columns}) VALUES ({params})"), values)  # noqa: S608

    def rows(self, sql: str, **params) -> list:
        with self.admin.begin() as conn:
            _in_schema(conn, self.schema)
            return list(conn.execute(text(sql), params).all())


@pytest.fixture
async def scratch(monkeypatch) -> AsyncIterator[Scratch]:
    admin = create_engine(_sync_url())
    schema = "insights_rollup_" + uuid.uuid4().hex[:12]
    role = "insights_rollup_" + uuid.uuid4().hex[:12]
    migration = _migration()
    with admin.begin() as conn:
        # Identifiers are a fixed prefix plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(
            f'CREATE TABLE "{schema}".tenants (id UUID PRIMARY KEY, data_region VARCHAR(10) NOT NULL, '
            "settings JSONB NOT NULL DEFAULT '{}', deleted_at TIMESTAMPTZ NULL)"
        )
    with admin.begin() as conn:
        _in_schema(conn, schema)
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        conn.exec_driver_sql(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{PASSWORD}' NOBYPASSRLS")
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    url = admin.url.set(drivername="postgresql+asyncpg", username=role, password=PASSWORD)
    runtime = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    from core import database

    factory = async_sessionmaker(runtime, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    try:
        yield Scratch(admin, schema, role)
    finally:
        await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _seed_day(s: Scratch, tenant: uuid.UUID, agent: uuid.UUID) -> None:
    """Five facts inside DAY (IST), one just before and one at its end."""
    finance = {"agent_id": agent, "agent_type": "ap_processor", "domain": "finance"}
    # 23:59:59.999 IST the day before: not in DAY.
    s.fact(tenant, datetime(2026, 10, 31, 18, 29, 59, 999000, tzinfo=UTC), tokens_total=1000, cost_usd=1)
    # 00:00 IST: the first instant of DAY (October partition).
    s.fact(tenant, _utc(2026, 10, 31, 18, 30), **finance, duration_ms=400, tokens_in=100, tokens_out=50,
           tokens_total=150, cost_usd=Decimal("0.010000"), confidence=0.9, confidence_floor=0.8)  # fmt: skip
    s.fact(tenant, _utc(2026, 10, 31, 23, 0), **finance, duration_ms=1500, tokens_total=300,
           cost_usd=Decimal("0.020000"), confidence=0.7, confidence_floor=0.8, hitl_required=True)  # fmt: skip
    # November partition from here.
    s.fact(tenant, _utc(2026, 11, 1, 5, 0), **finance, status="failed", duration_ms=700_000,
           error_class="TimeoutError")  # fmt: skip
    s.fact(tenant, _utc(2026, 11, 1, 10, 0), entrypoint="a2a", agent_ref="a2a-task-1", tokens_total=500,
           cost_usd=Decimal("0.050000"), outcome_validated=True)  # fmt: skip
    # 23:59:59 IST: the last second of DAY.
    s.fact(tenant, _utc(2026, 11, 1, 18, 29, 59), **finance, duration_ms=500, tokens_total=150,
           cost_usd=Decimal("0.010000"), confidence=0.85, confidence_floor=0.8)  # fmt: skip
    # 00:00 IST the next day: not in DAY.
    s.fact(tenant, _utc(2026, 11, 1, 18, 30), **finance, tokens_total=2000, cost_usd=2)


DAILY_SQL = """
    SELECT agent_id, agent_ref, entrypoint, status, runs, hitl_required, outcome_validated, tokens_in,
        tokens_out, tokens_total, cost_usd, duration_ms_sum, duration_n, latency_buckets, confidence_sum,
        confidence_n, below_floor, dims_hash, environment, data_quality
    FROM insights_daily WHERE tenant_id = :t AND day = :d ORDER BY entrypoint, status
"""


def _state(s: Scratch, tenant: uuid.UUID, day: date):
    found = s.rows("SELECT * FROM insights_rollup_state WHERE tenant_id = :t AND day = :d", t=tenant, d=day)
    return found[0] if found else None


async def test_rollup_reconcile_tamper_and_isolation(scratch: Scratch, monkeypatch) -> None:
    s = scratch
    tenant_a, tenant_b = s.tenant("IN"), s.tenant("IN")
    agent = uuid.uuid4()
    _seed_day(s, tenant_a, agent)
    # Tenant B's facts on the same day must never reach tenant A's rollup.
    s.fact(tenant_b, _utc(2026, 11, 1, 6, 0), agent_id=agent, domain="finance", tokens_total=9999,
           cost_usd=Decimal("9.000000"), duration_ms=100)  # fmt: skip

    # The zone comes from the tenant's reporting settings (IN, unset -> IST).
    found = await rollup.rollup_day(tenant_a, DAY)
    assert found == {"day": "2026-11-01", "rows": 3, "runs": 5, "tokens_total": 1100, "cost_usd": "0.090000"}

    rows = s.rows(DAILY_SQL, t=tenant_a, d=DAY)
    assert [(r.entrypoint, r.status) for r in rows] == [("a2a", "completed"), ("agents", "completed"),
                                                        ("agents", "failed")]  # fmt: skip
    a2a, done, failed = rows
    assert (a2a.agent_id, a2a.agent_ref, a2a.runs, a2a.outcome_validated, a2a.tokens_total) == (
        None, "a2a-task-1", 1, 1, 500,
    )  # fmt: skip
    assert a2a.duration_n == 0 and a2a.latency_buckets == [0] * 12 and a2a.confidence_n == 0
    assert (done.agent_id, done.runs, done.hitl_required, done.tokens_in, done.tokens_out, done.tokens_total) == (
        agent, 3, 1, 100, 50, 600,
    )  # fmt: skip
    assert done.cost_usd == Decimal("0.040000")
    assert (done.duration_ms_sum, done.duration_n) == (2400, 3)
    assert done.latency_buckets == [2, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert done.confidence_sum == pytest.approx(2.45) and done.confidence_n == 3 and done.below_floor == 1
    assert (failed.runs, failed.duration_n, failed.latency_buckets[11], failed.cost_usd) == (1, 1, 1, Decimal("0"))
    for row in rows:
        assert (row.environment, row.data_quality) == ("production", "measured")
        dims = {"company_id": None, "agent_id": row.agent_id, "agent_ref": row.agent_ref,
                "agent_type": "ap_processor" if row.agent_id else None,
                "domain": "finance" if row.agent_id else None, "entrypoint": row.entrypoint, "task_type": None,
                "status": row.status, "environment": "production", "data_quality": "measured"}  # fmt: skip
        assert row.dims_hash == rollup.dims_hash(dims)
    # Percentiles over the day's summed buckets.
    summed = buckets.sum_buckets(r.latency_buckets for r in rows)
    assert sum(summed) == 4
    assert buckets.percentile_from_buckets(summed, 0.5) == 500.0
    assert buckets.percentile_from_buckets(summed, 0.99) == 600_000.0

    state = _state(s, tenant_a, DAY)
    assert state.status == "rolled" and state.rolled_at is not None and state.reconciled_at is None
    assert (state.source_runs, state.rolled_runs, state.source_tokens, state.rolled_tokens) == (5, 5, 1100, 1100)
    assert state.rolled_cost_usd == Decimal("0.090000")

    # Re-rolling gives the same rows (new ids, same content).
    before = [tuple(r) for r in rows]
    assert (await rollup.rollup_day(tenant_a, DAY, zone=IST))["rows"] == 3
    assert [tuple(r) for r in s.rows(DAILY_SQL, t=tenant_a, d=DAY)] == before
    assert len(s.rows("SELECT id FROM insights_daily WHERE tenant_id = :t", t=tenant_a)) == 3

    # Reconcile: agrees without a re-roll.
    assert await rollup.reconcile_day(tenant_a, DAY) == {"day": "2026-11-01", "status": "reconciled",
                                                         "rerolled": False}  # fmt: skip
    state = _state(s, tenant_a, DAY)
    assert state.status == "reconciled" and state.reconciled_at is not None and state.detail is None

    # Tamper with a daily row: the reconcile re-rolls and the day reconciles again.
    s.rows("UPDATE insights_daily SET runs = runs + 5, cost_usd = cost_usd + 1 WHERE tenant_id = :t "
           "AND status = 'failed' RETURNING id", t=tenant_a)  # fmt: skip
    assert await rollup.reconcile_day(tenant_a, DAY) == {"day": "2026-11-01", "status": "reconciled",
                                                         "rerolled": True}  # fmt: skip
    assert [tuple(r) for r in s.rows(DAILY_SQL, t=tenant_a, d=DAY)] == before
    assert _state(s, tenant_a, DAY).status == "reconciled"

    # A rollup that does not fix it (here: a re-roll that does nothing) leaves the day as mismatch.
    s.rows("UPDATE insights_daily SET tokens_total = tokens_total + 7 WHERE tenant_id = :t "
           "AND status = 'failed' RETURNING id", t=tenant_a)  # fmt: skip

    async def no_reroll(tenant_id, day, *, zone=None):
        return {}

    real_rollup_day = rollup.rollup_day
    monkeypatch.setattr(rollup, "rollup_day", no_reroll)
    mismatch = await rollup.reconcile_day(tenant_a, DAY)
    assert mismatch["status"] == "mismatch" and mismatch["detail"] == "tokens 1100!=1107"
    state = _state(s, tenant_a, DAY)
    assert (state.status, state.detail, state.reconciled_at) == ("mismatch", "tokens 1100!=1107", None)
    assert (state.source_tokens, state.rolled_tokens) == (1100, 1107)
    monkeypatch.setattr(rollup, "rollup_day", real_rollup_day)

    # Tenant isolation: B's rollup holds only B's fact, A's rows are untouched, and B's
    # session sees none of A's rows.
    found_b = await rollup.rollup_day(tenant_b, DAY)
    assert (found_b["runs"], found_b["tokens_total"], found_b["cost_usd"]) == (1, 9999, "9.000000")
    assert len(s.rows("SELECT id FROM insights_daily WHERE tenant_id = :t", t=tenant_a)) == 3
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_b) as session:
        for table in ("insights_daily", "insights_rollup_state", "run_facts"):
            seen = (await session.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # noqa: S608
                                          {"t": tenant_a})).scalar()  # fmt: skip
            assert seen == 0, table
        assert (await session.execute(text("SELECT count(*) FROM insights_daily"))).scalar() == 1


async def test_rollup_uses_the_tenants_reporting_zone(scratch: Scratch) -> None:
    s = scratch
    # A US tenant reporting in New York: 1 November 2026 is the 25-hour DST day (04:00Z .. 05:00Z next day).
    tenant = s.tenant("US", {"reporting": {"timezone": "America/New_York"}})
    s.fact(tenant, _utc(2026, 11, 1, 3, 59), duration_ms=10)  # 23:59 EDT on 31 October
    s.fact(tenant, _utc(2026, 11, 1, 4, 0), duration_ms=10)
    s.fact(tenant, _utc(2026, 11, 2, 4, 59), duration_ms=10)  # 23:59 EST on 1 November
    s.fact(tenant, _utc(2026, 11, 2, 5, 0), duration_ms=10)
    assert (await rollup.rollup_day(tenant, DAY))["runs"] == 2


async def test_closed_day_and_reconcile_sweeps(scratch: Scratch, monkeypatch) -> None:
    s = scratch
    tenant_on, tenant_off = s.tenant("IN"), s.tenant("IN")
    deleted = s.tenant("IN")
    s.rows("UPDATE tenants SET deleted_at = now() WHERE id = :t RETURNING id", t=deleted)
    for tenant in (tenant_on, tenant_off, deleted):
        s.fact(tenant, _utc(2026, 11, 1, 6, 0), tokens_total=10, cost_usd=Decimal("0.001000"), duration_ms=100)

    async def enabled(tenant_id):
        return tenant_id != tenant_off

    monkeypatch.setattr(insights_tasks, "_enabled_for", enabled)
    # 03:00 IST on 2 November: yesterday is DAY.
    now = datetime(2026, 11, 2, 3, 0, tzinfo=IST).astimezone(UTC)
    monkeypatch.setattr(insights_tasks, "_now", lambda: now)

    def as_of_now() -> None:
        # rolled_at is the database clock; the sweep's clock is faked: the rollups ran "now".
        s.rows("UPDATE insights_rollup_state SET rolled_at = :now WHERE rolled_at IS NOT NULL RETURNING 1", now=now)

    found = await insights_tasks._sweep("rollup_closed_days", insights_tasks._roll_closed)
    # Every one of the last 31 days had no rollup: all caught up, for the one enabled tenant.
    assert found == {"tenants": 1, "days": 31, "skipped": 1, "failed": 0}
    states = {r.day: r for r in s.rows("SELECT day, status, rolled_runs FROM insights_rollup_state "
                                        "WHERE tenant_id = :t", t=tenant_on)}  # fmt: skip
    assert set(states) == {DAY - timedelta(days=back) for back in range(31)}
    assert states[DAY].rolled_runs == 1 and states[DAY - timedelta(days=1)].rolled_runs == 0
    assert s.rows("SELECT id FROM insights_rollup_state WHERE tenant_id IN (:a, :b)", a=tenant_off, b=deleted) == []
    as_of_now()

    # Nothing new: the next hourly run rolls nothing.
    found = await insights_tasks._sweep("rollup_closed_days", insights_tasks._roll_closed)
    assert found["days"] == 0
    # A late fact for DAY (created after its rollup) makes the sweep roll DAY again.
    s.fact(tenant_on, _utc(2026, 11, 1, 7, 0), tokens_total=5, duration_ms=100, created_at=now + timedelta(minutes=1))
    # A late fact five days back, written now: its day is rolled again too (created_at, not started_at).
    five_back = DAY - timedelta(days=4)
    s.fact(tenant_on, datetime(2026, 10, 28, 12, 0, tzinfo=IST), duration_ms=100,
           created_at=now + timedelta(minutes=1))  # fmt: skip
    found = await insights_tasks._sweep("rollup_closed_days", insights_tasks._roll_closed)
    assert found["days"] == 2
    assert _state(s, tenant_on, DAY).rolled_runs == 2 and _state(s, tenant_on, five_back).rolled_runs == 1
    as_of_now()

    # Reconcile from 02:30 IST: the last three closed days not reconciled yet.
    found = await insights_tasks._sweep("reconcile_yesterday", insights_tasks._reconcile)
    assert found == {"tenants": 1, "days": 3, "skipped": 1, "failed": 0}
    for back in range(3):
        assert _state(s, tenant_on, DAY - timedelta(days=back)).status == "reconciled"
    found = await insights_tasks._sweep("reconcile_yesterday", insights_tasks._reconcile)
    assert found["days"] == 0

    # rollup_today rolls the enabled tenant's local today (2 November in IST).
    s.fact(tenant_on, _utc(2026, 11, 1, 21, 0), tokens_total=3)  # 02:30 IST on 2 November
    found = await insights_tasks._sweep("rollup_today", insights_tasks._roll_today)
    assert found == {"tenants": 1, "days": 1, "skipped": 1, "failed": 0}
    assert _state(s, tenant_on, date(2026, 11, 2)).rolled_tokens == 3
