# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Agent output parsing reads provider content blocks for their text.

A live model answered with ``[{"type": "text", "text": "```json\\n{...}\\n```",
"extras": {"signature": "..."}}]``. The parser joined list items with
``str(item)``, so the dict's repr hid the fenced JSON and the run's output
became ``{"raw_output": "<repr>", "status": "completed"}``. These tests hold
the parsers to each block's text and keep plain string answers unchanged.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from core.config import Settings, settings
from core.langgraph.agent_graph import (
    _extract_confidence,
    _parse_json_output,
    _parse_json_output_str_blocks,
    build_agent_graph,
)
from core.llm.content import (
    block_parsing_enabled,
    block_text,
    content_text,
    model_answer,
    strip_code_fence,
    with_text,
)

ANSWER = {"status": "incomplete", "validation_errors": ["GSTIN format is invalid"], "next_step": "ask the vendor"}
FENCED = "```json\n" + json.dumps(ANSWER) + "\n```"
SIGNED_TEXT_BLOCK = {"type": "text", "text": FENCED, "extras": {"signature": "c2lnbmF0dXJlLXN5bnRoZXRpYw=="}}
PRODUCTION_SHAPE = [SIGNED_TEXT_BLOCK]
THINKING_BLOCK = {"type": "thinking", "thinking": "Check the GSTIN checksum first.", "signature": "c3ludGhldGlj"}
REFUSAL_BLOCK = {"type": "refusal", "refusal": "I cannot validate this vendor."}
# A text block with no text that only carries a signature, as some providers send ahead of the answer.
EMPTY_SIGNED_BLOCK = {"type": "text", "text": "", "extras": {"signature": "c2lnbmF0dXJlLW9ubHk="}}


def _no_block_repr(value: Any) -> None:
    rendered = repr(value)
    assert "'type': 'text'" not in rendered
    assert "extras" not in rendered
    assert "signature" not in rendered


class TestContentText:
    def test_the_production_shape_gives_the_fenced_answer_text(self):
        assert content_text(PRODUCTION_SHAPE) == FENCED

    def test_strings_and_blocks_are_joined_by_newlines(self):
        assert content_text(["first", {"type": "text", "text": "second"}, "third"]) == "first\nsecond\nthird"

    def test_a_list_of_strings_joins_as_before(self):
        items = ['{"confidence": 0.95,', '"status": "completed"}']
        assert content_text(items) == "\n".join(str(item) for item in items)

    def test_blocks_without_answer_text_are_skipped(self):
        content = [
            THINKING_BLOCK,
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "reasoning", "text": "private reasoning", "summary": []},
            {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {}},
            {"extras": {"signature": "only-a-signature"}},
            {"type": "text", "text": "the answer"},
        ]
        assert content_text(content) == "the answer"

    def test_a_dict_with_a_text_key_and_no_type_is_read(self):
        assert content_text([{"text": "untyped"}]) == "untyped"

    def test_objects_with_a_text_attribute_are_read(self):
        assert content_text([SimpleNamespace(text="from an sdk block"), SimpleNamespace(text=None)]) == (
            "from an sdk block"
        )

    def test_a_single_block_gives_its_text(self):
        assert content_text(SIGNED_TEXT_BLOCK) == FENCED
        assert content_text(SimpleNamespace(text="object answer")) == "object answer"

    def test_a_single_block_without_text_gives_an_empty_string(self):
        assert content_text(THINKING_BLOCK) == ""
        assert content_text({"type": "tool_use", "id": "x"}) == ""

    def test_strings_none_and_scalars(self):
        assert content_text("  plain  ") == "  plain  "
        assert content_text("") == ""
        assert content_text(None) == ""
        assert content_text([]) == ""
        assert content_text(12345) == "12345"

    def test_block_text(self):
        assert block_text("s") == "s"
        assert block_text({"type": "text", "text": "t"}) == "t"
        assert block_text({"type": "text", "text": 7}) is None
        assert block_text(THINKING_BLOCK) is None
        assert block_text(["nested"]) is None
        assert block_text(REFUSAL_BLOCK) == "I cannot validate this vendor."
        assert block_text({"type": "refusal", "refusal": None}) is None
        assert block_text({"type": "refusal", "text": "not where a refusal keeps it"}) is None

    def test_a_refusal_block_is_read_as_the_answer(self):
        assert content_text([THINKING_BLOCK, REFUSAL_BLOCK]) == "I cannot validate this vendor."

    def test_a_signature_only_text_block_adds_nothing(self):
        assert content_text([EMPTY_SIGNED_BLOCK, {"type": "text", "text": "plain"}]) == "plain"
        assert content_text([EMPTY_SIGNED_BLOCK]) == ""
        assert content_text([SimpleNamespace(text=""), SimpleNamespace(text="sdk")]) == "sdk"

    def test_empty_string_items_join_as_before(self):
        items = ["first", "", "third"]
        assert content_text(items) == "\n".join(str(item) for item in items) == "first\n\nthird"


