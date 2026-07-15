from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _FailingSessionContext:
    async def __aenter__(self):
        raise RuntimeError("durable store unavailable")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Upload:
    filename = "doc.txt"
    content_type = "text/plain"
    size = None

    def __init__(self, content: bytes = b"hello"):
        self._file = io.BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)


@pytest.mark.asyncio
async def test_chat_hitl_queue_failure_is_retryable_not_success(monkeypatch):
    from api.v1 import chat

    monkeypatch.setattr(chat, "get_tenant_session", lambda _tenant_id: _FailingSessionContext())

    recorded = await chat._record_chat_hitl(
        tenant_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        agent_type="tax_compliance",
        agent_name="TDS Agent",
        domain="finance",
        query="file 26Q for 10000",
        hitl_trigger="tds_filing_requires_approval",
        confidence=0.91,
    )

    assert recorded is False
    with pytest.raises(HTTPException) as exc_info:
        chat._raise_chat_hitl_persist_failed(
            agent_id="agent-1",
            trigger="tds_filing_requires_approval",
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "chat_hitl_queue_unavailable"


@pytest.mark.asyncio
async def test_knowledge_upload_db_persist_failure_returns_503(monkeypatch):
    from api.v1 import knowledge

    monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)

    async def _no_existing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge, "_db_find_existing_by_filename", _no_existing)

    async def _fail_store(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(knowledge, "_db_store_doc", _fail_store)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge.upload_document(
            file=_Upload(),
            tenant_id=str(uuid.uuid4()),
            allow_duplicate=False,
            replace=False,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "document_metadata_persist_failed"


@pytest.mark.asyncio
async def test_knowledge_search_exhausted_backends_raise_not_empty(monkeypatch):
    import core.database
    from api.v1 import knowledge

    monkeypatch.setattr(core.database, "get_tenant_session", lambda _tenant_id: _FailingSessionContext())

    with pytest.raises(RuntimeError, match="knowledge filename fallback failed"):
        await knowledge._native_semantic_search(str(uuid.uuid4()), "invoice", 3)


@pytest.mark.asyncio
async def test_knowledge_search_embedding_timeout_uses_keyword_fallback(monkeypatch):
    import httpx

    import core.database
    import core.embeddings
    from api.v1 import knowledge

    class _Rows:
        def fetchall(self):
            return [("Smoke Doc", "AgenticOrg production smoke knowledge text")]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, _stmt, _params):
            return _Rows()

    monkeypatch.setattr(
        core.embeddings,
        "embed_one",
        lambda _query: (_ for _ in ()).throw(httpx.ReadTimeout("TEI read timed out")),
    )
    monkeypatch.setattr(core.database, "get_tenant_session", lambda _tenant_id: _Session())

    results = await knowledge._native_semantic_search(
        str(uuid.uuid4()),
        "AgenticOrg production smoke",
        3,
    )

    assert len(results) == 1
    assert results[0].document_name == "Smoke Doc"
    assert results[0].chunk_text == "AgenticOrg production smoke knowledge text"
    assert results[0].score == 0.0


@pytest.mark.asyncio
async def test_agent_connector_config_failure_does_not_return_partial_success(monkeypatch):
    import core.crypto
    import core.database
    from api.v1 import agents

    class _Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(
                connector_name="zoho_books",
                config={},
                credentials_encrypted={"_encrypted": "bad-token"},
            )

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, _stmt):
            return _Result()

    monkeypatch.setattr(
        core.database,
        "get_tenant_session",
        lambda _tenant_id, _company_id=None: _Session(),
    )
    monkeypatch.setattr(
        core.crypto,
        "decrypt_for_tenant",
        lambda _ciphertext: (_ for _ in ()).throw(RuntimeError("decrypt failed")),
    )

    with pytest.raises(RuntimeError, match="Failed to load connector configuration"):
        await agents._resolve_connector_configs(
            tenant_id=str(uuid.uuid4()),
            connector_ids=["registry-zoho_books"],
        )
