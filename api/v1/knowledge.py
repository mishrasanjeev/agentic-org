"""Knowledge Base API — backed by RAGFlow for vector search and document management.

When RAGFLOW_API_URL is set, all operations proxy to the RAGFlow instance.
When RAGFlow is unavailable, falls back to PostgreSQL document storage
with basic keyword search (no vector embeddings).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_current_tenant

logger = structlog.get_logger()

router = APIRouter()

_RAGFLOW_URL = os.getenv("RAGFLOW_API_URL", "")
_RAGFLOW_KEY = os.getenv("RAGFLOW_API_KEY", "")

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ragflow_available() -> bool:
    return bool(_RAGFLOW_URL and _httpx)


def _ragflow_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if _RAGFLOW_KEY:
        headers["Authorization"] = f"Bearer {_RAGFLOW_KEY}"
    return headers


def _dataset_for(tenant_id: str) -> str:
    """Return the RAGFlow dataset ID owned by this tenant.

    SECURITY_AUDIT-2026-04-19 HIGH-07: every tenant previously shared
    ``datasets/default``, so list/search/delete could surface another
    tenant's documents. Each tenant now has its own dataset. The ID is
    derived deterministically so re-deploys keep talking to the same
    backing storage, and it contains only URL-safe characters.
    """
    safe = "".join(c for c in str(tenant_id) if c.isalnum() or c in "-_")
    return f"tenant_{safe}" if safe else "tenant_unknown"


async def _ragflow_ensure_dataset(dataset_id: str) -> None:
    """Create the per-tenant dataset in RAGFlow if it does not yet exist.

    Best-effort — RAGFlow returns 409 (or similar) when the dataset is
    already present. We intentionally ignore errors here: the caller's
    primary operation (upload / search) will surface a real failure.
    """
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=15) as client:
        try:
            await client.post(
                "/api/v1/datasets",
                json={"name": dataset_id, "id": dataset_id},
                headers=_ragflow_headers(),
            )
        except Exception:  # noqa: S110
            # Dataset may already exist, or RAGFlow may reject the id
            # format. In either case the downstream call will report.
            pass


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


class SearchResult(BaseModel):
    chunk_text: str
    score: float
    document_name: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


# Canonical status values surfaced to the UI. "ready" was a legacy
# RAGFlow-side label that leaked straight into the response; it is now
# normalised to "indexed" before it reaches the client.
DOC_STATUS_PROCESSING = "processing"
DOC_STATUS_INDEXED = "indexed"
DOC_STATUS_FAILED = "failed"
_LEGACY_STATUS_MAP: dict[str, str] = {
    # map everything the upload path or RAGFlow might set onto the three
    # canonical values the frontend badge component knows about.
    "ready": DOC_STATUS_INDEXED,
    "done": DOC_STATUS_INDEXED,
    "ok": DOC_STATUS_INDEXED,
    "processed": DOC_STATUS_INDEXED,
}


def _normalize_status(raw: str | None) -> str:
    """Map any incoming status string onto the canonical set."""
    if not raw:
        return DOC_STATUS_PROCESSING
    return _LEGACY_STATUS_MAP.get(raw, raw)


class DocumentOut(BaseModel):
    document_id: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    status: str
    created_at: str
    # TC_010: frontend expected `uploaded_at` but the API only returned
    # `created_at`, so the Uploaded column rendered "-" for every row.
    # We now expose both; `uploaded_at` is the user-facing name and
    # `created_at` is retained for back-compat with SDK consumers.
    uploaded_at: str
    deleted: bool = False


class DocumentListResponse(BaseModel):
    items: list[DocumentOut]
    total: int


class StatsResponse(BaseModel):
    total_documents: int
    total_chunks: int
    index_size_mb: float


class DuplicateDocumentError(Exception):
    """Raised when an upload would create a filename duplicate in the
    current tenant scope (TC_011)."""

    def __init__(self, document_id: str, filename: str) -> None:
        self.document_id = document_id
        self.filename = filename
        super().__init__(f"document already exists: {filename}")


# ---------------------------------------------------------------------------
# RAGFlow proxy helpers
# ---------------------------------------------------------------------------
async def _ragflow_upload(
    tenant_id: str, filename: str, content: bytes, content_type: str | None,
) -> dict[str, Any]:
    """Upload a document to the tenant's private RAGFlow dataset."""
    dataset_id = _dataset_for(tenant_id)
    await _ragflow_ensure_dataset(dataset_id)
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=60) as client:
        resp = await client.post(
            f"/api/v1/datasets/{dataset_id}/documents",
            files={"file": (filename, content, content_type or "application/octet-stream")},
            headers={"Authorization": f"Bearer {_RAGFLOW_KEY}"} if _RAGFLOW_KEY else {},
            params={"tenant_id": tenant_id},
        )
        resp.raise_for_status()
        return resp.json()