class TestStripCodeFence:
    def test_a_json_fence_and_its_language_tag_are_removed(self):
        assert strip_code_fence(FENCED) == json.dumps(ANSWER)

    def test_a_bare_fence_is_removed(self):
        assert strip_code_fence('  ```\n{"a": 1}\n```  ') == '{"a": 1}'

    def test_text_without_a_leading_fence_is_only_stripped(self):
        assert strip_code_fence('  {"a": 1}\n') == '{"a": 1}'
        prose = 'Result:\n```json\n{"a": 1}\n```'
        assert strip_code_fence(prose) == prose


class TestParseJsonOutput:
    def test_the_production_shape_parses_to_the_object(self):
        result = _parse_json_output(PRODUCTION_SHAPE)
        assert result == ANSWER
        _no_block_repr(result)

    def test_a_thinking_block_is_skipped(self):
        assert _parse_json_output([THINKING_BLOCK, SIGNED_TEXT_BLOCK]) == ANSWER

    def test_mixed_strings_and_blocks_parse(self):
        content = ['{"status": "completed",', {"type": "text", "text": '"total": 750}'}]
        assert _parse_json_output(content) == {"status": "completed", "total": 750}

    def test_a_single_dict_block_parses(self):
        assert _parse_json_output(SIGNED_TEXT_BLOCK) == ANSWER

    def test_an_sdk_object_block_parses(self):
        assert _parse_json_output([SimpleNamespace(text=FENCED)]) == ANSWER

    def test_plain_and_fenced_strings_are_unchanged(self):
        assert _parse_json_output(json.dumps(ANSWER)) == ANSWER
        assert _parse_json_output(FENCED) == ANSWER
        assert _parse_json_output("  not json at all \n") == {
            "raw_output": "  not json at all \n",
            "status": "completed",
        }

    def test_arrays_and_scalars_in_a_block_are_still_wrapped(self):
        assert _parse_json_output([{"type": "text", "text": "[1, 2, 3]"}]) == {
            "raw_output": [1, 2, 3],
            "status": "completed",
        }
        assert _parse_json_output({"type": "text", "text": "42"}) == {"raw_output": 42, "status": "completed"}

    def test_invalid_json_keeps_the_readable_text(self):
        content = [THINKING_BLOCK, {"type": "text", "text": "The GSTIN is invalid.", "extras": {"signature": "x"}}]
        result = _parse_json_output(content)
        assert result == {"raw_output": "The GSTIN is invalid.", "status": "completed"}
        _no_block_repr(result)

    def test_a_refusal_answer_keeps_its_text(self):
        assert _parse_json_output([REFUSAL_BLOCK]) == {
            "raw_output": "I cannot validate this vendor.",
            "status": "completed",
        }

    def test_a_signature_only_block_ahead_of_the_answer_adds_no_stray_newline(self):
        assert _parse_json_output([EMPTY_SIGNED_BLOCK, {"type": "text", "text": "plain"}]) == {
            "raw_output": "plain",
            "status": "completed",
        }
        assert _parse_json_output([EMPTY_SIGNED_BLOCK, SIGNED_TEXT_BLOCK]) == ANSWER


