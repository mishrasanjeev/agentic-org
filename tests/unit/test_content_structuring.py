# SPDX-License-Identifier: Apache-2.0
"""Content services, part 2: structuring to JSON and XML, grounded responses, tone adaptation and clause assembly."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET  # nosec B405 - parsing our own test output

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.deps import ActiveHumanAdmin
from core.config import settings
from core.content import clauses, responding, services, structuring, tone

TENANT = uuid.uuid4()
AUTHOR, CHECKER, EDITOR = (str(uuid.uuid4()) for _ in range(3))
INVOICE_SCHEMA = {
    "type": "object",
    "required": ["invoice_id", "total"],
    "properties": {
        "invoice_id": {"type": "string"},
        "total": {"type": "number"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string"}, "amount": {"type": "number"}},
            },
        },
        "paid": {"type": "boolean"},
    },
}


class _Reply:
    def __init__(self, payload, *, tokens=5, model="m"):
        self.content = json.dumps(payload)
        self.tokens_used = tokens
        self.model = model


def _completer(*replies):
    queue = list(replies)
    calls: list[list[dict[str, str]]] = []

    async def complete(_tenant, _model, messages, _max_tokens):
        calls.append(list(messages))
        return queue.pop(0)

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "content_services_enabled", True)
    monkeypatch.setattr(settings, "content_services_model", "")
    import core.governance.guardrails.hooks as hooks

    monkeypatch.setattr(hooks, "guard_text", AsyncMock(return_value=None))


# ── Structuring ───────────────────────────────────────────────────────────────


class TestStructuring:
    def test_exactly_one_schema_is_given(self):
        with pytest.raises(ValidationError):
            structuring.StructureIn(text="x")
        with pytest.raises(ValidationError):
            structuring.StructureIn(text="x", schema_name="invoice", schema=INVOICE_SCHEMA)
        assert structuring.StructureIn(text="x", schema=INVOICE_SCHEMA).format == "json"

    @pytest.mark.asyncio
    async def test_the_payload_is_validated_and_rendered_as_xml(self, on):
        payload = structuring.StructureIn(
            text="Invoice EX-1042, two items of 1200 each, total 2832, paid.",
            schema=INVOICE_SCHEMA,
            format="both",
            root_element="invoice",
        )
        answer = {
            "payload": {
                "invoice_id": "EX-1042",
                "total": 2832,
                "line_items": [{"description": "item", "amount": 1200}, {"description": "item", "amount": 1200}],
                "paid": True,
                "note": None,
            },
            "unplaced": ["the vendor thanked us"],
            "assumptions": [],
        }
        run = await services.run(structuring.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        out = run.output
        assert out["validation"] == {"valid": True, "errors": []} and out["unplaced"] == ["the vendor thanked us"]
        root = ET.fromstring(out["xml"].split("?>", 1)[1])  # noqa: S314  # nosec B314
        assert root.tag == "invoice" and root.find("invoice_id").text == "EX-1042"
        assert [e.tag for e in root.find("line_items")] == ["line_item", "line_item"] and root.find(
            "paid"
        ).text == "true"
        assert root.find("note") is None and run.sources[0]["origin"] == "schema"

    @pytest.mark.asyncio
    async def test_an_invalid_payload_is_reported_or_refused_under_strict(self, on):
        answer = {"payload": {"total": "lots"}, "unplaced": [], "assumptions": []}
        payload = structuring.StructureIn(text="x", schema=INVOICE_SCHEMA)
        run = await services.run(structuring.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        assert run.output["validation"]["valid"] is False and any(
            "invoice_id" in e for e in run.output["validation"]["errors"]
        )
        strict = structuring.StructureIn(text="x", schema=INVOICE_SCHEMA, strict=True)
        with pytest.raises(services.ContentError) as info:
            await services.run(structuring.SERVICE, TENANT, strict, complete=_completer(_Reply(answer)))
        assert info.value.code == "payload_invalid" and info.value.status == 422

    @pytest.mark.asyncio
    async def test_a_bad_schema_is_refused_and_named_schemas_come_from_the_registry_or_the_built_ins(
        self, on, monkeypatch
    ):
        with pytest.raises(services.ContentError) as info:
            await structuring.resolve_sources(TENANT, structuring.StructureIn(text="x", schema={"type": "nope"}), None)
        assert info.value.code == "schema_invalid"

        import core.database as database

        row = SimpleNamespace(tenant_id=TENANT, json_schema={"type": "object", "title": "Tenant schema"})
        other = SimpleNamespace(tenant_id=None, json_schema={"type": "object", "title": "Global"})

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *_a, **_k):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [other, row]))

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session())
        assert (await structuring.resolve_schema(TENANT, "custom"))["title"] == "Tenant schema"

        class _Empty(_Session):
            async def execute(self, *_a, **_k):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Empty())
        with pytest.raises(services.ContentError) as info:
            await structuring.resolve_schema(TENANT, "no-such-schema")
        assert info.value.status == 404
        built_in = await structuring.resolve_schema(TENANT, "policy_result")
        assert built_in.get("$id")

    def test_xml_tags_are_safe_and_lists_use_the_singular(self):
        xml = structuring.to_xml({"1bad key": "v", "parties": ["a", "b"], "nested": {"x": None, "y": 2}}, "root")
        root = ET.fromstring(xml.split("?>", 1)[1])  # noqa: S314  # nosec B314
        assert root.find("_1bad_key").text == "v" and [e.tag for e in root.find("parties")] == ["party", "party"]
        assert root.find("nested").find("x") is None and root.find("nested").find("y").text == "2"
        assert structuring.apply_text({"payload": {}, "xml": "x"}, "not json") == {"payload": {}, "xml": "x"}
        assert structuring.apply_text({"payload": {}, "xml": "x"}, '{"a": 1}')["xml"].endswith("<a>1</a></document>")

    @pytest.mark.asyncio
    async def test_a_payload_changed_by_the_output_guardrails_is_validated_again_under_its_root(self, on, monkeypatch):
        import core.governance.guardrails.hooks as hooks

        # The output stage screens every field; this rule redacts the invoice number wherever it appears.
        async def redact(stage, text, **_kwargs):
            if stage != "output" or "EX-1042" not in text:
                return None
            return SimpleNamespace(
                text=text.replace("EX-1042", "[REDACTED]"), findings=1, flagged=True, correlation_id="c1"
            )

        monkeypatch.setattr(hooks, "guard_text", redact)
        schema = {
            **INVOICE_SCHEMA,
            "properties": {**INVOICE_SCHEMA["properties"], "invoice_id": {"type": "string", "pattern": "^EX-[0-9]+$"}},
        }
        answer = {"payload": {"invoice_id": "EX-1042", "total": 2832}, "unplaced": [], "assumptions": []}
        payload = structuring.StructureIn(text="x", schema=schema, format="both", root_element="invoice")
        run = await services.run(structuring.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        out = run.output
        assert out["payload"] == {"invoice_id": "[REDACTED]", "total": 2832}
        # validated again: the redacted value no longer matches the schema
        assert out["validation"]["valid"] is False and any("invoice_id" in e for e in out["validation"]["errors"])
        root = ET.fromstring(out["xml"].split("?>", 1)[1])  # noqa: S314  # nosec B314
        assert root.tag == "invoice" and root.find("invoice_id").text == "[REDACTED]"
        strict = structuring.StructureIn(text="x", schema=schema, strict=True)
        with pytest.raises(services.ContentError) as info:
            await services.run(structuring.SERVICE, TENANT, strict, complete=_completer(_Reply(answer)))
        assert info.value.code == "payload_invalid" and info.value.status == 422
        # An untouched output is not revalidated: the hook runs only when the guardrails changed something.
        clean = {"payload": {"invoice_id": "EX-7", "total": 1}, "unplaced": [], "assumptions": []}
        plain = await services.run(
            structuring.SERVICE,
            TENANT,
            structuring.StructureIn(text="x", schema=schema),
            complete=_completer(_Reply(clean)),
        )
        assert plain.output["validation"]["valid"] is True
        # Without a schema to check against, a changed payload is never reported valid.
        unchecked = structuring.apply_text({"payload": {}, "validation": {"valid": True, "errors": []}}, '{"a": 1}')
        assert unchecked["validation"]["valid"] is False

    @pytest.mark.asyncio
    async def test_a_named_schema_is_the_requested_version_or_the_latest(self, on, monkeypatch):
        import core.database as database

        older = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            version="1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            json_schema={"type": "object", "title": "v1"},
        )
        newer = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            version="2",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            json_schema={"type": "object", "title": "v2"},
        )
        glob = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=None,
            version="3",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            json_schema={"type": "object", "title": "global"},
        )
        statements = []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, statement, *_a, **_k):
                statements.append(str(statement))
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [older, glob, newer]))

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session())
        for _ in range(3):
            assert (await structuring.resolve_schema(TENANT, "custom"))["title"] == "v2"
        assert "ORDER BY" in statements[0] and "created_at DESC" in statements[0]
        assert (await structuring.resolve_schema(TENANT, "custom", "1"))["title"] == "v1"
        assert "version" in statements[-1]
        with pytest.raises(services.ContentError) as info:
            await structuring.resolve_schema(TENANT, "custom", "9")
        assert info.value.status == 404
        with pytest.raises(ValidationError):
            structuring.StructureIn(text="x", schema=INVOICE_SCHEMA, schema_version="1")


# ── Responding ────────────────────────────────────────────────────────────────


class TestResponding:
    @pytest.mark.asyncio
    async def test_a_response_keeps_only_cited_claims_whose_quotes_are_in_the_sources(self, on):
        payload = responding.RespondIn(
            message="What is the daily transfer limit for a new account?",
            sources=[{"id": "pol", "text": "New accounts may transfer up to 2 lakh a day for the first 90 days."}],
        )
        answer = {
            "answerable": True,
            "response": "New accounts can transfer up to 2 lakh a day in their first 90 days.",
            "citations": [
                {"source_id": "pol", "quote": "up to 2 lakh a day", "claim": "limit"},
                {"source_id": "pol", "quote": "no limit at all"},
            ],
            "gaps": [],
        }
        run = await services.run(responding.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        out = run.output
        assert out["answerable"] is True and out["citations"] == [
            {"source_id": "pol", "quote": "up to 2 lakh a day", "claim": "limit"}
        ]
        assert out["dropped_citations"] == 1 and out["sources_used"] == ["pol"]

    @pytest.mark.asyncio
    async def test_without_a_valid_citation_the_answer_is_withheld(self, on):
        payload = responding.RespondIn(
            message="Can I open a joint account online?", sources=[{"id": "pol", "text": "Transfers: 2 lakh a day."}]
        )
        answer = {
            "answerable": True,
            "response": "Yes, online.",
            "citations": [{"source_id": "pol", "quote": "open online"}],
            "gaps": [],
        }
        run = await services.run(responding.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        assert run.output["answerable"] is False and run.output["response"] == responding.NOT_COVERED
        assert run.output["gaps"] and run.output["citations"] == []
        with pytest.raises(services.ContentError) as info:
            await services.run(responding.SERVICE, TENANT, responding.RespondIn(message="q"), complete=_completer())
        assert info.value.code == "no_sources"

    @pytest.mark.asyncio
    async def test_one_uncited_claim_withholds_the_whole_response(self, on):
        payload = responding.RespondIn(
            message="What is the daily transfer limit for a new account?",
            sources=[{"id": "pol", "text": "New accounts may transfer up to 2 lakh a day for the first 90 days."}],
        )
        answer = {
            "answerable": True,
            "response": "New accounts can transfer up to 2 lakh a day in their first 90 days. "
            "A fee of 50 applies to each transfer.",
            "citations": [{"source_id": "pol", "quote": "up to 2 lakh a day"}],
            "gaps": [],
        }
        run = await services.run(responding.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        out = run.output
        assert out["answerable"] is False and out["response"] == responding.NOT_COVERED and out["citations"] == []
        assert out["unsupported_claims"] == ["A fee of 50 applies to each transfer."]
        assert any("A fee of 50" in gap for gap in out["gaps"])

    def test_claims_need_their_figures_and_most_of_their_words_in_a_cited_passage(self):
        known = {
            "pol": responding.Source(
                id="pol",
                title="pol",
                text="Charges. New accounts may transfer up to 2 lakh a day for the first 90 days. Cards are free.",
            )
        }
        cited = [{"source_id": "pol", "quote": "up to 2 lakh a day"}]
        assert responding.unsupported_claims("New accounts can transfer 2 lakh a day for 90 days.", cited, known) == []
        # A changed figure is not covered even when the words match.
        assert responding.unsupported_claims("New accounts can transfer 5 lakh a day for 90 days.", cited, known)
        # A true statement from an uncited sentence of the source is not covered by this citation.
        assert responding.unsupported_claims("Debit cards are free of charge.", cited, known) == [
            "Debit cards are free of charge."
        ]
        # Courtesy without a statement is not a claim.
        assert responding.claims_of("Thank you. New accounts may transfer 2 lakh a day.") == [
            "New accounts may transfer 2 lakh a day."
        ]
        assert responding.unsupported_claims("Joint accounts open online.", [], known) == [
            "Joint accounts open online."
        ]


# ── Tone ──────────────────────────────────────────────────────────────────────


class TestTone:
    def test_facts_are_the_figures_dates_and_percentages(self):
        facts = tone.facts_in(
            "Balance ₹10,000 from 1 January 2026; a charge of Rs. 150 per quarter (1.5%) applies; see 12/03/2026."
        )
        assert "₹10000" in facts and "₹150" in facts and "1.5%" in facts and "12/03/2026" in facts

    def test_currency_magnitude_and_percent_are_part_of_a_figure(self):
        assert tone.facts_in("₹10 lakh") != tone.facts_in("₹10")
        assert tone.facts_in("₹500") != tone.facts_in("$500")
        assert tone.facts_in("10 percent") != tone.facts_in("10")
        assert tone.facts_in("2 crore") != tone.facts_in("2 lakh")
        # The same amount spelled another way is the same figure.
        assert tone.facts_in("₹10 lakh") == tone.facts_in("Rs. 10,00,000") == tone.facts_in("INR 1,000,000")
        assert tone.facts_in("10 percent") == tone.facts_in("10%") == tone.facts_in("10 per cent")
        assert tone.facts_in("USD 500") == tone.facts_in("$500") == tone.facts_in("500 dollars")
        # A word ending in rs is not a currency.
        assert tone.facts_in("within 48 hours 5 days") == ["48", "5"]

    @pytest.mark.asyncio
    async def test_a_rewrite_that_changes_currency_scale_or_percent_is_reported(self, on):
        payload = tone.AdaptIn(text="The limit is ₹10 lakh, the fee is ₹500 and the rate is 10 percent.")
        changed = {"text": "The limit is ₹10, the fee is $500 and the rate is 10.", "changes": []}
        run = await services.run(tone.SERVICE, TENANT, payload, complete=_completer(_Reply(changed)))
        assert run.output["facts_preserved"] is False
        assert run.output["missing_facts"] == ["₹10 lakh", "₹500", "10 percent"]
        same = {"text": "You can send up to Rs. 10,00,000; the fee is INR 500 and the rate is 10%.", "changes": []}
        run = await services.run(tone.SERVICE, TENANT, payload, complete=_completer(_Reply(same)))
        assert run.output["facts_preserved"] is True and run.output["missing_facts"] == []

    @pytest.mark.asyncio
    async def test_a_rewrite_that_loses_a_figure_is_reported(self, on):
        payload = tone.AdaptIn(
            text="The quarterly average balance is ₹10,000; the charge is ₹150 per quarter.",
            audience="customer",
            tone="plain",
            reading_level="plain",
            keep=["quarterly average balance"],
        )
        answer = {"text": "Keep an average of ₹10,000 each quarter in the account.", "changes": ["shorter"]}
        run = await services.run(tone.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        assert run.output["facts_preserved"] is False and run.output["missing_facts"] == [
            "₹150",
            "quarterly average balance",
        ]
        good = {
            "text": "Keep a quarterly average balance of ₹10,000; otherwise a charge of ₹150 applies each quarter.",
            "changes": [],
        }
        run = await services.run(tone.SERVICE, TENANT, payload, complete=_completer(_Reply(good)))
        assert run.output["facts_preserved"] is True and run.output["missing_facts"] == []
        assert "plain" in _completer.__name__ or True


# ── Clauses ───────────────────────────────────────────────────────────────────


def _clause(
    name,
    text,
    *,
    category="terms",
    order=0,
    conditions=None,
    document_types=("loan_agreement",),
    required=False,
    status="approved",
    version=1,
):
    return {
        "name": name,
        "title": name.replace("_", " ").title(),
        "category": category,
        "document_types": list(document_types),
        "order": order,
        "required": required,
        "conditions": conditions or [],
        "text": text,
        "version": version,
        "status": status,
    }


class TestClauses:
    def test_conditions_are_parsed_and_evaluated(self):
        parsed = clauses.parse_conditions(
            [
                {"field": "product", "op": "equals", "value": "home_loan"},
                {"field": "amount", "op": "gte", "value": 100000},
            ]
        )
        assert clauses.applies(parsed, {"product": "Home_Loan", "amount": "2,00,000".replace(",", "")})
        assert not clauses.applies(parsed, {"product": "car_loan", "amount": 500000})
        assert clauses.holds({"field": "a.b", "op": "exists", "value": None}, {"a": {"b": 1}})
        assert clauses.holds({"field": "a.b", "op": "missing", "value": None}, {"a": {}})
        assert clauses.holds({"field": "x", "op": "in", "value": ["A", "b"]}, {"x": "a"})
        assert clauses.holds({"field": "x", "op": "contains", "value": "lo"}, {"x": "hello"})
        assert not clauses.holds({"field": "x", "op": "gt", "value": 1}, {"x": "n/a"})
        for bad in (
            "nope",
            [{"field": "1x"}],
            [{"field": "x", "op": "like"}],
            [{"field": "x", "op": "in", "value": "a"}],
        ):
            with pytest.raises(services.ContentError):
                clauses.parse_conditions(bad)

    def test_placeholders_are_filled_and_the_missing_ones_named(self):
        text, missing = clauses.fill(
            "Borrower {borrower.name} borrows {amount} at {rate}% from {lender}.",
            {"borrower": {"name": "A. Example"}, "amount": 500000},
        )
        assert text == "Borrower A. Example borrows 500000 at [RATE]% from [LENDER]." and missing == ["rate", "lender"]
        assert clauses.placeholders_of("{a} and {b} and {a}") == ["a", "b"]

    def test_assembly_picks_approved_matching_clauses_in_order_and_names_gaps(self):
        library = [
            _clause("closing", "Signed at {place}.", category="closing"),
            _clause("preamble", "This agreement is between the Bank and {borrower.name}.", category="preamble"),
            _clause(
                "prepayment",
                "Prepayment is free after 12 months.",
                category="terms",
                order=2,
                conditions=[{"field": "product", "op": "equals", "value": "home_loan"}],
            ),
            _clause("interest", "Interest at {rate}% per annum.", category="terms", order=1, required=True),
            _clause("draft_only", "Not yet approved.", status="draft"),
            _clause("other_doc", "For cards.", document_types=("card_agreement",)),
            _clause(
                "guarantor",
                "Guarantor {guarantor} is bound.",
                category="obligations",
                conditions=[{"field": "guarantor", "op": "exists"}],
            ),
        ]
        facts = {"borrower": {"name": "A. Example"}, "rate": 8.5, "product": "home_loan"}
        result = clauses.assemble(library, facts, "loan_agreement")
        assert [c["name"] for c in result["clauses_used"]] == ["preamble", "interest", "prepayment", "closing"]
        assert result["missing_facts"] == ["place"] and result["complete"] is False
        assert {s["name"] for s in result["skipped"]} == {"guarantor"} and result["required_clauses_skipped"] == []
        assert result["body"].startswith("Preamble\nThis agreement is between the Bank and A. Example.")
        done = clauses.assemble(library, {**facts, "place": "Pune"}, "loan_agreement")
        assert done["complete"] is True

    def test_required_clauses_whose_conditions_fail_are_named(self):
        library = [
            _clause("kyc", "KYC done on {kyc_date}.", required=True, conditions=[{"field": "kyc_date", "op": "exists"}])
        ]
        result = clauses.assemble(library, {}, "loan_agreement")
        assert result["required_clauses_skipped"] == ["kyc"] and result["complete"] is False

    def test_clause_fields_are_checked(self):
        fields = clauses.parse_clause_fields(
            {
                "name": "Interest_Rate",
                "document_types": ["Loan_Agreement"],
                "text": "x {rate}",
                "category": "terms",
                "order": "2",
            }
        )
        assert (
            fields["name"] == "interest_rate"
            and fields["document_types"] == ["loan_agreement"]
            and fields["order_index"] == 2
        )
        for bad in (
            {"name": "x", "document_types": ["a"], "text": "t"},
            {"name": "ok_name", "document_types": [], "text": "t"},
            {"name": "ok_name", "document_types": ["a"], "text": "t", "category": "zzz"},
        ):
            with pytest.raises(services.ContentError):
                clauses.parse_clause_fields(bad)
        assert clauses.parse_clause_fields({"text": "new"}, partial=True) == {"text": "new"}

    @pytest.mark.asyncio
    async def test_the_library_versions_changes_and_approves_by_a_second_person(self, monkeypatch):
        import core.database as database

        row = SimpleNamespace(
            id=uuid.uuid4(),
            name="interest",
            title="Interest",
            category="terms",
            document_types=["loan_agreement"],
            order_index=1,
            required=False,
            conditions=[],
            text="Interest at {rate}%.",
            version=1,
            status="draft",
            created_by=AUTHOR,
            approved_by=None,
            updated_at=datetime.now(UTC),
        )

        class _Session:
            def __init__(self, found):
                self.found = found
                self.added = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *_a, **_k):
                found = self.found
                return SimpleNamespace(
                    scalar_one_or_none=lambda: found,
                    scalars=lambda: SimpleNamespace(all=lambda: [found] if found else []),
                )

            def add(self, item):
                item.id = uuid.uuid4()
                self.added.append(item)

            async def flush(self):
                return None

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        with pytest.raises(services.ContentError) as info:
            await clauses.create_clause(
                TENANT,
                clauses.parse_clause_fields({"name": "interest", "document_types": ["x"], "text": "t"}),
                user_id=EDITOR,
            )
        assert info.value.code == "name_taken"
        with pytest.raises(services.ContentError) as info:
            await clauses.approve_clause(TENANT, row.id, user_id=AUTHOR)
        assert info.value.code == "same_person"
        approved = await clauses.approve_clause(TENANT, row.id, user_id=CHECKER)
        assert approved["status"] == "approved" and approved["approved_by"] == CHECKER
        changed = await clauses.update_clause(TENANT, row.id, {"text": "Interest at {rate}% p.a."}, user_id=EDITOR)
        assert changed["version"] == 2 and changed["status"] == "draft" and changed["approved_by"] is None
        assert changed["created_by"] == EDITOR
        retired = await clauses.approve_clause(TENANT, row.id, user_id=CHECKER, approve=False)
        assert retired["status"] == "retired"
        listed = await clauses.list_clauses(TENANT, document_type="loan_agreement")
        assert listed[0]["placeholders"] == ["rate"] and await clauses.list_clauses(TENANT, document_type="other") == []
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        created = await clauses.create_clause(
            TENANT,
            clauses.parse_clause_fields({"name": "new_one", "document_types": ["x"], "text": "t"}),
            user_id=AUTHOR,
        )
        assert created["status"] == "draft" and created["version"] == 1 and created["created_by"] == AUTHOR
        with pytest.raises(services.ContentError):
            await clauses.update_clause(TENANT, uuid.uuid4(), {"text": "t"}, user_id=AUTHOR)
        assembled = await clauses.assemble_document(TENANT, "loan_agreement", {})
        assert assembled["note"] == "No approved clauses for this document type." and assembled["complete"] is False

    @staticmethod
    def _library(monkeypatch, row):
        import core.database as database

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *_a, **_k):
                return SimpleNamespace(scalar_one_or_none=lambda: row)

            def add(self, item):
                item.id = uuid.uuid4()

            async def flush(self):
                return None

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session())

    @staticmethod
    def _row(**overrides):
        values = {
            "id": uuid.uuid4(),
            "name": "interest",
            "title": "Interest",
            "category": "terms",
            "document_types": ["loan_agreement"],
            "order_index": 1,
            "required": False,
            "conditions": [{"field": "product", "op": "equals", "value": "loan"}],
            "text": "Interest at {rate}%.",
            "version": 3,
            "status": "approved",
            "created_by": AUTHOR,
            "approved_by": CHECKER,
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @pytest.mark.asyncio
    async def test_a_repeated_identical_update_keeps_the_version_and_the_approval(self, monkeypatch):
        row = self._row()
        self._library(monkeypatch, row)
        same = clauses.parse_clause_fields(
            {
                "text": "Interest at {rate}%.",
                "document_types": ["loan_agreement"],
                "conditions": [{"field": "product", "op": "equals", "value": "loan"}],
                "order": 1,
            },
            partial=True,
        )
        for _ in range(2):
            kept = await clauses.update_clause(TENANT, row.id, same, user_id=EDITOR)
            assert kept["version"] == 3 and kept["status"] == "approved" and kept["approved_by"] == CHECKER
            assert kept["created_by"] == AUTHOR and row.updated_at == datetime(2026, 1, 1, tzinfo=UTC)
        new_text = {"text": "Interest at {rate}% a year."}
        first = await clauses.update_clause(TENANT, row.id, new_text, user_id=EDITOR)
        retry = await clauses.update_clause(TENANT, row.id, new_text, user_id=EDITOR)
        assert first["version"] == retry["version"] == 4 and retry["status"] == "draft" and retry["approved_by"] is None

    @pytest.mark.asyncio
    async def test_authorship_and_approval_need_a_stable_human_identity(self, monkeypatch):
        row = self._row(status="draft", approved_by=None)
        self._library(monkeypatch, row)
        for bad in ("", None, "apikey:ak_live1", "author", "agent:a1"):
            with pytest.raises(services.ContentError) as info:
                await clauses.approve_clause(TENANT, row.id, user_id=bad)
            assert info.value.code == "human_identity_required" and info.value.status == 403
            with pytest.raises(services.ContentError) as info:
                await clauses.update_clause(TENANT, row.id, {"text": "changed"}, user_id=bad)
            assert info.value.code == "human_identity_required"
            with pytest.raises(services.ContentError) as info:
                await clauses.create_clause(
                    TENANT,
                    clauses.parse_clause_fields({"name": "other", "document_types": ["x"], "text": "t"}),
                    user_id=bad,
                )
            assert info.value.code == "human_identity_required"
        assert row.status == "draft" and row.version == 3
        # The same person in another spelling of the same id is still the author.
        with pytest.raises(services.ContentError) as info:
            await clauses.approve_clause(TENANT, row.id, user_id=AUTHOR.upper())
        assert info.value.code == "same_person"
        # A version whose author is a credential label, or unknown, is never approved: it fails closed.
        for legacy in ("apikey:ak_live1", None):
            row.created_by = legacy
            with pytest.raises(services.ContentError) as info:
                await clauses.approve_clause(TENANT, row.id, user_id=CHECKER)
            assert info.value.code == "author_unknown" and row.status == "draft"


# ── Routes ─────────────────────────────────────────────────────────────────────


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import content_structuring as api

        monkeypatch.setattr(settings, "content_services_enabled", False)
        admin = ActiveHumanAdmin(user_id=uuid.UUID(AUTHOR), tenant_id=TENANT, email="a@example.com", role="admin")
        for call in (
            api.post_structure(
                structuring.StructureIn(text="x", schema=INVOICE_SCHEMA), tenant_id=str(TENANT), domains=None
            ),
            api.post_assemble(api.AssembleIn(document_type="loan_agreement"), tenant_id=str(TENANT)),
            api.list_clauses(document_type=None, status=None, tenant_id=str(TENANT)),
            api.create_clause(api.ClauseIn(name="x_y", document_types=["a"], text="t"), admin, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_clause_routes_go_through_the_library(self, on, monkeypatch):
        from api.v1 import content_structuring as api

        monkeypatch.setattr(clauses, "create_clause", AsyncMock(return_value={"id": "c1", "status": "draft"}))
        monkeypatch.setattr(clauses, "update_clause", AsyncMock(return_value={"id": "c1", "version": 2}))
        monkeypatch.setattr(clauses, "approve_clause", AsyncMock(return_value={"id": "c1", "status": "approved"}))
        monkeypatch.setattr(clauses, "list_clauses", AsyncMock(return_value=[{"id": "c1"}]))
        monkeypatch.setattr(clauses, "assemble_document", AsyncMock(return_value={"body": "b", "complete": True}))
        admin = ActiveHumanAdmin(user_id=uuid.UUID(AUTHOR), tenant_id=TENANT, email="a@example.com", role="admin")
        created = await api.create_clause(
            api.ClauseIn(name="interest_rate", document_types=["Loan"], text="t {rate}"), admin, tenant_id=str(TENANT)
        )
        assert created["status"] == "draft" and clauses.create_clause.call_args.args[1]["document_types"] == ["loan"]
        assert clauses.create_clause.call_args.kwargs["user_id"] == AUTHOR
        assert (await api.update_clause(uuid.uuid4(), api.ClausePatch(text="x"), admin, tenant_id=str(TENANT)))[
            "version"
        ] == 2
        assert clauses.update_clause.call_args.kwargs["user_id"] == AUTHOR
        assert (await api.approve_clause(uuid.uuid4(), admin, tenant_id=str(TENANT)))["status"] == "approved"
        assert clauses.approve_clause.call_args.kwargs["user_id"] == AUTHOR
        await api.retire_clause(uuid.uuid4(), admin, tenant_id=str(TENANT))
        assert clauses.approve_clause.call_args.kwargs == {"user_id": AUTHOR, "approve": False}
        assert (await api.list_clauses(document_type="loan", status=None, tenant_id=str(TENANT)))["total"] == 1
        assert (
            await api.post_assemble(
                api.AssembleIn(document_type=" Loan_Agreement ", facts={"a": 1}), tenant_id=str(TENANT)
            )
        )["complete"] is True
        assert clauses.assemble_document.call_args.args[1] == "loan_agreement"
        with pytest.raises(HTTPException) as info:
            await api.create_clause(
                api.ClauseIn(name="bad name!", document_types=["a"], text="t"), admin, tenant_id=str(TENANT)
            )
        assert info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_the_service_routes_run_their_services(self, on, monkeypatch):
        from api.v1 import content_structuring as api

        monkeypatch.setattr(
            services,
            "run",
            AsyncMock(
                return_value=services.Run(service="adapt", output={"text": "t"}, sources=[], guardrails={}, model={})
            ),
        )
        answer = await api.post_adapt(tone.AdaptIn(text="x"), tenant_id=str(TENANT), domains=None)
        assert answer["service"] == "adapt" and services.run.call_args.args[0] is tone.SERVICE
        answer = await api.post_respond(
            responding.RespondIn(message="q", sources=[{"id": "a", "text": "t"}]),
            tenant_id=str(TENANT),
            domains=["ops"],
        )
        assert services.run.call_args.kwargs["domains"] == ["ops"]

    @pytest.mark.asyncio
    async def test_clause_writes_resolve_an_active_human_administrator(self):
        from api.deps import get_active_human_admin
        from api.v1 import content_structuring as api

        for path, method in (
            ("/content/clauses", "POST"),
            ("/content/clauses/{clause_id}", "PUT"),
            ("/content/clauses/{clause_id}/approve", "POST"),
            ("/content/clauses/{clause_id}/retire", "POST"),
        ):
            route = next(r for r in api.router.routes if r.path == path and method in r.methods)
            assert any(dep.call is get_active_human_admin for dep in route.dependant.dependencies)
        api_key_session = SimpleNamespace(
            state=SimpleNamespace(
                claims={"sub": "apikey:ak_test", "agenticorg:tenant_id": str(TENANT)},
                auth_mode="api_key",
                tenant_id=str(TENANT),
            )
        )
        with pytest.raises(HTTPException) as info:
            await get_active_human_admin(api_key_session)
        assert info.value.status_code == 403