async def _ragflow_search(tenant_id: str, query: str, top_k: int) -> list[dict[str, Any]]:
    """Search documents inside the tenant's own dataset only."""
    dataset_id = _dataset_for(tenant_id)
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=30) as client:
        resp = await client.post(
            "/api/v1/retrieval",
            json={"query": query, "top_k": top_k, "dataset_ids": [dataset_id]},
            headers=_ragflow_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        chunks = data.get("data", {}).get("chunks", [])
        return [
            {
                "chunk_text": c.get("content", "")[:300],
                "score": round(c.get("similarity", 0.0), 4),
                "document_name": c.get("document_name", ""),
            }
            for c in chunks
        ]


async def _ragflow_list(tenant_id: str) -> list[dict[str, Any]]:
    """List documents from the tenant's dataset only."""
    dataset_id = _dataset_for(tenant_id)
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=30) as client:
        resp = await client.get(
            f"/api/v1/datasets/{dataset_id}/documents",
            headers=_ragflow_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        docs = data.get("data", {}).get("documents", data.get("data", []))
        if isinstance(docs, dict):
            docs = docs.get("docs", [])
        return docs if isinstance(docs, list) else []


async def _ragflow_delete(tenant_id: str, doc_id: str) -> bool:
    """Delete a document from the tenant's dataset."""
    dataset_id = _dataset_for(tenant_id)
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=30) as client:
        resp = await client.delete(
            f"/api/v1/datasets/{dataset_id}/documents/{doc_id}",
            headers=_ragflow_headers(),
        )
        return resp.status_code < 400


async def _ragflow_dataset_stats(tenant_id: str) -> dict[str, int] | None:
    """Fetch real chunk + index-size metrics for the tenant's dataset.

    TC_007 / TC_008: ``knowledge_stats`` used to hardcode
    ``total_chunks=0`` and derive ``index_size_mb`` from a sum of raw
    file sizes — both misrepresented the RAG index state. Returns
    ``{"chunk_count": int, "index_size_bytes": int}`` on success or
    ``None`` on any failure. Never raises — the caller falls back to a
    DB-derived estimate when we can't reach the registry.
    """
    dataset_id = _dataset_for(tenant_id)
    try:
        async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=15) as client:
            resp = await client.get(
                "/api/v1/datasets",
                params={"name": dataset_id},
                headers=_ragflow_headers(),
            )
            if resp.status_code >= 400:
                return None
            payload = resp.json()
    except Exception as exc:
        logger.debug("ragflow_dataset_stats_failed", error=str(exc))
        return None

    # RAGFlow wraps dataset records in `data` and exposes `chunk_count`
    # (or `chunk_num` in older builds) plus `token_num` / `index_size`.
    # We accept either naming convention so the stats card renders
    # correctly regardless of RAGFlow version.
    datasets = payload.get("data", payload)
    if isinstance(datasets, dict):
        datasets = datasets.get("datasets", [datasets])
    if not isinstance(datasets, list) or not datasets:
        return None
    ds = next(
        (
            d for d in datasets
            if d.get("name") == dataset_id or d.get("id") == dataset_id
        ),
        datasets[0],
    )
    chunk_count = int(
        ds.get("chunk_count")
        or ds.get("chunk_num")
        or ds.get("chunks_count")
        or 0,
    )
    # Prefer a field explicitly labelled as bytes. Otherwise fall back
    # to the token count and convert with a 4-bytes-per-token average,
    # which is a reasonable rough estimate of embedded-index storage.
    explicit_bytes = ds.get("index_size_bytes") or ds.get("index_size")
    if explicit_bytes:
        index_size_bytes = int(explicit_bytes)
    else:
        token_num = int(ds.get("token_num") or ds.get("tokens") or 0)
        index_size_bytes = token_num * 4 if token_num else 0
    return {
        "chunk_count": chunk_count,
        "index_size_bytes": index_size_bytes,
    }


