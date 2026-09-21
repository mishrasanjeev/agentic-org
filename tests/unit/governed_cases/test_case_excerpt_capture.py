# SPDX-License-Identifier: Apache-2.0
"""Capturing the passage behind a citation (PRD A-6, FINDINGS A-48).

A provider cites an ``excerpt_ref`` on its evidence. Without the passage itself a reviewer can
confirm only that a record was named, not what it said. The tool gateway keeps the record each
piece of evidence is attached to, exactly as it arrived, so the memo can attach every cited
reference and the case can serve the passage.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core.cases.runtime import _merged_excerpts
from core.tool_gateway.provider_gateway import captured_excerpts

HIT = {
    "hit_id": "hit-1",
    "matched_name": "Ansel Pikworth",
    "aliases": ["A. Pikworth"],
    "evidence": [
        {
            "provider": "mock",
            "record_id": "mock:watchlist:wl-0001",
            "field": "names[0]",
            "retrieved_at": "2026-09-01T09:05:00Z",
            "excerpt_ref": "excerpt:mock-watchlist-wl-0001",
        }
    ],
}

RESPONSE = {
    "screening_id": "scr-1",
    "hits": [HIT],
    "evidence": [
        {
            "provider": "mock",
            "record_id": "mock:screening:scr-1",
            "field": "hits",
            "retrieved_at": "2026-09-01T09:05:00Z",
            "excerpt_ref": None,
        }
    ],
}


def test_every_cited_excerpt_is_captured_with_the_record_it_is_attached_to() -> None:
    captured = captured_excerpts(RESPONSE)
    assert [c.excerpt_ref for c in captured] == ["excerpt:mock-watchlist-wl-0001"]
    excerpt = captured[0]
    assert (excerpt.provider, excerpt.record_id) == ("mock", "mock:watchlist:wl-0001")
    assert excerpt.fields == ("names[0]",)
    # The passage is the record as it arrived, without the evidence wrapper, and it hashes to its digest.
    body = json.loads(excerpt.text)
    assert body["matched_name"] == "Ansel Pikworth" and "evidence" not in body
    assert excerpt.sha256 == "sha256:" + hashlib.sha256(excerpt.text.encode("utf-8")).hexdigest()


def test_evidence_without_an_excerpt_reference_captures_nothing() -> None:
    assert captured_excerpts({"evidence": [{"record_id": "r", "field": "f"}]}) == ()
    assert captured_excerpts({"hits": []}) == ()


def test_a_reference_cited_twice_is_captured_once_with_both_fields() -> None:
    twice: dict[str, Any] = {
        "pages": [
            {
                "url": "https://example.com/",
                "evidence": [
                    {"provider": "mock", "record_id": "mock:web:example.com/", "field": "content", "excerpt_ref": "e1"},
                    {"provider": "mock", "record_id": "mock:web:example.com/", "field": "title", "excerpt_ref": "e1"},
                ],
            }
        ]
    }
    captured = captured_excerpts(twice)
    assert len(captured) == 1
    assert captured[0].fields == ("content", "title")


def test_the_memo_reference_never_carries_the_passage() -> None:
    reference = captured_excerpts(RESPONSE)[0].reference()
    assert set(reference) == {"excerpt_ref", "provider", "record_id", "media_type", "sha256"}
    assert "text" not in reference


def test_a_case_keeps_one_passage_per_reference_and_the_first_capture_wins() -> None:
    merged = _merged_excerpts(
        [{"excerpt_ref": "e1", "text": "first"}],
        [{"excerpt_ref": "e1", "text": "second"}, {"excerpt_ref": "e0", "text": "other"}],
    )
    assert [entry["excerpt_ref"] for entry in merged] == ["e0", "e1"]
    assert merged[1]["text"] == "first"


def test_an_entry_without_a_reference_is_dropped_rather_than_stored_unidentified() -> None:
    assert _merged_excerpts(None, [{"text": "no reference"}]) == []
