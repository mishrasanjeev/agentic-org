# SPDX-License-Identifier: Apache-2.0
"""Non-token metering: embeddings, OCR pages, speech minutes, priced tool calls and storage GB-days."""

from __future__ import annotations

import decimal
import math
import uuid
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.deps import ActiveHumanAdmin
from api.v1 import spend as api
from core import embeddings as embeddings_module
from core.config import settings
from core.spend import clock, context, meter, metering, rates, storage, writer
from tests.unit.spend_metering_fakes import install_metering, tenant_ids
from tests.unit.spend_usage_fakes import ACTOR, OTHER_TENANT, T0, TENANT
from tests.unit.test_spend_usage import card, event, hints

TID = str(TENANT)
ROOT = Path(__file__).resolve().parents[2]
ADMIN = ActiveHumanAdmin(user_id=uuid.UUID(ACTOR), tenant_id=TENANT, email="admin@example.com", role="admin")
REAL_SERVING_IDENTITY = embeddings_module.serving_identity
DAY = date(2026, 10, 1)


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def fresh_spend_context(monkeypatch):
    monkeypatch.setattr(context, "_SCOPE", ContextVar("agenticorg_spend_scope_metering", default=None))
    monkeypatch.setattr(context, "_CREDENTIAL", ContextVar("agenticorg_spend_credential_metering", default=None))


@pytest.fixture
def on(monkeypatch):
    """Spend on, a frozen clock, the writer captured, an in-house embedder named."""
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
    monkeypatch.setattr(clock, "now_utc", lambda: T0)
    got: dict[str, list] = {"events": [], "gaps": []}
    monkeypatch.setattr(writer, "submit", lambda events: got["events"].extend(events))
    monkeypatch.setattr(writer, "add_gap", lambda *args, **kw: got["gaps"].append(args))
    # Call-path gaps go through note_gap, which also starts the writer.
    monkeypatch.setattr(writer, "note_gap", lambda *args, **kw: got["gaps"].append(args))
    monkeypatch.setattr(writer, "start_for_gaps", lambda: None)
    monkeypatch.setattr(embeddings_module, "serving_identity", lambda: ("local_embeddings", "BAAI/bge-small-en-v1.5"))
    metering._PRICED_TOOLS_CACHE.clear()
    return got


@pytest.fixture
def store(monkeypatch):
    return install_metering(monkeypatch)


def tokens_of(texts, cap=512) -> int:
    return sum(min(math.ceil(len(t) / 4), cap) for t in texts)


# ---------------------------------------------------------------- isolation


class TestIsolation:
    SITES = (
        ("core/rag/ingest.py", 'spend.note("embeddings", tid, items=chunks, purpose="ingest", ref=document_id)'),
        ("core/rag/ingest.py", 'spend.note("ocr", tid, extracted=content, purpose="ingest", ref=document_id)'),
        (
            "core/rag/reindex.py",
            'spend.note("embeddings", tenant_id, items=batch, purpose="reindex", run_ref=run_ref, start=start)',
        ),
        ("api/v1/knowledge.py", 'spend.note("embeddings", tid, items=(query,), purpose="search")'),
        (
            "api/v1/knowledge.py",
            'spend.note("ocr", tenant_id, extracted=extracted_content, purpose="upload", ref=doc_id)',
        ),
        ("api/v1/idp.py", 'spend.note("ocr", tenant_id, result=result, purpose="idp")'),
        (
            "core/tasks/rpa_tasks.py",
            'spend.note("embeddings", tid, items=embedded, purpose="rpa", script_key=schedule.script_key)',
        ),
        ("core/speech/store.py", 'spend.note("speech", tenant_id, row=row, engine=engine, user_id=created_by)'),
        ("core/langgraph/tool_adapter.py", '"tool_call", tenant_id, connector=connector_name, tool=tool_name'),
    )

    def test_every_site_passes_raw_objects_and_never_computes(self):
        for path, anchor in self.SITES:
            text = source(path)
            assert anchor in text, (path, anchor)
            before = text[: text.index(anchor)].splitlines()[-4:]
            assert any(line.strip() == "if spend.enabled():" for line in before), (path, before)
        assert source("api/v1/knowledge.py").count('purpose="search")') == 2  # the vector and the hybrid search
        transcribe = source("core/speech/transcribe.py")
        assert 'spend_context.note_credential("deepgram", getattr(credential, "source", ""))' in transcribe

    @pytest.mark.asyncio
    async def test_handler_error_never_fails_ingest_speech_or_tool_call(self, monkeypatch, on):
        from core import spend
        from core.langgraph import tool_adapter
        from core.spend import vocab

        def explode(*args, **kwargs):
            raise decimal.InvalidOperation("malformed")

        monkeypatch.setattr(embeddings_module, "serving_identity", explode)
        monkeypatch.setattr(metering, "speech_minutes", explode)
        monkeypatch.setattr(vocab, "norm_provider", explode)
        result = await _ingest(monkeypatch)
        assert result.chunks_indexed >= 1
        kept = await _save_speech(monkeypatch, engine="faster_whisper")
        assert kept["status"] == "transcribed"
        monkeypatch.setattr(tool_adapter, "_dispatch_connector_tool", AsyncMock(return_value={"status": "sent"}))
        out = await tool_adapter._execute_connector_tool("chatops", "post_message", {}, tenant_id=TID, agent_id="a")
        assert out == {"status": "sent"}
        assert on["events"] == []
        spend.note("ocr", TID, extracted=None, purpose="nonsense")  # an unknown purpose: ignored
        spend.note("embeddings", TID, items=None, purpose="nonsense")
        assert on["events"] == []

    def test_note_failures_are_counted_under_the_kinds_usage_type(self, monkeypatch, on):
        from core import spend
        from observability import metrics

        def explode(*args):
            raise RuntimeError("down")

        monkeypatch.setattr(metering, "handle", explode)
        counter = metrics.spend_usage_write_failures_total.labels(usage_type="ocr_pages", reason="hook_error")
        before = counter._value.get()
        spend.note("ocr", TID, result={}, purpose="idp")
        assert counter._value.get() == before + 1

    def test_sites_do_nothing_while_off(self, monkeypatch):
        from core import spend

        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)

        def explode(*args):
            raise AssertionError("handled while off")

        monkeypatch.setattr(metering, "handle", explode)
        for kind in ("embeddings", "ocr", "speech", "tool_call"):
            spend.note(kind, TID)


