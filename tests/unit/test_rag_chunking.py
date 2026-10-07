# SPDX-License-Identifier: Apache-2.0
"""Layout-preserving extraction and the chunking strategies."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.rag import chunking, ingest
from core.rag.chunking import ChunkPlan
from core.rag.extractors import ExtractedSpan, looks_like_heading, paragraphs_of

ROOT = Path(__file__).resolve().parents[2]
LONG = ("The policy covers water damage from burst pipes. " * 12).strip()


def _span(text, kind="paragraph", heading=None, page=1, paragraph=None):
    return ExtractedSpan(text=text, page=page, kind=kind, heading=heading, paragraph=paragraph)


class TestLayout:
    def test_headings_are_recognised_by_number_capitals_or_title_case(self):
        assert looks_like_heading("1. Eligibility")
        assert looks_like_heading("2.3 Exposure limits")
        assert looks_like_heading("CLAIMS PROCEDURE")
        assert looks_like_heading("Exposure Limits")
        assert not looks_like_heading("The applicant must be at least eighteen years old.")
        assert not looks_like_heading("Note:")
        assert not looks_like_heading("x" * 100)

    def test_page_text_becomes_numbered_paragraphs_under_their_heading(self):
        text = (
            "1. Eligibility\n\nThe applicant must be resident.\n\nIncome is verified.\n\n"
            "2. Limits\n\nExposure is capped."
        )
        spans = paragraphs_of(text, page=3, start=5)
        assert [(s.kind, s.paragraph, s.page) for s in spans] == [
            ("heading", 5, 3),
            ("paragraph", 6, 3),
            ("paragraph", 7, 3),
            ("heading", 8, 3),
            ("paragraph", 9, 3),
        ]
        assert [s.heading for s in spans] == ["1. Eligibility"] * 3 + ["2. Limits"] * 2
        assert paragraphs_of("   ") == []

    def test_a_docx_keeps_heading_styles_and_table_rows_under_their_section(self):
        from core.rag import extractors

        class _P:
            def __init__(self, text, style):
                self.text = text
                self.style = SimpleNamespace(name=style)

        class _Cell:
            def __init__(self, text):
                self.text = text

        class _Row:
            def __init__(self, *cells):
                self.cells = [_Cell(c) for c in cells]

        class _Doc:
            paragraphs = [
                _P("Credit policy", "Title"),
                _P("The applicant must be resident.", "Normal"),
                _P("Exposure limits", "Heading 2"),
                _P("Exposure is capped at the policy limit.", "Normal"),
            ]
            tables = [SimpleNamespace(rows=[_Row("Product", "Limit"), _Row("Personal loan", "5,00,000")])]

        import docx as docx_module

        original = docx_module.Document
        docx_module.Document = lambda _stream: _Doc()  # type: ignore[assignment]
        extractors._validate_zip_container = lambda _stream: None  # type: ignore[assignment]
        try:
            content = extractors._extract_docx(
                b"zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
        finally:
            docx_module.Document = original  # type: ignore[assignment]
        kinds = [(s.kind, s.paragraph, s.heading) for s in content.spans]
        assert kinds[:4] == [
            ("heading", 1, "Credit policy"),
            ("paragraph", 2, "Credit policy"),
            ("heading", 3, "Exposure limits"),
            ("paragraph", 4, "Exposure limits"),
        ]
        assert kinds[4] == ("table_row", None, "Exposure limits") and content.spans[4].cell_range == "table 1 row 1"

    def test_the_default_span_is_a_paragraph_without_layout(self):
        span = ExtractedSpan(text="x")
        assert (span.kind, span.paragraph, span.heading) == ("paragraph", None, None)


class TestPlan:
    def test_the_default_plan_is_the_original_behaviour(self):
        assert ChunkPlan() == ChunkPlan(strategy="sentence", max_chars=1500)
        assert ChunkPlan.from_settings(None, None) == ChunkPlan()
        assert ChunkPlan.from_settings("nonsense", 512) == ChunkPlan(strategy="sentence", max_chars=2048)
        assert ChunkPlan.from_settings("heading", 32) == ChunkPlan(strategy="heading", max_chars=400)
        assert ChunkPlan.from_settings("paragraph", 8192) == ChunkPlan(strategy="paragraph", max_chars=6000)
        assert ChunkPlan.from_settings("paragraph", True).max_chars == 1500

    def test_strategies_are_named(self):
        assert chunking.validate_strategy("heading") == "heading"
        with pytest.raises(ValueError, match="chunk_strategy must be one of"):
            chunking.validate_strategy("semantic")


SECTION = [
    _span("1. Eligibility", kind="heading", heading="1. Eligibility", paragraph=1),
    _span("The applicant must be resident.", heading="1. Eligibility", paragraph=2),
    _span("Income is verified against statements.", heading="1. Eligibility", paragraph=3),
    _span("2. Limits", kind="heading", heading="2. Limits", paragraph=4),
    _span(LONG, heading="2. Limits", paragraph=5),
    _span("Exceptions go to the committee.", heading="2. Limits", paragraph=6),
]


class TestStrategies:
    def test_sentence_is_the_original_chunker(self):
        spans = [ExtractedSpan(text="short."), ExtractedSpan(text=LONG, page=7)]
        assert chunking.chunk(spans) == ingest._chunk_spans(spans, max_chars=1500, min_chars=120)
        assert chunking.chunk(spans, ChunkPlan(strategy="sentence", max_chars=400)) == ingest._chunk_spans(
            spans, max_chars=400, min_chars=120
        )

    def test_paragraph_chunks_never_cross_a_heading_and_keep_the_first_provenance(self):
        chunks = chunking.chunk(SECTION, ChunkPlan(strategy="paragraph", max_chars=400))
        texts = [text for text, _ in chunks]
        # Short paragraphs under one heading merge; the heading itself is not a chunk; a long one is cut.
        assert texts[0] == "The applicant must be resident.\n\nIncome is verified against statements."
        assert chunks[0][1].paragraph == 2 and chunks[0][1].heading == "1. Eligibility"
        assert all("Eligibility" not in text or "Limits" not in text for text in texts)
        assert all(len(text) <= 400 for text in texts)
        assert texts[-1] == "Exceptions go to the committee."
        assert chunks[-1][1].paragraph == 6
        assert all(span.heading == "2. Limits" for _text, span in chunks[1:])

    def test_heading_chunks_start_with_their_section_title(self):
        chunks = chunking.chunk(SECTION, ChunkPlan(strategy="heading", max_chars=400))
        assert chunks[0][0].startswith("1. Eligibility\n\n") and "Income is verified" in chunks[0][0]
        assert all(text.startswith(("1. Eligibility\n\n", "2. Limits\n\n")) for text, _ in chunks)
        assert all(len(text) <= 400 for text, _ in chunks)
        assert chunks[0][1].paragraph == 2 and chunks[1][1].heading == "2. Limits"
        # A long section is cut at sentence boundaries into several titled chunks.
        assert sum(1 for text, _ in chunks if text.startswith("2. Limits")) >= 2

    def test_spans_without_layout_chunk_under_no_heading(self):
        plain = [ExtractedSpan(text="One short paragraph."), ExtractedSpan(text="Another short one.")]
        assert chunking.chunk(plain, ChunkPlan(strategy="heading", max_chars=400)) == [
            ("One short paragraph.\n\nAnother short one.", plain[0])
        ]
        assert chunking.chunk(plain, ChunkPlan(strategy="paragraph", max_chars=400))[0][1] is plain[0]

    def test_a_lone_long_heading_is_kept(self):
        heading = _span("A" * 130, kind="heading", heading="A" * 130, paragraph=1)
        assert chunking.chunk([heading], ChunkPlan(strategy="paragraph"))[0][1] is heading
        assert chunking.chunk([heading], ChunkPlan(strategy="heading"))[0][0] == "A" * 130


class TestIngestion:
    def test_the_plan_comes_from_the_tenant_settings_and_falls_back(self, monkeypatch):
        import core.ai_providers as providers

        async def _effective(_tenant):
            return SimpleNamespace(chunk_strategy="heading", chunk_size=256)

        monkeypatch.setattr(providers, "get_effective_ai_setting", _effective)
        assert asyncio.run(ingest._resolve_chunk_plan(uuid.uuid4())) == ChunkPlan(strategy="heading", max_chars=1024)

        async def _broken(_tenant):
            raise RuntimeError("settings unavailable")

        monkeypatch.setattr(providers, "get_effective_ai_setting", _broken)
        assert asyncio.run(ingest._resolve_chunk_plan(uuid.uuid4())) == ChunkPlan()

    def test_ingestion_chunks_under_the_plan_and_stores_paragraph_and_heading(self):
        src = (ROOT / "core" / "rag" / "ingest.py").read_text(encoding="utf-8")
        assert "plan = await _resolve_chunk_plan(tenant_id)" in src
        assert "chunks = chunking.chunk(content.spans, plan)" in src
        assert "cell_range, frame_timestamp_s, paragraph, heading, created_at)" in src
        assert '"paragraph": span.paragraph,' in src and '"heading": (span.heading or "")[:200] or None,' in src

    def test_the_setting_is_exposed_and_validated(self):
        from api.v1.tenant_ai_settings import TenantAISettingUpdate

        assert TenantAISettingUpdate(chunk_strategy="paragraph").chunk_strategy == "paragraph"
        with pytest.raises(ValueError):
            TenantAISettingUpdate(chunk_strategy="semantic")
        from core.ai_providers.settings import _PLATFORM_DEFAULTS, EffectiveAISetting

        assert _PLATFORM_DEFAULTS.chunk_strategy == "sentence"
        assert "chunk_strategy" in EffectiveAISetting.__dataclass_fields__

    def test_the_migration_adds_the_columns(self):
        src = (ROOT / "migrations" / "versions" / "v6_z50_chunk_layout.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z49_agent_ratings"' in src
        for column in (
            "tenant_ai_settings ADD COLUMN IF NOT EXISTS chunk_strategy",
            "paragraph INTEGER",
            "heading VARCHAR(200)",
        ):
            assert column in src
