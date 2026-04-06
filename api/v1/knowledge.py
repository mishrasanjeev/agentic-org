"""Knowledge Base API — in-memory mock store (replaceable with RAGFlow)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, UploadFile
from pydantic import BaseModel, Field

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory store (swap with RAGFlow client later)
# ---------------------------------------------------------------------------
_documents: list[dict[str, Any]] = []
_chunks: list[dict[str, Any]] = []


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/knowledge/upload", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile):
    """Accept a file upload, store metadata, return document_id + status."""
    content = await file.read()
    doc_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "document_id": doc_id,
        "filename": file.filename or "untitled",
        "content_type": file.content_type,
        "size_bytes": len(content),
        "status": "processing",
        "created_at": _now_iso(),
        "deleted": False,
        "raw_content": content,  # kept in-memory for mock search
    }
    _documents.append(doc)

    # Create mock chunks (simulate chunking)
    text = content.decode("utf-8", errors="replace")
    chunk_size = 500
    for i in range(0, max(len(text), 1), chunk_size):
        _chunks.append(
            {
                "chunk_id": str(uuid.uuid4()),
                "document_id": doc_id,
                "document_name": doc["filename"],
                "chunk_text": text[i : i + chunk_size],
            }
        )

    # Mark as ready (in real impl this would be async)
    doc["status"] = "ready"

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
):
    """List documents (paginated), excluding soft-deleted."""
    active = [d for d in _documents if not d.get("deleted")]
    total = len(active)
    start = (page - 1) * per_page
    end = start + per_page
    items = [
        DocumentOut(
            document_id=d["document_id"],
            filename=d["filename"],
            content_type=d.get("content_type"),
            size_bytes=d["size_bytes"],
            status=d["status"],
            created_at=d["created_at"],
            deleted=d.get("deleted", False),
        )
        for d in active[start:end]
    ]
    return DocumentListResponse(items=items, total=total)


@router.delete("/knowledge/documents/{doc_id}", status_code=200)
async def delete_document(doc_id: str):
    """Soft-delete a document by ID."""
    for doc in _documents:
        if doc["document_id"] == doc_id and not doc.get("deleted"):
            doc["deleted"] = True
            return {"ok": True, "document_id": doc_id, "status": "deleted"}
    return {"ok": False, "detail": "Document not found"}


@router.post("/knowledge/search", response_model=SearchResponse)
async def search_knowledge(req: SearchRequest):
    """Simple keyword search over chunks (mock — replace with vector search)."""
    query_lower = req.query.lower()
    scored: list[dict[str, Any]] = []
    for chunk in _chunks:
        # Check document is not deleted
        parent = next(
            (d for d in _documents if d["document_id"] == chunk["document_id"]),
            None,
        )
        if parent and parent.get("deleted"):
            continue
        text: str = chunk["chunk_text"]
        # Simple relevance: fraction of query words found in chunk
        words = query_lower.split()
        hits = sum(1 for w in words if w in text.lower())
        if hits > 0:
            score = round(hits / max(len(words), 1), 4)
            scored.append(
                {
                    "chunk_text": text[:300],
                    "score": score,
                    "document_name": chunk["document_name"],
                }
            )
    scored.sort(key=lambda x: x["score"], reverse=True)
    results = [SearchResult(**s) for s in scored[: req.top_k]]
    return SearchResponse(results=results)


@router.get("/knowledge/stats", response_model=StatsResponse)
async def knowledge_stats():
    """Return aggregate stats about the knowledge base."""
    active_docs = [d for d in _documents if not d.get("deleted")]
    active_doc_ids = {d["document_id"] for d in active_docs}
    active_chunks = [c for c in _chunks if c["document_id"] in active_doc_ids]
    total_bytes = sum(d["size_bytes"] for d in active_docs)
    return StatsResponse(
        total_documents=len(active_docs),
        total_chunks=len(active_chunks),
        index_size_mb=round(total_bytes / (1024 * 1024), 2),
    )
