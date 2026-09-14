# SPDX-License-Identifier: Apache-2.0
"""Record-and-replay harness for model calls (core/model_replay)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from core import model_replay
from core.model_replay import (
    CassetteError,
    CassetteMissError,
    ModelMode,
    ModelModeError,
    ReplayChatModel,
    cassette_scope,
    current_mode,
    replay_router_call,
    request_key,
)


@tool
def lookup_invoice(invoice_id: str) -> str:
    """Look up an invoice by id."""
    return invoice_id


@tool
def lookup_vendor(vendor_id: str) -> str:
    """Look up a vendor by id."""
    return vendor_id


PROMPT = [SystemMessage(content="You are a careful analyst."), HumanMessage(content="Summarise invoice INV-0001.")]


class ToolAwareFake(FakeMessagesListChatModel):
    """The stock fake rejects bind_tools; real providers accept it."""

    bound: list[Any] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolAwareFake:  # type: ignore[override]
        return self.model_copy(update={"bound": list(tools)})


@pytest.fixture(autouse=True)
def _relaxed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_MODEL_MODE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(model_replay, "_runtime_env", lambda: "test")


def _never_called() -> Any:
    raise AssertionError("a live model must not be built in replay mode")


# ── Mode selection ──────────────────────────────────────────────────────────


def test_mode_defaults_to_live_outside_ci() -> None:
    assert current_mode() is ModelMode.LIVE


def test_mode_defaults_to_replay_in_ci_inside_a_cassette_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    with cassette_scope(tmp_path):
        assert current_mode() is ModelMode.REPLAY


def test_ci_without_a_cassette_scope_keeps_live_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("AGENTICORG_CASSETTE_DIR", raising=False)
    assert current_mode() is ModelMode.LIVE


def test_hermetic_fallback_keeps_the_fake_in_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    with cassette_scope(tmp_path):
        assert current_mode(hermetic_fallback=True) is ModelMode.LIVE


def test_explicit_mode_wins_over_ci_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    assert current_mode() is ModelMode.RECORD


def test_unknown_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay-if-possible")
    with pytest.raises(ModelModeError, match="replay-if-possible"):
        current_mode()


@pytest.mark.parametrize("mode", ["record", "replay"])
def test_strict_runtime_refuses_record_and_replay(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(model_replay, "_runtime_env", lambda: "production")
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", mode)
    with pytest.raises(ModelModeError, match="production"):
        current_mode()


def test_strict_runtime_ignores_ci_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_replay, "_runtime_env", lambda: "production")
    monkeypatch.setenv("CI", "true")
    with cassette_scope(tmp_path):
        assert current_mode() is ModelMode.LIVE


# ── Request key ─────────────────────────────────────────────────────────────


def test_key_is_stable_for_identical_requests() -> None:
    first = request_key("gemini-2.5-flash", PROMPT, tools=[], params={"temperature": 0.1, "max_tokens": 64})
    second = request_key("gemini-2.5-flash", list(PROMPT), tools=[], params={"max_tokens": 64, "temperature": 0.1})
    assert first == second
    assert first.startswith("sha256:")


def test_key_changes_when_the_rendered_prompt_changes() -> None:
    base = request_key("gemini-2.5-flash", PROMPT, tools=[], params={})
    edited = [PROMPT[0], HumanMessage(content="Summarise invoice INV-0002.")]
    assert request_key("gemini-2.5-flash", edited, tools=[], params={}) != base


def test_key_changes_with_model_params_and_tools() -> None:
    base = request_key("gemini-2.5-flash", PROMPT, tools=[], params={"temperature": 0.1})
    assert request_key("claude-sonnet-5", PROMPT, tools=[], params={"temperature": 0.1}) != base
    assert request_key("gemini-2.5-flash", PROMPT, tools=[], params={"temperature": 0.2}) != base
    with_tool = ReplayChatModel(model_name="m").bind_tools([lookup_invoice]).tool_schemas  # type: ignore[attr-defined]
    assert request_key("gemini-2.5-flash", PROMPT, tools=with_tool, params={"temperature": 0.1}) != base


def test_key_changes_when_tool_state_changes() -> None:
    call = AIMessage(
        content="", tool_calls=[{"name": "lookup_invoice", "args": {"invoice_id": "INV-0001"}, "id": "c1"}]
    )
    first = [*PROMPT, call, ToolMessage(content='{"total": 10}', tool_call_id="c1")]
    second = [*PROMPT, call, ToolMessage(content='{"total": 11}', tool_call_id="c1")]
    assert request_key("m", first, tools=[], params={}) != request_key("m", second, tools=[], params={})


def test_key_ignores_volatile_message_ids() -> None:
    one = [HumanMessage(content="hi", id="run-aaa")]
    two = [HumanMessage(content="hi", id="run-bbb")]
    assert request_key("m", one, tools=[], params={}) == request_key("m", two, tools=[], params={})


# ── Chat model: record, replay, miss ────────────────────────────────────────


async def test_record_then_replay_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = AIMessage(
        content="",
        tool_calls=[{"name": "lookup_invoice", "args": {"invoice_id": "INV-0001"}, "id": "call-1"}],
    )
    live = ToolAwareFake(responses=[reply])
    model = ReplayChatModel(model_name="gemini-2.5-flash", temperature=0.1, max_tokens=64, live_factory=lambda: live)
    bound = model.bind_tools([lookup_invoice])

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    with cassette_scope(tmp_path):
        recorded = await bound.ainvoke(PROMPT)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["model"] == "gemini-2.5-flash"
    assert saved["request"]["messages"][1]["content"] == "Summarise invoice INV-0001."
    assert [t["function"]["name"] for t in saved["request"]["tools"]] == ["lookup_invoice"]

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    replaying = ReplayChatModel(
        model_name="gemini-2.5-flash", temperature=0.1, max_tokens=64, live_factory=_never_called
    )
    with cassette_scope(tmp_path):
        replayed = await replaying.bind_tools([lookup_invoice]).ainvoke(PROMPT)
    assert replayed.tool_calls == recorded.tool_calls
    assert replayed.tool_calls[0]["args"] == {"invoice_id": "INV-0001"}


async def test_replay_miss_fails_loudly_without_building_a_live_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    model = ReplayChatModel(model_name="gemini-2.5-flash", live_factory=_never_called)
    with cassette_scope(tmp_path), pytest.raises(CassetteMissError) as err:
        await model.ainvoke(PROMPT)
    message = str(err.value)
    assert "sha256:" in message
    assert str(tmp_path) in message
    assert "AGENTICORG_MODEL_MODE=record" in message


async def test_replay_miss_after_prompt_change_names_the_nearest_cassette(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = FakeMessagesListChatModel(responses=[AIMessage(content="Total is 10.")])
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    with cassette_scope(tmp_path):
        await ReplayChatModel(model_name="m", live_factory=lambda: live).ainvoke(PROMPT)

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    changed = [PROMPT[0], HumanMessage(content="Summarise invoice INV-0001 briefly.")]
    with cassette_scope(tmp_path), pytest.raises(CassetteMissError) as err:
        await ReplayChatModel(model_name="m", live_factory=_never_called).ainvoke(changed)
    assert "differs from the nearest recording at messages[1]" in str(err.value)


async def test_replay_without_a_cassette_scope_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    monkeypatch.delenv("AGENTICORG_CASSETTE_DIR", raising=False)
    with pytest.raises(CassetteError, match="cassette directory"):
        await ReplayChatModel(model_name="m", live_factory=_never_called).ainvoke(PROMPT)


async def test_corrupt_cassette_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    key = request_key("m", PROMPT, tools=[], params={"temperature": 0.1, "max_tokens": 4096, "stop": None})
    (tmp_path / f"{key.removeprefix('sha256:')}.json").write_text("{not json", encoding="utf-8")
    with cassette_scope(tmp_path), pytest.raises(CassetteError, match="unreadable"):
        await ReplayChatModel(model_name="m", live_factory=_never_called).ainvoke(PROMPT)


async def test_live_mode_passes_through_and_writes_nothing(tmp_path: Path) -> None:
    live = FakeMessagesListChatModel(responses=[AIMessage(content="live answer")])
    with cassette_scope(tmp_path):
        result = await ReplayChatModel(model_name="m", live_factory=lambda: live).ainvoke(PROMPT)
    assert result.content == "live answer"
    assert list(tmp_path.iterdir()) == []


async def test_bound_tools_change_the_replay_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = ToolAwareFake(responses=[AIMessage(content="ok")])
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    with cassette_scope(tmp_path):
        await ReplayChatModel(model_name="m", live_factory=lambda: live).bind_tools([lookup_invoice]).ainvoke(PROMPT)

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    with cassette_scope(tmp_path), pytest.raises(CassetteMissError):
        await ReplayChatModel(model_name="m", live_factory=_never_called).bind_tools([lookup_vendor]).ainvoke(PROMPT)


# ── Router path ─────────────────────────────────────────────────────────────


async def test_router_call_record_then_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [{"role": "user", "content": "Classify this ticket."}]
    calls = 0

    async def live_call() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "content": "billing",
            "model": "gemini-2.5-flash",
            "tokens_used": 7,
            "cost_usd": 0.0,
            "latency_ms": 5,
            "raw": {},
        }

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "record")
    with cassette_scope(tmp_path):
        recorded = await replay_router_call(
            model="gemini-2.5-flash", messages=messages, temperature=0.0, max_tokens=32, live_call=live_call
        )
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    with cassette_scope(tmp_path):
        replayed = await replay_router_call(
            model="gemini-2.5-flash", messages=messages, temperature=0.0, max_tokens=32, live_call=live_call
        )
    assert calls == 1
    assert replayed == recorded
    assert replayed["content"] == "billing"


async def test_router_replay_miss_never_calls_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def live_call() -> dict[str, Any]:
        raise AssertionError("live call in replay mode")

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    with cassette_scope(tmp_path), pytest.raises(CassetteMissError):
        await replay_router_call(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "x"}],
            temperature=0.0,
            max_tokens=8,
            live_call=live_call,
        )


# ── Wiring into the two model entry points ─────────────────────────────────


def test_create_chat_model_in_replay_mode_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.langgraph import llm_factory

    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    monkeypatch.setattr(llm_factory, "_build_model", _never_called)

    model = llm_factory.create_chat_model(model="gemini-2.5-flash", temperature=0.2, max_tokens=128)

    assert isinstance(model, ReplayChatModel)
    assert (model.model_name, model.temperature, model.max_tokens) == ("gemini-2.5-flash", 0.2, 128)


async def test_llm_router_uses_cassettes_when_mode_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm.router import LLMRouter

    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")
    with cassette_scope(tmp_path), pytest.raises(CassetteMissError):
        await LLMRouter()._call_model("gemini-2.5-flash", [{"role": "user", "content": "x"}], 0.0, 8)


def test_model_cassette_fixture_scopes_calls_per_test(model_cassette: Path, request: pytest.FixtureRequest) -> None:
    assert model_cassette.parts[-3:] == ("cassettes", "test_model_replay", request.node.name)
    assert model_replay._directory() == model_cassette
