# SPDX-License-Identifier: Apache-2.0
"""The unified review queue: who sees which kinds, the items in one shape and order, edits, the routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.content import drafts, services
from core.ownership import Caller
from core.workbench import access, assignments, queue

TENANT = uuid.uuid4()
ADMIN = Caller(user_id=uuid.uuid4(), role="admin", domains=None, is_admin=True, is_machine=False)
NOW = datetime.now(UTC)


def _approval(**kw):
    base = {
        "id": uuid.uuid4(),
        "title": "Approval required: pay vendor",
        "trigger_type": "workflow_step",
        "priority": "high",
        "status": "pending",
        "requested_by_user_id": None,
        "assignee_role": "cfo",
        "context": {"step_id": "pay", "_spec": {"x": 1}, "amount": 500},
        "decision_options": {"options": ["approve", "reject"]},
        "created_at": NOW - timedelta(hours=2),
        "expires_at": NOW + timedelta(hours=10),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _document(**kw):
    base = {
        "id": uuid.uuid4(),
        "filename": "slip.pdf",
        "status": "review",
        "created_by": "u2",
        "result": {"documents": [{"document_type": "salary_slip"}]},
        "review_reasons": ["required field net_pay not found"],
        "created_at": NOW - timedelta(days=1),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _draft(**kw):
    base = {
        "id": uuid.uuid4(),
        "service": "drafting",
        "kind": "letter",
        "status": "pending_approval",
        "title": "Welcome letter",
        "created_by": "u3",
        "created_at": NOW - timedelta(minutes=30),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _case(**kw):
    base = {
        "id": uuid.uuid4(),
        "case_ref": "KYB-1",
        "purpose": "onboarding",
        "provider": "registry",
        "state": "awaiting_decision",
        "created_by": "u4",
        "policy_id": "p1",
        "decision_requests": [{"id": "r1"}],
        "created_at": NOW - timedelta(hours=1),
    }
    base.update(kw)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each select with the rows kept for its entity."""

    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.seen: list[str] = []
        self.statements: list[str] = []

    async def execute(self, statement):
        table = statement.get_final_froms()[0].name
        self.seen.append(table)
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": False})))
        return _Result(self.rows_by_table.get(table, []))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)


class TestKinds:
    def test_a_role_sees_the_kinds_its_workbenches_show_and_the_queue_tab_opens_all(self):
        assert queue.kinds_for("admin") == ["approval", "document", "draft", "case"]
        assert queue.kinds_for("cfo") == ["approval", "document", "draft", "case"]  # review officer holds the queue tab
        assert queue.kinds_for("cmo") == ["case"]  # relationship manager: cases only
        assert queue.kinds_for("analyst") == [] and queue.kinds_for("auditor") == []  # no tab of theirs shows a kind
        assert queue.kinds_for("merchant") == []
        assert queue.kinds_for("cmo", {"investigator"}) == ["document", "case"]


