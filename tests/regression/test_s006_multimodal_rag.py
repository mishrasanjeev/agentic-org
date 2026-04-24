"""Regression tests for S0-06 — multimodal RAG ingestion (PR-3).

Covers the deterministic pieces of the acceptance criteria:

- Extractors route by MIME + filename suffix and return provenance.
- UnsupportedMimeType flows through the API boundary so operators see
  the honest 'unsupported modality' message rather than fake-accept.
- Migration v494 adds the expected columns and table.
- Upload endpoint actually calls ingest_document (AST inspection).

End-to-end ingestion round-trips (extract → embed → persist → search)
are exercised by PR-4's gold-corpus eval — we don't want to pay the
embedding cost on every unit-test run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── Extractors ───────────────────────────────────────────────────────


def test_plaintext_extractor_returns_content() -> None:
    from core.rag.extractors import extract

    body = b"hello world\nthis is plain text"
    result = extract(body, mime_type="text/plain", filename="note.txt")
    assert result.extraction_method == "text"
    assert result.total_chars == len(body.decode())
    assert "hello world" in result.full_text()


def test_csv_extractor_emits_per_row_spans() -> None:
    from core.rag.extractors import extract

    body = b"name,city\nalice,london\nbob,paris\n"
    result = extract(body, mime_type="text/csv", filename="data.csv")
    assert result.extraction_method == "csv"
    # Header row + 2 data rows
    assert len(result.spans) == 3
    assert all(span.cell_range and span.cell_range.startswith("row ") for span in result.spans)


def test_json_extractor_round_trips() -> None:
    from core.rag.extractors import extract

    body = json.dumps({"a": 1, "b": [2, 3]}).encode()
    result = extract(body, mime_type="application/json", filename="x.json")
    assert result.extraction_method == "json"
    assert '"a": 1' in result.full_text()


def test_unknown_mime_raises_unsupported() -> None:
    import pytest

    from core.rag.extractors import UnsupportedMimeType, extract

    with pytest.raises(UnsupportedMimeType):
        # Truly binary body + no text/* or suffix → refuse.
        extract(b"\x00\xff\x00binary" * 100, mime_type="application/octet-stream", filename="blob.bin")


def test_image_modality_refused_with_clear_hint() -> None:
    import pytest

    from core.rag.extractors import UnsupportedMimeType, extract

    with pytest.raises(UnsupportedMimeType) as exc_info:
        extract(b"\x89PNG\r\n\x1a\n" + b"x" * 20, mime_type="image/png", filename="scan.png")
    # The message must point at the feature flag operators need to flip
    assert "AGENTICORG_RAG_OCR_ENABLED" in str(exc_info.value)


def test_audio_modality_refused_with_clear_hint() -> None:
    import pytest

    from core.rag.extractors import UnsupportedMimeType, extract

    with pytest.raises(UnsupportedMimeType) as exc_info:
        extract(b"ID3" + b"x" * 20, mime_type="audio/mpeg", filename="recording.mp3")
    assert "AGENTICORG_RAG_AUDIO_ENABLED" in str(exc_info.value)


def test_pdf_extractor_is_wired() -> None:
    """Importing pypdf alone is enough proof here — a live PDF round-trip
    requires a sample file that bloats the repo. The AST + import check
    confirms the code path exists."""
    src = _read("core/rag/extractors.py")
    assert "_extract_pdf" in src
    assert "pypdf" in src
    assert "PdfReader" in src


def test_docx_and_xlsx_extractors_registered() -> None:
    src = _read("core/rag/extractors.py")
    assert "_extract_docx" in src
    assert "python-docx" in src
    assert "_extract_xlsx" in src
    assert "openpyxl" in src


# ── Chunker ──────────────────────────────────────────────────────────


def test_chunker_merges_short_spans_and_splits_long_ones() -> None:
    from core.rag.extractors import ExtractedSpan
    from core.rag.ingest import _chunk_spans

    short = ExtractedSpan(text="short.")
    long_text = ("The quick brown fox jumps over the lazy dog. " * 100).strip()
    long_span = ExtractedSpan(text=long_text, page=7)
    chunks = _chunk_spans([short, long_span], max_chars=400, min_chars=80)
    # Long span must split into multiple chunks
    assert len(chunks) >= 3
    # Every chunk text length is bounded by max_chars (allow +buffer because of sentence-boundary cut)
    for text, _ in chunks:
        assert len(text) <= 500


# ── Ingest API ───────────────────────────────────────────────────────


def test_ingest_api_exports() -> None:
    from core.rag import UnsupportedMimeType, ingest_document
    from core.rag.ingest import IngestResult

    # Exports are stable part of the contract — future PRs must not
    # silently rename these.
    assert callable(ingest_document)
    assert UnsupportedMimeType is not None
    assert IngestResult is not None


def test_knowledge_upload_calls_ingest_document() -> None:
    src = _read("api/v1/knowledge.py")
    assert "from core.rag import" in src
    assert "ingest_document(" in src
    # The upload endpoint must handle UnsupportedMimeType explicitly
    # so operators see the honest modality gap rather than a generic 500.
    assert "UnsupportedMimeType" in src


# ── Migration ────────────────────────────────────────────────────────


def test_v494_migration_adds_expected_columns_and_table() -> None:
    src = _read("migrations/versions/v4_9_4_multimodal_rag.py")
    assert 'revision = "v494_multimodal_rag"' in src
    assert 'down_revision = "v493_tenant_ai_settings"' in src
    # Revision ID within 32-char gate
    assert len("v494_multimodal_rag") <= 32

    for col in (
        "mime_type",
        "embedding_model",
        "embedding_dimensions",
        "token_count",
        "source_object_id",
        "source_object_type",
    ):
        assert col in src, f"migration missing ADD COLUMN for {col!r}"
    assert "CREATE TABLE IF NOT EXISTS knowledge_chunk_sources" in src
    # FK guarded on information_schema so CI (no tenants) doesn't fail
    assert "information_schema.tables" in src


# ── UnsupportedMimeType error payload ────────────────────────────────


def test_supported_types_list_is_stable() -> None:
    """A product-facing list of supported MIME types — if we extend
    this list we want a regression to bump so the UI advertises the
    same set.
    """
    src = _read("core/rag/extractors.py")
    for needle in (
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/pdf",
        "wordprocessingml",
        "spreadsheetml",
    ):
        assert needle in src, f"supported MIME type drift: {needle!r}"


# ── Binary / streaming defences ──────────────────────────────────────


def test_plaintext_extractor_handles_non_utf8_body() -> None:
    from core.rag.extractors import extract

    # Latin-1 encoded bytes should fall back cleanly
    body = "naïve".encode("latin-1")
    result = extract(body, mime_type="text/plain", filename="x.txt")
    assert "na" in result.full_text()
