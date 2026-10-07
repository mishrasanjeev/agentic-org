# SPDX-License-Identifier: Apache-2.0
"""Bundle splitting: a file of many documents becomes segments of consecutive pages, each one document.

A new segment starts when the page's type changes, or when a page of the same
type looks like a first page again (a second statement in the same bundle).
Pages the rules cannot type join the segment before them (a continuation
page says little) unless they come first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.idp.classify import UNKNOWN, Classification, classify
from core.idp.pages import Page


@dataclass
class Segment:
    index: int
    document_type: str
    pages: list[int]  # 1-indexed page numbers
    confidence: float
    classifications: list[Classification] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "document_type": self.document_type,
            "pages": list(self.pages),
            "confidence": round(self.confidence, 3),
            "page_types": [c.to_dict() for c in self.classifications],
        }


def _segment_confidence(classifications: list[Classification], document_type: str) -> float:
    typed = [c.confidence for c in classifications if c.document_type == document_type]
    if not typed:
        return 0.0
    # The typed pages decide; untyped continuation pages dilute a little.
    share = len(typed) / max(1, len(classifications))
    return round(min(0.98, (sum(typed) / len(typed)) * (0.8 + 0.2 * share)), 3)


def split(pages: list[Page]) -> list[Segment]:
    """Segments of consecutive pages, one document each."""
    segments: list[Segment] = []
    current: list[tuple[Page, Classification]] = []
    current_type: str | None = None

    def close() -> None:
        nonlocal current, current_type
        if not current:
            return
        kind = current_type or UNKNOWN
        segments.append(
            Segment(
                index=len(segments),
                document_type=kind,
                pages=[p.number for p, _ in current],
                confidence=_segment_confidence([c for _, c in current], kind),
                classifications=[c for _, c in current],
            )
        )
        current, current_type = [], None

    for page in pages:
        result = classify(page.text)
        if not current:
            current.append((page, result))
            current_type = result.document_type if result.document_type != UNKNOWN else None
            continue
        if result.document_type == UNKNOWN:
            current.append((page, result))  # a continuation page
            continue
        if current_type is None:
            # The pages before were untyped: they belong to this document, which starts the segment's type.
            current.append((page, result))
            current_type = result.document_type
            continue
        if result.document_type != current_type or result.first_page:
            close()
            current.append((page, result))
            current_type = result.document_type
            continue
        current.append((page, result))
    close()
    return segments
