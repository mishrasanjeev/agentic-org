"""Multimodal RAG ingestion — S0-06 closure (PR-3).

One entry point, ``ingest_document``, used by every upload surface
(knowledge base, SOP opt-in, voice transcript opt-in). Routes the
source stream through a MIME-specific extractor, chunks with
provenance, embeds via the tenant's configured model (PR-2), and
persists to ``knowledge_documents`` so native pgvector search
covers every modality — not just seeded text.
"""

from core.rag.eval import (
    EvalReport as EvalReport,
)
from core.rag.eval import (
    GoldQuery as GoldQuery,
)
from core.rag.eval import (
    QueryRun as QueryRun,
)
from core.rag.eval import (
    QUALITY_FLOOR as QUALITY_FLOOR,
)
from core.rag.eval import (
    RetrievedChunk as RetrievedChunk,
)
from core.rag.eval import (
    aggregate as aggregate,
)
from core.rag.eval import (
    gate_decision as gate_decision,
)
from core.rag.eval import (
    load_gold_corpus as load_gold_corpus,
)
from core.rag.eval import (
    score_run as score_run,
)
from core.rag.ingest import (
    UnsupportedMimeType as UnsupportedMimeType,
)
from core.rag.ingest import (
    ingest_document as ingest_document,
)