def _state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="validate the invoice GSTIN")],
        "agent_id": "agent-blocks",
        "agent_type": "ap_processor",
        "domain": "finance",
        "tenant_id": "",
        "grant_token": "",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def _run_graph(scripted_model: Any, message: AIMessage) -> dict[str, Any]:
    scripted_model([message])
    graph = build_agent_graph(
        system_prompt="scripted",
        authorized_tools=[],
        confidence_floor=0.1,
        run_grant=NO_RUN_GRANT_FOR_TESTS,
    )
    return await graph.compile().ainvoke(_state())


class TestEvaluateNode:
    async def test_a_block_answer_gives_the_same_output_and_confidence_as_a_string_answer(self, scripted_model: Any):
        from_blocks = await _run_graph(scripted_model, AIMessage(content=[THINKING_BLOCK, SIGNED_TEXT_BLOCK]))
        from_string = await _run_graph(scripted_model, AIMessage(content=FENCED))

        assert from_blocks["output"] == ANSWER
        assert from_blocks["output"] == from_string["output"]
        # The length signal measures the answer text, not the number of blocks.
        assert from_blocks["confidence"] == from_string["confidence"]
        _no_block_repr(from_blocks["output"])


class TestOtherModelOutputReaders:
    async def test_sop_parser_reads_block_content(self):
        from core.langgraph.sop_parser import parse_sop_document

        draft = {
            "agent_name": "Invoice Checker",
            "agent_type": "invoice_checker",
            "domain": "finance",
            "description": "Checks invoices",
            "steps": [],
            "required_tools": [],
            "hitl_conditions": [],
            "confidence_floor": 0.9,
            "escalation_chain": [],
            "suggested_prompt": "Check invoices.",
        }
        response = MagicMock()
        response.content = [
            THINKING_BLOCK,
            {"type": "text", "text": "```json\n" + json.dumps(draft) + "\n```", "extras": {"signature": "s"}},
        ]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with patch("core.langgraph.sop_parser.create_chat_model", return_value=llm):
            result = await parse_sop_document("Check every invoice", domain_hint="finance")

        assert result["agent_name"] == "Invoice Checker"
        assert "parse_error" not in result

    async def test_sop_parser_keeps_string_content_behaviour(self):
        from core.langgraph.sop_parser import parse_sop_document

        response = MagicMock()
        response.content = "  not json  "
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with patch("core.langgraph.sop_parser.create_chat_model", return_value=llm):
            result = await parse_sop_document("Some SOP text")

        assert result["suggested_prompt"] == "not json"
        assert "parse_error" in result

    async def test_explainer_reads_block_content(self):
        from core.explainer import _call_llm_for_summary

        response = MagicMock()
        response.content = [{"type": "text", "text": '{"bullets": ["Checked the GSTIN."]}', "extras": {}}]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with patch("core.langgraph.llm_factory.create_chat_model", return_value=llm):
            bullets = await _call_llm_for_summary(["step"], {"status": "incomplete"}, [])

        assert bullets == ["Checked the GSTIN."]

    async def test_feedback_analyzer_reads_block_content(self):
        from core.feedback.analyzer import analyze_feedback

        entries = [
            {"feedback_type": "thumbs_down", "text": f"Wrong currency in run {i}.", "corrected_output": None}
            for i in range(12)
        ]
        answer = {"amendment": "Always report amounts in INR", "reason": "currency errors", "confidence": 0.8}
        response = MagicMock()
        response.content = [THINKING_BLOCK, {"type": "text", "text": json.dumps(answer)}]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with (
            patch("core.feedback.collector.list_feedback", new=AsyncMock(return_value=entries)),
            patch("core.langgraph.llm_factory.create_chat_model", return_value=llm),
        ):
            result = await analyze_feedback("agent-blocks", "tenant-blocks")

        assert result["source"] == "llm"
        assert result["amendment"] == "Always report amounts in INR"


