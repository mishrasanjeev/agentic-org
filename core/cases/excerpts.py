# SPDX-License-Identifier: Apache-2.0
"""The passages behind a governed case's citations, stored encrypted and bounded.

A cited excerpt is the record a provider returned - a sanctions entry with a person's name, date
of birth, nationality and address, or a page of a company's website. It is kept so a reviewer can
read what a citation points at (PRD A-6), which means it is personal data at rest, so the passage
is encrypted with the tenant's key (``core.crypto.tenant_secrets``) exactly like every other
sensitive column in this repository. Everything a screen needs to *list* an excerpt - the
reference, the provider, the record, the digest and the fields cited - stays in clear text; it is
already in the memo.

Three rules keep the store honest:

* **Newest capture wins.** A re-investigation may return a different record under the same
  reference; the memo then cites the new digest, so the passage kept must be the new one.
* **Bounded.** A case re-investigated repeatedly (a provider webhook can trigger that) would
  otherwise grow without limit: the most recent :data:`MAX_CASE_EXCERPTS` are kept, ordered by when
  they were captured and, within one capture, by the order they arrived in.
* **Erasable.** ``forget`` drops every passage while leaving the references, so a case can be
  minimised without losing what it cited.

The digest is over the text as it was captured - the gateway truncates a very large record - so it
verifies the stored copy, not the provider's original byte stream. The reader re-hashes before
returning a passage: a passage that no longer matches its digest is refused, never shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from prometheus_client import Counter

logger = structlog.get_logger()

#: The most recent passages a case keeps. Older ones fall off; their references stay in the memo.
MAX_CASE_EXCERPTS = 200

#: Key holding the ciphertext of a passage in a stored entry.
CIPHERTEXT_KEY = "text_encrypted"

REFERENCE_KEYS = ("excerpt_ref", "provider", "record_id", "media_type", "sha256", "fields", "captured_at")

#: Order within one capture, so a batch larger than the limit keeps the passages captured last
#: rather than the ones whose reference happens to sort last.
SEQUENCE_KEY = "captured_seq"

excerpt_reads_total = Counter(
    "agenticorg_case_excerpt_reads_total",
    "Reads of a stored case excerpt, by result (served, integrity_failed, unreadable)",
    ["result"],
)


def _chain_verifications() -> Any:
    """The shared digest-verification counter (PRD §10 chain-verification alert)."""
    from observability.metrics import chain_verifications_total

    return chain_verifications_total


class ExcerptError(ValueError):
    """A passage could not be returned. ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def reference(entry: Mapping[str, Any]) -> dict[str, Any]:
    """What a list may show: everything except the passage itself."""
    return {key: entry[key] for key in REFERENCE_KEYS if key in entry}


async def tenant_key(tenant_id: uuid.UUID | str) -> str:
    """The tenant's key encryption key, resolved *before* the case's write session is opened.

    Resolving it reads the tenant row, and :func:`store` runs inside the transaction that holds
    the case row locked; opening a second session there is what ``resolve_tenant_kek`` exists to
    avoid. ``""`` means the deployment's legacy key.
    """
    from core.crypto.tenant_secrets import resolve_tenant_kek

    tenant = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    return await resolve_tenant_kek(tenant)


