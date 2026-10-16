# SPDX-License-Identifier: Apache-2.0
"""Tenant-scoped upload recovery using existing document rows and maintenance workers."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from core.config import settings
from core.database import get_tenant_session
from core.models.document import Document
from core.rag.extractors import ExtractedContent, ExtractedSpan
from core.rag.ingest import ingest_document

RETRY_DELAYS = (30, 60, 120, 240)
MAX_PREPARED_BYTES = 4 * 1024 * 1024
MAX_SPANS = 10_000
KEY = "index_recovery"


def enabled() -> bool:
    return bool(settings.knowledge_upload_recovery_enabled)


def prepare(content: ExtractedContent, *, replaces: str | None = None) -> dict[str, Any] | None:
    """Keep bounded extracted text/provenance, never raw files or provider credentials."""
    if len(content.spans) > MAX_SPANS:
        return None
    payload = {
        "spans": [asdict(span) for span in content.spans],
        "mime_type": content.mime_type,
        "extraction_method": content.extraction_method,
        "total_chars": content.total_chars,
        "extra": content.extra,
    }
    if len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_PREPARED_BYTES:
        return None
    return {
        "content": payload,
        "attempts": 0,
        "next_attempt_at": datetime.now(UTC).isoformat(),
        "replaces": replaces,
    }


def _due(value: Any, now: datetime) -> bool:
    try:
        moment = datetime.fromisoformat(value)
        return moment.tzinfo is not None and moment <= now
    except (TypeError, ValueError):
        return False


async def retire(session: Any, tenant_id: uuid.UUID, doc_id: str) -> bool:
    """Retire metadata and native chunks together; a foreign document never matches."""
    old = await session.scalar(
        select(Document)
        .where(Document.tenant_id == tenant_id, Document.id == uuid.UUID(doc_id), Document.status != "deleted")
        .with_for_update()
    )
    if old is None:
        return False
    old.status = "deleted"
    old.metadata_ = {key: value for key, value in (old.metadata_ or {}).items() if key != KEY}
    await session.execute(
        text("UPDATE knowledge_documents SET status = 'deleted' WHERE tenant_id = :tid AND source_object_id = :id"),
        {"tid": str(tenant_id), "id": doc_id},
    )
    return True


async def process(tenant_id: uuid.UUID, document_id: uuid.UUID, *, now: datetime | None = None) -> str:
    """The row lock covers indexing, status and replacement, so duplicate jobs cannot race deletion."""
    if not enabled():
        return "disabled"
    now = now or datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        row = await session.scalar(
            select(Document)
            .where(Document.tenant_id == tenant_id, Document.id == document_id, Document.status == "processing")
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return "skipped"
        meta = dict(row.metadata_ or {})
        job = meta.get(KEY)
        if not isinstance(job, dict) or not _due(job.get("next_attempt_at"), now):
            return "skipped"
        attempts = job.get("attempts")
        if not isinstance(attempts, int) or not 0 <= attempts <= len(RETRY_DELAYS):
            row.status = "failed"
            row.metadata_ = {**meta, "ingestion_error": "invalid_index_recovery_state", KEY: None}
            return "failed"
        payload = job.get("content")
        try:
            if not isinstance(payload, dict) or len(json.dumps(payload).encode()) > MAX_PREPARED_BYTES:
                raise ValueError("invalid extracted content")
            if not isinstance(payload.get("spans"), list) or len(payload["spans"]) > MAX_SPANS:
                raise ValueError("invalid extracted spans")
            content = ExtractedContent(**{**payload, "spans": [ExtractedSpan(**s) for s in payload["spans"]]})
        except (TypeError, ValueError):
            row.status = "failed"
            meta.pop(KEY, None)
            row.metadata_ = {**meta, "ingestion_error": "invalid_index_recovery_content"}
            return "failed"
        result = await ingest_document(
            tenant_id=tenant_id,
            title=row.filename or row.name,
            stream=b"",
            mime_type=row.content_type or content.mime_type,
            filename=row.filename or row.name,
            source=f"upload://{row.filename or row.name}?revision={row.id}",
            source_object_id=str(row.id),
            source_object_type="upload",
            extracted_content=content,
            metadata={"domain": meta["domain"]} if meta.get("domain") else None,
            session=session,
        )
        if result.chunks_indexed > 0:
            replaces = job.get("replaces")
            if replaces:
                # Another replacement/deletion won; never resurrect an obsolete revision.
                if not await retire(session, tenant_id, replaces):
                    await session.execute(
                        text(
                            "UPDATE knowledge_documents SET status = 'deleted' "
                            "WHERE tenant_id = :tid AND source_object_id = :id"
                        ),
                        {"tid": str(tenant_id), "id": str(row.id)},
                    )
                    row.status = "failed"
                    meta["ingestion_error"] = "replacement_source_changed"
                else:
                    row.status = "indexed"
            else:
                row.status = "indexed"
            meta.pop(KEY, None)
            if row.status == "indexed":
                meta.pop("ingestion_error", None)
        elif result.retryable and attempts < len(RETRY_DELAYS):
            meta[KEY] = {
                **job,
                "attempts": attempts + 1,
                "next_attempt_at": (now + timedelta(seconds=RETRY_DELAYS[attempts])).isoformat(),
            }
            meta["ingestion_error"] = "embedding_temporarily_unavailable"
        else:
            row.status = "failed"
            meta.pop(KEY, None)
            meta["ingestion_error"] = "index_retry_exhausted" if result.retryable else "indexing_failed"
        row.metadata_ = meta
        return row.status


async def candidates(tenant_id: uuid.UUID, *, limit: int = 10) -> list[uuid.UUID]:
    async with get_tenant_session(tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(Document.id)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.status == "processing",
                        Document.metadata_[KEY].is_not(None),
                        Document.metadata_[KEY]["next_attempt_at"].astext <= datetime.now(UTC).isoformat(),
                    )
                    .order_by(Document.created_at, Document.id)
                    .limit(max(1, min(limit, 10)))
                )
            ).all()
        )


async def state(tenant_id: uuid.UUID, document_id: uuid.UUID) -> tuple[str, str | None]:
    async with get_tenant_session(tenant_id) as session:
        row = await session.scalar(select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id))
        if row is None:
            raise ValueError("stored document not found")
        return row.status, (row.metadata_ or {}).get("ingestion_error")
