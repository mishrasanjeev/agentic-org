# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Onboarding desk applications: questions, documents, the store, the text conversation and the routes."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.onboarding_desk import applications, branding, conversation, documents, questions, store, tokens
from core.onboarding_desk.store import DeskError
from core.voice.runtime import build_twilio_signature

TENANT = uuid.uuid4()
LEAD = uuid.uuid4()
APP = uuid.uuid4()
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
TODAY = NOW.date()

US_ANSWERS = {
    "legal_name": "Northwind Coffee LLC",
    "business_structure": "llc",
    "registration_number": "202612345678",
    "tax_id": "12-3456789",
    "registered_address": {
        "lines": ["1 Market St"],
        "locality": "San Francisco",
        "region": "CA",
        "postal_code": "94105",
    },
    "activity": "food_service",
    "owners": [
        {
            "name": "Avery Example",
            "kind": "person",
            "role": "owner_and_controller",
            "ownership_pct": 60,
            "date_of_birth": "1980-04",
        },
        {"name": "Holdco Inc", "kind": "business", "role": "owner", "ownership_pct": 40},
    ],
}


# ── Questions ────────────────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize(
        ("item", "value", "country", "expected"),
        [
            ("legal_name", "  Northwind   Coffee LLC ", "US", "Northwind Coffee LLC"),
            ("trading_name", "Café Nord", "CA", "Café Nord"),
            ("business_structure", "2", "US", "llc"),
            ("business_structure", "Société par actions", "CA", "corporation"),
            ("business_structure", "SOLE_PROPRIETORSHIP", "US", "sole_proprietorship"),
            ("registration_number", "c 1234-567", "US", "C1234-567"),
            ("tax_id", "123456789", "US", "12-3456789"),
            ("tax_id", "12-3456789", "US", "12-3456789"),
            ("tax_id", "123 456 789 RT0001", "CA", "123456789"),
            ("website", "northwind.example", "US", "https://northwind.example"),
            ("activity", "Restauration", "CA", "food_service"),
        ],
    )
    def test_answers_are_normalised(self, item, value, country, expected):
        assert questions.validate_answer(item, value, country=country, today=TODAY) == expected

    @pytest.mark.parametrize(
        ("item", "value", "country", "code"),
        [
            ("legal_name", "x", "US", "required"),
            ("legal_name", "a" * 200, "US", "too_long"),
            ("legal_name", "Bad\x07Name", "US", "invalid_characters"),
            ("legal_name", "<script>", "US", "invalid_characters"),
            ("business_structure", "trust", "US", "not_a_choice"),
            ("registration_number", "!!", "US", "registration_invalid"),
            ("tax_id", "123", "US", "ein_invalid"),
            ("tax_id", "001234567", "US", "ein_invalid"),
            ("tax_id", "12345", "CA", "business_number_invalid"),
            ("website", "javascript:alert(1)", "US", "url_invalid"),
            ("website", "https://user:pw@x.example", "US", "url_invalid"),
            ("website", "localhost", "US", "url_invalid"),
            ("formation_document", "x", "US", "upload_required"),
            ("favourite_colour", "blue", "US", "item_unknown"),
            ("legal_name", "Acme", "GB", "country_not_supported"),
        ],
    )
    def test_answers_are_refused(self, item, value, country, code):
        with pytest.raises(questions.AnswerError) as caught:
            questions.validate_answer(item, value, country=country, today=TODAY)
        assert caught.value.code == code

    def test_addresses(self):
        us = questions.validate_answer(
            "registered_address",
            {"lines": "1 Market St", "locality": "SF", "region": "ca", "postal_code": "94105-1234"},
            country="US",
        )
        assert us == {"lines": ["1 Market St"], "locality": "SF", "region": "CA", "postal_code": "94105-1234"}
        ca = questions.validate_answer(
            "registered_address",
            {"lines": ["100 Rue Exemple", ""], "locality": "Montréal", "region": "QC", "postal_code": "h2x1y4"},
            country="CA",
        )
        assert ca["postal_code"] == "H2X 1Y4" and ca["lines"] == ["100 Rue Exemple"]
        for bad, code in (
            ({"lines": [], "locality": "x", "region": "CA", "postal_code": "94105"}, "address_invalid"),
            ({"lines": ["1"], "locality": "", "region": "CA", "postal_code": "94105"}, "address_invalid"),
            ({"lines": ["1"], "locality": "x", "region": "ZZ", "postal_code": "94105"}, "region_invalid"),
            ({"lines": ["1"], "locality": "x", "region": "CA", "postal_code": "9410"}, "postal_code_invalid"),
            ({"lines": ["1"], "locality": "x", "region": "CA", "postal_code": "94105", "x": 1}, "address_invalid"),
            ("1 Market St", "address_invalid"),
        ):
            with pytest.raises(questions.AnswerError) as caught:
                questions.validate_answer("registered_address", bad, country="US")
            assert caught.value.code == code
        with pytest.raises(questions.AnswerError, match="postal_code_invalid"):
            questions.validate_answer(
                "registered_address",
                {"lines": ["1"], "locality": "x", "region": "ON", "postal_code": "12345"},
                country="CA",
            )

    def test_owners(self):
        owners = questions.validate_answer("owners", US_ANSWERS["owners"], country="US", today=TODAY)
        assert owners[0]["date_of_birth"] == "1980-04" and owners[1]["date_of_birth"] is None
        assert owners[1]["ownership_pct"] == 40.0
        minimal = questions.validate_answer("owners", [{"name": "Avery Example"}], country="US", today=TODAY)
        assert minimal == [
            {"name": "Avery Example", "kind": "person", "role": "owner", "ownership_pct": None, "date_of_birth": None}
        ]
        for bad, code in (
            ([], "owners_invalid"),
            ([{"name": "A B", "kind": "robot"}], "owners_invalid"),
            ([{"name": "A B", "role": "boss"}], "owners_invalid"),
            ([{"name": "A B", "ssn": "123"}], "owners_invalid"),
            ([{"name": "A B", "ownership_pct": 101}], "ownership_invalid"),
            ([{"name": "A B", "ownership_pct": True}], "ownership_invalid"),
            ([{"name": "A B", "ownership_pct": 60}, {"name": "C D", "ownership_pct": 50}], "ownership_over_100"),
            ([{"name": "A B", "date_of_birth": "2020-01-01"}], "date_invalid"),
            ([{"name": "A B", "date_of_birth": "2030"}], "date_invalid"),
            ([{"name": "A B", "date_of_birth": "1880"}], "date_invalid"),
            ([{"name": "A B", "date_of_birth": "1980-02-30"}], "date_invalid"),
            ([{"name": "A B", "date_of_birth": "04/1980"}], "date_invalid"),
            ([{"name": "A B"}] * 21, "owners_invalid"),
        ):
            with pytest.raises(questions.AnswerError) as caught:
                questions.validate_answer("owners", bad, country="US", today=TODAY)
            assert caught.value.code == code


