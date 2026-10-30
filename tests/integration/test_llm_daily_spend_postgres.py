# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Gemini daily cap and budget alerts against real Postgres, through the paths that write spend.

Both controls summed ``agent_task_results``, which only the demo seeder
writes, so in production the cap never refused a call and no budget alert
fired. Here a tenant's router calls are priced by the real Gemini path (the
SDK client is the only stand-in) and recorded in ``llm_daily_spend``; the
third call is refused at a $1 cap while another tenant is unaffected. An agent
run's cost, written by the run route's ledger writer, fires a budget alert.
The runtime connects as a NOBYPASSRLS role with the production grants.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

MODEL = "gemini-2.5-flash"
# 4M input tokens at $0.075/M plus 1M output tokens at $0.30/M.
CALL_COST = 0.60


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema():
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


class _FakeGeminiModels:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        self.calls += 1
        usage = SimpleNamespace(prompt_token_count=4_000_000, candidates_token_count=1_000_000)
        return SimpleNamespace(text="Synthetic summary", usage_metadata=usage, candidates=[])


async def _make_tenants(admin, *tenants: uuid.UUID) -> None:
    async with admin.begin() as conn:
        for tenant in tenants:
            await conn.execute(text("""
                INSERT INTO tenants(id,name,slug,plan,data_region,settings)
                VALUES (:id,'Synthetic spend test',:slug,'free','IN','{}')
            """), {"id": tenant, "slug": "spend-test-" + tenant.hex})


async def _runtime_role(admin) -> tuple[str, object]:
    """A NOBYPASSRLS login role with the row DML scripts/create_app_role.sql grants."""
    role = "spend_runtime_" + uuid.uuid4().hex[:12]
    async with admin.begin() as conn:
        # Identifier is a fixed prefix plus UUID hex; never provider/client input.
        await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
        await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        await conn.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION public.gemini_daily_platform_spend() TO "{role}"')
    engine = create_async_engine(admin.url.set(username=role, password="local-test-password"), poolclass=NullPool)
    return role, engine


async def _drop_role(admin, role: str) -> None:
    async with admin.begin() as conn:
        if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
            await conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            await conn.exec_driver_sql(f'DROP ROLE "{role}"')


def _use_engine(monkeypatch, engine) -> None:
    from core import database

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)


@pytest.mark.asyncio
async def test_gemini_cap_refuses_once_recorded_router_spend_reaches_it(monkeypatch):
    from google import genai

    from core.llm.router import DailyBudgetExceeded, LLMRouter

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    role, runtime = None, None
    models = _FakeGeminiModels()
    try:
        await _make_tenants(admin, tenant_a, tenant_b)
        role, runtime = await _runtime_role(admin)
        _use_engine(monkeypatch, runtime)
        # The real provider path: no fake LLM, no cassette, only the SDK client replaced.
        monkeypatch.setenv("AGENTICORG_TEST_FAKE_LLM", "0")
        monkeypatch.delenv("AGENTICORG_MODEL_MODE", raising=False)
        monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "1.0")
        monkeypatch.setenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", "0")
        monkeypatch.setattr(genai, "Client", lambda api_key=None: SimpleNamespace(aio=SimpleNamespace(models=models)))
        monkeypatch.setattr(LLMRouter, "_provider_secret", AsyncMock(return_value="synthetic-key"))
        router = LLMRouter()
        messages = [{"role": "user", "content": "Summarise the synthetic ledger"}]

        for _ in range(2):
            response = await router.complete(messages, model_override=MODEL, tenant_id=str(tenant_a))
            assert response.cost_usd == pytest.approx(CALL_COST)

        async with admin.connect() as conn:
            row = (await conn.execute(text(
                "SELECT provider, model, call_count, cost_usd FROM llm_daily_spend WHERE tenant_id=:t"
            ), {"t": tenant_a})).mappings().one()
        assert (row["provider"], row["model"], row["call_count"]) == ("gemini", MODEL, 2)
        assert float(row["cost_usd"]) == pytest.approx(2 * CALL_COST)

        # $1.20 recorded against a $1 cap: refused before the provider is called.
        with pytest.raises(DailyBudgetExceeded, match="daily spend cap reached"):
            await router.complete(messages, model_override=MODEL, tenant_id=str(tenant_a))
        assert models.calls == 2

        # The cap is per tenant: another tenant's first call goes through.
        await router.complete(messages, model_override=MODEL, tenant_id=str(tenant_b))
        assert models.calls == 3

        from core import database

        async with database.get_tenant_session(tenant_a) as session:
            others = await session.execute(
                text("SELECT count(*) FROM llm_daily_spend WHERE tenant_id=:other"), {"other": tenant_b}
            )
            assert others.scalar_one() == 0

        # The platform-wide cap reads the same rows through the aggregate-only function.
        async with admin.connect() as conn:
            expected = (await conn.execute(text(
                "SELECT COALESCE(SUM(cost_usd),0) FROM llm_daily_spend "
                "WHERE provider='gemini' AND day=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date"
            ))).scalar_one()
        async with runtime.connect() as conn:
            platform = (await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar_one()
        assert float(platform) == pytest.approx(float(expected))
        assert float(platform) >= 3 * CALL_COST - 1e-9
        monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "100.0")
        monkeypatch.setenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", "1.0")
        with pytest.raises(DailyBudgetExceeded, match="platform daily spend cap reached"):
            await router.complete(messages, model_override=MODEL, tenant_id=str(tenant_b))
        assert models.calls == 3
    finally:
        if runtime is not None:
            await runtime.dispose()
        async with admin.begin() as conn:
            await conn.execute(text("DELETE FROM llm_daily_spend WHERE tenant_id IN (:a,:b)"),
                               {"a": tenant_a, "b": tenant_b})
            await conn.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


