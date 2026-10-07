# SPDX-License-Identifier: Apache-2.0
"""Bundle splitting: a file of many documents becomes segments of consecutive pages, each one document.

A new segment starts when the page's type changes. A page of the same type
that repeats the type's heading starts a new segment only with stronger
evidence, because statements and agreements repeat their heading on every
page: page numbering that restarts ("Page 1 of 2") or a previous page that
ended its numbering, an identifying field (account number, statement period,
invoice number) that differs from the segment's, or a previous page whose
last lines close the document (a closing balance, a net pay, a signature
block). Numbering that continues ("Page 2 of 3") keeps the page in the
segment. Pages the rules cannot type join the segment before them (a
continuation page says little) unless they come first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.idp import fields
from core.idp.classify import UNKNOWN, Classification, classify, looks_like_last_page
from core.idp.pages import Page

_PAGE_NUMBER = re.compile(r"\bpage\s*(?:no\.?\s*)?(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b", re.I)
_TAIL_LINES = 5  # how much of a page's end is read for the closing evidence


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


def _page_number(page: Page) -> tuple[int, int] | None:
    """The page's own "page N of M" marker, when it has one."""
    match = _PAGE_NUMBER.search(page.text)
    if match is None:
        return None
    number, total = int(match.group(1)), int(match.group(2))
    return (number, total) if 0 < number <= total else None


def _starts_new_document(segment: list[Page], document_type: str, page: Page, result: Classification) -> bool:
    """Whether a page of the segment's own type opens another document, on more than a repeated heading."""
    if not result.first_page:
        return False
    numbering = _page_number(page)
    if numbering is not None:
        return numbering[0] == 1
    previous = segment[-1]
    previous_numbering = _page_number(previous)
    if previous_numbering is not None:
        return previous_numbering[0] == previous_numbering[1]
    before = fields.identity(document_type, segment)
    here = fields.identity(document_type, [page])
    if any(before[name] != here[name] for name in before.keys() & here.keys()):
        return True
    tail = "\n".join(line.text for line in previous.lines[-_TAIL_LINES:])
    return looks_like_last_page(tail, document_type)


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
        if result.document_type != current_type or _starts_new_document(
            [p for p, _ in current], current_type, page, result
        ):
            close()
            current.append((page, result))
            current_type = result.document_type
            continue
        current.append((page, result))
    close()
    return segments
