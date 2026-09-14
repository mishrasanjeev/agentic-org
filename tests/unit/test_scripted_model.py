# SPDX-License-Identifier: Apache-2.0
"""Scripted chat model: fixed tool-call sequences for graph-mechanics tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from core.langgraph.agent_graph import build_agent_graph
from core.test_doubles.scripted_model import (
    ScriptedChatModel,
    ScriptExhaustedError,
    ScriptMismatchError,
    final,
    tool_call,
)


@tool
def lookup_invoice(invoice_id: str) -> str:
    """Look up an invoice."""
    return invoice_id


# ── The double itself ───────────────────────────────────────────────────────


def test_tool_call_helper_builds_deterministic_ids() -> None:
    first = tool_call("lookup_invoice", invoice_id="INV-0001")
    again = tool_call("lookup_invoice", invoice_id="INV-0001")
    other = tool_call("lookup_invoice", invoice_id="INV-0002")
    named = tool_call("lookup_invoice", invoice_id="INV-0002", call_id="call-b")
    assert first.tool_calls[0]["name"] == "lookup_invoice"
    assert first.tool_calls[0]["args"] == {"invoice_id": "INV-0001"}
    assert first.tool_calls[0]["id"] == again.tool_calls[0]["id"]
    assert first.tool_calls[0]["id"].startswith("call-lookup_invoice-")
    assert first.tool_calls[0]["id"] != other.tool_calls[0]["id"]
    assert named.tool_calls[0]["id"] == "call-b"


def test_final_helper_serialises_structured_output() -> None:
    message = final({"status": "completed", "confidence": 0.9})
    assert message.content == '{"confidence": 0.9, "status": "completed"}'
    assert not message.tool_calls


async def test_steps_are_returned_in_order_and_calls_are_recorded() -> None:
    model = ScriptedChatModel(
        steps=[tool_call("lookup_invoice", invoice_id="INV-0001"), final({"status": "completed"})]
    )
    bound = model.bind_tools([lookup_invoice])

    first = await bound.ainvoke([HumanMessage(content="go")])
    second = await bound.ainvoke([HumanMessage(content="go"), first, ToolMessage(content="ok", tool_call_id="x")])

    assert first.tool_calls[0]["name"] == "lookup_invoice"
    assert second.content == '{"status": "completed"}'
    assert model.remaining == 0
    assert [len(seen) for seen in model.calls] == [1, 3]
    assert bound.bound_tool_names == ["lookup_invoice"]


async def test_running_past_the_script_fails_loudly() -> None:
    model = ScriptedChatModel(steps=[final({"status": "completed"})])
    await model.ainvoke([HumanMessage(content="go")])
    with pytest.raises(ScriptExhaustedError, match="all 1 scripted step"):
        await model.ainvoke([HumanMessage(content="again")])


async def test_a_step_calling_an_unbound_tool_fails_loudly() -> None:
    model = ScriptedChatModel(steps=[tool_call("delete_vendor", vendor_id="VND-0001")])
    with pytest.raises(ScriptMismatchError, match="delete_vendor"):
        await model.bind_tools([lookup_invoice]).ainvoke([HumanMessage(content="go")])


async def test_a_callable_step_can_depend_on_the_conversation() -> None:
    def echo_last(messages: list[Any]) -> AIMessage:
        return final({"echo": messages[-1].content})

    model = ScriptedChatModel(steps=[echo_last])
    result = await model.ainvoke([HumanMessage(content="CASE-0001")])
    assert result.content == '{"echo": "CASE-0001"}'


# ── Fixture: graph mechanics without model text ─────────────────────────────


def _state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="send the invoice reminder")],
        "agent_id": "agent-scripted",
        "agent_type": "analyst",
        "domain": "ops",
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


async def test_fixture_drives_a_tool_call_then_completion(scripted_model: Any) -> None:
    model = scripted_model(
        [
            tool_call("gmail__send_email", to="ap@example.com", subject="Reminder"),
            final({"status": "completed", "confidence": 0.95}),
        ]
    )
    executed = AsyncMock(return_value={"id": "msg-1", "status": "sent"})
    with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.5,
        )
        result = await graph.compile().ainvoke(_state())

    assert result["status"] == "completed"
    assert executed.await_count == 1
    assert [entry["tool"] for entry in result["tool_calls_log"]] == ["gmail__send_email"]
    assert model.remaining == 0


async def test_fixture_drives_interrupt_and_resume(scripted_model: Any) -> None:
    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    graph = build_agent_graph(
        system_prompt="scripted",
        authorized_tools=[],
        confidence_floor=0.5,
        hitl_condition="total > 500000",
    )
    compiled = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "scripted-hitl-1"}}

    paused = await compiled.ainvoke(_state(), config)
    assert paused["__interrupt__"][0].value["type"] == "hitl_approval"

    resumed = await compiled.ainvoke(Command(resume={"action": "reject", "reason": "over limit"}), config)
    assert resumed["status"] == "failed"
    assert "over limit" in resumed["error"]


def test_unconsumed_steps_are_reported_at_teardown() -> None:
    model = ScriptedChatModel(steps=[final({"status": "completed"}), final({"status": "completed"})])
    with pytest.raises(ScriptMismatchError, match="2 scripted steps were never used"):
        model.assert_consumed()


async def test_fully_consumed_script_passes_the_teardown_check() -> None:
    model = ScriptedChatModel(steps=[final({"status": "completed"})])
    await model.ainvoke([HumanMessage(content="go")])
    model.assert_consumed()