class TestItems:
    @pytest.mark.asyncio
    async def test_items_come_in_one_shape_ordered_by_priority_then_age(self, monkeypatch):
        session = _Session(
            {
                "hitl_queue": [_approval()],
                "idp_documents": [_document()],
                "content_drafts": [_draft()],
                "governed_cases": [_case()],
            }
        )
        _use(monkeypatch, session)
        found = await queue.list_items(TENANT, ["approval", "document", "draft", "case"], limit=10, caller=ADMIN)
        assert found["counts"] == {"approval": 1, "document": 1, "draft": 1, "case": 1}
        kinds = [item["kind"] for item in found["items"]]
        # high priority first (the approval and the case, older first), then normal priority by age
        assert kinds == ["approval", "case", "document", "draft"]
        for item in found["items"]:
            assert {
                "kind",
                "id",
                "title",
                "summary",
                "priority",
                "status",
                "created_at",
                "age_seconds",
                "path",
                "actions",
            } <= set(item)
        approval = next(i for i in found["items"] if i["kind"] == "approval")
        assert approval["summary"] == "step_id: pay, amount: 500" and approval["due_at"] and approval["age_seconds"] > 0
        case = next(i for i in found["items"] if i["kind"] == "case")
        assert case["path"] == "/dashboard/approvals/cases/KYB-1" and case["actions"] == ["open"]
        assert set(session.seen) == {"hitl_queue", "idp_documents", "content_drafts", "governed_cases"}

    @pytest.mark.asyncio
    async def test_only_the_wanted_kinds_are_read(self, monkeypatch):
        session = _Session({"content_drafts": [_draft()]})
        _use(monkeypatch, session)
        found = await queue.list_items(TENANT, ["draft", "nothing"], limit=5, caller=ADMIN)
        assert session.seen == ["content_drafts"] and found["kinds"] == ["draft"]
        assert found["items"][0]["title"] == "Welcome letter" and found["items"][0]["priority"] == "normal"

    @pytest.mark.asyncio
    async def test_approvals_are_scoped_to_the_callers_visible_agents(self, monkeypatch):
        session = _Session({"hitl_queue": [_approval()]})
        _use(monkeypatch, session)
        cfo = Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False)
        found = await queue.list_items(TENANT, ["approval"], limit=5, caller=cfo)
        assert found["counts"] == {"approval": 1}
        assert "agents" in session.statements[-1] and "domain IN" in session.statements[-1]
        await queue.list_items(TENANT, ["approval"], limit=5, caller=ADMIN)
        assert "agents" not in session.statements[-1]
        await queue.list_items(TENANT, ["approval"], limit=5)
        assert "false" in session.statements[-1].lower()  # no caller: nothing

    @pytest.mark.asyncio
    async def test_an_item_in_full_with_its_editable_fields(self, monkeypatch):
        from core.content import drafts as draft_store
        from core.idp import store

        monkeypatch.setattr(
            draft_store,
            "get_draft",
            AsyncMock(
                return_value={
                    "id": "d",
                    "status": "pending_approval",
                    "title": "T",
                    "output": {"body": "text", "count": 2},
                }
            ),
        )
        found = await queue.get_item(TENANT, "draft", str(uuid.uuid4()))
        assert (
            found["editable"] == [{"name": "title", "value": "T"}, {"name": "body", "value": "text"}]
            and found["decidable"]
        )

        monkeypatch.setattr(
            store,
            "get_document",
            AsyncMock(
                return_value={
                    "id": "x",
                    "status": "review",
                    "documents": [
                        {
                            "index": 0,
                            "fields": [{"name": "net_pay", "value": None, "status": "missing"}],
                            "extra_fields": [],
                        }
                    ],
                }
            ),
        )
        found = await queue.get_item(TENANT, "document", str(uuid.uuid4()))
        assert found["editable"] == [{"name": "net_pay", "value": "", "document_index": 0, "status": "missing"}]

        row = _approval(context={"step_id": "pay", "review_edits": [{"name": "amount", "value": "450"}]})
        _use(monkeypatch, _Session({"hitl_queue": [row]}))
        found = await queue.get_item(TENANT, "approval", str(row.id))
        assert (
            found["item"]["review_edits"] == [{"name": "amount", "value": "450"}]
            and found["editable"] == []
            and found["decidable"]
        )

        case = _case()
        _use(monkeypatch, _Session({"governed_cases": [case]}))
        found = await queue.get_item(TENANT, "case", "KYB-1")
        assert found["item"]["policy_id"] == "p1" and found["decidable"]

        _use(monkeypatch, _Session({}))
        for kind, item_id in (
            ("approval", "not-a-uuid"),
            ("approval", str(uuid.uuid4())),
            ("case", "KYB-9"),
            ("draft", "x"),
        ):
            with pytest.raises(queue.QueueError) as info:
                await queue.get_item(TENANT, kind, item_id)
            assert info.value.status == 404
        with pytest.raises(queue.QueueError) as info:
            await queue.get_item(TENANT, "nothing", "1")
        assert info.value.code == "kind_unknown"


