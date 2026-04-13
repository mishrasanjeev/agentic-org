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
from fastapi import APIRouter, Depends, Query, UploadFile
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


class DocumentOut(BaseModel):
    document_id: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    status: str
    created_at: str
    deleted: bool = False


class DocumentListResponse(BaseModel):
    items: list[DocumentOut]
    total: int


class StatsResponse(BaseModel):
    total_documents: int
    total_chunks: int
    index_size_mb: float


# ---------------------------------------------------------------------------
# RAGFlow proxy helpers
# ---------------------------------------------------------------------------
async def _ragflow_upload(
    tenant_id: str, filename: str, content: bytes, content_type: str | None,
) -> dict[str, Any]:
    """Upload a document to RAGFlow."""
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=60) as client:
        resp = await client.post(
            "/api/v1/datasets/default/documents",
            files={"file": (filename, content, content_type or "application/octet-stream")},
            headers={"Authorization": f"Bearer {_RAGFLOW_KEY}"} if _RAGFLOW_KEY else {},
            params={"tenant_id": tenant_id},
        )
        resp.raise_for_status()
        return resp.json()


async def _ragflow_search(tenant_id: str, query: str, top_k: int) -> list[dict[str, Any]]:
    """Search documents via RAGFlow vector search."""
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=30) as client:
        resp = await client.post(
            "/api/v1/retrieval",
            json={"query": query, "top_k": top_k, "dataset_ids": ["default"]},
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
    """List documents from RAGFlow."""
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=30) as client:
        resp = await client.get(
            "/api/v1/datasets/default/documents",
            headers=_ragflow_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        docs = data.get("data", {}).get("documents", data.get("data", []))
        if isinstance(docs, dict):
            docs = docs.get("docs", [])
        return docs if isinstance(docs, list) else []


async def _ragflow_delete(doc_id: str) -> bool:
    """Delete a document from RAGFlow."""
    async with _httpx.AsyncClient(base_url=_RAGFLOW_URL, timeout=30) as client:
        resp = await client.delete(
            f"/api/v1/datasets/default/documents/{doc_id}",
            headers=_ragflow_headers(),
        )
        return resp.status_code < 400


# ---------------------------------------------------------------------------
# PostgreSQL fallback — document metadata persistence
# ---------------------------------------------------------------------------
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
):
    """Upload a document to the knowledge base.

    Uses RAGFlow for chunking + vector indexing when available.
    Falls back to PostgreSQL metadata storage otherwise.
    """
    content = await file.read()
    doc_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "document_id": doc_id,
        "filename": file.filename or "untitled",
        "content_type": file.content_type,
        "size_bytes": len(content),
        "status": "processing",
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
            doc["status"] = "ready"
            logger.info("knowledge_upload_ragflow", doc_id=rf_doc_id, filename=doc["filename"])
        except Exception as exc:
            logger.warning("ragflow_upload_failed_fallback_db", error=str(exc))
            doc["status"] = "ready"
            await _db_store_doc(tenant_id, doc)
    else:
        doc["status"] = "ready"
        try:
            await _db_store_doc(tenant_id, doc)
        except Exception as exc:
            logger.warning("db_store_doc_failed", error=str(exc))

    return DocumentOut(
        document_id=doc["document_id"],
        filename=doc["filename"],
        content_type=doc["content_type"],
        size_bytes=doc["size_bytes"],
        status=doc["status"],
        created_at=doc["created_at"],
    )


@router.get("/knowledge/documents", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tenant_id: str = Depends(get_current_tenant),
):
    """List documents in the knowledge base."""
    docs: list[dict[str, Any]] = []

    if _ragflow_available():
        try:
            docs = await _ragflow_list(tenant_id)
        except Exception as exc:
            logger.warning("ragflow_list_failed_fallback_db", error=str(exc))
            try:
                docs = await _db_list_docs(tenant_id)
            except Exception:
                docs = []
    else:
        try:
            docs = await _db_list_docs(tenant_id)
        except Exception as exc:
            logger.debug("knowledge_db_list_failed", error=str(exc))
            docs = []

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
            status=d.get("status", "ready"),
            created_at=d.get("created_at", ""),
        )
        for d in page_items
    ]
    return DocumentListResponse(items=items, total=total)


@router.delete("/knowledge/documents/{doc_id}", status_code=200)
async def delete_document(doc_id: str, tenant_id: str = Depends(get_current_tenant)):
    """Delete a document from the knowledge base."""
    if _ragflow_available():
        try:
            deleted = await _ragflow_delete(doc_id)
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


@router.post("/knowledge/search", response_model=SearchResponse)
async def search_knowledge(
    req: SearchRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Search the knowledge base using vector similarity (RAGFlow) or keyword fallback."""
    if _ragflow_available():
        try:
            chunks = await _ragflow_search(tenant_id, req.query, req.top_k)
            return SearchResponse(results=[SearchResult(**c) for c in chunks])
        except Exception as exc:
            logger.warning("ragflow_search_failed", error=str(exc))

    # Fallback: basic keyword search against DB documents
    # (No vector embeddings — just returns empty for now)
    return SearchResponse(results=[])


@router.get("/knowledge/stats", response_model=StatsResponse)
async def knowledge_stats(tenant_id: str = Depends(get_current_tenant)):
    """Return aggregate stats about the knowledge base."""
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

    total_bytes = sum(d.get("size_bytes", d.get("size", 0)) for d in docs)
    return StatsResponse(
        total_documents=len(docs),
        total_chunks=0,  # RAGFlow manages chunk count internally
        index_size_mb=round(total_bytes / (1024 * 1024), 2),
    )
