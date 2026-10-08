# SPDX-License-Identifier: Apache-2.0
"""Document analysis: reconciliation across documents, stamp detection, the report, and the routes."""

from __future__ import annotations

import io
import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.idp import reconcile, report, stamps, store
from core.idp.pages import DocumentError

TENANT = uuid.uuid4()


def _doc(index, kind, **fields):
    return {
        "index": index,
        "document_type": kind,
        "confidence": 0.9,
        "pages": [index + 1],
        "fields": [
            {
                "name": name,
                "value": value,
                "confidence": 0.9,
                "page": index + 1,
                "bbox": [1, 2, 3, 4],
                "status": "found",
                "required": True,
            }
            for name, value in fields.items()
        ],
        "extra_fields": [],
        "tables": [],
        "review": {"needed": False, "reasons": []},
    }


class TestReconcile:
    def test_names_dates_ids_and_amounts_are_compared_after_normalisation(self):
        assert reconcile.names_agree("Mr. A. K. Example", "example a k")
        assert reconcile.names_agree("Anil Kumar Example", "Anil Example")
        assert reconcile.names_agree("A. Example", "Anil Example")
        assert not reconcile.names_agree("Anil Example", "Sunil Example")
        assert reconcile.agree("date", "01/01/1990", "1990-01-01") and reconcile.agree(
            "date", "1 January 1990", "01-01-1990"
        )
        assert not reconcile.agree("date", "01/01/1990", "02/01/1990") and not reconcile.agree("date", "nope", "nope")
        assert reconcile.agree("id", "abcde 1234 f", "ABCDE1234F") and not reconcile.agree("id", "", "")
        assert reconcile.agree("amount", "41,250", "41250.00") and reconcile.agree("amount", "100000", "101000")
        assert not reconcile.agree("amount", "100000", "120000") and not reconcile.agree("amount", "x", "1")

    def test_agreement_is_required_between_every_pair_of_values(self):
        # An initial matches both full names, but the full names do not match each other.
        documents = [
            _doc(0, "government_id", name="A Example"),
            _doc(1, "salary_slip", employee_name="Anil Example"),
            _doc(2, "kyc_form", customer_name="Arun Example"),
        ]
        assert reconcile.names_agree("A Example", "Anil Example") and reconcile.names_agree("A Example", "Arun Example")
        result = reconcile.reconcile(documents)
        assert "name" not in result["agreements"] and result["disagreements"][0]["item"] == "name"
        consistent = reconcile.reconcile(documents[:2] + [_doc(2, "kyc_form", customer_name="Anil K Example")])
        assert "name" in consistent["agreements"] and consistent["consistent"] is True

    def test_two_digit_birth_years_resolve_to_the_latest_century_not_in_the_future(self):
        today = date(2026, 10, 7)
        assert reconcile.normalise_date("01/01/90", today=today) == "1990-01-01"
        assert reconcile.normalise_date("15-08-05", today=today) == "2005-08-15"
        assert reconcile.normalise_date("01/10/26", today=today) == "2026-10-01"
        assert reconcile.normalise_date("01/12/26", today=today) == "1926-12-01"
        assert reconcile.normalise_date("31/02/90", today=today) is None
        assert reconcile.agree("date", "01/01/90", "1990-01-01")
        documents = [
            _doc(0, "government_id", date_of_birth="01/01/90"),
            _doc(1, "kyc_form", date_of_birth="1990-01-01"),
        ]
        assert reconcile.reconcile(documents)["agreements"] == ["date_of_birth"]

    def test_amounts_keep_their_sign(self):
        assert reconcile.normalise_amount("-1,000") == -1000.0 and reconcile.normalise_amount("1000") == 1000.0
        assert reconcile.normalise_amount("Rs. -1,000") == -1000.0 and reconcile.normalise_amount("-₹ 12.50") == -12.5
        assert reconcile.normalise_amount("−7") == -7.0 and reconcile.normalise_amount("₹41,250/-") == 41250.0
        assert reconcile.normalise_amount("10-20") is None and reconcile.normalise_amount("") is None
        assert not reconcile.agree("amount", "-1000", "1000") and reconcile.agree("amount", "-1000", "-1,000.00")
        documents = [
            _doc(0, "salary_slip", net_pay="-41250"),
            _doc(1, "salary_slip", net_pay="41250"),
        ]
        assert reconcile.reconcile(documents)["disagreements"][0]["item"] == "net_pay"

    def test_disagreements_name_every_value_with_its_document_and_box(self):
        documents = [
            _doc(0, "government_id", name="Anil Example", id_number="ABCDE1234F", date_of_birth="01/01/1990"),
            _doc(1, "salary_slip", employee_name="Anil Example", net_pay="41250"),
            _doc(2, "kyc_form", customer_name="Sunil Example", date_of_birth="1990-01-01"),
            _doc(3, "tax_return", pan="ABCDE1234F"),
        ]
        result = reconcile.reconcile(documents)
        assert result["consistent"] is False and result["highest_severity"] == "high"
        assert result["agreements"] == ["date_of_birth", "pan"] and result["unverified"] == ["net_pay"]
        assert result["absent"] == ["employer", "loan_amount"]
        names = result["disagreements"][0]
        assert names["item"] == "name" and [v["document_type"] for v in names["values"]] == [
            "government_id",
            "salary_slip",
            "kyc_form",
        ]
        assert names["values"][2]["value"] == "Sunil Example" and names["values"][2]["bbox"] == [1, 2, 3, 4]
        clean = reconcile.reconcile(documents[:2] + [documents[3]])
        assert clean["consistent"] is True and clean["highest_severity"] is None


