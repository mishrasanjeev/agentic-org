# SPDX-License-Identifier: Apache-2.0
"""Context-window management: what is sent to a model fits it, most relevant tool results first."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.config import settings
from core.prompts import context_window as cw

ROOT = Path(__file__).resolve().parents[2]
QUESTION = "What interest does the savings account earn each quarter?"


def _call(call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "search_policy", "args": {"q": call_id}, "id": call_id}])


def _result(call_id: str, text: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id)


def _conversation(*results: str, last: str = "The newest result.") -> list:
    """A system prompt, a question, one tool round per older result, then the newest round."""
    messages: list = [SystemMessage(content="You answer from the policy."), HumanMessage(content=QUESTION)]
    for index, text in enumerate(results):
        messages += [_call(f"c{index}"), _result(f"c{index}", text)]
    messages += [_call("newest"), _result("newest", last)]
    return messages


RELEVANT = "The savings account earns interest every quarter at the published rate. " * 40
UNRELATED = "Branch opening hours and parking arrangements for visitors. " * 40


class TestEstimates:
    def test_tokens_are_estimated_from_length(self):
        assert cw.estimate_tokens("") == 0 and cw.estimate_tokens(None) == 0
        assert cw.estimate_tokens("abcd") == 1 and cw.estimate_tokens("abcde") == 2
        assert cw.message_tokens(HumanMessage(content="abcd")) == cw.MESSAGE_OVERHEAD_TOKENS + 1
        assert cw.message_tokens(_call("c1")) > cw.MESSAGE_OVERHEAD_TOKENS

    def test_the_window_comes_from_the_catalogue_with_room_kept_for_the_answer(self):
        window, reserve = cw.window_for("gpt-4o")
        assert (window, reserve) == (128_000, 16_384)
        # A model whose answer limit is most of its window keeps at most a quarter for the answer.
        o1_window, o1_reserve = cw.window_for("o1")
        assert o1_reserve == int(o1_window * cw.MAX_OUTPUT_RESERVE_SHARE)
        assert cw.window_for("a-model-the-catalogue-lacks") == (cw.DEFAULT_WINDOW, cw.DEFAULT_OUTPUT_RESERVE)
        assert cw.budget_for("gpt-4o") == int((128_000 - 16_384) * 0.9)


class TestFit:
    def test_a_conversation_that_fits_is_sent_as_it_is(self):
        messages = _conversation("short")
        result = cw.fit(messages, "gpt-4o")
        assert result.messages is messages and result.changed is False and result.fits is True
        assert result.before_tokens == result.after_tokens

    def test_the_least_relevant_older_result_is_omitted_first(self):
        messages = _conversation(RELEVANT, UNRELATED)
        budget = cw.total_tokens(messages) - 100
        result = cw.fit(messages, None, budget=budget)
        assert result.fits and result.omitted == 1 and result.truncated == 0
        contents = [m.content for m in result.messages if isinstance(m, ToolMessage)]
        assert contents == [RELEVANT, cw.OMITTED, "The newest result."]

    def test_between_equally_relevant_results_the_older_goes_first(self):
        messages = _conversation(UNRELATED, UNRELATED)
        result = cw.fit(messages, None, budget=cw.total_tokens(messages) - 100)
        contents = [m.content for m in result.messages if isinstance(m, ToolMessage)]
        assert contents == [cw.OMITTED, UNRELATED, "The newest result."]

    def test_omission_keeps_every_tool_call_answered_and_everything_else_untouched(self):
        messages = _conversation(RELEVANT, UNRELATED, UNRELATED)
        result = cw.fit(messages, None, budget=cw.total_tokens(messages) // 3)
        assert len(result.messages) == len(messages)
        for before, after in zip(messages, result.messages, strict=True):
            assert type(before) is type(after)
            if isinstance(before, ToolMessage):
                assert after.tool_call_id == before.tool_call_id
            else:
                assert after is before
        assert result.messages[-1].content == "The newest result."

    def test_the_callers_messages_are_not_changed(self):
        messages = _conversation(RELEVANT, UNRELATED)
        cw.fit(messages, None, budget=cw.total_tokens(messages) // 2)
        assert [m.content for m in messages if isinstance(m, ToolMessage)] == [
            RELEVANT,
            UNRELATED,
            "The newest result.",
        ]

    def test_when_omitting_is_not_enough_the_largest_result_is_cut_to_its_beginning(self):
        huge = "A long extract from the newest search. " * 2000
        messages = _conversation(UNRELATED, last=huge)
        budget = 5_000
        result = cw.fit(messages, None, budget=budget)
        newest = result.messages[-1].content
        assert result.omitted == 1 and result.truncated == 1 and result.fits
        assert newest.endswith(cw.TRUNCATED) and newest.startswith("A long extract") and len(newest) < len(huge)

    def test_a_conversation_that_cannot_fit_is_reported_not_broken(self):
        messages = [
            SystemMessage(content="s" * 40_000),
            HumanMessage(content=QUESTION),
            _call("c"),
            _result("c", "tiny"),
        ]
        result = cw.fit(messages, None, budget=100)
        assert result.fits is False
        assert [m.content for m in result.messages] == [m.content for m in messages]

    def test_only_tool_results_are_ever_changed(self):
        long_human = HumanMessage(content=QUESTION + " " + "context " * 5000)
        messages = [
            SystemMessage(content="s"),
            long_human,
            _call("c0"),
            _result("c0", UNRELATED),
            _call("n"),
            _result("n", "x"),
        ]
        result = cw.fit(messages, None, budget=2_000)
        assert result.messages[1] is long_human and result.messages[0].content == "s"


class TestSwitch:
    def test_off_by_default_nothing_is_measured(self):
        assert settings.context_window_managed is False
        assert cw.fit_for_call(_conversation(RELEVANT * 50), "gpt-4o") is None

    def test_on_the_call_is_fitted_and_metered_without_a_tenant_label(self, monkeypatch):
        from observability.metrics import context_window_trims_total

        assert tuple(context_window_trims_total._labelnames) == ("result",)
        monkeypatch.setattr(settings, "context_window_managed", True)
        monkeypatch.setattr(cw, "budget_for", lambda _model: 800)
        series = context_window_trims_total.labels(result="fitted")
        before = series._value.get()
        result = cw.fit_for_call(_conversation(UNRELATED, UNRELATED), "gpt-4o")
        assert result is not None and result.changed and result.fits
        assert series._value.get() == before + 1
        untouched = cw.fit_for_call(_conversation("short"), "gpt-4o")
        assert untouched is not None and untouched.changed is False
        assert series._value.get() == before + 1

    def test_the_reasoning_node_sends_the_fitted_copy_and_grounds_on_the_full_one(self):
        src = (ROOT / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")
        reason = src[src.index("async def reason(") : src.index("async def evaluate(")]
        assert reason.index("full_messages = messages") < reason.index("fitted = fit_for_call(messages, called_model)")
        assert reason.index("fitted = fit_for_call(") < reason.index("request_digest = messages_digest(messages)")
        assert reason.index("fitted = fit_for_call(") < reason.index("invoke_timed(llm, messages)")
        assert "messages=full_messages" in reason


@pytest.mark.parametrize("budget_share", [0.9, 0.6, 0.3])
def test_the_result_never_exceeds_the_budget_when_tool_results_can_absorb_it(budget_share):
    messages = _conversation(RELEVANT, UNRELATED, RELEVANT, UNRELATED)
    budget = int(cw.total_tokens(messages) * budget_share)
    result = cw.fit(messages, None, budget=budget)
    assert result.fits and cw.total_tokens(result.messages) == result.after_tokens <= budget
