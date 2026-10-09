# SPDX-License-Identifier: Apache-2.0
"""Additive speech migrations preserve ciphertext on populated/idempotent upgrades."""

from __future__ import annotations

import importlib
import json
import os
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Requires local PostgreSQL")


@pytest.mark.asyncio
async def test_schema_only_speech_upgrades_preserve_existing_ciphertext() -> None:
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    schema = "speech_migration_" + uuid.uuid4().hex
    revisions = [
        importlib.import_module("migrations.versions." + name)
        for name in ("v6_z69_speech_recordings", "v6_z70_speech_summaries", "v6_z71_speech_live_sessions")
    ]

    def upgrade(connection, selected=None):
        with Operations.context(MigrationContext.configure(connection)):
            for revision in revisions if selected is None else selected:
                revision.upgrade()

    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
            await connection.run_sync(upgrade, revisions[:1])
            initial_id, initial_tenant = uuid.uuid4(), uuid.uuid4()
            initial_envelope = json.dumps({"_encrypted": "agko_vfixture$" + initial_tenant.hex})
            await connection.execute(
                text(
                    "INSERT INTO speech_recordings (id, tenant_id, content, transcript_encrypted) "
                    "VALUES (:id, :tenant, :content, CAST(:envelope AS jsonb))"
                ),
                {
                    "id": initial_id,
                    "tenant": initial_tenant,
                    "content": b"synthetic audio",
                    "envelope": initial_envelope,
                },
            )
            await connection.run_sync(upgrade, revisions[1:])
            initial = (
                await connection.execute(
                    text("SELECT transcript_encrypted, summary_encrypted FROM speech_recordings WHERE id = :id"),
                    {"id": initial_id},
                )
            ).one()
            assert initial.transcript_encrypted == json.loads(initial_envelope)
            assert initial.summary_encrypted == {}
            for tenant in (uuid.uuid4(), uuid.uuid4()):
                envelope = json.dumps({"_encrypted": "agko_vfixture$" + tenant.hex})
                await connection.execute(
                    text(
                        "INSERT INTO speech_recordings "
                        "(id, tenant_id, content, transcript_encrypted, summary_encrypted) "
                        "VALUES (:id, :tenant, :content, CAST(:envelope AS jsonb), CAST(:envelope AS jsonb))"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant, "content": b"synthetic audio", "envelope": envelope},
                )
                await connection.execute(
                    text(
                        "INSERT INTO speech_live_sessions (id, tenant_id, turns_encrypted) "
                        "VALUES (:id, :tenant, CAST(:envelope AS jsonb))"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant, "envelope": envelope},
                )
            before = (
                await connection.execute(
                    text(
                        "SELECT id, tenant_id, content, transcript_encrypted, summary_encrypted "
                        "FROM speech_recordings ORDER BY id"
                    )
                )
            ).all()
            sessions_before = (
                await connection.execute(
                    text("SELECT id, tenant_id, turns_encrypted FROM speech_live_sessions ORDER BY id")
                )
            ).all()
            await connection.run_sync(upgrade)
            assert (
                await connection.execute(
                    text(
                        "SELECT id, tenant_id, content, transcript_encrypted, summary_encrypted "
                        "FROM speech_recordings ORDER BY id"
                    )
                )
            ).all() == before
            assert (
                await connection.execute(
                    text("SELECT id, tenant_id, turns_encrypted FROM speech_live_sessions ORDER BY id")
                )
            ).all() == sessions_before
            protections = (
                await connection.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relnamespace = CAST(:schema AS regnamespace) "
                        "AND relname IN ('speech_recordings', 'speech_live_sessions')"
                    ),
                    {"schema": schema},
                )
            ).all()
            assert len(protections) == 2
            assert all(enabled and forced for _, enabled, forced in protections)
            await connection.execute(DropSchema(schema, cascade=True))
    finally:
        await engine.dispose()
