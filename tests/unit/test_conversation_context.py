# SPDX-License-Identifier: Apache-2.0
"""Conversational services: multi-turn context, references to the last action, ambiguity and graceful fallbacks."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from core.conversation import context, fallbacks
from core.conversation import dialogue as engine
from core.conversation import intents as catalogue
from core.conversation.dialogue import Dialogue

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 7)


def _turns(*texts: str, dialogue: Dialogue | None = None) -> tuple[Dialogue, list[engine.Outcome]]:
    dialogue = dialogue or Dialogue()
    return dialogue, [engine.advance(dialogue, text, today=TODAY) for text in texts]


class TestContext:
    def test_recent_turns_are_bounded_and_roled(self):
        entries = [
            {"role": "user", "text": "q" * 500},
            {"role": "agent", "text": "a"},
            {"role": "user", "text": ""},
            "junk",
        ]
        turns = context.recent_turns(entries, chars=100)
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert turns[0]["text"].endswith("…") and len(turns[0]["text"]) == 101
        assert len(context.recent_turns([{"role": "user", "text": str(i)} for i in range(20)])) == context.RECENT_TURNS

    def test_the_context_block_and_note_are_empty_without_history(self):
        assert context.context_block([]) == {} and context.with_context_note("be brief", []) == "be brief"
        block = context.context_block([{"role": "user", "text": "what is our cash runway"}])
        assert block == {"conversation": [{"role": "user", "text": "what is our cash runway"}]}
        assert context.with_context_note("be brief", [{"role": "user", "text": "x"}]).endswith(context.CONTEXT_NOTE)

    def test_references_resolve_from_the_last_action(self):
        last = {"amount": 500.0, "payee": "Ravi", "from_account": "1234"}
        assert context.references("send the same amount to Priya") == {"amount"}
        assert context.resolve("send the same amount to Priya", {"payee": "Priya"}, last) == {
            "payee": "Priya",
            "amount": 500.0,
        }
        assert context.resolve("pay 200 to that person", {"amount": 200.0}, last)["payee"] == "Ravi"
        assert context.resolve("balance of that account", {}, last)["account"] == "1234"
        assert context.resolve("send 300 to Priya", {"amount": 300.0, "payee": "Priya"}, last) == {
            "amount": 300.0,
            "payee": "Priya",
        }
        assert (
            context.repeats_last("do that again")
            and context.repeats_last("Again!")
            and not context.repeats_last("again 500")
        )


class TestDialogueContext:
    def test_the_same_amount_and_again_come_from_the_last_action(self):
        dialogue, outcomes = _turns("transfer 500 to Ravi", "yes", "send the same amount to Priya", "yes", "again")
        assert outcomes[1].kind == "execute"
        assert outcomes[2].kind == "confirm" and outcomes[2].slots == {"amount": 500.0, "payee": "Priya"}
        assert outcomes[3].kind == "execute"
        assert outcomes[4].kind == "confirm" and outcomes[4].slots == {"amount": 500.0, "payee": "Priya"}
        assert dialogue.last_intent == "fund_transfer" and dialogue.last_slots == {"amount": 500.0, "payee": "Priya"}

    def test_two_amounts_or_two_payees_are_asked_about_instead_of_acted_on(self):
        _, outcomes = _turns("transfer 500 or 600 to Ravi", "600")
        assert outcomes[0].kind == "ask" and "₹500 or ₹600" in outcomes[0].text
        assert [o["value"] for o in outcomes[0].options] == [500.0, 600.0]
        assert outcomes[1].kind == "confirm" and outcomes[1].slots == {"amount": 600.0, "payee": "Ravi"}
        _, outcomes = _turns("send 500 to Ravi or Priya")
        assert outcomes[0].kind == "ask" and "Ravi or Priya" in outcomes[0].text and outcomes[0].missing == ["payee"]

    def test_two_fallbacks_offer_a_person_and_yes_hands_over(self):
        dialogue, outcomes = _turns("what is the weather", "and the cricket score", "yes")
        assert [o.kind for o in outcomes] == ["fallback", "fallback", "escalate"]
        assert "connect you to a person" in outcomes[1].text and outcomes[1].options[0]["intent"] == "talk_to_agent"
        assert dialogue.fallbacks == 0

    def test_entities_name_every_amount_and_either_payee(self):
        assert catalogue.parse_amounts("500 or 600") == [500.0, 600.0]
        assert catalogue.parse_amounts("₹1,000 or rs 2000 or 1000") == [1000.0, 2000.0]
        found = catalogue.extract_entities("send 500 to Ravi or Priya")
        assert (
            found["payee_options"] == ["Ravi", "Priya"] and found["amount"] == 500.0 and "amount_options" not in found
        )

    def test_a_marked_amount_and_a_bare_alternative_are_both_named(self):
        assert catalogue.parse_amounts("transfer ₹500 or 600 to Ravi") == [500.0, 600.0]
        assert catalogue.parse_amounts("transfer 500 or ₹600 to Ravi") == [500.0, 600.0]
        assert catalogue.parse_amounts("pay rs 2 lakh or 150000") == [200000.0, 150000.0]
        assert catalogue.extract_entities("transfer ₹500 or 600 to Ravi")["amount_options"] == [500.0, 600.0]

    def test_numbers_that_are_not_amounts_do_not_make_a_marked_amount_ambiguous(self):
        assert catalogue.parse_amounts("transfer ₹500 to account ending 1234") == [500.0]
        assert catalogue.parse_amounts("transfer ₹500 to Ravi in 2 days") == [500.0]
        assert catalogue.parse_amounts("pay ₹5k on 12/10/2026") == [5000.0]
        assert "amount_options" not in catalogue.extract_entities("send ₹500 to Ravi, reference ABC12345")

    def test_a_marked_amount_with_a_bare_alternative_is_asked_about_instead_of_acted_on(self):
        _, outcomes = _turns("transfer ₹500 or 600 to Ravi", "600")
        assert outcomes[0].kind == "ask" and [o["value"] for o in outcomes[0].options] == [500.0, 600.0]
        assert outcomes[1].kind == "confirm" and outcomes[1].slots == {"amount": 600.0, "payee": "Ravi"}
        _, outcomes = _turns("transfer 500 or ₹600 to Ravi")
        assert outcomes[0].kind == "ask" and [o["value"] for o in outcomes[0].options] == [500.0, 600.0]


class TestFallbacks:
    def test_the_kind_follows_what_happened(self):
        assert (
            fallbacks.classify({"status": "failed", "error": "timeout: agent exceeded 120s"}) == fallbacks.KIND_TIMEOUT
        )
        assert fallbacks.classify({"status": "guardrail_blocked"}) == fallbacks.KIND_REFUSED
        assert fallbacks.classify({"status": "failed", "error": "tool error"}) == fallbacks.KIND_TOOL_FAILURE
        assert fallbacks.classify({"status": "completed"}, answer="") == fallbacks.KIND_NO_ANSWER
        assert fallbacks.classify({"status": "completed"}, answer="x", confidence=0.2) == fallbacks.KIND_LOW_CONFIDENCE
        assert fallbacks.classify({"status": "completed"}, answer="x", confidence=0.9) is None
        assert fallbacks.classify(None, answer=None) == fallbacks.KIND_NO_ANSWER

    def test_messages_say_what_is_known_and_offer_a_person_after_two(self):
        first = fallbacks.message(fallbacks.KIND_TIMEOUT, consecutive=1)
        assert "did not answer in time" in first and "talk to a person" in first
        assert "cannot tell whether the request went through" in first and "check" in first
        second = fallbacks.message(fallbacks.KIND_NO_ANSWER, consecutive=2)
        assert "connect you to a person" in second and fallbacks.offers_person(2) and not fallbacks.offers_person(1)

    def test_the_chat_route_feeds_the_turns_back_and_falls_back_gracefully(self):
        chat = (ROOT / "api" / "v1" / "chat.py").read_text(encoding="utf-8")
        assert "run_context = conversation_context.context_block(history_entries)" in chat
        assert '"context": run_context}' in chat
        assert "conversation_fallbacks.message(fallback_kind, consecutive=consecutive)" in chat
        assert "consecutive=1" not in chat
        assert chat.index("conversation_fallbacks.message(") < chat.index('"No agent was able to answer that query. "')

    def test_a_timeout_never_claims_that_nothing_changed(self):
        text = fallbacks.message(fallbacks.KIND_TIMEOUT, consecutive=2).lower()
        for claim in ("not done anything", "nothing has been changed", "nothing was changed", "nothing has been done"):
            assert claim not in text
        assert "cannot tell" in text and "connect you to a person" in text

    def test_a_low_confidence_answer_is_held_back_unless_it_reports_a_human_review(self):
        completed = {"status": "completed"}
        assert fallbacks.hold_back(completed, answer="x", confidence=0.2) == fallbacks.KIND_LOW_CONFIDENCE
        assert fallbacks.hold_back(completed, answer="x", confidence=0.9) is None
        assert fallbacks.hold_back(completed, answer="", confidence=None) == fallbacks.KIND_NO_ANSWER
        assert fallbacks.hold_back({"status": "hitl_triggered"}, answer="queued", confidence=0.1, hitl=True) is None
        assert fallbacks.hold_back(None, answer=None, confidence=None, hitl=True) == fallbacks.KIND_NO_ANSWER

    def test_the_streak_counts_trailing_fallbacks_in_the_history(self):
        def agent(fallback=None):
            entry = {"role": "agent", "text": "a"}
            if fallback:
                entry[fallbacks.FALLBACK_KEY] = fallback
            return entry

        user = {"role": "user", "text": "q"}
        assert fallbacks.streak(None) == 0 and fallbacks.streak([]) == 0
        assert fallbacks.streak([user, agent("timeout")]) == 1
        assert fallbacks.streak([user, agent("timeout"), user, agent("no_answer")]) == 2
        assert fallbacks.streak([user, agent("timeout"), user, agent(), user, agent("no_answer")]) == 1
        assert fallbacks.streak([user, agent("timeout"), user, agent()]) == 0
