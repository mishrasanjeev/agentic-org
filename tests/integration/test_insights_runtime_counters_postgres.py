# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Runtime counters against real Postgres, written and read by a NOBYPASSRLS role under forced RLS.

Two tenants ("Alpha" and "Bravo") are seeded with a tool call, a guardrail
counter and a security counter each. The tool-call writer adds Alpha's calls
(an agent id that is not a stored agent is kept without it), the counters add
up across flushes in one upsert per tenant, and the Engineering view, the
Compliance view and the connectors explorer show Alpha's figures and never
Bravo's. The migration's upgrade and downgrade (refused while gateway rows exist)
run on their own schema.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from tests.integration.test_insights_isolation_postgres import _seed_tenant
from tests.integration.test_insights_isolation_postgres import world as world  # the shared fixture

pytestmark = pytest.mark.timeout(300)


def _by_key(view: dict) -> dict:
    return {m["key"]: m for m in view["tiles"] + view["charts"]}


@pytest.mark.asyncio
async def test_writers_and_metrics_keep_to_their_tenant(world, monkeypatch) -> None:
    from api.v1 import insights as api
    from api.v1 import insights_explore as explore
    from core.config import settings
    from core.insights import runtime_counters as rc

    monkeypatch.setattr(settings, "insights_enabled", True)
    monkeypatch.setattr(rc, "insights_enabled_for", AsyncMock(return_value=True))
    monkeypatch.setattr(rc, "FLUSH_SECONDS", 3600)
    alpha, bravo = _seed_tenant(world, "Alpha"), _seed_tenant(world, "Bravo")
    with world.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{world.schema}"')
        agent = conn.execute(text("SELECT id FROM agents WHERE tenant_id=:t AND domain='finance'"),
                             {"t": uuid.UUID(alpha.tid)}).scalar_one()

    common = {"tenant_id": alpha.tid, "tool_name": "alpha.post_invoice", "connector_name": "alpha-ledger"}
    assert await rc.record_tool_call(**common, agent_id=agent, status="success", error_code=None, latency_ms=40)
    # Not a stored agent: the call is kept without its agent.
    assert await rc.record_tool_call(**common, agent_id=uuid.uuid4(), status="replayed", error_code=None,
                                     latency_ms=2, idempotency_key="INV-42")
    with world.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{world.schema}"')
        rows = conn.execute(text(
            "SELECT agent_id, status, replayed, idempotency_key FROM tool_calls WHERE tenant_id=:t ORDER BY called_at"
        ), {"t": uuid.UUID(alpha.tid)}).all()
    assert [(r.status, r.replayed) for r in rows] == [("failed", False), ("success", False), ("replayed", True)]
    assert rows[1].agent_id == agent and rows[2].agent_id is None
    assert rows[2].idempotency_key != "INV-42"

    for _ in range(3):
        rc.count_security_event(alpha.tid, "login_failed")
    rc.count_security_event(alpha.tid, "rate_limited")
    rc.count_guardrail_event(alpha.tid, "input", "alpha_pii", "mask", "enforced")
    assert await rc.flush() == 3
    rc.count_guardrail_event(alpha.tid, "input", "alpha_pii", "mask", "enforced")
    assert await rc.flush() == 1
    if rc._FLUSHER is not None:
        rc._FLUSHER.cancel()

    engineering = _by_key(await api.insights_view("engineering", alpha.admin, tenant_id=alpha.tid))
    tools = engineering["engineering.tool_usage"]
    assert tools["value"] == 3
    assert tools["rows"] == [{"label": "alpha.post_invoice", "connector": "alpha-ledger", "value": 3, "failed": 1,
                              "refused": 0, "replayed": 1, "success_rate": 66.7, "p95_ms": tools["rows"][0]["p95_ms"]}]
    compliance = _by_key(await api.insights_view("compliance", alpha.auditor, tenant_id=alpha.tid))
    security = compliance["compliance.security_events"]
    assert security["value"] == 6
    assert {r["label"]: r["value"] for r in security["rows"]} == {"login_failed": 5, "rate_limited": 1}
    guardrails = compliance["compliance.guardrail_events"]
    assert guardrails["value"] == 5
    assert guardrails["rows"] == [{"label": "input / alpha_pii", "stage": "input", "detector": "alpha_pii",
                                   "action": "mask", "mode": "enforced", "value": 5}]
    page = await explore.insights_explore("connectors", alpha.admin, tenant_id=alpha.tid)
    ledger = next(r for r in page["rows"] if r["connector"] == "alpha-ledger")
    assert ledger["calls_7d"] == 3 and ledger["success_rate_7d"] == 66.7

    bravo_view = _by_key(await api.insights_view("compliance", bravo.auditor, tenant_id=bravo.tid))
    assert bravo_view["compliance.security_events"]["value"] == 2
    assert bravo_view["compliance.guardrail_events"]["value"] == 3
    bravo_tools = _by_key(await api.insights_view("engineering", bravo.admin, tenant_id=bravo.tid))
    assert bravo_tools["engineering.tool_usage"]["value"] == 1
    for payload in (bravo_view, bravo_tools):
        assert "alpha" not in json.dumps(payload, default=str).lower()


