# SPDX-License-Identifier: Apache-2.0
"""Content services: the framework, the three services, the sources, the drafts queue and the routes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.content import drafting, drafts, extraction, services, sources, summarisation

TENANT = uuid.uuid4()


class _Reply:
    def __init__(self, payload, *, tokens=12, model="model-x"):
        self.content = payload if isinstance(payload, str) else json.dumps(payload)
        self.tokens_used = tokens
        self.model = model


def _completer(*replies):
    """A model seam that answers the given replies in order."""
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
    # The guardrail hooks are off in tests unless a test turns them on.
    import core.governance.guardrails.hooks as hooks

    monkeypatch.setattr(hooks, "guard_text", AsyncMock(return_value=None))


# ── Framework ─────────────────────────────────────────────────────────────────


class TestFramework:
    def test_json_answers_are_read_from_fences_prose_and_plain(self):
        assert services.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert services.parse_json('Here you go: {"a": {"b": 2}} thanks') == {"a": {"b": 2}}
        assert (
            services.parse_json("[1, 2]") is None
            and services.parse_json("") is None
            and services.parse_json("{nope") is None
        )

    def test_schema_errors_name_the_path(self):
        schema = {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}}
        assert services.schema_errors({"title": "x"}, schema) == []
        errors = services.schema_errors({"title": 3}, schema)
        assert errors and errors[0].startswith("title:")

    @pytest.mark.asyncio
    async def test_an_invalid_answer_is_retried_once_with_the_problems_named_then_refused(self, on):
        schema = {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}}
        complete = _completer(_Reply("not json"), _Reply({"title": "ok"}))
        answer, usage = await services.ask_model(TENANT, [{"role": "user", "content": "x"}], schema, complete=complete)
        assert answer == {"title": "ok"} and usage["retries"] == 1 and usage["tokens"] == 24
        assert "not a JSON object" in complete.calls[1][-1]["content"]

        complete = _completer(_Reply({"title": 1}), _Reply({"title": 2}))
        with pytest.raises(services.ContentError) as info:
            await services.ask_model(TENANT, [{"role": "user", "content": "x"}], schema, complete=complete)
        assert info.value.code == "model_output_invalid" and info.value.status == 502

    @pytest.mark.asyncio
    async def test_a_model_failure_is_a_refusal_not_a_crash(self, on):
        async def broken(*_a):
            raise RuntimeError("provider down")

        with pytest.raises(services.ContentError) as info:
            await services.ask_model(TENANT, [], {"type": "object"}, complete=broken)
        assert info.value.code == "model_failed"

    @pytest.mark.asyncio
    async def test_guardrails_transform_or_block(self, on, monkeypatch):
        import core.governance.guardrails.hooks as hooks

        result = SimpleNamespace(text="masked text", findings=1, flagged=True, correlation_id="c1")
        monkeypatch.setattr(hooks, "guard_text", AsyncMock(return_value=result))
        screened = await services.guard("output", "raw text", tenant_id=TENANT, service="draft", context=["ctx"])
        assert screened["text"] == "masked text" and screened["findings"] == 1 and screened["applied"] is True
        assert hooks.guard_text.call_args.kwargs["use_case"] == "content.draft" and hooks.guard_text.call_args.kwargs[
            "context"
        ] == ["ctx"]

        from core.governance.guardrails.schema import GuardrailBlocked

        def blocked(*_a, **_k):
            raise GuardrailBlocked(
                "card number in the input", stage="input", correlation_id="c2", rule_id="r1", rule_name="pan"
            )

        monkeypatch.setattr(hooks, "guard_text", AsyncMock(side_effect=blocked))
        with pytest.raises(services.ContentError) as info:
            await services.guard("input", "4111 1111 1111 1111", tenant_id=TENANT, service="draft")
        assert info.value.code == "guardrail_blocked" and info.value.status == 422

    def test_the_catalogue_describes_every_service(self):
        names = {item["name"] for item in services.catalogue()}
        assert {"draft", "summarise", "extract"} <= names
        draft = next(item for item in services.catalogue() if item["name"] == "draft")
        assert draft["guardrail_profile"] == {"input": True, "output": True, "grounded": False}
        assert draft["dataset"]["cases"] == 3 and "properties" in draft["input_schema"]
        with pytest.raises(services.ContentError):
            services.get("nope")

    def test_quotes_are_matched_ignoring_case_and_whitespace(self):
        assert services.quote_in("Pay within 30 DAYS", "The bank shall pay\n within   30 days of receipt.")
        assert not services.quote_in("within 45 days", "within 30 days")
        assert not services.quote_in("", "anything")

    @pytest.mark.asyncio
    async def test_off_means_refused(self, monkeypatch):
        monkeypatch.setattr(settings, "content_services_enabled", False)
        with pytest.raises(services.ContentError) as info:
            await services.run(drafting.SERVICE, TENANT, drafting.DraftIn(kind="memo", subject="s", points=["p"]))
        assert info.value.status == 404


# ── Sources ───────────────────────────────────────────────────────────────────


class TestSources:
    def test_inline_sources_are_bounded_and_distinct(self):
        items = [{"id": "a", "title": "A", "text": "one"}, {"id": "a", "text": "two"}, {"id": "b", "text": "   "}]
        out = sources.inline_sources(items)
        assert [s.id for s in out] == ["a", "a-2"] and out[0].origin == "inline"
        assert sources.by_id(out)["a-2"].text == "two"

    @pytest.mark.asyncio
    async def test_knowledge_sources_come_from_ready_documents_the_caller_may_see(self, monkeypatch):
        import core.database as database
        import core.rag.access as access

        doc = uuid.uuid4()

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, statement, params):
                assert (
                    "d.status = 'ready'" in str(statement)
                    and params["ids"] == [str(doc)]
                    and params["dom"] == ["finance"]
                )
                return SimpleNamespace(fetchall=lambda: [(doc, "Policy", "The limit is 2 lakh.")])

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session())
        monkeypatch.setattr(access, "sql_clause", lambda domains: (" AND d.domain = ANY(:dom)", {"dom": domains}))
        out = await sources.knowledge_sources(TENANT, [str(doc), "not-a-uuid"], ["finance"])
        assert len(out) == 1 and out[0].origin == "knowledge" and out[0].title == "Policy"
        assert await sources.knowledge_sources(TENANT, ["bad"], None) == []


# ── Drafting ──────────────────────────────────────────────────────────────────


class TestDrafting:
    def test_policy_sends_notices_and_circulars_and_marked_drafts_to_approval(self):
        assert drafting.requires_approval(drafting.DraftIn(kind="notice", subject="s", points=["p"])) is True
        assert drafting.requires_approval(drafting.DraftIn(kind="email", subject="s", points=["p"])) is False
        assert (
            drafting.requires_approval(drafting.DraftIn(kind="email", subject="s", points=["p"], require_approval=True))
            is True
        )
        assert (
            drafting.requires_approval(
                drafting.DraftIn(kind="circular", subject="s", points=["p"], require_approval=False)
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_a_draft_names_only_given_sources_and_its_placeholders(self, on):
        payload = drafting.DraftIn(
            kind="notice",
            subject="Branch timings",
            points=["Timings change to 10 am to 4 pm"],
            sources=[{"id": "pol-1", "title": "Timings policy", "text": "Branches open 10 am to 4 pm."}],
        )
        answer = {
            "title": "Change in branch timings",
            "body": "From [DATE], branches open 10 am to 4 pm. Contact [BRANCH NAME] for appointments.",
            "sections": [{"heading": "Timings", "text": "10 am to 4 pm"}],
            "sources_used": ["pol-1", "made-up"],
            "placeholders": ["[DATE]"],
            "notes": "",
        }
        complete = _completer(_Reply(answer))
        run = await services.run(drafting.SERVICE, TENANT, payload, complete=complete)
        assert run.output["sources_used"] == ["pol-1"] and run.output["placeholders"] == ["[BRANCH NAME]", "[DATE]"]
        assert "Dropped 1 source reference" in run.output["notes"] and run.output["requires_approval"] is True
        assert run.sources == [{"id": "pol-1", "title": "Timings policy", "origin": "inline", "chars": 28}]
        assert run.model == {"model": "model-x", "tokens": 12, "retries": 0}
        prompt = complete.calls[0][1]["content"]
        assert "[pol-1] Timings policy" in prompt and "formal tone" in prompt
        assert run.guardrails == {
            "input": {"findings": 0, "flagged": False, "applied": False},
            "output": {"findings": 0, "flagged": False, "applied": False},
        }

    @pytest.mark.asyncio
    async def test_a_transformed_output_travels_transformed(self, on, monkeypatch):
        import core.governance.guardrails.hooks as hooks

        async def guard_text(stage, text, **_kw):
            if stage == "output":
                return SimpleNamespace(text=text.replace("9876", "XXXX"), findings=1, flagged=True, correlation_id="c")
            return None

        monkeypatch.setattr(hooks, "guard_text", guard_text)
        payload = drafting.DraftIn(kind="email", subject="Card", points=["Card ending 9876 dispatched"])
        answer = {
            "title": "Your card",
            "body": "Your card ending 9876 is on its way.",
            "sections": [],
            "sources_used": [],
        }
        run = await services.run(drafting.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        assert (
            run.output["body"] == "Your card ending XXXX is on its way." and run.guardrails["output"]["findings"] == 1
        )


# ── Summarisation and extraction ──────────────────────────────────────────────


class TestSummarisation:
    @pytest.mark.asyncio
    async def test_key_points_cite_given_documents_and_uncovered_documents_are_named(self, on):
        payload = summarisation.SummariseIn(
            documents=[
                {"id": "a", "text": "Limit is 2 lakh for new accounts."},
                {"id": "b", "text": "After 90 days the limit is 5 lakh."},
            ],
            length="brief",
        )
        answer = {
            "summary": "Limits rise after 90 days.",
            "key_points": [
                {"text": "2 lakh at first", "sources": ["a"]},
                {"text": "5 lakh later", "sources": ["b", "zzz"]},
                {"text": "made up", "sources": ["zzz"]},
            ],
            "per_document": [{"id": "a", "summary": "new accounts"}, {"id": "nope", "summary": "x"}],
            "open_questions": ["Does the limit apply per day?"],
        }
        complete = _completer(_Reply(answer))
        run = await services.run(summarisation.SERVICE, TENANT, payload, complete=complete)
        out = run.output
        assert "about 80 words" in complete.calls[0][1]["content"]
        assert out["key_points"][1]["sources"] == ["b"] and out["key_points"][2]["grounded"] is False
        assert (
            out["ungrounded_points"] == 1
            and out["sources_used"] == ["a", "b"]
            and out["documents_not_covered"] == ["b"]
        )

    @pytest.mark.asyncio
    async def test_no_documents_is_refused(self, on):
        with pytest.raises(services.ContentError) as info:
            await services.run(summarisation.SERVICE, TENANT, summarisation.SummariseIn(), complete=_completer())
        assert info.value.code == "no_documents"


class TestExtraction:
    @pytest.mark.asyncio
    async def test_items_must_quote_their_source_and_deadlines_are_normalised(self, on):
        text = (
            "The vendor shall deliver the monthly report within 5 working days of month end. "
            "The bank shall pay undisputed invoices within 30 days of receipt."
        )
        payload = extraction.ExtractIn(documents=[{"id": "sla", "text": text}], reference_date="2026-10-07")
        answer = {
            "obligations": [
                {
                    "party": "bank",
                    "obligation": "pay invoices",
                    "deadline": "2026-11-06",
                    "deadline_basis": "30 days of receipt",
                    "source_id": "sla",
                    "quote": "pay undisputed invoices within 30 days",
                    "severity": "high",
                },
                {
                    "party": "vendor",
                    "obligation": "deliver report",
                    "deadline": "soon",
                    "deadline_basis": None,
                    "source_id": "sla",
                    "quote": "deliver the MONTHLY report within 5 working days",
                    "severity": "low",
                },
                {
                    "party": "vendor",
                    "obligation": "invented",
                    "deadline": None,
                    "deadline_basis": None,
                    "source_id": "sla",
                    "quote": "shall pay a penalty",
                    "severity": "low",
                },
                {
                    "party": "x",
                    "obligation": "y",
                    "deadline": None,
                    "deadline_basis": None,
                    "source_id": "nope",
                    "quote": "pay",
                    "severity": "low",
                },
            ]
        }
        run = await services.run(extraction.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        out = run.output
        assert out["dropped"] == 2 and [o["party"] for o in out["obligations"]] == ["bank", "vendor"]
        assert out["obligations"][0]["deadline"] == "2026-11-06" and out["obligations"][1]["deadline"] is None
        assert out["obligations"][1]["severity"] == "low" and out["reference_date"] == "2026-10-07"
        assert extraction.rendered(out).startswith("bank: pay invoices")
        assert extraction.apply_text(out, "bank: pay\nvendor: ship")["obligations"][1]["obligation"] == "ship"
        assert extraction.apply_text(out, "only one line") == out


# ── Drafts queue ──────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return [self.row] if self.row is not None else []


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_a, **_k):
        return _Result(self.row)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()


def _draft_row(**overrides):
    base = {
        "id": uuid.uuid4(),
        "service": "draft",
        "kind": "notice",
        "status": "pending_approval",
        "title": "Notice",
        "input": {"kind": "notice"},
        "output": {"body": "b"},
        "sources": [],
        "guardrails": {},
        "created_by": "author",
        "decided_by": None,
        "decision_notes": None,
        "decided_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDrafts:
    @pytest.mark.asyncio
    async def test_a_draft_is_recorded_with_its_status(self, monkeypatch):
        import core.database as database

        session = _Session()
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: session)
        run = services.Run(service="draft", output={"body": "b"}, sources=[], guardrails={}, model={})
        answer = await drafts.record(
            TENANT,
            user_id="u1",
            run=run,
            payload={"kind": "notice"},
            kind="notice",
            title="Notice",
            requires_approval=True,
        )
        assert (
            answer["status"] == "pending_approval"
            and session.added[0].created_by == "u1"
            and answer["output"] == {"body": "b"}
        )

    @pytest.mark.asyncio
    async def test_a_second_person_decides_a_waiting_draft(self, monkeypatch):
        import core.database as database

        row = _draft_row()
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        with pytest.raises(services.ContentError) as info:
            await drafts.decide(TENANT, row.id, user_id="author", decision="approve")
        assert info.value.code == "same_person"
        with pytest.raises(services.ContentError) as info:
            await drafts.decide(TENANT, row.id, user_id="checker", decision="shred")
        assert info.value.code == "decision_unknown"
        answer = await drafts.decide(TENANT, row.id, user_id="checker", decision="approve", notes="fine")
        assert answer["status"] == "approved" and row.decided_by == "checker" and row.decision_notes == "fine"
        with pytest.raises(services.ContentError) as info:
            await drafts.decide(TENANT, row.id, user_id="checker", decision="reject")
        assert info.value.code == "not_pending"
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        with pytest.raises(services.ContentError) as info:
            await drafts.decide(TENANT, uuid.uuid4(), user_id="checker", decision="reject")
        assert info.value.status == 404
        assert await drafts.get_draft(TENANT, uuid.uuid4()) is None
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        listed = await drafts.list_drafts(TENANT, status="approved")
        assert listed[0]["status"] == "approved" and "output" not in listed[0]


# ── Routes ─────────────────────────────────────────────────────────────────────


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_catalogue_answers_while_off_and_the_rest_is_not_found(self, monkeypatch):
        from api.v1 import content as api

        monkeypatch.setattr(settings, "content_services_enabled", False)
        listed = await api.list_services(tenant_id=str(TENANT))
        assert listed["enabled"] is False and {"draft", "summarise", "extract"} <= {
            s["name"] for s in listed["services"]
        }
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        for call in (
            api.post_draft(
                drafting.DraftIn(kind="memo", subject="s", points=["p"]), request, tenant_id=str(TENANT), domains=None
            ),
            api.post_summarise(
                summarisation.SummariseIn(documents=[{"id": "a", "text": "t"}]), tenant_id=str(TENANT), domains=None
            ),
            api.list_drafts(status=None, limit=10, tenant_id=str(TENANT)),
            api.install_service_dataset("draft", request, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_draft_route_runs_the_service_and_keeps_the_draft(self, on, monkeypatch):
        from api.v1 import content as api

        answer = {"title": "Memo", "body": "Body", "sections": [], "sources_used": []}
        monkeypatch.setattr(services, "_complete", _completer(_Reply(answer)))
        recorded = AsyncMock(return_value={"id": "d1", "status": "draft", "output": {}, "input": {}})
        monkeypatch.setattr(drafts, "record", recorded)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        result = await api.post_draft(
            drafting.DraftIn(kind="memo", subject="s", points=["p"]), request, tenant_id=str(TENANT), domains=["ops"]
        )
        assert result["output"]["title"] == "Memo" and result["draft"] == {"id": "d1", "status": "draft"}
        assert recorded.call_args.kwargs["requires_approval"] is False and recorded.call_args.kwargs["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_a_refused_service_call_is_the_error_it_names(self, on, monkeypatch):
        from api.v1 import content as api

        monkeypatch.setattr(
            services,
            "run",
            AsyncMock(side_effect=services.ContentError(422, "guardrail_blocked", "blocked", {"rule": "pan"})),
        )
        with pytest.raises(HTTPException) as info:
            await api.post_extract(
                extraction.ExtractIn(documents=[{"id": "a", "text": "t"}]), tenant_id=str(TENANT), domains=None
            )
        assert info.value.status_code == 422 and info.value.detail["details"] == {"rule": "pan"}

    @pytest.mark.asyncio
    async def test_the_dataset_is_installed_once(self, on, monkeypatch):
        import core.database as database
        from api.v1 import content as api
        from core.evals import datasets

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session())
        created = AsyncMock(return_value=(SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())))
        monkeypatch.setattr(datasets, "create", created)
        monkeypatch.setattr(datasets, "dataset_dict", lambda d: {"id": str(d.id)})
        monkeypatch.setattr(datasets, "version_dict", lambda v: {"id": str(v.id)})
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": str(uuid.uuid4())}))
        answer = await api.install_service_dataset("extract", request, tenant_id=str(TENANT))
        assert answer["installed"] is True and created.call_args.kwargs["name"] == "content: obligation extraction"
        assert len(created.call_args.kwargs["cases"]) == 3
        monkeypatch.setattr(
            datasets, "create", AsyncMock(side_effect=datasets.DatasetError(409, "name_taken", "exists"))
        )
        assert (await api.install_service_dataset("extract", request, tenant_id=str(TENANT)))["installed"] is False
        with pytest.raises(HTTPException) as info:
            await api.install_service_dataset("nope", request, tenant_id=str(TENANT))
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_decisions_and_listing_go_through_the_queue(self, on, monkeypatch):
        from api.v1 import content as api

        monkeypatch.setattr(drafts, "decide", AsyncMock(return_value={"id": "d", "status": "approved"}))
        monkeypatch.setattr(drafts, "list_drafts", AsyncMock(return_value=[{"id": "d"}]))
        monkeypatch.setattr(drafts, "get_draft", AsyncMock(return_value=None))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "checker"}))
        decided = await api.decide_draft(
            uuid.uuid4(), api.DecisionIn(decision="approve"), request, tenant_id=str(TENANT)
        )
        assert decided["status"] == "approved"
        assert (await api.list_drafts(status="approved", limit=10, tenant_id=str(TENANT)))["total"] == 1
        with pytest.raises(HTTPException) as info:
            await api.list_drafts(status="lost", limit=10, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        with pytest.raises(HTTPException) as info:
            await api.get_draft(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