class TestPolicy:
    def test_defaults_and_merchant_types(self):
        policy = questions.application_policy_from(None)
        assert policy.for_type("cafe").required == questions.DEFAULT_REQUIRED
        custom = questions.application_policy_from(
            {
                "default": {"required": ["legal_name", "owners", "tax_id"], "optional": ["website"]},
                "merchant_types": {"cafe": {"required": ["legal_name", "owners"]}},
            }
        )
        assert custom.for_type("cafe").required == ("legal_name", "owners") and custom.for_type("cafe").optional == ()
        assert custom.for_type("salon").items() == ("legal_name", "owners", "tax_id", "website")
        assert questions.application_policy_from(custom.to_dict()) == custom

    @pytest.mark.parametrize(
        ("raw", "field"),
        [
            ({"other": {}}, "application"),
            ({"default": []}, "default"),
            ({"default": {"required": ["legal_name", "owners"], "extra": []}}, "default"),
            ({"default": {"required": ["legal_name", "owners", "ssn"]}}, "default.required"),
            ({"default": {"required": ["legal_name", "owners", "owners"]}}, "default.required"),
            ({"default": {"required": ["legal_name", "owners"], "optional": ["owners"]}}, "default"),
            ({"default": {"required": ["owners"]}}, "default.required"),
            ({"default": {"required": "legal_name"}}, "default.required"),
            ({"merchant_types": {"Bad Type": {}}}, "merchant_types"),
            ({"merchant_types": []}, "merchant_types"),
            ({"merchant_types": {f"t{i}": {} for i in range(51)}}, "merchant_types"),
            ({"merchant_types": {"cafe": {"required": ["owners"]}}}, "merchant_types.cafe.required"),
        ],
    )
    def test_invalid_policies(self, raw, field):
        with pytest.raises(questions.PolicyError) as caught:
            questions.application_policy_from(raw)
        assert caught.value.field == field

    def test_missing_and_view(self):
        qs = questions.QuestionSet()
        assert questions.missing(qs, {}, set()) == list(questions.DEFAULT_REQUIRED)
        assert questions.missing(qs, US_ANSWERS, {"formation_document"}) == []
        assert questions.missing(qs, US_ANSWERS, set()) == ["formation_document"]
        fr = questions.view(qs, "fr")
        assert fr[0] == {
            "id": "legal_name",
            "kind": "text",
            "required": True,
            "label": "Dénomination sociale",
            "reason": questions.ITEMS["legal_name"].reason["fr"],
        }
        structure = next(i for i in fr if i["id"] == "business_structure")
        assert structure["choices"][0] == {"value": "corporation", "label": "Société par actions"}
        assert [i["required"] for i in fr][-3:] == [False, False, False]

    def test_every_applicant_string_exists_in_english_and_french(self):
        for item in questions.ITEMS.values():
            assert item.kind in questions.KINDS
            for text in (item.label, item.reason, *(labels for _, labels in item.choices)):
                assert set(text) == {"en", "fr"} and all(v.strip() for v in text.values())
        for text in questions.ERRORS.values():
            assert set(text) == {"en", "fr"} and all(text.values())
        assert set(conversation._TEXT["en"]) == set(conversation._TEXT["fr"])
        assert questions.error_text("nonsense", "fr") == questions.ERRORS["required"]["fr"]

    @pytest.mark.parametrize(
        ("country", "region", "answers", "schemes", "jurisdiction"),
        [
            ("US", "NY", US_ANSWERS, ["us_ein", "us_state_file_number"], "US-CA"),
            (
                "CA",
                "QC",
                {
                    "legal_name": "Café Nord Inc",
                    "tax_id": "123456789",
                    "registration_number": "1171234567",
                    "owners": [
                        {
                            "name": "Camille Exemple",
                            "kind": "person",
                            "role": "owner",
                            "ownership_pct": None,
                            "date_of_birth": None,
                        }
                    ],
                    "activity": "other",
                    "trading_name": "Café Nord",
                    "website": "https://nord.example",
                },
                ["ca_business_number", "ca_qc_neq"],
                "CA-QC",
            ),
            (
                "CA",
                "ON",
                {
                    "legal_name": "Maple Ltd",
                    "registration_number": "1234567",
                    "registered_address": {
                        "lines": ["1 Bay St"],
                        "locality": "Toronto",
                        "region": "ON",
                        "postal_code": "M5J 2N8",
                    },
                    "owners": [],
                },
                ["ca_corporation_number"],
                "CA-ON",
            ),
        ],
    )
    def test_the_case_application_validates_as_a_business_case(self, country, region, answers, schemes, jurisdiction):
        from core.cases.store import business_case_document, new_case_ref
        from core.domain_schemas import validate

        normalised = {
            key: (questions.validate_answer(key, value, country=country, today=TODAY) if key != "owners" else value)
            for key, value in answers.items()
        }
        application = questions.business_case_application(normalised, country=country, region=region)
        assert [i["scheme"] for i in application["identifiers"]] == schemes
        assert application["jurisdiction"] == jurisdiction
        case = SimpleNamespace(
            case_ref=new_case_ref(),
            purpose="aml.cdd.onboarding",
            state="submitted",
            created_at=NOW,
            updated_at=NOW,
            application=application,
            subject=None,
            decision=None,
        )
        validate("business_case", business_case_document(case))
        assert "role" not in json.dumps(application["declared_owners"])


# ── Documents ────────────────────────────────────────────────────────────────