class TestBaseAgentReason:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (FENCED, ANSWER),
            ("  plain words  ", {"raw_output": "plain words", "status": "completed"}),
        ],
    )
    async def test_string_content_is_parsed_as_before(self, content: str, expected: dict[str, Any]):
        from core.agents.base import BaseAgent
        from core.llm.router import LLMResponse

        agent = BaseAgent(agent_id="agent-blocks", tenant_id="tenant-blocks")
        reply = LLMResponse(content=content, model="synthetic-model")
        with patch("core.agents.base.llm_router.complete", new=AsyncMock(return_value=reply)):
            result = await agent._reason({"task": "validate"}, [])

        assert result == expected


class TestWithText:
    """A guardrail transform is written back into block content without disturbing the other blocks."""

    TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {}}

    def test_string_and_other_content_becomes_the_text(self):
        assert with_text("before", "after") == "after"
        assert with_text(None, "after") == "after"

    def test_the_text_block_takes_the_text_and_other_blocks_stay_in_place(self):
        content = [THINKING_BLOCK, SIGNED_TEXT_BLOCK, self.TOOL_USE]
        out = with_text(content, "redacted")
        assert out == [THINKING_BLOCK, {**SIGNED_TEXT_BLOCK, "text": "redacted"}, self.TOOL_USE]
        assert content == [THINKING_BLOCK, SIGNED_TEXT_BLOCK, self.TOOL_USE]
        assert content_text(out) == "redacted"

    def test_several_text_blocks_give_way_to_one(self):
        content = ["first", THINKING_BLOCK, {"type": "text", "text": "second"}, SimpleNamespace(text="third")]
        out = with_text(content, "joined and redacted")
        assert out == ["joined and redacted", THINKING_BLOCK]
        assert with_text((SimpleNamespace(text="sdk"),), "t") == [{"type": "text", "text": "t"}]

    def test_a_list_without_a_text_block_gains_one(self):
        assert with_text([THINKING_BLOCK], "t") == [THINKING_BLOCK, {"type": "text", "text": "t"}]
        assert with_text([EMPTY_SIGNED_BLOCK], "t") == [EMPTY_SIGNED_BLOCK, {"type": "text", "text": "t"}]

    def test_a_refusal_block_takes_the_text_as_its_refusal(self):
        out = with_text([THINKING_BLOCK, REFUSAL_BLOCK], "redacted refusal")
        assert out == [THINKING_BLOCK, {"type": "refusal", "refusal": "redacted refusal"}]
        assert content_text(out) == "redacted refusal"

    def test_a_signature_only_text_block_stays_in_place(self):
        content = [EMPTY_SIGNED_BLOCK, SIGNED_TEXT_BLOCK, self.TOOL_USE]
        out = with_text(content, "redacted")
        assert out == [EMPTY_SIGNED_BLOCK, {**SIGNED_TEXT_BLOCK, "text": "redacted"}, self.TOOL_USE]
        assert content_text(out) == "redacted"


@pytest.fixture
def block_parsing_off(monkeypatch):
    monkeypatch.setattr(settings, "agent_output_block_parsing", False)


