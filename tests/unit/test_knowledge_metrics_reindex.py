# SPDX-License-Identifier: Apache-2.0
"""Retrieval quality metrics, the grounding indicator and incremental re-indexing."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.v1 import knowledge
from core.config import settings
from core.rag import metrics, reindex

ROOT = Path(__file__).resolve().parents[2]


class TestRelevance:
    def test_coverage_and_relevance_are_shares_of_the_query_terms(self):
        assert metrics.coverage(["locker", "rent"], "The locker rent is 1200") == 1.0
        assert metrics.coverage(["locker", "rent"], "Rent is due") == 0.5
        assert metrics.coverage([], "anything") == 0.0
        assert metrics.relevance("locker rent", ["locker rent is 1200", "nothing here", "rent due"]) == (0.5, 0.6667)
        assert metrics.relevance("locker rent", []) == (0.0, 0.0)
        assert metrics.relevance("the of", ["x"]) == (0.0, 0.0)

    def test_a_sample_is_figures_only_and_off_by_default(self):
        s = metrics.sample(
            "locker rent",
            ["locker rent is 1200", "nothing"],
            [0.9, 0.1],
            path="hybrid",
            latency_ms=42,
            expanded=True,
            withheld=1,
        )
        assert s.as_dict() == {
            "path": "hybrid",
            "results": 2,
            "withheld": 1,
            "top_score": 0.9,
            "relevance": 0.5,
            "covered_share": 0.5,
            "latency_ms": 42,
            "expanded": True,
            "graph": False,
            "query_terms": 2,
        }
        assert metrics.sample("q", [], [], path="odd", latency_ms=-1).path == "vector_keyword"
        assert settings.knowledge_metrics_enabled is False and metrics.enabled() is False
        assert settings.knowledge_reindex_enabled is False and reindex.enabled() is False

    def test_observe_counts_by_path_and_outcome(self):
        from observability import metrics as prom

        before = prom.knowledge_searches_total.labels(path="hybrid", outcome="empty")._value.get()
        metrics.observe(metrics.sample("locker rent", [], [], path="hybrid", latency_ms=5))
        assert prom.knowledge_searches_total.labels(path="hybrid", outcome="empty")._value.get() == before + 1


class TestGrounding:
    def test_supported_sentences_count_and_the_rest_are_listed(self):
        chunks = ["The locker rent is 1200 rupees a year.", "A nominee can open the locker after death."]
        answer = (
            "The locker rent is 1200 rupees a year. A nominee may open the locker. "
            "The bank charges 5000 for a lost key."
        )
        result = metrics.grounding(answer, chunks)
        assert result["sentences"] == 3 and result["supported"] == 2 and result["score"] == 0.6667
        assert result["unsupported"] == ["The bank charges 5000 for a lost key."]
        assert result["hallucination_risk"] == "medium"

    def test_short_sentences_are_skipped_and_an_empty_answer_is_unknown(self):
        assert metrics.grounding("Yes. No.", ["anything"]) == {
            "sentences": 0,
            "supported": 0,
            "score": None,
            "unsupported": [],
            "hallucination_risk": "unknown",
        }
        low = metrics.grounding("The locker rent is 1200 rupees a year.", ["The locker rent is 1200 rupees a year."])
        assert low["score"] == 1.0 and low["hallucination_risk"] == "low"
        high = metrics.grounding("The bank charges 5000 for a lost key.", ["locker rent"])
        assert high["score"] == 0.0 and high["hallucination_risk"] == "high"
        many = metrics.grounding(" ".join(f"Sentence number {i} says something else entirely." for i in range(9)), [])
        assert len(many["unsupported"]) == metrics.MAX_UNSUPPORTED


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

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return _Rows(self.answers.pop(0) if self.answers else [])


@pytest.mark.asyncio
async def test_record_writes_one_row_and_summary_folds_the_window():
    tid = uuid.uuid4()
    session = _Session()
    await metrics.record(
        session, tid, metrics.sample("locker rent", ["locker rent"], [0.8], path="hybrid", latency_ms=7)
    )
    sql, params = session.calls[0]
    assert "INSERT INTO knowledge_retrieval_metrics" in sql and params["tid"] == str(tid) and params["relevance"] == 1.0
    assert "query" not in params and "text" not in params
    session = _Session([(10, 0.55, 0.8, 2, 30.0, 120.0, 3, 1, 4)], [("hybrid", 7), ("vector_keyword", 3)])
    folded = await metrics.summary(session, tid, 24 * 365)
    assert folded == {
        "window_hours": metrics.MAX_WINDOW_HOURS,
        "searches": 10,
        "empty_share": 0.2,
        "mean_relevance": 0.55,
        "mean_covered_share": 0.8,
        "p50_latency_ms": 30.0,
        "p95_latency_ms": 120.0,
        "expanded_share": 0.3,
        "graph_share": 0.1,
        "withheld": 4,
        "paths": {"hybrid": 7, "vector_keyword": 3},
    }
    assert all(
        "make_interval(hours => :hours)" in sql and params["hours"] == metrics.MAX_WINDOW_HOURS
        for sql, params in session.calls
    )
    empty = await metrics.summary(_Session([(0, None, None, 0, None, None, 0, 0, None)], []), tid, 1)
    assert empty["searches"] == 0 and empty["empty_share"] is None and empty["paths"] == {}


class TestReindex:
    @pytest.mark.asyncio
    async def test_stale_chunks_are_listed_oldest_first_with_bound_conditions(self):
        tid = uuid.uuid4()
        session = _Session([("c1", "text", "upload://a#chunk1-ab", "local/old-model", False)])
        rows = await reindex.stale_chunks(
            session, tid, model_name="local/new-model", since=date(2026, 1, 1), limit=5000, want_entities=True
        )
        assert rows[0][0] == "c1"
        sql, params = session.calls[0]
        assert "d.embedding_model IS DISTINCT FROM :model OR NOT EXISTS" in sql and "d.created_at >= :since" in sql
        assert "ORDER BY d.created_at ASC, d.id ASC LIMIT :limit" in sql and "d.status = 'ready'" in sql
        assert params == {
            "tid": str(tid),
            "model": "local/new-model",
            "limit": reindex.MAX_LIMIT,
            "since": date(2026, 1, 1),
        }
        plain = _Session([])
        await reindex.stale_chunks(plain, tid, model_name="m", limit=0)
        assert (
            "NOT EXISTS" not in plain.calls[0][0]
            and "since" not in plain.calls[0][1]
            and plain.calls[0][1]["limit"] == 1
        )

    def test_counts_name_what_is_stale(self):
        rows = [("a", "t", "s", "old", True), ("b", "t", "s", "new", False), ("c", "t", "s", None, False)]
        assert reindex.counts(rows, model_name="new", want_entities=True) == {
            "candidates": 3,
            "stale_embeddings": 2,
            "missing_entities": 2,
        }
        assert reindex.counts(rows, model_name="new", want_entities=False)["missing_entities"] == 0

    @pytest.mark.asyncio
    async def test_reindex_re_embeds_the_stale_rows_and_records_missing_entities(self, monkeypatch):
        tid = uuid.uuid4()
        rows = [
            ("a", "Form 16 is issued by the Income Tax Department", "upload://a#chunk1-ab", "old", False),
            ("b", "already current", "upload://b#chunk1-cd", "new", True),
        ]
        embedded = []

        async def _embed(texts):
            embedded.append(list(texts))
            return [[0.1, 0.2] for _ in texts]

        seen = []

        async def _index(session, tenant_id, source, text):
            seen.append((source, text))
            return 2

        from core.rag import entities

        monkeypatch.setattr(entities, "index_chunk", _index)
        session = _Session()
        done = await reindex.reindex(
            session, tid, rows, model_name="new", column="embedding", embed=_embed, want_entities=True
        )
        assert done == {"re_embedded": 1, "entities_indexed": 1}
        assert embedded == [["Form 16 is issued by the Income Tax Department"]]
        sql, params = session.calls[0]
        assert "UPDATE knowledge_documents SET embedding = CAST(:vec AS vector)" in sql
        assert params == {"vec": "[0.100000,0.200000]", "model": "new", "id": "a", "tid": str(tid)}
        assert seen == [("upload://a#chunk1-ab", "Form 16 is issued by the Income Tax Department")]
        with pytest.raises(ValueError):
            await reindex.reindex(session, tid, rows, model_name="new", column="other", embed=_embed)

        async def _short(texts):
            return []

        with pytest.raises(RuntimeError):
            await reindex.reindex(_Session(), tid, rows, model_name="new", column="embedding", embed=_short)


class TestSearchPath:
    @pytest.fixture
    def native(self, monkeypatch):
        async def _native(tenant_id, query, top_k, filters=None, domains=None):
            return [
                knowledge.SearchResult(chunk_text="locker rent is 1200", score=0.9, document_name="Fees"),
                knowledge.SearchResult(chunk_text="blocked", score=0.5, document_name="Fees"),
            ]

        async def _guard(_tenant, results):
            return [r for r in results if r.chunk_text != "blocked"]

        monkeypatch.setattr(knowledge, "_native_semantic_search", _native)
        monkeypatch.setattr(knowledge, "_guard_results", _guard)
        monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)

    @pytest.mark.asyncio
    async def test_off_nothing_is_recorded(self, native, monkeypatch):
        async def _record(*args):
            raise AssertionError("no sample while the switch is off")

        monkeypatch.setattr(metrics, "record", _record)
        response = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="locker rent", top_k=3), str(uuid.uuid4()), None
        )
        assert [r.chunk_text for r in response.results] == ["locker rent is 1200"]

    @pytest.mark.asyncio
    async def test_on_one_sample_is_recorded_with_the_withheld_count_and_a_failure_is_swallowed(
        self, native, monkeypatch
    ):
        monkeypatch.setattr(settings, "knowledge_metrics_enabled", True)
        monkeypatch.setattr(settings, "knowledge_hybrid_search", True)
        recorded = []

        async def _record(session, tenant_id, s):
            recorded.append((tenant_id, s))

        monkeypatch.setattr(metrics, "record", _record)
        monkeypatch.setattr(knowledge, "_graph_session", lambda _tid: _Session())
        tid = str(uuid.uuid4())
        response = await knowledge._search_knowledge(knowledge.SearchRequest(query="locker rent", top_k=3), tid, None)
        assert len(response.results) == 1 and len(recorded) == 1
        tenant_id, s = recorded[0]
        assert str(tenant_id) == tid
        assert s.path == "hybrid" and s.results == 1 and s.withheld == 1 and s.relevance == 1.0 and s.top_score == 0.9
        assert s.expanded is False and s.graph is False and s.latency_ms >= 0

        async def _broken(session, tenant_id, s):
            raise RuntimeError("metrics table missing")

        monkeypatch.setattr(metrics, "record", _broken)
        again = await knowledge._search_knowledge(knowledge.SearchRequest(query="locker rent", top_k=3), tid, None)
        assert [r.chunk_text for r in again.results] == ["locker rent is 1200"]


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await knowledge.knowledge_metrics(hours=24, tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "knowledge_metrics_disabled"
        with pytest.raises(HTTPException) as refused:
            await knowledge.knowledge_grounding(
                knowledge.GroundingRequest(answer="a b c d", chunks=["a"]), tenant_id=tid
            )
        assert refused.value.status_code == 404
        with pytest.raises(HTTPException) as refused:
            await knowledge.knowledge_reindex(knowledge.ReindexRequest(), tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "knowledge_reindex_disabled"

    @pytest.mark.asyncio
    async def test_on_metrics_and_grounding_answer_from_the_tenant_window(self, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_metrics_enabled", True)
        session = _Session([(2, 0.5, 1.0, 0, 10.0, 20.0, 0, 0, 0)], [("hybrid", 2)])
        monkeypatch.setattr(knowledge, "_graph_session", lambda _tid: session)
        folded = await knowledge.knowledge_metrics(hours=6, tenant_id=str(uuid.uuid4()))
        assert folded.searches == 2 and folded.window_hours == 6 and folded.paths == {"hybrid": 2}
        grounded = await knowledge.knowledge_grounding(
            knowledge.GroundingRequest(answer="The locker rent is 1200 rupees.", chunks=["locker rent is 1200 rupees"]),
            tenant_id=str(uuid.uuid4()),
        )
        assert grounded.score == 1.0 and grounded.hallucination_risk == "low"

    @pytest.mark.asyncio
    async def test_on_reindex_counts_in_a_dry_run_and_re_embeds_otherwise(self, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_reindex_enabled", True)
        rows = [("a", "text", "upload://a#chunk1-ab", "local/old", False)]
        listed = []

        async def _stale(session, tenant_id, *, model_name, since, limit, want_entities):
            listed.append((model_name, since, limit, want_entities))
            return rows

        monkeypatch.setattr(reindex, "stale_chunks", _stale)
        ran = []

        async def _run(session, tenant_id, got, *, model_name, column, embed, want_entities):
            ran.append((got, model_name, column, want_entities))
            return {"re_embedded": 1, "entities_indexed": 0}

        monkeypatch.setattr(reindex, "reindex", _run)

        async def _profile(_tid):
            return ("local", "BAAI/bge-small-en-v1.5", 384)

        from core.rag import ingest

        monkeypatch.setattr(ingest, "_resolve_embedding_profile", _profile)
        monkeypatch.setattr(knowledge, "_graph_session", lambda _tid: _Session())
        tid = str(uuid.uuid4())
        dry = await knowledge.knowledge_reindex(knowledge.ReindexRequest(limit=10), tenant_id=tid)
        assert dry.model_dump() == {
            "dry_run": True,
            "model": "local/BAAI/bge-small-en-v1.5",
            "candidates": 1,
            "stale_embeddings": 1,
            "missing_entities": 0,
            "re_embedded": 0,
            "entities_indexed": 0,
        }
        assert listed == [("local/BAAI/bge-small-en-v1.5", None, 10, False)] and ran == []
        wet = await knowledge.knowledge_reindex(knowledge.ReindexRequest(dry_run=False, limit=10), tenant_id=tid)
        assert wet.re_embedded == 1 and ran[0][0] == rows and ran[0][2] in ("embedding", "embedding_bge_m3")


def test_the_migration_is_shaped():
    migration = (ROOT / "migrations" / "versions" / "v6_z53_retrieval_metrics.py").read_text(encoding="utf-8")
    assert 'down_revision = "v6z52_knowledge_entities"' in migration
    assert "ix_knowledge_retrieval_metrics_tenant_created" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration and "knowledge_retrieval_metrics_tenant_isolation" in migration
    assert "query" not in migration.split("CREATE TABLE")[1].split(");")[0].replace("query_terms", "")
