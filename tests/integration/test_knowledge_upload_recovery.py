# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Upload recovery and replacement against real tenant-scoped PostgreSQL transactions."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.models.document import Document
from core.rag import ingest, upload_recovery
from core.rag.extractors import ExtractedContent, ExtractedSpan

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="requires a local PostgreSQL test database")


def _recovery_base(database_url: str) -> URL:
    from scripts.reset_test_database import safe_test_database_name

    base = make_url(database_url)
    checked_url = database_url
    # CI names its loopback parent database "test"; only the generated _test
    # database below is migrated or dropped, never the configured parent.
    if os.getenv("CI") == "true" and base.host in {"localhost", "127.0.0.1"} and base.database == "test":
        checked_url = base.set(database="agenticorg_test").render_as_string(hide_password=False)
    safe_test_database_name(checked_url)
    return base


@pytest.mark.parametrize(
    ("ci", "url", "allowed"),
    [
        ("true", "postgresql+asyncpg://test:test@localhost:5432/test", True),
        ("true", "postgresql+asyncpg://test:test@127.0.0.1:5432/test", True),
        ("false", "postgresql+asyncpg://test:test@localhost:5432/test", False),
        ("true", "postgresql+asyncpg://test:test@database.example:5432/test", False),
        ("true", "postgresql+asyncpg://test:test@localhost:5432/production", False),
        ("true", "postgresql+asyncpg://test:test@localhost:5432/test?dbname=production", False),
        ("false", "postgresql+asyncpg://test:test@postgres:5432/agenticorg_test", True),
        ("true", "sqlite:///test", False),
    ],
)
def test_recovery_database_parent_guard(monkeypatch, ci, url, allowed):
    from scripts.reset_test_database import UnsafeDatabaseURLError

    monkeypatch.setenv("CI", ci)
    if allowed:
        assert _recovery_base(url).database == make_url(url).database
    else:
        with pytest.raises(UnsafeDatabaseURLError):
            _recovery_base(url)


