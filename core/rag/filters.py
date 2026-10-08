# SPDX-License-Identifier: Apache-2.0
"""Metadata filters for knowledge search: which documents a query may match, as SQL the search paths share.

A search may be narrowed by the fields every knowledge document carries:
``category`` (a bank's branch, product line or business segment is recorded
there at upload), ``source``, ``file_type`` and the ``created_at`` window.
The filters become ``AND`` clauses with bound parameters; nothing from the
request is placed in the SQL text, and the tenant and status clauses the
search paths already apply are untouched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_LIST = 20
MAX_TEXT = 200


class SearchFilters(BaseModel):
    """Optional narrowing of a knowledge search; every field is an AND condition."""

    model_config = {"extra": "forbid"}

    category: list[str] | None = Field(None, max_length=MAX_LIST)
    source: list[str] | None = Field(None, max_length=MAX_LIST)
    file_type: list[str] | None = Field(None, max_length=MAX_LIST)
    created_from: date | None = None
    created_to: date | None = None

    @field_validator("category", "source", "file_type")
    @classmethod
    def _clean_texts(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if any(len(value) > MAX_TEXT for value in cleaned):
            raise ValueError(f"a filter value is text of at most {MAX_TEXT} characters")
        return cleaned or None

    @field_validator("created_to")
    @classmethod
    def _window(cls, value: date | None, info: Any) -> date | None:
        start = info.data.get("created_from")
        if value is not None and start is not None and value < start:
            raise ValueError("created_to is before created_from")
        return value

    def is_empty(self) -> bool:
        return not any((self.category, self.source, self.file_type, self.created_from, self.created_to))


def sql_clauses(
    filters: SearchFilters | None, *, prefix: str = "f", alias: str = "d"
) -> tuple[str, dict[str, Any]]:
    """The ``AND`` clauses and their parameters for ``filters``; empty when there is nothing to narrow.

    Columns are qualified with ``alias``, the documents table in the search SQL.
    """
    if filters is None or filters.is_empty():
        return "", {}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for column in ("category", "source", "file_type"):
        values = getattr(filters, column)
        if values:
            names = []
            for index, value in enumerate(values):
                key = f"{prefix}_{column}_{index}"
                params[key] = value
                names.append(f":{key}")
            clauses.append(f"{alias}.{column} IN ({', '.join(names)})")
    if filters.created_from is not None:
        params[f"{prefix}_from"] = datetime.combine(filters.created_from, datetime.min.time(), tzinfo=UTC)
        clauses.append(f"{alias}.created_at >= :{prefix}_from")
    if filters.created_to is not None:
        params[f"{prefix}_to"] = datetime.combine(filters.created_to, datetime.max.time(), tzinfo=UTC)
        clauses.append(f"{alias}.created_at <= :{prefix}_to")
    return (" AND " + " AND ".join(clauses)) if clauses else "", params
