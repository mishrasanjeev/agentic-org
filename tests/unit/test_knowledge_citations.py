# SPDX-License-Identifier: Apache-2.0
"""Citations on search hits and the excerpt a reader opens from one."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from api.v1 import knowledge
from core.config import settings
from core.rag import citations


class TestCitations:
    def test_a_citation_names_the_place_and_the_chunk(self):
        source = "upload://policy.pdf#chunk12-ab12cd34ef56"
        citation = citations.citation_from_row("doc-1", source, 4, 12, "Exposure limits", None, None)
        assert citation.chunk_index == 12 and citation.page == 4 and citation.paragraph == 12
        assert citation.label() == "page 4 · paragraph 12 · Exposure limits"
        assert citations.source_prefix(source) == "upload://policy.pdf"
        assert citations.chunk_index_of("upload://x.txt") is None and citations.source_prefix(None) is None
        sheet = citations.citation_from_row("doc-2", None, None, None, None, "Q1", "A1:C9")
        assert sheet.label() == "sheet Q1 · A1:C9" and sheet.chunk_index is None

    def test_highlights_locate_the_query_terms(self):
        text = "Locker rent is charged yearly. Rent is refunded pro rata."
        spans = citations.highlights(text, "rent refund")
        assert [text[a:b] for a, b in spans] == ["rent", "Rent"]
        assert citations.highlights(text, "") == [] and citations.highlights("", "rent") == []
        assert len(citations.highlights("rent " * 200, "rent")) == citations.MAX_HIGHLIGHTS


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt, params):
        self.calls.append((str(stmt), params))
        return _Rows(self.answers.pop(0) if self.answers else [])


@pytest.fixture
def sessions(monkeypatch):
    import core.database

    holder = {"session": _Session()}
    monkeypatch.setattr(core.database, "get_tenant_session", lambda _tid: holder["session"])

    async def guard(_tenant, results):
        return results

    monkeypatch.setattr(knowledge, "_guard_results", guard)
    return holder


ROW = (
    "11111111-1111-1111-1111-111111111111",
    "Policy",
    "Exposure is capped.",
    "upload://policy.pdf#chunk3-abcdef012345",
    4,
    12,
    "Exposure limits",
    None,
    None,
)


@pytest.mark.asyncio
async def test_every_search_path_joins_provenance_and_returns_a_citation(sessions, monkeypatch):
    async def _embed(_query):
        return [0.1, 0.2]

    import core.embeddings

    monkeypatch.setattr(core.embeddings, "embed_one_async", _embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    # Vector-or-keyword path: the vector row carries title, content, score, then id, source and provenance.
    sessions["session"] = _Session(
        [("Policy", "Exposure is capped.", 0.9, ROW[0], ROW[3], 4, 12, "Exposure limits", None, None)]
    )
    results = await knowledge._native_vector_or_keyword_search(uuid.uuid4(), "exposure", 3, None)
    assert results[0].citation is not None and results[0].citation.label() == "page 4 · paragraph 12 · Exposure limits"
    assert "LEFT JOIN knowledge_chunk_sources s" in sessions["session"].calls[0][0]
    # Hybrid path: lexical and vector rows carry the citation through fusion.
    monkeypatch.setattr(settings, "knowledge_hybrid_search", True)
    session = _Session([ROW], [ROW])
    sessions["session"] = session
    results = await knowledge._native_hybrid_search(uuid.uuid4(), "exposure", 3, None)
    assert results[0].citation is not None and results[0].citation.chunk_index == 3 and results[0].citation.page == 4
    assert all("LEFT JOIN knowledge_chunk_sources s" in sql for sql, _ in session.calls)
    # Re-ranking keeps the citation on the reordered hit.
    monkeypatch.setattr(settings, "knowledge_rerank_enabled", True)
    sessions["session"] = _Session([ROW], [ROW])
    results = await knowledge._native_hybrid_search(uuid.uuid4(), "exposure", 3, None)
    assert results[0].citation is not None and results[0].citation.paragraph == 12


def test_fusion_accepts_rows_with_and_without_a_citation():
    citation = citations.citation_from_row("a", "s#chunk1-abc", 1)
    results = knowledge._fuse_native_hits([("a", "A", "text", citation)], [("b", "B", "text")], 2)
    assert results[0].citation == citation and results[1].citation is None


@pytest.mark.asyncio
async def test_the_excerpt_returns_the_whole_chunk_its_place_and_its_neighbours(sessions):
    doc_id = uuid.UUID(ROW[0])
    previous = ("22222222-2222-2222-2222-222222222222",)
    sessions["session"] = _Session([ROW], [previous], [])
    excerpt = await knowledge.knowledge_excerpt(doc_id, q="exposure capped", tenant_id=str(uuid.uuid4()))
    assert excerpt.document_id == ROW[0] and excerpt.content == "Exposure is capped."
    assert excerpt.citation is not None and excerpt.citation.heading == "Exposure limits"
    assert [excerpt.content[a:b] for a, b in excerpt.highlights] == ["Exposure", "capped"]
    assert excerpt.previous_id == previous[0] and excerpt.next_id is None
    calls = sessions["session"].calls
    assert "d.id = :id AND d.tenant_id = :tid" in calls[0][0]
    assert (
        calls[1][1]["pattern"] == "upload://policy.pdf#chunk2-%"
        and calls[2][1]["pattern"] == "upload://policy.pdf#chunk4-%"
    )
    assert all("tenant_id = :tid" in sql for sql, _ in calls)


@pytest.mark.asyncio
async def test_a_missing_or_withheld_chunk_is_not_found(sessions, monkeypatch):
    sessions["session"] = _Session([])
    with pytest.raises(HTTPException) as missing:
        await knowledge.knowledge_excerpt(uuid.uuid4(), q=None, tenant_id=str(uuid.uuid4()))
    assert missing.value.status_code == 404

    async def withhold(_tenant, _results):
        return []

    monkeypatch.setattr(knowledge, "_guard_results", withhold)
    sessions["session"] = _Session([ROW], [], [])
    with pytest.raises(HTTPException) as withheld:
        await knowledge.knowledge_excerpt(uuid.UUID(ROW[0]), q=None, tenant_id=str(uuid.uuid4()))
    assert withheld.value.status_code == 404
