# SPDX-License-Identifier: Apache-2.0
"""Conversational services: intent recognition, entities, the dialogue, sessions, governed execution and the routes."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.conversation import dialogue as engine
from core.conversation import intents as catalogue
from core.conversation import runtime, supervisor
from core.conversation.dialogue import Dialogue

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 7)
TENANT = uuid.uuid4()


@pytest.fixture(autouse=True)
def _no_operator_overrides(monkeypatch):
    """Operator overrides allow every call unless a test places one."""
    from core.governance import operator_override

    monkeypatch.setattr(operator_override, "check", AsyncMock(return_value=operator_override.ALLOWED))


# ── Recognition and entities ──────────────────────────────────────────────────


class TestRecognition:
    @pytest.mark.parametrize(
        ("text", "intent"),
        [
            ("What is my balance?", "balance_enquiry"),
            ("show me the last 5 transactions", "mini_statement"),
            ("please block my card, it was stolen", "card_block"),
            ("I lost my card", "card_block"),
            ("transfer 5000 to Ravi", "fund_transfer"),
            ("send Rs 2,500 to Priya via IMPS", "fund_transfer"),
            ("pay my electricity bill of 1200", "bill_payment"),
            ("recharge my mobile for 299", "bill_payment"),
            ("what is the interest rate on a home loan", "loan_enquiry"),
            ("I want to dispute a transaction of 999 I did not make", "dispute_transaction"),
            ("I was charged twice for 450 yesterday", "dispute_transaction"),
            ("what is the status of my loan application APP123456", "application_status"),
            ("I want to talk to a human", "talk_to_agent"),
            ("hello", "greeting"),
        ],
    )
    def test_banking_requests_are_recognised(self, text, intent):
        matches = catalogue.recognise(text)
        assert matches and matches[0].intent.name == intent and matches[0].confidence >= catalogue.MIN_CONFIDENCE

    def test_other_text_is_below_the_floor(self):
        matches = catalogue.recognise("what is our cash runway for next quarter")
        assert not matches or matches[0].confidence < catalogue.MIN_CONFIDENCE

    def test_two_requests_in_one_message_are_split(self):
        assert len(catalogue.split_requests("block my card and transfer 500 to Ravi")) == 2
        assert catalogue.split_requests("transfer 500 to Ravi and Priya") == ["transfer 500 to Ravi and Priya"]

    @pytest.mark.parametrize(
        ("text", "amount"),
        [
            ("transfer 5000", 5000.0),
            ("send ₹2,500.50", 2500.5),
            ("pay rs 1200", 1200.0),
            ("2 lakh", 200000.0),
            ("5k", 5000.0),
            ("1.5 crore", 15000000.0),
        ],
    )
    def test_amounts_in_rupees(self, text, amount):
        assert catalogue.parse_amount(text) == amount

    def test_entities_cover_accounts_cards_dates_payees_references_and_periods(self):
        found = catalogue.extract_entities(
            "transfer 500 to Ravi Kumar from account ending 1234 on 12/03/2026, ref APP123456, last 30 days",
            today=TODAY,
        )
        assert found["amount"] == 500 and found["payee"] == "Ravi Kumar" and found["account"] == "1234"
        assert found["date"] == "2026-03-12" and found["reference"] == "APP123456" and found["period"] == "last 30 days"
        assert catalogue.extract_entities("block my card ending 9876")["card"] == "9876"
        assert catalogue.extract_entities("ending 4321")["ending"] == "4321"
        assert catalogue.extract_entities("a home loan for 20 lakh")["loan_type"] == "home"
        assert catalogue.parse_date("yesterday", today=TODAY) == "2026-10-06"
        assert catalogue.parse_date("3 March 2026") == "2026-03-03"
        assert catalogue.parse_payee("pay 200 to the account") is None


# ── The dialogue ──────────────────────────────────────────────────────────────


def _turns(*texts: str) -> tuple[Dialogue, list[engine.Outcome]]:
    dialogue = Dialogue()
    outcomes = [engine.advance(dialogue, text, today=TODAY) for text in texts]
    return dialogue, outcomes


class TestDialogue:
    def test_a_transfer_collects_what_is_missing_confirms_and_only_then_executes(self):
        dialogue, outcomes = _turns("transfer 5000 to Ravi", "from the account ending 1234", "yes")
        first, second, third = outcomes
        # Amount and payee came from the first message; the optional account is not asked for, so it confirms.
        assert first.kind == "confirm" and first.slots == {"amount": 5000.0, "payee": "Ravi"}
        assert "₹5,000 to Ravi" in first.text
        # A correction before confirmation re-confirms with the new value.
        assert second.kind == "confirm" and second.slots["from_account"] == "1234" and "ending 1234" in second.text
        assert third.kind == "execute" and third.action == "fund_transfer"
        assert third.slots == {"amount": 5000.0, "payee": "Ravi", "from_account": "1234"}
        assert dialogue.stage == engine.STAGE_IDLE and dialogue.slots == {}

    def test_missing_required_slots_are_asked_in_order_and_context_carries_across_turns(self):
        _, outcomes = _turns("I want to transfer some money", "2 lakh", "to Priya Sharma")
        assert [o.kind for o in outcomes] == ["ask", "ask", "confirm"]
        assert "How much" in outcomes[0].text and "receive" in outcomes[1].text
        assert outcomes[2].slots == {"amount": 200000.0, "payee": "Priya Sharma"}

    def test_no_cancels_and_nothing_runs(self):
        _, outcomes = _turns("transfer 500 to Ravi", "no")
        assert outcomes[1].kind == "cancelled" and "Nothing has been done" in outcomes[1].text

    def test_an_invalid_answer_is_asked_again_and_three_failures_escalate(self):
        _, outcomes = _turns("send money to Ravi", "lots", "a bit", "dunno")
        assert [o.kind for o in outcomes] == ["ask", "ask", "ask", "escalate"]
        assert "number" in outcomes[1].text

    def test_an_amount_above_the_cap_is_refused_at_the_slot(self):
        _, outcomes = _turns("transfer 50 lakh to Ravi")
        assert outcomes[0].kind == "ask" and outcomes[0].missing == ["amount"]
        _, outcomes = _turns("send money to Ravi", "50 lakh")
        assert outcomes[1].kind == "ask" and "10,00,000" in outcomes[1].text

    def test_a_card_block_collects_the_card_and_reason_then_confirms(self):
        _, outcomes = _turns("my card was stolen, block it", "ending 9876", "yes")
        assert outcomes[0].kind == "ask" and "four digits" in outcomes[0].text
        assert outcomes[1].kind == "confirm" and "ending 9876" in outcomes[1].text and "stolen" in outcomes[1].text
        assert outcomes[2].kind == "execute" and outcomes[2].slots == {"card": "9876", "reason": "stolen"}

    def test_a_read_intent_executes_without_confirmation(self):
        _, outcomes = _turns("what is the status of application APP123456")
        assert outcomes[0].kind == "execute" and outcomes[0].action == "application_status"
        assert outcomes[0].slots == {"reference": "APP123456"}
        _, outcomes = _turns("what is my balance")
        assert outcomes[0].kind == "execute" and outcomes[0].slots == {}

    def test_two_requests_are_clarified_and_a_number_picks_one(self):
        _, outcomes = _turns("block my card and transfer 500 to Ravi", "2")
        assert outcomes[0].kind == "clarify" and [o["intent"] for o in outcomes[0].options] == [
            "card_block",
            "fund_transfer",
        ]
        assert outcomes[1].kind == "confirm" and outcomes[1].intent == "fund_transfer"

    def test_a_dispute_collects_the_reason_and_summarises_the_transaction(self):
        _, outcomes = _turns("I was charged 999 at Example Mart yesterday but never received the order", "yes")
        assert outcomes[0].kind == "confirm"
        assert outcomes[0].slots["amount"] == 999 and outcomes[0].slots["reason"] == "not received"
        assert outcomes[0].slots["transaction_date"] == "2026-10-06"
        assert outcomes[1].kind == "execute" and outcomes[1].action == "dispute_transaction"

    def test_asking_for_a_person_escalates_with_a_handoff_summary(self):
        dialogue, outcomes = _turns("I want to speak to a human")
        assert outcomes[0].kind == "escalate"
        summary = engine.handoff_summary(dialogue)
        assert summary["turns"] == 1 and summary["recent"][0]["role"] == "user"

    def test_a_request_for_a_person_mid_transfer_keeps_what_was_collected_and_promises_nothing(self):
        dialogue, outcomes = _turns("send money to Ravi", "connect me with a human agent")
        handoff = outcomes[1].handoff
        assert outcomes[1].kind == "escalate" and dialogue.stage == engine.STAGE_IDLE
        assert handoff["intent"] == "fund_transfer" and handoff["slots"] == {"payee": "Ravi"}
        assert handoff["reason"] == "requested" and handoff["recent"][-1]["role"] == "assistant"
        assert "connect you" not in outcomes[1].text and "One moment" not in outcomes[1].text
        _, outcomes = _turns("send money to Ravi", "lots", "a bit", "dunno")
        assert (
            outcomes[-1].handoff["reason"] == engine.ESCALATION_SLOTS
            and outcomes[-1].handoff["intent"] == "fund_transfer"
        )
        assert "connect you" not in outcomes[-1].text

    def test_unknown_text_falls_back_and_greetings_are_answered(self):
        _, outcomes = _turns("what is the weather", "hello")
        assert outcomes[0].kind == "fallback" and outcomes[1].kind == "greeting"

    def test_switching_intent_mid_dialogue_starts_the_new_one(self):
        _, outcomes = _turns("send money", "actually block my card")
        assert outcomes[1].kind == "ask" and outcomes[1].intent == "card_block"

    def test_the_dialogue_survives_a_round_trip_through_its_dict(self):
        dialogue, _ = _turns("transfer 500 to Ravi")
        restored = Dialogue.from_dict(dialogue.to_dict())
        assert restored.stage == engine.STAGE_CONFIRMING and restored.slots == {"amount": 500.0, "payee": "Ravi"}
        assert engine.advance(restored, "yes").kind == "execute"

    def test_rupees_use_indian_grouping(self):
        assert (
            engine.rupees(1234567) == "₹12,34,567"
            and engine.rupees(5000) == "₹5,000"
            and engine.rupees(2500.5) == "₹2,500.50"
        )


# ── Runtime: bindings, execution and the chat hook ───────────────────────────


class TestRuntime:
    def test_bindings_resolve_from_the_agent_first_then_the_actions_aliases(self):
        tools = ["zoho_books:get_balance", "initiate_transfer", "tool:core:write:block_card"]
        assert runtime.resolve_binding("fund_transfer", tools, {}) == "initiate_transfer"
        assert runtime.resolve_binding("balance_enquiry", tools, {}) == "zoho_books:get_balance"
        assert runtime.resolve_binding("card_block", tools, {}) == "tool:core:write:block_card"
        assert (
            runtime.resolve_binding("fund_transfer", tools, {"fund_transfer": "zoho_books:get_balance"})
            == "zoho_books:get_balance"
        )
        assert runtime.resolve_binding("fund_transfer", tools, {"fund_transfer": "not_authorised"}) is None
        assert runtime.resolve_binding("bill_payment", tools, {}) is None
        assert runtime.bindings_of({"conversation": {"bindings": {"fund_transfer": "x", "bad": 1}}}) == {
            "fund_transfer": "x"
        }

    def test_a_qualified_binding_prefers_its_own_connector_over_an_earlier_tool_of_the_same_name(self):
        tools = ["banking_aa:fetch_bank_statement", "zoho_books:fetch_bank_statement"]
        bound = {"mini_statement": "zoho_books:fetch_bank_statement"}
        assert runtime.resolve_binding("mini_statement", tools, bound) == "zoho_books:fetch_bank_statement"
        # The same connector and tool in another spelling is still the exact match.
        spelled = ["banking_aa:fetch_bank_statement", "tool:zoho_books:read:fetch_bank_statement"]
        assert runtime.resolve_binding("mini_statement", spelled, bound) == "tool:zoho_books:read:fetch_bank_statement"
        # Never another connector's tool of the same name.
        assert runtime.resolve_binding("mini_statement", ["banking_aa:fetch_bank_statement"], bound) is None
        # A bare binding falls back to the bare name only when it is unambiguous.
        bare = {"mini_statement": "fetch_bank_statement"}
        assert runtime.resolve_binding("mini_statement", tools, bare) is None
        assert (
            runtime.resolve_binding("mini_statement", ["banking_aa:fetch_bank_statement", "send_email"], bare)
            == "banking_aa:fetch_bank_statement"
        )
        # An unqualified authorised tool still serves a qualified binding when it is the only one.
        assert runtime.resolve_binding("mini_statement", ["fetch_bank_statement"], bound) == "fetch_bank_statement"
        # The action's aliases never pick between two connectors either.
        assert runtime.resolve_binding("mini_statement", tools, {}) is None

    def test_bill_payment_never_binds_a_payment_intent_tool_by_alias(self):
        assert "create_payment_intent" not in runtime.ACTIONS["bill_payment"]
        assert runtime.resolve_binding("bill_payment", ["stripe:create_payment_intent"], {}) is None
        assert runtime.resolve_binding("bill_payment", ["stripe:create_payment_intent", "biller:pay_bill"], {}) == (
            "biller:pay_bill"
        )
        # An agent that means it declares the binding explicitly.
        explicit = {"bill_payment": "stripe:create_payment_intent"}
        assert runtime.resolve_binding("bill_payment", ["stripe:create_payment_intent"], explicit) == (
            "stripe:create_payment_intent"
        )

    def test_params_carry_the_slots_and_the_intent(self):
        assert runtime.params_for("fund_transfer", {"amount": 5.0, "payee": "Ravi", "remarks": ""}) == {
            "amount": 5.0,
            "payee": "Ravi",
            "intent": "fund_transfer",
        }

    @pytest.mark.asyncio
    async def test_a_confirmed_action_runs_the_bound_tool_under_the_grant(self, monkeypatch):
        from core.langgraph import tool_adapter

        calls: list[dict] = []

        class _Tool:
            async def ainvoke(self, params):
                calls.append(params)
                return {"status": "ok", "reference": "TXN-1"}

        monkeypatch.setattr(tool_adapter, "_build_tool_index", lambda *_a, **_k: {"transfer_funds": ("core_bank", "d")})
        monkeypatch.setattr(tool_adapter, "build_tools_for_agent", lambda *_a, **_k: [_Tool()])
        permitted = AsyncMock(return_value=True)
        import auth.run_grants as run_grants

        monkeypatch.setattr(run_grants, "direct_tool_call_permitted", permitted)
        outcome = engine.Outcome(
            kind="execute", text="t", intent="fund_transfer", slots={"amount": 500.0, "payee": "Ravi"}
        )
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["transfer_funds"], run_grant=object()
        )

        execution = await runtime.execute(outcome, context)

        assert execution["status"] == "executed" and calls == [
            {"amount": 500.0, "payee": "Ravi", "intent": "fund_transfer"}
        ]
        assert execution["tool_call"] == {
            "connector": "core_bank",
            "tool": "transfer_funds",
            "params": {"amount": 500.0, "payee": "Ravi"},
            "status": "success",
        }
        assert permitted.call_args.kwargs["runtime"] == "conversation"
        assert "Reference TXN-1" in runtime.answer_for(outcome, execution)

    @pytest.mark.asyncio
    async def test_a_refused_grant_never_reaches_the_tool_and_says_so(self, monkeypatch):
        from core.langgraph import tool_adapter

        built = []
        monkeypatch.setattr(tool_adapter, "_build_tool_index", lambda *_a, **_k: {"transfer_funds": ("core_bank", "d")})
        monkeypatch.setattr(tool_adapter, "build_tools_for_agent", lambda *a, **k: built.append(a) or [])
        import auth.run_grants as run_grants

        monkeypatch.setattr(run_grants, "direct_tool_call_permitted", AsyncMock(return_value=False))
        outcome = engine.Outcome(kind="execute", text="t", intent="fund_transfer", slots={"amount": 1.0, "payee": "R"})
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["transfer_funds"], run_grant=object()
        )

        execution = await runtime.execute(outcome, context)

        assert execution["status"] == "refused" and built == []
        assert "not permitted" in runtime.answer_for(
            outcome, execution
        ) and "Nothing has been done" in runtime.answer_for(outcome, execution)

    @pytest.mark.asyncio
    async def test_an_unbound_intent_and_a_failed_tool_are_honest(self, monkeypatch):
        from core.langgraph import tool_adapter

        outcome = engine.Outcome(kind="execute", text="t", intent="fund_transfer", slots={"amount": 1.0, "payee": "R"})
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=[], run_grant=object()
        )
        execution = await runtime.execute(outcome, context)
        assert execution["status"] == "unbound" and "no tool is set up" in runtime.answer_for(outcome, execution)

        class _Tool:
            async def ainvoke(self, params):
                return {"error": "insufficient_funds", "message": "Insufficient balance"}

        monkeypatch.setattr(tool_adapter, "_build_tool_index", lambda *_a, **_k: {"transfer_funds": ("core_bank", "d")})
        monkeypatch.setattr(tool_adapter, "build_tools_for_agent", lambda *_a, **_k: [_Tool()])
        import auth.run_grants as run_grants

        monkeypatch.setattr(run_grants, "direct_tool_call_permitted", AsyncMock(return_value=True))
        context.authorized_tools = ["transfer_funds"]
        execution = await runtime.execute(outcome, context)
        assert execution["status"] == "failed" and execution["tool_call"]["status"] == "error"
        assert "Insufficient balance" in runtime.answer_for(outcome, execution)

    @staticmethod
    def _wire(monkeypatch, calls: list[dict]):
        from core.langgraph import tool_adapter

        class _Tool:
            async def ainvoke(self, params):
                calls.append(params)
                return {"status": "ok", "reference": "TXN-1"}

        monkeypatch.setattr(tool_adapter, "_build_tool_index", lambda *_a, **_k: {"transfer_funds": ("core_bank", "d")})
        monkeypatch.setattr(tool_adapter, "build_tools_for_agent", lambda *_a, **_k: [_Tool()])
        import auth.run_grants as run_grants

        monkeypatch.setattr(run_grants, "direct_tool_call_permitted", AsyncMock(return_value=True))

    @pytest.mark.asyncio
    async def test_an_agent_throttle_or_halt_holds_the_action_before_the_tool_and_the_claim(self, monkeypatch):
        from core.governance import operator_override

        calls: list[dict] = []
        self._wire(monkeypatch, calls)
        check = AsyncMock(return_value=operator_override.OverrideDecision(blocked=True, reason="Agent is throttled."))
        monkeypatch.setattr(operator_override, "check", check)
        claim = AsyncMock(return_value="k1")
        outcome = engine.Outcome(kind="execute", text="t", intent="fund_transfer", slots={"amount": 1.0, "payee": "R"})
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["transfer_funds"], run_grant=object()
        )

        execution = await runtime.execute(outcome, context, claim=claim)

        assert execution["status"] == "held" and calls == [] and claim.await_count == 0
        assert check.call_args.kwargs == {"agent_id": "a1", "throttle_unit": "agent"}
        answer = runtime.answer_for(outcome, execution)
        assert "Agent is throttled." in answer and "Nothing has been done" in answer

    @pytest.mark.asyncio
    async def test_a_claimed_action_carries_its_key_and_an_unclaimed_one_never_runs(self, monkeypatch):
        calls: list[dict] = []
        self._wire(monkeypatch, calls)
        outcome = engine.Outcome(kind="execute", text="t", intent="fund_transfer", slots={"amount": 1.0, "payee": "R"})
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["transfer_funds"], run_grant=object()
        )

        execution = await runtime.execute(outcome, context, claim=AsyncMock(return_value="key-1"))
        assert execution["status"] == "executed" and execution["execution_key"] == "key-1"
        assert calls == [{"amount": 1.0, "payee": "R", "intent": "fund_transfer", "idempotency_key": "key-1"}]
        assert "idempotency_key" not in execution["tool_call"]["params"]

        lost = await runtime.execute(outcome, context, claim=AsyncMock(return_value=None))
        assert lost["status"] == "superseded" and len(calls) == 1
        assert "nothing more has been done" in runtime.answer_for(outcome, lost)

    @pytest.mark.asyncio
    async def test_the_chat_hook_handles_banking_turns_and_leaves_the_rest_to_the_agent(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        store: dict[str, Dialogue] = {}

        async def load(_tid, key):
            return store.get(key, Dialogue())

        async def save(_tid, key, dialogue, **_kw):
            store[key] = dialogue

        monkeypatch.setattr(runtime, "load_dialogue", load)
        monkeypatch.setattr(runtime, "save_dialogue", save)
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())
        executed = AsyncMock(
            return_value={"status": "executed", "intent": "fund_transfer", "result": {}, "tool_call": {"tool": "t"}}
        )
        monkeypatch.setattr(runtime, "execute", executed)
        context = runtime.ExecutionContext(
            tenant_id=str(TENANT), agent_id="a1", authorized_tools=["transfer_funds"], run_grant=object()
        )
        common = {"tenant_id": str(TENANT), "company_id": "c1", "user_id": "u1", "agent_id": "a1", "context": context}

        assert await runtime.chat_turn(text="what is our cash runway", **common) is None
        first = await runtime.chat_turn(text="transfer 500 to Ravi", **common)
        assert (
            first is not None and first["outcome"]["kind"] == "confirm" and first["dialogue"]["stage"] == "confirming"
        )
        # The dialogue is in progress, so even a non-banking reply is handled by it.
        second = await runtime.chat_turn(text="yes", **common)
        assert second is not None and second["outcome"]["kind"] == "execute" and second["tool_calls"] == [{"tool": "t"}]
        assert executed.await_count == 1 and "Done" in second["answer"]

    @pytest.mark.asyncio
    async def test_a_read_intent_with_no_bound_tool_is_left_to_the_agent(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        monkeypatch.setattr(runtime, "load_dialogue", AsyncMock(return_value=Dialogue()))
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        context = runtime.ExecutionContext(tenant_id=str(TENANT), agent_id="a1", authorized_tools=["send_email"])
        assert (
            await runtime.chat_turn(
                tenant_id=str(TENANT),
                company_id="c",
                user_id="u",
                agent_id="a1",
                text="what is my balance",
                context=context,
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_the_hook_is_silent_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", False)
        assert (
            await runtime.chat_turn(
                tenant_id=str(TENANT), company_id="c", user_id="u", agent_id="", text="transfer 500 to Ravi"
            )
            is None
        )

    def test_the_chat_route_hands_banking_turns_to_the_runtime(self):
        chat = (ROOT / "api" / "v1" / "chat.py").read_text(encoding="utf-8")
        assert "handled = await conversation_runtime.chat_turn(" in chat
        assert "conversation: dict | None = None" in chat
        assert chat.count("await _append_history(") == 2
        assert chat.index("conversation_runtime.enabled()") < chat.index("lg_result = await langgraph_run(")

    def test_the_chat_route_comments_cite_reviews_by_date_only(self):
        chat = (ROOT / "api" / "v1" / "chat.py").read_text(encoding="utf-8")
        assert "Codex" not in chat and "the 2026-04-22 review" in chat


# ── Routes ─────────────────────────────────────────────────────────────────────


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_turn_and_session_routes_are_not_found_while_off(self, monkeypatch):
        from api.v1 import conversation as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", False)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        for call in (
            api.post_turn(api.TurnIn(text="hi"), request, tenant_id=str(TENANT)),
            api.get_session(request, company_id="", agent_id="", channel="web", tenant_id=str(TENANT)),
            api.reset_session(request, company_id="", agent_id="", channel="web", tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "conversation_disabled"

    @pytest.mark.asyncio
    async def test_the_intents_route_lists_the_catalogue(self):
        from api.v1 import conversation as api

        answer = await api.list_intents(tenant_id=str(TENANT))
        names = [item["name"] for item in answer["intents"]]
        assert "fund_transfer" in names and answer["actions"]["fund_transfer"][0] == "transfer_funds"
        transfer = next(item for item in answer["intents"] if item["name"] == "fund_transfer")
        assert transfer["confirm"] is True and [s["name"] for s in transfer["slots"] if s["required"]] == [
            "amount",
            "payee",
        ]

    @pytest.mark.asyncio
    async def test_a_turn_without_an_agent_collects_and_confirms_but_cannot_run(self, monkeypatch):
        from api.v1 import conversation as api

        monkeypatch.setattr(settings, "conversation_v2_enabled", True)
        store: dict[str, Dialogue] = {}

        async def load(_tid, key):
            return store.get(key, Dialogue())

        async def save(_tid, key, dialogue, **_kw):
            store[key] = dialogue

        monkeypatch.setattr(runtime, "load_dialogue", load)
        monkeypatch.setattr(runtime, "save_dialogue", save)
        monkeypatch.setattr(supervisor, "taken_over", AsyncMock(return_value=None))
        monkeypatch.setattr(supervisor, "announce_turn", AsyncMock())
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))

        first = await api.post_turn(
            api.TurnIn(text="transfer 500 to Ravi", company_id="c1"), request, tenant_id=str(TENANT)
        )
        assert first["outcome"]["kind"] == "confirm" and first["session_key"] == "web:c1:-:u:u1"
        second = await api.post_turn(api.TurnIn(text="yes", company_id="c1"), request, tenant_id=str(TENANT))
        assert second["outcome"]["kind"] == "execute" and second["outcome"]["execution"]["status"] == "unbound"
        assert "Nothing has been done" in second["answer"]
        with pytest.raises(HTTPException) as info:
            await api.post_turn(api.TurnIn(text="hi", channel="carrier-pigeon"), request, tenant_id=str(TENANT))
        assert info.value.status_code == 422
