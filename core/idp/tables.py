# SPDX-License-Identifier: Apache-2.0
"""Table extraction: ruled tables from a PDF's own layout where the library finds them, else columns from word gaps.

The fallback groups a page's lines into a table when several consecutive
lines share column positions (words aligned at the same x), which is how a
statement's transaction list or a slip's earnings block reads on a scanned
page. Every table keeps its page and box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from core.idp.pages import Page, union

logger = structlog.get_logger()

MIN_ROWS = 3
MIN_COLUMNS = 2
MAX_TABLES = 20
COLUMN_GAP = 18.0  # points of horizontal gap that separate columns


@dataclass
class Table:
    page: int
    bbox: tuple[float, float, float, float]
    rows: list[list[str]]
    method: str  # layout | words
    header: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "bbox": [round(v, 1) for v in self.bbox],
            "method": self.method,
            "header": list(self.header),
            "rows": [list(row) for row in self.rows],
            "row_count": len(self.rows),
            "column_count": max((len(r) for r in self.rows), default=0),
        }


def _layout_tables(raw_page: Any, number: int) -> list[Table]:
    try:
        found = raw_page.find_tables()
    # enterprise-gate: broad-except-ok reason=layout-engine-boundary-degrades-to-word-alignment-logging-the-failure
    except Exception:  # noqa: BLE001 - the layout engine is optional
        return []
    out: list[Table] = []
    for table in getattr(found, "tables", []) or []:
        try:
            rows = [[str(c or "").strip() for c in row] for row in table.extract()]
            box = tuple(float(v) for v in table.bbox)
        # enterprise-gate: broad-except-ok reason=layout-engine-boundary-degrades-to-word-alignment-logging-the-failure
        except Exception as exc:  # noqa: BLE001 - one unreadable table must not lose the page
            logger.warning("idp_table_unreadable", page=number, error_type=type(exc).__name__)
            continue
        rows = [row for row in rows if any(row)]
        if len(rows) < 2:
            continue
        out.append(Table(page=number, bbox=box, rows=rows[1:], method="layout", header=rows[0]))
    return out


def _columns_of(line: Any) -> list[tuple[float, float, str]]:
    """A line's words grouped into cells by horizontal gaps: (x0, x1, text)."""
    cells: list[list[Any]] = []
    for word in line.words:
        if cells and word.bbox[0] - cells[-1][-1].bbox[2] <= COLUMN_GAP:
            cells[-1].append(word)
        else:
            cells.append([word])
    return [(c[0].bbox[0], c[-1].bbox[2], " ".join(w.text for w in c)) for c in cells]


def _aligned(a: list[tuple[float, float, str]], b: list[tuple[float, float, str]], *, tolerance: float = 24.0) -> bool:
    if len(a) < MIN_COLUMNS or len(b) < MIN_COLUMNS or abs(len(a) - len(b)) > 1:
        return False
    starts_a = [c[0] for c in a]
    matched = sum(1 for c in b if any(abs(c[0] - s) <= tolerance for s in starts_a))
    return matched >= min(len(a), len(b)) - 1 and matched >= MIN_COLUMNS


def word_tables(page: Page) -> list[Table]:
    """Tables from runs of consecutive lines with aligned columns."""
    lines = page.lines
    tables: list[Table] = []
    run: list[tuple[Any, list[tuple[float, float, str]]]] = []

    def close() -> None:
        if len(run) >= MIN_ROWS:
            rows = [[c[2] for c in cells] for _, cells in run]
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            box = union([line.bbox for line, _ in run])
            tables.append(Table(page=page.number, bbox=box, rows=rows[1:], method="words", header=rows[0]))
        run.clear()

    for line in lines:
        cells = _columns_of(line)
        if run and _aligned(run[-1][1], cells):
            run.append((line, cells))
        else:
            close()
            if len(cells) >= MIN_COLUMNS:
                run.append((line, cells))
    close()
    return tables[:MAX_TABLES]


def extract(pages: list[Page], raw_pages: list[Any] | None = None) -> list[Table]:
    """The tables of every page: the layout engine's where it finds any, word alignment otherwise."""
    out: list[Table] = []
    for index, page in enumerate(pages):
        found: list[Table] = []
        if raw_pages is not None and index < len(raw_pages) and page.source == "text":
            found = _layout_tables(raw_pages[index], page.number)
        if not found:
            found = word_tables(page)
        out.extend(found)
        if len(out) >= MAX_TABLES:
            break
    return out[:MAX_TABLES]
