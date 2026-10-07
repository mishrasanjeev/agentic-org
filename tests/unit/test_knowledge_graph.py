# SPDX-License-Identifier: Apache-2.0
"""Graph retrieval: entities found at ingestion, walked at search time, with every lookup bound and tenant scoped."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.v1 import knowledge
from core.config import settings
from core.rag import entities

ROOT = Path(__file__).resolve().parents[2]
TEXT = (
    "Form 16 is issued by the Income Tax Department before 15 June 2026. The Reserve Bank of India caps the "
    "locker rent at INR 5,000 a year; see KYC-2024 and ISO 27001. Form 16 is also needed for a loan. "
    "Account 123456789012 and card 4111 1111 1111 1111 are never recorded. The bank opens on 2026-04-01."
)


class TestExtract:
    def test_names_codes_amounts_and_dates_are_found_with_their_kind_and_count(self):
        found = {(e.entity, e.kind): e.mentions for e in entities.extract(TEXT)}
        assert found[("form 16", "code")] == 2
        assert found[("income tax department", "name")] == 1
        assert found[("reserve bank of india", "name")] == 1
        assert found[("kyc-2024", "code")] == 1 and found[("iso 27001", "code")] == 1
        assert found[("inr5000", "amount")] == 1
        assert found[("15 june 2026", "date")] == 1 and found[("2026-04-01", "date")] == 1

    def test_identity_numbers_and_sentence_starters_are_never_entities(self):
        keys = {e.entity for e in entities.extract(TEXT)}
        assert not any("123456789012" in k or "4111" in k for k in keys)
        assert "the reserve bank of india" not in keys and "the bank" not in keys
        assert entities.extract("") == []
        assert all(e.kind in entities.KINDS for e in entities.extract(TEXT))

    def test_the_list_is_bounded_and_ordered_by_mentions(self):
        many = " ".join(f"Code{i} Name{i}" for i in range(80))
        found = entities.extract(many)
        assert len(found) == entities.MAX_PER_CHUNK
        ordered = entities.extract("Form 16, Form 16, Form 16 and KYC-2024")
        assert ordered[0].entity == "form 16" and ordered[0].mentions == 3

    def test_off_by_default(self):
        assert settings.knowledge_graph_retrieval_enabled is False and entities.enabled() is False


class TestClauses:
    def test_the_query_names_entities_and_terms(self):
        keys, words = entities.query_keys("What does Form 16 say about locker rent?")
        assert keys == ["form 16"] and words == ["form", "about", "locker", "rent"]

    def test_the_match_clause_binds_every_value_and_escapes_wildcards(self):
        clause, params = entities.match_clause(["form 16"], ["50%_off", "rent"])
        assert clause == (
            "(e.entity IN (:g_k0) OR ' ' || e.entity || ' ' LIKE :g_w0 ESCAPE '\\' "
            "OR ' ' || e.entity || ' ' LIKE :g_w1 ESCAPE '\\')"
        )
        assert params == {"g_k0": "form 16", "g_w0": "% 50\\%\\_off %", "g_w1": "% rent %"}
        assert entities.match_clause([], []) == ("", {})


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


PROV = (None, None, None, None, None)


@pytest.mark.asyncio
async def test_expand_matches_walks_the_neighbours_and_reads_the_linked_chunks_tenant_scoped():
    tid = uuid.uuid4()
    session = _Session(
        [("form 16", "code", 3, 5)],
        [("form 16", "income tax department", "name", 2), ("form 16", "15 june 2026", "date", 1)],
        [("c1", "Tax guide", "Form 16 is issued by the Income Tax Department", "upload://tax.pdf#chunk1-ab", *PROV, 2)],
    )
    rows, detail = await entities.expand(session, tid, "Form 16 filing", ["finance"])
    assert detail == {"matched": ["form 16"], "neighbours": 2, "edges": 2, "chunks": 1}
    assert rows[0][1] == "Tax guide"
    matched_sql, matched_params = session.calls[0]
    assert "e.tenant_id = :tid AND d.status = 'ready' AND (d.domain IS NULL OR d.domain IN (:acl_0))" in matched_sql
    assert matched_params["g_k0"] == "form 16" and matched_params["tid"] == str(tid)
    neighbour_sql, neighbour_params = session.calls[1]
    assert "m.entity IN (:n_0)" in neighbour_sql and neighbour_params["n_0"] == "form 16"
    chunk_sql, chunk_params = session.calls[2]
    assert "e.entity IN (:c_0, :c_1, :c_2)" in chunk_sql and chunk_params["c_2"] == "15 june 2026"
    assert "LEFT JOIN knowledge_chunk_sources s" in chunk_sql and "d.status = 'ready'" in chunk_sql
    for sql, _ in session.calls:
        assert "form 16" not in sql


@pytest.mark.asyncio
async def test_a_query_that_names_nothing_reads_nothing():
    session = _Session()
    rows, detail = await entities.expand(session, uuid.uuid4(), "???", None)
    assert rows == [] and detail["matched"] == [] and session.calls == []


@pytest.mark.asyncio
async def test_index_chunk_records_the_entities_of_a_stored_chunk_and_nothing_for_a_missing_one():
    tid = uuid.uuid4()
    session = _Session([("chunk-id",)])
    written = await entities.index_chunk(session, tid, "upload://tax.pdf#chunk1-ab", "Form 16 and KYC-2024")
    assert written == 2
    lookup, lookup_params = session.calls[0]
    assert (
        "WHERE tenant_id = :tid AND source = :source" in lookup
        and lookup_params["source"] == "upload://tax.pdf#chunk1-ab"
    )
    insert, rows = session.calls[1]
    assert "ON CONFLICT (document_id, entity) DO NOTHING" in insert
    assert rows == [
        {"tid": str(tid), "doc": "chunk-id", "entity": "form 16", "kind": "code", "mentions": 1},
        {"tid": str(tid), "doc": "chunk-id", "entity": "kyc-2024", "kind": "code", "mentions": 1},
    ]
    missing = _Session([])
    assert await entities.index_chunk(missing, tid, "upload://x#chunk1-ab", "Form 16") == 0 and len(missing.calls) == 1
    assert await entities.index_chunk(_Session(), tid, "upload://x#chunk1-ab", "nothing here") == 0


class TestSearchPath:
    @pytest.fixture
    def native(self, monkeypatch):
        calls = []

        async def _native(tenant_id, query, top_k, filters=None, domains=None):
            calls.append(query)
            return [knowledge.SearchResult(chunk_text="locker rent is 1200", score=0.9, document_name="Fees")]

        async def _guard(_tenant, results):
            return results

        monkeypatch.setattr(knowledge, "_native_semantic_search", _native)
        monkeypatch.setattr(knowledge, "_guard_results", _guard)
        monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)
        return calls

    @pytest.mark.asyncio
    async def test_off_the_graph_is_not_consulted(self, native, monkeypatch):
        async def _expand(*args, **kwargs):
            raise AssertionError("the graph must not be read while the switch is off")

        monkeypatch.setattr(entities, "expand", _expand)
        response = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="Form 16 locker rent", top_k=3, trace=True), str(uuid.uuid4()), None
        )
        assert [r.chunk_text for r in response.results] == ["locker rent is 1200"] and response.trace is None

    @pytest.mark.asyncio
    async def test_on_the_graph_chunks_are_fused_in_and_traced(self, native, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_graph_retrieval_enabled", True)
        monkeypatch.setattr(settings, "knowledge_query_transform_enabled", True)
        seen = []

        async def _expand(session, tenant_id, query, domains, *, limit):
            seen.append((query, domains, limit))
            return (
                [
                    (
                        "c1",
                        "Tax guide",
                        "Form 16 is issued by the Income Tax Department",
                        "upload://t.pdf#chunk1-ab",
                        *PROV,
                        2,
                    ),
                    ("c2", "Fees", "locker rent is 1200", "upload://f.pdf#chunk2-cd", *PROV, 1),
                ],
                {"matched": ["form 16"], "neighbours": 1, "edges": 1, "chunks": 2},
            )

        monkeypatch.setattr(entities, "expand", _expand)
        monkeypatch.setattr(knowledge, "_graph_session", lambda _tid: _Session())
        request = knowledge.SearchRequest(query="Form 16 locker rent", top_k=3, trace=True)
        response = await knowledge._search_knowledge(request, str(uuid.uuid4()), ["finance"])
        assert seen == [("Form 16 locker rent", ["finance"], 6)]
        # The chunk both the search and the graph found comes first; the graph-only chunk follows with a citation.
        assert [r.chunk_text for r in response.results] == [
            "locker rent is 1200",
            "Form 16 is issued by the Income Tax Department",
        ]
        assert response.results[1].citation is not None and response.results[1].citation.document_id == "c1"
        assert response.trace is not None
        graph = [s for s in response.trace.steps if s.stage == "graph"]
        assert graph and graph[0].detail == {"matched": ["form 16"], "neighbours": 1, "edges": 1, "chunks": 2}

    @pytest.mark.asyncio
    async def test_a_graph_failure_leaves_the_search_answer_as_it_was(self, native, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_graph_retrieval_enabled", True)

        async def _broken(*args, **kwargs):
            raise RuntimeError("graph down")

        monkeypatch.setattr(entities, "expand", _broken)
        monkeypatch.setattr(knowledge, "_graph_session", lambda _tid: _Session())
        response = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="Form 16", top_k=3), str(uuid.uuid4()), None
        )
        assert [r.chunk_text for r in response.results] == ["locker rent is 1200"]


class TestGraphEndpoint:
    @pytest.mark.asyncio
    async def test_off_the_endpoint_is_not_found(self):
        with pytest.raises(HTTPException) as refused:
            await knowledge.knowledge_graph(q="Form 16", limit=20, tenant_id=str(uuid.uuid4()), user_domains=None)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "knowledge_graph_disabled"

    @pytest.mark.asyncio
    async def test_on_it_returns_the_matched_entities_their_neighbours_and_the_edges(self, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_graph_retrieval_enabled", True)
        session = _Session(
            [("form 16", "code", 3, 5)],
            [("form 16", "income tax department", "name", 2)],
        )
        monkeypatch.setattr(knowledge, "_graph_session", lambda _tid: session)
        response = await knowledge.knowledge_graph(
            q="Form 16", limit=5, tenant_id=str(uuid.uuid4()), user_domains=["hr"]
        )
        assert [e.model_dump() for e in response.entities] == [
            {"entity": "form 16", "kind": "code", "chunks": 3, "mentions": 5, "matched": True},
            {"entity": "income tax department", "kind": "name", "chunks": 2, "mentions": 0, "matched": False},
        ]
        assert [e.model_dump() for e in response.edges] == [
            {"source": "form 16", "target": "income tax department", "weight": 2}
        ]
        assert all("(d.domain IS NULL OR d.domain IN (:acl_0))" in sql for sql, _ in session.calls)


def test_ingestion_indexes_entities_only_while_on_and_the_migration_is_shaped():
    src = (ROOT / "core" / "rag" / "ingest.py").read_text(encoding="utf-8")
    assert (
        "if entities.enabled():" in src
        and "await entities.index_chunk(session, tid, canonical_source, chunk_text)" in src
    )
    migration = (ROOT / "migrations" / "versions" / "v6_z52_knowledge_entities.py").read_text(encoding="utf-8")
    assert 'down_revision = "v6z51_knowledge_document_domain"' in migration
    assert "REFERENCES knowledge_documents(id) ON DELETE CASCADE" in migration
    assert "ux_knowledge_entities_document_entity" in migration and "ix_knowledge_entities_tenant_entity" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration and "knowledge_entities_tenant_isolation" in migration
