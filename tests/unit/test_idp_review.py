# SPDX-License-Identifier: Apache-2.0
"""Document review: the store keeps files and results, renders pages, takes corrections and decisions; the routes."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.idp import store
from core.idp.pages import DocumentError
from tests.unit.idp_pdf_fixture import make_pdf

TENANT = uuid.uuid4()


def _pdf(lines: list[str]) -> bytes:
    return make_pdf(lines, table_columns=False)


def _result(needs_review: bool = True) -> dict:
    return {
        "pages": [{"number": 1, "width": 595, "height": 842, "lines": [], "word_boxes": [{"text": "x"}]}],
        "segments": [{"index": 0, "document_type": "salary_slip", "pages": [1]}],
        "documents": [
            {
                "index": 0,
                "document_type": "salary_slip",
                "confidence": 0.9,
                "pages": [1],
                "fields": [
                    {
                        "name": "net_pay",
                        "value": "41250",
                        "confidence": 0.95,
                        "page": 1,
                        "bbox": [60, 80, 120, 92],
                        "status": "found",
                        "required": True,
                    },
                    {
                        "name": "pay_period",
                        "value": None,
                        "confidence": 0.0,
                        "page": None,
                        "bbox": None,
                        "status": "missing",
                        "required": True,
                    },
                ],
                "extra_fields": [{"name": "branch", "value": "Pune", "confidence": 0.7}],
                "tables": [],
                "review": {
                    "needed": needs_review,
                    "reasons": ["required field pay_period not found"] if needs_review else [],
                },
            }
        ],
        "review": {"needed": needs_review, "documents": [0] if needs_review else []},
        "ocr": {"available": True, "pages": [], "unavailable": []},
    }


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_a, **_k):
        row = self.row
        return SimpleNamespace(
            scalar_one_or_none=lambda: row, scalars=lambda: SimpleNamespace(all=lambda: [row] if row else [])
        )

    def add(self, row):
        row.id = uuid.uuid4()
        self.added.append(row)

    async def flush(self):
        return None


def _row(**overrides):
    base = {
        "id": uuid.uuid4(),
        "filename": "slip.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "content": _pdf(["SALARY SLIP", "Net Pay: 41,250"]),
        "status": "review",
        "pages": 1,
        "result": _result(),
        "corrections": {},
        "review_reasons": ["required field pay_period not found"],
        "review_notes": None,
        "created_by": "uploader",
        "reviewed_by": None,
        "reviewed_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestStore:
    @pytest.mark.asyncio
    async def test_save_keeps_the_file_and_routes_by_the_pipelines_decision(self, monkeypatch):
        import core.database as database

        session = _Session()
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: session)
        data = _pdf(["x"])
        kept = await store.save(
            TENANT, filename="a.pdf", mime_type="application/pdf", data=data, result=_result(), created_by="u1"
        )
        assert kept["status"] == "review" and kept["review_reasons"] == ["required field pay_period not found"]
        row = session.added[0]
        assert row.content == data and "word_boxes" not in row.result["pages"][0] and row.pages == 1
        kept = await store.save(
            TENANT, filename="b.pdf", mime_type="application/pdf", data=data, result=_result(False), created_by=None
        )
        assert kept["status"] == "processed" and kept["document_types"] == ["salary_slip"]
        with pytest.raises(DocumentError) as info:
            await store.save(
                TENANT,
                filename="c",
                mime_type="application/pdf",
                data=b"x" * (store.MAX_BYTES + 1),
                result=_result(),
                created_by=None,
            )
        assert info.value.status == 413

    def test_corrections_apply_to_the_fields_with_the_original_beside(self):
        documents = store.effective_documents(
            _result(), {"0": {"net_pay": {"value": "41,250.00", "by": "r1", "at": "t"}}}
        )
        net = next(f for f in documents[0]["fields"] if f["name"] == "net_pay")
        assert net["value"] == "41,250.00" and net["original_value"] == "41250" and net["corrected"] is True
        period = next(f for f in documents[0]["fields"] if f["name"] == "pay_period")
        assert period["corrected"] is False

    def test_pages_render_as_png_from_pdfs_and_images(self):
        png = store.render_page(_pdf(["hello"]), "application/pdf", 1)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        with pytest.raises(DocumentError) as info:
            store.render_page(_pdf(["hello"]), "application/pdf", 2)
        assert info.value.code == "page_not_found"
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (3000, 1000), "white").save(buffer, format="PNG")
        rendered = store.render_page(buffer.getvalue(), "image/png", 1)
        assert Image.open(io.BytesIO(rendered)).size[0] == store.MAX_IMAGE_SIDE
        with pytest.raises(DocumentError):
            store.render_page(buffer.getvalue(), "image/png", 2)

    @pytest.mark.asyncio
    async def test_a_reviewer_corrects_fields_then_decides_and_the_document_closes(self, monkeypatch):
        import core.database as database

        row = _row()
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        detail = await store.correct(
            TENANT, row.id, document_index=0, field="pay_period", value="September 2026", user_id="r1"
        )
        period = next(f for f in detail["documents"][0]["fields"] if f["name"] == "pay_period")
        assert (
            period["value"] == "September 2026"
            and period["corrected_by"] == "r1"
            and row.corrections["0"]["pay_period"]["by"] == "r1"
        )
        await store.correct(TENANT, row.id, document_index=0, field="branch", value="Pune Main", user_id="r1")
        for kwargs, code in (
            ({"document_index": 9, "field": "net_pay"}, "document_index_unknown"),
            ({"document_index": 0, "field": "nope"}, "field_unknown"),
        ):
            with pytest.raises(DocumentError) as info:
                await store.correct(TENANT, row.id, value="x", user_id="r1", **kwargs)
            assert info.value.code == code
        decided = await store.decide(TENANT, row.id, decision="approve", user_id="r2", notes="checked")
        assert decided["status"] == "approved" and row.reviewed_by == "r2" and row.review_notes == "checked"
        with pytest.raises(DocumentError) as info:
            await store.correct(TENANT, row.id, document_index=0, field="net_pay", value="1", user_id="r1")
        assert info.value.code == "decided"
        with pytest.raises(DocumentError) as info:
            await store.decide(TENANT, row.id, decision="reject", user_id="r2")
        assert info.value.code == "decided"
        with pytest.raises(DocumentError):
            await store.decide(TENANT, row.id, decision="shred", user_id="r2")
        listed = await store.list_documents(TENANT, status="approved")
        assert listed[0]["corrections"] == 2 and listed[0]["status"] == "approved"
        assert (await store.get_document(TENANT, row.id))["image_dpi"] == store.IMAGE_DPI
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        assert await store.get_document(TENANT, uuid.uuid4()) is None
        with pytest.raises(DocumentError):
            await store.page_image(TENANT, uuid.uuid4(), 1)


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_review_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import idp_review as api

        monkeypatch.setattr(settings, "idp_enabled", False)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "r1"}))
        for call in (
            api.list_documents(status=None, limit=10, tenant_id=str(TENANT)),
            api.get_document(uuid.uuid4(), tenant_id=str(TENANT)),
            api.page_image(uuid.uuid4(), 1, tenant_id=str(TENANT)),
            api.correct_field(uuid.uuid4(), api.CorrectionIn(field="x"), request, tenant_id=str(TENANT)),
            api.decide_document(uuid.uuid4(), api.DecisionIn(decision="approve"), request, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_routes_serve_the_store(self, monkeypatch):
        from api.v1 import idp_review as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        monkeypatch.setattr(store, "list_documents", AsyncMock(return_value=[{"id": "d"}]))
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value={"id": "d", "documents": []}))
        monkeypatch.setattr(store, "page_image", AsyncMock(return_value=b"\x89PNG"))
        monkeypatch.setattr(store, "correct", AsyncMock(return_value={"id": "d", "documents": [{"fields": []}]}))
        monkeypatch.setattr(store, "decide", AsyncMock(return_value={"id": "d", "status": "rejected"}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "r1"}))
        assert (await api.list_documents(status="review", limit=10, tenant_id=str(TENANT)))["total"] == 1
        with pytest.raises(HTTPException) as info:
            await api.list_documents(status="lost", limit=10, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        assert (await api.get_document(uuid.uuid4(), tenant_id=str(TENANT)))["id"] == "d"
        image = await api.page_image(uuid.uuid4(), 1, tenant_id=str(TENANT))
        assert image.media_type == "image/png" and image.body == b"\x89PNG"
        corrected = await api.correct_field(
            uuid.uuid4(), api.CorrectionIn(document_index=0, field="net_pay", value="1"), request, tenant_id=str(TENANT)
        )
        assert corrected["id"] == "d" and store.correct.call_args.kwargs["user_id"] == "r1"
        assert (
            await api.decide_document(
                uuid.uuid4(), api.DecisionIn(decision="reject", notes="n"), request, tenant_id=str(TENANT)
            )
        )["status"] == "rejected"
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.get_document(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_analyse_keeps_the_document_when_asked(self, monkeypatch):
        from api.v1 import idp as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        monkeypatch.setattr(api.pipeline, "process", lambda *_a, **_k: _result())
        saved = AsyncMock(return_value={"id": "kept-1", "status": "review"})
        monkeypatch.setattr(store, "save", saved)
        upload = SimpleNamespace(
            filename="slip.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"%PDF-x")
        )
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        answer = await api.analyse(upload, request, ocr=True, with_words=False, store=True, tenant_id=str(TENANT))
        assert (
            answer["document_id"] == "kept-1"
            and answer["status"] == "review"
            and saved.call_args.kwargs["created_by"] == "u1"
        )
        answer = await api.analyse(upload, request, ocr=True, with_words=False, store=False, tenant_id=str(TENANT))
        assert "document_id" not in answer and saved.await_count == 1