# ---------------------------------------------------------------- embeddings


def _content(extracted_method="text"):
    from core.rag.extractors import ExtractedContent, ExtractedSpan

    text = "The quick brown fox jumps over the lazy dog. " * 12
    return ExtractedContent(
        spans=[ExtractedSpan(text=text, page=1)],
        mime_type="image/png" if extracted_method == "tesseract-ocr" else "text/plain",
        extraction_method=extracted_method,
        total_chars=len(text),
        extra={"page_count": 3, "ocr_pages": [1]} if extracted_method == "tesseract-ocr" else {},
    )


def _prepare_ingest(monkeypatch, content):
    """Ingestion with in-memory storage and embeddings, extracting ``content``."""
    import core.database
    from core.rag import ingest
    from core.rag.chunking import ChunkPlan

    class _IngestSession:
        async def execute(self, statement, params=None):
            return None

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(core.database, "async_session_factory", lambda: _IngestSession())
    monkeypatch.setattr(ingest, "_resolve_embedding_profile", AsyncMock(return_value=("openai", "small", 3)))
    monkeypatch.setattr(ingest, "_resolve_chunk_plan", AsyncMock(return_value=ChunkPlan()))
    monkeypatch.setattr(
        ingest, "_embed_chunks", AsyncMock(side_effect=lambda texts, model=None: [[0.1, 0.2, 0.3]] * len(texts))
    )
    monkeypatch.setattr(ingest.entities, "enabled", lambda: False)
    monkeypatch.setattr(ingest.provenance, "enabled", lambda: False)
    monkeypatch.setattr(ingest, "extract", lambda stream, mime_type, filename: content)
    return ingest


async def _ingest(monkeypatch, *, extracted=True, extracted_method="text"):
    content = _content(extracted_method)
    ingest = _prepare_ingest(monkeypatch, content)
    return await ingest.ingest_document(
        tenant_id=TENANT,
        title="Fox",
        stream=b"fox bytes",
        mime_type=content.mime_type,
        filename="fox.png" if extracted_method == "tesseract-ocr" else "fox.txt",
        extracted_content=content if extracted else None,
    )


class TestEmbeddings:
    def test_embedding_tokens_estimated_from_chars_and_capped_by_model_limit(self, on):
        assert metering.embedding_tokens([0, 1, 4, 5, 4000], max_tokens=512) == 0 + 1 + 1 + 2 + 512
        assert metering.embedding_tokens([4000], max_tokens=8192) == 1000
        assert metering.embedding_max_tokens("BAAI/bge-m3") == 8192
        assert metering.embedding_max_tokens("BAAI/bge-small-en-v1.5") == 512
        assert metering.embedding_max_tokens("some/other-model") == metering.DEFAULT_EMBEDDING_MAX_TOKENS
        metering.handle("embeddings", TID, {"items": ("x" * 3000,), "purpose": "search"})
        event_ = on["events"][-1]
        assert event_.quantity == 512 and event_.quantity_estimated is True and event_.calls == 1
        assert (event_.usage_type, event_.unit) == ("embedding_tokens", "embedding_token")

    def test_embeddings_label_ignored_in_house_engine_recorded(self, monkeypatch, on):
        import core.ai_providers

        monkeypatch.setattr(core.ai_providers, "get_effective_ai_setting", AsyncMock(side_effect=AssertionError))
        monkeypatch.setattr(embeddings_module, "serving_identity", REAL_SERVING_IDENTITY)
        monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", "true")
        monkeypatch.setenv("AGENTICORG_TEI_URL", "http://tei.internal")
        assert embeddings_module.serving_identity() == ("tei", "BAAI/bge-m3")
        metering.handle("embeddings", TID, {"items": ("y" * 40000,), "purpose": "search"})
        assert (on["events"][-1].provider, on["events"][-1].model, on["events"][-1].quantity) == (
            "tei",
            "BAAI/bge-m3",
            8192,
        )
        assert on["events"][-1].billing_account == "in_house"
        monkeypatch.delenv("AGENTICORG_TEI_URL")
        assert embeddings_module.serving_identity() == ("local_embeddings", "BAAI/bge-m3")
        monkeypatch.delenv("AGENTICORG_RAG_USE_BGE_M3")
        monkeypatch.delenv("AGENTICORG_TEST_FAKE_EMBEDDINGS", raising=False)
        assert embeddings_module.serving_identity() == ("local_embeddings", embeddings_module.EMBEDDING_MODEL_NAME)

    def test_fake_embeddings_are_not_metered(self, monkeypatch, on):
        monkeypatch.setattr(embeddings_module, "serving_identity", REAL_SERVING_IDENTITY)
        monkeypatch.delenv("AGENTICORG_RAG_USE_BGE_M3", raising=False)
        monkeypatch.setenv("AGENTICORG_TEST_FAKE_EMBEDDINGS", "1")
        monkeypatch.setattr(settings, "env", "test")
        assert embeddings_module.serving_identity() is None
        metering.handle("embeddings", TID, {"items": ("query text",), "purpose": "search"})
        assert on["events"] == []

    @pytest.mark.asyncio
    async def test_ingest_meters_once_per_document(self, monkeypatch, on):
        from core.rag import ingest

        result = await _ingest(monkeypatch)
        embedded = ingest._embed_chunks.call_args.args[0]
        assert result.chunks_indexed == len(embedded) >= 1
        assert len(on["events"]) == 1  # no OCR: the caller passed its own extraction
        event_ = on["events"][0]
        assert event_.idempotency_key == f"emb:ingest:{event_.source_ref}" and uuid.UUID(event_.source_ref)
        assert event_.quantity == tokens_of(embedded)
        assert event_.hints.application == "knowledge" and event_.hints.default_use_case == "knowledge.ingest"
        assert event_.tenant_id == TID and event_.event_time == T0

    @pytest.mark.asyncio
    async def test_reindex_meters_per_batch(self, on):
        from core.rag import reindex

        class _Session:
            async def execute(self, statement, params=None):
                return None

        class _DbRow:
            """Indexable like a database row, but neither a tuple nor a list."""

            def __init__(self, *values):
                self._values = values

            def __getitem__(self, index):
                return self._values[index]

            def __len__(self):
                return len(self._values)

        rows = [_DbRow(uuid.uuid4(), "chunk text " * (i + 1), "src", "old/model", True) for i in range(40)]
        embed = AsyncMock(side_effect=lambda texts: [[0.0]] * len(texts))
        done = await reindex.reindex(_Session(), TENANT, rows, model_name="new/model", column="embedding", embed=embed)
        assert done["re_embedded"] == 40
        assert [e.idempotency_key.rsplit(":", 1)[1] for e in on["events"]] == ["0", "32"]
        run_ref = on["events"][0].source_ref
        assert {e.source_ref for e in on["events"]} == {run_ref}
        assert on["events"][0].idempotency_key == f"emb:reindex:{run_ref}:0"
        assert on["events"][0].quantity == tokens_of([r[1] for r in rows[:32]])
        assert on["events"][1].quantity == tokens_of([r[1] for r in rows[32:]])
        assert on["events"][0].hints.default_use_case == "knowledge.reindex"

    @pytest.mark.asyncio
    async def test_search_meters_each_query_embedding(self, monkeypatch, on):
        import core.database
        from api.v1 import knowledge

        class _Rows:
            def fetchall(self):
                return []

        class _Session:
            async def execute(self, statement, params=None):
                return _Rows()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: _Session())
        monkeypatch.setattr(embeddings_module, "embed_one_async", AsyncMock(return_value=[0.1, 0.2]))
        monkeypatch.delenv("AGENTICORG_RAG_USE_BGE_M3", raising=False)
        assert await knowledge._native_vector_or_keyword_search(TENANT, "what is the leave policy", 5) == []
        assert await knowledge._native_hybrid_search(TENANT, "what is the leave policy", 5) == []
        assert len(on["events"]) == 2
        assert all(e.idempotency_key.startswith("emb:search:") and e.source_ref == "" for e in on["events"])
        assert on["events"][0].idempotency_key != on["events"][1].idempotency_key
        assert on["events"][0].quantity == math.ceil(len("what is the leave policy") / 4)
        assert on["events"][0].hints.default_use_case == "knowledge.search"
        with context.scope(application="agents", agent_id="11111111-0000-4000-8000-000000000001"):
            await knowledge._native_vector_or_keyword_search(TENANT, "q", 5)
        assert on["events"][-1].hints.application == "agents"  # an agent's search is the agent's

    @pytest.mark.asyncio
    async def test_rpa_meters_once_per_run(self, monkeypatch, on):
        import core.database
        from core.tasks import rpa_tasks

        class _Result:
            def scalar_one_or_none(self):
                return None

        class _Session:
            async def execute(self, statement, params=None):
                return _Result()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        def embed_one(text):
            if text.startswith("broken"):
                raise RuntimeError("embedder down")
            return [0.1, 0.2]

        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: _Session())
        monkeypatch.setattr(embeddings_module, "embed_one", embed_one)
        chunks = [
            {"content": "Circular one " * 20, "title": "a"},
            {"content": "broken chunk", "title": "b"},
            {"content": "", "title": "c"},
            {"content": "Circular two " * 5, "title": "d"},
        ]
        inserted = await rpa_tasks._embed_and_store(TENANT, SimpleNamespace(script_key="regulator-circulars"), chunks)
        assert inserted == 2 and len(on["events"]) == 1
        event_ = on["events"][0]
        assert event_.idempotency_key.startswith("emb:rpa:regulator-circulars:")
        assert event_.source_ref == "regulator-circulars" and event_.hints.application == "system"
        assert event_.quantity == tokens_of(["Circular one " * 20, "Circular two " * 5])
        assert event_.hints.default_use_case == "rpa.ingest"