class TestDocuments:
    def test_sniff_and_names(self):
        assert documents.sniff(b"%PDF-1.7 ...") == "application/pdf"
        assert documents.sniff(b"\x89PNG\r\n\x1a\nrest") == "image/png"
        assert documents.sniff(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
        for content, code in (
            (b"", "document_empty"),
            (b"MZ\x90\x00", "document_type_unsupported"),
            (b"%PDF-" + b"0" * documents.MAX_BYTES, "document_too_large"),
        ):
            with pytest.raises(documents.DocumentRefusedError) as caught:
                documents.sniff(content)
            assert caught.value.code == code
        assert documents.safe_filename("../../etc/passwd.exe", "application/pdf") == "passwd.pdf"
        assert (
            documents.safe_filename("C:\\Users\\x\\Articles <final>.PDF", "application/pdf") == "Articles _final_.pdf"
        )
        assert documents.safe_filename("\x00\x1b", "image/png") == "document.png"
        assert documents.safe_filename(None, "image/jpeg") == "document.jpg"

    def test_suggestions_are_validated_answers(self):
        us = "STATE OF DELAWARE\nEntity Name: Northwind Coffee LLC\nFile Number: 7654321\nEIN 12-3456789\n"
        assert documents.suggest(us, items=("legal_name", "tax_id", "registration_number"), country="US") == [
            {"item": "legal_name", "value": "Northwind Coffee LLC"},
            {"item": "tax_id", "value": "12-3456789"},
            {"item": "registration_number", "value": "7654321"},
        ]
        assert documents.suggest(us, items=("legal_name",), country="US") == [
            {"item": "legal_name", "value": "Northwind Coffee LLC"}
        ]
        ca = "The name of the corporation is Maple Goods Inc.\nBusiness Number: 123 456 789\nNEQ 1171234567"
        assert documents.suggest(ca, items=("legal_name", "tax_id", "registration_number"), country="CA") == [
            {"item": "legal_name", "value": "Maple Goods Inc"},
            {"item": "tax_id", "value": "123456789"},
            {"item": "registration_number", "value": "1171234567"},
        ]
        assert documents.suggest("BN 123456789RT0001", items=("tax_id",), country="CA")[0]["value"] == "123456789"
        assert documents.suggest("Legal name: <script>alert(1)</script>", items=("legal_name",), country="US") == []
        assert documents.suggest("nothing here", items=("legal_name", "tax_id"), country="US") == []

    def test_extract_statuses(self, monkeypatch):
        from core.idp.pages import DocumentError

        def run(reader):
            monkeypatch.setattr(documents, "_read_text", reader)
            return asyncio.run(documents.extract(b"%PDF-", "application/pdf", items=("tax_id",), country="US"))

        assert run(lambda c, m: ("EIN 12-3456789", "read")).to_dict() == {
            "status": "suggested",
            "suggestions": [{"item": "tax_id", "value": "12-3456789"}],
        }
        assert run(lambda c, m: ("hello", "read")).status == "nothing_found"
        assert run(lambda c, m: ("", "ocr_unavailable")).status == "ocr_unavailable"

        def broken(c, m):
            raise DocumentError(422, "pdf_unreadable", "x")

        assert run(broken).status == "unreadable"
        monkeypatch.setattr(documents, "READ_TIMEOUT_SECONDS", 0.01)

        def slow(c, m):
            import time

            time.sleep(0.2)
            return "", "read"

        assert run(slow).status == "unreadable"

    def test_reading_real_files(self):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
        text, status = documents._read_text(buffer.getvalue(), "image/png")
        assert text == "" and status in ("ocr_unavailable", "unreadable")


# ── Store ────────────────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return list(self.rows)

    def scalar(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self, results):
        self.results = [list(r) for r in results]
        self.added = []
        self.deleted = []
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.results.pop(0) if self.results else [])

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)


@pytest.fixture
def db(monkeypatch):
    def install(*results):
        session = FakeSession(results)

        @asynccontextmanager
        async def fake(tenant_id):
            yield session

        monkeypatch.setattr(store, "_session", fake)
        return session

    async def identity(text):
        return text

    async def kek(tenant_id):
        return ""

    async def seal(kek, text):
        return text

    monkeypatch.setattr(store, "_decrypt", identity)
    monkeypatch.setattr(applications, "_kek", kek)
    monkeypatch.setattr(applications, "_seal", seal)
    return install


