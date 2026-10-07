# SPDX-License-Identifier: Apache-2.0
"""The processing pipeline: pages, segments, documents with fields and tables, and what needs a person.

Confidence routing: a document whose type is unknown or below the type
floor, a required field that is missing or weak, or a page the engine could
not read, sends the document to review with the reasons named. The result
is what the review overlay (the next part) draws: every field and table has
its page and box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import settings
from core.idp import bundle, classify, tables
from core.idp import fields as field_extraction
from core.idp.pages import Page, load_pages

logger = structlog.get_logger()

TYPE_FLOOR = 0.6
FIELD_FLOOR = 0.7


def enabled() -> bool:
    return bool(getattr(settings, "idp_enabled", False))


@dataclass
class Document:
    index: int
    document_type: str
    confidence: float
    pages: list[int]
    fields: list[field_extraction.Field] = field(default_factory=list)
    extra_fields: list[field_extraction.Field] = field(default_factory=list)
    tables: list[tables.Table] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "document_type": self.document_type,
            "confidence": round(self.confidence, 3),
            "pages": list(self.pages),
            "fields": [f.to_dict() for f in self.fields],
            "extra_fields": [f.to_dict() for f in self.extra_fields],
            "tables": [t.to_dict() for t in self.tables],
            "review": self.review,
        }


def review_of(
    document_type: str, confidence: float, found: list[field_extraction.Field], pages: list[Page]
) -> dict[str, Any]:
    """Whether a person must look, and why."""
    reasons: list[str] = []
    if document_type == classify.UNKNOWN:
        reasons.append("document type not recognised")
    elif confidence < TYPE_FLOOR:
        reasons.append(f"document type confidence {confidence:.2f} below {TYPE_FLOOR}")
    for item in found:
        if item.required and item.status == "missing":
            reasons.append(f"required field {item.name} not found")
        elif item.required and item.confidence < FIELD_FLOOR:
            reasons.append(f"field {item.name} confidence {item.confidence:.2f} below {FIELD_FLOOR}")
    unread = [p.number for p in pages if p.source == "empty"]
    if unread:
        reasons.append(f"pages not read: {', '.join(str(n) for n in unread)}")
    return {"needed": bool(reasons), "reasons": reasons}


def process(stream: bytes, mime_type: str, *, ocr: bool = True, with_words: bool = False) -> dict[str, Any]:
    """Pages, segments and documents from one file, with the review decision per document."""
    pages = load_pages(stream, mime_type, ocr=ocr)
    segments = bundle.split(pages)
    raw_pages: list[Any] | None = None
    documents: list[Document] = []
    for segment in segments:
        own_pages = [p for p in pages if p.number in segment.pages]
        found = field_extraction.extract(segment.document_type, own_pages)
        extra = field_extraction.generic(own_pages, known={f.name for f in found})
        own_tables = tables.extract(own_pages, raw_pages)
        documents.append(
            Document(
                index=segment.index,
                document_type=segment.document_type,
                confidence=segment.confidence,
                pages=list(segment.pages),
                fields=found,
                extra_fields=extra,
                tables=own_tables,
                review=review_of(segment.document_type, segment.confidence, found, own_pages),
            )
        )
    needs_review = any(d.review["needed"] for d in documents)
    logger.info(
        "idp_processed",
        pages=len(pages),
        documents=len(documents),
        types=[d.document_type for d in documents],
        review=needs_review,
    )
    return {
        "pages": [p.to_dict(with_words=with_words) for p in pages],
        "segments": [s.to_dict() for s in segments],
        "documents": [d.to_dict() for d in documents],
        "review": {
            "needed": needs_review,
            "documents": [d.index for d in documents if d.review["needed"]],
        },
        "ocr": {
            "available": any(p.ocr == "done" for p in pages) or all(p.source == "text" for p in pages),
            "pages": [p.number for p in pages if p.ocr == "done"],
            "unavailable": [p.number for p in pages if p.ocr == "unavailable"],
        },
    }
