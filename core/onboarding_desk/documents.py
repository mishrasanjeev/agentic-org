# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Applicant documents: what is accepted, and the values a document suggests for the applicant to confirm.

Only PDFs, PNG and JPEG files are accepted, recognised by their first bytes
rather than the name or the declared type, up to 10 MB and 10 pages. The text
is read with the document pipeline (a PDF's text layer, OCR where the engine is
installed) off the event loop and under a time limit. Values found in it are
suggestions only: each goes through the same validation as a typed answer, and
none is saved until the applicant confirms it. Document text is never given to
a model.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.onboarding_desk import questions

logger = structlog.get_logger()

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_PER_APPLICATION = 10
READ_TIMEOUT_SECONDS = 30.0
MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}
EXTRACTION_STATUSES = ("suggested", "nothing_found", "unreadable", "ocr_unavailable")

_LEGAL_NAME = re.compile(
    r"(?im)^\s*(?:legal name|entity name|corporate name|business name|name of (?:the )?(?:corporation|company|"
    r"entity|limited liability company|llc)|dénomination(?: sociale)?|nom de la (?:société|personne morale))"
    r"\s*[:\-]?\s*(\S.{1,158}?)\s*$"
)
_NAME_IS = re.compile(r"(?i)\bthe name of the (?:corporation|company|limited liability company) is\s+([^\n.]{2,158})")
_EIN = re.compile(r"\b(\d{2}-\d{7})\b")
_BN = re.compile(r"\b(\d{9})\s?R[TC]\s?\d{4}\b")
_BN_LABELLED = re.compile(
    r"(?i)(?:business number|numéro d'entreprise|\bBN\b|\bNE\b)\s*[:#]?\s*(\d{3}\s?\d{3}\s?\d{3})\b"
)
_REGISTRATION = re.compile(
    r"(?i)\b(?:file (?:number|no\.?)|entity (?:number|no\.?|id)|document (?:number|no\.?)|corporation number|"
    r"numéro de (?:la )?société|numéro d'entreprise du québec|NEQ)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{2,19})\b"
)


class DocumentRefusedError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class Extraction:
    status: str
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "suggestions": list(self.suggestions)}


def sniff(content: bytes) -> str:
    """The file's real type from its first bytes; ``DocumentRefusedError`` when it is not one we accept."""
    if not content:
        raise DocumentRefusedError("document_empty", "the file is empty")
    if len(content) > MAX_BYTES:
        raise DocumentRefusedError(
            "document_too_large", f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB", 413
        )
    for mime, prefixes in MAGIC.items():
        if any(content.startswith(prefix) for prefix in prefixes):
            return mime
    raise DocumentRefusedError("document_type_unsupported", "upload a PDF, PNG or JPEG file", 415)


def safe_filename(name: str | None, mime: str) -> str:
    """A display name with no path, control characters or surprises, ending in the real extension."""
    base = unicodedata.normalize("NFC", (name or "").replace("\\", "/").rsplit("/", 1)[-1])
    base = "".join(ch for ch in base if not unicodedata.category(ch).startswith("C"))
    stem = re.sub(r"[^\w .()-]", "_", base.rsplit(".", 1)[0]).strip(" .")[:80] or "document"
    extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime]
    return f"{stem}.{extension}"


def _read_text(content: bytes, mime: str) -> tuple[str, str]:
    from core.idp.pages import image_pages, pdf_pages

    pages = pdf_pages(content, max_pages=MAX_PAGES) if mime == "application/pdf" else image_pages(content)
    text = "\n".join(page.text for page in pages)
    if text.strip():
        return text, "read"
    return "", "ocr_unavailable" if any(page.ocr == "unavailable" for page in pages) else "unreadable"


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def suggest(text: str, *, items: tuple[str, ...], country: str) -> list[dict[str, Any]]:
    """Values the text suggests for the given items, each already valid as an answer."""
    found: dict[str, str | None] = {
        "legal_name": _first(_LEGAL_NAME, text) or _first(_NAME_IS, text),
        "tax_id": _first(_EIN, text) if country == "US" else (_first(_BN, text) or _first(_BN_LABELLED, text)),
        "registration_number": _first(_REGISTRATION, text),
    }
    out: list[dict[str, Any]] = []
    for item_id, raw in found.items():
        if raw is None or item_id not in items:
            continue
        try:
            value = questions.validate_answer(item_id, raw, country=country)
        except questions.AnswerError:
            continue
        out.append({"item": item_id, "value": value})
    return out


async def extract(content: bytes, mime: str, *, items: tuple[str, ...], country: str) -> Extraction:
    """Read the document and suggest values for the application's items; never raises for a bad file."""
    from core.idp.pages import DocumentError

    try:
        text, status = await asyncio.wait_for(asyncio.to_thread(_read_text, content, mime), READ_TIMEOUT_SECONDS)
    except (DocumentError, TimeoutError) as exc:
        logger.info("onboarding_document_unreadable", error=type(exc).__name__)
        return Extraction("unreadable")
    if status != "read":
        return Extraction(status)
    suggestions = suggest(text, items=items, country=country)
    return Extraction("suggested" if suggestions else "nothing_found", suggestions)