# ---------------------------------------------------------------- OCR


class TestOcr:
    @pytest.mark.asyncio
    async def test_idp_counts_only_ocr_done_pages(self, monkeypatch, on):
        from api.v1 import idp as idp_api
        from core.idp import pipeline

        result = {"pages": [{}, {}, {}], "ocr": {"available": True, "pages": [1, 3], "unavailable": [2]}}
        monkeypatch.setattr(pipeline, "enabled", lambda: True)
        monkeypatch.setattr(pipeline, "process", lambda *args, **kwargs: result)
        monkeypatch.setattr(idp_api, "_read_bounded", AsyncMock(return_value=b"%PDF-1.7"))
        monkeypatch.setattr(idp_api.console, "document_rules", AsyncMock(return_value=None))
        file = SimpleNamespace(filename="statement.pdf", content_type="application/pdf")
        request = SimpleNamespace(state=SimpleNamespace(claims={}))
        answer = await idp_api.analyse(file, request, ocr=True, with_words=False, store=False, tenant_id=TID)
        assert answer["filename"] == "statement.pdf"
        assert len(on["events"]) == 1
        event_ = on["events"][0]
        assert (event_.usage_type, event_.unit, event_.quantity) == ("ocr_pages", "ocr_page", 2)
        assert (event_.provider, event_.model, event_.billing_account) == ("tesseract", "", "in_house")
        assert event_.idempotency_key.startswith("ocr:idp:") and event_.hints.application == "documents"
        metering.handle("ocr", TID, {"result": {"ocr": {"pages": []}}, "purpose": "idp"})
        metering.handle("ocr", TID, {"result": "not a result", "purpose": "idp"})
        assert len(on["events"]) == 1

    def test_upload_ocr_counts_pdf_ocr_pages_and_image_frames(self, on):
        pdf = SimpleNamespace(extraction_method="pypdf+ocr", extra={"page_count": 9, "ocr_pages": [2, 5]})
        image = SimpleNamespace(extraction_method="tesseract-ocr", extra={"page_count": 4, "ocr_pages": [1]})
        text_pdf = SimpleNamespace(extraction_method="pypdf", extra={"page_count": 9, "ocr_pages": []})
        assert metering.ocr_page_count(pdf) == 2  # only the low-text pages were OCR'd
        assert metering.ocr_page_count(image) == 4  # every frame is OCR'd, even those without text
        assert metering.ocr_page_count(text_pdf) == 0
        assert metering.ocr_page_count(SimpleNamespace(extraction_method="tesseract-ocr", extra="junk")) == 0
        assert metering.ocr_page_count(SimpleNamespace(extraction_method="x+ocr", extra={"ocr_pages": 3})) == 0
        assert metering.ocr_page_count(None) == 0
        metering.handle("ocr", TID, {"extracted": pdf, "purpose": "upload", "ref": "doc-1"})
        metering.handle("ocr", TID, {"extracted": text_pdf, "purpose": "upload", "ref": "doc-2"})
        assert [(e.idempotency_key, e.source_ref, e.quantity) for e in on["events"]] == [
            ("ocr:upload:doc-1", "doc-1", 2)
        ]
        assert on["events"][0].hints.application == "knowledge"
        assert on["events"][0].hints.default_use_case == "knowledge.ocr"

    @pytest.mark.asyncio
    async def test_upload_route_meters_its_ocr_once(self, monkeypatch, on):
        import io

        from api.v1 import knowledge

        content = _content("tesseract-ocr")
        _prepare_ingest(monkeypatch, content)

        class _Gate:
            async def run_blocking(self, fn, *args, **kwargs):
                return content

        class _Upload:
            filename = "scan.txt"
            content_type = "text/plain"
            size = None

            def __init__(self):
                self._file = io.BytesIO(b"scanned page bytes")

            async def read(self, size=-1):
                return self._file.read(size)

        monkeypatch.setattr(knowledge, "_DOCUMENT_EXTRACTION_CAPACITY", _Gate())
        monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)
        monkeypatch.setattr(knowledge, "_db_find_existing_by_filename", AsyncMock(return_value=None))
        monkeypatch.setattr(knowledge, "_db_store_doc", AsyncMock(return_value=None))
        monkeypatch.setattr(knowledge, "_db_set_doc_status", AsyncMock(return_value=None))
        out = await knowledge.upload_document(
            file=_Upload(), tenant_id=TID, domain=None, allow_duplicate=False, replace=False
        )
        assert out.ingestion_status == "indexed" and out.extraction_method == "tesseract-ocr"
        ocr = [e for e in on["events"] if e.usage_type == "ocr_pages"]
        # One record for the upload's OCR, keyed by the route's document; ingestion was handed the
        # extraction, so it meters no OCR of its own.
        assert [(e.idempotency_key, e.source_ref, e.quantity) for e in ocr] == [
            (f"ocr:upload:{out.document_id}", out.document_id, 3)
        ]
        assert ocr[0].hints.application == "knowledge" and ocr[0].tenant_id == TID
        assert [e.usage_type for e in on["events"]] == ["ocr_pages", "embedding_tokens"]

    @pytest.mark.asyncio
    async def test_ingest_counts_ocr_only_when_it_extracted_itself(self, monkeypatch, on):
        await _ingest(monkeypatch, extracted=True, extracted_method="tesseract-ocr")
        assert [e.usage_type for e in on["events"]] == ["embedding_tokens"]  # the route meters the upload's OCR
        on["events"].clear()
        await _ingest(monkeypatch, extracted=False, extracted_method="tesseract-ocr")
        kinds = {e.usage_type: e for e in on["events"]}
        assert set(kinds) == {"embedding_tokens", "ocr_pages"}
        assert kinds["ocr_pages"].quantity == 3
        document = kinds["embedding_tokens"].source_ref
        assert kinds["ocr_pages"].idempotency_key == f"ocr:ingest:{document}"