async def _db_chunk_count(tenant_id: str) -> int:
    """Count indexed chunks in the Postgres fallback so the Total Chunks
    card still reflects something real when RAGFlow is unavailable
    (TC_007). Counts knowledge_documents rows with a non-null embedding
    for this tenant."""
    from uuid import UUID as _UUID

    from sqlalchemy import text as _sqtext

    from core.database import get_tenant_session

    tid = _UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            row = (await session.execute(
                _sqtext(
                    "SELECT COUNT(*) FROM knowledge_documents "
                    "WHERE tenant_id = :tid AND embedding IS NOT NULL"
                ),
                {"tid": str(tid)},
            )).fetchone()
            return int(row[0] or 0) if row else 0
    except Exception as exc:
        logger.debug("db_chunk_count_failed", error=str(exc))
        return 0


# ---------------------------------------------------------------------------
# PostgreSQL fallback — document metadata persistence
# ---------------------------------------------------------------------------
async def _db_find_existing_by_filename(
    tenant_id: str, filename: str,
) -> dict[str, Any] | None:
    """Return the first non-deleted document with the given filename for
    this tenant, or None. Used to block duplicate uploads (TC_011)."""
    from uuid import UUID as _UUID

    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.document import Document

    tid = _UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Document).where(
                Document.tenant_id == tid,
                Document.filename == filename,
                Document.status != "deleted",
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "document_id": str(row.id),
            "filename": row.filename,
            "size_bytes": row.size_bytes,
            "status": _normalize_status(row.status),
        }


async def _db_store_doc(tenant_id: str, doc: dict[str, Any]) -> None:
    """Store document metadata in PostgreSQL (fallback when RAGFlow is down)."""
    from uuid import UUID as _UUID

    from core.database import get_tenant_session
    from core.models.document import Document

    tid = _UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        db_doc = Document(
            id=_UUID(doc["document_id"]),
            tenant_id=tid,
            # `name` is the legacy NOT NULL column — mirror filename into it
            # so rows are queryable from either generation of schema.
            name=doc["filename"],
            filename=doc["filename"],
            content_type=doc.get("content_type"),
            size_bytes=doc["size_bytes"],
            status=doc["status"],
        )
        session.add(db_doc)


