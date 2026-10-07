# SPDX-License-Identifier: Apache-2.0
"""Document processing: pages and boxes, classification, bundle splitting, fields, tables, routing and the routes."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.idp import bundle, classify, fields, pages, pipeline, tables
from core.idp.pages import Page, Word
from tests.unit.idp_pdf_fixture import make_pdf

STATEMENT = [
    "ACCOUNT STATEMENT",
    "Account Number: 123456789012",
    "Account Holder: A. Example",
    "Statement Period: 01/09/2026 to 30/09/2026",
    "IFSC: EXMP0001234",
    "Opening Balance: 12,500.00",
    "Date        Description        Debit     Credit    Balance",
    "02/09/2026  Salary credit               45,000    57,500",
    "05/09/2026  Card payment       2,300              55,200",
    "09/09/2026  Transfer to Ravi   5,000              50,200",
    "Closing Balance: 50,200.00",
]
SLIP = [
    "SALARY SLIP for September 2026",
    "Employee Name: A. Example",
    "Employee ID: EMP-0042",
    "Employer: Example Industries Ltd",
    "Pay Period: September 2026",
    "Basic Pay 30,000",
    "HRA 12,000",
    "Gross Pay: 45,000",
    "Net Pay: 41,250",
]


def _pdf(*documents: list[str], table_columns: bool = True, width: int = 595, height: int = 842) -> bytes:
    """A PDF with one page per document, each line placed so word boxes are real."""
    return make_pdf(*documents, table_columns=table_columns, width=width, height=height)


def _png() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (400, 200), "white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class TestPages:
    def test_a_text_pdf_yields_words_with_boxes_and_lines(self):
        result = pages.load_pages(_pdf(STATEMENT), "application/pdf")
        assert len(result) == 1 and result[0].source == "text" and result[0].ocr == "not_needed"
        page = result[0]
        assert page.words and all(w.confidence == 1.0 for w in page.words)
        assert page.lines[0].text == "ACCOUNT STATEMENT" and page.lines[0].bbox[0] >= 59
        assert "Account Number: 123456789012" in page.text and page.confidence == 1.0

    def test_grouping_words_into_lines_by_vertical_position(self):
        words = [Word("b", (50, 10, 60, 20)), Word("a", (10, 11, 20, 21)), Word("c", (10, 40, 20, 50))]
        lines = pages.group_lines(words)
        assert [line.text for line in lines] == ["a b", "c"] and lines[0].bbox == (10, 10, 60, 21)
        assert pages.group_lines([]) == []

    def test_a_scanned_page_is_ocrd_when_the_engine_is_there_and_says_so_when_it_is_not(self, monkeypatch):
        blank = _pdf([], width=300, height=300)  # no text layer
        monkeypatch.setattr(pages, "ocr_available", lambda: False)
        result = pages.load_pages(blank, "application/pdf")
        assert result[0].source == "empty" and result[0].ocr == "unavailable"

        monkeypatch.setattr(pages, "ocr_available", lambda: True)
        monkeypatch.setattr(
            pages,
            "ocr_words",
            lambda image, scale=1.0: ([Word("SALARY", (10 / scale, 10 / scale, 80 / scale, 30 / scale), 0.9)], "Latin"),
        )
        result = pages.load_pages(blank, "application/pdf")
        assert result[0].source == "ocr" and result[0].ocr == "done" and result[0].script == "Latin"
        assert result[0].words[0].text == "SALARY" and result[0].words[0].bbox[0] < 10
        assert pages.load_pages(blank, "application/pdf", ocr=False)[0].ocr == "not_needed"

    def test_images_are_one_page_and_bad_files_are_refused(self, monkeypatch):
        monkeypatch.setattr(pages, "ocr_available", lambda: False)
        result = pages.load_pages(_png(), "image/png")
        assert result[0].width == 400 and result[0].ocr == "unavailable"
        for stream, mime, code in (
            (b"", "application/pdf", "empty_file"),
            (b"nope", "text/plain", "unsupported_type"),
            (b"%PDF-broken", "application/pdf", "pdf_unreadable"),
            (b"xx", "image/png", "image_unreadable"),
        ):
            with pytest.raises(pages.DocumentError) as info:
                pages.load_pages(stream, mime)
            assert info.value.code == code
        with pytest.raises(pages.DocumentError) as info:
            pages.load_pages(b"x" * (pages.MAX_BYTES + 1), "application/pdf")
        assert info.value.status == 413


class TestClassify:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("\n".join(STATEMENT), "bank_statement"),
            ("\n".join(SLIP), "salary_slip"),
            ("TAX INVOICE Invoice No: INV-7 GSTIN 27ABCDE1234F1Z5 Grand Total 2,832", "invoice"),
            (
                "LOAN APPLICATION FORM Applicant Name: A. Example Loan Amount: 5,00,000 Tenure: 36 months",
                "loan_application",
            ),
            ("Income Tax Department Permanent Account Number ABCDE1234F Date of Birth 01/01/1990", "government_id"),
            ("This Agreement is made between the parties WHEREAS IN WITNESS WHEREOF", "agreement"),
        ],
    )
    def test_types_are_recognised(self, text, expected):
        result = classify.classify(text)
        assert result.document_type == expected and result.confidence >= classify.MIN_CONFIDENCE

    def test_a_page_the_rules_cannot_type_is_unknown_not_a_guess(self):
        result = classify.classify("Thank you for banking with us. Page 2 of 3.")
        assert result.document_type == classify.UNKNOWN and result.confidence < classify.MIN_CONFIDENCE
        assert classify.classify("").document_type == classify.UNKNOWN
        assert len(classify.catalogue()) == len(classify.CATALOGUE)


class TestBundle:
    def test_a_file_of_two_documents_is_split_and_continuations_join_the_document_before(self):
        result = pages.load_pages(
            _pdf(STATEMENT, ["Thank you for banking with us.", "Page 2 of 2"], SLIP), "application/pdf"
        )
        segments = bundle.split(result)
        assert [s.document_type for s in segments] == ["bank_statement", "salary_slip"]
        assert segments[0].pages == [1, 2] and segments[1].pages == [3]
        assert 0 < segments[0].confidence < segments[1].confidence or segments[0].confidence > 0

    def test_two_statements_in_a_row_become_two_segments(self):
        result = pages.load_pages(_pdf(STATEMENT, STATEMENT), "application/pdf")
        segments = bundle.split(result)
        assert len(segments) == 2 and all(s.document_type == "bank_statement" for s in segments)

    def test_untyped_leading_pages_join_the_first_typed_document(self):
        result = pages.load_pages(_pdf(["Cover sheet"], SLIP), "application/pdf")
        segments = bundle.split(result)
        assert len(segments) == 1 and segments[0].document_type == "salary_slip" and segments[0].pages == [1, 2]
        assert bundle.split([]) == []


class TestFields:
    def test_statement_fields_come_with_a_box_and_a_confidence(self):
        result = pages.load_pages(_pdf(STATEMENT), "application/pdf")
        found = {f.name: f for f in fields.extract("bank_statement", result)}
        assert found["account_number"].value == "123456789012" and found["account_number"].status == "found"
        assert found["opening_balance"].value == "12500.00" and found["closing_balance"].value == "50200.00"
        assert found["ifsc"].value == "EXMP0001234" and found["statement_period"].value.startswith("01/09/2026")
        assert found["account_number"].page == 1 and found["account_number"].bbox is not None
        assert found["account_number"].bbox[0] > 60 and found["account_number"].confidence >= pipeline.FIELD_FLOOR

    def test_a_value_on_the_next_line_and_a_missing_required_field(self):
        page = Page(number=1, width=595, height=842)
        y = 50
        for line in ("Employee Name", "A. Example", "Net Pay: 41,250"):
            x = 60
            for token in line.split():
                page.words.append(Word(token, (x, y, x + 8 * len(token), y + 12), 0.8))
                x += 8 * len(token) + 6
            y += 20
        found = {f.name: f for f in fields.extract("salary_slip", [page])}
        assert found["employee_name"].value == "A. Example" and found["employee_name"].confidence < 0.9
        assert (
            found["net_pay"].value == "41250"
            and found["pay_period"].status == "missing"
            and found["pay_period"].required
        )

    def test_generic_label_value_lines_are_picked_up_once(self):
        result = pages.load_pages(
            _pdf(["Branch: Pune Main", "Branch: Pune Main", "Currency: INR", "Just a sentence here."]),
            "application/pdf",
        )
        extra = fields.generic(result, known={"currency"})
        assert [f.name for f in extra] == ["branch"] and extra[0].value == "Pune Main" and extra[0].bbox is not None
        assert fields.normalise_value("₹ 1,23,456.00", "amount") == "123456.00"


class TestTables:
    def test_aligned_lines_form_a_table_with_a_header(self):
        result = pages.load_pages(_pdf(STATEMENT), "application/pdf")
        found = tables.extract(result)
        assert found and found[0].method == "words" and found[0].header[0] == "Date"
        assert found[0].rows[0][0] == "02/09/2026" and found[0].to_dict()["row_count"] == 3
        assert found[0].page == 1 and found[0].bbox[1] > 0

    def test_no_table_without_enough_aligned_rows(self):
        result = pages.load_pages(_pdf(["Only one row here  two"], table_columns=True), "application/pdf")
        assert tables.extract(result) == []


class TestPipeline:
    def test_the_pipeline_types_documents_extracts_and_routes(self, monkeypatch):
        monkeypatch.setattr(settings, "idp_enabled", True)
        result = pipeline.process(_pdf(STATEMENT, SLIP), "application/pdf")
        assert [d["document_type"] for d in result["documents"]] == ["bank_statement", "salary_slip"]
        statement = result["documents"][0]
        assert statement["review"]["needed"] is False and statement["tables"]
        assert any(f["name"] == "closing_balance" and f["value"] == "50200.00" for f in statement["fields"])
        assert result["review"]["needed"] is False and result["ocr"]["available"] is True
        assert result["pages"][0]["lines"][0]["text"] == "ACCOUNT STATEMENT" and "word_boxes" not in result["pages"][0]
        with_words = pipeline.process(_pdf(STATEMENT), "application/pdf", with_words=True)
        assert with_words["pages"][0]["word_boxes"][0]["text"] == "ACCOUNT"

    def test_unknown_types_missing_fields_and_unread_pages_need_review(self, monkeypatch):
        monkeypatch.setattr(pages, "ocr_available", lambda: False)
        blank = _pdf([])
        result = pipeline.process(blank, "application/pdf")
        assert result["review"]["needed"] is True
        reasons = result["documents"][0]["review"]["reasons"]
        assert "document type not recognised" in reasons and any(r.startswith("pages not read") for r in reasons)
        assert result["ocr"]["unavailable"] == [1]
        weak = pipeline.review_of(
            "salary_slip",
            0.9,
            [
                fields.Field("net_pay", None, 0.0, status="missing"),
                fields.Field("employee_name", "x", 0.5, status="weak"),
            ],
            [],
        )
        assert weak == {
            "needed": True,
            "reasons": ["required field net_pay not found", "field employee_name confidence 0.50 below 0.7"],
        }


class TestRoutes:
    @pytest.mark.asyncio
    async def test_document_types_answer_and_the_rest_is_not_found_while_off(self, monkeypatch):
        from api.v1 import idp as api

        monkeypatch.setattr(settings, "idp_enabled", False)
        listed = await api.document_types(tenant_id="t")
        assert listed["enabled"] is False and any(
            d["name"] == "bank_statement" and d["fields"] for d in listed["document_types"]
        )
        upload = SimpleNamespace(
            filename="x.pdf", content_type="application/pdf", read=AsyncMock(return_value=_pdf(STATEMENT))
        )
        with pytest.raises(HTTPException) as info:
            await api.analyse(upload, ocr=True, with_words=False, tenant_id="t")
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_analyse_processes_the_upload_and_refuses_bad_files(self, monkeypatch):
        from api.v1 import idp as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        upload = SimpleNamespace(
            filename="bundle.pdf", content_type="application/pdf", read=AsyncMock(return_value=_pdf(SLIP))
        )
        answer = await api.analyse(upload, ocr=True, with_words=False, tenant_id="t")
        assert answer["filename"] == "bundle.pdf" and answer["documents"][0]["document_type"] == "salary_slip"
        bad = SimpleNamespace(filename="x.txt", content_type="text/plain", read=AsyncMock(return_value=b"hello"))
        with pytest.raises(HTTPException) as info:
            await api.analyse(bad, ocr=True, with_words=False, tenant_id="t")
        assert info.value.status_code == 415
        assert (await api.classify_text({"text": "\n".join(SLIP)}, tenant_id="t"))["document_type"] == "salary_slip"
        with pytest.raises(HTTPException):
            await api.classify_text({"text": ""}, tenant_id="t")


class TestReviewHardening:
    def test_negative_amounts_keep_their_sign(self):
        assert fields.normalise_value("-1,234.00", "amount") == "-1234.00"
        assert fields.normalise_value("₹ -12,500.50", "amount") == "-12500.50"
        assert fields.normalise_value("Rs. 500", "amount") == "500"
        assert fields.normalise_value("41,250.", "amount") == "41250"
        page = Page(number=1, width=595, height=842)
        x = 60
        for token in ("Closing", "Balance:", "-1,234.00"):
            page.words.append(Word(token, (x, 50, x + 8 * len(token), 62)))
            x += 8 * len(token) + 6
        found = {f.name: f for f in fields.extract("bank_statement", [page])}
        assert found["closing_balance"].value == "-1234.00"

    def test_a_pdf_over_the_page_limit_is_refused_not_truncated(self, monkeypatch):
        monkeypatch.setattr(pages, "MAX_PAGES", 2)
        with pytest.raises(pages.DocumentError) as info:
            pages.load_pages(_pdf(STATEMENT, SLIP, SLIP), "application/pdf")
        assert info.value.status == 413 and info.value.code == "too_many_pages"
        assert len(pages.load_pages(_pdf(STATEMENT, SLIP), "application/pdf")) == 2

    def test_a_repeated_heading_alone_does_not_split_a_statement(self):
        first = [
            "ACCOUNT STATEMENT",
            "Account Number: 123456789012",
            "Opening Balance: 12,500.00",
            "02/09/2026  Salary credit  45,000  57,500",
        ]
        middle = ["ACCOUNT STATEMENT", "Account Number: 123456789012", "05/09/2026  Card payment  2,300  55,200"]
        last = [
            "ACCOUNT STATEMENT",
            "Account Number: 123456789012",
            "09/09/2026  Transfer  5,000  50,200",
            "Closing Balance: 50,200.00",
        ]
        result = pages.load_pages(_pdf(first, middle, last), "application/pdf")
        assert all(classify.classify(p.text).first_page for p in result)
        segments = bundle.split(result)
        assert len(segments) == 1 and segments[0].pages == [1, 2, 3]
        assert segments[0].document_type == "bank_statement"

    def test_a_differing_identifying_field_splits_a_bundle(self):
        one = ["ACCOUNT STATEMENT", "Account Number: 123456789012", "Opening Balance: 1,000.00"]
        two = ["ACCOUNT STATEMENT", "Account Number: 999988887777", "Opening Balance: 2,000.00"]
        segments = bundle.split(pages.load_pages(_pdf(one, two), "application/pdf"))
        assert [s.pages for s in segments] == [[1], [2]]

    def test_page_numbering_decides_when_the_pages_carry_it(self):
        head = ["ACCOUNT STATEMENT", "Account Number: 123456789012"]
        result = pages.load_pages(
            _pdf(
                [*head, "Closing Balance: 10.00", "Page 1 of 2"],
                [*head, "Page 2 of 2"],
                [*head, "Page 1 of 1"],
            ),
            "application/pdf",
        )
        # Page 2 continues the numbering even though page 1 ends with a closing balance; page 3 restarts it.
        assert [s.pages for s in bundle.split(result)] == [[1, 2], [3]]

    @pytest.mark.asyncio
    async def test_the_upload_is_read_with_a_bound_and_oversize_is_refused(self, monkeypatch):
        from api.v1 import idp as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        asked: list[int] = []

        async def read(size: int = -1) -> bytes:
            asked.append(size)
            return b"%PDF-" + b"0" * (size - 5)

        upload = SimpleNamespace(filename="big.pdf", content_type="application/pdf", read=read)
        with pytest.raises(HTTPException) as info:
            await api.analyse(upload, ocr=True, with_words=False, tenant_id="t")
        assert info.value.status_code == 413 and info.value.detail["error"] == "too_large"
        assert asked == [pages.MAX_BYTES + 1]

        declared = SimpleNamespace(
            filename="big.pdf", content_type="application/pdf", size=pages.MAX_BYTES + 1, read=AsyncMock()
        )
        with pytest.raises(HTTPException) as info:
            await api.analyse(declared, ocr=True, with_words=False, tenant_id="t")
        assert info.value.status_code == 413
        declared.read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processing_runs_off_the_event_loop(self, monkeypatch):
        import threading

        from api.v1 import idp as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        seen: dict[str, object] = {}

        def fake_process(stream, mime, *, ocr, with_words):
            seen["thread"] = threading.current_thread()
            seen["args"] = (stream, mime, ocr, with_words)
            return {"documents": []}

        monkeypatch.setattr(pipeline, "process", fake_process)
        upload = SimpleNamespace(
            filename="a.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"%PDF-1")
        )
        answer = await api.analyse(upload, ocr=False, with_words=True, tenant_id="t")
        assert answer == {"filename": "a.pdf", "documents": []}
        assert seen["thread"] is not threading.main_thread() and seen["args"] == (
            b"%PDF-1",
            "application/pdf",
            False,
            True,
        )


class TestDocumentScopes:
    def test_the_documents_family_maps_to_review_queue_scopes(self):
        from api.route_enforcement import SCOPE_FAMILIES, required_scopes_for, unmapped_scope_families
        from core.rbac import ROLE_SCOPES

        assert SCOPE_FAMILIES["documents"] == ("approvals:read", "approvals:write")
        assert required_scopes_for("documents.read", "GET") == ("approvals:read",)
        assert required_scopes_for("documents.analyse.sensitive.write", "POST") == ("approvals:write",)
        assert required_scopes_for("documents.read", "POST") == ("approvals:write",)
        assert "documents" not in unmapped_scope_families(["documents.read", "documents.analyse.sensitive.write"])
        writers = {role for role, scopes in ROLE_SCOPES.items() if "approvals:write" in scopes}
        assert {"cfo", "chro", "cmo", "coo", "domain_lead", "developer"} <= writers
        assert "auditor" not in writers and "analyst" not in writers

    def test_the_routes_declare_scopes_in_the_documents_family(self):
        from api.route_metadata import ROUTE_METADATA_ATTR
        from api.v1 import idp as api

        for handler in (api.document_types, api.analyse, api.classify_text):
            scope = getattr(handler, ROUTE_METADATA_ATTR)["scope"]
            assert scope.split(".", 1)[0] == "documents"
