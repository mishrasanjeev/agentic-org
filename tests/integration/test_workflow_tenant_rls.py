# SPDX-License-Identifier: Apache-2.0
"""Replay workflow persistence with production-style FORCE RLS, not a superuser."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.database import session_factory_scope
from core.models.tenant import Tenant
from core.models.workflow import WorkflowEventWait, WorkflowRunState
from workflows.event_waits import EventWaitRecord, SqlAlchemyWorkflowEventWaitRepository
from workflows.state_store import SqlAlchemyWorkflowStateRepository, StaleStateError, WorkflowStateStore


@asynccontextmanager
async def _restricted_database(db_engine):
    from core.models.base import BaseModel

    suffix = uuid.uuid4().hex
    schema = f"workflow_rls_{suffix}"
    role = f"workflow_role_{suffix}"
    admin = create_async_engine(
        db_engine.url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    restricted = None
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
            await connection.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{role}"'))
            await connection.run_sync(BaseModel.metadata.create_all)
            for table in ("workflow_run_states", "workflow_event_waits"):
                await connection.execute(text(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY'))
                await connection.execute(text(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY'))
                await connection.execute(
                    text(
                        f'CREATE POLICY tenant_isolation ON "{schema}"."{table}" '
                        "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true)) "
                        "WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))"
                    )
                )
            await connection.execute(text(f'GRANT ALL ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"'))
            await connection.execute(text(f'GRANT ALL ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"'))
        factory = async_sessionmaker(admin, expire_on_commit=False)
        async with factory.begin() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Example A", slug=f"example-a-{suffix}"),
                    Tenant(id=tenant_b, name="Example B", slug=f"example-b-{suffix}"),
                ]
            )
        restricted = create_async_engine(
            db_engine.url,
            pool_size=1,
            max_overflow=0,
            connect_args={"server_settings": {"search_path": schema, "role": role}},
        )
        runtime_factory = async_sessionmaker(restricted, class_=AsyncSession, expire_on_commit=False)
        async with runtime_factory() as session:
            assert not await session.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
        # Policy/feature-flag reads inside the real engine use the same isolated
        # database and loop as persistence, not the application's shared pool.
        with session_factory_scope(lambda: runtime_factory):
            yield runtime_factory, tenant_a, tenant_b
    finally:
        if restricted is not None:
            await restricted.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_workflow_state_persists_and_reloads_with_force_rls(db_engine):
    async with _restricted_database(db_engine) as (factory, tenant_a, tenant_b):
        state = {"id": "wfr_example_a", "tenant_id": str(tenant_a), "status": "running", "step_results": {}}
        repository = SqlAlchemyWorkflowStateRepository(factory)
        await repository.save(state, state_hash="initial", tenant_id=tenant_a, actor="test")
        # A fresh worker must find authoritative PostgreSQL state without Redis.
        loaded = await SqlAlchemyWorkflowStateRepository(factory).load(state["id"])
        assert loaded is not None and loaded["tenant_id"] == str(tenant_a)
        loaded["status"] = "completed"
        await repository.save(loaded, state_hash="terminal", tenant_id=tenant_a, actor="test", expected_version=1)
        assert (await repository.load(state["id"]))["status"] == "completed"
        async with factory.begin() as session:
            await session.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tenant, true)"), {"tenant": str(tenant_b)}
            )
            assert await session.scalar(select(WorkflowRunState).where(WorkflowRunState.run_id == state["id"])) is None
        # Context must not leak into another pooled transaction.
        async with factory() as session:
            assert await session.scalar(select(WorkflowRunState).where(WorkflowRunState.run_id == state["id"])) is None


@pytest.mark.asyncio
async def test_event_wait_register_match_and_timeout_with_force_rls(db_engine):
    async with _restricted_database(db_engine) as (factory, tenant_a, tenant_b):
        repository = SqlAlchemyWorkflowEventWaitRepository(factory)
        record = EventWaitRecord(
            engine_run_id="wfr_example_a", step_id="wait", event_type="example.received", tenant_id=tenant_a
        )
        await repository.register(record)
        assert len(await repository.list_waiting(tenant_id=tenant_a)) == 1
        assert await repository.list_waiting(tenant_id=tenant_b) == []
        assert await repository.list_waiting() == []
        matched = await SqlAlchemyWorkflowEventWaitRepository(factory).claim_matched(
            engine_run_id=record.engine_run_id,
            step_id=record.step_id,
            matched_event_id="example-1",
            event_data={"status": "received"},
        )
        assert matched is not None and matched.status == "matched"
        record.step_id = "timeout"
        await repository.register(record)
        timed_out = await SqlAlchemyWorkflowEventWaitRepository(factory).mark_timed_out(
            engine_run_id=record.engine_run_id, step_id=record.step_id
        )
        assert timed_out is not None and timed_out.status == "timed_out"
        async with factory.begin() as session:
            await session.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tenant, true)"), {"tenant": str(tenant_b)}
            )
            assert (
                await session.scalar(
                    select(WorkflowEventWait).where(WorkflowEventWait.engine_run_id == record.engine_run_id)
                )
                is None
            )


@pytest.mark.asyncio
async def test_engine_runs_real_transform_and_reloads_without_redis_under_rls(db_engine):
    from workflows.engine import WorkflowEngine

    async with _restricted_database(db_engine) as (factory, tenant_a, tenant_b):
        store = WorkflowStateStore(repository=SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_a))
        engine = WorkflowEngine(store)
        run_id = await engine.start_run(
            {
                "name": "Read-only acceptance",
                "steps": [{"id": "receive_text", "type": "transform", "operation": "identity"}],
            },
            {"text": "Synthetic channel acceptance"},
            tenant_id=str(tenant_a),
        )
        result = await engine.execute(run_id)
        assert result["status"] == "completed"
        reloaded = await SqlAlchemyWorkflowStateRepository(factory).load(run_id)
        assert reloaded is not None
        assert reloaded["status"] == "completed"
        assert reloaded["steps_completed"] == 1
        assert reloaded["step_results"]["receive_text"]["output"]["text"] == "Synthetic channel acceptance"
        assert await SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_b).load(run_id) is None


@pytest.mark.asyncio
async def test_bound_repositories_reject_foreign_scope_and_preserve_version_and_replay(db_engine):
    async with _restricted_database(db_engine) as (factory, tenant_a, tenant_b):
        repository = SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_a)
        state = {"id": "wfr_example_a", "tenant_id": str(tenant_a), "status": "running"}
        assert (
            await repository.save(
                state, state_hash="initial", tenant_id=tenant_a, actor="test", idempotency_key="start"
            )
            == 1
        )
        assert (
            await repository.save(
                state, state_hash="initial", tenant_id=tenant_a, actor="test", idempotency_key="start"
            )
            is None
        )
        with pytest.raises(StaleStateError):
            await repository.save(state, state_hash="stale", tenant_id=tenant_a, actor="test", expected_version=0)
        with pytest.raises(ValueError, match="repository scope"):
            await repository.save(state, state_hash="foreign", tenant_id=tenant_b, actor="test")
        assert await SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_b).load(state["id"]) is None
        event_repository = SqlAlchemyWorkflowEventWaitRepository(factory, tenant_id=tenant_a)
        record = EventWaitRecord(
            engine_run_id=state["id"], step_id="wait", event_type="example.received", tenant_id=tenant_a
        )
        await event_repository.register(record)
        assert await event_repository.list_waiting(tenant_id=tenant_b) == []
        foreign_repository = SqlAlchemyWorkflowEventWaitRepository(factory, tenant_id=tenant_b)
        assert (
            await foreign_repository.claim_matched(
                engine_run_id=state["id"], step_id="wait", matched_event_id="foreign", event_data={}
            )
            is None
        )
        assert await foreign_repository.mark_timed_out(engine_run_id=state["id"], step_id="wait") is None
        with pytest.raises(ValueError, match="repository scope"):
            await foreign_repository.register(record)
        assert (await event_repository.list_waiting(tenant_id=tenant_a))[0].status == "waiting"


@pytest.mark.asyncio
async def test_redis_cannot_select_tenant_or_replace_requested_run(db_engine):
    import json

    async with _restricted_database(db_engine) as (factory, tenant_a, tenant_b):
        redis = AsyncMock()
        cached = {"id": "wfr_cache", "tenant_id": str(tenant_a), "status": "running"}
        redis.get.return_value = json.dumps(cached)
        unscoped = WorkflowStateStore(repository=SqlAlchemyWorkflowStateRepository(factory), redis=redis)
        assert await unscoped.load(cached["id"]) is None
        assert await SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_a).load(cached["id"]) is None
        foreign = WorkflowStateStore(
            repository=SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_b),
            redis=redis,
        )
        assert await foreign.load(cached["id"]) is None
        scoped = WorkflowStateStore(
            repository=SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_a),
            redis=redis,
        )
        assert await scoped.load("wfr_different") is None
        assert await scoped.load(cached["id"]) is not None
        assert await SqlAlchemyWorkflowStateRepository(factory, tenant_id=tenant_a).load(cached["id"]) is not None
