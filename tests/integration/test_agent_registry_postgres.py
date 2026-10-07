# SPDX-License-Identifier: Apache-2.0
"""Registry migration parity, RLS, and concurrent first-write route regression."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

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
from core.ownership import Caller
from core.schemas.api import AgentCardIn
from migrations.versions import v6_z48_agent_registry as migration

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
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
            # Only the agent and registry tables are relevant to this isolated schema.
            conn.execute(CreateTable(Agent.__table__, include_foreign_key_constraints=[]))
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
