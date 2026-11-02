# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The run-fact writer against real Postgres, through the runtime's own tenant session as a NOBYPASSRLS role.

v6z106 runs in a scratch schema holding only a ``tenants`` table (as in
``test_insights_migration_postgres.py``), so the test needs neither the full
revision chain nor its extensions. ``core.database`` is pointed at an engine
that logs in as a role without BYPASSRLS whose search path is that schema, so
every write goes through ``get_tenant_session`` and the forced row-level
security of ``run_facts`` exactly as in production. Checked: a fact lands once
however often it is written, only its tenant reads it, another tenant cannot
write it, the runner's recorder writes rows the CHECK constraints accept, a
seeded batch is one statement, and nothing is written while Insights is off.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z106_insights_foundation.py"

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"
)


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration() -> Any:
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_writer_pg", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Env:
    def __init__(self, admin: Any, schema: str, tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
        self.admin = admin
        self.schema = schema
        self.tenant_a = tenant_a
        self.tenant_b = tenant_b

    def count(self, where: str = "true", **params: Any) -> int:
        with self.admin.connect() as conn:
            conn.exec_driver_sql(f'SET search_path TO "{self.schema}"')
            # Fixed SQL fragments from this test only.
            return conn.execute(text(f"SELECT count(*) FROM run_facts WHERE {where}"), params).scalar()  # noqa: S608

    def rows(self, where: str = "true", **params: Any) -> list[Any]:
        with self.admin.connect() as conn:
            conn.exec_driver_sql(f'SET search_path TO "{self.schema}"')
            return list(
                conn.execute(text(f"SELECT * FROM run_facts WHERE {where} ORDER BY run_key"), params).mappings()  # noqa: S608
            )


@pytest.fixture
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    from core import database
    from core.config import settings

    admin = create_engine(_sync_url())
    schema = "insights_w_" + uuid.uuid4().hex[:12]
    role = "insights_writer_" + uuid.uuid4().hex[:12]
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    runtime = None
    with admin.begin() as conn:
        # Identifiers are fixed prefixes plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'CREATE TABLE "{schema}".tenants (id UUID PRIMARY KEY)')
        conn.exec_driver_sql(f'SET search_path TO "{schema}"')
        with Operations.context(MigrationContext.configure(conn)):
            _migration().upgrade()
        conn.execute(text("INSERT INTO tenants (id) VALUES (:a), (:b)"), {"a": tenant_a, "b": tenant_b})
        conn.exec_driver_sql(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'local-test-password' NOBYPASSRLS")
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    try:
        url = database_url_for(role)
        runtime = create_async_engine(
            url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
        )
        factory = async_sessionmaker(runtime, expire_on_commit=False)
        monkeypatch.setattr(database, "async_session_factory", factory)
        monkeypatch.setattr(database, "current_session_factory", lambda: factory)
        monkeypatch.setattr(settings, "insights_enabled", True)
        # The tenant flag is read through its own module (a scratch schema has no feature_flags table).
        opted_out: set[uuid.UUID] = set()

        async def is_enabled(flag_key: str, *, tenant_id: Any = None, user_id: Any = None, default: bool = False):
            assert flag_key == "insights_dashboard" and default is True
            return tenant_id not in opted_out

        monkeypatch.setattr("core.feature_flags.is_enabled", is_enabled)
        environment = _Env(admin, schema, tenant_a, tenant_b)
        environment.opted_out = opted_out  # type: ignore[attr-defined]
        yield environment
    finally:
        if runtime is not None:
            await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def database_url_for(role: str) -> Any:
    from sqlalchemy.engine import make_url

    return make_url(os.environ["AGENTICORG_DB_URL"]).set(username=role, password="local-test-password")


NOVEMBER = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


async def test_a_fact_is_written_once_and_only_its_tenant_reads_it(env: _Env) -> None:
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.insights.run_facts import record_run_fact
    from core.models.insights import RunFact

    fact = {
        "tenant_id": str(env.tenant_a),
        "run_key": "chat:corr-1",
        "entrypoint": "chat",
        "agent_id": "a2a-task-7",
        "status": "operator_override",
        "confidence": 3.0,
        "tokens_in": 120,
        "tokens_out": 30,
        "cost_usd": 0.0125,
        "duration_ms": 900,
        "started_at": NOVEMBER,
    }
    assert await record_run_fact(**fact) is True
    # The same run written again (a retried hook) is a no-op.
    assert await record_run_fact(**fact) is True
    assert env.count() == 1
    (row,) = env.rows()
    assert row["status"] == "refused" and row["error_class"] == "operator_override"
    assert row["agent_id"] is None and row["agent_ref"] == "a2a-task-7" and row["confidence"] == 1.0
    assert row["tokens_total"] == 150 and row["cost_usd"] == Decimal("0.012500")
    assert row["data_quality"] == "measured" and row["environment"] == "production"

    # Only tenant A's own session reads it.
    async with get_tenant_session(env.tenant_a) as session:
        assert (await session.execute(select(func.count()).select_from(RunFact))).scalar() == 1
    async with get_tenant_session(env.tenant_b) as session:
        assert (await session.execute(select(func.count()).select_from(RunFact))).scalar() == 0
        assert (await session.execute(text("SELECT count(*) FROM run_facts_y2026m11"))).scalar() == 0


async def test_tenant_b_cannot_write_a_fact_for_tenant_a(env: _Env) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from core.database import get_tenant_session
    from core.insights.run_facts import normalise_fact
    from core.models.insights import RunFact

    row = normalise_fact({"tenant_id": env.tenant_a, "run_key": "agents:forged", "status": "completed"})
    with pytest.raises(DBAPIError, match="row-level security"):
        async with get_tenant_session(env.tenant_b) as session:
            await session.execute(insert(RunFact).values([row]))
    assert env.count() == 0


async def test_the_runner_recorder_writes_rows_the_constraints_accept(env: _Env) -> None:
    from core.governance.model_gateway import RouteDecision
    from core.insights.run_facts import RunRecorder

    decision = RouteDecision(provider="openai", model="gpt-4o", correlation_id="corr-run", reason="p", applied=True)
    outcomes = {
        "completed": {
            "status": "completed",
            "confidence": 0.92,
            "performance": {"total_latency_ms": 1200, "llm_tokens_used": 500, "llm_cost_usd": 0.0021},
        },
        "hitl": {"status": "hitl_triggered", "confidence": 0.41, "performance": {"llm_tokens_used": 80}},
        "gateway": {"status": "model_gateway_refused", "error_code": "E1015", "performance": {}},
        "guardrail": {"status": "guardrail_blocked", "error_code": "E1016"},
        "limit": {"status": "limit_exceeded"},
        "paused": {"status": "paused"},
    }
    for name, result in outcomes.items():
        recorder = RunRecorder(
            tenant_id=str(env.tenant_a),
            agent_id=str(uuid.uuid4()),
            entrypoint="a2a" if name == "hitl" else "agents",
            agent_type="analyst",
            domain="finance",
            company_id=str(uuid.uuid4()),
            confidence_floor=0.88,
            task_input={"action": name},
            use_case="agent_run",
        )
        recorder.routed(decision, provider="openai", model="gpt-4o", thread_id=f"{env.tenant_a}:{name}")
        recorder.correlation_id = f"corr-{name}"
        assert await recorder.finish(result) is result
    failing = RunRecorder(tenant_id=str(env.tenant_a), agent_id="voice-agent", entrypoint="voice")
    failing.capture({"status": "failed", "error": "message with detail"}, error_class="KeyError")
    await failing.finish()

    rows = {row["task_type"]: row for row in env.rows()}
    assert env.count() == 7
    assert rows["completed"]["status"] == "completed" and rows["completed"]["tokens_total"] == 500
    assert rows["completed"]["duration_ms"] == 1200 and rows["completed"]["provider"] == "openai"
    assert rows["completed"]["run_key"] == "agents:corr-completed"
    assert rows["hitl"]["status"] == "hitl_triggered" and rows["hitl"]["hitl_required"] is True
    assert rows["hitl"]["entrypoint"] == "a2a"
    assert (rows["gateway"]["status"], rows["gateway"]["error_class"]) == ("refused", "E1015")
    assert (rows["guardrail"]["status"], rows["guardrail"]["error_class"]) == ("refused", "E1016")
    assert rows["limit"]["status"] == "limit_exceeded" and rows["paused"]["status"] == "paused"
    (voice,) = env.rows("entrypoint = 'voice'")
    assert voice["status"] == "failed" and voice["error_class"] == "KeyError" and voice["agent_ref"] == "voice-agent"


async def test_a_seeded_batch_is_written_and_marked_seeded(env: _Env) -> None:
    from core.insights.run_facts import record_run_facts

    facts = [
        {"run_key": f"seed:{i}", "status": "completed", "data_quality": "seeded", "started_at": NOVEMBER}
        for i in range(5)
    ]
    assert await record_run_facts(env.tenant_b, facts) == 5
    assert await record_run_facts(env.tenant_b, facts) == 5  # idempotent: nothing new
    assert env.count("tenant_id = :t AND data_quality = 'seeded'", t=env.tenant_b) == 5


async def test_nothing_is_written_while_off(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings
    from core.insights.run_facts import record_run_fact

    env.opted_out.add(env.tenant_b)  # type: ignore[attr-defined]
    assert await record_run_fact(tenant_id=env.tenant_b, run_key="agents:x", status="completed") is False
    monkeypatch.setattr(settings, "insights_enabled", False)
    assert await record_run_fact(tenant_id=env.tenant_a, run_key="agents:y", status="completed") is False
    assert env.count() == 0
