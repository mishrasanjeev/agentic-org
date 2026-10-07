# SPDX-License-Identifier: Apache-2.0
"""The business console: the catalogue and its bounds, the store, the effective values, and where they take effect."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from core.config import settings
from core.workbench import console

TENANT = uuid.uuid4()


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
    def __init__(self, rows=None, *, fail=False):
        self.rows = list(rows or [])
        self.added: list = []
        self.deleted: list = []
        self.fail = fail

    async def execute(self, statement):
        if self.fail is OperationalError:
            raise OperationalError("select", {}, Exception("connection refused"))
        if self.fail:
            raise RuntimeError("no database")
        text = str(statement)
        if "business_settings.key = " in text or "business_settings.key IN" in text:
            params = statement.compile().params
            keys: set[str] = set()
            for k, v in params.items():
                if k.startswith("key"):
                    keys |= set(v) if isinstance(v, list | tuple) else {v}
            return _Result([r for r in self.rows if r.key in keys] if keys else self.rows)
        return _Result(self.rows)

    def add(self, row):
        self.added.append(row)
        if getattr(row, "__tablename__", "") == "business_settings":
            self.rows.append(row)

    async def delete(self, row):
        self.deleted.append(row)
        self.rows = [r for r in self.rows if r is not row]

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)


def _row(key, value, previous=None):
    from core.models.business_setting import BusinessSetting

    return BusinessSetting(tenant_id=TENANT, key=key, value=value, previous=previous, updated_by="admin-1")


class TestCatalogue:
    def test_keys_are_unique_grouped_and_defaults_pass_their_own_checks(self):
        items = console.catalogue()
        keys = [item.key for item in items]
        assert len(keys) == len(set(keys)) and len(keys) == 8
        groups = {key for key, _ in console.GROUPS}
        for item in items:
            assert item.group in groups
            assert console.check(item.key, item.default) == item.default
            assert item.to_dict()["options"] == list(item.options)
        assert "bank_statement" in console.definition("documents.always_review_types").options
        assert set(console.definition("content.approval_kinds").options) >= {"notice", "circular", "letter"}
        assert "fund_transfer" in console.definition("conversations.amount_limits").options
        with pytest.raises(console.ConsoleError) as info:
            console.definition("nothing")
        assert info.value.status == 404

    def test_speech_settings_appear_only_while_speech_is_on(self, monkeypatch):
        monkeypatch.setattr(settings, "speech_intelligence_enabled", False)
        assert not [k for k in console.definitions() if k.startswith("speech.")]
        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        speech = [k for k in console.definitions() if k.startswith("speech.")]
        assert "speech.required_disclosures" in speech
        assert [key for key, _ in console.GROUPS][-1] == "speech"

    def test_values_are_checked_against_their_kind_and_bounds(self):
        assert console.check("documents.type_confidence_floor", 0.8) == 0.8
        assert console.check("conversations.slot_retries", 5) == 5
        assert console.check("documents.always_review_types", ["bank_statement", "bank_statement"]) == [
            "bank_statement"
        ]
        assert console.check("conversations.amount_limits", {"fund_transfer": 50000}) == {"fund_transfer": 50000.0}
        rules = console.check("queue.priority_rules", [{"field": "amount", "op": ">=", "value": 100000}])
        assert rules == [{"kind": "any", "field": "amount", "op": ">=", "value": 100000, "priority": "high"}]
        bad = [
            ("documents.type_confidence_floor", 1.5),
            ("documents.type_confidence_floor", "0.5"),
            ("documents.type_confidence_floor", True),
            ("conversations.slot_retries", 2.5),
            ("conversations.slot_retries", 0),
            ("documents.always_review_types", ["passport_of_mars"]),
            ("documents.always_review_types", "bank_statement"),
            ("conversations.amount_limits", {"balance_enquiry": 5}),
            ("conversations.amount_limits", {"fund_transfer": 0}),
            ("queue.priority_rules", [{"field": "", "op": "==", "value": 1}]),
            ("queue.priority_rules", [{"field": "a", "op": "between", "value": 1}]),
            ("queue.priority_rules", [{"field": "a", "op": ">=", "value": "x"}]),
            ("queue.priority_rules", [{"field": "a", "op": "==", "value": 1, "priority": "top"}]),
            ("queue.priority_rules", [{"field": "a", "op": "==", "value": 1, "kind": "ticket"}]),
            ("queue.priority_rules", "x"),
        ]
        for key, value in bad:
            with pytest.raises(console.ConsoleError) as info:
                console.check(key, value)
            assert info.value.status == 422, (key, value)


class TestStore:
    @pytest.mark.asyncio
    async def test_values_show_the_tenants_value_or_the_default(self, monkeypatch):
        _use(monkeypatch, _Session([_row("documents.type_confidence_floor", 0.8, 0.6)]))
        found = {item["key"]: item for item in await console.values(TENANT)}
        assert found["documents.type_confidence_floor"]["value"] == 0.8
        assert found["documents.type_confidence_floor"]["source"] == "set"
        assert found["documents.type_confidence_floor"]["previous"] == 0.6
        assert found["documents.type_confidence_floor"]["updated_by"] == "admin-1"
        assert found["content.approval_kinds"]["value"] == ["notice", "circular"]
        assert found["content.approval_kinds"]["source"] == "default"

    @pytest.mark.asyncio
    async def test_put_keeps_the_previous_value_and_writes_an_audit_row(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        first = await console.put(TENANT, "conversations.slot_retries", 4, actor="admin-1")
        assert first["value"] == 4 and first["previous"] is None and first["source"] == "set"
        second = await console.put(TENANT, "conversations.slot_retries", 5, actor="admin-2")
        assert second["value"] == 5 and second["previous"] == 4 and second["updated_by"] == "admin-2"
        audits = [a for a in session.added if getattr(a, "__tablename__", "") == "audit_log"]
        assert len(audits) == 2 and audits[1].details == {
            "key": "conversations.slot_retries",
            "previous": 4,
            "value": 5,
        }
        assert audits[1].actor_id == "admin-2" and audits[1].event_type == "workbench.console.set"
        with pytest.raises(console.ConsoleError):
            await console.put(TENANT, "conversations.slot_retries", 99, actor="admin-1")
        reset = await console.reset(TENANT, "conversations.slot_retries", actor="admin-1")
        assert reset["value"] == 3 and reset["source"] == "default" and len(session.deleted) == 1
        assert [a.event_type for a in session.added if getattr(a, "__tablename__", "") == "audit_log"][
            -1
        ] == "workbench.console.reset"
        assert (await console.reset(TENANT, "conversations.slot_retries", actor="admin-1"))["source"] == "default"

    @pytest.mark.asyncio
    async def test_effective_values_follow_the_flag_the_store_and_the_checks(self, monkeypatch):
        session = _Session([_row("documents.type_confidence_floor", 0.9), _row("conversations.slot_retries", "bad")])
        _use(monkeypatch, session)
        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        assert await console.effective(TENANT, ["documents.type_confidence_floor"]) == {
            "documents.type_confidence_floor": 0.6
        }
        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        found = await console.effective(
            TENANT, ["documents.type_confidence_floor", "conversations.slot_retries", "nothing"]
        )
        assert found == {
            "documents.type_confidence_floor": 0.9,
            "conversations.slot_retries": 3,
        }  # a bad row is ignored
        _use(monkeypatch, _Session(fail=True))
        assert await console.value(TENANT, "documents.type_confidence_floor") == 0.6
        _use(monkeypatch, _Session(fail=OperationalError))
        assert await console.value(TENANT, "documents.type_confidence_floor") == 0.6  # a database outage too
        assert await console.effective(TENANT, []) == {}


class TestReaders:
    @pytest.mark.asyncio
    async def test_the_services_rules_come_from_the_effective_values(self, monkeypatch):
        monkeypatch.setattr(
            console,
            "effective",
            AsyncMock(
                side_effect=lambda tenant_id, keys: {
                    "documents.type_confidence_floor": 0.8,
                    "documents.field_confidence_floor": 0.9,
                    "documents.always_review_types": ["bank_statement"],
                    "content.approval_kinds": ["letter"],
                    "conversations.slot_retries": 1,
                    "conversations.negative_turns_before_handoff": 3,
                    "conversations.amount_limits": {"fund_transfer": 500},
                }
            ),
        )
        rules = await console.document_rules(TENANT)
        assert (rules.type_floor, rules.field_floor, rules.always_review) == (0.8, 0.9, ("bank_statement",))
        assert await console.approval_kinds(TENANT) == ("letter",)
        talk = await console.conversation_rules(TENANT)
        assert (talk.retries, talk.negative_turns, talk.amount_limits) == (1, 3, {"fund_transfer": 500.0})

    def test_document_review_follows_the_rules(self):
        from core.idp import fields, pipeline

        found = [fields.Field("net_pay", "1", 0.75, status="ok", required=True)]
        assert pipeline.review_of("salary_slip", 0.65, found, []) == {"needed": False, "reasons": []}
        strict = pipeline.ReviewRules(type_floor=0.7, field_floor=0.8)
        reasons = pipeline.review_of("salary_slip", 0.65, found, [], rules=strict)["reasons"]
        assert reasons == ["document type confidence 0.65 below 0.7", "field net_pay confidence 0.75 below 0.8"]
        always = pipeline.ReviewRules(always_review=("salary_slip",))
        assert pipeline.review_of("salary_slip", 0.9, found, [], rules=always)["reasons"] == [
            "document type salary_slip is always reviewed"
        ]

    def test_draft_approval_follows_the_kinds(self):
        from core.content import drafting

        letter = drafting.DraftIn(kind="letter", subject="s", points=["a point about the matter"], audience="customer")
        assert drafting.requires_approval(letter) is False
        assert drafting.requires_approval(letter, kinds=("letter",)) is True
        notice = drafting.DraftIn(kind="notice", subject="s", points=["a point about the matter"], audience="customer")
        assert drafting.requires_approval(notice, kinds=("letter",)) is False

    def test_the_dialogue_follows_retries_and_amount_ceilings(self):
        from core.conversation import dialogue as engine

        strict = engine.Rules(retries=1, amount_limits={"fund_transfer": 500})
        assert strict.ceiling(engine.INTENTS["fund_transfer"]) == 500.0
        assert strict.ceiling(engine.INTENTS["balance_enquiry"]) == engine.INTENTS["balance_enquiry"].max_amount
        assert engine.Rules().ceiling(engine.INTENTS["fund_transfer"]) == engine.INTENTS["fund_transfer"].max_amount

        dialogue = engine.Dialogue()
        engine.advance(dialogue, "transfer money to Ravi", rules=strict)
        refused = engine.advance(dialogue, "600", rules=strict)
        assert refused.kind == "escalate"  # one bad answer and the strict rules hand over

        lenient = engine.Rules(retries=3, amount_limits={"fund_transfer": 500})
        dialogue = engine.Dialogue()
        engine.advance(dialogue, "transfer money to Ravi", rules=lenient)
        asked = engine.advance(dialogue, "600", rules=lenient)
        assert asked.kind == "ask" and "₹500" in asked.text.replace(",", "")
        assert engine.advance(dialogue, "400", rules=lenient).kind != "ask" or dialogue.slots.get("amount") == 400

        dialogue = engine.Dialogue()
        started = engine.advance(dialogue, "transfer 600 to Ravi", rules=lenient)
        assert (
            dialogue.slots.get("amount") is None and started.kind == "ask"
        )  # the prefilled amount is above the ceiling

    def test_queue_priority_rules_match_on_facts(self):
        items = [
            {"kind": "approval", "priority": "normal", "facts": {"amount": 250000, "step_id": "pay"}},
            {"kind": "document", "priority": "normal", "facts": {"document_types": ["bank_statement"]}},
            {"kind": "draft", "priority": "normal", "facts": {"service": "drafting"}},
            {"kind": "case", "priority": "high", "facts": {"purpose": "onboarding"}},
        ]
        rules = [
            {"kind": "approval", "field": "amount", "op": ">=", "value": 100000, "priority": "critical"},
            {"kind": "any", "field": "document_types", "op": "contains", "value": "Bank_Statement", "priority": "high"},
            {"kind": "draft", "field": "service", "op": "==", "value": "drafting", "priority": "low"},
            {"kind": "case", "field": "purpose", "op": "<=", "value": 5, "priority": "low"},
        ]
        out = console.apply_priority_rules(items, rules)
        assert [i["priority"] for i in out] == ["critical", "high", "low", "high"]
        assert out[0]["priority_rule"] == "amount >= 100000" and "priority_rule" not in out[3]
        assert console.apply_priority_rules(items, []) is items


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import workbench_console as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        for call in (
            api.list_settings(tenant_id=str(TENANT)),
            api.set_setting("conversations.slot_retries", api.ValueIn(value=4), request, tenant_id=str(TENANT)),
            api.reset_setting("conversations.slot_retries", request, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_routes_list_set_and_reset_through_the_console(self, monkeypatch):
        from api.v1 import workbench_console as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        session = _Session()
        _use(monkeypatch, session)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        listed = await api.list_settings(tenant_id=str(TENANT))
        assert [g["key"] for g in listed["groups"]] == [
            "documents",
            "content",
            "conversations",
            "queue",
        ] and listed["total"] == 8
        out = await api.set_setting("conversations.slot_retries", api.ValueIn(value=4), request, tenant_id=str(TENANT))
        assert out["value"] == 4 and out["updated_by"] == "u1"
        with pytest.raises(HTTPException) as info:
            await api.set_setting("conversations.slot_retries", api.ValueIn(value=0), request, tenant_id=str(TENANT))
        assert info.value.status_code == 422 and info.value.detail["error"] == "value_invalid"
        with pytest.raises(HTTPException) as info:
            await api.set_setting("nothing", api.ValueIn(value=0), request, tenant_id=str(TENANT))
        assert info.value.status_code == 404
        assert (await api.reset_setting("conversations.slot_retries", request, tenant_id=str(TENANT)))[
            "source"
        ] == "default"


class TestConversationRoute:
    @pytest.mark.asyncio
    async def test_the_direct_turns_route_takes_the_rules(self, monkeypatch):
        from api.v1 import conversation as api
        from core.conversation import dialogue as engine
        from core.conversation import runtime

        seen: dict[str, object] = {}
        real_advance = engine.advance

        def spy(dialogue, text, **kwargs):
            seen.update(kwargs)
            return real_advance(dialogue, text, **kwargs)

        monkeypatch.setattr(engine, "advance", spy)
        monkeypatch.setattr(runtime, "enabled", lambda: True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=engine.Dialogue()))
        monkeypatch.setattr(runtime, "held_turn", AsyncMock(return_value=None))
        monkeypatch.setattr(runtime, "finish_turn", AsyncMock(return_value={"answer": "ok"}))
        monkeypatch.setattr(
            runtime,
            "business_rules",
            AsyncMock(
                return_value=console.ConversationRules(
                    retries=1, negative_turns=2, amount_limits={"fund_transfer": 500}
                )
            ),
        )
        monkeypatch.setattr(api, "_execution_context", AsyncMock(return_value=None))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        assert await api.post_turn(api.TurnIn(text="transfer money to Ravi"), request, tenant_id=str(TENANT)) == {
            "answer": "ok"
        }
        rules = seen["rules"]
        assert isinstance(rules, engine.Rules) and rules.retries == 1 and rules.amount_limits == {"fund_transfer": 500}