class TestStamps:
    @staticmethod
    def _page(with_stamp: bool):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        for y in range(80, 500, 30):
            draw.rectangle((60, y, 400, y + 10), fill="black")  # text-like black lines
        if with_stamp:
            draw.ellipse((380, 560, 540, 720), outline=(40, 60, 200), width=14)
            draw.ellipse((410, 590, 510, 690), fill=(60, 80, 210))
        return image

    def test_a_coloured_region_is_a_candidate_and_black_text_is_not(self):
        found = stamps.detect(self._page(True), page_number=1, page_size=(595, 842))
        assert found and found[0].colour == "blue" and found[0].confidence >= 0.5
        box = found[0].bbox
        assert box[0] > 300 and box[1] > 500 and box[2] <= 595 and box[3] <= 842
        assert stamps.detect(self._page(False), page_number=1, page_size=(595, 842)) == []
        verdict = stamps.verify("cheque", found)
        assert verdict["status"] == "present" and verdict["expected"] == ["bank"]
        assert (
            stamps.verify("cheque", [])["status"] == "missing"
            and stamps.verify("invoice", [])["status"] == "not_expected"
        )
        assert (
            stamps.colour_name(0.0) == "red"
            and stamps.colour_name(0.33) == "green"
            and stamps.colour_name(0.8) == "purple"
        )

    def test_png_input_and_text_overlap_lower_the_confidence(self):
        buffer = io.BytesIO()
        self._page(True).save(buffer, format="PNG")
        plain = stamps.detect_from_png(buffer.getvalue(), page_number=2, page_size=(595, 842))
        overlapped = stamps.detect_from_png(
            buffer.getvalue(), page_number=2, page_size=(595, 842), text_boxes=[(300, 500, 595, 842)]
        )
        assert (
            plain[0].page == 2
            and overlapped[0].overlaps_text is True
            and overlapped[0].confidence < plain[0].confidence
        )