async def store(
    kek: str | None,
    existing: Sequence[Mapping[str, Any]] | None,
    captured: Sequence[Mapping[str, Any]],
    *,
    now: str = "",
    limit: int = MAX_CASE_EXCERPTS,
) -> list[dict[str, Any]]:
    """Merge freshly captured passages into a case's store, encrypted, newest first, bounded.

    Takes the key from :func:`tenant_key`, which the caller resolves before opening the write
    session. ``None`` means the caller resolved no key, which is refused as soon as there is a
    passage to encrypt; ``""`` is a real key - the legacy one - and encrypts normally. The two are
    kept apart deliberately: this column holds personal data under a customer-managed key, and a
    quiet fall back to the legacy key would be the wrong failure.

    It encrypts off the event loop (a customer-managed key is a gRPC call to a key
    manager). Only the passages that survive the bound are encrypted: trimming happens first, so a
    capture larger than the limit does no key work for entries it is about to drop.
    """
    from core.crypto.tenant_secrets import encrypt_with_kek

    merged: dict[str, dict[str, Any]] = {}
    for entry in existing or []:
        ref = str(entry.get("excerpt_ref") or "")
        if ref:
            merged[ref] = dict(entry)
    pending: dict[str, str] = {}
    for sequence, entry in enumerate(captured):
        ref = str(entry.get("excerpt_ref") or "")
        text = entry.get("text")
        if not ref or not isinstance(text, str) or not text:
            continue
        stored = {key: value for key, value in entry.items() if key != "text"}
        stored["sha256"] = digest(text)
        stored["captured_at"] = now or stored.get("captured_at") or ""
        stored[SEQUENCE_KEY] = sequence
        # The newest capture replaces an older passage for the same reference: the memo cites the
        # digest of the newest one.
        merged[ref] = stored
        pending[ref] = text
    # Oldest first, and within one capture the order it was captured in, so trimming drops the
    # oldest passages rather than whichever references sort first.
    ordered = sorted(
        merged.values(),
        key=lambda e: (str(e.get("captured_at") or ""), int(e.get(SEQUENCE_KEY) or 0), str(e["excerpt_ref"])),
    )
    dropped = max(0, len(ordered) - max(1, limit))
    if dropped:
        logger.info("case_excerpts_trimmed", dropped=dropped, kept=max(1, limit))
    kept = ordered[dropped:]
    for entry in kept:
        text = pending.get(str(entry["excerpt_ref"]))
        if text is None:
            continue
        if kek is None:
            # Unrepresentable rather than merely unreachable: ``encrypt_with_kek("")`` falls back
            # to the deployment's legacy key, so a caller that had not resolved one would encrypt
            # a customer-managed tenant's personal data under the wrong key and say nothing.
            raise ExcerptError("excerpt_key_unresolved", "a passage was captured but no tenant key was resolved")
        entry[CIPHERTEXT_KEY] = await asyncio.to_thread(encrypt_with_kek, text, kek)
    return kept


def read(entry: Mapping[str, Any]) -> str:
    """The passage, re-hashed before it is returned.

    Refuses (``ExcerptError``) rather than serving a passage whose digest no longer matches what
    the memo cites: the console prints that digest next to the text, so an unverified passage must
    never reach it.
    """
    from core.crypto.tenant_secrets import decrypt_for_tenant

    ciphertext = entry.get(CIPHERTEXT_KEY)
    if not isinstance(ciphertext, str) or not ciphertext:
        excerpt_reads_total.labels(result="unreadable").inc()
        raise ExcerptError("excerpt_not_held", "the case holds no passage for this reference")
    try:
        text = decrypt_for_tenant(ciphertext)
    # enterprise-gate: broad-except-ok reason=undecryptable-passage-refuses-the-read-and-is-never-served
    except Exception as exc:
        excerpt_reads_total.labels(result="unreadable").inc()
        logger.error("case_excerpt_undecryptable", error=type(exc).__name__)
        raise ExcerptError("excerpt_unreadable", "the stored passage could not be decrypted") from exc
    expected = str(entry.get("sha256") or "")
    if digest(text) != expected:
        excerpt_reads_total.labels(result="integrity_failed").inc()
        _chain_verifications().labels(chain="case_excerpt", outcome="failed").inc()
        logger.error("case_excerpt_integrity_failed", excerpt_ref=str(entry.get("excerpt_ref") or ""))
        raise ExcerptError("excerpt_integrity_failed", "the stored passage does not match its digest")
    excerpt_reads_total.labels(result="served").inc()
    _chain_verifications().labels(chain="case_excerpt", outcome="verified").inc()
    return text


def forget(existing: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Drop every passage, keeping the references so the memo's citations still resolve."""
    return [reference(entry) for entry in existing or []]
