# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded upload recovery: state transitions, source safety, retries and worker wiring."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from core.rag import upload_recovery as recovery
from core.rag.extractors import ExtractedContent, ExtractedSpan
from core.rag.ingest import IngestResult, transient_embedding_failure


def content():
    return ExtractedContent(
        spans=[ExtractedSpan(text="Synthetic policy 40721", page=2)],
        mime_type="text/plain",
        extraction_method="plaintext",
        total_chars=22,
    )


@pytest.fixture
def scope(monkeypatch):
    tenant, document = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        id=document,
        tenant_id=tenant,
        status="processing",
        filename="policy.txt",
        name="policy.txt",
        content_type="text/plain",
        metadata_={recovery.KEY: recovery.prepare(content()), "domain": "finance"},
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=row), execute=AsyncMock())

    @asynccontextmanager
    async def scoped(tid):
        assert tid == tenant
        yield session

    monkeypatch.setattr(recovery, "get_tenant_session", scoped)
    monkeypatch.setattr(recovery.settings, "knowledge_upload_recovery_enabled", True)
    ingest = AsyncMock(return_value=IngestResult("", 1, 0, 5, "text/plain", "plaintext", "model", 384))
    monkeypatch.setattr(recovery, "ingest_document", ingest)
    return tenant, row, session, ingest


def test_prepared_text_is_bounded_and_preserves_provenance(monkeypatch):
    prepared = recovery.prepare(content(), replaces=str(uuid.uuid4()))
    assert prepared["content"]["spans"][0]["page"] == 2
    monkeypatch.setattr(recovery, "MAX_SPANS", 0)
    assert recovery.prepare(content()) is None
    monkeypatch.setattr(recovery, "MAX_SPANS", 2)
    monkeypatch.setattr(recovery, "MAX_PREPARED_BYTES", 10)
    assert recovery.prepare(content()) is None


@pytest.mark.parametrize(
    "code,expected", [(400, False), (401, False), (403, False), (422, False), (429, True), (500, True), (503, True)]
)
def test_only_transient_http_errors_retry(code, expected):
    response = httpx.Response(code, request=httpx.Request("POST", "https://embeddings.example.test"))
    error = httpx.HTTPStatusError("synthetic", request=response.request, response=response)
    assert transient_embedding_failure(error) is expected


def test_transport_failures_retry_but_parse_failures_do_not():
    assert transient_embedding_failure(httpx.ReadTimeout("synthetic"))
    assert transient_embedding_failure(TimeoutError())
    assert not transient_embedding_failure(ValueError("invalid vector"))


@pytest.mark.asyncio
async def test_flag_off_never_reads_documents(scope, monkeypatch):
    tenant, row, session, ingest = scope
    monkeypatch.setattr(recovery.settings, "knowledge_upload_recovery_enabled", False)
    assert await recovery.process(tenant, row.id) == "disabled"
    session.scalar.assert_not_awaited()
    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_locked_deleted_or_missing_document_never_indexes(scope):
    tenant, row, session, ingest = scope
    session.scalar.return_value = None
    assert await recovery.process(tenant, row.id) == "skipped"
    ingest.assert_not_awaited()
    statement = session.scalar.call_args.args[0]
    assert statement._for_update_arg.skip_locked
    assert tenant in statement.compile().params.values()
    assert row.id in statement.compile().params.values()


@pytest.mark.parametrize("due", [None, "invalid", "2026-01-01", "2999-01-01T00:00:00+00:00"])
@pytest.mark.asyncio
async def test_not_due_or_invalid_timestamp_never_calls_embedder(scope, due):
    tenant, row, _session, ingest = scope
    row.metadata_[recovery.KEY]["next_attempt_at"] = due
    assert await recovery.process(tenant, row.id) == "skipped"
    ingest.assert_not_awaited()


@pytest.mark.parametrize("attempts", [-1, 99, "1"])
@pytest.mark.asyncio
async def test_invalid_attempt_state_fails_closed(scope, attempts):
    tenant, row, _session, ingest = scope
    row.metadata_[recovery.KEY]["attempts"] = attempts
    assert await recovery.process(tenant, row.id) == "failed"
    ingest.assert_not_awaited()


@pytest.mark.parametrize("payload", [None, {"spans": "not a list"}, {"spans": [{"unknown": "value"}]}])
@pytest.mark.asyncio
async def test_invalid_recovery_content_fails_closed(scope, payload):
    tenant, row, _session, ingest = scope
    row.metadata_[recovery.KEY]["content"] = payload
    assert await recovery.process(tenant, row.id) == "failed"
    assert recovery.KEY not in row.metadata_
    ingest.assert_not_awaited()