@pytest.mark.asyncio
async def test_a_counter_for_an_unknown_tenant_is_dropped(world, monkeypatch) -> None:
    from core.config import settings
    from core.insights import runtime_counters as rc

    monkeypatch.setattr(settings, "insights_enabled", True)
    monkeypatch.setattr(rc, "insights_enabled_for", AsyncMock(return_value=True))
    monkeypatch.setattr(rc, "FLUSH_SECONDS", 3600)
    rc.count_security_event(uuid.uuid4(), "webhook_signature_failed")
    # The foreign key to tenants refuses it; the flush logs and carries on.
    assert await rc.flush() == 0
    if rc._FLUSHER is not None:
        rc._FLUSHER.cancel()
    with world.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{world.schema}"')
        assert conn.execute(text("SELECT count(*) FROM security_events_daily")).scalar_one() == 0


def test_the_migration_upgrades_and_refuses_to_downgrade_over_gateway_rows() -> None:
    import importlib
    import os

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    url = os.getenv("AGENTICORG_DB_URL")
    if not url:
        pytest.skip("Requires the disposable Docker Postgres database")
    migration = importlib.import_module("migrations.versions.v6_z115_insights_runtime_counters")
    engine = create_engine(url.replace("+asyncpg", ""))
    schema = "counters_mig_" + uuid.uuid4().hex[:12]
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            conn.exec_driver_sql(f'SET search_path TO "{schema}"')
            conn.exec_driver_sql("CREATE TABLE tenants (id UUID PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE agents (id UUID PRIMARY KEY)")
            conn.exec_driver_sql("""
                CREATE TABLE tool_calls (id UUID PRIMARY KEY, tenant_id UUID NOT NULL, step_exec_id UUID NOT NULL,
                    agent_id UUID NOT NULL REFERENCES agents(id), tool_name VARCHAR(100) NOT NULL,
                    status VARCHAR(20) NOT NULL, called_at TIMESTAMPTZ NOT NULL DEFAULT now())
            """)
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.upgrade()  # a replay is a no-op
            tenant = uuid.uuid4()
            conn.execute(text("INSERT INTO tenants VALUES (:t)"), {"t": tenant})
            conn.execute(text(
                "INSERT INTO tool_calls (id, tenant_id, tool_name, status, connector_name) "
                "VALUES (:id, :t, 'lookup', 'success', 'ledger')"
            ), {"id": uuid.uuid4(), "t": tenant})
            conn.execute(text(
                "INSERT INTO security_events_daily (id, tenant_id, day, event_kind, count) "
                "VALUES (:id, :t, :d, 'login_failed', 1)"
            ), {"id": uuid.uuid4(), "t": tenant, "d": datetime.now(UTC).date()})
            with pytest.raises(Exception, match="ck_security_events_daily_kind"), conn.begin_nested():
                conn.execute(text(
                    "INSERT INTO security_events_daily (id, tenant_id, day, event_kind, count) "
                    "VALUES (:id, :t, :d, 'anything', 1)"
                ), {"id": uuid.uuid4(), "t": tenant, "d": datetime.now(UTC).date()})
            with pytest.raises(Exception, match="gateway tool calls exist"), conn.begin_nested():
                with Operations.context(MigrationContext.configure(conn)):
                    migration.downgrade()
            conn.exec_driver_sql("DELETE FROM tool_calls")
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            gone = conn.execute(text(
                "SELECT to_regclass(:a), to_regclass(:b)"
            ), {"a": f'"{schema}".guardrail_events', "b": f'"{schema}".security_events_daily'}).one()
            assert tuple(gone) == (None, None)
            nullable = conn.execute(text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_schema=:s AND table_name='tool_calls' "
                "AND column_name IN ('step_exec_id','agent_id')"
            ), {"s": schema}).scalars().all()
            assert nullable == ["NO", "NO"]
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()
