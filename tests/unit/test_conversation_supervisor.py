# SPDX-License-Identifier: Apache-2.0
"""Conversational services: the escalation hand-off, the supervisor's view, takeover and the held session."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.conversation import dialogue as engine
from core.conversation import escalation, runtime, supervisor
from core.conversation.dialogue import Dialogue

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()


def _dialogue(*texts: str) -> Dialogue:
    dialogue = Dialogue()
    for text in texts:
        engine.advance(dialogue, text)
    return dialogue


class TestHandoffContent:
    def test_the_summary_names_the_intent_the_slots_the_reason_and_the_last_message(self):
        dialogue = _dialogue("transfer 500 to Ravi", "I want to talk to a human")
        text = escalation.summary_text(dialogue, reason=escalation.REASON_REQUESTED)
        assert text.startswith("Hand-off from chat: fund transfer") or text.startswith(
            "Hand-off from chat: talk to a person"
        )
        assert "the user asked for a person" in text and 'Last message: "I want to talk to a human"' in text
        assert escalation.intent_tag(Dialogue()) == "general"

    def test_tickets_carry_the_tag_the_summary_and_the_transcript(self):
        lines = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]
        ticket = escalation.ticket_params(
            "create_ticket", summary="S", tag="fund_transfer", lines=lines, reason="requested"
        )
        assert ticket["subject"] == "Chat hand-off: fund_transfer" and ticket["tags"] == [
            "chat_handoff",
            "fund_transfer",
        ]
        assert "user: hi" in ticket["description"] and ticket["priority"] == "normal"
        incident = escalation.ticket_params(
            "create_incident", summary="S", tag="card_block", lines=lines, reason="fallbacks"
        )
        assert incident["short_description"] == "Chat hand-off: card_block" and incident["urgency"] == "2"
        assert escalation.ticket_tool(["zoho:get_balance", "zendesk:create_ticket"]) == "zendesk:create_ticket"
        assert escalation.ticket_tool(["send_email"]) is None

    @pytest.mark.asyncio
    async def test_a_handoff_records_the_review_item_raises_the_ticket_and_marks_the_session(self, monkeypatch):
        dialogue = _dialogue("I want to speak to someone")
        monkeypatch.setattr(escalation, "_review_item", AsyncMock(return_value="hitl-1"))
        ran = AsyncMock(return_value={"status": "executed", "result": {"ticket": {"id": 4711}}})
        monkeypatch.setattr(runtime, "run_tool", ran)
        marked = AsyncMock()
        announced = AsyncMock()
        monkeypatch.setattr(supervisor, "mark_escalated", marked)
        monkeypatch.setattr(supervisor, "announce", announced)
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["zendesk:create_ticket"], run_grant=object()
        )

        record = await escalation.handoff(
            TENANT,
            session_key="web:c:a1:u:u1",
            dialogue=dialogue,
            user_id="u1",
            agent_id="a1",
            channel="web",
            reason=escalation.REASON_REQUESTED,
            context=context,
        )

        assert record["hitl_id"] == "hitl-1" and record["ticket"] == {
            "status": "executed",
            "tool": "create_ticket",
            "reference": "4711",
        }
        params = ran.call_args.args[2]
        assert params["subject"].startswith("Chat hand-off") and "Transcript" in params["description"]
        assert (
            marked.call_args.args[1] == "web:c:a1:u:u1"
            and announced.call_args.kwargs["event"] == "conversation.escalated"
        )
        assert "reference is 4711" in escalation.handoff_answer(record)

    @pytest.mark.asyncio
    async def test_without_an_agent_or_a_ticket_tool_the_handoff_still_leaves_a_record(self, monkeypatch):
        dialogue = _dialogue("talk to a person")
        monkeypatch.setattr(supervisor, "mark_escalated", AsyncMock())
        monkeypatch.setattr(supervisor, "announce", AsyncMock())
        record = await escalation.handoff(
            TENANT,
            session_key="k",
            dialogue=dialogue,
            user_id="u1",
            agent_id="",
            channel="web",
            reason=escalation.REASON_REQUESTED,
            context=None,
            intent="talk_to_agent",
        )
        assert record["hitl_id"] is None and record["ticket"] is None and record["intent"] == "talk_to_agent"
        # The session is still marked for a supervisor, but nobody was asked to take over: nothing is promised.
        told = escalation.handoff_answer(record)
        assert "nothing has been handed over" in told and "handed this over" not in told


class TestTurns:
    @pytest.mark.asyncio
    async def test_asking_for_a_person_in_chat_hands_off_and_tells_the_user(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())
        handoff = AsyncMock(
            return_value={
                "reason": "requested",
                "intent": "talk_to_agent",
                "hitl_id": "h1",
                "ticket": None,
                "summary": "s",
                "at": "now",
            }
        )
        monkeypatch.setattr(escalation, "handoff", handoff)

        answer = await runtime.chat_turn(
            tenant_id=str(TENANT), company_id="c", user_id="u", agent_id="a1", text="I want to talk to a human"
        )

        assert answer is not None and answer["outcome"]["kind"] == "escalate"
        assert answer["outcome"]["handoff"] == {
            "reason": "requested",
            "intent": "talk_to_agent",
            "hitl_id": "h1",
            "ticket": None,
        }
        assert (
            "handed this over" in answer["answer"] and handoff.call_args.kwargs["reason"] == escalation.REASON_REQUESTED
        )

    @pytest.mark.asyncio
    async def test_a_held_session_sends_the_message_to_the_supervisor_instead_of_the_runtime(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value="sup-1"))
        forwarded = AsyncMock(return_value={"id": "s"})
        monkeypatch.setattr(supervisor, "user_message", forwarded)
        advanced = AsyncMock()
        monkeypatch.setattr(runtime, "finish_turn", advanced)

        answer = await runtime.chat_turn(
            tenant_id=str(TENANT), company_id="c", user_id="u", agent_id="a1", text="transfer 500 to Ravi"
        )

        assert (
            answer is not None
            and answer["outcome"]["kind"] == "handed_over"
            and answer["answer"] == runtime.HELD_ANSWER
        )
        assert forwarded.call_args.args[2] == "transfer 500 to Ravi" and advanced.await_count == 0

    @pytest.mark.asyncio
    async def test_every_turn_is_announced_to_the_live_feed(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        announced = AsyncMock()
        monkeypatch.setattr(supervisor, "announce_turn", announced)

        answer = await runtime.chat_turn(
            tenant_id=str(TENANT), company_id="c", user_id="u", agent_id="", text="transfer 500 to Ravi"
        )

        assert answer is not None and answer["outcome"]["kind"] == "confirm"
        roles = [call.kwargs["role"] for call in announced.call_args_list]
        assert roles == ["user", "assistant"] and announced.call_args_list[1].kwargs["stage"] == "confirming"


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
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_args, **_kw):
        return _Result(self.row)


def _row(**overrides):
    base = {
        "id": uuid.uuid4(),
        "session_key": "web:c:a1:u:u1",
        "user_id": "u1",
        "agent_id": None,
        "channel": "web",
        "status": "active",
        "intent": "fund_transfer",
        "state": {
            "stage": "confirming",
            "history": [{"role": "user", "text": "transfer 500 to Ravi"}],
            "slots": {"amount": 500.0},
        },
        "turns": 1,
        "taken_over_by": None,
        "taken_over_at": None,
        "escalation": None,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_the_view_lists_the_session_and_its_transcript_without_the_whole_state(self, monkeypatch):
        import core.database as database

        row = _row(
            escalation={
                "reason": "requested",
                "intent": "fund_transfer",
                "at": "t",
                "hitl_id": "h",
                "ticket": None,
                "summary": "S",
            }
        )
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        listed = await supervisor.list_live(TENANT)
        assert (
            listed[0]["stage"] == "confirming"
            and listed[0]["escalation"]["hitl_id"] == "h"
            and "history" not in listed[0]
        )
        view = await supervisor.transcript(TENANT, row.id)
        assert (
            view is not None
            and view["history"][0]["text"] == "transfer 500 to Ravi"
            and view["escalation_summary"] == "S"
        )
        assert view["slots"] == {"amount": 500.0}

    @pytest.mark.asyncio
    async def test_takeover_reply_and_release_change_the_holder_and_announce(self, monkeypatch):
        import core.database as database

        row = _row()
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        announced = AsyncMock()
        monkeypatch.setattr(supervisor, "announce", announced)

        taken = await supervisor.takeover(TENANT, row.id, "sup-1")
        assert taken is not None and row.taken_over_by == "sup-1" and row.state["history"][-1]["role"] == "system"
        assert announced.call_args.kwargs["event"] == "conversation.takeover"

        refused = await supervisor.reply(TENANT, row.id, "someone-else", "hello")
        assert refused is not None and refused["refused"] == "not_taken_over"
        replied = await supervisor.reply(TENANT, row.id, "sup-1", "Hello, I am here to help.")
        last = row.state["history"][-1]
        assert replied is not None and {k: last[k] for k in ("role", "text")} == {
            "role": "supervisor",
            "text": "Hello, I am here to help.",
        }
        assert last["at"]
        assert announced.call_args.kwargs == {"event": "conversation.message", "role": "supervisor"}

        released = await supervisor.release(TENANT, row.id, "sup-1")
        assert (
            released is not None
            and row.taken_over_by is None
            and announced.call_args.kwargs["event"] == "conversation.release"
        )
        assert await supervisor.taken_over(TENANT, row.session_key) is None

    @pytest.mark.asyncio
    async def test_a_feed_failure_never_fails_the_turn(self, monkeypatch):
        import api.websocket.feed as feed

        monkeypatch.setattr(feed, "broadcast_to_tenant", AsyncMock(side_effect=RuntimeError("broker down")))
        await supervisor.announce(TENANT, "k", event="conversation.turn", role="user", text="x")


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_console_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import conversation_supervisor as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", False)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "sup"}))
        for call in (
            api.list_sessions(limit=10, include_idle=False, tenant_id=str(TENANT)),
            api.get_session(uuid.uuid4(), tenant_id=str(TENANT)),
            api.take_over(uuid.uuid4(), request, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_reply_without_the_hold_is_refused(self, monkeypatch):
        from api.v1 import conversation_supervisor as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(supervisor, "reply", AsyncMock(return_value={"id": "s", "refused": "not_taken_over"}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "sup"}))
        with pytest.raises(HTTPException) as info:
            await api.send_reply(uuid.uuid4(), api.ReplyIn(text="hi"), request, tenant_id=str(TENANT))
        assert info.value.status_code == 409

    def test_the_ui_and_the_chat_route_carry_the_session_key_and_the_supervisor_page(self):
        chat = (ROOT / "api" / "v1" / "chat.py").read_text(encoding="utf-8")
        assert '"session_key": handled.get("session_key")' in chat
        panel = (ROOT / "ui" / "src" / "components" / "ChatPanel.tsx").read_text(encoding="utf-8")
        assert 'event.type !== "conversation.message"' in panel
        app = (ROOT / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")
        assert 'path="/dashboard/conversations"' in app


class TestConsoleRoutes:
    @pytest.mark.asyncio
    async def test_the_console_lists_opens_takes_over_replies_and_releases(self, monkeypatch):
        from api.v1 import conversation_supervisor as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        session_id = uuid.uuid4()
        view = {"id": str(session_id), "session_key": "k", "taken_over_by": "sup"}
        monkeypatch.setattr(supervisor, "list_live", AsyncMock(return_value=[view]))
        monkeypatch.setattr(supervisor, "transcript", AsyncMock(return_value={**view, "history": []}))
        monkeypatch.setattr(supervisor, "takeover", AsyncMock(return_value=view))
        monkeypatch.setattr(supervisor, "reply", AsyncMock(return_value=view))
        monkeypatch.setattr(supervisor, "release", AsyncMock(return_value={**view, "taken_over_by": None}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "sup"}))

        listed = await api.list_sessions(limit=10, include_idle=True, tenant_id=str(TENANT))
        assert listed == {"sessions": [view], "total": 1}
        assert (await api.get_session(session_id, tenant_id=str(TENANT)))["history"] == []
        assert (await api.take_over(session_id, request, tenant_id=str(TENANT)))["taken_over_by"] == "sup"
        assert (await api.send_reply(session_id, api.ReplyIn(text="hello"), request, tenant_id=str(TENANT)))[
            "id"
        ] == str(session_id)
        assert (await api.release_session(session_id, request, tenant_id=str(TENANT)))["taken_over_by"] is None
        assert supervisor.reply.call_args.args[2:] == ("sup", "hello")

    @pytest.mark.asyncio
    async def test_an_unknown_session_is_not_found_on_every_route(self, monkeypatch):
        from api.v1 import conversation_supervisor as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        for name in ("transcript", "takeover", "reply", "release"):
            monkeypatch.setattr(supervisor, name, AsyncMock(return_value=None))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "sup"}))
        session_id = uuid.uuid4()
        for call in (
            api.get_session(session_id, tenant_id=str(TENANT)),
            api.take_over(session_id, request, tenant_id=str(TENANT)),
            api.send_reply(session_id, api.ReplyIn(text="x"), request, tenant_id=str(TENANT)),
            api.release_session(session_id, request, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_the_review_item_of_a_handoff_is_written_for_the_agent(self, monkeypatch):
        import core.database as database
        import core.push.sender as sender

        added: list = []
        agent_row = SimpleNamespace(name="Branch assistant", visibility="tenant", owner_user_id=None)

        class _Found:
            def scalar_one_or_none(self):
                return agent_row

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *_a, **_k):
                return _Found()

            def add(self, row):
                added.append(row)

            async def flush(self):
                return None

        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session())
        notify = AsyncMock(return_value={})
        monkeypatch.setattr(sender, "notify_approval_created", notify)
        agent = uuid.uuid4()
        item_id = await escalation._review_item(
            TENANT,
            agent_id=str(agent),
            title="Hand-off: Fund transfer",
            reason=escalation.REASON_FALLBACKS,
            context={"summary": "s"},
            requested_by=None,
        )
        assert (
            item_id
            and added[0].agent_id == agent
            and added[0].priority == "high"
            and added[0].trigger_type == escalation.TRIGGER
        )
        assert notify.call_args.kwargs["item_id"] == item_id == str(added[0].id)
        assert (
            await escalation._review_item(TENANT, agent_id="", title="t", reason="r", context={}, requested_by=None)
            is None
        )


class TestReviewFixes:
    """What was said stays off the tenant-wide feed, replies replay, refusals do not write, hand-offs keep context."""

    @pytest.mark.asyncio
    async def test_no_announcement_on_the_tenant_wide_feed_carries_what_was_said(self, monkeypatch):
        import api.websocket.feed as feed
        import core.database as database

        sent: list[dict] = []

        async def capture(_tenant: str, payload: dict) -> None:
            sent.append(payload)

        monkeypatch.setattr(feed, "broadcast_to_tenant", capture)
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        await runtime.chat_turn(
            tenant_id=str(TENANT), company_id="c", user_id="u", agent_id="", text="transfer 500 to Ravi"
        )

        row = _row(taken_over_by="sup-1")
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        await supervisor.user_message(TENANT, row.session_key, "my card number is private")
        await supervisor.reply(TENANT, row.id, "sup-1", "Please share the last four digits only.")

        assert [p["type"] for p in sent] == ["conversation.turn", "conversation.turn"] + ["conversation.message"] * 2
        for payload in sent:
            assert "text" not in payload
            assert not any("Ravi" in str(v) or "private" in str(v) or "four digits" in str(v) for v in payload.values())

    @pytest.mark.asyncio
    async def test_a_refused_reply_leaves_the_transcript_and_the_session_untouched(self, monkeypatch):
        import core.database as database

        before = datetime(2026, 1, 1, tzinfo=UTC)
        row = _row(taken_over_by="sup-1", updated_at=before)
        history = list(row.state["history"])
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        announced = AsyncMock()
        monkeypatch.setattr(supervisor, "announce", announced)

        refused = await supervisor.reply(TENANT, row.id, "someone-else", "an unauthorised message")

        assert refused is not None and refused["refused"] == supervisor.NOT_HOLDER
        assert row.state["history"] == history and row.updated_at == before and announced.await_count == 0

    @pytest.mark.asyncio
    async def test_the_own_session_replays_supervisor_messages_sent_while_the_chat_was_closed(self, monkeypatch):
        import core.database as database
        from api.v1 import conversation as api

        row = _row(
            state={
                "history": [
                    {"role": "user", "text": "transfer 500 to Ravi"},
                    {"role": "system", "text": "A supervisor has joined the conversation.", "at": "2026-10-07T10:00"},
                    {"role": "supervisor", "text": "I can help with that.", "at": "2026-10-07T10:01"},
                ]
            }
        )
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(row))
        replayed = await supervisor.replay(TENANT, row.session_key)
        assert replayed == [
            {"role": "system", "text": "A supervisor has joined the conversation.", "at": "2026-10-07T10:00"},
            {"role": "supervisor", "text": "I can help with that.", "at": "2026-10-07T10:01"},
        ]
        monkeypatch.setattr(database, "get_tenant_session", lambda *_a, **_k: _Session(None))
        assert await supervisor.replay(TENANT, "web:c:a:u:nobody") == []

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        replay = AsyncMock(return_value=replayed)
        monkeypatch.setattr(supervisor, "replay", replay)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        shown = await api.get_session(request, company_id="c", agent_id="a1", channel="web", tenant_id=str(TENANT))
        assert shown["messages"] == replayed and replay.call_args.args[1] == "web:c:a1:u:u1"

    def test_three_bad_answers_escalate_with_the_intent_and_the_slots_already_collected(self):
        dialogue = Dialogue()
        for text in ("block my card ending 4321", "blah", "blah"):
            engine.advance(dialogue, text)
        outcome = engine.advance(dialogue, "blah")
        assert outcome.kind == "escalate" and dialogue.intent is None
        assert outcome.intent == "card_block" and outcome.slots == {"card": "4321"}
        assert outcome.escalation == escalation.REASON_SLOTS and outcome.missing == ["reason"]

    @pytest.mark.asyncio
    async def test_the_handoff_after_bad_answers_carries_the_card_block_tag_and_slots(self, monkeypatch):
        dialogue = Dialogue()
        for text in ("block my card ending 4321", "blah", "blah"):
            engine.advance(dialogue, text)
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=dialogue))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce", AsyncMock())
        monkeypatch.setattr(supervisor, "mark_escalated", AsyncMock())
        review = AsyncMock(return_value="h1")
        monkeypatch.setattr(escalation, "_review_item", review)

        answer = await runtime.chat_turn(
            tenant_id=str(TENANT), company_id="c", user_id="u", agent_id=str(uuid.uuid4()), text="blah"
        )

        assert answer is not None and answer["outcome"]["handoff"]["intent"] == "card_block"
        context = review.call_args.kwargs["context"]
        assert context["intent"] == "card_block" and context["slots"] == {"card": "4321"}
        assert context["summary"].startswith("Hand-off from chat: card block") and "card 4321" in context["summary"]
        assert context["handoff"]["intent"] == "card_block" and context["handoff"]["slots"] == {"card": "4321"}
        assert review.call_args.kwargs["reason"] == escalation.REASON_SLOTS

    @pytest.mark.asyncio
    async def test_accepting_the_offer_of_a_person_after_fallbacks_hands_off_as_fallbacks(self, monkeypatch):
        dialogue = Dialogue()
        for text in ("qwerty zzz", "asdf ghjk"):
            engine.advance(dialogue, text)
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=dialogue))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())
        handoff = AsyncMock(return_value={"reason": "fallbacks", "intent": "talk_to_agent", "hitl_id": None})
        monkeypatch.setattr(escalation, "handoff", handoff)

        from api.v1 import conversation as api

        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u"}))
        answer = await api.post_turn(api.TurnIn(text="yes"), request, tenant_id=str(TENANT))

        assert answer is not None and answer["outcome"]["kind"] == "escalate"
        assert handoff.call_args.kwargs["reason"] == escalation.REASON_FALLBACKS
        ticket = escalation.ticket_params(
            "create_ticket", summary="s", tag="t", lines=[], reason=escalation.REASON_FALLBACKS
        )
        assert ticket["priority"] == "high"

    def test_an_unsolicited_request_for_a_person_is_still_requested(self):
        outcome = engine.advance(Dialogue(), "I want to talk to a human")
        assert outcome.kind == "escalate" and outcome.escalation == escalation.REASON_REQUESTED
