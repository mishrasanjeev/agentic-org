"""Multimodal RAG ingestion — S0-06 closure (PR-3).

One entry point, ``ingest_document``, used by every upload surface
(knowledge base, SOP opt-in, voice transcript opt-in). Routes the
source stream through a MIME-specific extractor, chunks with
provenance, embeds via the tenant's configured model (PR-2), and
persists to ``knowledge_documents`` so native pgvector search
covers every modality — not just seeded text.
"""

from core.rag.ingest import (
    UnsupportedMimeType as UnsupportedMimeType,
)
from core.rag.ingest import (
    ingest_document as ingest_document,
)
