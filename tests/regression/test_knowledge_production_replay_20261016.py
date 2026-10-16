# SPDX-License-Identifier: Apache-2.0
"""Replay short-document indexing and malformed PDF upload failures."""

from __future__ import annotations

import asyncio
import io
import zipfile
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from api.deps import get_current_tenant
from api.v1 import knowledge
from core.rag.extractors import ExtractedSpan, UnsupportedMimeType, extract
from core.rag.ingest import _chunk_spans


@pytest.mark.parametrize("remote", [True, False])
def test_multilingual_embedding_profile_matches_the_actual_serving_model(monkeypatch, remote) -> None:
    import core.ai_providers
    from core.rag.ingest import _resolve_embedding_profile

    async def configured(_tenant):
        return SimpleNamespace(
            embedding_provider="local", embedding_model="BAAI/bge-small-en-v1.5", embedding_dimensions=384
        )

    monkeypatch.setattr(core.ai_providers, "get_effective_ai_setting", configured)
    monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", "true")
    if remote:
        monkeypatch.setenv("AGENTICORG_TEI_URL", "https://embeddings.example.test")
    else:
        monkeypatch.delenv("AGENTICORG_TEI_URL", raising=False)
    expected = ("tei" if remote else "local", "BAAI/bge-m3", 1024)
    assert asyncio.run(_resolve_embedding_profile(UUID(int=101))) == expected


def test_short_extracted_document_keeps_its_text_and_page() -> None:
    span = ExtractedSpan(text="Synthetic review code 40721.", page=1)
    assert _chunk_spans([span]) == [(span.text, span)]


def test_three_short_spans_are_accumulated_without_discarding_the_first_two() -> None:
    spans = [ExtractedSpan(text=f"Synthetic section {index}.", page=index) for index in range(1, 4)]
    chunks = _chunk_spans(spans)
    assert chunks == [("\n\n".join(span.text for span in spans), spans[0])]


def test_short_tail_is_not_dropped_after_a_full_chunk() -> None:
    first = ExtractedSpan(text="A meaningful policy sentence. " * 8, page=1)
    tail = ExtractedSpan(text="Escalation code 40721.", page=2)
    chunks = _chunk_spans([first, tail])
    assert chunks[-1] == (tail.text, tail)


def test_short_prefix_stays_before_a_long_span_and_chunks_are_bounded() -> None:
    prefix = ExtractedSpan(text="Policy section.", page=1)
    body = ExtractedSpan(text="A meaningful policy sentence. " * 40, page=2)
    chunks = _chunk_spans([prefix, body], max_chars=400)
    assert chunks[0][0].startswith(prefix.text)
    assert chunks[0][1] is prefix
    assert all(len(text) <= 400 for text, _ in chunks)


@pytest.mark.parametrize("data", [b"not a valid PDF", b"%PDF-1.7\ntruncated", b""])
def test_malformed_pdf_is_a_controlled_extraction_refusal(data: bytes) -> None:
    with pytest.raises(UnsupportedMimeType, match="PDF"):
        extract(data, "application/pdf", "synthetic.pdf")


def test_a_password_protected_pdf_is_refused_without_trying_ocr() -> None:
    from pypdf import PdfReader, PdfWriter

    data = io.BytesIO()
    page = canvas.Canvas(data)
    page.drawString(40, 760, "Synthetic protected document")
    page.save()
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(io.BytesIO(data.getvalue())))
    writer.encrypt("synthetic-test-password")
    protected = io.BytesIO()
    writer.write(protected)
    with pytest.raises(UnsupportedMimeType, match="PDF"):
        extract(protected.getvalue(), "application/pdf", "protected.pdf")


def test_corrupt_pdf_replacement_returns_415_before_any_lookup_or_mutation(monkeypatch) -> None:
    async def no_database_access(*_args, **_kwargs):
        pytest.fail("A corrupt replacement must be refused before document state is read or changed")

    monkeypatch.setattr(knowledge, "_db_find_existing_by_filename", no_database_access)
    monkeypatch.setattr(knowledge, "_db_store_doc", no_database_access)
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(UUID(int=101))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/knowledge/upload?replace=true",
            files={"file": ("synthetic.pdf", b"not a valid PDF", "application/pdf")},
        )
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "document_extraction_unsupported"


@pytest.mark.parametrize("suffix", ["docx", "xlsx", "pptx"])
def test_valid_zip_with_invalid_office_contents_is_a_controlled_refusal(suffix) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<invalid")
    with pytest.raises(UnsupportedMimeType):
        extract(stream.getvalue(), "application/octet-stream", f"invalid.{suffix}")