def app_row(**overrides):
    base = {
        "id": APP,
        "lead_id": LEAD,
        "status": "in_progress",
        "merchant_type": "general",
        "country": "US",
        "region": "CA",
        "locale": "en",
        "answers_ciphertext": None,
        "transcript_ciphertext": None,
        "answered_items": [],
        "pending_item": None,
        "last_channel": None,
        "created_at": NOW,
        "updated_at": None,
        "submitted_at": None,
        "case_ref": None,
        "case_opened_at": None,
        "case_error": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def answers_blob(values):
    return json.dumps({"values": values, "sources": {k: {"source": "web", "at": NOW.isoformat()} for k in values}})


def doc_row(item="formation_document", **overrides):
    base = {
        "id": uuid.uuid4(),
        "application_id": APP,
        "item": item,
        "filename": "articles.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "extraction_status": "suggested",
        "suggestions_ciphertext": None,
        "content_ciphertext": "JVBERi0=",
        "created_at": NOW,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def lead(monkeypatch):
    holder = {
        "lead": SimpleNamespace(
            id=LEAD, status="contacted", merchant_type="general", country="US", region="CA", locale="en"
        ),
        "consents": [],
    }

    async def get_lead(tid, lid):
        return holder["lead"], holder["consents"]

    monkeypatch.setattr(store, "get_lead", get_lead)
    return holder


class TestStore:
    def test_open_for_lead(self, db, lead):
        existing = app_row()
        session = db([existing])
        assert asyncio.run(applications.open_for_lead(TENANT, LEAD, now=NOW)) is existing
        assert len(session.statements) == 1
        session = db([], [], [existing], [])
        assert asyncio.run(applications.open_for_lead(TENANT, LEAD, now=NOW)) is existing
        compiled = [str(s) for s in session.statements]
        assert "ON CONFLICT" in compiled[1] and compiled[3].startswith("UPDATE onboarding_desk_leads")
        db([], [], [], [])
        with pytest.raises(DeskError, match="could not be opened"):
            asyncio.run(applications.open_for_lead(TENANT, LEAD, now=NOW))
        lead["lead"].status = "opted_out"
        with pytest.raises(DeskError) as caught:
            asyncio.run(applications.open_for_lead(TENANT, LEAD, now=NOW))
        assert caught.value.status == 410

    def test_save_answers(self, db):
        row = app_row(
            answers_ciphertext=answers_blob({"website": "https://a.example"}),
            answered_items=["website"],
            pending_item="legal_name",
        )
        db([row], [], [row])
        errors = asyncio.run(
            applications.save_answers(
                TENANT,
                APP,
                {"legal_name": "Northwind LLC", "tax_id": "12", "ssn": "1", "website": None},
                source="sms",
                now=NOW,
            )
        )
        assert errors == {"tax_id": "ein_invalid", "ssn": "item_not_requested"}
        saved = json.loads(row.answers_ciphertext)
        assert saved["values"] == {"legal_name": "Northwind LLC"} and saved["sources"]["legal_name"]["source"] == "sms"
        assert row.answered_items == ["legal_name"] and row.pending_item is None and row.last_channel == "sms"
        closed = app_row(status="submitted")
        db([closed], [], [closed])
        with pytest.raises(DeskError, match="submitted"):
            asyncio.run(applications.save_answers(TENANT, APP, {"legal_name": "X Y"}, source="web"))
        for bad in ({}, [], {f"k{i}": 1 for i in range(21)}):
            with pytest.raises(DeskError, match="answers"):
                asyncio.run(applications.save_answers(TENANT, APP, bad, source="web"))
        db([])
        with pytest.raises(DeskError) as caught:
            asyncio.run(applications.save_answers(TENANT, APP, {"legal_name": "X Y"}, source="web"))
        assert caught.value.status == 404

    def test_save_keeps_document_items_answered(self, db):
        row = app_row(answered_items=["formation_document"])
        db([row], [], [row])
        asyncio.run(applications.save_answers(TENANT, APP, {"legal_name": "Acme Co"}, source="web", now=NOW))
        assert row.answered_items == ["formation_document", "legal_name"]

    def test_state(self, db):
        row = app_row(answers_ciphertext=answers_blob(US_ANSWERS), transcript_ciphertext=json.dumps([{"text": "hi"}]))
        doc = doc_row()
        db([], [doc], [SimpleNamespace(status="open")])
        view = asyncio.run(applications.state(TENANT, row))
        assert view["missing"] == [] and view["ready"] is True and view["person_requested"] is True
        formation = next(i for i in view["items"] if i["id"] == "formation_document")
        assert formation["documents"][0]["filename"] == "articles.pdf" and "transcript" not in view
        legal = next(i for i in view["items"] if i["id"] == "legal_name")
        assert legal["answer"] == "Northwind Coffee LLC" and legal["source"] == "web"
        db([], [doc], [])
        operator = asyncio.run(applications.state(TENANT, row, operator=True))
        assert operator["transcript"] == [{"text": "hi"}] and operator["case_application"]["jurisdiction"] == "US-CA"
        db([], [], [])
        incomplete = asyncio.run(applications.state(TENANT, app_row(locale="fr"), operator=True))
        assert incomplete["case_application"] is None and incomplete["locale"] == "fr" and not incomplete["ready"]

    def test_add_document(self, db, monkeypatch):
        async def extract(content, mime, *, items, country):
            assert "formation_document" not in items and country == "US"
            return documents.Extraction("suggested", [{"item": "legal_name", "value": "Northwind Coffee LLC"}])

        monkeypatch.setattr(documents, "extract", extract)
        row = app_row()
        session = db([row], [], [2], [row])
        result = asyncio.run(
            applications.add_document(
                TENANT,
                APP,
                item="formation_document",
                filename="../Articles.pdf",
                content=b"%PDF-1.4 x",
                now=NOW,
            )
        )
        assert result["suggestions"] == [{"item": "legal_name", "value": "Northwind Coffee LLC"}]
        assert result["filename"] == "Articles.pdf" and result["mime_type"] == "application/pdf"
        stored = session.added[0]
        assert stored.content_ciphertext == "JVBERi0xLjQgeA==" and json.loads(stored.suggestions_ciphertext)
        assert row.answered_items == ["formation_document"]
        with pytest.raises(DeskError) as caught:
            asyncio.run(applications.add_document(TENANT, APP, item="formation_document", filename="a", content=b"MZ"))
        assert caught.value.status == 415
        db([row], [])
        with pytest.raises(DeskError, match="does not ask"):
            asyncio.run(applications.add_document(TENANT, APP, item="legal_name", filename="a", content=b"%PDF-"))
        db([row], [], [documents.MAX_PER_APPLICATION])
        with pytest.raises(DeskError, match="at most"):
            asyncio.run(
                applications.add_document(TENANT, APP, item="formation_document", filename="a", content=b"%PDF-")
            )

    def test_confirm_and_remove(self, db):
        doc = doc_row(suggestions_ciphertext=json.dumps([{"item": "legal_name", "value": "Northwind Coffee LLC"}]))
        row = app_row()
        db([doc], [row], [], [row])
        errors = asyncio.run(applications.confirm_suggestions(TENANT, APP, doc.id, ["legal_name"], now=NOW))
        assert errors == {} and json.loads(row.answers_ciphertext)["sources"]["legal_name"]["source"] == (
            f"document:{doc.id}"
        )
        db([doc])
        with pytest.raises(DeskError, match="suggested"):
            asyncio.run(applications.confirm_suggestions(TENANT, APP, doc.id, ["tax_id"]))
        db([])
        with pytest.raises(DeskError) as caught:
            asyncio.run(applications.confirm_suggestions(TENANT, APP, doc.id, ["legal_name"]))
        assert caught.value.code == "document_not_found"
        other = doc_row(item="tax_id_document")
        row = app_row(answered_items=["formation_document", "legal_name", "tax_id_document"])
        session = db([row], [doc, other])
        asyncio.run(applications.remove_document(TENANT, APP, doc.id, now=NOW))
        assert session.deleted == [doc] and row.answered_items == ["legal_name", "tax_id_document"]
        db([row], [other])
        with pytest.raises(DeskError, match="no such document"):
            asyncio.run(applications.remove_document(TENANT, APP, doc.id))

    def test_transcript_person_and_submit(self, db):
        row = app_row(transcript_ciphertext=json.dumps([{"text": str(i)} for i in range(200)]))
        db([row])
        asyncio.run(
            applications.append_transcript(
                TENANT, APP, [{"channel": "sms", "from": "applicant", "text": "x" * 2000}, {"from": "bot"}], now=NOW
            )
        )
        transcript = json.loads(row.transcript_ciphertext)
        assert len(transcript) == 200 and transcript[-2]["text"] == "x" * 1000 and transcript[-1]["from"] == "desk"
        handoff = SimpleNamespace(status="open")
        row = app_row()
        session = db([row], [], [row], [handoff])
        assert asyncio.run(applications.request_person(TENANT, APP, channel="web", message=" call  me ", now=NOW)) is (
            handoff
        )
        assert "ON CONFLICT" in str(session.statements[1])
        assert json.loads(row.transcript_ciphertext)[-1]["text"] == "[asked for a person] call me"
        row = app_row()
        db([row], [], [row], [None])
        asyncio.run(applications.request_person(TENANT, APP, channel="sms", now=NOW))
        assert json.loads(row.transcript_ciphertext)[-1]["text"] == "[asked for a person]"
        complete = app_row(answers_ciphertext=answers_blob(US_ANSWERS), pending_item="tax_id")
        session = db([complete], [], [doc_row()], [complete], [])
        asyncio.run(applications.submit(TENANT, APP, now=NOW))
        assert complete.status == "submitted" and complete.submitted_at == NOW and complete.pending_item is None
        assert str(session.statements[-1]).startswith("UPDATE onboarding_desk_leads")
        incomplete = app_row(answers_ciphertext=answers_blob({"legal_name": "X Y"}))
        db([incomplete], [], [], [incomplete])
        with pytest.raises(DeskError) as caught:
            asyncio.run(applications.submit(TENANT, APP))
        assert caught.value.code == "application_incomplete" and "owners" in caught.value.message

    def test_pending_policy_and_operator_views(self, db):
        row = app_row()
        db([row])
        asyncio.run(applications.set_pending_item(TENANT, APP, "tax_id"))
        assert row.pending_item == "tax_id"
        session = db([])
        asyncio.run(
            applications.put_application_policy(TENANT, {"default": {"required": ["legal_name", "owners"]}}, actor="u")
        )
        assert session.added[0].settings["application"]["default"]["required"] == ["legal_name", "owners"]
        policy_row = SimpleNamespace(settings={"branding": {"partner_name": "P"}}, updated_by="", updated_at=None)
        db([policy_row])
        asyncio.run(applications.put_application_policy(TENANT, {}, actor="u"))
        assert policy_row.settings["branding"] == {"partner_name": "P"} and "application" in policy_row.settings
        db([policy_row])
        assert asyncio.run(applications.get_application_policy(TENANT)).default == questions.QuestionSet()
        db()
        with pytest.raises(DeskError, match="application_policy_invalid|default"):
            asyncio.run(applications.put_application_policy(TENANT, {"default": {"required": []}}, actor="u"))
        db([row])
        assert applications.summary_view(asyncio.run(applications.list_applications(TENANT, status="in_progress"))[0])[
            "id"
        ] == str(APP)
        with pytest.raises(DeskError):
            asyncio.run(applications.list_applications(TENANT, status="lost"))
        doc = doc_row()
        db([doc])
        assert asyncio.run(applications.document_content(TENANT, APP, doc.id)) == (
            "articles.pdf",
            "application/pdf",
            b"%PDF-",
        )
        handoff = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=APP,
            lead_id=LEAD,
            channel="web",
            status="open",
            message_ciphertext="call me",
            created_at=NOW,
            resolved_at=None,
            resolved_by=None,
            resolution_note=None,
        )
        db([handoff])
        assert (
            asyncio.run(applications.handoff_view(asyncio.run(applications.list_handoffs(TENANT))[0]))["message"]
            == "call me"
        )
        with pytest.raises(DeskError):
            asyncio.run(applications.list_handoffs(TENANT, status="lost"))
        db([handoff])
        asyncio.run(applications.resolve_handoff(TENANT, handoff.id, actor="u1", note=" called  back ", now=NOW))
        assert handoff.status == "resolved" and handoff.resolution_note == "called back" and handoff.resolved_by == "u1"
        db([handoff])
        asyncio.run(applications.resolve_handoff(TENANT, handoff.id, actor="u2"))
        assert handoff.resolved_by == "u1"
        db([])
        with pytest.raises(DeskError, match="hand-off"):
            asyncio.run(applications.resolve_handoff(TENANT, handoff.id, actor="u"))

    def test_open_leads_by_phone(self, monkeypatch):
        rows = [SimpleNamespace(id=LEAD)]

        @asynccontextmanager
        async def fake(tenant_id):
            yield FakeSession([rows])

        monkeypatch.setattr(store, "_session", fake)
        monkeypatch.setattr(settings, "secret_key", "k" * 32)
        assert asyncio.run(store.open_leads_by_phone(TENANT, "+1 415 555 0134")) == rows
        assert asyncio.run(store.open_leads_by_phone(TENANT, "not a phone")) == []
        assert asyncio.run(store.open_leads_by_phone(TENANT, "")) == []


# ── The text conversation ────────────────────────────────────────────────────


BRANDING = branding.branding_from(
    {
        "partner_name": "Northwind Payments",
        "onboarding_url": "https://apply.northwind.example/start",
        "sms_conversation": True,
    }
)


@pytest.fixture
def chat(monkeypatch):
    state = {
        "branding": BRANDING,
        "leads": [SimpleNamespace(id=LEAD)],
        "consents": [SimpleNamespace(channel="sms", status="granted", expires_at=None)],
        "app": app_row(),
        "answers": {},
        "documents": set(),
        "transcript": [],
        "people": [],
    }

    async def get_branding(tid):
        return state["branding"]

    async def leads_by_phone(tid, phone):
        return state["leads"]

    async def get_lead(tid, lid):
        return SimpleNamespace(id=LEAD), state["consents"]

    async def open_for_lead(tid, lid, *, now=None):
        if state["app"] is None:
            raise DeskError(410, "lead_closed", "closed")
        return state["app"]

    async def get_application(tid, aid):
        return state["app"]

    async def policy(tid):
        return questions.ApplicationPolicy()

    async def save(tid, aid, raw, *, source, now=None):
        errors = {}
        for key, value in raw.items():
            try:
                state["answers"][key] = questions.validate_answer(key, value, country="US")
            except questions.AnswerError as exc:
                errors[key] = exc.code
        return errors

    async def app_state(tid, row, *, operator=False):
        return {"missing": questions.missing(questions.QuestionSet(), state["answers"], state["documents"])}

    async def pending(tid, aid, item):
        state["app"].pending_item = item

    async def transcript(tid, aid, entries, *, now=None):
        state["transcript"].extend(entries)

    async def person(tid, aid, *, channel, message="", now=None):
        state["people"].append(channel)

    monkeypatch.setattr(store, "get_branding", get_branding)
    monkeypatch.setattr(store, "open_leads_by_phone", leads_by_phone)
    monkeypatch.setattr(store, "get_lead", get_lead)
    for name, fn in (
        ("open_for_lead", open_for_lead),
        ("get_application", get_application),
        ("get_application_policy", policy),
        ("save_answers", save),
        ("state", app_state),
        ("set_pending_item", pending),
        ("append_transcript", transcript),
        ("request_person", person),
    ):
        monkeypatch.setattr(applications, name, fn)
    return state


def say(body):
    return asyncio.run(conversation.handle_sms(TENANT, "+14155550134", body, now=NOW))


class TestConversation:
    def test_asks_one_item_at_a_time_with_the_reason(self, chat):
        first = say("hi")
        assert first.startswith("Northwind Payments: Legal business name? We match it")
        assert chat["app"].pending_item == "legal_name"
        second = say("Northwind Coffee LLC")
        assert chat["answers"]["legal_name"] == "Northwind Coffee LLC"
        assert "Business structure?" in second and "Reply with a number: 1 Corporation, 2 Limited" in second
        retry = say("9")
        assert retry.startswith("Northwind Payments: Please pick one of the options. Business structure?")
        assert "1 Corporation" in retry
        say("2")
        assert chat["answers"]["business_structure"] == "llc" and chat["app"].pending_item == "registration_number"
        assert [e["from"] for e in chat["transcript"][:2]] == ["applicant", "desk"]

    def test_sends_the_secure_page_for_the_rest(self, chat):
        chat["answers"].update(
            {
                k: v
                for k, v in US_ANSWERS.items()
                if k in ("legal_name", "business_structure", "registration_number", "tax_id")
            }
        )
        reply = say("next")
        assert "secure page" in reply and "https://apply.northwind.example/start?t=" in reply
        assert chat["app"].pending_item is None
        token = reply.rsplit("?t=", 1)[1]
        assert tokens.verify(token, "apply", now=NOW).lead_id == LEAD
        chat["answers"].update(US_ANSWERS)
        chat["documents"].add("formation_document")
        assert "Everything is in" in say("done?")

    def test_keywords_french_and_submitted(self, chat):
        assert "AI assistant" in say("help") and "Reply PERSON" in say("Help!")
        assert "will contact you" in say("person") and chat["people"] == ["sms"]
        chat["app"] = app_row(locale="fr")
        assert say("aide").startswith("Northwind Payments : Ceci est l'assistant IA")
        assert say("bonjour").startswith("Northwind Payments : Dénomination sociale?")
        chat["app"] = app_row(status="submitted")
        assert "submitted" in say("hello")

    def test_does_not_answer_when_it_should_not(self, chat):
        chat["branding"] = branding.branding_from({"partner_name": "P", "onboarding_url": "https://a.example"})
        assert say("hi") is None
        chat["branding"] = BRANDING
        chat["leads"] = []
        assert say("hi") is None
        chat["leads"] = [SimpleNamespace(id=LEAD), SimpleNamespace(id=uuid.uuid4())]
        assert say("hi") is None
        chat["leads"] = [SimpleNamespace(id=LEAD)]
        chat["consents"] = [
            SimpleNamespace(channel="email", status="granted", expires_at=None),
            SimpleNamespace(channel="sms", status="withdrawn", expires_at=None),
            SimpleNamespace(channel="sms", status="granted", expires_at=NOW - timedelta(days=1)),
        ]
        assert say("hi") is None
        chat["consents"] = [SimpleNamespace(channel="sms", status="granted", expires_at=NOW + timedelta(days=1))]
        chat["app"] = None
        assert say("hi") is None

    def test_long_replies_fall_back_to_the_page(self, chat, monkeypatch):
        monkeypatch.setattr(conversation.messages, "MAX_SMS", 40)
        reply = say("hi")
        assert "secure page" in reply


# ── Routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from api import deps
    from api.v1 import onboarding_desk as api

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[deps.get_current_user] = lambda: {"sub": str(uuid.uuid4())}
    app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
    monkeypatch.setattr(settings, "onboarding_desk_enabled", True)
    return TestClient(app)


@pytest.fixture
def api_state(monkeypatch):
    calls = []
    row = app_row()

    async def open_for_lead(tid, lid, *, now=None):
        if lid != LEAD:
            raise DeskError(410, "lead_closed", "closed")
        return row

    async def get_application(tid, aid):
        return row

    async def state(tid, r, *, operator=False):
        return {"id": str(r.id), "locale": "fr", "operator": operator}

    async def save(tid, aid, raw, *, source, now=None):
        calls.append(("save", raw, source))
        return {"tax_id": "ein_invalid"}

    async def add(tid, aid, *, item, filename, content, now=None):
        calls.append(("add", item, filename, content))
        return {"id": "d1", "suggestions": []}

    async def confirm(tid, aid, did, items, *, now=None):
        calls.append(("confirm", did, items))
        return {}

    async def remove(tid, aid, did, *, now=None):
        calls.append(("remove", did))

    async def person(tid, aid, *, channel, message="", now=None):
        calls.append(("person", channel, message))

    async def submit(tid, aid, *, now=None):
        raise DeskError(422, "application_incomplete", "required items are missing: owners")

    async def get_branding(tid):
        return BRANDING

    for name, fn in (
        ("open_for_lead", open_for_lead),
        ("get_application", get_application),
        ("state", state),
        ("save_answers", save),
        ("add_document", add),
        ("confirm_suggestions", confirm),
        ("remove_document", remove),
        ("request_person", person),
        ("submit", submit),
    ):
        monkeypatch.setattr(applications, name, fn)
    monkeypatch.setattr(store, "get_branding", get_branding)
    return calls


def session_headers():
    return {"X-Applicant-Session": tokens.sign("session", TENANT, LEAD)}


class TestApplicantRoutes:
    def test_session_exchange(self, client, api_state):
        response = client.post("/api/v1/onboarding-desk/apply/session", json={"t": tokens.sign("apply", TENANT, LEAD)})
        body = response.json()
        assert response.status_code == 200 and body["partner_name"] == "Northwind Payments"
        assert tokens.verify(body["session"], "session").lead_id == LEAD and body["application"]["locale"] == "fr"
        bad = client.post("/api/v1/onboarding-desk/apply/session", json={"t": tokens.sign("unsubscribe", TENANT, LEAD)})
        assert bad.status_code == 401 and bad.json()["detail"]["error"] == "link_invalid"
        closed = client.post(
            "/api/v1/onboarding-desk/apply/session", json={"t": tokens.sign("apply", TENANT, uuid.uuid4())}
        )
        assert closed.status_code == 410

    def test_the_session_is_required(self, client, api_state):
        assert client.get("/api/v1/onboarding-desk/apply/application").status_code == 401
        link_as_session = {"X-Applicant-Session": tokens.sign("apply", TENANT, LEAD)}
        assert client.get("/api/v1/onboarding-desk/apply/application", headers=link_as_session).status_code == 401
        ok = client.get("/api/v1/onboarding-desk/apply/application", headers=session_headers())
        assert ok.status_code == 200 and ok.json()["operator"] is False

    def test_answers_documents_person_and_submit(self, client, api_state):
        headers = session_headers()
        saved = client.put("/api/v1/onboarding-desk/apply/answers", json={"answers": {"tax_id": "1"}}, headers=headers)
        assert saved.json()["errors"] == {
            "tax_id": {"code": "ein_invalid", "message": questions.ERRORS["ein_invalid"]["fr"]}
        }
        upload = client.post(
            "/api/v1/onboarding-desk/apply/documents",
            headers=headers,
            data={"item": "formation_document"},
            files={"file": ("a.pdf", b"%PDF-1", "application/pdf")},
        )
        assert upload.status_code == 200 and api_state[-1] == ("add", "formation_document", "a.pdf", b"%PDF-1")
        doc_id = str(uuid.uuid4())
        confirmed = client.post(
            f"/api/v1/onboarding-desk/apply/documents/{doc_id}/confirm", json={"items": ["tax_id"]}, headers=headers
        )
        assert confirmed.status_code == 200 and api_state[-1] == ("confirm", uuid.UUID(doc_id), ["tax_id"])
        assert client.delete(f"/api/v1/onboarding-desk/apply/documents/{doc_id}", headers=headers).status_code == 200
        assert client.delete("/api/v1/onboarding-desk/apply/documents/nope", headers=headers).status_code == 404
        assert (
            client.post(
                "/api/v1/onboarding-desk/apply/person", json={"message": "call me"}, headers=headers
            ).status_code
            == 200
        )
        assert api_state[-1] == ("person", "web", "call me")
        refused = client.post("/api/v1/onboarding-desk/apply/submit", headers=headers)
        assert refused.status_code == 422 and refused.json()["detail"]["error"] == "application_incomplete"

    def test_errors_and_the_off_switch(self, client, api_state, monkeypatch):
        async def refuse(*args, **kwargs):
            raise DeskError(409, "application_closed", "this application is submitted")

        for name in ("save_answers", "add_document", "confirm_suggestions", "remove_document", "request_person"):
            monkeypatch.setattr(applications, name, refuse)
        headers = session_headers()
        doc = str(uuid.uuid4())
        for method, path, kwargs in (
            ("put", "/apply/answers", {"json": {"answers": {"a": 1}}}),
            ("post", "/apply/documents", {"data": {"item": "x"}, "files": {"file": ("a", b"x", "text/plain")}}),
            ("post", f"/apply/documents/{doc}/confirm", {"json": {"items": ["a"]}}),
            ("delete", f"/apply/documents/{doc}", {}),
            ("post", "/apply/person", {"json": {}}),
        ):
            response = getattr(client, method)(f"/api/v1/onboarding-desk{path}", headers=headers, **kwargs)
            assert response.status_code == 409, path
        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        assert client.get("/api/v1/onboarding-desk/apply/application", headers=headers).status_code == 404
        assert client.post("/api/v1/onboarding-desk/apply/session", json={"t": "x"}).status_code == 404


class TestOperatorRoutes:
    def test_policy_applications_documents_and_handoffs(self, client, monkeypatch):
        handoff = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=APP,
            lead_id=LEAD,
            channel="sms",
            status="open",
            message_ciphertext=None,
            created_at=NOW,
            resolved_at=None,
            resolved_by=None,
            resolution_note=None,
        )

        async def get_policy(tid):
            return questions.ApplicationPolicy()

        async def put_policy(tid, raw, *, actor):
            if raw.get("bad"):
                raise DeskError(422, "application_policy_invalid", "x")
            return questions.application_policy_from(raw)

        async def listed(tid, *, status=None, limit=50):
            if status == "lost":
                raise DeskError(422, "status_invalid", "x")
            return [app_row()]

        async def get_application(tid, aid):
            if aid != APP:
                raise DeskError(404, "application_not_found", "x")
            return app_row()

        async def state(tid, row, *, operator=False):
            return {"operator": operator}

        async def content(tid, aid, did):
            return "articles.pdf", "application/pdf", b"%PDF-1"

        async def handoffs(tid, *, status="open", limit=50):
            if status == "lost":
                raise DeskError(422, "status_invalid", "x")
            return [handoff]

        async def resolve(tid, hid, *, actor, note=""):
            if hid != handoff.id:
                raise DeskError(404, "handoff_not_found", "x")
            handoff.status = "resolved"
            return handoff

        async def nothing(ciphertext):
            return None

        for name, fn in (
            ("get_application_policy", get_policy),
            ("put_application_policy", put_policy),
            ("list_applications", listed),
            ("get_application", get_application),
            ("state", state),
            ("document_content", content),
            ("list_handoffs", handoffs),
            ("resolve_handoff", resolve),
        ):
            monkeypatch.setattr(applications, name, fn)
        monkeypatch.setattr(store, "_decrypt", nothing)
        base = "/api/v1/onboarding-desk"
        policy = client.get(f"{base}/application-policy").json()
        assert policy["default"]["required"][0] == "legal_name" and len(policy["catalogue"]) == len(questions.ITEMS)
        assert client.put(f"{base}/application-policy", json={}).status_code == 200
        assert client.put(f"{base}/application-policy", json={"bad": 1}).status_code == 422
        assert client.get(f"{base}/applications").json()["applications"][0]["id"] == str(APP)
        assert client.get(f"{base}/applications?status=lost").status_code == 422
        assert client.get(f"{base}/applications/{APP}").json() == {"operator": True, "case": None}
        assert client.get(f"{base}/applications/{uuid.uuid4()}").status_code == 404
        download = client.get(f"{base}/applications/{APP}/documents/{uuid.uuid4()}")
        assert download.content == b"%PDF-1" and download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["content-disposition"] == 'attachment; filename="articles.pdf"'
        assert client.get(f"{base}/handoffs").json()["handoffs"][0]["channel"] == "sms"
        assert client.get(f"{base}/handoffs?status=lost").status_code == 422
        assert client.post(f"{base}/handoffs/{handoff.id}/resolve", json={"note": "ok"}).json()["status"] == "resolved"
        assert client.post(f"{base}/handoffs/{uuid.uuid4()}/resolve", json={}).status_code == 404

        async def missing_doc(tid, aid, did):
            raise DeskError(404, "document_not_found", "x")

        monkeypatch.setattr(applications, "document_content", missing_doc)
        assert client.get(f"{base}/applications/{APP}/documents/{uuid.uuid4()}").status_code == 404

    def test_twilio_replies_with_the_conversation(self, client, monkeypatch):
        import api.v1.agents as agents_api

        async def get_branding(tid):
            return branding.branding_from(
                {**BRANDING.to_dict(), "senders": {"sms": {"connector": "registry-twilio", "sender": "+14165550100"}}}
            )

        async def resolve(tid, ids, a, b):
            return {"auth_token": "tok"}, ["twilio"]

        async def engaged(tid, phone):
            return 1

        replies = iter(["Northwind <Payments> & co: Legal business name?", None])

        async def handle(tid, phone, body):
            item = next(replies)
            if item is None:
                raise DeskError(409, "application_closed", "x")
            return item

        monkeypatch.setattr(store, "get_branding", get_branding)
        monkeypatch.setattr(agents_api, "_resolve_connector_configs", resolve)
        monkeypatch.setattr(store, "mark_engaged", engaged)
        monkeypatch.setattr(conversation, "handle_sms", handle)
        monkeypatch.setattr(settings, "public_api_base_url", "https://api.example.test")
        path = f"/api/v1/onboarding-desk/webhooks/twilio/{TENANT}"
        params = {"From": "+14155550134", "Body": "hi"}
        signature = build_twilio_signature(url=f"https://api.example.test{path}", params=params, auth_token="tok")
        response = client.post(path, data=params, headers={"X-Twilio-Signature": signature})
        assert response.text.endswith(
            "<Response><Message>Northwind &lt;Payments&gt; &amp; co: Legal business name?</Message></Response>"
        )
        assert client.post(path, data=params, headers={"X-Twilio-Signature": signature}).text.endswith("<Response/>")


