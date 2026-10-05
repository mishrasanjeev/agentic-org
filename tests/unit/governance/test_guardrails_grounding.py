# SPDX-License-Identifier: Apache-2.0
"""The grounding checker: claims held against the run's context, as a detector, a rule and a hook."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.governance.guardrails import engine, grounding, hooks
from core.governance.guardrails.detectors import REGISTRY
from core.governance.guardrails.schema import (
    DETECTORS,
    STRUCTURAL_DETECTORS,
    GuardrailBlocked,
    Rule,
    validate_rule_fields,
)

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[3]

# Synthetic policy text and a synthetic account statement.
POLICY = (
    "The savings account earns interest at 3.5% per year, credited every quarter. "
    "A minimum balance of 5,000 rupees applies to the account. "
    "Cash withdrawals at a branch are free up to four times each month."
)
STATEMENT = "Account ending 4821. Closing balance 18,250.00 rupees on 30 September 2026."


def _rule(**over) -> Rule:
    base = {
        "id": str(uuid.uuid4()),
        "name": "grounded answers",
        "stage": "output",
        "detector": "grounding",
        "action": "flag",
    }
    base.update(over)
    return Rule(**base)


def _rules(rules: list[Rule], enforced: bool = True):
    return (
        patch.object(engine, "active_rules", AsyncMock(return_value=rules)),
        patch.object(engine, "enforcing", AsyncMock(return_value=enforced)),
        patch.object(engine, "_meter", lambda *a: None),
        patch.object(engine, "_audit_outcome", AsyncMock()),
    )


def _evaluate(rules: list[Rule], text: str, *, enforced: bool = True, **kwargs):
    with contextlib.ExitStack() as stack:
        for p in _rules(rules, enforced):
            stack.enter_context(p)
        return asyncio.run(engine.evaluate("output", text, tenant_id=TENANT, **kwargs))


@pytest.fixture(autouse=True)
def _relaxed(monkeypatch):
    monkeypatch.setattr(engine.settings, "env", "test")


class TestTokens:
    def test_content_tokens_drop_stopwords_and_fold_plurals_and_figures(self):
        assert grounding.content_tokens("The accounts are earning the interest.") == ["account", "earning", "interest"]
        assert grounding.content_tokens("Balance of 5,000.00 rupees and 3.50%") == ["balance", "5000", "rupee", "3.5%"]
        assert grounding.content_tokens("") == [] and grounding.content_tokens("the of and") == []

    def test_the_context_is_read_up_to_its_bound(self, monkeypatch):
        monkeypatch.setattr(grounding, "MAX_CONTEXT_CHARS", 20)
        vocabulary = grounding.context_vocabulary(["alpha beta gamma delta", "epsilon"])
        assert "alpha" in vocabulary and "epsilon" not in vocabulary


class TestCheck:
    def test_a_supported_answer_has_no_findings(self):
        answer = "The savings account earns interest at 3.5% per year. The minimum balance is 5,000 rupees."
        assert grounding.check(answer, [POLICY]) == []

    def test_an_answer_written_without_the_context_is_an_unsupported_claim(self):
        answer = "Premium customers receive complimentary airport lounge access worldwide."
        findings = grounding.check(answer, [POLICY])
        assert [f.kind for f in findings] == ["unsupported_claim"]
        assert findings[0].score == 1.0 and (findings[0].start, findings[0].end) == (0, len(answer))
        assert findings[0].detail == "support 0.00"

    def test_an_invented_figure_is_reported_even_when_the_words_are_supported(self):
        answer = "The savings account earns interest at 7.25% per year."
        findings = grounding.check(answer, [POLICY])
        assert [f.kind for f in findings] == ["unsupported_number"] and findings[0].score >= 0.9

    def test_a_figure_written_another_way_is_the_same_figure(self):
        assert grounding.check("The closing balance on the account is 18250 rupees.", [STATEMENT]) == []
        assert grounding.check("The closing balance on the account is 18,250.00 rupees.", [STATEMENT]) == []

    def test_only_the_unsupported_sentence_is_reported_with_its_span(self):
        good = "A minimum balance of 5,000 rupees applies to the account."
        bad = "Overdraft protection is included automatically for every customer."
        answer = f"{good} {bad}"
        findings = grounding.check(answer, [POLICY])
        assert len(findings) == 1 and answer[findings[0].start : findings[0].end] == bad

    def test_questions_and_short_sentences_are_not_claims(self):
        assert grounding.check("Thank you. Anything else? Happy to help.", [POLICY]) == []
        assert grounding.check("Lounge access worldwide.", [POLICY], min_claim_words=3) != []

    def test_min_support_sets_how_much_of_a_claim_the_context_must_carry(self):
        answer = "The savings account earns generous loyalty rewards quarterly."
        assert grounding.check(answer, [POLICY], min_support=0.2) == []
        assert [f.kind for f in grounding.check(answer, [POLICY], min_support=0.9)] == ["unsupported_claim"]

    def test_no_context_is_skipped_unless_the_rule_requires_one(self):
        answer = "The savings account earns interest at 3.5% per year."
        assert grounding.check(answer, None) == [] and grounding.check(answer, ["", "  "]) == []
        required = grounding.check(answer, [], require_context=True)
        assert [f.kind for f in required] == ["no_context"] and required[0].score == 1.0
        assert grounding.check("", [], require_context=True) == []

    def test_the_users_words_add_support_but_never_stand_in_for_retrieved_context(self):
        answer = "Your nominee is recorded as the registered guardian on file."
        asked = ["Please confirm my nominee is recorded as the registered guardian on file."]
        assert grounding.check(answer, [POLICY], user_input=asked) == []
        assert [f.kind for f in grounding.check(answer, [POLICY])] == ["unsupported_claim"]
        # Nothing retrieved: silent, or no_context when the rule requires one, whatever the user wrote.
        assert grounding.check(answer, [], user_input=asked) == []
        required = grounding.check(answer, [], user_input=asked, require_context=True)
        assert [f.kind for f in required] == ["no_context"]

    def test_a_finding_never_carries_the_text(self):
        answer = "Premium customers receive complimentary airport lounge access worldwide."
        for finding in grounding.check(answer, [POLICY]):
            assert "lounge" not in finding.detail and "Premium" not in str(finding.to_dict().values())


class TestRule:
    def test_the_detector_is_registered_structural_and_output_only(self):
        assert "grounding" in DETECTORS and "grounding" in STRUCTURAL_DETECTORS and "grounding" in REGISTRY
        assert REGISTRY["grounding"].uses_context is True
        fields = {"name": "g", "stage": "output", "detector": "grounding"}
        assert validate_rule_fields(fields)["options"] == {}
        options = {"min_support": 0.6, "min_claim_words": 5, "require_context": True, "include_user_input": False}
        assert validate_rule_fields({**fields, "options": options})["options"] == options

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"stage": "input"}, "grounding applies to the output stage"),
            ({"action": "redact"}, "describe the whole text"),
            ({"options": {"min_support": 0}}, "min_support is a fraction"),
            ({"options": {"min_support": 1.5}}, "min_support is a fraction"),
            ({"options": {"min_claim_words": 0}}, "min_claim_words is a whole number"),
            ({"options": {"require_context": "yes"}}, "require_context must be true or false"),
            ({"options": {"patterns": ["x"]}}, "options not taken by grounding"),
        ],
    )
    def test_an_unusable_rule_is_refused(self, change, message):
        with pytest.raises(ValueError, match=message):
            validate_rule_fields({"name": "g", "stage": "output", "detector": "grounding", **change})


class TestEngine:
    UNGROUNDED = "Premium customers receive complimentary airport lounge access worldwide."

    def test_an_ungrounded_answer_is_flagged_and_left_as_it_is(self):
        result = _evaluate([_rule()], self.UNGROUNDED, context=[POLICY])
        assert result.allowed is True and result.text == self.UNGROUNDED
        assert [(o.detector, o.kinds, o.findings) for o in result.outcomes] == [("grounding", ["unsupported_claim"], 1)]

    def test_a_block_rule_suppresses_an_ungrounded_answer_when_enforcing(self):
        with pytest.raises(GuardrailBlocked, match="grounded answers"):
            _evaluate([_rule(action="block")], self.UNGROUNDED, context=[POLICY])
        recorded = _evaluate([_rule(action="block")], self.UNGROUNDED, enforced=False, context=[POLICY])
        assert recorded.allowed is True and recorded.outcomes[0].blocked is False

    def test_a_grounded_answer_passes_a_block_rule(self):
        answer = "Cash withdrawals at a branch are free up to four times each month."
        result = _evaluate([_rule(action="block")], answer, context=[POLICY])
        assert result.allowed is True and result.outcomes == []

    def test_the_threshold_is_how_unsupported_a_claim_must_be(self):
        answer = "The savings account earns generous loyalty rewards quarterly."
        strict = _rule(options={"min_support": 0.9}, threshold=0.3)
        lenient = _rule(options={"min_support": 0.9}, threshold=0.95)
        assert _evaluate([strict], answer, context=[POLICY]).outcomes != []
        assert _evaluate([lenient], answer, context=[POLICY]).outcomes == []

    def test_the_users_words_count_as_context_unless_the_rule_says_otherwise(self):
        answer = "Your nominee is recorded as the registered guardian on file."
        asked = ["Please confirm my nominee is recorded as the registered guardian on file."]
        assert _evaluate([_rule()], answer, context=[POLICY], user_input=asked).outcomes == []
        only_retrieved = _rule(options={"include_user_input": False})
        assert _evaluate([only_retrieved], answer, context=[POLICY], user_input=asked).outcomes != []

    def test_without_a_context_the_rule_is_silent_unless_it_requires_one(self):
        assert _evaluate([_rule()], self.UNGROUNDED).outcomes == []
        required = _evaluate([_rule(options={"require_context": True})], self.UNGROUNDED)
        assert [o.kinds for o in required.outcomes] == [["no_context"]]

    def test_a_claim_the_user_stated_does_not_satisfy_require_context(self):
        # No retrieval happened; the model repeats what the user asserted.
        said = ["Premium customers receive complimentary airport lounge access worldwide, correct?"]
        rule = _rule(action="block", options={"require_context": True})
        with pytest.raises(GuardrailBlocked):
            _evaluate([rule], self.UNGROUNDED, context=[], user_input=said)
        flagged = _evaluate([_rule(options={"require_context": True})], self.UNGROUNDED, user_input=said)
        assert [o.kinds for o in flagged.outcomes] == [["no_context"]]

    def test_only_a_detector_that_uses_the_context_receives_it(self):
        seen: list[dict] = []

        class _Spy:
            name = "pattern"

            def detect(self, text, options, *, threshold):
                seen.append(dict(options))
                return []

        with patch.dict(REGISTRY, {"pattern": _Spy()}):
            _evaluate([_rule(detector="pattern", options={"patterns": ["x"]})], "text", context=[POLICY])
        assert seen == [{"patterns": ["x"]}]

    def test_the_rules_stored_options_are_not_changed_by_a_run(self):
        rule = _rule(options={"min_support": 0.6})
        _evaluate([rule], self.UNGROUNDED, context=[POLICY])
        assert rule.options == {"min_support": 0.6}


class TestHook:
    @pytest.fixture
    def hooks_on(self, monkeypatch):
        monkeypatch.setattr(hooks.settings, "guardrails_hooks_enabled", True)

    def _conversation(self) -> list:
        return [
            SystemMessage(content="You answer from the policy."),
            HumanMessage(content="What interest does the savings account earn?"),
            AIMessage(content="", tool_calls=[{"name": "search_policy", "args": {"q": "interest"}, "id": "c1"}]),
            ToolMessage(content=POLICY, tool_call_id="c1"),
        ]

    def test_the_run_context_is_the_tool_results_and_the_users_words(self):
        retrieved, written = hooks.run_context(self._conversation())
        assert retrieved == [POLICY] and written == ["What interest does the savings account earn?"]
        assert hooks.run_context([]) == ([], [])

    def test_the_answer_is_held_against_the_conversations_tool_results(self, hooks_on):
        grounded = AIMessage(content="The savings account earns interest at 3.5% per year.")
        invented = AIMessage(content="The savings account earns interest at 9.75% per year.")
        with contextlib.ExitStack() as stack:
            for p in _rules([_rule(action="block")]):
                stack.enter_context(p)
            out = asyncio.run(hooks.guard_output_message(grounded, tenant_id=TENANT, messages=self._conversation()))
            assert out is grounded
            with pytest.raises(GuardrailBlocked):
                asyncio.run(hooks.guard_output_message(invented, tenant_id=TENANT, messages=self._conversation()))

    def test_evidence_handed_over_in_a_human_turn_is_retrieved_context(self, hooks_on):
        evidence = hooks.as_retrieved(HumanMessage(content=POLICY))
        conversation = [SystemMessage(content="Write the case summary from the evidence."), evidence]
        assert hooks.run_context(conversation) == ([POLICY], [])
        rule = _rule(action="block", options={"require_context": True, "include_user_input": False})
        grounded = AIMessage(content="The savings account earns interest at 3.5% per year.")
        invented = AIMessage(content="The savings account earns interest at 9.75% per year.")
        with contextlib.ExitStack() as stack:
            for p in _rules([rule]):
                stack.enter_context(p)
            assert (
                asyncio.run(hooks.guard_output_message(grounded, tenant_id=TENANT, messages=conversation)) is grounded
            )
            with pytest.raises(GuardrailBlocked):
                asyncio.run(hooks.guard_output_message(invented, tenant_id=TENANT, messages=conversation))

    def test_the_marker_survives_a_content_rewrite_and_the_governed_case_sets_it(self):
        evidence = hooks.as_retrieved(HumanMessage(content=POLICY, additional_kwargs={"other": 1}))
        assert evidence.additional_kwargs == {"other": 1, hooks.RETRIEVED_MARKER: True}
        rewritten = evidence.model_copy(update={"content": "pseudonymised"})
        assert hooks.run_context([rewritten]) == (["pseudonymised"], [])
        src = (ROOT / "core" / "agents" / "case_model_call.py").read_text(encoding="utf-8")
        assert "as_retrieved(HumanMessage(content=context))" in src

    def test_a_run_with_no_tool_result_is_not_judged(self, hooks_on):
        answer = AIMessage(content="Premium customers receive complimentary airport lounge access worldwide.")
        with contextlib.ExitStack() as stack:
            for p in _rules([_rule(action="block", options={"include_user_input": False})]):
                stack.enter_context(p)
            assert asyncio.run(hooks.guard_output_message(answer, tenant_id=TENANT, messages=[])) is answer

    def test_the_reasoning_node_passes_the_conversation_to_the_output_stage(self):
        src = (ROOT / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")
        # The full conversation, not the copy that may have been trimmed to fit the context window.
        assert "response, tenant_id=tenant_id, agent_id=called_agent, messages=full_messages" in src
        assert "full_messages = messages" in src


class TestDryRunEndpoint:
    def test_the_dry_run_takes_a_context(self):
        from fastapi import Depends, FastAPI, Request
        from fastapi.testclient import TestClient

        from api.deps import get_current_tenant
        from api.route_enforcement import enforce_route_metadata
        from api.v1 import guardrails as api
        from core.governance.guardrails.schema import GuardrailResult

        app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

        @app.middleware("http")
        async def _auth(request: Request, call_next):
            request.state.auth_mode = "api_key"
            request.state.claims = {"sub": "apikey:key_01"}
            request.state.scopes = ["agenticorg:admin"]
            request.state.tenant_id = str(TENANT)
            return await call_next(request)

        app.include_router(api.router, prefix="/api/v1")
        app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
        result = GuardrailResult(stage="output", text="x", allowed=True, enforced=False, correlation_id="c")
        evaluate = AsyncMock(return_value=result)
        with (
            patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
            patch.object(api.guardrails, "evaluate", evaluate),
        ):
            client = TestClient(app)
            body = {"stage": "output", "text": "x", "context": [POLICY], "user_input": ["a question"]}
            assert client.post("/api/v1/guardrails/evaluate", json=body).status_code == 200
            too_many = {"stage": "output", "text": "x", "context": ["c"] * 51}
            assert client.post("/api/v1/guardrails/evaluate", json=too_many).status_code == 422
        kwargs = evaluate.await_args.kwargs
        assert kwargs["context"] == [POLICY] and kwargs["user_input"] == ["a question"] and kwargs["dry_run"] is True
