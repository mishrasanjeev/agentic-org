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
import uuid
from typing import Any

import pytest

from core.cases import excerpts as case_excerpts
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


TENANT = uuid.UUID("0c9f2a5e-0000-4000-8000-0000000000a8")


@pytest.fixture(autouse=True)
def _tenant_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypt with the legacy tenant key: these tests are about the store, not the key manager."""
    from core.crypto import tenant_secrets

    async def no_byok(_tenant_id: uuid.UUID) -> str:
        return ""

    monkeypatch.setattr(tenant_secrets, "_resolve_kek", no_byok)


async def test_a_stored_passage_is_encrypted_and_only_its_reference_is_readable() -> None:
    """A passage is a provider record about a person, so it is not left in clear text."""
    stored = await case_excerpts.store(TENANT, [], [{"excerpt_ref": "e1", "provider": "mock", "text": "Ansel"}])
    entry = stored[0]
    assert "text" not in entry
    assert entry[case_excerpts.CIPHERTEXT_KEY] and "Ansel" not in entry[case_excerpts.CIPHERTEXT_KEY]
    assert set(case_excerpts.reference(entry)) <= set(case_excerpts.REFERENCE_KEYS)
    assert case_excerpts.CIPHERTEXT_KEY not in case_excerpts.reference(entry)
    assert case_excerpts.read(entry) == "Ansel"


async def test_the_newest_capture_of_a_reference_wins() -> None:
    """The memo cites the newest digest, so the passage kept has to be the newest one."""
    first = await case_excerpts.store(TENANT, [], [{"excerpt_ref": "e1", "text": "first"}], now="2026-09-01T09:00:00Z")
    second = await case_excerpts.store(
        TENANT, first, [{"excerpt_ref": "e1", "text": "second"}], now="2026-09-02T09:00:00Z"
    )
    assert len(second) == 1
    assert case_excerpts.read(second[0]) == "second"
    assert second[0]["sha256"] == case_excerpts.digest("second")


async def test_a_case_keeps_only_the_most_recent_passages() -> None:
    """A case re-investigated over and over must not grow without limit."""
    older = await case_excerpts.store(
        TENANT, [], [{"excerpt_ref": "e_old", "text": "older"}], now="2026-09-01T09:00:00Z", limit=4
    )
    captured = [{"excerpt_ref": f"e{n}", "text": f"passage {n}"} for n in range(6)]
    stored = await case_excerpts.store(TENANT, older, captured, now="2026-09-02T09:00:00Z", limit=4)

    assert len(stored) == 4
    # The oldest capture goes first, and within one capture the ones that arrived first - not
    # whichever references happen to sort first.
    assert [entry["excerpt_ref"] for entry in stored] == ["e2", "e3", "e4", "e5"]


async def test_an_entry_without_a_reference_or_a_passage_is_not_stored() -> None:
    assert await case_excerpts.store(TENANT, None, [{"text": "no reference"}]) == []
    assert await case_excerpts.store(TENANT, None, [{"excerpt_ref": "e1"}]) == []


async def test_a_passage_that_no_longer_matches_its_digest_is_refused() -> None:
    """The console prints the digest beside the passage: an unverified passage must not reach it."""
    stored = await case_excerpts.store(TENANT, [], [{"excerpt_ref": "e1", "text": "as returned"}])
    tampered = {**stored[0], "sha256": case_excerpts.digest("something else")}
    with pytest.raises(case_excerpts.ExcerptError) as refused:
        case_excerpts.read(tampered)
    assert refused.value.reason == "excerpt_integrity_failed"


def test_a_reference_without_a_passage_reads_as_not_held() -> None:
    with pytest.raises(case_excerpts.ExcerptError) as refused:
        case_excerpts.read({"excerpt_ref": "e1", "sha256": case_excerpts.digest("x")})
    assert refused.value.reason == "excerpt_not_held"


def test_an_undecryptable_passage_is_refused_rather_than_shown() -> None:
    entry = {"excerpt_ref": "e1", "sha256": case_excerpts.digest("x"), case_excerpts.CIPHERTEXT_KEY: "not-ciphertext"}
    with pytest.raises(case_excerpts.ExcerptError) as refused:
        case_excerpts.read(entry)
    assert refused.value.reason in {"excerpt_unreadable", "excerpt_integrity_failed"}


async def test_forgetting_keeps_the_references_the_memo_cites() -> None:
    stored = await case_excerpts.store(TENANT, [], [{"excerpt_ref": "e1", "provider": "mock", "text": "Ansel"}])
    forgotten = case_excerpts.forget(stored)
    assert [entry["excerpt_ref"] for entry in forgotten] == ["e1"]
    assert all(case_excerpts.CIPHERTEXT_KEY not in entry for entry in forgotten)
    with pytest.raises(case_excerpts.ExcerptError):
        case_excerpts.read(forgotten[0])
