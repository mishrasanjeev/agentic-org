# SPDX-License-Identifier: Apache-2.0
"""Upload recovery and replacement against real tenant-scoped PostgreSQL transactions."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.models.document import Document
from core.rag import ingest, upload_recovery
from core.rag.extractors import ExtractedContent, ExtractedSpan

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="requires a local PostgreSQL test database")


@pytest.mark.asyncio
async def test_timeout_recovers_atomically_without_losing_original_or_crossing_tenants(monkeypatch):
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant, foreign = uuid.uuid4(), uuid.uuid4()
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
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
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tid)})
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

        listing = await knowledge.list_documents(page=1, per_page=100, tenant_id=str(tenant), user_domains=None)
        assert [item.document_id for item in listing.items] == [str(new_id)]
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
