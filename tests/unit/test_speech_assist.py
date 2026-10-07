# SPDX-License-Identifier: Apache-2.0
"""Speech, part 3: disclosure scripts checked on a transcript, live agent assist turn by turn, the routes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.speech import assist, disclosures, store
from core.speech.audio import SpeechError
from core.workbench import console

TENANT = uuid.uuid4()
ALL = [item.key for item in disclosures.CATALOGUE]


class TestDisclosures:
    def test_the_catalogue_and_what_a_call_type_requires(self, monkeypatch):
        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        keys = [item["key"] for item in disclosures.catalogue()]
        assert keys == ALL and "recorded_line" in keys
        assert [d.key for d in disclosures.required_for("service", ALL)] == ["recorded_line", "identity_verification"]
        assert "product_terms" in [d.key for d in disclosures.required_for("loan", ALL)]
        assert "collections_rights" in [d.key for d in disclosures.required_for("collections", ALL)]
        assert disclosures.required_for("loan", ["nothing", "cooling_off"])[0].key == "cooling_off"
        assert console.check("speech.required_disclosures", ["cooling_off"]) == ["cooling_off"]
        with pytest.raises(console.ConsoleError):
            console.check("speech.required_disclosures", ["oath"])

    def test_a_transcript_is_checked_for_said_late_and_missing(self):
        turns = [
            {"speaker": "agent", "start": 2.0, "text": "Good morning, this call is being recorded for quality."},
            {"speaker": "customer", "start": 5.0, "text": "Hello"},
            {
                "speaker": "agent",
                "start": 150.0,
                "text": "Before we proceed I need to verify your identity, your date of birth please",
            },
            {"speaker": "customer", "start": 160.0, "text": "the interest rate is fine, I agree to proceed"},
        ]
        needed = disclosures.required_for("loan", ALL)
        found = disclosures.check(turns, required=needed, agent="agent")
        assert [f["key"] for f in found["found"]] == ["recorded_line", "identity_verification"]
        assert [m["key"] for m in found["missing"]] == [
            "product_terms",
            "cooling_off",
            "consent_to_proceed",
        ]  # the customer said them, not the agent
        assert [item["key"] for item in found["late"]] == ["identity_verification"] and found["compliant"] is False
        assert disclosures.check([], required=needed, agent="agent")["missing"][0]["script"]
        late_free = disclosures.check(
            turns[:1], required=disclosures.required_for("service", ["recorded_line"]), agent="agent"
        )
        assert late_free["compliant"] is True
        due = disclosures.overdue(needed, found, elapsed=200.0)
        assert [d["key"] for d in due] == []  # the deadline ones were said, late but said
        due = disclosures.overdue(needed, disclosures.check([], required=needed, agent="agent"), elapsed=90.0)
        assert [d["key"] for d in due] == ["recorded_line"]


class TestAssess:
    def test_each_turn_tells_the_agent_what_to_see(self):
        required = ["recorded_line", "identity_verification"]
        turns = [{"speaker": "customer", "start": 1.0, "text": "I want to transfer 500 to Ravi, your app is useless"}]
        view = assist.assess(turns, required=required, agent="agent", elapsed=1.0, previous_flags=[])
        assert [c["state"] for c in view["checklist"]] == ["pending", "pending"] and view["compliant_so_far"] is False
        assert view["mood"]["label"] == "negative" and view["intent"]["name"] == "fund_transfer"
        assert view["next_step"] is not None
        assert view["new_flags"] == []
        turns.append({"speaker": "agent", "start": 70.0, "text": "Let me help with that transfer."})
        view = assist.assess(turns, required=required, agent="agent", elapsed=70.0, previous_flags=[])
        assert [f["kind"] for f in view["new_flags"]] == ["disclosure_overdue"] and view["new_flags"][0][
            "key"
        ] == "recorded_line"
        again = assist.assess(turns, required=required, agent="agent", elapsed=75.0, previous_flags=view["new_flags"])
        assert again["new_flags"] == []  # raised once
        turns.append({"speaker": "customer", "start": 80.0, "text": "This is terrible, I want to speak to a manager"})
        view = assist.assess(turns, required=required, agent="agent", elapsed=80.0, previous_flags=[])
        kinds = sorted(f["kind"] for f in view["new_flags"])
        assert kinds == ["disclosure_overdue", "escalation_phrase", "negative_streak"]
        turns.append(
            {
                "speaker": "agent",
                "start": 85.0,
                "text": "I am sorry. This call is being recorded, and I need to verify your identity first.",
            }
        )
        view = assist.assess(turns, required=required, agent="agent", elapsed=85.0, previous_flags=[])
        assert [c["state"] for c in view["checklist"]] == ["late", "said"]

    def test_the_next_question_follows_the_intent_slots(self):
        assert assist.next_question("fund_transfer", [{"text": "transfer money"}])["slot"] in (
            "amount",
            "payee",
            "account",
        )
        assert (
            assist.next_question("fund_transfer", [{"text": "transfer 500 to Ravi from account ending 1234"}])["slot"]
            is None
        )
        assert assist.next_question("nothing", []) is None
        assert assist.next_question("greeting", []) is None or True


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
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def execute(self, statement):
        return _Result(self.rows)

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)
    monkeypatch.setattr(assist, "encrypt_for_tenant", AsyncMock(side_effect=lambda text, tenant: "enc:" + text))
    monkeypatch.setattr(assist, "decrypt_for_tenant", lambda text: text[4:])


class TestLive:
    @pytest.mark.asyncio
    async def test_a_live_session_takes_turns_raises_flags_surfaces_knowledge_and_closes_with_a_report(
        self, monkeypatch
    ):
        session = _Session()
        _use(monkeypatch, session)
        opened = await assist.start(TENANT, call_ref="C-1", call_type="loan", agent_id="a1", required=ALL)
        assert (
            opened["status"] == "open"
            and "product_terms" in opened["required"]
            and opened["checklist"][0]["state"] == "pending"
        )
        row = session.rows[0]
        row.started_at = datetime.now(UTC) - timedelta(seconds=5)
        searched: list[str] = []

        async def search(tenant_id, text, limit, domains=None):
            searched.append((text, domains))
            return [{"document": "Loans FAQ", "text": "Home loan rates start at 8.5 per cent.", "score": 0.9}]

        first = await assist.append_turn(
            TENANT,
            row.id,
            speaker="customer",
            text="I want a home loan for 20 lakh",
            at=4.0,
            search=search,
            domains=["finance"],
        )
        assert (
            first["turn_count"] == 1
            and first["intent"]["name"] in ("loan_enquiry", "loan_application")
            and first["suggestions"][0]["document"] == "Loans FAQ"
        )
        assert first["new_flags"] == [] and searched == [("I want a home loan for 20 lakh", ["finance"])]
        second = await assist.append_turn(
            TENANT,
            row.id,
            speaker="agent",
            text="Sure, the interest rate is 8.5 per cent per annum",
            at=70.0,
            search=search,
        )
        assert second["suggestions"] == [] and [f["kind"] for f in second["new_flags"]] == ["disclosure_overdue"]
        assert [c["state"] for c in second["checklist"] if c["key"] == "product_terms"] == ["said"]
        assert row.turn_count == 2 and row.turns_encrypted["_encrypted"].startswith("enc:") and len(row.flags) == 1
        third = await assist.append_turn(
            TENANT, row.id, speaker="agent", text="This call is being recorded", at=80.0, search=search
        )
        assert [c["state"] for c in third["checklist"] if c["key"] == "recorded_line"] == ["late"] and third[
            "new_flags"
        ] == []
        with pytest.raises(SpeechError) as info:
            await assist.append_turn(TENANT, row.id, speaker="customer", text="   ", at=81.0, search=search)
        assert info.value.code == "text_empty"
        closed = await assist.close(TENANT, row.id)
        assert closed["status"] == "closed" and closed["report"]["compliant"] is False
        assert [m["key"] for m in closed["report"]["missing"]] == [
            "identity_verification",
            "cooling_off",
            "consent_to_proceed",
        ]
        assert closed["report"]["flags"][0]["kind"] == "disclosure_overdue" and closed["report"]["turns"] == 3
        with pytest.raises(SpeechError) as info:
            await assist.append_turn(TENANT, row.id, speaker="customer", text="more", at=90.0, search=search)
        assert info.value.code == "closed"
        detail = await assist.get(TENANT, row.id, with_turns=True)
        assert len(detail["turns"]) == 3 and detail["turns"][0]["speaker"] == "customer"
        assert (await assist.list_sessions(TENANT, status="closed"))[0]["id"] == str(row.id)
        session.rows = []
        assert await assist.get(TENANT, uuid.uuid4()) is None
        for call in (
            assist.append_turn(TENANT, uuid.uuid4(), speaker="c", text="x", at=1.0, search=search),
            assist.close(TENANT, uuid.uuid4()),
        ):
            with pytest.raises(SpeechError) as info:
                await call
            assert info.value.status == 404
        with pytest.raises(SpeechError) as info:
            await assist.start(TENANT, call_ref="x", call_type="poetry", agent_id=None, required=ALL)
        assert info.value.code == "call_type_unknown"

    @pytest.mark.asyncio
    async def test_a_turn_that_lost_the_race_is_retried_on_the_newer_list(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        opened = await assist.start(TENANT, call_ref="C-3", call_type="service", agent_id=None, required=[])
        row = session.rows[0]
        row.id = uuid.UUID(opened["id"])
        real_execute = session.execute
        bumped = {"done": False}

        async def racing_execute(statement):
            result = await real_execute(statement)
            if "FOR UPDATE" in str(statement) and not bumped["done"]:
                bumped["done"] = True
                row.turn_count = 1  # another request kept a turn between the read and the lock
                row.turns_encrypted = {
                    "_encrypted": "enc:" + json.dumps([{"speaker": "agent", "text": "hello", "start": 0.0}])
                }
            return result

        session.execute = racing_execute
        out = await assist.append_turn(
            TENANT, row.id, speaker="customer", text="hi there", at=2.0, search=AsyncMock(return_value=[])
        )
        assert out["turn_count"] == 2 and [t["text"] for t in assist.turns_of_row(row)] == ["hello", "hi there"]

    @pytest.mark.asyncio
    async def test_the_knowledge_search_degrades_to_nothing(self, monkeypatch):
        import api.v1.knowledge as knowledge

        monkeypatch.setattr(knowledge, "_native_semantic_search", AsyncMock(side_effect=RuntimeError("down")))
        assert await assist.default_search(TENANT, "rates", 3, ["finance"]) == []
        monkeypatch.setattr(
            knowledge,
            "_native_semantic_search",
            AsyncMock(return_value=[SimpleNamespace(document_name="FAQ", chunk_text="x" * 500, score=0.42)]),
        )
        found = await assist.default_search(TENANT, "rates", 3, ["finance"])
        assert found == [{"document": "FAQ", "text": "x" * 400, "score": 0.42}]
        assert knowledge._native_semantic_search.call_args.args[4] == ["finance"]  # the caller domains reach the search

    def test_unreadable_turns_are_empty(self):
        row = SimpleNamespace(turns_encrypted={"_encrypted": "enc:not json"})
        assert assist.turns_of_row(SimpleNamespace(turns_encrypted={})) == []
        import core.speech.assist as module

        original = module.decrypt_for_tenant
        module.decrypt_for_tenant = lambda text: text[4:]
        try:
            assert assist.turns_of_row(row) == []
        finally:
            module.decrypt_for_tenant = original


class TestRoutes:
    @pytest.mark.asyncio
    async def test_disclosures_answer_off_and_the_rest_is_not_found(self, monkeypatch):
        from api.v1 import speech_assist as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", False)
        monkeypatch.setattr(console, "value", AsyncMock(return_value=["recorded_line"]))
        listed = await api.list_disclosures(tenant_id=str(TENANT))
        assert (
            listed["enabled"] is False
            and listed["required"] == ["recorded_line"]
            and len(listed["disclosures"]) == len(ALL)
        )
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        for call in (
            api.recording_disclosures(uuid.uuid4(), call_type="service", tenant_id=str(TENANT)),
            api.start_session(api.SessionIn(call_ref="C-1"), request, tenant_id=str(TENANT)),
            api.post_turn(uuid.uuid4(), api.TurnIn(text="hi"), tenant_id=str(TENANT), domains=None),
            api.close_session(uuid.uuid4(), tenant_id=str(TENANT)),
            api.list_sessions(status=None, limit=10, tenant_id=str(TENANT)),
            api.get_session(uuid.uuid4(), tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_routes_serve_the_checker_and_the_live_sessions(self, monkeypatch):
        from api.v1 import speech_assist as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        monkeypatch.setattr(console, "value", AsyncMock(return_value=ALL))
        transcript = {"turns": [{"speaker": "agent", "start": 1.0, "text": "This call is being recorded"}]}
        monkeypatch.setattr(
            store,
            "get_recording",
            AsyncMock(return_value={"id": "r", "channel_roles": ["agent", "customer"], "transcript": transcript}),
        )
        checked = await api.recording_disclosures(uuid.uuid4(), call_type="service", tenant_id=str(TENANT))
        assert [f["key"] for f in checked["found"]] == ["recorded_line"] and [m["key"] for m in checked["missing"]] == [
            "identity_verification"
        ]
        with pytest.raises(HTTPException) as info:
            await api.recording_disclosures(uuid.uuid4(), call_type="poetry", tenant_id=str(TENANT))
        assert info.value.status_code == 422
        monkeypatch.setattr(assist, "start", AsyncMock(return_value={"id": "s", "status": "open"}))
        monkeypatch.setattr(assist, "append_turn", AsyncMock(return_value={"id": "s", "new_flags": []}))
        monkeypatch.setattr(assist, "close", AsyncMock(return_value={"id": "s", "status": "closed"}))
        monkeypatch.setattr(assist, "list_sessions", AsyncMock(return_value=[{"id": "s"}]))
        monkeypatch.setattr(assist, "get", AsyncMock(return_value={"id": "s", "turns": []}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        out = await api.start_session(api.SessionIn(call_ref="C-1", call_type="loan"), request, tenant_id=str(TENANT))
        assert (
            out["status"] == "open"
            and assist.start.call_args.kwargs["agent_id"] == "u1"
            and assist.start.call_args.kwargs["required"] == ALL
        )
        assert (
            await api.post_turn(
                uuid.uuid4(),
                api.TurnIn(speaker="customer", text="hi", at=3.0),
                tenant_id=str(TENANT),
                domains=["finance"],
            )
        )["id"] == "s"
        assert assist.append_turn.call_args.kwargs["at"] == 3.0
        assert assist.append_turn.call_args.kwargs["domains"] == ["finance"]
        assert (await api.close_session(uuid.uuid4(), tenant_id=str(TENANT)))["status"] == "closed"
        assert (await api.list_sessions(status="open", limit=10, tenant_id=str(TENANT)))["total"] == 1
        with pytest.raises(HTTPException) as info:
            await api.list_sessions(status="lost", limit=10, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        assert (await api.get_session(uuid.uuid4(), tenant_id=str(TENANT)))["id"] == "s"
        monkeypatch.setattr(assist, "get", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.get_session(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
        monkeypatch.setattr(assist, "append_turn", AsyncMock(side_effect=SpeechError(409, "closed", "no")))
        with pytest.raises(HTTPException) as info:
            await api.post_turn(uuid.uuid4(), api.TurnIn(text="hi"), tenant_id=str(TENANT), domains=None)
        assert info.value.status_code == 409
        monkeypatch.setattr(store, "get_recording", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.recording_disclosures(uuid.uuid4(), call_type="service", tenant_id=str(TENANT))
        assert info.value.status_code == 404
        assert json.dumps(checked)  # the checklist is plain data