@pytest.mark.parametrize(
    "retryable,attempts,expected,error",
    [
        (True, 0, "processing", "embedding_temporarily_unavailable"),
        (True, 4, "failed", "index_retry_exhausted"),
        (False, 0, "failed", "indexing_failed"),
    ],
)
@pytest.mark.asyncio
async def test_transient_backoff_is_bounded_and_permanent_failure_is_terminal(
    scope, retryable, attempts, expected, error
):
    tenant, row, _session, ingest = scope
    row.metadata_[recovery.KEY]["attempts"] = attempts
    ingest.return_value = IngestResult("", 0, 1, 0, "text/plain", "plaintext", "model", 384, retryable=retryable)
    now = datetime.now(UTC)
    assert await recovery.process(tenant, row.id, now=now) == expected
    assert row.metadata_["ingestion_error"] == error
    if expected == "processing":
        assert row.metadata_[recovery.KEY]["attempts"] == 1
        assert datetime.fromisoformat(row.metadata_[recovery.KEY]["next_attempt_at"]) == now + timedelta(seconds=30)
    else:
        assert recovery.KEY not in row.metadata_


@pytest.mark.parametrize("old_exists", [True, False])
@pytest.mark.asyncio
async def test_replacement_success_retires_old_chunks_but_lost_source_never_resurrects(scope, monkeypatch, old_exists):
    tenant, row, session, ingest = scope
    old = str(uuid.uuid4())
    row.metadata_[recovery.KEY]["replaces"] = old
    retire = AsyncMock(return_value=old_exists)
    monkeypatch.setattr(recovery, "retire", retire)
    assert await recovery.process(tenant, row.id) == ("indexed" if old_exists else "failed")
    retire.assert_awaited_once_with(session, tenant, old)
    assert recovery.KEY not in row.metadata_
    assert ingest.call_args.kwargs["session"] is session
    assert ingest.call_args.kwargs["extracted_content"].spans[0].page == 2
    assert ingest.call_args.kwargs["metadata"] == {"domain": "finance"}
    if not old_exists:
        assert row.metadata_["ingestion_error"] == "replacement_source_changed"
        session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_removes_retry_content_and_error(scope):
    tenant, row, _session, _ingest = scope
    row.metadata_["ingestion_error"] = "embedding_temporarily_unavailable"
    assert await recovery.process(tenant, row.id) == "indexed"
    assert row.metadata_ == {"domain": "finance"}


@pytest.mark.asyncio
async def test_retire_requires_owned_row_and_removes_pending_job(scope):
    tenant, row, session, _ingest = scope
    assert await recovery.retire(session, tenant, str(row.id))
    assert row.status == "deleted" and recovery.KEY not in row.metadata_
    session.scalar.return_value = None
    assert not await recovery.retire(session, tenant, str(row.id))


@pytest.mark.asyncio
async def test_public_status_never_exposes_prepared_content(scope):
    tenant, row, session, _ingest = scope
    assert await recovery.state(tenant, row.id) == ("processing", None)
    session.scalar.return_value = None
    with pytest.raises(ValueError, match="not found"):
        await recovery.state(tenant, row.id)


@pytest.mark.asyncio
async def test_candidate_scan_is_tenant_scoped_and_bounded(scope):
    tenant, row, session, _ingest = scope
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [row.id]))
    assert await recovery.candidates(tenant, limit=999) == [row.id]
    statement = session.scalars.call_args.args[0]
    params = statement.compile().params
    assert tenant in params.values() and 10 in params.values()
    assert "next_attempt_at" in params.values()


@pytest.mark.asyncio
async def test_worker_sweep_is_disabled_without_database_access(monkeypatch):
    from core.tasks import knowledge_tasks

    monkeypatch.setattr(recovery.settings, "knowledge_upload_recovery_enabled", False)
    factory = AsyncMock()
    monkeypatch.setattr(knowledge_tasks, "async_session_factory", factory)
    assert await knowledge_tasks._sweep() == 0
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_worker_sweep_isolates_failed_document_and_routes_existing_queue(monkeypatch):
    from core.tasks import knowledge_tasks

    tenant = uuid.uuid4()
    documents = [uuid.uuid4(), uuid.uuid4()]
    session = SimpleNamespace(
        execute=AsyncMock(), scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [tenant]))
    )

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(recovery.settings, "knowledge_upload_recovery_enabled", True)
    monkeypatch.setattr(knowledge_tasks, "async_session_factory", factory)
    candidates = AsyncMock(return_value=documents)
    process = AsyncMock(side_effect=[RuntimeError("synthetic transient database failure"), "indexed"])
    monkeypatch.setattr(recovery, "candidates", candidates)
    monkeypatch.setattr(recovery, "process", process)
    assert await knowledge_tasks._sweep() == 2
    assert process.await_count == 2
    assert knowledge_tasks.app.conf.task_routes["core.tasks.knowledge_tasks.*"]["queue"] == "maintenance"
    assert knowledge_tasks.app.conf.beat_schedule["knowledge-upload-recovery"]["schedule"] == 60
