# SPDX-License-Identifier: Apache-2.0
"""Deterministic name normalisation and similarity for the mock provider."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_LEGAL_FORMS = frozenset(
    {
        "co",
        "company",
        "cooperative",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "llp",
        "ltd",
        "plc",
    }
)
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalise_name(name: str, *, business: bool) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").casefold()
    tokens = [t for t in _NON_ALNUM.split(folded) if t]
    if business:
        tokens = [t for t in tokens if t not in _LEGAL_FORMS] or tokens
    return " ".join(tokens)


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


def query_score(query: str, candidate: str) -> float:
    """How well a search term matches a business name: similarity, or 0.9 when every query word appears."""
    query_tokens = set(query.split())
    containment = 0.9 if query_tokens and query_tokens <= set(candidate.split()) else 0.0
    return max(similarity(query, candidate), containment)