class TestWiring:
    def test_branding_switch_and_session_tokens(self):
        assert BRANDING.sms_conversation is True and BRANDING.to_dict()["sms_conversation"] is True
        assert branding.branding_from({}).sms_conversation is False
        with pytest.raises(branding.BrandingError) as caught:
            branding.branding_from({"sms_conversation": "yes"})
        assert caught.value.field == "sms_conversation"
        token = tokens.sign("session", TENANT, LEAD, now=NOW)
        assert tokens.verify(token, "session", now=NOW + timedelta(hours=1)).lead_id == LEAD
        with pytest.raises(tokens.TokenError, match="expired"):
            tokens.verify(token, "session", now=NOW + timedelta(hours=3))
        with pytest.raises(tokens.TokenError):
            tokens.verify(tokens.sign("apply", TENANT, LEAD, now=NOW), "session", now=NOW)

    def test_middleware_exempts_only_the_applicant_routes(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        prefixes = GrantexAuthMiddleware.EXEMPT_PREFIXES
        assert "/api/v1/onboarding-desk/apply/" in prefixes
        assert not any(p.startswith("/api/v1/onboarding-desk/applications") for p in prefixes)

    def test_migration_v6z98(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z98_onboarding_applications.py"
        spec = importlib.util.spec_from_file_location("v6_z98_onboarding_applications", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.revision == "v6z98_onboarding_applications"
        assert module.down_revision == "v6z97_onboarding_outreach"
        statements = []
        monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(" ".join(str(sql).split())))
        module.upgrade()
        joined = "\n".join(statements)
        for table in module.TABLES:
            assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in joined
            assert f"CREATE TABLE IF NOT EXISTS {table}" in joined
        open_index = "ux_onboarding_handoffs_open ON onboarding_desk_handoffs(tenant_id, application_id)"
        assert f"{open_index} WHERE status = 'open'" in joined
        assert "ux_onboarding_applications_lead" in joined
        statements.clear()
        module.downgrade()
        assert statements == [f"DROP TABLE IF EXISTS {t};" for t in reversed(module.TABLES)]

    def test_models_match_the_migration(self):
        from core.models.onboarding_desk import OnboardingApplication, OnboardingDocument, OnboardingHandoff

        assert OnboardingApplication.__tablename__ == "onboarding_desk_applications"
        assert {c.name for c in OnboardingDocument.__table__.columns} >= {
            "content_ciphertext",
            "suggestions_ciphertext",
        }
        assert {c.name for c in OnboardingHandoff.__table__.columns} >= {"message_ciphertext", "resolution_note"}