class TestEdits:
    def test_edits_are_checked(self):
        assert queue.check_edits([{"name": " title ", "value": "x"}]) == [
            {"name": "title", "value": "x", "document_index": 0}
        ]
        assert queue.check_edits([]) == []
        for bad in (
            "x",
            [{"value": "x"}],
            [{"name": "a", "value": 1}],
            [{"name": "a", "document_index": -1}],
            [{"name": "a"}] * 51,
        ):
            with pytest.raises(queue.QueueError) as info:
                queue.check_edits(bad)
            assert info.value.status == 422
        assert queue.edit_note([]) == ""
        assert queue.edit_note([{"name": "amount", "value": "450"}]) == "Edited before the decision: amount=450"

    @pytest.mark.asyncio
    async def test_a_drafts_text_is_edited_with_the_original_kept(self, monkeypatch):
        from core.models.content_draft import ContentDraft

        row = ContentDraft(
            tenant_id=TENANT,
            service="drafting",
            kind="letter",
            status="pending_approval",
            title="Welcome",
            output={"body": "Dear customer", "n": 1},
            created_by="u1",
        )
        session = _Session({"content_drafts": [row]})
        _use(monkeypatch, session)
        answer = await drafts.edit(
            TENANT, uuid.uuid4(), user_id="rev", fields={"title": "Welcome aboard", "body": "Dear valued customer"}
        )
        assert answer["title"] == "Welcome aboard" and answer["output"]["body"] == "Dear valued customer"
        assert answer["edits"]["by"] == "rev" and answer["edits"]["fields"]["body"] == {
            "original": "Dear customer",
            "value": "Dear valued customer",
        }
        answer = await drafts.edit(TENANT, uuid.uuid4(), user_id="rev", fields={"body": "Dear friend"})
        assert answer["edits"]["fields"]["body"]["original"] == "Dear customer"  # the first original stays
        for fields, code in (({"n": "2"}, "field_not_editable"), ({}, "edits_empty")):
            with pytest.raises(services.ContentError) as info:
                await drafts.edit(TENANT, uuid.uuid4(), user_id="rev", fields=fields)
            assert info.value.code == code
        row.status = "approved"
        with pytest.raises(services.ContentError) as info:
            await drafts.edit(TENANT, uuid.uuid4(), user_id="rev", fields={"title": "x"})
        assert info.value.code == "not_pending"
        _use(monkeypatch, _Session({}))
        with pytest.raises(services.ContentError) as info:
            await drafts.edit(TENANT, uuid.uuid4(), user_id="rev", fields={"title": "x"})
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_edits_go_to_the_store_that_owns_the_item(self, monkeypatch):
        from core.idp import store

        monkeypatch.setattr(drafts, "edit", AsyncMock(return_value={"id": "d"}))
        assert await queue.apply_edits(
            TENANT, "draft", str(uuid.uuid4()), [{"name": "title", "value": "x", "document_index": 0}], user_id="r"
        ) == {"id": "d"}
        assert drafts.edit.call_args.kwargs["fields"] == {"title": "x"}
        assert await queue.apply_edits(TENANT, "draft", "x", [], user_id="r") is None

        monkeypatch.setattr(store, "correct", AsyncMock(return_value={"id": "doc"}))
        edits = [
            {"name": "net_pay", "value": "1", "document_index": 0},
            {"name": "name", "value": "A", "document_index": 1},
        ]
        assert await queue.apply_edits(TENANT, "document", str(uuid.uuid4()), edits, user_id="r") == {"id": "doc"}
        assert store.correct.call_count == 2 and store.correct.call_args.kwargs["document_index"] == 1

        row = _approval()
        _use(monkeypatch, _Session({"hitl_queue": [row]}))
        out = await queue.apply_edits(
            TENANT, "approval", str(row.id), [{"name": "amount", "value": "450", "document_index": 0}], user_id="r"
        )
        assert (
            out["review_edits"][0]["name"] == "amount"
            and row.context["review_edits"][0]["by"] == "r"
            and row.context["step_id"] == "pay"
        )
        row.status = "decided"
        with pytest.raises(queue.QueueError) as info:
            await queue.apply_edits(
                TENANT, "approval", str(row.id), [{"name": "a", "value": "b", "document_index": 0}], user_id="r"
            )
        assert info.value.status == 409
        with pytest.raises(queue.QueueError) as info:
            await queue.apply_edits(
                TENANT, "case", "KYB-1", [{"name": "a", "value": "b", "document_index": 0}], user_id="r"
            )
        assert info.value.code == "edits_unsupported"

        from core.idp.pages import DocumentError

        monkeypatch.setattr(store, "correct", AsyncMock(side_effect=DocumentError(404, "field_unknown", "no")))
        with pytest.raises(queue.QueueError) as info:
            await queue.apply_edits(TENANT, "document", str(uuid.uuid4()), edits[:1], user_id="r")
        assert info.value.code == "field_unknown"
        monkeypatch.setattr(drafts, "edit", AsyncMock(side_effect=services.ContentError(409, "not_pending", "no")))
        with pytest.raises(queue.QueueError) as info:
            await queue.apply_edits(TENANT, "draft", str(uuid.uuid4()), edits[:1], user_id="r")
        assert info.value.code == "not_pending"


