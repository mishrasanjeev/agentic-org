"""MIME-specific text extractors for the multimodal RAG ingestion service.

Each extractor takes a ``bytes`` stream + filename and returns an
``ExtractedContent`` with the full text plus per-section provenance
(page / sheet / frame timestamp). Extractors that cannot produce usable
text raise ``UnsupportedMimeType`` — the ingestion service translates
that into a 415 at the API boundary rather than fake-indexing.

Modality coverage (PR-3 initial):

- text / markdown / csv / json — naive decode
- PDF — pypdf page-by-page
- Word (DOCX) — python-docx paragraph-level
- Excel (XLSX) — openpyxl sheet + cell

Modalities gated by system-level deps (follow-up PR):

- Image OCR — requires Tesseract binary + pytesseract
- Audio transcription — requires ffmpeg + whisper
- Video extraction — requires ffmpeg

Stubs for those modalities raise ``UnsupportedMimeType`` with a clear
operator message pointing at the feature flag that needs to flip.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any


class UnsupportedMimeType(ValueError):  # noqa: N818 — external-facing API name; Error suffix would read redundantly
    """Raised by extractors + ingest service when a type isn't supported.

    Callers at the API boundary translate this into a ``415 Unsupported
    Media Type`` — distinct from a 422 validation error because the
    platform COULD accept the body, it just has no extractor wired.
    """


@dataclass
class ExtractedSpan:
    """A single chunk of extracted text with provenance."""

    text: str
    # Page number (1-indexed) for PDFs; None for other modalities.
    page: int | None = None
    # Sheet name for XLSX; None otherwise.
    sheet: str | None = None
    # Cell range for XLSX (e.g. "A1:Z42"); None otherwise.
    cell_range: str | None = None
    # Frame timestamp (seconds) for video; None otherwise.
    frame_timestamp_s: float | None = None


@dataclass
class ExtractedContent:
    """Result of extraction for a single uploaded artifact."""

    spans: list[ExtractedSpan]
    mime_type: str
    extraction_method: str  # "pypdf", "python-docx", "openpyxl", "text", ...
    total_chars: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def full_text(self, separator: str = "\n\n") -> str:
        return separator.join(s.text for s in self.spans if s.text)


# ── Pure-text extractors ─────────────────────────────────────────────


_TEXT_LIKE_MIMETYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/jsonl",
}


def _extract_plaintext(stream: bytes, mime_type: str) -> ExtractedContent:
    try:
        text = stream.decode("utf-8")
    except UnicodeDecodeError:
        text = stream.decode("latin-1", errors="replace")
    return ExtractedContent(
        spans=[ExtractedSpan(text=text.strip())],
        mime_type=mime_type,
        extraction_method="text",
        total_chars=len(text),
    )


def _extract_csv(stream: bytes, mime_type: str) -> ExtractedContent:
    """Flatten CSV rows into one chunk per row with row provenance."""
    try:
        text = stream.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = stream.decode("latin-1", errors="replace")
    reader = csv.reader(io.StringIO(text))
    spans: list[ExtractedSpan] = []
    for idx, row in enumerate(reader):
        joined = " | ".join(cell.strip() for cell in row if cell.strip())
        if not joined:
            continue
        spans.append(
            ExtractedSpan(text=joined, cell_range=f"row {idx + 1}")
        )
    return ExtractedContent(
        spans=spans,
        mime_type=mime_type,
        extraction_method="csv",
        total_chars=sum(len(s.text) for s in spans),
    )


def _extract_json(stream: bytes, mime_type: str) -> ExtractedContent:
    try:
        text = stream.decode("utf-8")
        payload = json.loads(text)
        # Serialize back with indentation so retrieval can match on
        # structured keys.
        pretty = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        return ExtractedContent(
            spans=[ExtractedSpan(text=pretty)],
            mime_type=mime_type,
            extraction_method="json",
            total_chars=len(pretty),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnsupportedMimeType(
            f"JSON body failed to decode for mime={mime_type!r}: {exc}"
        ) from exc


# ── PDF ──────────────────────────────────────────────────────────────


def _extract_pdf(stream: bytes, mime_type: str) -> ExtractedContent:
    try:
        from pypdf import PdfReader  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedMimeType(
            "PDF extraction requires pypdf; add it to pyproject dependencies"
        ) from exc

    reader = PdfReader(io.BytesIO(stream))
    spans: list[ExtractedSpan] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        if text:
            spans.append(ExtractedSpan(text=text, page=idx))
    return ExtractedContent(
        spans=spans,
        mime_type=mime_type,
        extraction_method="pypdf",
        total_chars=sum(len(s.text) for s in spans),
        extra={"page_count": len(reader.pages)},
    )


# ── Word ─────────────────────────────────────────────────────────────


def _extract_docx(stream: bytes, mime_type: str) -> ExtractedContent:
    try:
        from docx import Document  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedMimeType(
            "DOCX extraction requires python-docx; ensure it's installed"
        ) from exc

    doc = Document(io.BytesIO(stream))
    spans: list[ExtractedSpan] = []
    for idx, paragraph in enumerate(doc.paragraphs, start=1):
        text = (paragraph.text or "").strip()
        if text:
            # DOCX has no page concept until reflow. Use paragraph index
            # as provenance so retrieval can still point operators at
            # "paragraph 42".
            spans.append(ExtractedSpan(text=text, page=None, cell_range=f"para {idx}"))
    # Also pull tables — often the most information-dense part of
    # business documents.
    for t_idx, table in enumerate(getattr(doc, "tables", []), start=1):
        for r_idx, row in enumerate(table.rows, start=1):
            cells = [cell.text.strip() for cell in row.cells]
            joined = " | ".join(c for c in cells if c)
            if joined:
                spans.append(
                    ExtractedSpan(
                        text=joined,
                        cell_range=f"table {t_idx} row {r_idx}",
                    )
                )
    return ExtractedContent(
        spans=spans,
        mime_type=mime_type,
        extraction_method="python-docx",
        total_chars=sum(len(s.text) for s in spans),
    )


# ── Excel ────────────────────────────────────────────────────────────


def _extract_xlsx(stream: bytes, mime_type: str) -> ExtractedContent:
    try:
        from openpyxl import load_workbook  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedMimeType(
            "XLSX extraction requires openpyxl; ensure it's installed"
        ) from exc

    wb = load_workbook(io.BytesIO(stream), data_only=True)
    spans: list[ExtractedSpan] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        for row in ws.iter_rows(values_only=True):
            row_text = " | ".join(
                str(cell).strip()
                for cell in row
                if cell is not None and str(cell).strip()
            )
            if not row_text:
                continue
            spans.append(
                ExtractedSpan(
                    text=row_text,
                    sheet=sheet_name,
                    cell_range=None,
                )
            )
    return ExtractedContent(
        spans=spans,
        mime_type=mime_type,
        extraction_method="openpyxl",
        total_chars=sum(len(s.text) for s in spans),
        extra={"sheet_count": len(wb.sheetnames)},
    )


# ── Stubs for deps-gated modalities (follow-up PR) ───────────────────


def _extract_image_ocr(stream: bytes, mime_type: str) -> ExtractedContent:
    raise UnsupportedMimeType(
        "Image OCR requires Tesseract + pytesseract. Enable the feature "
        "flag AGENTICORG_RAG_OCR_ENABLED after the deploy image ships the "
        "system binary."
    )


def _extract_audio(stream: bytes, mime_type: str) -> ExtractedContent:
    raise UnsupportedMimeType(
        "Audio transcription requires ffmpeg + whisper. Enable the "
        "feature flag AGENTICORG_RAG_AUDIO_ENABLED after the deploy "
        "image ships both dependencies and allocates compute budget."
    )


def _extract_video(stream: bytes, mime_type: str) -> ExtractedContent:
    raise UnsupportedMimeType(
        "Video extraction requires ffmpeg + whisper. Enable the feature "
        "flag AGENTICORG_RAG_VIDEO_ENABLED after the deploy image ships "
        "the necessary binaries."
    )


# ── Dispatcher ───────────────────────────────────────────────────────


def extract(stream: bytes, mime_type: str, filename: str = "") -> ExtractedContent:
    """Route ``(stream, mime_type)`` to the correct extractor.

    Unknown MIME types fall through to the filename suffix as a hint;
    truly unrecognised bodies raise ``UnsupportedMimeType`` so the API
    boundary can 415 cleanly.
    """
    mt = (mime_type or "").lower().strip()
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if mt in _TEXT_LIKE_MIMETYPES:
        if mt == "text/csv":
            return _extract_csv(stream, mt)
        if mt in ("application/json", "application/jsonl"):
            return _extract_json(stream, mt)
        return _extract_plaintext(stream, mt)
    if mt == "application/pdf" or suffix == "pdf":
        return _extract_pdf(stream, mt or "application/pdf")
    if mt in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) or suffix == "docx":
        return _extract_docx(stream, mt or "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if mt in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) or suffix == "xlsx":
        return _extract_xlsx(stream, mt or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if mt.startswith("image/") or suffix in ("png", "jpg", "jpeg", "webp", "tiff"):
        return _extract_image_ocr(stream, mt or f"image/{suffix}")
    if mt.startswith("audio/") or suffix in ("mp3", "wav", "ogg", "m4a", "flac"):
        return _extract_audio(stream, mt or f"audio/{suffix}")
    if mt.startswith("video/") or suffix in ("mp4", "mov", "mkv", "webm"):
        return _extract_video(stream, mt or f"video/{suffix}")

    # Fall-through: try UTF-8 decode on unknown bodies. If that works and
    # produces meaningful content, index as text — otherwise refuse.
    try:
        text = stream.decode("utf-8")
        if text.strip():
            return ExtractedContent(
                spans=[ExtractedSpan(text=text)],
                mime_type=mt or "application/octet-stream",
                extraction_method="text-fallback",
                total_chars=len(text),
            )
    except UnicodeDecodeError:
        pass
    raise UnsupportedMimeType(
        f"No extractor registered for mime_type={mt!r} and filename "
        f"suffix={suffix!r}. Supported: text / PDF / DOCX / XLSX / CSV "
        "/ JSON. Image / audio / video are gated behind deploy-image "
        "feature flags."
    )
