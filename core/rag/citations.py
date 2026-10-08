# SPDX-License-Identifier: Apache-2.0
"""Citations: where a retrieved chunk came from, and the excerpt a reader opens from it.

Ingestion keeps a provenance row per chunk (``knowledge_chunk_sources``):
page, paragraph number, nearest heading, sheet and cell range. A search
joins that row to each hit, so an answer can cite "policy.pdf, page 4,
paragraph 12, under Exposure limits" rather than a document name. The
excerpt endpoint returns the whole chunk with the query's terms located in
it and the chunks either side, so the console can open the place in the
source and move through it.

A citation carries references and numbers only; the excerpt carries the
chunk's text, which the tenant's own retrieval guardrails have already
passed at search time and pass again here.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

# Every search path selects the hit's provenance through this join; ``d`` is the documents table.
PROVENANCE_JOIN = (
    " LEFT JOIN knowledge_chunk_sources s"
    " ON s.chunk_source = d.source AND s.tenant_id = d.tenant_id"
)
PROVENANCE_COLUMNS = "s.page, s.paragraph, s.heading, s.sheet, s.cell_range"
_CHUNK_INDEX = re.compile(r"#chunk(\d+)-[0-9a-f]+$")
_TOKEN = re.compile(r"[a-z0-9]+")
MAX_HIGHLIGHTS = 50


class Citation(BaseModel):
    """Where a chunk came from: the chunk row, its source and the place in the source."""

    document_id: str
    source: str | None = None
    chunk_index: int | None = None
    page: int | None = None
    paragraph: int | None = None
    heading: str | None = None
    sheet: str | None = None
    cell_range: str | None = None

    def label(self) -> str:
        """A short human form: ``page 4 · paragraph 12 · Exposure limits``."""
        parts: list[str] = []
        if self.page is not None:
            parts.append(f"page {self.page}")
        if self.sheet:
            parts.append(f"sheet {self.sheet}")
        if self.cell_range and not self.paragraph:
            parts.append(self.cell_range)
        if self.paragraph is not None:
            parts.append(f"paragraph {self.paragraph}")
        if self.heading:
            parts.append(self.heading)
        return " · ".join(parts)


def chunk_index_of(source: str | None) -> int | None:
    """The chunk number ingestion encoded in the row's source (``...#chunk12-ab12cd34ef56``)."""
    if not source:
        return None
    found = _CHUNK_INDEX.search(source)
    return int(found.group(1)) if found else None


def source_prefix(source: str | None) -> str | None:
    """The source without its chunk suffix: what every chunk of one upload shares."""
    if not source:
        return None
    return _CHUNK_INDEX.sub("", source)


def citation_from_row(
    document_id: Any,
    source: str | None,
    page: Any = None,
    paragraph: Any = None,
    heading: str | None = None,
    sheet: str | None = None,
    cell_range: str | None = None,
) -> Citation:
    return Citation(
        document_id=str(document_id),
        source=source or None,
        chunk_index=chunk_index_of(source),
        page=int(page) if page is not None else None,
        paragraph=int(paragraph) if paragraph is not None else None,
        heading=heading or None,
        sheet=sheet or None,
        cell_range=cell_range or None,
    )


def highlights(text: str, query: str) -> list[tuple[int, int]]:
    """Character spans of the query's terms in the text, in order, for the console to mark."""
    terms = {term for term in _TOKEN.findall((query or "").lower()) if len(term) >= 2}
    if not terms or not text:
        return []
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"[A-Za-z0-9]+", text):
        if match.group(0).lower() in terms:
            spans.append((match.start(), match.end()))
            if len(spans) >= MAX_HIGHLIGHTS:
                break
    return spans