# ---------------------------------------------------------------- speech


async def _save_speech(monkeypatch, *, engine):
    from core.speech import store as speech_store
    from core.speech import transcribe
    from tests.unit.test_speech_transcription import _Session, _use, mono_call

    session = _Session()
    _use(monkeypatch, session)
    monkeypatch.setattr(transcribe, "transcribe", AsyncMock(return_value=[transcribe.Word("hello", 0.4, 0.8, 0.9)]))
    return await speech_store.save(
        TENANT,
        filename="call.wav",
        mime_type="audio/wav",
        data=mono_call(),
        channel_roles=["agent", "customer"],
        language="en",
        engine=engine,
        created_by=ACTOR,
    )


def _row(**over):
    base = {"id": uuid.uuid4(), "status": "transcribed", "duration_seconds": 90.5}
    base.update(over)
    return SimpleNamespace(**base)


class TestSpeech:
    @pytest.mark.asyncio
    async def test_speech_minutes_from_duration_for_transcribed_only(self, monkeypatch, on):
        row = _row()
        metering.handle("speech", TID, {"row": row, "engine": "faster_whisper", "user_id": ACTOR})
        event_ = on["events"][0]
        assert (event_.usage_type, event_.unit, event_.quantity) == (
            "speech_minutes",
            "audio_minute",
            Decimal("1.508333"),
        )
        assert (event_.provider, event_.model, event_.billing_account) == ("faster_whisper", "base", "in_house")
        assert event_.idempotency_key == f"speech:{row.id}" and event_.source_ref == str(row.id)
        assert event_.hints.application == "speech" and event_.hints.default_use_case == "speech.transcription"
        assert event_.hints.initiating_user_id == ACTOR
        for status in ("received", "failed"):
            metering.handle("speech", TID, {"row": _row(status=status), "engine": "faster_whisper"})
        metering.handle("speech", TID, {"row": _row(duration_seconds=0), "engine": "deepgram"})
        assert len(on["events"]) == 1
        kept = await _save_speech(monkeypatch, engine="faster_whisper")
        assert kept["status"] == "transcribed" and len(on["events"]) == 2
        saved = on["events"][1]
        assert saved.idempotency_key.startswith("speech:") and saved.quantity == metering.speech_minutes(
            kept["duration_seconds"]
        )

    @pytest.mark.asyncio
    async def test_supplied_engine_is_not_metered(self, monkeypatch, on):
        metering.handle("speech", TID, {"row": _row(), "engine": "supplied"})
        metering.handle("speech", TID, {"row": _row(), "engine": None})
        metering.handle("speech", TID, {"row": None, "engine": "deepgram"})
        assert on["events"] == []
        from core.speech import store as speech_store
        from tests.unit.test_speech_transcription import _Session, _use, stereo_call

        _use(monkeypatch, _Session())
        kept = await speech_store.save(
            TENANT,
            filename="call.wav",
            mime_type="audio/wav",
            data=stereo_call(),
            channel_roles=["agent", "customer"],
            language="en",
            engine="supplied",
            created_by=None,
        )
        assert kept["status"] == "received" and on["events"] == []

    @pytest.mark.asyncio
    async def test_whisper_is_in_house_zero_deepgram_needs_card(self, monkeypatch, store, on):
        monkeypatch.setattr(writer, "submit", lambda events: on["events"].extend(events))
        metering.handle("speech", TID, {"row": _row(duration_seconds=120), "engine": "faster_whisper"})
        metering.handle("speech", TID, {"row": _row(duration_seconds=60), "engine": "deepgram"})
        await meter.write_events(store, TENANT, on["events"], now=T0)
        records = {r.provider: r for r in store.of("spend_usage_records")}
        assert records["faster_whisper"].price_source == "in_house" and records["faster_whisper"].amount == 0
        assert records["deepgram"].unpriced is True and records["deepgram"].amount is None
        store.add(
            card(
                provider="deepgram",
                usage_type="speech_minutes",
                model_sku="nova-2",
                unit="audio_minute",
                unit_price=Decimal("0.0043"),
            )
        )
        on["events"].clear()
        metering.handle("speech", TID, {"row": _row(duration_seconds=60), "engine": "deepgram"})
        await meter.write_events(store, TENANT, on["events"], now=T0)
        priced = [r for r in store.of("spend_usage_records") if r.provider == "deepgram" and not r.unpriced]
        assert len(priced) == 1 and priced[0].amount == Decimal("0.0043") and priced[0].price_source == "contract"

    @pytest.mark.asyncio
    async def test_deepgram_billing_account_from_the_credential_source(self, monkeypatch, on):
        import httpx

        from core.ai_providers import resolver
        from core.speech import audio, transcribe
        from tests.unit.test_speech_transcription import mono_call

        metering.handle("speech", TID, {"row": _row(), "engine": "deepgram"})
        assert on["events"][-1].billing_account is None  # unknown here: the writer infers it
        payload = {"results": {"channels": [{"alternatives": [{"words": [{"word": "hi", "start": 0, "end": 1}]}]}]}}
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
        for source_name, account in (("tenant", "tenant_key"), ("platform_env", "platform_key")):
            credential = resolver.ResolvedCredential(
                secret="dg-key", provider="stt_deepgram", kind="stt", source=source_name
            )
            monkeypatch.setattr(resolver, "get_provider_credential", AsyncMock(return_value=credential))
            words = await transcribe.transcribe_deepgram(TENANT, audio.load(mono_call(), "audio/wav"))
            assert [w.text for w in words] == ["hi"]
            assert context.current_credential() == ("deepgram", source_name)
            metering.handle("speech", TID, {"row": _row(), "engine": "deepgram"})
            assert on["events"][-1].billing_account == account
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        context.note_credential("deepgram", "tenant")  # a no-op while off
        assert context.current_credential() == ("deepgram", "platform_env")


