# SPDX-License-Identifier: Apache-2.0
"""Search filters as bound SQL, the re-ranking stage, and both wired into the native search paths."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.v1 import knowledge
from core.config import settings
from core.rag import rerank
from core.rag.filters import SearchFilters, sql_clauses

ROOT = Path(__file__).resolve().parents[2]


class TestFilters:
    def test_filters_become_bound_and_clauses_and_nothing_from_the_request_enters_the_sql(self):
        filters = SearchFilters(
            category=[" retail ", "sme"], file_type=["pdf"], created_from=date(2026, 1, 1), created_to=date(2026, 6, 30)
        )
        where, params = sql_clauses(filters)
        assert where == (
            " AND category IN (:f_category_0, :f_category_1) AND file_type IN (:f_file_type_0)"
            " AND created_at >= :f_from AND created_at <= :f_to"
        )
        assert params["f_category_0"] == "retail" and params["f_category_1"] == "sme"
        assert params["f_from"].isoformat().startswith("2026-01-01T00:00:00") and params["f_to"].hour == 23
        assert "retail" not in where

    def test_empty_filters_add_nothing(self):
        assert sql_clauses(None) == ("", {})
        assert sql_clauses(SearchFilters()) == ("", {})
        assert SearchFilters(category=["  ", ""]).is_empty()

    def test_refused_shapes(self):
        with pytest.raises(ValidationError):
            SearchFilters(branch=["x"])
        with pytest.raises(ValidationError):
            SearchFilters(created_from=date(2026, 6, 1), created_to=date(2026, 1, 1))
        with pytest.raises(ValidationError):
            SearchFilters(category=["c" * 201])
        with pytest.raises(ValidationError):
            SearchFilters(source=[f"s{i}" for i in range(21)])


class TestRerank:
    def _candidate(self, key, title, text, fused=1.0):
        return rerank.Candidate(key=key, title=title, text=text, fused=fused)

    def test_the_query_terms_decide_the_order_and_the_fused_score_breaks_ties(self):
        candidates = [
            self._candidate("a", "Fees", "Our fees for a locker are listed in the schedule.", fused=0.9),
            self._candidate(
                "b", "Locker rent", "Locker rent is charged yearly in advance and refunded pro rata.", fused=0.5
            ),
            self._candidate("c", "Holidays", "Branches are closed on public holidays.", fused=0.7),
        ]
        ordered = rerank.rerank("locker rent refund", candidates, 3)
        assert [c.key for c, _ in ordered] == ["b", "a", "c"]
        scores = [s for _, s in ordered]
        assert scores[0] > scores[1] > scores[2] and all(0.0 <= s <= 1.0 for s in scores)
        # The fused score is the tie-breaker when the texts say the same.
        same = [
            self._candidate("x", "T", "no match here", fused=0.4),
            self._candidate("y", "T", "no match here", fused=0.8),
        ]
        assert [c.key for c, _ in rerank.rerank("locker", same, 2)] == ["y", "x"]

    def test_the_parts_of_a_score_are_explainable(self):
        parts = rerank.explain(
            "locker rent",
            self._candidate("b", "Locker rent", "Locker rent is charged yearly.", fused=1.0),
            max_fused=1.0,
        )
        assert (
            parts["coverage"] == 1.0 and parts["phrase"] == 1.0 and parts["title"] == 1.0 and parts["proximity"] == 1.0
        )
        assert parts["score"] == 1.0
        far = rerank.explain(
            "locker rent", self._candidate("z", "Notes", "locker " + "word " * 40 + "rent", fused=0.0), max_fused=1.0
        )
        assert far["coverage"] == 1.0 and far["phrase"] == 0.0 and far["proximity"] < 0.2

    def test_stop_words_are_ignored_and_the_pool_is_bounded(self):
        assert rerank.terms("what is the fee for a locker") == ["fee", "locker"]
        assert rerank.terms("the") == ["the"]
        pool = [self._candidate(str(i), "T", "locker", fused=1.0) for i in range(300)]
        assert len(rerank.rerank("locker", pool, 5)) == 5 and rerank.rerank("locker", [], 5) == []

    def test_off_by_default(self):
        assert settings.knowledge_rerank_enabled is False and rerank.enabled() is False


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt, params):
        self.calls.append((str(stmt), params))
        return _Rows(self.rows)


@pytest.fixture
def sessions(monkeypatch):
    import core.database

    holder = {"session": _Session([])}
    monkeypatch.setattr(core.database, "get_tenant_session", lambda _tid: holder["session"])
    return holder


@pytest.mark.asyncio
async def test_filters_reach_every_sql_of_the_hybrid_path(sessions, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_hybrid_search", True)

    async def _embed(_query):
        return [0.1, 0.2]

    import core.embeddings

    monkeypatch.setattr(core.embeddings, "embed_one_async", _embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    session = _Session([("1", "Locker rent", "Locker rent is charged yearly.")])
    sessions["session"] = session
    filters = SearchFilters(category=["retail"], created_from=date(2026, 1, 1))
    results = await knowledge._native_hybrid_search(uuid.uuid4(), "locker rent", 3, filters)
    assert results and results[0].document_name == "Locker rent"
    for sql, params in session.calls:
        assert "category IN (:f_category_0)" in sql and "created_at >= :f_from" in sql
        assert params["f_category_0"] == "retail" and "tenant_id = :tid" in sql


@pytest.mark.asyncio
async def test_filters_reach_the_vector_and_keyword_paths(sessions, monkeypatch):
    async def _embed(_query):
        return [0.1, 0.2]

    import core.embeddings

    monkeypatch.setattr(core.embeddings, "embed_one_async", _embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    session = _Session([])
    sessions["session"] = session
    await knowledge._native_vector_or_keyword_search(uuid.uuid4(), "locker", 3, SearchFilters(file_type=["pdf"]))
    assert len(session.calls) == 2
    for sql, params in session.calls:
        assert "file_type IN (:f_file_type_0)" in sql and params["f_file_type_0"] == "pdf"


@pytest.mark.asyncio
async def test_a_narrowed_search_does_not_fall_back_to_upload_metadata(monkeypatch):
    async def _nothing(_tid, _query, _top_k, _filters=None):
        return []

    monkeypatch.setattr(knowledge, "_native_vector_or_keyword_search", _nothing)
    called = []

    import core.database

    def _session(_tid):
        called.append(_tid)
        raise AssertionError("the document fallback must not run for a narrowed search")

    monkeypatch.setattr(core.database, "get_tenant_session", _session)
    assert await knowledge._native_semantic_search(str(uuid.uuid4()), "locker", 3, SearchFilters(category=["x"])) == []
    assert called == []


@pytest.mark.asyncio
async def test_rerank_reorders_the_fused_pool_when_on(sessions, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_hybrid_search", True)
    monkeypatch.setattr(settings, "knowledge_rerank_enabled", True)

    async def _embed(_query):
        return [0.1, 0.2]

    import core.embeddings

    monkeypatch.setattr(core.embeddings, "embed_one_async", _embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    # Both channels rank the off-topic document first; the re-ranker puts the one that answers on top.
    rows = [
        ("1", "Holidays", "Branches are closed on public holidays."),
        ("2", "Locker rent", "Locker rent is charged yearly."),
    ]
    sessions["session"] = _Session(rows)
    results = await knowledge._native_hybrid_search(uuid.uuid4(), "locker rent", 2, None)
    assert [r.document_name for r in results] == ["Locker rent", "Holidays"]
    assert results[0].score > results[1].score
    monkeypatch.setattr(settings, "knowledge_rerank_enabled", False)
    sessions["session"] = _Session(rows)
    results = await knowledge._native_hybrid_search(uuid.uuid4(), "locker rent", 2, None)
    assert [r.document_name for r in results] == ["Holidays", "Locker rent"]


def test_the_request_carries_filters_and_the_response_shape_is_unchanged():
    request = knowledge.SearchRequest(query="locker", filters={"category": ["retail"]})
    assert request.filters is not None and request.filters.category == ["retail"]
    assert set(knowledge.SearchResult.model_fields) == {"chunk_text", "score", "document_name"}
    src = (ROOT / "api" / "v1" / "knowledge.py").read_text(encoding="utf-8")
    assert "await _native_semantic_search(tenant_id, req.query, req.top_k, req.filters)" in src
