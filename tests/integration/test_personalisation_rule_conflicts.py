# SPDX-License-Identifier: Apache-2.0
"""Concurrent rule names are tenant-scoped conflicts, not server errors."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from core.models.personalisation import PersonalisationConsent, PersonalisationEvent, PersonalisationRule
from core.personalisation.rules import PersonalisationError
from core.personalisation.service import create_rule, delete_rule

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Requires local PostgreSQL")


@pytest.mark.asyncio
async def test_concurrent_rule_creation_returns_one_conflict_and_allows_another_tenant(monkeypatch):
    import core.database

    engine = create_async_engine(DB_URL, poolclass=NullPool)
    schema = "personalisation_security_" + uuid.uuid4().hex
    metadata = MetaData()
    PersonalisationConsent.__table__.to_metadata(metadata, schema=schema)
    PersonalisationRule.__table__.to_metadata(metadata, schema=schema)
    events = PersonalisationEvent.__table__.to_metadata(metadata, schema=schema)
    tenants = [uuid.uuid4(), uuid.uuid4()]

    @asynccontextmanager
    async def session_for(tenant):
        assert tenant in tenants
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": schema})
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session

    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(metadata.create_all)
        monkeypatch.setattr(core.database, "get_tenant_session", session_for)
        raw = {"name": "welcome", "purpose": "service", "variant": {"template": "Welcome"}, "allowed_attributes": []}
        results = await asyncio.gather(
            *(create_rule(tenants[0], raw, actor="synthetic-operator") for _ in range(2)), return_exceptions=True
        )
        assert sum(isinstance(result, dict) for result in results) == 1
        conflicts = [result for result in results if isinstance(result, PersonalisationError)]
        assert len(conflicts) == 1 and conflicts[0].status == 409 and conflicts[0].code == "rule_exists"
        other = await create_rule(tenants[1], raw, actor="synthetic-operator")
        assert other["name"] == "welcome"
        made = next(result for result in results if isinstance(result, dict))
        event_id = uuid.uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                insert(events).values(
                    id=event_id,
                    tenant_id=tenants[0],
                    subject_ref="synthetic-subject",
                    purpose="service",
                    rule_id=uuid.UUID(made["id"]),
                    outcome="rendered",
                    content_hash="synthetic-content-hash",
                )
            )
        await delete_rule(tenants[0], uuid.UUID(made["id"]), actor="synthetic-operator")
        async with engine.connect() as connection:
            event = (await connection.execute(select(events).where(events.c.id == event_id))).mappings().one()
            assert event["rule_id"] is None and event["content_hash"] == "synthetic-content-hash"
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await engine.dispose()