# ---------------------------------------------------------------- priced tool calls


def tool_card(**over):
    base = {"provider": "chatops", "usage_type": "tool_calls", "model_sku": "post_message", "unit": "call"}
    base.update(over)
    return card(unit_price=Decimal("0.01"), **base)


class TestTools:
    @pytest.mark.asyncio
    async def test_tool_call_metered_only_when_a_card_prices_it(self, monkeypatch, store, on):
        from core.langgraph import tool_adapter

        monkeypatch.setattr(tool_adapter, "_dispatch_connector_tool", AsyncMock(return_value={"status": "sent"}))
        agent = "44444444-4444-4444-8444-444444444444"
        await tool_adapter._execute_connector_tool("ChatOps", "post_message", {}, tenant_id=TID, agent_id=agent)
        event_ = on["events"][0]
        assert (event_.usage_type, event_.unit, event_.quantity, event_.calls) == ("tool_calls", "call", 1, 1)
        assert (event_.provider, event_.model, event_.billing_account) == ("chatops", "post_message", "tenant_key")
        assert event_.skip_if_unpriced is True and event_.idempotency_key.startswith("tool:")
        assert event_.hints.agent_id == agent
        result = await meter.write_events(store, TENANT, [event_], now=T0)
        assert result.skipped == 1 and result.written == 0
        gaps = store.of("spend_meter_gaps")
        assert [(g.usage_type, g.reason, g.detail, g.count) for g in gaps] == [
            ("tool_calls", "unpriced_tool", "chatops:post_message", 1)
        ]
        store.add(tool_card())
        metering.invalidate_priced_tools(TENANT)
        assert (await meter.write_events(store, TENANT, [replace_key(event_)], now=T0)).written == 1
        store.add(tool_card(provider="ticketing", model_sku=""))  # a provider default prices every tool
        ticketing = replace_key(event_, provider="ticketing", model="create_issue")
        assert (await meter.write_events(store, TENANT, [ticketing], now=T0)).written == 1
        priced = sorted(r.amount for r in store.of("spend_usage_records"))
        assert priced == [Decimal("0.01"), Decimal("0.01")]

    @pytest.mark.asyncio
    async def test_tool_call_error_outcome_not_metered(self, monkeypatch, on):
        from core.langgraph import tool_adapter

        for result in ({"error": "boom"}, {"error": "guardrail_blocked"}, {"error": "operator_override"}):
            monkeypatch.setattr(tool_adapter, "_dispatch_connector_tool", AsyncMock(return_value=result))
            assert await tool_adapter._execute_connector_tool("chatops", "post_message", {}, tenant_id=TID) == result
        assert on["events"] == [] and on["gaps"] == []
        metering.handle("tool_call", None, {"connector": "chatops", "tool": "post_message", "result": {}})
        assert on["events"] == []  # no tenant: counted, not queued

    @pytest.mark.asyncio
    async def test_priced_tool_cache_skips_without_queuing_and_counts_the_gap(self, store, on):
        store.add(tool_card())
        store.add(tool_card(provider="ticketing", model_sku=""))
        store.add(tool_card(provider="ledger", model_sku="post_invoice", effective_to=date(2026, 6, 1)))  # ended
        await metering.refresh_priced_tools(store, TENANT, now=T0)
        assert metering.cached_priced_tools(TENANT) == frozenset({("chatops", "post_message"), ("ticketing", "")})
        metering.handle("tool_call", TID, {"connector": "chatops", "tool": "list_channels", "result": {}})
        metering.handle("tool_call", TID, {"connector": "ledger", "tool": "post_invoice", "result": {}})
        assert on["events"] == []
        assert [g[2:] for g in on["gaps"]] == [
            ("tool_calls", "unpriced_tool", "chatops:list_channels"),
            ("tool_calls", "unpriced_tool", "ledger:post_invoice"),
        ]
        metering.handle("tool_call", TID, {"connector": "chatops", "tool": "post_message", "result": {}})
        metering.handle("tool_call", TID, {"connector": "ticketing", "tool": "anything", "result": {}})
        assert [(e.provider, e.model) for e in on["events"]] == [("chatops", "post_message"), ("ticketing", "anything")]
        selects = len(store.statements)
        await metering.refresh_priced_tools(store, TENANT, now=T0)  # fresh: no read
        assert len(store.statements) == selects

    @pytest.mark.asyncio
    async def test_priced_tool_cache_follows_the_tenants_aliases(self, store, on):
        from core.spend import mappings

        store.add(tool_card())
        await metering.refresh_priced_tools(store, TENANT, now=T0)
        assert metering.cached_priced_tools(TENANT) == frozenset({("chatops", "post_message")})
        await mappings.put_alias(
            TENANT, {"provider": "chatops", "alias": "send_message", "model_sku": "post_message"}, actor=ACTOR, now=T0
        )
        assert metering.cached_priced_tools(TENANT) is None  # an alias write drops the set
        await metering.refresh_priced_tools(store, TENANT, now=T0)
        assert metering.cached_priced_tools(TENANT) == frozenset(
            {("chatops", "post_message"), ("chatops", "send_message")}
        )
        metering.handle("tool_call", TID, {"connector": "chatops", "tool": "send_message", "result": {}})
        assert on["gaps"] == [] and [(e.provider, e.model) for e in on["events"]] == [("chatops", "send_message")]
        assert (await meter.write_events(store, TENANT, on["events"], now=T0)).written == 1
        (record,) = [r for r in store.of("spend_usage_records") if r.usage_type == "tool_calls"]
        assert record.model == "post_message" and record.amount == Decimal("0.01")  # priced through the alias
        metering.handle("tool_call", TID, {"connector": "chatops", "tool": "unrelated", "result": {}})
        assert [g[2:] for g in on["gaps"]] == [("tool_calls", "unpriced_tool", "chatops:unrelated")]

    @pytest.mark.asyncio
    async def test_in_house_models_take_no_alias(self, store):
        from fastapi import HTTPException

        from core.spend import mappings
        from core.spend.errors import SpendError

        for provider in ("vllm", "Ollama", "tesseract"):
            with pytest.raises(SpendError) as info:
                await mappings.put_alias(
                    TENANT, {"provider": provider, "alias": "inhouse-70b", "model_sku": "other"}, actor=ACTOR, now=T0
                )
            assert (info.value.status, info.value.code) == (422, "invalid_reference")
        with pytest.raises(HTTPException) as refused:
            await api.put_model_alias(
                api.AliasIn(provider="vllm", alias="inhouse-70b", model_sku="other"), ADMIN, tenant_id=TID
            )
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "invalid_reference"
        assert store.of("spend_model_aliases") == []

    @pytest.mark.asyncio
    async def test_card_write_invalidates_the_priced_tool_cache(self, store, on):
        await metering.refresh_priced_tools(store, TENANT, now=T0)
        assert metering.cached_priced_tools(TENANT) == frozenset()
        await rates.create_card(
            TENANT,
            {
                "provider": "chatops",
                "usage_type": "tool_calls",
                "unit": "call",
                "unit_price": "0.01",
                "currency": "USD",
                "effective_from": "2026-09-01",
                "source": "contract",
            },
            actor=ACTOR,
            now=T0,
        )
        assert metering.cached_priced_tools(TENANT) is None
        await metering.refresh_priced_tools(store, TENANT, now=T0)
        assert metering.cached_priced_tools(TENANT) == frozenset({("chatops", "")})

    @pytest.mark.asyncio
    async def test_writer_refreshes_the_priced_tool_set_and_survives_a_failed_read(self, monkeypatch, store, on):
        store.add(tool_card())
        tool = event(
            usage_type="tool_calls", unit="call", quantity=Decimal(1), provider="chatops", model="post_message"
        )
        tool = replace_key(tool, skip_if_unpriced=True)
        assert (await meter.write_events(store, TENANT, [tool], now=T0)).written == 1
        assert metering.cached_priced_tools(TENANT) == frozenset({("chatops", "post_message")})
        metering.invalidate_priced_tools(TENANT)
        monkeypatch.setattr(metering, "priced_tool_set", AsyncMock(side_effect=RuntimeError("read failed")))
        assert (await meter.write_events(store, TENANT, [replace_key(tool)], now=T0)).written == 1
        assert metering.cached_priced_tools(TENANT) is None
        metering._PRICED_TOOLS_CACHE.update({str(i): (0.0, frozenset()) for i in range(metering.PRICED_TOOLS_MAX)})
        monkeypatch.setattr(metering, "priced_tool_set", AsyncMock(return_value=frozenset()))
        await metering.refresh_priced_tools(store, TENANT, now=T0)
        assert list(metering._PRICED_TOOLS_CACHE) == [TID]  # full: cleared before adding