class TestReport:
    def test_the_report_and_its_markdown_say_what_was_found(self):
        detail = {
            "id": "d1",
            "filename": "bundle.pdf",
            "status": "review",
            "pages": 2,
            "review_reasons": ["required field pay_period not found"],
            "documents": [
                _doc(0, "government_id", name="Anil Example", id_number="ABCDE1234F", date_of_birth="01/01/1990"),
                {
                    **_doc(1, "salary_slip", employee_name="Sunil Example", net_pay="41250"),
                    "fields": _doc(1, "salary_slip", employee_name="Sunil Example", net_pay="41250")["fields"]
                    + [
                        {
                            "name": "pay_period",
                            "value": None,
                            "confidence": 0,
                            "page": None,
                            "bbox": None,
                            "status": "missing",
                            "required": True,
                        }
                    ],
                },
            ],
        }
        stamp_result = {
            "pages": [
                {"page": 1, "status": "present", "candidates": [{"page": 1}]},
                {"page": 2, "status": "missing", "candidates": []},
            ]
        }
        built = report.build(detail, stamps=stamp_result)
        assert (
            built["documents"][1]["missing_fields"] == ["pay_period"]
            and built["reconciliation"]["disagreements"][0]["item"] == "name"
        )
        text = built["narrative"]
        assert "holds 2 documents" in text and "Not found: pay_period on the salary slip." in text
        assert "disagree on: name" in text and "found on page 1" in text and "not found on page 2" in text
        assert text.endswith("The file is waiting for a person.")
        markdown = report.to_markdown(built)
        assert (
            markdown.startswith("# Document analysis: bundle.pdf")
            and "| id_number | ABCDE1234F | 90% | found |" in markdown
        )
        assert "- name (high): government_id p1: Anil Example; salary_slip p2: Sunil Example" in markdown
        assert "- Page 2: missing (0 ink region(s))" in markdown and "- required field pay_period not found" in markdown
        empty = report.build({"id": "d2", "status": "approved", "documents": []})
        assert empty["narrative"] == "The file holds no recognised document. The file was approved."

    def test_corrected_fields_are_neither_missing_nor_weak(self):
        document = _doc(0, "salary_slip", employee_name="Anil Example", net_pay="41250")
        document["fields"] += [
            # As the store returns them: the reviewer's value applied, the extraction status kept.
            {"name": "pay_period", "value": "September 2026", "original_value": None, "status": "missing",
             "required": True, "corrected": True, "confidence": 0, "page": None},
            {"name": "employer", "value": "Example Works", "original_value": "Exampl Wrks", "status": "weak",
             "required": True, "corrected": True, "confidence": 0.4, "page": 1},
            {"name": "gross_pay", "value": "50000", "status": "weak", "required": False, "corrected": False,
             "confidence": 0.4, "page": 1},
            {"name": "branch", "value": None, "status": "missing", "required": True, "corrected": False,
             "confidence": 0, "page": None},
        ]  # fmt: skip
        built = report.build({"id": "d3", "status": "review", "documents": [document]})
        section = built["documents"][0]
        assert section["missing_fields"] == ["branch"] and section["weak_fields"] == ["gross_pay"]
        assert "Not found: branch on the salary slip." in built["narrative"] and "pay_period" not in built["narrative"]
        period = next(f for f in section["key_fields"] if f["name"] == "pay_period")
        assert period["corrected"] is True and period["value"] == "September 2026"
        assert "| pay_period | September 2026 |" in report.to_markdown(built)


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_analysis_routes_serve_reconciliation_stamps_and_the_report(self, monkeypatch):
        from api.v1 import idp_analysis as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        detail = {
            "id": "d1",
            "filename": "f.pdf",
            "status": "review",
            "pages": 1,
            "review_reasons": [],
            "pages_detail": [
                {"number": 1, "width": 595, "height": 842, "lines": [{"text": "x", "bbox": [10, 10, 50, 20]}]}
            ],
            "documents": [_doc(0, "cheque", name="A")],
        }
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=detail))
        monkeypatch.setattr(store, "document_content", AsyncMock(return_value=(b"file", "application/pdf")))
        monkeypatch.setattr(store, "render_pages", lambda _data, _mime, numbers: ((n, b"png") for n in numbers))
        candidate = stamps.Candidate(1, (300, 500, 400, 600), "blue", 0.02, 0.5, 0.8)
        monkeypatch.setattr(stamps, "detect_from_png", lambda *_a, **_k: [candidate])

        recon = await api.reconcile_document(uuid.uuid4(), tenant_id=str(TENANT))
        assert recon["consistent"] is True and "name" in recon["absent"]
        found = await api.stamps_of_document(uuid.uuid4(), tenant_id=str(TENANT))
        assert found["present_on"] == [1] and found["pages"][0]["document_type"] == "cheque"
        built = await api.report_of_document(uuid.uuid4(), output="json", with_stamps=True, tenant_id=str(TENANT))
        assert built["stamps"]["present_on"] == [1] and "found on page 1" in built["narrative"]
        markdown = await api.report_of_document(
            uuid.uuid4(), output="markdown", with_stamps=False, tenant_id=str(TENANT)
        )
        assert markdown.media_type == "text/markdown" and markdown.body.startswith(b"# Document analysis")

        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.reconcile_document(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
        monkeypatch.setattr(settings, "idp_enabled", False)
        with pytest.raises(HTTPException) as info:
            await api.report_of_document(uuid.uuid4(), output="json", with_stamps=False, tenant_id=str(TENANT))
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_stamp_scan_reads_and_parses_the_file_once_for_all_pages(self, monkeypatch):
        from api.v1 import idp_analysis as api
        from tests.unit.idp_pdf_fixture import make_pdf

        pdf = make_pdf(["one"], ["two"], ["three"], table_columns=False)
        content = AsyncMock(return_value=(pdf, "application/pdf"))
        monkeypatch.setattr(store, "document_content", content)
        monkeypatch.setattr(store, "page_image", AsyncMock(side_effect=AssertionError("loaded per page")))
        opened: list[int] = []
        real_open = store.open_pdf
        monkeypatch.setattr(store, "open_pdf", lambda data: opened.append(len(data)) or real_open(data))
        seen: list[int] = []
        monkeypatch.setattr(stamps, "detect_from_png", lambda png, page_number, **_k: seen.append(page_number) or [])
        detail = {
            "pages_detail": [{"number": n, "width": 595, "height": 842, "lines": []} for n in (1, 2, 3, 9)],
            "documents": [{**_doc(0, "cheque"), "pages": [1, 2, 3]}],
        }
        result = await api.stamp_check(str(TENANT), uuid.uuid4(), detail)
        assert content.await_count == 1 and opened == [len(pdf)] and seen == [1, 2, 3]
        assert result["missing_on"] == [1, 2, 3] and [p["page"] for p in result["pages"]] == [1, 2, 3]

        content.side_effect = DocumentError(404, "not_found", "No such document")
        assert (await api.stamp_check(str(TENANT), uuid.uuid4(), detail))["pages"] == []

    def test_pages_render_lazily_from_one_open_of_the_file(self, monkeypatch):
        from tests.unit.idp_pdf_fixture import make_pdf

        pdf = make_pdf(["one"], ["two"], table_columns=False)
        opened: list[int] = []
        real_open = store.open_pdf
        monkeypatch.setattr(store, "open_pdf", lambda data: opened.append(1) or real_open(data))
        rendered = list(store.render_pages(pdf, "application/pdf", [2, 1, 2, 7]))
        assert [n for n, _png in rendered] == [1, 2] and all(png[:4] == b"\x89PNG" for _n, png in rendered)
        assert opened == [1]
        assert store.render_page(pdf, "application/pdf", 2)[:4] == b"\x89PNG"
        with pytest.raises(DocumentError):
            store.render_page(pdf, "application/pdf", 3)