class TestCounts:
    @pytest.mark.asyncio
    async def test_the_queue_counter_sums_the_four_stores(self, monkeypatch):
        calls: list[str] = []

        async def scalar(statement):
            calls.append(statement.get_final_froms()[0].name)
            return 2

        class Session:
            async def __aenter__(self):
                return SimpleNamespace(scalar=scalar)

            async def __aexit__(self, *args):
                return False

        _use(monkeypatch, Session())
        found = await access.counts(TENANT, {"queue", "conversations"})
        assert found == {"queue": 8, "conversations": 2}
        assert set(calls) == {
            "hitl_queue",
            "idp_documents",
            "content_drafts",
            "governed_cases",
            "conversation_sessions",
        }


class TestRoutes:
    def _request(self, scopes=("agenticorg:admin",)):
        return SimpleNamespace(
            state=SimpleNamespace(claims={"agenticorg:user_id": "u1", "role": "domain_lead"}, scopes=list(scopes))
        )

    @pytest.mark.asyncio
    async def test_the_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import workbench_queue as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        request = self._request()
        for call in (
            api.list_queue(request, kind=None, limit=10, role="cfo", tenant_id=str(TENANT)),
            api.get_item("draft", "x", request, role="cfo", tenant_id=str(TENANT)),
            api.decide(
                "draft",
                "x",
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                request,
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            ),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_list_and_item_routes_narrow_to_the_callers_kinds(self, monkeypatch):
        from api.v1 import workbench_queue as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        monkeypatch.setattr(assignments, "assigned_to", AsyncMock(return_value={"investigator"}))
        monkeypatch.setattr(
            queue,
            "list_items",
            AsyncMock(return_value={"items": [{"kind": "document"}], "counts": {"document": 1}, "kinds": ["document"]}),
        )
        request = self._request()
        found = await api.list_queue(request, kind=None, limit=10, role="cmo", tenant_id=str(TENANT))
        assert found["allowed_kinds"] == ["document", "case"] and found["total"] == 1
        assert queue.list_items.call_args.args[1] == ["document", "case"]
        assert queue.list_items.call_args.kwargs["caller"].role == "domain_lead"
        await api.list_queue(request, kind=["draft", "document"], limit=10, role="cmo", tenant_id=str(TENANT))
        assert queue.list_items.call_args.args[1] == ["document"]  # drafts are not the relationship manager's
        with pytest.raises(HTTPException) as info:
            await api.list_queue(request, kind=["nothing"], limit=10, role="cmo", tenant_id=str(TENANT))
        assert info.value.status_code == 422

        monkeypatch.setattr(
            queue,
            "get_item",
            AsyncMock(return_value={"kind": "document", "item": {}, "editable": [], "decidable": True}),
        )
        assert (await api.get_item("document", "x", request, role="cmo", tenant_id=str(TENANT)))["kind"] == "document"
        with pytest.raises(HTTPException) as info:
            await api.get_item("draft", "x", request, role="cmo", tenant_id=str(TENANT))
        assert info.value.status_code == 404
        monkeypatch.setattr(queue, "get_item", AsyncMock(side_effect=queue.QueueError(404, "not_found", "no")))
        with pytest.raises(HTTPException) as info:
            await api.get_item("document", "x", request, role="cmo", tenant_id=str(TENANT))
        assert info.value.detail["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_a_decision_applies_the_edits_then_goes_through_the_owning_store(self, monkeypatch):
        from api.v1 import approvals as approvals_api
        from api.v1 import workbench_queue as api
        from core.idp import store

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        monkeypatch.setattr(assignments, "assigned_to", AsyncMock(return_value=set()))
        monkeypatch.setattr(queue, "apply_edits", AsyncMock(return_value={"id": "d"}))
        monkeypatch.setattr(drafts, "decide", AsyncMock(return_value={"id": "d", "status": "approved"}))
        monkeypatch.setattr(store, "decide", AsyncMock(return_value={"id": "x", "status": "rejected"}))
        monkeypatch.setattr(approvals_api, "decide", AsyncMock(return_value={"hitl_id": "h", "status": "decided"}))
        request = self._request()
        draft_id = str(uuid.uuid4())
        body = api.DecisionIn(decision="approve", notes="fine", edits=[api.EditIn(name="title", value="New")])
        out = await api.decide(
            "draft",
            draft_id,
            body,
            SimpleNamespace(),
            request,
            role="cfo",
            tenant_id=str(TENANT),
            user_claims={},
            user_domains=None,
        )
        assert out["edited"] is True and out["outcome"]["status"] == "approved"
        assert queue.apply_edits.call_args.args[1:3] == ("draft", draft_id)
        assert drafts.decide.call_args.kwargs["notes"] == "fine Edited before the decision: title=New"

        out = await api.decide(
            "document",
            str(uuid.uuid4()),
            api.DecisionIn(decision="reject"),
            SimpleNamespace(),
            request,
            role="cfo",
            tenant_id=str(TENANT),
            user_claims={},
            user_domains=None,
        )
        assert out["outcome"]["status"] == "rejected" and store.decide.call_args.kwargs["notes"] == ""

        hitl_id = uuid.uuid4()
        out = await api.decide(
            "approval",
            str(hitl_id),
            api.DecisionIn(decision="approve", notes="go"),
            "bg",
            request,
            role="cfo",
            tenant_id=str(TENANT),
            user_claims={"sub": "u1"},
            user_domains=["finance"],
        )
        assert out["outcome"]["status"] == "decided"
        args = approvals_api.decide.call_args.args
        assert (
            args[0] == hitl_id
            and args[1].decision == "approve"
            and args[1].notes == "go"
            and args[2] == "bg"
            and args[6] == "cfo"
            and args[7] == ["finance"]
        )

        with pytest.raises(HTTPException) as info:
            await api.decide(
                "case",
                "KYB-1",
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                request,
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.status_code == 422
        with pytest.raises(HTTPException) as info:
            await api.decide(
                "draft",
                draft_id,
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                self._request(scopes=()),
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.status_code == 403
        with pytest.raises(HTTPException) as info:
            await api.decide(
                "draft",
                draft_id,
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                request,
                role="cmo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.status_code == 404  # the relationship manager's queue has no drafts
        monkeypatch.setattr(queue, "apply_edits", AsyncMock(side_effect=queue.QueueError(409, "decided", "closed")))
        with pytest.raises(HTTPException) as info:
            await api.decide(
                "approval",
                str(hitl_id),
                body,
                SimpleNamespace(),
                request,
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.status_code == 409
        monkeypatch.setattr(queue, "apply_edits", AsyncMock(return_value=None))
        monkeypatch.setattr(
            store,
            "decide",
            AsyncMock(
                side_effect=__import__("core.idp.pages", fromlist=["DocumentError"]).DocumentError(
                    409, "decided", "already"
                )
            ),
        )
        with pytest.raises(HTTPException) as info:
            await api.decide(
                "document",
                str(uuid.uuid4()),
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                request,
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.detail["error"] == "decided"
        with pytest.raises(HTTPException) as info:
            await api.decide(
                "document",
                "not-a-uuid",
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                request,
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.status_code == 404
