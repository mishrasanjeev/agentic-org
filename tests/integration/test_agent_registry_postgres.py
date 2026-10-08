# SPDX-License-Identifier: Apache-2.0
"""Registry migration parity, RLS, and concurrent first-write route regression."""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from api.v1 import agent_registry as api
from core.agent_registry import lifecycle
from core.config import settings
from core.models.agent import Agent
from core.models.agent_registry import AgentRegistryEntry, AgentRegistryEvent
from core.models.agent_task_result import AgentTaskResult
from core.models.feedback import AgentFeedback
from core.ownership import Caller
from core.schemas.api import AgentCardIn

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
migration = importlib.import_module("migrations.versions.v6_z48_agent_registry")
source_state_migration = importlib.import_module("migrations.versions.v6_z49_registry_from_state")
ratings_migration = importlib.import_module("migrations.versions.v6_z49_agent_ratings")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Requires local PostgreSQL")


@pytest.fixture(params=["fresh", "bootstrap"])
def registry_db(request):
    suffix = uuid.uuid4().hex
    schema = f"registry_{suffix}"
    role = f"registry_probe_{suffix}"
    engine = create_engine(DB_URL.replace("postgresql+asyncpg", "postgresql"), poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            # Include the empty history tables read by the full card response.
            conn.execute(CreateTable(Agent.__table__, include_foreign_key_constraints=[]))
            for model in (AgentTaskResult, AgentFeedback):
                conn.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
            if request.param == "bootstrap":
                AgentRegistryEntry.__table__.create(conn)
                AgentRegistryEvent.__table__.create(conn)
                # Reproduce a pre-fix metadata bootstrap lacking all migration checks.
                for table, constraint in (
                    ("agent_registry", "ck_agent_registry_state"),
                    ("agent_registry", "ck_agent_registry_risk_tier"),
                    ("agent_registry_events", "ck_agent_registry_events_to"),
                    ("agent_registry_events", "ck_agent_registry_events_from"),
                ):
                    conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.upgrade()
                source_state_migration.upgrade()
                source_state_migration.upgrade()
                # The full card includes its tenant-scoped ratings summary.
                ratings_migration.upgrade()
            conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
            conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO {role}'))
        yield schema, role
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        engine.dispose()


@pytest.mark.asyncio
async def test_registry_first_card_write_is_serialized_and_tenant_isolated(registry_db, monkeypatch):
    schema, role = registry_db
    engine = create_async_engine(DB_URL, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid, other_tid, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    caller = Caller(uuid.uuid4(), "admin", None, True, False)
    monkeypatch.setattr(settings, "agent_registry_enabled", True)

    @asynccontextmanager
    async def session_for(tenant_id):
        async with factory.begin() as session:
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            await session.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
            )
            yield session

    original_get = lifecycle.get_entry

    async def slower_first_read(*args, **kwargs):
        entry = await original_get(*args, **kwargs)
        if entry is None:
            # Widen the first-insert race; the parent lock must serialize this window.
            await asyncio.sleep(0.1)
        return entry

    monkeypatch.setattr(api, "get_tenant_session", session_for)
    monkeypatch.setattr(lifecycle, "get_entry", slower_first_read)
    try:
        async with factory.begin() as session:
            session.add(
                Agent(
                    id=agent_id,
                    tenant_id=tid,
                    name="Synthetic registry",
                    agent_type="custom",
                    domain="ops",
                    system_prompt_ref="synthetic/v1",
                    hitl_condition="confidence < 0.8",
                )
            )

        async def write(fields):
            return await api.set_agent_card(
                agent_id, AgentCardIn(**fields), tenant_id=str(tid), user_domains=None, caller=caller
            )

        cards = await asyncio.wait_for(
            asyncio.gather(write({"purpose": "Synthetic purpose"}), write({"risk_tier": "high"})), timeout=10
        )
        assert all(card["registry"]["state"] == "draft" for card in cards)
        assert all(card["rating"] == {"count": 0, "average": None} for card in cards)
        assert all(card["reliability"]["runs"] == 0 for card in cards)
        async with session_for(tid) as session:
            entries = list((await session.execute(select(AgentRegistryEntry))).scalars())
            assert len(entries) == 1
            assert (entries[0].purpose, entries[0].risk_tier) == ("Synthetic purpose", "high")
        async with session_for(other_tid) as session:
            assert list((await session.execute(select(AgentRegistryEntry))).scalars()) == []
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session_for(tid) as session:
                session.add(
                    AgentRegistryEvent(
                        id=uuid.uuid4(), tenant_id=other_tid, agent_id=agent_id, from_state="draft", to_state="review"
                    )
                )
                await session.flush()
        for column in ("state", "risk_tier"):
            with pytest.raises(IntegrityError):
                async with session_for(tid) as session:
                    await session.execute(
                        update(AgentRegistryEntry)
                        .where(AgentRegistryEntry.agent_id == agent_id)
                        .values(**{column: "invalid"})
                    )
        for column in ("from_state", "to_state"):
            with pytest.raises(IntegrityError):
                async with session_for(tid) as session:
                    values = {"from_state": "draft", "to_state": "review", column: "invalid"}
                    session.add(AgentRegistryEvent(id=uuid.uuid4(), tenant_id=tid, agent_id=agent_id, **values))
                    await session.flush()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_catalogue_filters_and_visibility_precede_limit(registry_db, monkeypatch):
    schema, role = registry_db
    engine = create_async_engine(DB_URL, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid, other_tid = uuid.uuid4(), uuid.uuid4()
    caller = Caller(uuid.uuid4(), "developer", ["finance"], False, False)
    monkeypatch.setattr(settings, "agent_registry_enabled", True)
    monkeypatch.setattr(lifecycle, "MAX_LISTED", 1)

    @asynccontextmanager
    async def session_for(tenant_id):
        async with factory.begin() as session:
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            await session.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
            )
            yield session

    monkeypatch.setattr(api, "get_tenant_session", session_for)
    try:
        async with factory.begin() as session:
            for index, (tenant, name, domain, visibility) in enumerate(
                [
                    (tid, "Needle 10%_ approved", "finance", "tenant"),
                    (tid, "Unrelated", "ops", "tenant"),
                    (tid, "Needle 10%_ private", "finance", "personal"),
                    (other_tid, "Needle 10%_ other tenant", "finance", "tenant"),
                ]
            ):
                agent = Agent(
                    id=uuid.uuid4(),
                    tenant_id=tenant,
                    name=name,
                    agent_type="custom",
                    domain=domain,
                    visibility=visibility,
                    system_prompt_ref="synthetic/v1",
                    hitl_condition="confidence < 0.8",
                )
                session.add(agent)
                await session.flush()
                entry = lifecycle.new_entry(tenant, agent.id)
                entry.updated_at = datetime.now(UTC) + timedelta(seconds=index)
                session.add(entry)
        for filters in ({"domain": "finance"}, {"q": "NEEDLE 10%_"}, {}):
            result = await api.list_agent_registry(tenant_id=str(tid), user_domains=None, caller=caller, **filters)
            assert [row["name"] for row in result["entries"]] == ["Needle 10%_ approved"]
        result = await api.list_agent_registry(q="missing%_", tenant_id=str(tid), user_domains=None, caller=caller)
        assert result["entries"] == []
        async with factory.begin() as session:
            fresh_agent = Agent(
                id=uuid.uuid4(),
                tenant_id=tid,
                name="New unregistered agent",
                agent_type="custom",
                domain="finance",
                status="shadow",
                visibility="tenant",
                system_prompt_ref="synthetic/v1",
                hitl_condition="confidence < 0.8",
            )
            session.add(fresh_agent)
            fresh_id = fresh_agent.id
        result = await api.list_agent_registry(
            q="New unregistered",
            state="draft",
            tenant_id=str(tid),
            user_domains=None,
            caller=caller,
        )
        assert len(result["entries"]) == 1
        assert result["entries"][0]["agent_id"] == str(fresh_id)
        assert result["entries"][0]["state"] == "draft"
        assert result["entries"][0]["state_changed_at"] is None
        async with session_for(tid) as session:
            assert await lifecycle.get_entry(session, tid, fresh_id) is None  # GET never writes.
    finally:
        await engine.dispose()


@pytest.mark.parametrize("historical_state", ["review", "legacy_invalid"])
def test_forward_source_state_migration_preserves_history_and_rejects_new_invalid_rows(historical_state):
    schema = f"registry_upgrade_{uuid.uuid4().hex}"
    engine = create_engine(DB_URL.replace("postgresql+asyncpg", "postgresql"), poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            with Operations.context(MigrationContext.configure(conn)):
                source_state_migration.upgrade()  # An absent legacy table is safe.
            conn.execute(text("CREATE TABLE agent_registry_events (from_state VARCHAR(16) NOT NULL)"))
            conn.execute(text("INSERT INTO agent_registry_events VALUES (:state)"), {"state": historical_state})
            with Operations.context(MigrationContext.configure(conn)):
                source_state_migration.upgrade()
                source_state_migration.upgrade()
            assert conn.execute(text("SELECT from_state FROM agent_registry_events")).scalar_one() == historical_state
            validated = conn.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conname = 'ck_agent_registry_events_from' "
                    "AND conrelid = 'agent_registry_events'::regclass"
                )
            ).scalar_one()
            assert validated is (historical_state == "review")
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(text("INSERT INTO agent_registry_events VALUES ('invalid')"))
            conn.execute(text("INSERT INTO agent_registry_events VALUES ('draft')"))
            with Operations.context(MigrationContext.configure(conn)):
                source_state_migration.downgrade()
                source_state_migration.downgrade()
                source_state_migration.upgrade()
            assert conn.execute(text("SELECT count(*) FROM agent_registry_events")).scalar_one() == 2
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