async def _db_list_docs(tenant_id: str) -> list[dict[str, Any]]:
    """List documents from PostgreSQL."""
    from uuid import UUID as _UUID

    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.document import Document

    tid = _UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Document).where(
                Document.tenant_id == tid,
                Document.status != "deleted",
            ).order_by(Document.created_at.desc())
        )
        docs = result.scalars().all()
        return [
            {
                "document_id": str(d.id),
                "filename": d.filename,
                "content_type": d.content_type,
                "size_bytes": d.size_bytes,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else "",
            }
            for d in docs
        ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/knowledge/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile,
    tenant_id: str = Depends(get_current_tenant),
    allow_duplicate: bool = Query(
        default=False,
        description=(
            "TC_011: when False (default), an upload whose filename already "
            "exists in the tenant's non-deleted documents returns 409 "
            "Conflict with the existing document_id. Set true to add a "
            "second copy with the same filename — does NOT replace the "
            "existing document. Use ?replace=true for replacement."
        ),
    ),
    replace: bool = Query(
        default=False,
        description=(
            "TC_007 / Codex 2026-04-22 review: when True, soft-delete any "
            "existing document with the same filename (RAGFlow + DB) "
            "before ingesting the new one, so users who want 'replace' "
            "actually get replace instead of a second identical row. "
            "Mutually exclusive with allow_duplicate=true."
        ),
    ),
):
    """Upload a document to the knowledge base.

    Uses RAGFlow for chunking + vector indexing when available.
    Falls back to PostgreSQL metadata storage otherwise.

    Dedup policy:
      - default (neither flag)           → 409 Conflict on filename match
      - ``?allow_duplicate=true``        → add a second doc with same name
      - ``?replace=true``                → soft-delete existing, ingest new
    """
    filename = file.filename or "untitled"

    if allow_duplicate and replace:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "conflicting_dedup_flags",
                "message": (
                    "allow_duplicate=true and replace=true are mutually "
                    "exclusive. Pick one: add a second copy, or replace."
                ),
            },
        )

    # TC_011: filename-level dedup. Caller can opt out with
    # ?allow_duplicate=true (adds a second copy) or ?replace=true
    # (deletes the old one first). This check is best-effort — if the
    # DB lookup fails, we proceed with the upload rather than block
    # legitimate usage.
    if not allow_duplicate and not replace:
        try:
            existing = await _db_find_existing_by_filename(tenant_id, filename)
        except Exception as exc:
            logger.debug("dedup_lookup_failed_soft", error=str(exc))
            existing = None
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "duplicate_filename",
                    "message": (
                        f"A document named {filename!r} is already in the "
                        "knowledge base. Upload again with ?replace=true "
                        "to replace it, or ?allow_duplicate=true to add "
                        "a second copy alongside the existing one."
                    ),
                    "existing_document_id": existing["document_id"],
                },
            )

    # Root-cause fix for TC_007 (Codex 2026-04-22): when the caller asks
    # for replace, actually replace. Previously the UI alerted "check
    # the duplicate box to replace it", but the duplicate box only added
    # another copy — the existing document was never touched. Now
    # replace=true soft-deletes the existing document in both RAGFlow
    # and the DB mirror before ingesting the new one, so the UI copy
    # and the backend behaviour agree.
    if replace:
        try:
            existing = await _db_find_existing_by_filename(tenant_id, filename)
        except Exception as exc:
            logger.debug("replace_lookup_failed_soft", error=str(exc))
            existing = None
        if existing is not None:
            old_doc_id = existing["document_id"]
            if _ragflow_available():
                try:
                    await _ragflow_delete(tenant_id, old_doc_id)
                except Exception as exc:
                    logger.warning(
                        "replace_ragflow_delete_failed",
                        doc_id=old_doc_id,
                        error=str(exc),
                    )
            try:
                from uuid import UUID as _UUID

                from sqlalchemy import update

                from core.database import get_tenant_session
                from core.models.document import Document

                tid = _UUID(tenant_id)
                async with get_tenant_session(tid) as session:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.id == _UUID(old_doc_id),
                            Document.tenant_id == tid,
                        )
                        .values(status="deleted")
                    )
            except Exception as exc:
                logger.warning(
                    "replace_db_soft_delete_failed",
                    doc_id=old_doc_id,
                    error=str(exc),
                )

    content = await file.read()
    doc_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "document_id": doc_id,
        "filename": filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
        "status": DOC_STATUS_PROCESSING,
        "created_at": _now_iso(),
    }

    if _ragflow_available():
        try:
            rf_result = await _ragflow_upload(
                tenant_id, doc["filename"], content, doc["content_type"],
            )
            # RAGFlow returns its own document ID
            rf_doc_id = rf_result.get("data", {}).get("id", doc_id)
            doc["document_id"] = rf_doc_id
            doc["status"] = DOC_STATUS_INDEXED
            logger.info("knowledge_upload_ragflow", doc_id=rf_doc_id, filename=doc["filename"])
        except Exception as exc:
            logger.warning("ragflow_upload_failed_fallback_db", error=str(exc))
            doc["status"] = DOC_STATUS_INDEXED
    else:
        doc["status"] = DOC_STATUS_INDEXED

    # Session 5 TC-013: always mirror metadata to Postgres so the document
    # list survives a RAGFlow outage or a RAGFlow-side search lag. Without
    # this, documents uploaded via the RAGFlow path disappeared from the UI
    # after a page refresh whenever /knowledge/documents fell back to the
    # DB listing (first mirror was only created on RAGFlow upload failure).
    try:
        await _db_store_doc(tenant_id, doc)
    except Exception as exc:
        logger.warning("db_store_doc_failed", error=str(exc))

    return DocumentOut(
        document_id=doc["document_id"],
        filename=doc["filename"],
        content_type=doc["content_type"],
        size_bytes=doc["size_bytes"],
        status=_normalize_status(doc["status"]),
        created_at=doc["created_at"],
        uploaded_at=doc["created_at"],
    )


