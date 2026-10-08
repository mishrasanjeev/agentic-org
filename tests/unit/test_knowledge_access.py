# SPDX-License-Identifier: Apache-2.0
"""Document-level access control: a limited caller never sees a document outside its domains."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.v1 import knowledge
from core.config import settings
from core.rag import access, ingest

ROOT = Path(__file__).resolve().parents[2]


class TestRule:
    def test_an_unrestricted_caller_adds_nothing(self):
        assert access.normalise_domains(None) is None
        assert access.sql_clause(None) == ("", {})
        assert access.metadata_clause(None) == ("", {})
        assert access.may_see("finance", None) and access.may_see(None, None)

    def test_a_limited_caller_sees_shared_and_own_domain_documents(self):
        domains = access.normalise_domains([" Finance ", "hr", "", 7])
        assert domains == ["finance", "hr"]
        clause, params = access.sql_clause(domains)
        assert clause == " AND (d.domain IS NULL OR d.domain IN (:acl_0, :acl_1))"
        assert params == {"acl_0": "finance", "acl_1": "hr"}
        assert access.metadata_clause(domains)[0] == (
            " AND (metadata->>'domain' IS NULL OR metadata->>'domain' IN (:acl_0, :acl_1))"
        )
        assert (
            access.may_see(None, domains) and access.may_see("Finance", domains) and not access.may_see("ops", domains)
        )

    def test_a_limited_caller_with_no_domain_sees_shared_documents_only(self):
        assert access.sql_clause([]) == (" AND d.domain IS NULL", {})
        assert access.metadata_clause([]) == (" AND metadata->>'domain' IS NULL", {})
        assert not access.may_see("finance", []) and access.may_see(None, [])

    def test_the_domain_list_is_bounded(self):
        assert len(access.normalise_domains([f"d{i}" for i in range(80)]) or []) == access.MAX_DOMAINS


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return _Rows(self.answers.pop(0) if self.answers else [])

    def scalars(self):
        return self


@pytest.fixture
def sessions(monkeypatch):
    import core.database

    holder = {"session": _Session()}
    monkeypatch.setattr(core.database, "get_tenant_session", lambda _tid: holder["session"])

    async def guard(_tenant, results):
        return results

    monkeypatch.setattr(knowledge, "_guard_results", guard)

    async def _embed(_query):
        return [0.1, 0.2]

    import core.embeddings

    monkeypatch.setattr(core.embeddings, "embed_one_async", _embed)
    monkeypatch.setattr(core.embeddings, "rag_embedding_column", lambda: "embedding")
    return holder


@pytest.mark.asyncio
async def test_every_search_sql_carries_the_rule_for_a_limited_caller(sessions, monkeypatch):
    domains = ["finance"]
    session = _Session([], [])
    sessions["session"] = session
    await knowledge._native_vector_or_keyword_search(uuid.uuid4(), "locker", 3, None, domains)
    monkeypatch.setattr(settings, "knowledge_hybrid_search", True)
    hybrid = _Session([], [], [])
    sessions["session"] = hybrid
    await knowledge._native_hybrid_search(uuid.uuid4(), "locker", 3, None, domains)
    for sql, params in session.calls + hybrid.calls:
        assert "(d.domain IS NULL OR d.domain IN (:acl_0))" in sql and params["acl_0"] == "finance"
    # The uploads fallback applies the same rule through the metadata.
    monkeypatch.setattr(settings, "knowledge_hybrid_search", False)

    async def _nothing(_tid, _query, _top_k, _filters=None, _domains=None):
        return []

    monkeypatch.setattr(knowledge, "_native_vector_or_keyword_search", _nothing)
    fallback = _Session([])
    sessions["session"] = fallback
    await knowledge._native_semantic_search(str(uuid.uuid4()), "locker", 3, None, domains)
    assert fallback.calls and "metadata->>'domain' IN (:acl_0)" in fallback.calls[0][0]
    # The filename fallback, last of all, carries it through the ORM.
    assert len(fallback.calls) == 2
    assert "metadata ->> " in fallback.calls[1][0] and "IN (" in fallback.calls[1][0]


@pytest.mark.asyncio
async def test_an_unrestricted_caller_runs_the_sql_as_before(sessions):
    session = _Session([], [])
    sessions["session"] = session
    await knowledge._native_vector_or_keyword_search(uuid.uuid4(), "locker", 3, None, None)
    assert all("domain" not in sql for sql, _ in session.calls)


@pytest.mark.asyncio
async def test_the_excerpt_and_its_neighbours_are_withheld_outside_the_domains(sessions):
    session = _Session([])
    sessions["session"] = session
    with pytest.raises(HTTPException) as refused:
        await knowledge.knowledge_excerpt(uuid.uuid4(), q=None, tenant_id=str(uuid.uuid4()), user_domains=["hr"])
    assert refused.value.status_code == 404
    assert "(d.domain IS NULL OR d.domain IN (:acl_0))" in session.calls[0][0]
    row = (
        "11111111-1111-1111-1111-111111111111",
        "Policy",
        "Exposure is capped.",
        "upload://policy.pdf#chunk3-abcdef012345",
        None,
        None,
        None,
        None,
        None,
    )
    session = _Session([row], [], [])
    sessions["session"] = session
    excerpt = await knowledge.knowledge_excerpt(
        uuid.UUID(row[0]), q=None, tenant_id=str(uuid.uuid4()), user_domains=["hr"]
    )
    assert excerpt.previous_id is None
    assert all("(d.domain IS NULL OR d.domain IN (:acl_0))" in sql for sql, _ in session.calls)


@pytest.mark.asyncio
async def test_the_document_list_hides_what_the_caller_may_not_see(monkeypatch):
    monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)

    def _doc(document_id, filename, metadata):
        return {
            "document_id": document_id,
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": 10,
            "status": "indexed",
            "metadata": metadata,
            "created_at": "",
        }

    async def _rows(_tenant):
        return [
            _doc("a", "shared.pdf", {}),
            _doc("b", "hr.pdf", {"domain": "hr"}),
            _doc("c", "finance.pdf", {"domain": "finance"}),
        ]

    monkeypatch.setattr(knowledge, "_db_list_docs", _rows)
    # The listing's second read (seeded knowledge rows) is not for this test: no database here.
    import core.database

    def _no_database(_tid):
        raise RuntimeError("no database in this test")

    monkeypatch.setattr(core.database, "get_tenant_session", _no_database)
    limited = await knowledge.list_documents(page=1, per_page=20, tenant_id=str(uuid.uuid4()), user_domains=["hr"])
    assert [d.filename for d in limited.items] == ["shared.pdf", "hr.pdf"] and limited.total == 2
    everything = await knowledge.list_documents(page=1, per_page=20, tenant_id=str(uuid.uuid4()), user_domains=None)
    assert everything.total == 3


def test_ingestion_writes_the_domain_and_the_upload_names_it():
    assert ingest._document_domain({"domain": " Finance "}) == "finance"
    assert ingest._document_domain({"domain": ""}) is None and ingest._document_domain(None) is None
    src = (ROOT / "core" / "rag" / "ingest.py").read_text(encoding="utf-8")
    assert "CAST(:vector AS vector), :domain, now())" in src and '"domain": document_domain,' in src
    api = (ROOT / "api" / "v1" / "knowledge.py").read_text(encoding="utf-8")
    assert 'metadata={"domain": domain} if domain else None,' in api
    assert 'doc_metadata["domain"] = domain' in api
    assert (
        "user_domains: list[str] | None = Depends(get_user_domains)"
        in api[api.index("async def search_knowledge(") :][:400]
    )


def test_the_migration_adds_a_nullable_domain_with_an_index():
    src = (ROOT / "migrations" / "versions" / "v6_z51_knowledge_document_domain.py").read_text(encoding="utf-8")
    assert 'down_revision = "v6z50_chunk_layout"' in src
    assert "ADD COLUMN IF NOT EXISTS domain VARCHAR(50) NULL" in src
    assert "ix_knowledge_documents_tenant_domain ON knowledge_documents(tenant_id, domain)" in src
