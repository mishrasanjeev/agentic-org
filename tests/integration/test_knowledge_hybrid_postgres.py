# SPDX-License-Identifier: Apache-2.0
"""Native hybrid retrieval against real PostgreSQL full-text and pgvector operators."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api.v1 import knowledge

_DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not _DB_URL, reason="integration tests require AGENTICORG_DB_URL")


@pytest.mark.asyncio
async def test_hybrid_rank_tenant_status_missing_vector_and_literal_query(monkeypatch) -> None:
    import core.database
    import core.embeddings

    engine = create_async_engine(_DB_URL, poolclass=StaticPool)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    both, lexical_only, vector_only, other_tenant, deleted = [uuid.uuid4() for _ in range(5)]
    rows = [
        {
            "id": both, "tenant": tenant_a, "title": "Alpha shared", "content": "alpha item",
            "status": "ready", "vec": "[1,0,0]",
        },
        {
            "id": lexical_only, "tenant": tenant_a, "title": "Lexical only", "content": "alpha item",
            "status": "ready", "vec": None,
        },
        {
            "id": vector_only, "tenant": tenant_a, "title": "Vector only", "content": "unrelated",
            "status": "ready", "vec": "[0.9,0.1,0]",
        },
        {
            "id": other_tenant, "tenant": tenant_b, "title": "Other tenant", "content": "alpha item",
            "status": "ready", "vec": "[1,0,0]",
        },
        {
            "id": deleted, "tenant": tenant_a, "title": "Deleted", "content": "alpha item",
            "status": "deleted", "vec": "[1,0,0]",
        },
    ]

    async def embed(_query):
        return [1.0, 0.0, 0.0]

    async def fail_embed(_query):
        raise TimeoutError("embedding unavailable")

    monkeypatch.setattr(core.embeddings, "embed_one_async", embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(
                "CREATE TEMP TABLE knowledge_documents ("
                "id uuid PRIMARY KEY, tenant_id uuid NOT NULL, title text NOT NULL, "
                "content text NOT NULL, status text NOT NULL, embedding vector(3)) "
                "ON COMMIT PRESERVE ROWS"
            ))
            await conn.execute(text(
                "INSERT INTO knowledge_documents (id, tenant_id, title, content, status, embedding) "
                "VALUES (:id, :tenant, :title, :content, :status, CAST(:vec AS vector))"
            ), rows)
            await conn.execute(text("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text("ALTER TABLE knowledge_documents FORCE ROW LEVEL SECURITY"))
            await conn.execute(text(
                "CREATE POLICY tenant_scope ON knowledge_documents "
                "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))"
            ))
            await conn.commit()

            monkeypatch.setattr(
                core.database,
                "async_session_factory",
                async_sessionmaker(bind=conn, class_=AsyncSession, expire_on_commit=False),
            )
            results = await knowledge._native_hybrid_search(tenant_a, "alpha", 3)
            assert [r.document_name for r in results][0] == "Alpha shared"
            assert {r.document_name for r in results} == {"Alpha shared", "Lexical only", "Vector only"}
            assert results[0].score > results[1].score

            monkeypatch.setattr(core.embeddings, "embed_one_async", fail_embed)
            lexical_results = await knowledge._native_hybrid_search(tenant_a, "alpha", 3)
            assert {r.document_name for r in lexical_results} == {"Alpha shared", "Lexical only"}
            keyword_results = await knowledge._native_hybrid_search(tenant_a, "pha", 3)
            assert {r.document_name for r in keyword_results} == {"Alpha shared", "Lexical only"}
            assert await knowledge._native_hybrid_search(tenant_a, "%", 3) == []
    finally:
        await engine.dispose()
