# SPDX-License-Identifier: Apache-2.0
"""Source excerpts, stored apart from structured fields and cited by ``excerpt_ref``.

Excerpts are short passages of attacker-controlled source text kept so a human
reviewer can see where a field came from. They are never inlined into a model
prompt; memos and evidence packages cite them by reference.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Excerpt:
    excerpt_ref: str
    source_kind: str
    field: str
    text: str


def excerpt_ref(source_kind: str, field: str, text: str) -> str:
    """Content-addressed reference: the same passage for the same field has the same ref."""
    digest = hashlib.sha256(f"{source_kind}\x1f{field}\x1f{text}".encode()).hexdigest()
    return f"exc_{digest[:32]}"


class ExcerptStore(Protocol):
    def put(self, excerpt: Excerpt) -> None: ...

    def get(self, ref: str) -> Excerpt | None: ...


class InMemoryExcerptStore:
    """Per-process store for tests and single-run tools; persistence belongs to the case store."""

    def __init__(self) -> None:
        self._items: dict[str, Excerpt] = {}

    def put(self, excerpt: Excerpt) -> None:
        self._items.setdefault(excerpt.excerpt_ref, excerpt)

    def get(self, ref: str) -> Excerpt | None:
        return self._items.get(ref)

    def __len__(self) -> int:
        return len(self._items)
