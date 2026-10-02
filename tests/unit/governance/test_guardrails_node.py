# SPDX-License-Identifier: Apache-2.0
"""The reasoning node under guardrails: an answer is redacted before it travels on; a blocked input ends the run."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant
from core.governance.guardrails import engine, hooks
from core.governance.guardrails.schema import GuardrailBlocked, Rule
from core.test_doubles.scripted_model import final

TENANT = str(uuid.UUID(int=0x1F5C))
AGENT = str(uuid.UUID(int=0xC3))
CARD = "4111 1111 1111 1111"


def _state(text: str) -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content=text)],
        "agent_id": AGENT,
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "grant_token": "",
        "grant_denial": {},
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


def _rule(**over) -> Rule:
    base = {
        "id": str(uuid.uuid4()),
        "name": "cards",
        "stage": "output",
        "detector": "sensitive_data",
        "action": "redact",
    }
    base.update(over)
    return Rule(**base)


@pytest.fixture
def guarded(monkeypatch):
    from core.governance.guardrails import detectors

    monkeypatch.setattr(hooks.settings, "guardrails_hooks_enabled", True)
    monkeypatch.setattr(engine.settings, "env", "test")
    monkeypatch.setattr(detectors.SensitiveDataDetector, "_analyser_spans", lambda self, text, entities: None)


async def _run(scripted_model, text: str, rules: list[Rule]) -> dict[str, Any]:
    from core.langgraph.agent_graph import build_agent_graph

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(engine, "active_rules", AsyncMock(return_value=rules)))
        stack.enter_context(patch.object(engine, "enforcing", AsyncMock(return_value=True)))
        stack.enter_context(patch.object(engine, "_meter", lambda *a: None))
        stack.enter_context(patch.object(engine, "_audit_outcome", AsyncMock()))
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=[],
            connector_config={},
            connector_names=[],
            confidence_floor=0.5,
            tenant_id=TENANT,
            agent_id=AGENT,
            run_grant=RunGrant(mode=EnforcementMode.OFF, token="", source="minted"),
        )
        compiled = graph.compile(checkpointer=MemorySaver())
        return await compiled.ainvoke(_state(text), {"configurable": {"thread_id": f"guard-{uuid.uuid4().hex[:6]}"}})


async def test_a_card_number_in_the_answer_is_redacted_before_delivery(scripted_model, guarded):
    scripted_model([final(f"Pay with card {CARD} today.")])
    result = await _run(scripted_model, "what card?", [_rule()])
    answers = [m for m in result["messages"] if isinstance(m, AIMessage)]
    assert answers[-1].content == "Pay with card <CREDIT_CARD> today."
    assert CARD not in str(result.get("output"))


async def test_an_input_that_a_rule_blocks_ends_the_run_before_the_model_is_called(scripted_model, guarded):
    scripted_model([])
    with pytest.raises(GuardrailBlocked, match="input blocked by rule no-cards"):
        await _run(scripted_model, f"use {CARD}", [_rule(stage="input", action="block", name="no-cards")])


async def test_flag_only_mode_leaves_the_answer_as_the_model_wrote_it(scripted_model, guarded):
    from core.langgraph.agent_graph import build_agent_graph

    scripted_model([final(f"card {CARD}")])
    with (
        patch.object(engine, "active_rules", AsyncMock(return_value=[_rule()])),
        patch.object(engine, "enforcing", AsyncMock(return_value=False)),
        patch.object(engine, "_meter", lambda *a: None),
    ):
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=[],
            connector_config={},
            connector_names=[],
            confidence_floor=0.5,
            tenant_id=TENANT,
            agent_id=AGENT,
            run_grant=RunGrant(mode=EnforcementMode.OFF, token="", source="minted"),
        )
        result = await graph.compile(checkpointer=MemorySaver()).ainvoke(
            _state("x"), {"configurable": {"thread_id": "flag-only"}}
        )
    assert [m for m in result["messages"] if isinstance(m, AIMessage)][-1].content == f"card {CARD}"
