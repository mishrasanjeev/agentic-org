# SPDX-License-Identifier: Apache-2.0
"""Real Postgres isolation and replay checks for the marketing outbox."""
from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import uuid
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture(scope="module", autouse=True)
async def migrated_delivery_schema():
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


async def _ensure_marketing_tenant(engine, tenant_id) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO tenants(id,name,slug,plan,data_region,settings)
            VALUES (:id,'Synthetic test','lead-defaults-test','free','IN','{}') ON CONFLICT(id) DO NOTHING
        """), {"id": tenant_id})


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["agenticorg", "grantex"])
async def test_demo_receipt_on_fresh_schema_keeps_required_lead_defaults(monkeypatch, product):
    from unittest.mock import MagicMock

    from starlette.requests import Request

    from api.v1 import demo
    from core import database, demo_delivery

    url = os.getenv("AGENTICORG_DB_URL")
    if not url:
        pytest.skip("Requires the disposable Docker Postgres database")
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    # submit_demo_request writes demo_requests through get_platform_session,
    # which resolves current_session_factory(); no module-level factory to patch.
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr(demo.auth_state, "check_window_rate", AsyncMock(return_value=False))
    monkeypatch.setenv("AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("AGENTICORG_GRANTEX_ENQUIRIES_ENABLED", "true")
    enqueue = AsyncMock(return_value=False)
    monkeypatch.setattr(demo_delivery, "enqueue", enqueue)
    email = f"local-{uuid.uuid4().hex}@example.org"
    try:
        await _ensure_marketing_tenant(engine, demo_delivery.MARKETING_TENANT)
        opposite_id = uuid.uuid4()
        async with database.get_tenant_session(demo_delivery.MARKETING_TENANT) as session:
            session.add(demo.LeadPipeline(
                id=opposite_id, tenant_id=demo_delivery.MARKETING_TENANT, name="Other product enquiry", email=email,
                source="website" if product == "grantex" else "grantex_website",
            ))
        request = Request({"type": "http", "client": ("198.51.100.15", 1), "headers": []})
        response = await demo.submit_demo_request(
            demo.DemoRequest(name="Synthetic lead defaults", email=email, product=product,
                             message="Synthetic question"),
            request, MagicMock(),
        )
        assert response["lead_id"] is not None
        assert response["lead_id"] != str(opposite_id)
        assert response["delivery_request_id"] is not None
        assert response["agent_triggered"] is False
        assert response["agent_queued"] is (product == "agenticorg")
        async with database.get_tenant_session(demo_delivery.MARKETING_TENANT) as session:
            row = (await session.execute(text("SELECT score_factors,followup_count,page_visits "
                                              "FROM lead_pipeline WHERE id=:id AND tenant_id=:tenant"),
                                         {"id": uuid.UUID(response["lead_id"]),
                                          "tenant": demo_delivery.MARKETING_TENANT})).mappings().one()
            assert row == {"score_factors": {}, "followup_count": 0, "page_visits": []}
            source = (await session.execute(text("SELECT source FROM lead_pipeline WHERE id=:id"),
                                            {"id": uuid.UUID(response["lead_id"])})).scalar_one()
            assert source == ("grantex_website" if product == "grantex" else "website")
            agent_status = (await session.execute(text("SELECT agent_status FROM demo_delivery_jobs WHERE id=:id"),
                                                  {"id": uuid.UUID(response["delivery_request_id"])})).scalar_one()
            assert agent_status == ("skipped" if product == "grantex" else "pending")
        enqueue.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("agent_result,expected", [
    ("completed", "completed"), ("hitl_triggered", "needs_review"), ("failed", "failed"),
])
@pytest.mark.asyncio
async def test_worker_records_agent_outcome_independently_of_mail(monkeypatch, agent_result, expected):
    from core import database, demo_delivery

    url = os.getenv("AGENTICORG_DB_URL")
    if not url:
        pytest.skip("Requires the disposable Docker Postgres database")
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr("api.v1.demo._send_email_notification", lambda _: True)
    monkeypatch.setattr("api.v1.demo._send_trial_confirmation", lambda _: False)
    agent = AsyncMock(return_value={"status": agent_result})
    monkeypatch.setattr("api.v1.sales._run_sales_agent_on_lead", agent)
    lead_id = uuid.uuid4()
    try:
        # Self-contained: the KEK lookup needs the marketing tenant row, which
        # must not depend on another test having inserted it first.
        await _ensure_marketing_tenant(engine, demo_delivery.MARKETING_TENANT)
        job = await demo_delivery.prepare({"name": "Synthetic result", "email": "owner@example.org"}, str(lead_id))
        assert await demo_delivery.dispatch_one(demo_delivery.MARKETING_TENANT, job) == {
            "notification": "smtp_accepted", "confirmation": "failed", "agent": expected,
        }
        state = await demo_delivery.statuses(demo_delivery.MARKETING_TENANT, lead_id)
        assert state[0]["agent_status"] == expected
        assert "payload_encrypted" not in state[0]
        assert await demo_delivery.dispatch_one(demo_delivery.MARKETING_TENANT, job) == {}
        agent.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_restricted_role_spend_and_outbox_isolation(monkeypatch):
    url = os.getenv("AGENTICORG_DB_URL")
    if not url:
        pytest.skip("Requires the disposable Docker Postgres database")
    from core import database, demo_delivery
    from core.llm.router import DailyBudgetExceeded, _todays_gemini_spend_usd, assert_under_gemini_cap

    admin = create_async_engine(url, poolclass=NullPool)
    role = "lead_runtime_" + uuid.uuid4().hex[:12]
    tenant_a = demo_delivery.MARKETING_TENANT
    tenant_b = uuid.uuid4()
    task_a, task_b = uuid.uuid4(), uuid.uuid4()
    app_engine = None
    try:
        async with admin.begin() as conn:
            if (await conn.execute(text("SELECT to_regclass('public.demo_delivery_jobs')"))).scalar() is None:
                pytest.fail("Apply migration v6z85 before running the isolation proof")
            for tenant in (tenant_a, tenant_b):
                await conn.execute(text("""
                    INSERT INTO tenants(id,name,slug,plan,data_region,settings)
                    VALUES (:id,'Synthetic test',:slug,'free','IN','{}') ON CONFLICT(id) DO NOTHING
                """), {"id": tenant, "slug": "lead-test-" + tenant.hex})
            for task, tenant, cost in ((task_a, tenant_a, 1.25), (task_b, tenant_b, 2.75)):
                await conn.execute(text("""
                    INSERT INTO agent_task_results(id,tenant_id,agent_id,agent_type,domain,task_type,
                        tokens_used,cost_usd,duration_ms,status,llm_model,hitl_required)
                    VALUES (:id,:tenant,:agent,'sales_agent','marketing','test',1,:cost,1,
                        'completed','gemini-test',false)
                """), {"id": task, "tenant": tenant, "agent": uuid.uuid4(), "cost": cost})
            # Identifier is a fixed prefix plus UUID hex; never provider/client input.
            await conn.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'local-test-password\' NOBYPASSRLS')
            await conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            await conn.exec_driver_sql(f'GRANT SELECT ON tenants,agent_task_results TO "{role}"')
            await conn.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON demo_delivery_jobs TO "{role}"')
            await conn.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION public.gemini_daily_platform_spend() TO "{role}"')
            expected = float((await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar_one())
        app_url = admin.url.set(username=role, password="local-test-password")
        app_engine = create_async_engine(app_url, poolclass=NullPool)
        factory = async_sessionmaker(app_engine, expire_on_commit=False)
        monkeypatch.setattr(database, "async_session_factory", factory)
        monkeypatch.setattr(database, "current_session_factory", lambda: factory)
        assert await _todays_gemini_spend_usd() == pytest.approx(expected)
        assert await _todays_gemini_spend_usd(str(tenant_b)) == pytest.approx(2.75)
        async with database.get_tenant_session(tenant_a) as session:
            assert (await session.execute(text("SELECT count(*) FROM agent_task_results WHERE tenant_id=:other"),
                                          {"other": tenant_b})).scalar_one() == 0
            membership = await session.execute(text(
                "SELECT pg_has_role(current_user,'agenticorg_spend_aggregate','MEMBER')"))
            assert membership.scalar_one() is False
        monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "1.0")
        with pytest.raises(DailyBudgetExceeded, match="daily spend cap reached"):
            await assert_under_gemini_cap(tenant_id=str(tenant_a))
        monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "10.0")
        monkeypatch.setenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", "1.0")
        with pytest.raises(DailyBudgetExceeded, match="platform daily spend cap reached"):
            await assert_under_gemini_cap(tenant_id=str(tenant_a))
        job = await demo_delivery.prepare({"name": "Synthetic owner", "email": "owner@example.org"}, None)
        async with database.get_tenant_session(tenant_b) as session:
            assert (await session.execute(text("SELECT count(*) FROM demo_delivery_jobs WHERE id=:id"),
                                          {"id": job})).scalar_one() == 0
        monkeypatch.setattr("api.v1.demo._send_email_notification", lambda _: False)
        monkeypatch.setattr("api.v1.demo._send_trial_confirmation", lambda _: True)
        agent = AsyncMock()
        monkeypatch.setattr("api.v1.sales._run_sales_agent_on_lead", agent)
        assert await demo_delivery.dispatch_one(tenant_a, job) == {
            "notification": "failed", "confirmation": "smtp_accepted", "agent": "skipped",
        }
        assert await demo_delivery.dispatch_one(tenant_a, job) == {}
        agent.assert_not_called()
        async with database.get_tenant_session(tenant_a) as session:
            row = (await session.execute(text("SELECT * FROM demo_delivery_jobs WHERE id=:id"),
                                         {"id": job})).mappings().one()
            assert "owner@example.org" not in row["payload_encrypted"]
            assert row["notification_status"] == "failed"
            assert row["confirmation_status"] == "smtp_accepted"
        from core.crypto import verify_all

        key_ref = verify_all.parse_encrypted_container(row["payload_encrypted"])
        assert key_ref is not None
        monkeypatch.setattr(verify_all, "_SCANNERS", [
            ("demo_delivery_jobs.payload_encrypted", "core.models.demo_delivery:DemoDeliveryJob:payload_encrypted"),
        ])
        monkeypatch.setattr(verify_all, "discover_tenant_company_scopes", AsyncMock(return_value=[
            verify_all.TenantCompanyScope(tenant_a), verify_all.TenantCompanyScope(tenant_b),
        ]))
        with pytest.raises(verify_all.KeyStillReferencedError):
            await verify_all.assert_key_unreferenced(key_ref.ref)
        with pytest.raises(ValueError, match="server-owned tenant"):
            await demo_delivery.dispatch_one(tenant_b, job)

        # A crashed sender is ambiguous, not a retryable success. Only still
        # pending actions may be recovered; other tenants remain invisible.
        monkeypatch.setenv("AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED", "true")
        async with database.get_tenant_session(tenant_a) as session:
            await session.execute(text("""
                UPDATE demo_delivery_jobs SET notification_status='processing',
                    confirmation_status='pending',agent_status='processing',
                    updated_at=now()-INTERVAL '16 minutes' WHERE id=:id
            """), {"id": job})
        enqueue = AsyncMock(return_value=True)
        monkeypatch.setattr(demo_delivery, "enqueue", enqueue)
        assert await demo_delivery.sweep() >= 1
        assert any(call.args == (job,) for call in enqueue.call_args_list)
        async with database.get_tenant_session(tenant_a) as session:
            row = (await session.execute(text("SELECT * FROM demo_delivery_jobs WHERE id=:id"),
                                         {"id": job})).mappings().one()
            assert row["notification_status"] == "unknown"
            assert row["agent_status"] == "unknown"
        assert await demo_delivery.dispatch_one(tenant_a, job) == {"confirmation": "smtp_accepted"}
        monkeypatch.setenv("AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED", "false")
        assert await demo_delivery.sweep() == 0
    finally:
        if app_engine:
            await app_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text("DELETE FROM agent_task_results WHERE id IN (:a,:b)"), {"a": task_a, "b": task_b})
            if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})).scalar():
                await conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
                await conn.exec_driver_sql(f'DROP ROLE "{role}"')
        await admin.dispose()


@pytest.mark.asyncio
async def test_non_superuser_install_replay_and_rollback(monkeypatch, tmp_path):
    """A superuser must not mask function ACL/ownership transfer mistakes."""
    url = os.getenv("AGENTICORG_DB_URL")
    if not url:
        pytest.skip("Requires the disposable Docker Postgres database")
    admin = create_async_engine(url, poolclass=NullPool)
    role = "lead_installer_" + uuid.uuid4().hex[:12]
    db = "lead_policy_" + uuid.uuid4().hex[:12]
    installer = None
    created = False
    migration = importlib.import_module("migrations.versions.v6_z85_lead_delivery_spend")
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
            # The cluster already holds the aggregate role from the main
            # migration. Seed only the creator's admin-only management grant.
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
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            rights = (await conn.execute(text("""
                SELECT pg_has_role(current_user,'agenticorg_spend_aggregate','USAGE') AS inherits,
                    pg_has_role(current_user,'agenticorg_spend_aggregate','SET') AS can_set,
                    public.gemini_daily_platform_spend() AS spend
            """))).mappings().one()
            assert rights["inherits"] is False
            assert rights["can_set"] is False
            assert rights["spend"] == 0
            assert (await conn.execute(text("SELECT relforcerowsecurity FROM pg_class "
                                           "WHERE oid='public.agent_task_results'::regclass"))).scalar() is True
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.downgrade))
            assert (await conn.execute(text("SELECT to_regclass('public.demo_delivery_jobs')"))).scalar() is None
        async with installer.begin() as conn:
            await conn.run_sync(lambda c: apply(c, migration.upgrade))
            assert (await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar() == 0
            tenant = uuid.uuid4()
            await conn.execute(text("INSERT INTO tenants VALUES (:id)"), {"id": tenant})
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id',:tenant,true)"),
                               {"tenant": str(tenant)})
            await conn.execute(text("INSERT INTO demo_delivery_jobs(id,tenant_id,payload_encrypted) "
                                    "VALUES (:id,:tenant,'synthetic-encrypted-history')"),
                               {"id": uuid.uuid4(), "tenant": tenant})
        async with installer.begin() as conn:
            # With no tenant context, FORCE-RLS hides the populated history.
            assert (await conn.execute(text("SELECT count(*) FROM demo_delivery_jobs"))).scalar() == 0
            with pytest.raises(RuntimeError, match="Archive encrypted delivery history"):
                async with conn.begin_nested():
                    await conn.run_sync(lambda c: apply(c, migration.downgrade))
            assert (await conn.execute(text("SELECT relforcerowsecurity FROM pg_class "
                                           "WHERE oid='public.demo_delivery_jobs'::regclass"))).scalar() is True
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id',:tenant,true)"),
                               {"tenant": str(tenant)})
            assert (await conn.execute(text("SELECT payload_encrypted FROM demo_delivery_jobs"))).scalar() == \
                "synthetic-encrypted-history"
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


@pytest.mark.asyncio
async def test_concurrent_enqueue_leases_pending_publications_and_recovers_expired_lease(monkeypatch):
    from unittest.mock import MagicMock

    from core import database, demo_delivery

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    publish = MagicMock()
    monkeypatch.setattr("core.tasks.demo_tasks.dispatch.apply_async", publish)
    try:
        job = await demo_delivery.prepare({"name": "Synthetic lease", "email": "owner@example.org"}, None)
        results = await asyncio.gather(*(demo_delivery.enqueue(job) for _ in range(8)))
        assert sum(results) == 1
        assert await demo_delivery.enqueue(job) is False
        assert publish.call_count == 1
        assert publish.call_args.kwargs["expires"] == 300
        async with database.get_tenant_session(demo_delivery.MARKETING_TENANT) as session:
            await session.execute(text("UPDATE demo_delivery_jobs SET publish_after=now()-INTERVAL '1 second' "
                                       "WHERE id=:id"), {"id": job})
        publish.side_effect = ConnectionError("synthetic broker failure")
        assert await demo_delivery.enqueue(job) is False
        assert await demo_delivery.enqueue(job) is False
        assert publish.call_count == 2
        async with database.get_tenant_session(demo_delivery.MARKETING_TENANT) as session:
            assert (await session.execute(text("SELECT notification_status FROM demo_delivery_jobs WHERE id=:id"),
                                          {"id": job})).scalar() == "pending"
    finally:
        await engine.dispose()
