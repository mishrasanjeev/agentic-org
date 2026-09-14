# SPDX-License-Identifier: Apache-2.0
"""An agent graph run records its model calls and replays them with no live model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core import model_replay
from core.langgraph import llm_factory
from core.langgraph.agent_graph import build_agent_graph
from core.model_replay import CassetteMissError, cassette_scope


class _ToolAwareFake(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _ToolAwareFake:  # type: ignore[override]
        return self


def _state(task: str) -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="You approve nothing."), HumanMessage(content=task)],
        "agent_id": "agent-replay",
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


async def _run(task: str) -> dict[str, Any]:
    graph = build_agent_graph(
        system_prompt="You approve nothing.",
        authorized_tools=[],
        llm_model="gemini-2.5-flash",
        confidence_floor=0.5,
    )
    return await graph.compile().ainvoke(_state(task))


@pytest.fixture(autouse=True)
def _test_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_replay, "_runtime_env", lambda: "test")
    monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")


async def test_agent_graph_replays_a_recorded_run_without_a_live_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = AIMessage(content='{"status": "completed", "confidence": 0.92, "summary": "No action taken."}')
    monkeypatch.setattr(llm_factory, "_create_live_chat_model", lambda *a, **k: _ToolAwareFake(responses=[answer]))
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    with cassette_scope(tmp_path):
        recorded = await _run("Review case CASE-0001.")
    assert len(list(tmp_path.glob("*.json"))) == 1

    def _no_live(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("replay built a live model")

    monkeypatch.setattr(llm_factory, "_create_live_chat_model", _no_live)
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    with cassette_scope(tmp_path):
        replayed = await _run("Review case CASE-0001.")

    assert replayed["status"] == recorded["status"] == "completed"
    assert replayed["output"] == recorded["output"]
    assert replayed["confidence"] == recorded["confidence"]


async def test_agent_graph_prompt_change_misses_instead_of_replaying_stale_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = AIMessage(content='{"status": "completed", "confidence": 0.92}')
    monkeypatch.setattr(llm_factory, "_create_live_chat_model", lambda *a, **k: _ToolAwareFake(responses=[answer]))
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    with cassette_scope(tmp_path):
        await _run("Review case CASE-0001.")

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    with cassette_scope(tmp_path), pytest.raises(CassetteMissError, match=r"messages\[1\]"):
        await _run("Review case CASE-0002.")