@pytest.mark.asyncio
async def test_budget_alert_fires_from_recorded_agent_run_cost(monkeypatch):
    from api.v1.agents import _record_cost_ledger
    from core.billing import budget_evaluator
    from core.models.agent import Agent
    from core.models.budget_alert import BudgetAlert
    from core.models.company import Company

    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    tenant = uuid.uuid4()
    company_id, agent_in_company, agent_tenant_wide = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tenant_alert, company_alert = uuid.uuid4(), uuid.uuid4()
    role, runtime = None, None
    try:
        await _make_tenants(admin, tenant)
        async with async_sessionmaker(admin, expire_on_commit=False)() as session:
            session.add(Company(id=company_id, tenant_id=tenant, name="Synthetic Traders", pan="AAAAA0000A"))
            await session.flush()
            for agent_id, company in ((agent_in_company, company_id), (agent_tenant_wide, None)):
                session.add(Agent(
                    id=agent_id, tenant_id=tenant, company_id=company, name="Synthetic AP agent",
                    agent_type="ap_processor", domain="finance", system_prompt_ref="synthetic",
                    hitl_condition="never",
                ))
            await session.flush()
            session.add(BudgetAlert(
                id=tenant_alert, tenant_id=tenant, name="Tenant AI spend", period="monthly",
                threshold_usd=Decimal("5.00"), warn_at_percent=80, notify_channels="email",
            ))
            session.add(BudgetAlert(
                id=company_alert, tenant_id=tenant, company_id=company_id, name="Company AI spend",
                period="monthly", threshold_usd=Decimal("10.00"), warn_at_percent=80, notify_channels="email",
            ))
            await session.commit()

        role, runtime = await _runtime_role(admin)
        _use_engine(monkeypatch, runtime)
        # What POST /agents/{id}/run and /chat/query record after a run.
        assert await _record_cost_ledger(tenant, agent_in_company, {"llm_cost_usd": 3.0, "llm_tokens_used": 900})
        assert await _record_cost_ledger(tenant, agent_tenant_wide, {"llm_cost_usd": 1.0, "llm_tokens_used": 300})

        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        assert await budget_evaluator._spend_since(tenant, None, None, month_start) == Decimal("4.0000")
        assert await budget_evaluator._spend_since(tenant, company_id, None, month_start) == Decimal("3.0000")
        assert await budget_evaluator._spend_since(tenant, uuid.uuid4(), None, month_start) == Decimal("0")

        # The beat job enumerates tenants as the operator role, then works per tenant.
        _use_engine(monkeypatch, admin)
        monkeypatch.setattr(budget_evaluator, "async_session_factory", async_sessionmaker(admin))
        sent = AsyncMock()
        monkeypatch.setattr(budget_evaluator, "_send_notification", sent)
        await budget_evaluator.evaluate_budget_alerts()

        fired = {call.args[0].id: (call.args[1], call.args[2]) for call in sent.await_args_list}
        assert fired.get(tenant_alert) == (Decimal("4.0000"), 80)
        assert company_alert not in fired
        async with admin.connect() as conn:
            triggered = (await conn.execute(
                text("SELECT last_triggered_at FROM budget_alerts WHERE id=:id"), {"id": tenant_alert}
            )).scalar_one()
        assert triggered is not None
    finally:
        if runtime is not None:
            await runtime.dispose()
        async with admin.begin() as conn:
            await conn.execute(text("DELETE FROM budget_alerts WHERE tenant_id=:t"), {"t": tenant})
            await conn.execute(text("DELETE FROM agent_cost_ledger WHERE tenant_id=:t"), {"t": tenant})
            await conn.execute(text("DELETE FROM agents WHERE tenant_id=:t"), {"t": tenant})
            await conn.execute(text("DELETE FROM companies WHERE tenant_id=:t"), {"t": tenant})
            await conn.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tenant})
        if role:
            await _drop_role(admin, role)
        await admin.dispose()


