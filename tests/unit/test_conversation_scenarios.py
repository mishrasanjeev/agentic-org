# SPDX-License-Identifier: Apache-2.0
"""Conversational services: scenario templates, summaries, ratings and sentiment."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.conversation import dialogue as engine
from core.conversation import feedback, runtime, scenarios, summary, supervisor
from core.conversation import intents as catalogue
from core.conversation.dialogue import Dialogue

TODAY = date(2026, 10, 7)
TENANT = uuid.uuid4()


def _turns(*texts: str, dialogue: Dialogue | None = None) -> tuple[Dialogue, list[engine.Outcome]]:
    dialogue = dialogue or Dialogue()
    return dialogue, [engine.advance(dialogue, text, today=TODAY) for text in texts]


class TestScenarios:
    def test_follow_ups_chain_the_next_step_with_what_is_known(self):
        executed = {"status": "executed", "result": {"dispute": {"id": "DSP-9"}}}
        offer = scenarios.follow_up("dispute_transaction", {"amount": 999.0}, executed)
        assert offer is not None and offer.intent == "application_status" and offer.prefill == {"reference": "DSP-9"}
        assert "DSP-9" in offer.text
        loan = scenarios.follow_up(
            "loan_enquiry", {"loan_type": "home", "amount": 2_000_000.0}, {"status": "executed", "result": {}}
        )
        assert (
            loan is not None
            and loan.intent == "loan_application"
            and loan.prefill == {"loan_type": "home", "amount": 2_000_000.0}
        )
        applied = scenarios.follow_up(
            "loan_application", {}, {"status": "executed", "result": {"application_number": "APP-77"}}
        )
        assert (
            applied is not None
            and applied.intent == "application_status"
            and applied.prefill == {"reference": "APP-77"}
        )
        card = scenarios.follow_up("card_block", {"card": "9876"}, {"status": "executed", "result": {"status": "ok"}})
        assert card is not None and card.intent == "card_replacement" and card.prefill == {"card": "9876"}
        person = scenarios.follow_up(
            "application_status", {}, {"status": "executed", "result": {"status": "documents required"}}
        )
        assert person is not None and person.intent == "talk_to_agent" and person.kind == scenarios.KIND_PERSON

    def test_nothing_is_offered_after_a_failed_action_or_at_the_end_of_a_flow(self):
        assert scenarios.follow_up("dispute_transaction", {}, {"status": "failed"}) is None
        assert scenarios.follow_up("dispute_transaction", {}, {"status": "executed", "result": {}}) is None
        assert scenarios.follow_up("fund_transfer", {}, {"status": "executed", "result": {"id": 1}}) is None
        assert (
            scenarios.follow_up("application_status", {}, {"status": "executed", "result": {"status": "approved"}})
            is None
        )
        assert scenarios.reference_of({"status": "executed", "result": {"data": {"reference_number": "R1"}}}) == "R1"

    def test_yes_starts_the_offered_step_prefilled_and_no_ends_it(self):
        dialogue = Dialogue(
            stage=engine.STAGE_OFFERING,
            offer={"intent": "application_status", "text": "t", "prefill": {"reference": "DSP-9"}, "kind": "follow_up"},
        )
        _, outcomes = _turns("yes", dialogue=dialogue)
        assert outcomes[0].kind == "execute" and outcomes[0].slots == {"reference": "DSP-9"}
        dialogue = Dialogue(
            stage=engine.STAGE_OFFERING,
            offer={"intent": "card_replacement", "text": "t", "prefill": {"card": "9876"}, "kind": "follow_up"},
        )
        _, outcomes = _turns("no", dialogue=dialogue)
        assert outcomes[0].kind == "declined" and dialogue.offer is None and dialogue.stage == engine.STAGE_IDLE
        dialogue = Dialogue(
            stage=engine.STAGE_OFFERING,
            offer={"intent": "card_replacement", "text": "t", "prefill": {}, "kind": "follow_up"},
        )
        _, outcomes = _turns("what is my balance", dialogue=dialogue)
        assert outcomes[0].kind == "execute" and outcomes[0].intent == "balance_enquiry"

    def test_the_new_intents_collect_tenure_and_a_card(self):
        _, outcomes = _turns("I want to apply for a home loan of 20 lakh", "3 years", "yes")
        assert outcomes[0].kind == "ask" and "months or years" in outcomes[0].text
        assert outcomes[1].kind == "confirm" and outcomes[1].slots == {
            "loan_type": "home",
            "amount": 2_000_000.0,
            "tenure_months": 36,
        }
        assert (
            "over 36 months" in outcomes[1].text
            and outcomes[2].kind == "execute"
            and outcomes[2].action == "loan_application"
        )
        _, outcomes = _turns("I need a replacement card", "ending 1234", "yes")
        assert (
            outcomes[1].kind == "confirm"
            and "ending 1234" in outcomes[1].text
            and outcomes[2].action == "card_replacement"
        )
        assert (
            catalogue.parse_tenure("24 months") == 24
            and catalogue.parse_tenure("2 yrs") == 24
            and catalogue.parse_tenure("x") is None
        )


class TestFeedback:
    def test_sentiment_reads_the_lexicon_with_negation(self):
        assert feedback.sentiment("this is useless and frustrating")["label"] == "negative"
        assert feedback.sentiment("thanks, that was really helpful")["label"] == "positive"
        assert feedback.sentiment("transfer 500 to Ravi")["label"] == "neutral"
        assert feedback.sentiment("not helpful at all")["label"] == "negative"

    def test_ratings_are_read_from_numbers_thumbs_and_words(self):
        assert (
            feedback.rating_from_text("4") == 4
            and feedback.rating_from_text("5/5") == 5
            and feedback.rating_from_text("rating: 2") == 2
        )
        assert feedback.rating_from_text("thumbs down") == 1 and feedback.rating_from_text("very helpful") == 5
        assert feedback.rating_from_text("transfer 500 to Ravi") is None and feedback.rating_from_text("6") is None

    def test_a_rating_is_kept_when_asked_and_another_message_is_a_new_request(self):
        dialogue = Dialogue(stage=engine.STAGE_RATING, rating_asked=True)
        _, outcomes = _turns("4", dialogue=dialogue)
        assert outcomes[0].kind == "rated" and dialogue.rating == 4 and dialogue.stage == engine.STAGE_IDLE
        dialogue = Dialogue(stage=engine.STAGE_RATING, rating_asked=True)
        _, outcomes = _turns("what is my balance", dialogue=dialogue)
        assert outcomes[0].kind == "execute" and dialogue.rating is None

    @pytest.mark.asyncio
    async def test_a_rating_is_stored_with_the_agents_feedback_as_thumbs(self, monkeypatch):
        import core.feedback.collector as collector

        stored = AsyncMock(return_value={"feedback_id": "f1", "status": "stored", "storage": "database"})
        monkeypatch.setattr(collector, "submit_feedback", stored)
        agent = str(uuid.uuid4())
        record = await feedback.record_rating(
            TENANT,
            session_key="k",
            agent_id=agent,
            user_id="u",
            rating=2,
            comment="slow",
            channel="web",
            intent="fund_transfer",
        )
        assert record["stored_with_agent"] is True and record["rating"] == 2
        kwargs = stored.call_args.kwargs
        assert (
            kwargs["feedback_type"] == "thumbs_down"
            and kwargs["context"]["rating"] == 2
            and kwargs["source"] == "conversation"
        )
        none = await feedback.record_rating(TENANT, session_key="k", agent_id=None, user_id="u", rating=5)
        assert none["stored_with_agent"] is False and stored.await_count == 1


class TestRuntimeFlow:
    @staticmethod
    def _patch_common(monkeypatch, dialogue: Dialogue):
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=dialogue))
        monkeypatch.setattr(runtime, "save_dialogue", AsyncMock())
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())

    @pytest.mark.asyncio
    async def test_an_executed_dispute_offers_tracking_and_yes_tracks_it(self, monkeypatch):
        dialogue = Dialogue()
        self._patch_common(monkeypatch, dialogue)
        executed = AsyncMock(
            return_value={
                "status": "executed",
                "intent": "dispute_transaction",
                "result": {"dispute_id": "DSP-9"},
                "tool_call": {"tool": "raise_dispute"},
            }
        )
        monkeypatch.setattr(runtime, "execute", executed)
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["raise_dispute"], run_grant=object()
        )
        common = {"tenant_id": str(TENANT), "company_id": "c", "user_id": "u", "agent_id": "a1", "context": context}

        first = await runtime.chat_turn(text="I was charged twice for 450 yesterday, it was a duplicate", **common)
        assert first is not None and first["outcome"]["kind"] == "confirm"
        second = await runtime.chat_turn(text="yes", **common)
        assert second is not None and second["outcome"]["kind"] == "execute"
        assert "DSP-9" in second["answer"] and "track its status" in second["answer"]
        assert second["outcome"]["offer"]["intent"] == "application_status" and dialogue.stage == engine.STAGE_OFFERING
        assert dialogue.actions[-1]["intent"] == "dispute_transaction" and dialogue.actions[-1]["reference"] == "DSP-9"
        executed.return_value = {
            "status": "executed",
            "intent": "application_status",
            "result": {"status": "under review"},
            "tool_call": {"tool": "track"},
        }
        third = await runtime.chat_turn(text="yes", **common)
        assert (
            third is not None
            and third["outcome"]["kind"] == "execute"
            and third["outcome"]["intent"] == "application_status"
        )
        assert executed.call_args.args[0].slots == {"reference": "DSP-9"}
        # No further step: the rating is asked once.
        assert feedback.RATING_PROMPT in third["answer"] and dialogue.stage == engine.STAGE_RATING
        fourth = await runtime.chat_turn(text="5", **common)
        assert fourth is not None and fourth["outcome"]["kind"] == "rated" and fourth["dialogue"]["rating"] == 5

    @pytest.mark.asyncio
    async def test_the_follow_up_is_written_after_a_claim_and_a_superseded_confirmation_writes_nothing(
        self, monkeypatch
    ):
        dialogue = Dialogue()
        engine.advance(dialogue, "I was charged twice for 450 yesterday, it was a duplicate", today=TODAY)
        assert dialogue.stage == engine.STAGE_CONFIRMING
        self._patch_common(monkeypatch, dialogue)
        saved = AsyncMock()
        monkeypatch.setattr(runtime, "save_dialogue", saved)

        async def claimed(outcome, context, claim=None):
            assert claim is not None and await claim() == "key-1"
            dialogue.execution_key = "key-1"  # what claim_dialogue stores with the advanced dialogue
            return {"status": "executed", "intent": outcome.intent, "result": {"dispute_id": "DSP-9"}}

        monkeypatch.setattr(runtime, "execute", claimed)
        monkeypatch.setattr(runtime, "claim_dialogue", AsyncMock(return_value="key-1"))
        context = runtime.ExecutionContext(tenant_id=str(TENANT), agent_id="a1", run_grant=object())
        common = {"tenant_id": str(TENANT), "company_id": "c", "user_id": "u", "agent_id": "a1", "context": context}

        done = await runtime.chat_turn(text="yes", **common)
        # The offer of tracking is written on top of the claimed state, which keeps its execution key.
        assert done is not None and done["outcome"]["offer"]["intent"] == "application_status"
        assert saved.await_count == 1 and saved.call_args.args[2].execution_key == "key-1"

        stale = Dialogue()
        engine.advance(stale, "I was charged twice for 450 yesterday, it was a duplicate", today=TODAY)
        self._patch_common(monkeypatch, stale)
        saved = AsyncMock()
        monkeypatch.setattr(runtime, "save_dialogue", saved)
        monkeypatch.setattr(runtime, "claim_dialogue", AsyncMock(return_value=None))

        async def refused_claim(outcome, context, claim=None):
            assert claim is not None and await claim() is None
            return {"status": "superseded", "intent": outcome.intent, "message": "no longer current"}

        monkeypatch.setattr(runtime, "execute", refused_claim)

        refused = await runtime.chat_turn(text="yes", **common)
        assert refused is not None and "no longer current" in refused["answer"]
        assert feedback.RATING_PROMPT not in refused["answer"] and "offer" not in refused["outcome"]
        assert stale.actions == [] and stale.offer is None and saved.await_count == 0

    @pytest.mark.asyncio
    async def test_two_negative_turns_offer_a_person_and_yes_hands_off(self, monkeypatch):
        dialogue = Dialogue()
        self._patch_common(monkeypatch, dialogue)
        from core.conversation import escalation

        handoff = AsyncMock(
            return_value={"reason": "requested", "intent": "talk_to_agent", "hitl_id": "h", "ticket": None}
        )
        monkeypatch.setattr(escalation, "handoff", handoff)
        common = {"tenant_id": str(TENANT), "company_id": "c", "user_id": "u", "agent_id": "", "context": None}

        first = await runtime.chat_turn(text="transfer 500 to Ravi, this app is useless", **common)
        assert first is not None and dialogue.negative_turns == 1
        second = await runtime.chat_turn(text="no, this is frustrating", **common)
        assert (
            second is not None
            and "connect you to a person" in second["answer"]
            and dialogue.stage == engine.STAGE_OFFERING
        )
        third = await runtime.chat_turn(text="yes", **common)
        assert third is not None and third["outcome"]["kind"] == "escalate" and handoff.await_count == 1

    @pytest.mark.asyncio
    async def test_the_feedback_and_summary_routes(self, monkeypatch):
        from api.v1 import conversation as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        dialogue = Dialogue(
            turns=4,
            last_intent="fund_transfer",
            actions=[{"intent": "fund_transfer", "status": "executed", "reference": "TXN-1", "at": "t"}],
        )
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=dialogue))
        saved = AsyncMock()
        monkeypatch.setattr(runtime, "save_dialogue", saved)
        monkeypatch.setattr(
            feedback,
            "record_rating",
            AsyncMock(return_value={"rating": 4, "comment": "", "at": "t", "stored_with_agent": True}),
        )
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))

        answer = await api.post_feedback(api.FeedbackIn(rating=4, company_id="c1"), request, tenant_id=str(TENANT))
        assert (
            answer["rating"] == 4
            and answer["stored_with_agent"] is True
            and dialogue.rating == 4
            and saved.await_count == 1
        )
        shown = await api.get_summary(request, company_id="c1", agent_id="", channel="web", tenant_id=str(TENANT))
        assert "fund transfer done (reference TXN-1)" in shown["summary"]["text"] and shown["summary"]["rating"] == 4

        monkeypatch.setattr(settings, "conversation_v2_enabled", False)
        with pytest.raises(HTTPException):
            await api.post_feedback(api.FeedbackIn(rating=4), request, tenant_id=str(TENANT))


class TestSummary:
    def test_the_summary_names_requests_actions_pending_handoff_rating_and_sentiment(self):
        dialogue = Dialogue(
            turns=6,
            intent="card_block",
            stage=engine.STAGE_COLLECTING,
            pending="card",
            actions=[
                {"intent": "fund_transfer", "status": "executed", "reference": "TXN-1", "at": "t"},
                {"intent": "bill_payment", "status": "refused", "reference": None, "at": "t"},
            ],
            rating=3,
            sentiment=[{"score": -0.8, "label": "negative"}],
        )
        result = summary.summarise(dialogue, escalation={"reason": "requested", "ticket": {"reference": "4711"}})
        text = result["text"]
        assert text.startswith("A conversation of 6 turns about fund transfer, bill payment, card block.")
        assert "fund transfer done (reference TXN-1); bill payment refused under the grant." in text
        assert "Pending: card block: waiting for card." in text and "ticket 4711" in text
        assert "rated the conversation 3/5" in text and "read as negative" in text
        assert (
            result["requests"] == ["fund_transfer", "bill_payment", "card_block"]
            and result["pending"] == "card block: waiting for card"
        )
        assert summary.summarise(Dialogue())["text"] == "A conversation of 0 turns. No action has run."

    def test_the_supervisor_view_carries_rating_and_sentiment(self):
        row = SimpleNamespace(
            id=uuid.uuid4(),
            session_key="k",
            user_id="u",
            agent_id=None,
            channel="web",
            status="active",
            intent=None,
            state={"stage": "idle", "rating": 4, "sentiment": [{"score": 0.6, "label": "positive"}]},
            turns=2,
            taken_over_by=None,
            taken_over_at=None,
            escalation=None,
            updated_at=None,
        )
        view = supervisor.session_view(row)
        assert view["rating"] == 4 and view["sentiment"] == "positive"


class TestFeedbackReview:
    """Ratings, sentiment, storage reporting, retries, agent visibility and amount bounds on the feedback path."""

    def test_negative_rating_phrases_are_not_read_as_positive(self):
        cases = {
            "not helpful": 2,
            "this was not helpful": 2,
            "Not helpful.": 2,
            "not very helpful": 2,
            "not good": 2,
            "not bad": 3,
            "helpful": 4,
            "very helpful": 5,
            "really helpful, thanks": 4,
            "thumbs up": 5,
            "thumbs down": 1,
        }
        for text, expected in cases.items():
            assert feedback.rating_from_text(text) == expected, text
        assert feedback.rating_from_text("unhelpful") is None

    def test_sentiment_matches_whole_words_so_unhelpful_stays_negative(self):
        assert feedback.sentiment("this is unhelpful")["label"] == "negative"
        assert feedback.sentiment("this is not helpful")["label"] == "negative"
        assert feedback.sentiment("that was helpful")["label"] == "positive"
        assert feedback.sentiment("the goodwill gesture")["label"] == "neutral"

    def test_two_unhelpful_turns_count_as_a_negative_streak(self):
        dialogue = Dialogue()
        engine.advance(dialogue, "this is unhelpful", today=TODAY)
        engine.advance(dialogue, "still unhelpful", today=TODAY)
        assert [entry["label"] for entry in dialogue.sentiment][-2:] == ["negative", "negative"]
        assert dialogue.negative_turns >= feedback.NEGATIVE_STREAK

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "result",
        [
            {"feedback_id": "", "status": "error", "message": "Feedback storage is unavailable"},
            {"feedback_id": "f1", "status": "degraded", "storage": "memory"},
            None,
        ],
    )
    async def test_storage_is_reported_only_when_the_feedback_was_stored(self, monkeypatch, result):
        import core.feedback.collector as collector

        monkeypatch.setattr(collector, "submit_feedback", AsyncMock(return_value=result))
        record = await feedback.record_rating(
            TENANT, session_key="k", agent_id=str(uuid.uuid4()), user_id="u", rating=4
        )
        assert record["stored_with_agent"] is False

    @pytest.mark.asyncio
    async def test_a_retried_rating_carries_the_same_event_key(self, monkeypatch):
        import core.feedback.collector as collector

        stored = AsyncMock(return_value={"feedback_id": "f1", "status": "stored", "storage": "database"})
        monkeypatch.setattr(collector, "submit_feedback", stored)
        agent = str(uuid.uuid4())
        for _ in range(2):
            await feedback.record_rating(TENANT, session_key="web:c1:a:u:u1", agent_id=agent, user_id="u1", rating=4)
        await feedback.record_rating(TENANT, session_key="web:c1:a:u:u1", agent_id=agent, user_id="u1", rating=2)
        await feedback.record_rating(TENANT, session_key="web:c1:a:u:u2", agent_id=agent, user_id="u2", rating=4)
        keys = [call.kwargs["source_event_id"] for call in stored.call_args_list]
        assert keys[0] == keys[1] and len(set(keys)) == 3
        assert all(key.startswith("conversation:rating:") and len(key) <= 200 for key in keys)
        assert "u1" not in keys[0]

    @pytest.mark.asyncio
    async def test_feedback_for_an_agent_the_caller_cannot_see_is_refused(self, monkeypatch):
        from api.v1 import conversation as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        saved = AsyncMock()
        monkeypatch.setattr(runtime, "save_dialogue", saved)
        recorded = AsyncMock(return_value={"rating": 2, "comment": "", "at": "t", "stored_with_agent": True})
        monkeypatch.setattr(feedback, "record_rating", recorded)
        refuse = AsyncMock(side_effect=HTTPException(404, "Agent not found"))
        monkeypatch.setattr(api, "_require_visible_agent", refuse)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        agent = str(uuid.uuid4())

        with pytest.raises(HTTPException) as refused:
            await api.post_feedback(
                api.FeedbackIn(rating=2, company_id="c1", agent_id=agent), request, tenant_id=str(TENANT)
            )
        assert refused.value.status_code == 404
        assert refuse.await_args.args[1:] == (str(TENANT), "c1", agent)
        assert recorded.await_count == 0 and saved.await_count == 0

        monkeypatch.setattr(api, "_require_visible_agent", AsyncMock(return_value=None))
        answer = await api.post_feedback(
            api.FeedbackIn(rating=2, company_id="c1", agent_id=agent), request, tenant_id=str(TENANT)
        )
        assert answer["stored_with_agent"] is True and recorded.await_args.kwargs["agent_id"] == agent

    @pytest.mark.asyncio
    async def test_the_agent_is_loaded_under_the_callers_tenant_and_company_and_must_be_visible(self, monkeypatch):
        from contextlib import asynccontextmanager

        import api.v1.agents as agents_api
        from api.v1 import conversation as api

        company = uuid.uuid4()
        monkeypatch.setattr(agents_api, "_require_company_for_tenant", AsyncMock(return_value=company))
        owner = uuid.uuid4()
        holder: dict[str, object] = {"agent": None}
        opened: list[tuple[uuid.UUID, uuid.UUID]] = []

        class _Result:
            def scalar_one_or_none(self):
                return holder["agent"]

        class _Session:
            async def execute(self, statement):
                holder["statement"] = str(statement)
                return _Result()

        @asynccontextmanager
        async def _session(tid, company_uuid):
            opened.append((tid, company_uuid))
            yield _Session()

        monkeypatch.setattr(api, "get_tenant_session", _session)
        caller = SimpleNamespace(
            state=SimpleNamespace(claims={"agenticorg:user_id": str(uuid.uuid4()), "role": "analyst"}, scopes=[])
        )

        with pytest.raises(HTTPException) as bad:
            await api._require_visible_agent(caller, str(TENANT), "c1", "not-a-uuid")
        assert bad.value.status_code == 404

        with pytest.raises(HTTPException) as missing:
            await api._require_visible_agent(caller, str(TENANT), "c1", str(uuid.uuid4()))
        assert missing.value.status_code == 404 and opened[-1] == (TENANT, company)
        assert "tenant_id" in str(holder["statement"]) and "company_id" in str(holder["statement"])

        holder["agent"] = SimpleNamespace(visibility="personal", owner_user_id=owner, domain="ops")
        with pytest.raises(HTTPException) as hidden:
            await api._require_visible_agent(caller, str(TENANT), "c1", str(uuid.uuid4()))
        assert hidden.value.status_code == 404

        mine = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": str(owner)}, scopes=[]))
        assert await api._require_visible_agent(mine, str(TENANT), "c1", str(uuid.uuid4())) is None

    def test_non_finite_amounts_are_refused_even_without_a_cap(self):
        huge = "9" * 400
        assert catalogue.parse_amount(huge) is None
        assert catalogue.parse_amount(f"rs {huge}") is None
        assert catalogue.parse_amounts(f"{huge} or 500") == [500.0]
        slot = engine.Slot("amount", "amount", "How much?")
        value, problem = engine.parse_slot(slot, huge, {}, today=TODAY, max_amount=None)
        assert value is None and problem
        value, problem = engine.parse_slot(slot, "x", {"amount": float("inf")}, today=TODAY, max_amount=None)
        assert value is None and problem
        value, problem = engine.parse_slot(slot, "x", {"amount": float("nan")}, today=TODAY, max_amount=None)
        assert value is None and problem

    def test_a_loan_application_with_an_overflowing_amount_asks_again(self):
        dialogue = Dialogue()
        engine.advance(dialogue, "I want to apply for a home loan", today=TODAY)
        outcome = engine.advance(dialogue, "9" * 400, today=TODAY)
        assert dialogue.slots.get("amount") is None and outcome.kind != "confirm"
        engine.advance(dialogue, "20 lakh", today=TODAY)
        assert dialogue.slots.get("amount") == 2_000_000.0