class TestBlockParsingSetting:
    """agent_output_block_parsing: on by default; off, every parser reads content exactly as it did before."""

    def test_on_by_default_with_an_env_opt_out(self, monkeypatch):
        assert Settings.model_fields["agent_output_block_parsing"].default is True
        assert settings.agent_output_block_parsing is True and block_parsing_enabled() is True
        monkeypatch.setenv("AGENTICORG_AGENT_OUTPUT_BLOCK_PARSING", "false")
        assert Settings(_env_file=None).agent_output_block_parsing is False

    def test_off_the_content_is_read_as_given(self, block_parsing_off):
        assert block_parsing_enabled() is False
        assert model_answer(PRODUCTION_SHAPE) is PRODUCTION_SHAPE
        assert model_answer("  text  ") == "  text  "

    def test_off_block_content_parses_as_before(self, block_parsing_off):
        before = {"raw_output": "\n".join(str(item) for item in PRODUCTION_SHAPE), "status": "completed"}
        assert _parse_json_output(PRODUCTION_SHAPE) == before
        assert _parse_json_output(SIGNED_TEXT_BLOCK) == {"raw_output": str(SIGNED_TEXT_BLOCK), "status": "completed"}
        assert _parse_json_output([THINKING_BLOCK, SIGNED_TEXT_BLOCK]) == _parse_json_output_str_blocks(
            [THINKING_BLOCK, SIGNED_TEXT_BLOCK]
        )

    @pytest.mark.parametrize("parsing", [True, False])
    def test_strings_and_string_lists_parse_the_same_either_way(self, monkeypatch, parsing: bool):
        monkeypatch.setattr(settings, "agent_output_block_parsing", parsing)
        assert _parse_json_output(FENCED) == ANSWER
        assert _parse_json_output(json.dumps(ANSWER)) == ANSWER
        assert _parse_json_output("  not json at all \n") == {
            "raw_output": "  not json at all \n",
            "status": "completed",
        }
        assert _parse_json_output("[1, 2]") == {"raw_output": [1, 2], "status": "completed"}
        assert _parse_json_output(['{"confidence": 0.95,', '"status": "completed"}']) == {
            "confidence": 0.95,
            "status": "completed",
        }
        assert _parse_json_output(["hello", "world"]) == {"raw_output": "hello\nworld", "status": "completed"}
        assert _parse_json_output(12345) == {"raw_output": 12345, "status": "completed"}

    async def test_off_the_evaluate_node_keeps_the_previous_output_and_confidence(
        self, scripted_model: Any, block_parsing_off
    ):
        blocks = [THINKING_BLOCK, SIGNED_TEXT_BLOCK]
        result = await _run_graph(scripted_model, AIMessage(content=blocks))
        before = {"raw_output": "\n".join(str(item) for item in blocks), "status": "completed"}
        assert result["output"] == before
        # The previous length signal: the number of blocks.
        assert result["confidence"] == _extract_confidence(before, content_length=len(blocks))

    async def test_on_and_off_agree_on_a_string_answer(self, scripted_model: Any, monkeypatch):
        on = await _run_graph(scripted_model, AIMessage(content=FENCED))
        monkeypatch.setattr(settings, "agent_output_block_parsing", False)
        off = await _run_graph(scripted_model, AIMessage(content=FENCED))
        assert on["output"] == off["output"] == ANSWER and on["confidence"] == off["confidence"]

    async def test_off_the_sop_parser_fails_on_block_content_as_before(self, block_parsing_off):
        from core.langgraph.sop_parser import parse_sop_document

        response = MagicMock()
        response.content = [SIGNED_TEXT_BLOCK]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with (
            patch("core.langgraph.sop_parser.create_chat_model", return_value=llm),
            pytest.raises(AttributeError, match="strip"),
        ):
            await parse_sop_document("Check every invoice")

    async def test_off_the_explainer_and_analyser_fall_back_as_before(self, block_parsing_off):
        from core.explainer import _call_llm_for_summary
        from core.feedback.analyzer import analyze_feedback

        response = MagicMock()
        response.content = [{"type": "text", "text": '{"bullets": ["Checked the GSTIN."]}'}]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)
        entries = [
            {"feedback_type": "thumbs_down", "text": f"Wrong currency in run {i}.", "corrected_output": None}
            for i in range(12)
        ]
        with (
            patch("core.langgraph.llm_factory.create_chat_model", return_value=llm),
            patch("core.feedback.collector.list_feedback", new=AsyncMock(return_value=entries)),
        ):
            assert await _call_llm_for_summary(["step"], {"status": "incomplete"}, []) is None
            assert (await analyze_feedback("agent-blocks", "tenant-blocks"))["source"] == "heuristic"

    @pytest.mark.parametrize("parsing", [True, False])
    async def test_the_base_agent_parses_string_content_the_same_either_way(self, monkeypatch, parsing: bool):
        from core.agents.base import BaseAgent
        from core.llm.router import LLMResponse

        monkeypatch.setattr(settings, "agent_output_block_parsing", parsing)
        agent = BaseAgent(agent_id="agent-blocks", tenant_id="tenant-blocks")
        reply = LLMResponse(content=FENCED, model="synthetic-model")
        with patch("core.agents.base.llm_router.complete", new=AsyncMock(return_value=reply)):
            assert await agent._reason({"task": "validate"}, []) == ANSWER