@pytest.fixture(scope="module")
def recovery_database():
    base = _recovery_base(DB_URL)
    suffix = uuid.uuid4().hex
    name, role = f"knowledge_recovery_{suffix}_test", f"knowledge_recovery_{suffix}"
    admin = create_engine(base.set(drivername="postgresql", database="postgres"), isolation_level="AUTOCOMMIT")
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {name}"))  # noqa: S608 - generated test identifier
            connection.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        migrated = subprocess.run(
            [sys.executable, "scripts/alembic_migrate.py"],
            env={**os.environ, "AGENTICORG_DB_URL": url},
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr[-4000:]
        scoped_admin = create_engine(base.set(drivername="postgresql", database=name))
        try:
            with scoped_admin.begin() as connection:
                connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                connection.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                        f"documents, knowledge_documents, knowledge_chunk_sources TO {role}"
                    )
                )
                connection.execute(text(f"GRANT SELECT ON tenant_ai_settings TO {role}"))
        finally:
            scoped_admin.dispose()
        yield url, role
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))  # noqa: S608
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.asyncio
async def test_timeout_recovers_atomically_without_losing_original_or_crossing_tenants(monkeypatch, recovery_database):
    database_url, role = recovery_database
    engine = create_async_engine(database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant, foreign = uuid.uuid4(), uuid.uuid4()
    old_id, new_id, failed_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    content = ExtractedContent(
        spans=[ExtractedSpan(text="Synthetic new policy code 40721.", page=2)],
        mime_type="text/plain",
        extraction_method="plaintext",
        total_chars=32,
    )
    now = datetime.now(UTC) + timedelta(seconds=1)

    @asynccontextmanager
    async def scoped(tid):
        async with sessions() as session, session.begin():
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            await session.execute(text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": str(tid)})
            yield session

    monkeypatch.setattr(upload_recovery, "get_tenant_session", scoped)
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", scoped)
    monkeypatch.setattr(upload_recovery.settings, "knowledge_upload_recovery_enabled", True)
    monkeypatch.setattr(ingest, "_resolve_embedding_profile", _profile)
    monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", "false")
    calls = 0

    async def embeddings(texts, model=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("synthetic cold start")
        return [[0.1] * 384 for _ in texts]

    monkeypatch.setattr(ingest, "_embed_chunks", embeddings)
    try:
        async with sessions() as session, session.begin():
            for tid in (tenant, foreign):
                await session.execute(
                    text(
                        "INSERT INTO tenants(id,name,slug,plan,data_region,settings) "
                        "VALUES(:id,'Synthetic recovery tenant',:slug,'enterprise','IN','{}')"
                    ),
                    {"id": tid, "slug": f"recovery-{tid.hex}"},
                )
            session.add(
                Document(id=old_id, tenant_id=tenant, name="policy.txt", filename="policy.txt", status="indexed")
            )
            session.add(
                Document(
                    id=failed_id, tenant_id=tenant, name="policy.txt", filename="policy.txt", status="failed",
                    metadata_={"domain": "finance"},
                )
            )
            session.add(
                Document(
                    id=new_id,
                    tenant_id=tenant,
                    name="policy.txt",
                    filename="policy.txt",
                    status="processing",
                    metadata_={
                        "domain": "finance",
                        upload_recovery.KEY: upload_recovery.prepare(content, replaces=str(old_id)),
                    },
                )
            )
        async with scoped(foreign) as session:
            assert await session.get(Document, new_id) is None
        assert await upload_recovery.process(foreign, new_id, now=now) == "skipped"
        assert calls == 0
        assert await upload_recovery.process(tenant, new_id, now=now) == "processing"
        async with scoped(tenant) as session:
            assert (await session.get(Document, old_id)).status == "indexed"
            assert (await session.get(Document, new_id)).metadata_[upload_recovery.KEY]["attempts"] == 1
        assert await upload_recovery.process(tenant, new_id, now=now) == "skipped"
        assert calls == 1
        assert await upload_recovery.process(tenant, new_id, now=now + timedelta(seconds=31)) == "indexed"
        async with scoped(tenant) as session:
            assert (await session.get(Document, old_id)).status == "deleted"
            replacement = await session.get(Document, new_id)
            assert replacement.status == "indexed"
            assert upload_recovery.KEY not in replacement.metadata_
            chunks = (
                await session.execute(
                    text(
                        "SELECT content,embedding_dimensions FROM knowledge_documents "
                        "WHERE tenant_id=:tid AND source_object_id=:id AND status='ready'"
                    ),
                    {"tid": str(tenant), "id": str(new_id)},
                )
            ).all()
            assert len(chunks) == 1 and "40721" in chunks[0][0] and chunks[0][1] == 384
        from api.v1 import knowledge

        assert not await knowledge._db_may_mutate_document(str(tenant), str(new_id), ["hr"])
        assert await knowledge._db_may_mutate_document(str(tenant), str(new_id), ["finance"])
        assert not await knowledge._db_may_mutate_document(str(foreign), str(new_id), ["finance"])
        current = await knowledge._db_find_existing_by_filename(str(tenant), "policy.txt")
        assert current["document_id"] == str(new_id)
        listing = await knowledge.list_documents(page=1, per_page=100, tenant_id=str(tenant), user_domains=None)
        assert {item.document_id for item in listing.items} == {str(new_id), str(failed_id)}
        restricted = await knowledge.list_documents(page=1, per_page=100, tenant_id=str(tenant), user_domains=["hr"])
        assert restricted.total == 0
        assert await upload_recovery.process(tenant, new_id, now=now + timedelta(minutes=20)) == "skipped"
        assert calls == 2
    finally:
        async with sessions() as session, session.begin():
            for table in ("knowledge_chunk_sources", "knowledge_documents", "documents", "tenants"):
                column = "id" if table == "tenants" else "tenant_id"
                await session.execute(
                    text(f"DELETE FROM {table} WHERE {column} IN (:a,:b)"),  # noqa: S608 - fixed test-only identifiers
                    {"a": tenant, "b": foreign},
                )
        await engine.dispose()


async def _profile(_tenant):
    return ("local", "BAAI/bge-small-en-v1.5", 384)


@pytest.mark.asyncio
async def test_exhaustion_and_deleted_upload_never_execute_again(monkeypatch):
    from types import SimpleNamespace

    from core.rag.ingest import IngestResult

    row = SimpleNamespace(
        id=uuid.uuid4(), filename="synthetic.txt", name="synthetic.txt", content_type="text/plain", status="processing"
    )
    content = ExtractedContent(
        spans=[ExtractedSpan(text="Synthetic text")],
        mime_type="text/plain",
        extraction_method="plaintext",
        total_chars=14,
    )
    row.metadata_ = {
        upload_recovery.KEY: {**upload_recovery.prepare(content), "attempts": len(upload_recovery.RETRY_DELAYS)}
    }
    calls = 0

    async def failed(**kwargs):
        nonlocal calls
        calls += 1
        return IngestResult("", 0, 1, 0, "text/plain", "plaintext", "model", 384, errors=["timeout"], retryable=True)

    class Session:
        async def scalar(self, statement):
            return row if row.status == "processing" else None

    @asynccontextmanager
    async def scoped(_tenant):
        yield Session()

    monkeypatch.setattr(upload_recovery, "get_tenant_session", scoped)
    monkeypatch.setattr(upload_recovery, "ingest_document", failed)
    monkeypatch.setattr(upload_recovery.settings, "knowledge_upload_recovery_enabled", True)
    assert await upload_recovery.process(uuid.uuid4(), row.id, now=datetime.now(UTC) + timedelta(seconds=1)) == "failed"
    assert row.metadata_["ingestion_error"] == "index_retry_exhausted"
    assert upload_recovery.KEY not in row.metadata_
    row.status = "deleted"
    assert await upload_recovery.process(uuid.uuid4(), row.id) == "skipped"
    assert calls == 1