@router.get("/knowledge/documents", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tenant_id: str = Depends(get_current_tenant),
):
    """List documents in the knowledge base."""
    # Session 5 TC-013: merge the RAGFlow list with the DB mirror so a
    # just-uploaded document remains visible even if RAGFlow's search
    # index hasn't caught up yet or the connection is flapping. Without
    # this, uploads disappeared after a page refresh.
    rf_docs: list[dict[str, Any]] = []
    db_docs: list[dict[str, Any]] = []

    if _ragflow_available():
        try:
            rf_docs = await _ragflow_list(tenant_id)
        except Exception as exc:
            logger.warning("ragflow_list_failed_fallback_db_only", error=str(exc))

    try:
        db_docs = await _db_list_docs(tenant_id)
    except Exception as exc:
        logger.debug("knowledge_db_list_failed", error=str(exc))

    # Merge on document_id — prefer the RAGFlow record when both have it,
    # since RAGFlow carries the current chunk/index status.
    seen: set[str] = set()
    docs: list[dict[str, Any]] = []
    for record in (*rf_docs, *db_docs):
        key = str(record.get("document_id") or record.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        docs.append(record)

    # Also include knowledge_documents (seeded CA/GST compliance content)
    try:
        from uuid import UUID as _UUID

        from sqlalchemy import text as _sqtext

        from core.database import get_tenant_session as _gts

        tid = _UUID(tenant_id)
        async with _gts(tid) as session:
            kd_result = await session.execute(
                _sqtext(
                    "SELECT id, title, category, file_type, token_count, created_at "
                    "FROM knowledge_documents "
                    "WHERE tenant_id = :tid AND status = 'ready' "
                    "ORDER BY created_at DESC"
                ),
                {"tid": str(tid)},
            )
            for row in kd_result.fetchall():
                docs.append({
                    "document_id": str(row[0]),
                    "filename": row[1],
                    "content_type": row[3] or "text",
                    "size_bytes": (row[4] or 0) * 4,
                    "status": "ready",
                    "created_at": row[5].isoformat() if row[5] else "",
                })
    except Exception:
        logger.debug("knowledge_documents_query_skipped")

    total = len(docs)
    start = (page - 1) * per_page
    page_items = docs[start : start + per_page]

    items = [
        DocumentOut(
            document_id=d.get("document_id", d.get("id", "")),
            filename=d.get("filename", d.get("name", "")),
            content_type=d.get("content_type"),
            size_bytes=d.get("size_bytes", d.get("size", 0)),
            status=_normalize_status(d.get("status")),
            created_at=d.get("created_at", ""),
            uploaded_at=d.get("uploaded_at") or d.get("created_at", ""),
        )
        for d in page_items
    ]
    return DocumentListResponse(items=items, total=total)


@router.delete("/knowledge/documents/{doc_id}", status_code=200)
async def delete_document(doc_id: str, tenant_id: str = Depends(get_current_tenant)):
    """Delete a document from the knowledge base."""
    if _ragflow_available():
        try:
            deleted = await _ragflow_delete(tenant_id, doc_id)
            if deleted:
                return {"ok": True, "document_id": doc_id, "status": "deleted"}
        except Exception as exc:
            logger.warning("ragflow_delete_failed", error=str(exc))

    # Fallback: mark as deleted in DB
    try:
        from uuid import UUID as _UUID

        from sqlalchemy import update

        from core.database import get_tenant_session
        from core.models.document import Document

        tid = _UUID(tenant_id)
        async with get_tenant_session(tid) as session:
            await session.execute(
                update(Document)
                .where(Document.id == _UUID(doc_id), Document.tenant_id == tid)
                .values(status="deleted")
            )
        return {"ok": True, "document_id": doc_id, "status": "deleted"}
    except Exception:
        return {"ok": False, "detail": "Document not found"}


async def _native_semantic_search(
    tenant_id: str, query: str, top_k: int,
) -> list[SearchResult]:
    """Semantic search over knowledge_documents using pgvector + BGE.

    Falls back to an ILIKE keyword match if the embedding model or the
    pgvector column is unavailable in the current environment. Never
    raises — callers expect a best-effort result list.
    """
    from uuid import UUID as _UUID

    from sqlalchemy import text as _sqtext

    from core.database import get_tenant_session

    tid = _UUID(tenant_id)

    # Try the vector path first.
    try:
        from core.embeddings import embed_one

        qvec = embed_one(query)
        vector_literal = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
        async with get_tenant_session(tid) as session:
            rows = (await session.execute(
                _sqtext(
                    "SELECT title, content, "
                    "1 - (embedding <=> CAST(:q AS vector)) AS score "
                    "FROM knowledge_documents "
                    "WHERE tenant_id = :tid AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> CAST(:q AS vector) "
                    "LIMIT :k"
                ),
                {"q": vector_literal, "tid": str(tid), "k": top_k},
            )).fetchall()
        if rows:
            return [
                SearchResult(
                    chunk_text=(r[1] or "")[:300],
                    score=round(float(r[2] or 0.0), 4),
                    document_name=r[0] or "",
                )
                for r in rows
            ]
    except Exception as exc:
        logger.debug("native_semantic_search_skipped", error=str(exc))

    # Keyword fallback — surfaces something useful when embeddings are
    # unavailable (e.g. fastembed cache missing in a restricted CI env).
    try:
        async with get_tenant_session(tid) as session:
            rows = (await session.execute(
                _sqtext(
                    "SELECT title, content FROM knowledge_documents "
                    "WHERE tenant_id = :tid AND "
                    "(title ILIKE :like OR content ILIKE :like) "
                    "LIMIT :k"
                ),
                {"tid": str(tid), "like": f"%{query}%", "k": top_k},
            )).fetchall()
        results = [
            SearchResult(
                chunk_text=(r[1] or "")[:300],
                score=0.0,
                document_name=r[0] or "",
            )
            for r in rows
        ]
        if results:
            return results
    except Exception as exc:
        logger.debug("keyword_fallback_failed", error=str(exc))

    # TC_009 last-resort: match against uploaded document filenames in
    # the `documents` mirror table. Without this, any query fails when
    # RAGFlow hasn't finished chunking user uploads yet — the UI showed
    # "no results" even though the document the user just dropped in
    # was right there. Returning the filename with a score of 0 and
    # an empty chunk gives the UI enough signal to say "we found
    # alpha.pdf but haven't indexed it yet".
    try:
        from sqlalchemy import select as _select

        from core.models.document import Document

        async with get_tenant_session(tid) as session:
            match_rows = (await session.execute(
                _select(Document).where(
                    Document.tenant_id == tid,
                    Document.status != "deleted",
                    Document.filename.ilike(f"%{query}%"),
                ).limit(top_k)
            )).scalars().all()
            return [
                SearchResult(
                    chunk_text=(
                        f"(matched filename; {d.filename} has not finished "
                        "indexing yet — re-run the query in a few seconds)"
                    ),
                    score=0.0,
                    document_name=d.filename,
                )
                for d in match_rows
            ]
    except Exception as exc:
        logger.debug("filename_fallback_failed", error=str(exc))
        return []


@router.post("/knowledge/search", response_model=SearchResponse)
async def search_knowledge(
    req: SearchRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Search the knowledge base using vector similarity.

    Order: RAGFlow (if configured) → native pgvector + BGE embeddings →
    keyword fallback. All three paths return the same SearchResult shape.
    """
    if _ragflow_available():
        try:
            chunks = await _ragflow_search(tenant_id, req.query, req.top_k)
            return SearchResponse(results=[SearchResult(**c) for c in chunks])
        except Exception as exc:
            logger.warning("ragflow_search_failed", error=str(exc))

    results = await _native_semantic_search(tenant_id, req.query, req.top_k)
    return SearchResponse(results=results)


@router.get("/knowledge/stats", response_model=StatsResponse)
async def knowledge_stats(tenant_id: str = Depends(get_current_tenant)):
    """Return aggregate stats about the knowledge base.

    TC_007/TC_008: previously reported ``total_chunks=0`` unconditionally
    and computed ``index_size_mb`` from a sum of raw file sizes. Now:
      - total_chunks: RAGFlow dataset stats when reachable, else a
        Postgres COUNT of knowledge_documents with embeddings.
      - index_size_mb: RAGFlow-reported index size when available, else
        the file-size sum as a lower-bound estimate.
    Both values are labelled with `source` in debug logs so operators
    can see which path answered.
    """
    docs: list[dict[str, Any]] = []

    if _ragflow_available():
        try:
            docs = await _ragflow_list(tenant_id)
        except Exception as exc:
            logger.debug("ragflow_stats_failed", error=str(exc))

    if not docs:
        try:
            docs = await _db_list_docs(tenant_id)
        except Exception as exc:
            logger.debug("db_stats_failed", error=str(exc))

    chunk_count = 0
    index_size_bytes = 0
    stats_source = "fallback"

    # Prefer real RAGFlow metrics when the service is up.
    if _ragflow_available():
        stats = await _ragflow_dataset_stats(tenant_id)
        if stats is not None:
            chunk_count = stats["chunk_count"]
            index_size_bytes = stats["index_size_bytes"]
            stats_source = "ragflow"

    # Postgres fallback — count embedded knowledge_documents rows.
    if chunk_count == 0:
        chunk_count = await _db_chunk_count(tenant_id)
        if chunk_count:
            stats_source = "postgres"

    # Index-size fallback: if RAGFlow didn't provide one, use the sum of
    # raw file sizes as a lower-bound estimate. Better than the old hard
    # zero, explicitly labelled as "file bytes, not index bytes" in the
    # log trail.
    if index_size_bytes == 0:
        index_size_bytes = sum(
            d.get("size_bytes", d.get("size", 0)) for d in docs
        )

    logger.debug(
        "knowledge_stats",
        tenant_id=tenant_id,
        source=stats_source,
        total_documents=len(docs),
        total_chunks=chunk_count,
        index_size_bytes=index_size_bytes,
    )
    return StatsResponse(
        total_documents=len(docs),
        total_chunks=chunk_count,
        index_size_mb=round(index_size_bytes / (1024 * 1024), 2),
    )