def replace_key(event_, **over):
    from dataclasses import replace

    return replace(event_, idempotency_key=f"tool:{uuid.uuid4().hex}", **over)


# ---------------------------------------------------------------- storage


GIB = 1024**3


def _bytes(store, tenant=TENANT, **sizes):
    for name, size in sizes.items():
        store.bytes[(str(tenant), name)] = size


def storage_card(**over):
    base = {
        "provider": "platform_storage",
        "usage_type": "storage",
        "model_sku": "",
        "unit": "gb_month",
        "unit_price": Decimal("31"),
        "currency": "INR",
    }
    base.update(over)
    return card(**base)


class TestStorage:
    @pytest.mark.asyncio
    async def test_storage_sample_one_record_per_store_per_day_idempotent(self, store):
        _bytes(store, knowledge=2 * GIB, documents=1024**2, idp=0, speech=GIB // 2)
        store.add(storage_card())
        now = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
        out = await storage.sample_tenant(TENANT, day=DAY, now=now, write=True)
        assert out["written"] == 3 and out["measured"] is True
        assert out["stores"] == {
            "knowledge": "2.000000",
            "documents": "0.000977",
            "idp": "0.000000",
            "speech": "0.500000",
        }
        records = {r.model: r for r in store.of("spend_usage_records")}
        assert set(records) == {"knowledge", "documents", "speech"}  # an empty store writes nothing
        knowledge = records["knowledge"]
        assert knowledge.idempotency_key == "storage:2026-10-01:knowledge" and knowledge.source_ref == "knowledge"
        assert knowledge.event_time == datetime(2026, 10, 1, 18, 0, tzinfo=UTC)  # 23:30 IST
        assert (knowledge.usage_type, knowledge.unit, knowledge.provider) == ("storage", "gb_day", "platform_storage")
        assert knowledge.amount == Decimal("2.0000000000") and knowledge.currency == "INR"  # 31 a month / 31 days
        assert knowledge.billing_account == "in_house" and knowledge.quantity_estimated is False
        assert (
            knowledge.calls == 0 and knowledge.application == "knowledge" and knowledge.use_case == "storage.knowledge"
        )
        assert records["speech"].application == "speech" and records["documents"].application == "knowledge"
        again = await storage.sample_tenant(TENANT, day=DAY, now=now, write=True)
        assert again == {"day": "2026-10-01", "measured": False, "stores": {}, "written": 0}
        assert len(store.of("spend_usage_records")) == 3

    @pytest.mark.asyncio
    async def test_storage_intended_day_survives_a_delayed_run(self, monkeypatch, store):
        from core.spend import tenants

        assert storage.intended_day(datetime(2026, 10, 1, 18, 0, tzinfo=UTC)) == DAY  # on time, 23:30 IST
        assert storage.intended_day(datetime(2026, 10, 2, 0, 0, tzinfo=UTC)) == DAY  # 05:30 IST the next day
        assert storage.intended_day(datetime(2026, 10, 2, 0, 31, tzinfo=UTC)) == date(2026, 10, 2)
        assert storage.sample_time(DAY) == datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
        monkeypatch.setattr(tenants, "active_tenant_ids", tenant_ids(TENANT))
        _bytes(store, knowledge=GIB)
        out = await storage.sample_all_tenants(now=datetime(2026, 10, 2, 0, 0, tzinfo=UTC))
        assert out == {"day": "2026-10-01", "tenants": 1, "measured": 1, "written": 1, "failed": 0}
        assert store.of("spend_usage_records")[0].idempotency_key == "storage:2026-10-01:knowledge"

    @pytest.mark.asyncio
    async def test_storage_gap_fill_marks_missed_days_estimated(self, store):
        _bytes(store, knowledge=GIB, speech=GIB)
        first = await storage.sample_tenant(TENANT, day=date(2026, 9, 27), now=T0, write=True)
        assert first["written"] == 2 and first["filled"] == []  # no earlier sample: nothing is filled
        out = await storage.sample_tenant(TENANT, day=DAY, now=T0, write=True)
        assert out["filled"] == ["2026-09-28", "2026-09-29", "2026-09-30"] and out["written"] == 8
        by_day = {}
        for r in store.of("spend_usage_records"):
            by_day.setdefault(r.event_date.isoformat(), set()).add(r.quantity_estimated)
        assert by_day == {
            "2026-09-27": {False},
            "2026-09-28": {True},
            "2026-09-29": {True},
            "2026-09-30": {True},
            "2026-10-01": {False},
        }

    @pytest.mark.asyncio
    async def test_storage_empty_days_are_marked_and_never_filled(self, store):
        store.add(storage_card())
        _bytes(store, knowledge=GIB)
        await storage.sample_tenant(TENANT, day=date(2026, 9, 27), now=T0, write=True)
        _bytes(store, knowledge=0)
        for day in (date(2026, 9, 28), date(2026, 9, 29)):
            out = await storage.sample_tenant(TENANT, day=day, now=T0, write=True)
            assert out["written"] == 1  # one zero record marks the day sampled
        _bytes(store, knowledge=GIB)
        out = await storage.sample_tenant(TENANT, day=DAY, now=T0, write=True)
        assert out["filled"] == ["2026-09-30"]  # the missed beat only: the empty days are not filled
        found = {(r.event_date.isoformat(), r.quantity, r.quantity_estimated) for r in store.of("spend_usage_records")}
        assert found == {
            ("2026-09-27", Decimal(1), False),
            ("2026-09-28", Decimal(0), False),
            ("2026-09-29", Decimal(0), False),
            ("2026-09-30", Decimal(1), True),
            ("2026-10-01", Decimal(1), False),
        }
        markers = [r for r in store.of("spend_usage_records") if r.quantity == 0]
        assert {(r.model, r.amount, r.unpriced, r.price_source) for r in markers} == {
            ("knowledge", Decimal(0), False, "contract")
        }
        assert {r.idempotency_key for r in markers} == {"storage:2026-09-28:knowledge", "storage:2026-09-29:knowledge"}

    @pytest.mark.asyncio
    async def test_storage_empty_day_without_a_card_or_a_history_writes_nothing(self, store):
        _bytes(store, knowledge=GIB)
        await storage.sample_tenant(TENANT, day=date(2026, 9, 30), now=T0, write=True)  # no card: unpriced
        _bytes(store, knowledge=0)
        out = await storage.sample_tenant(TENANT, day=DAY, now=T0, write=True)
        assert out["written"] == 0  # an unpriced zero record would only count against the unpriced limit
        store.add(storage_card(tenant_id=OTHER_TENANT))
        out = await storage.sample_tenant(OTHER_TENANT, day=DAY, now=T0, write=True)
        assert out["written"] == 0  # a tenant that never kept anything writes nothing
        assert [r.tenant_id for r in store.of("spend_usage_records")] == [TENANT]
        marked = {storage.storage_key(DAY, "speech")}  # a day already marked is not marked twice
        assert (
            await storage.empty_day_markers(store, TENANT, {"speech": Decimal(0)}, [DAY], day=DAY, existing=marked)
            == []
        )

    def test_storage_plan_days(self):
        keys = {storage.storage_key(date(2026, 9, 25), "speech"), storage.storage_key(DAY, "knowledge")}
        assert storage.plan_days(DAY, keys) == (True, [date(2026, 9, 26) + timedelta(days=n) for n in range(5)])
        assert storage.plan_days(DAY, set()) == (False, [])
        assert storage.plan_days(DAY, {storage.storage_key(date(2026, 9, 30), "idp")}) == (False, [])

    @pytest.mark.asyncio
    async def test_storage_checks_keys_before_measuring(self, store):
        _bytes(store, knowledge=GIB)
        await storage.sample_tenant(TENANT, day=DAY, now=T0, write=True)
        assert store.measured == [TID]
        await storage.sample_tenant(TENANT, day=DAY, now=T0, write=True)
        assert store.measured == [TID]  # the day's key exists: nothing measured
        await storage.sample_tenant(TENANT, day=DAY + timedelta(days=1), now=T0, write=True)
        assert store.measured == [TID, TID]

    @pytest.mark.asyncio
    async def test_storage_skips_deleted_tenants(self, store):
        from core.models.tenant import Tenant

        store.add(Tenant(id=TENANT, name="a", slug="a", deleted_at=None))
        store.add(Tenant(id=OTHER_TENANT, name="b", slug="b", deleted_at=T0))
        _bytes(store, knowledge=GIB)
        _bytes(store, tenant=OTHER_TENANT, knowledge=GIB)
        out = await storage.sample_all_tenants(now=datetime(2026, 10, 1, 18, 0, tzinfo=UTC))
        assert out["tenants"] == 1 and out["written"] == 1
        assert {r.tenant_id for r in store.of("spend_usage_records")} == {TENANT}

    @pytest.mark.asyncio
    async def test_storage_missing_table_is_skipped(self, store):
        store.missing_tables = {"idp_documents", "speech_recordings"}
        _bytes(store, knowledge=10, documents=20, idp=30, speech=40)
        assert await storage.measure(store, TENANT) == {"knowledge": 10, "documents": 20}
        assert storage.gib(GIB * 3 // 2) == Decimal("1.500000") and storage.gib(-5) == Decimal("0.000000")

    @pytest.mark.asyncio
    async def test_storage_sweep_isolates_tenant_failures(self, monkeypatch, store):
        from core.spend import tenants

        broken = uuid.UUID("99999999-9999-4999-8999-999999999999")
        real = storage.sample_tenant

        async def sample(tenant_id, **kwargs):
            if tenant_id == broken:
                raise RuntimeError("tenant session down")
            return await real(tenant_id, **kwargs)

        monkeypatch.setattr(tenants, "active_tenant_ids", tenant_ids(broken, TENANT))
        monkeypatch.setattr(storage, "sample_tenant", sample)
        _bytes(store, knowledge=GIB)
        out = await storage.sample_all_tenants(now=datetime(2026, 10, 1, 18, 0, tzinfo=UTC))
        assert out["tenants"] == 2 and out["failed"] == 1 and out["written"] == 1

    @pytest.mark.asyncio
    async def test_storage_route_previews_without_writing(self, store):
        _bytes(store, knowledge=GIB, speech=GIB // 4)
        out = await api.preview_storage_sample(ADMIN, tenant_id=TID)
        assert out == {
            "day": "2026-10-01",
            "stores": {"knowledge": "1.000000", "documents": "0.000000", "idp": "0.000000", "speech": "0.250000"},
            "written": 0,
        }
        assert store.of("spend_usage_records") == []
        audits = store.of("audit_log")
        assert [a.event_type for a in audits] == ["spend.storage.preview"]
        assert audits[0].actor_id == ACTOR and audits[0].details["written"] == 0
        quiet = await storage.sample_tenant(TENANT, day=DAY, now=T0, write=False)  # no actor: no audit row
        assert quiet["written"] == 0 and len(store.of("audit_log")) == 1


# ---------------------------------------------------------------- hints and helpers


class TestHelpers:
    def test_events_carry_the_request_correlation_as_a_hash(self, monkeypatch, on):
        import structlog

        structlog.contextvars.bind_contextvars(request_id="client-request-1")
        try:
            metering.handle("ocr", TID, {"result": {"ocr": {"pages": [1]}}, "purpose": "idp"})
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        metering.handle("ocr", TID, {"result": {"ocr": {"pages": [1]}}, "purpose": "idp"})
        first, second = on["events"]
        assert first.correlation_ref == meter.correlation_ref("client-request-1") and "client" not in str(first)
        assert second.correlation_ref == ""

    def test_tenant_comes_from_the_scope_when_the_site_has_none(self, on):
        with context.scope(tenant_id=TID, application="workflows", workflow_id="wf-1"):
            metering.handle("ocr", None, {"result": {"ocr": {"pages": [1, 2]}}, "purpose": "idp"})
        event_ = on["events"][0]
        assert event_.tenant_id == TID and event_.hints.application == "workflows"
        assert event_.hints.workflow_id == "wf-1"
        metering.handle("ocr", None, {"result": {"ocr": {"pages": [1]}}, "purpose": "idp"})
        assert len(on["events"]) == 1  # no tenant anywhere: counted, not queued

    def test_counts_and_user_ids_are_bounded(self):
        assert metering._count("7") == 7 and metering._count(-3) == 0 and metering._count(True) == 0
        assert metering._count("x") == 0 and metering._count(None) == 0
        assert metering._user_uuid("not-a-uuid") is None and metering._user_uuid(None) is None
        assert metering._user_uuid(ACTOR) == ACTOR
        assert metering._chunk_chars("a string, not a chunk") == 0 and metering._chunk_chars((5, None)) == 0
        assert metering._row_chars("a string, not a row") == 0 and metering._row_chars(object()) == 0
        assert metering._row_chars(["id"]) == 0 and metering._text_chars(42) == 0

    def test_hints_from_the_events_are_server_side(self, on):
        metering.handle("speech", TID, {"row": _row(), "engine": "faster_whisper", "user_id": "u-raw"})
        assert on["events"][0].hints.initiating_user_id is None  # only a user uuid is kept
        assert hints(application="api").application == "api"
