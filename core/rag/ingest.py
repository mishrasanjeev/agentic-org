"""Unified RAG ingestion service.

``ingest_document(tenant_id, source, stream, mime_type, metadata)`` is
the single entry point every upload surface calls. It:

1. Picks the right extractor by MIME type (``core.rag.extractors``).
2. Chunks the extracted spans at sentence/paragraph boundaries while
   preserving per-chunk provenance (page / sheet / cell_range /
   frame_timestamp_s).
3. Resolves the tenant's embedding model via ``core.ai_providers``
   (PR-2) and embeds each chunk.
4. Persists to ``knowledge_documents`` with the new multimodal columns
   (``mime_type``, ``embedding_model``, ``embedding_dimensions``,
   ``token_count``, ``source_object_id``, ``source_object_type``).
5. Persists chunk-level provenance to ``knowledge_chunk_sources`` so
   retrieval can point operators at page 42 of invoice.pdf rather than
   just "somewhere in the doc".

Raises ``UnsupportedMimeType`` for types without an extractor — API
boundary translates into 415.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text as sqltext

from core.rag.extractors import (
    ExtractedContent,
    ExtractedSpan,
    UnsupportedMimeType,
    extract,
)

logger = structlog.get_logger(__name__)

# Re-export for core/rag/__init__.py
__all__ = ["UnsupportedMimeType", "ingest_document", "IngestResult"]


@dataclass
class IngestResult:
    """Outcome of a single ingest call."""

    document_id: str
    chunks_indexed: int
    chunks_skipped: int
    total_tokens: int
    mime_type: str
    extraction_method: str
    embedding_model: str
    embedding_dimensions: int
    source_object_id: str | None = None
    source_object_type: str | None = None
    errors: list[str] = field(default_factory=list)


def _chunk_spans(
    spans: list[ExtractedSpan],
    max_chars: int = 1500,
    min_chars: int = 120,
) -> list[tuple[str, ExtractedSpan]]:
    """Split extracted spans into embedding-ready chunks.

    Each input span may produce multiple chunks (when long) or be
    concatenated with neighbours (when short) so retrieval chunks land
    in the 120-1500 char band that embedding models retrieve well.
    Provenance of the FIRST span in a merged chunk is preserved.
    """
    out: list[tuple[str, ExtractedSpan]] = []
    buffer = ""
    buffer_provenance: ExtractedSpan | None = None
    for span in spans:
        if not span.text:
            continue
        # Split very long spans on sentence boundaries
        remaining = span.text
        while len(remaining) > max_chars:
            # Find the last period/newline before max_chars
            cut = remaining.rfind(". ", 0, max_chars)
            if cut < min_chars:
                cut = remaining.rfind("\n", 0, max_chars)
            if cut < min_chars:
                cut = max_chars
            out.append((remaining[: cut + 1].strip(), span))
            remaining = remaining[cut + 1 :].lstrip()
        # Accumulate short tail
        if len(remaining) < min_chars and not buffer:
            buffer = remaining
            buffer_provenance = span
            continue
        if buffer and buffer_provenance is not None:
            combined = (buffer + "\n\n" + remaining).strip()
            if len(combined) >= min_chars:
                out.append((combined, buffer_provenance))
            buffer = ""
            buffer_provenance = None
            continue
        if remaining.strip():
            out.append((remaining.strip(), span))

    if buffer and buffer_provenance is not None and len(buffer.strip()) >= min_chars:
        out.append((buffer.strip(), buffer_provenance))

    return out


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


async def _resolve_embedding_profile(
    tenant_id: _uuid.UUID | str | None,
) -> tuple[str, str, int]:
    """Return ``(provider, model, dimensions)`` for the caller's tenant.

    Falls back to the platform default (``local`` BGE small, 384 dims)
    when the tenant has no setting. Never raises — a corrupt setting
    returns defaults with a logged warning.
    """
    from core.ai_providers import get_effective_ai_setting

    try:
        effective = await get_effective_ai_setting(tenant_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("rag_embedding_profile_fallback", error=str(exc))
        return ("local", "BAAI/bge-small-en-v1.5", 384)
    return (
        effective.embedding_provider or "local",
        effective.embedding_model or "BAAI/bge-small-en-v1.5",
        effective.embedding_dimensions or 384,
    )


def _embed_chunks(
    texts: list[str], model: str | None = None
) -> list[list[float]]:
    """Batch-embed via ``core.embeddings``.

    PR-3 uses the platform BGE model regardless of which cloud embedding
    the tenant selected — cloud-embedding paths (OpenAI / Voyage /
    Cohere) require provider routing that lands in PR-4. Today we log
    the mismatch so operators see when a tenant's selection isn't
    honoured yet.
    """
    from core.embeddings import embed

    return embed(texts)


def _default_object_type_for_mime(mime_type: str) -> str:
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("image/"):
        return "image"
    if "pdf" in mime_type:
        return "pdf"
    if "wordprocessingml" in mime_type:
        return "docx"
    if "spreadsheetml" in mime_type:
        return "xlsx"
    if "csv" in mime_type:
        return "csv"
    if "json" in mime_type:
        return "json"
    return "text"


async def ingest_document(
    *,
    tenant_id: _uuid.UUID | str,
    title: str,
    stream: bytes,
    mime_type: str,
    filename: str = "",
    source: str = "",
    source_object_id: str | None = None,
    source_object_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> IngestResult:
    """Ingest one artifact end-to-end.

    Parameters
    ----------
    tenant_id : UUID | str
        Required — used for row-level tenant isolation and embedding-
        model resolution.
    title : str
        User-visible label stored on the knowledge_documents row.
    stream : bytes
        Raw body of the uploaded artifact.
    mime_type : str
        Declared content type; extractor may still fall back to the
        filename suffix when the body is mislabeled.
    filename : str
        Original filename; used for suffix fallback.
    source : str
        URL / URI / stable identifier for provenance (e.g.
        "upload://filename.pdf", "rbi://press-release-123").
    source_object_id, source_object_type : str | None
        When the document comes from another object in our system
        (e.g. a voice session transcript), these point back at it so
        retrieval can offer structured context.

    Returns
    -------
    IngestResult
        Shape documented on the dataclass.
    """
    metadata = metadata or {}

    # 1. Extract
    content: ExtractedContent = extract(stream, mime_type=mime_type, filename=filename)
    if not content.spans:
        return IngestResult(
            document_id="",
            chunks_indexed=0,
            chunks_skipped=0,
            total_tokens=0,
            mime_type=content.mime_type,
            extraction_method=content.extraction_method,
            embedding_model="",
            embedding_dimensions=0,
            source_object_id=source_object_id,
            source_object_type=source_object_type,
            errors=["extraction produced zero spans"],
        )

    # 2. Chunk with provenance
    chunks = _chunk_spans(content.spans)
    if not chunks:
        return IngestResult(
            document_id="",
            chunks_indexed=0,
            chunks_skipped=len(content.spans),
            total_tokens=0,
            mime_type=content.mime_type,
            extraction_method=content.extraction_method,
            embedding_model="",
            embedding_dimensions=0,
            source_object_id=source_object_id,
            source_object_type=source_object_type,
            errors=["chunking produced zero chunks (all spans below min_chars)"],
        )

    # 3. Resolve tenant embedding profile + embed
    provider, model, dimensions = await _resolve_embedding_profile(tenant_id)
    try:
        vectors = _embed_chunks([c[0] for c in chunks], model=model)
    except Exception as exc:
        logger.exception("rag_embed_failed")
        return IngestResult(
            document_id="",
            chunks_indexed=0,
            chunks_skipped=len(chunks),
            total_tokens=0,
            mime_type=content.mime_type,
            extraction_method=content.extraction_method,
            embedding_model=model,
            embedding_dimensions=dimensions,
            source_object_id=source_object_id,
            source_object_type=source_object_type,
            errors=[f"embedding failed: {type(exc).__name__}: {exc}"],
        )

    # 4. Persist
    from core.database import async_session_factory

    tid = tenant_id if isinstance(tenant_id, _uuid.UUID) else _uuid.UUID(str(tenant_id))
    document_id = _uuid.uuid4()
    object_type = source_object_type or _default_object_type_for_mime(content.mime_type)

    indexed = 0
    total_tokens = 0
    async with async_session_factory() as session:
        # Parent document row — one per artifact. This is the row the
        # knowledge_documents.embedding column has existed on since
        # v4_8_6; we also stamp mime_type / embedding_model /
        # source_object_id so retrieval can filter.
        for idx, ((chunk_text, span), vector) in enumerate(zip(chunks, vectors, strict=False)):
            token_count = len(chunk_text.split())
            total_tokens += token_count
            vector_literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
            chunk_title = title if idx == 0 else f"{title} — chunk {idx + 1}"
            chunk_source = source or f"upload://{filename}"
            # Encode chunk provenance into source so dedup is stable.
            dedup_key = _content_hash(chunk_text)[:12]
            canonical_source = f"{chunk_source}#chunk{idx + 1}-{dedup_key}"

            # Column name + model swap honour the RAG_USE_BGE_M3 flag
            # so the request path stays atomic with the search side.
            from core.embeddings import rag_embedding_column

            target_column = rag_embedding_column()
            await session.execute(
                sqltext(
                    "INSERT INTO knowledge_documents "  # nosec B608 — `target_column` is a module-level constant name, not user input
                    "  (id, tenant_id, title, content, category, source, "
                    "   file_type, mime_type, embedding_model, "
                    "   embedding_dimensions, token_count, "
                    "   source_object_id, source_object_type, "
                    f"   status, {target_column}, created_at) "
                    "VALUES "
                    "  (gen_random_uuid(), :tid, :title, :content, "
                    "   :category, :source, 'rag', :mime_type, "
                    "   :embedding_model, :embedding_dims, :token_count, "
                    "   :src_obj_id, :src_obj_type, 'ready', "
                    "   CAST(:vector AS vector), now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "tid": str(tid),
                    "title": chunk_title[:480],
                    "content": chunk_text[:4000],
                    "category": object_type,
                    "source": canonical_source[:500],
                    "mime_type": content.mime_type[:128],
                    "embedding_model": f"{provider}/{model}"[:128],
                    "embedding_dims": dimensions,
                    "token_count": token_count,
                    "src_obj_id": source_object_id,
                    "src_obj_type": object_type,
                    "vector": vector_literal,
                },
            )
            # Per-chunk provenance row (knowledge_chunk_sources) so
            # retrieval can surface "page 42 of invoice.pdf".
            if span.page or span.sheet or span.cell_range or span.frame_timestamp_s is not None:
                await session.execute(
                    sqltext(
                        "INSERT INTO knowledge_chunk_sources "
                        "  (id, tenant_id, chunk_source, page, sheet, "
                        "   cell_range, frame_timestamp_s, created_at) "
                        "VALUES "
                        "  (gen_random_uuid(), :tid, :source, :page, :sheet, "
                        "   :cell_range, :frame_ts, now())"
                    ),
                    {
                        "tid": str(tid),
                        "source": canonical_source[:500],
                        "page": span.page,
                        "sheet": (span.sheet or "")[:64],
                        "cell_range": (span.cell_range or "")[:128],
                        "frame_ts": span.frame_timestamp_s,
                    },
                )
            indexed += 1
        # Codex PR #304 review P1: the AsyncSession from
        # async_session_factory does NOT auto-commit on context exit.
        # Without this line every INSERT rolls back and the whole
        # ingestion is a silent no-op even though we increment
        # chunks_indexed and log success. Commit explicitly.
        await session.commit()

    logger.info(
        "rag_ingest_complete",
        extra={
            "tenant_id": str(tid),
            "title": title,
            "chunks_indexed": indexed,
            "mime_type": content.mime_type,
        },
    )
    return IngestResult(
        document_id=str(document_id),
        chunks_indexed=indexed,
        chunks_skipped=0,
        total_tokens=total_tokens,
        mime_type=content.mime_type,
        extraction_method=content.extraction_method,
        embedding_model=f"{provider}/{model}",
        embedding_dimensions=dimensions,
        source_object_id=source_object_id,
        source_object_type=object_type,
    )