async def _platform_function(conn) -> dict:
    return dict((await conn.execute(text("""
        SELECT r.rolname AS owner, p.prosecdef AS definer, p.proconfig AS config, p.prosrc AS body,
            pg_has_role(current_user,'agenticorg_spend_aggregate','USAGE') AS inherits,
            pg_has_role(current_user,'agenticorg_spend_aggregate','SET') AS can_set,
            has_column_privilege('agenticorg_spend_aggregate','public.agent_task_results','cost_usd','SELECT')
                AS reads_task_results
        FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
        WHERE p.oid='public.gemini_daily_platform_spend()'::regprocedure
    """))).mappings().one())


@pytest.mark.asyncio
async def test_non_superuser_install_replay_and_rollback(monkeypatch, tmp_path):
    """The function is replaced as its owner by an installer that keeps no aggregate rights afterwards."""
    url = os.getenv("AGENTICORG_DB_URL")
    admin = create_async_engine(url, poolclass=NullPool)
    role = "spend_installer_" + uuid.uuid4().hex[:12]
    db = "spend_ledger_" + uuid.uuid4().hex[:12]
    installer = None
    created = False
    v6z85 = importlib.import_module("migrations.versions.v6_z85_lead_delivery_spend")
    migration = importlib.import_module("migrations.versions.v6_z105_llm_daily_spend")
    progress = importlib.import_module("migrations.versions.v4_9_7_migration_progress")
    monkeypatch.setenv("AGENTICORG_MIGRATION_AUDIT_DIR", str(tmp_path))

    def apply(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            action()

    try:
        async with admin.connect() as connection:
            conn = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN CREATEROLE NOBYPASSRLS '
                                      "PASSWORD 'local-test-password'")
            # As in v6z85's proof: only the creator's admin-only management grant.
            await conn.exec_driver_sql(f'GRANT agenticorg_spend_aggregate TO "{role}" '
                                      'WITH ADMIN TRUE, INHERIT FALSE, SET FALSE')
            await conn.exec_driver_sql(f'CREATE DATABASE "{db}" OWNER "{role}"')
            created = True
        installer = create_async_engine(admin.url.set(username=role, password="local-test-password", database=db),
                                        poolclass=NullPool)
        async with installer.begin() as conn:
            await conn.exec_driver_sql("CREATE TABLE tenants(id UUID PRIMARY KEY)")
            await conn.exec_driver_sql("""
                CREATE TABLE agent_task_results(tenant_id UUID,cost_usd NUMERIC,
                    created_at TIMESTAMPTZ,llm_model TEXT,private_note TEXT)
            """)
            await conn.exec_driver_sql("ALTER TABLE agent_task_results ENABLE ROW LEVEL SECURITY")
            await conn.exec_driver_sql("ALTER TABLE agent_task_results FORCE ROW LEVEL SECURITY")
            await conn.exec_driver_sql("""
                CREATE POLICY tenant_scope ON agent_task_results
                USING (tenant_id::text=current_setting('agenticorg.tenant_id',true))
            """)
            await conn.run_sync(lambda c: apply(c, progress.upgrade))
            await conn.run_sync(lambda c: apply(c, v6z85.upgrade))
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            # A replay is a no-op.
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            state = await _platform_function(conn)
            assert state["owner"] == "agenticorg_spend_aggregate"
            assert state["definer"] is True
            assert state["config"] == ["search_path=pg_catalog"]
            assert "public.llm_daily_spend" in state["body"]
            assert (state["inherits"], state["can_set"], state["reads_task_results"]) == (False, False, False)
            assert (await conn.execute(text("SELECT relforcerowsecurity FROM pg_class "
                                           "WHERE oid='public.llm_daily_spend'::regclass"))).scalar() is True
            assert (await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar() == 0
            tenant = uuid.uuid4()
            await conn.execute(text("INSERT INTO tenants VALUES (:id)"), {"id": tenant})
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id',:tenant,true)"),
                               {"tenant": str(tenant)})
            for provider, cost in (("gemini", 0.75), ("claude", 2.0)):
                await conn.execute(text("""
                    INSERT INTO llm_daily_spend(id,tenant_id,day,provider,model,call_count,cost_usd)
                    VALUES (:id,:tenant,(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date,:provider,:model,1,:cost)
                """), {"id": uuid.uuid4(), "tenant": tenant, "provider": provider,
                       "model": f"{provider}-test", "cost": cost})
        async with installer.begin() as conn:
            # Without tenant context the owner sees no rows; the function sums Gemini across tenants.
            assert (await conn.execute(text("SELECT count(*) FROM llm_daily_spend"))).scalar() == 0
            assert float((await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar()) == 0.75
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.downgrade))
            state = await _platform_function(conn)
            assert state["owner"] == "agenticorg_spend_aggregate"
            assert "public.agent_task_results" in state["body"]
            assert (state["inherits"], state["can_set"], state["reads_task_results"]) == (False, False, True)
            assert (await conn.execute(text("SELECT to_regclass('public.llm_daily_spend')"))).scalar() is None
            assert (await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar() == 0
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            assert "public.llm_daily_spend" in (await _platform_function(conn))["body"]
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
