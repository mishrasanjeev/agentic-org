# SPDX-License-Identifier: Apache-2.0
"""Native knowledge retrieval keeps tenant scope while combining lexical and vector rank."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import SQLAlchemyError

from api.v1 import knowledge


def test_rank_fusion_deduplicates_and_breaks_ties_by_id() -> None:
    vector = [("b", "Vector B", "b"), ("a", "Shared A", "a")]
    lexical = [("a", "Shared A", "a"), ("c", "Lexical C", "c")]

    results = knowledge._fuse_native_hits(vector, lexical, 3)

    assert [r.document_name for r in results] == ["Shared A", "Vector B", "Lexical C"]
    assert results[0].score > results[1].score > results[2].score
    assert knowledge._fuse_native_hits([("b", "B", "")], [("a", "A", "")], 2)[0].document_name == "A"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, *, lexical=(), keyword=(), vector=(), errors=()):
        self.lexical = lexical
        self.keyword = keyword
        self.vector = vector
        self.errors = errors
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt, params):
        sql = str(stmt)
        self.calls.append((sql, params))
        channel = (
            "vector" if "CAST(:vector AS vector)" in sql
            else "lexical" if "websearch_to_tsquery" in sql
            else "keyword"
        )
        if channel in self.errors:
            raise SQLAlchemyError(f"{channel} unavailable")
        return _Rows(getattr(self, channel))


@pytest.fixture
def scoped_session(monkeypatch):
    import core.database

    tenant = uuid.uuid4()
    sessions = []

    def factory(tid):
        assert tid == tenant
        return sessions[-1]

    monkeypatch.setattr(core.database, "get_tenant_session", factory)
    return tenant, sessions


@pytest.mark.asyncio
async def test_hybrid_queries_bind_tenant_and_user_text(scoped_session, monkeypatch) -> None:
    import core.embeddings

    tenant, sessions = scoped_session
    query = "%' OR 1=1 --"
    session = _Session()
    sessions.append(session)

    async def embed(_query):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(core.embeddings, "embed_one_async", embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")

    assert await knowledge._native_hybrid_search(tenant, query, 5) == []
    assert len(session.calls) == 3
    for sql, params in session.calls:
        assert "tenant_id = :tid" in sql
        assert "status = 'ready'" in sql
        assert query not in sql
        assert params["tid"] == str(tenant)
    assert session.calls[0][1]["query"] == query
    assert session.calls[1][1]["query"] == query
    assert "strpos(lower(title), lower(:query))" in session.calls[1][0]
    assert session.calls[2][1]["vector"] == "[0.100000,0.200000,0.300000]"


@pytest.mark.asyncio
async def test_missing_vectors_and_embedding_error_keep_lexical_hits(scoped_session, monkeypatch) -> None:
    import core.embeddings

    tenant, sessions = scoped_session
    hit = (uuid.uuid4(), "Lexical", "alpha text")
    session = _Session(lexical=[hit], vector=[])
    sessions.append(session)

    async def embed(_query):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(core.embeddings, "embed_one_async", embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    assert (await knowledge._native_hybrid_search(tenant, "alpha", 2))[0].score == 0.5

    async def fail_embed(_query):
        raise TimeoutError("embedding unavailable")

    monkeypatch.setattr(core.embeddings, "embed_one_async", fail_embed)
    results = await knowledge._native_hybrid_search(tenant, "alpha", 2)
    assert [r.document_name for r in results] == ["Lexical"]
    assert len(session.calls) == 3  # two full-text reads; no second vector query


@pytest.mark.asyncio
async def test_pgvector_outage_keeps_keyword_hits(scoped_session, monkeypatch) -> None:
    import core.embeddings

    tenant, sessions = scoped_session
    session = _Session(keyword=[(uuid.uuid4(), "Keyword", "alpha")], errors={"vector"})
    sessions.append(session)

    async def embed(_query):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(core.embeddings, "embed_one_async", embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    results = await knowledge._native_hybrid_search(tenant, "alpha", 2)
    assert [r.document_name for r in results] == ["Keyword"]
    assert results[0].score == 0.5


@pytest.mark.asyncio
async def test_full_text_error_uses_literal_keyword_query(scoped_session, monkeypatch) -> None:
    import core.embeddings

    tenant, sessions = scoped_session
    session = _Session(keyword=[(uuid.uuid4(), "Keyword", "alpha")], errors={"lexical"})
    sessions.append(session)

    async def fail_embed(_query):
        raise TimeoutError("embedding unavailable")

    monkeypatch.setattr(core.embeddings, "embed_one_async", fail_embed)
    results = await knowledge._native_hybrid_search(tenant, "alpha", 2)
    assert [r.document_name for r in results] == ["Keyword"]


@pytest.mark.asyncio
async def test_hybrid_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.setattr(knowledge.settings, "knowledge_hybrid_search", False)
    called = []
    hit = knowledge.SearchResult(chunk_text="x", score=0.5, document_name="Synthetic")

    async def legacy(*_args):
        called.append("legacy")
        return [hit]

    async def hybrid(*_args):
        called.append("hybrid")
        return [hit]

    monkeypatch.setattr(knowledge, "_native_vector_or_keyword_search", legacy)
    monkeypatch.setattr(knowledge, "_native_hybrid_search", hybrid)
    tenant = str(uuid.uuid4())
    await knowledge._native_semantic_search(tenant, "alpha", 1)
    monkeypatch.setattr(knowledge.settings, "knowledge_hybrid_search", True)
    await knowledge._native_semantic_search(tenant, "alpha", 1)
    assert called == ["legacy", "hybrid"]


@pytest.mark.asyncio
async def test_hybrid_blank_query_does_not_reach_document_fallback(monkeypatch) -> None:
    monkeypatch.setattr(knowledge.settings, "knowledge_hybrid_search", True)

    async def unexpected(*_args):
        raise AssertionError("blank query should not reach native search")

    monkeypatch.setattr(knowledge, "_native_hybrid_search", unexpected)
    assert await knowledge._native_semantic_search(str(uuid.uuid4()), "  ", 3) == []


@pytest.mark.asyncio
async def test_search_response_contract_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)

    async def native(*_args):
        return [knowledge.SearchResult(chunk_text="alpha", score=0.5, document_name="Synthetic")]

    async def guard(_tenant, results):
        return results

    monkeypatch.setattr(knowledge, "_native_semantic_search", native)
    monkeypatch.setattr(knowledge, "_guard_results", guard)
    response = await knowledge._search_knowledge(knowledge.SearchRequest(query="alpha"), str(uuid.uuid4()))
    assert response.model_dump() == {
        "results": [{"chunk_text": "alpha", "score": 0.5, "document_name": "Synthetic"}]
    }


@pytest.mark.asyncio
async def test_all_hybrid_backends_failing_is_not_empty_success(scoped_session, monkeypatch) -> None:
    import core.embeddings

    tenant, sessions = scoped_session
    sessions.append(_Session(errors={"lexical", "keyword", "vector"}))

    async def fail_embed(_query):
        raise TimeoutError("embedding unavailable")

    monkeypatch.setattr(core.embeddings, "embed_one_async", fail_embed)
    monkeypatch.setattr(knowledge.settings, "knowledge_hybrid_search", True)
    with pytest.raises(RuntimeError, match="knowledge filename fallback failed"):
        await knowledge._native_semantic_search(str(tenant), "alpha", 2)
