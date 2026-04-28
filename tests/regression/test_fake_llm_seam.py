"""Foundation #7 PR-A — fake LLM hermetic seam regressions.

Pinned behaviors:

- ``is_active()`` reflects the env var.
- ``fake_complete`` returns a deterministic response for the same
  ``(model, messages)`` input.
- Custom registered responses win over the default.
- ``reset()`` clears registered responses + the call log.
- ``LLMRouter._call_model`` short-circuits to the fake when the
  flag is set — we verify by patching the real provider methods to
  raise and confirming a fake call still succeeds.
- The fake's ``cost_usd`` is always 0 so it can't trip the cap.
"""

from __future__ import annotations

import os

import pytest

from core.test_doubles import fake_llm


def test_is_active_reflects_env_var(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)
    assert fake_llm.is_active() is False
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_LLM", "1")
    assert fake_llm.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_LLM", "true")
    assert fake_llm.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_LLM", "no")
    assert fake_llm.is_active() is False


def test_fake_complete_is_deterministic_for_same_input() -> None:
    msg = [{"role": "user", "content": "summarise this paragraph"}]
    a = fake_llm.fake_complete(
        model="gemini-2.5-flash", messages=msg, temperature=0.2, max_tokens=500
    )
    b = fake_llm.fake_complete(
        model="gemini-2.5-flash", messages=msg, temperature=0.2, max_tokens=500
    )
    assert a["content"] == b["content"]
    assert a["raw"]["fingerprint"] == b["raw"]["fingerprint"]


def test_fake_complete_differs_for_different_input() -> None:
    a = fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "alpha"}],
        temperature=0.0,
        max_tokens=100,
    )
    b = fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "beta"}],
        temperature=0.0,
        max_tokens=100,
    )
    assert a["raw"]["fingerprint"] != b["raw"]["fingerprint"]


def test_register_response_overrides_default() -> None:
    fake_llm.register_response(
        prompt_contains="extract entities",
        content='{"entities": ["Alice"]}',
    )
    out = fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[
            {"role": "user", "content": "Please extract entities from this text"}
        ],
        temperature=0.0,
        max_tokens=200,
    )
    assert out["content"] == '{"entities": ["Alice"]}'


def test_register_empty_marker_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        fake_llm.register_response(prompt_contains="   ", content="x")


def test_call_log_records_each_call_in_order() -> None:
    assert fake_llm.call_count() == 0
    fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "first"}],
        temperature=0.0,
        max_tokens=100,
    )
    fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "second"}],
        temperature=0.0,
        max_tokens=100,
    )
    log = fake_llm.call_log()
    assert len(log) == 2
    assert log[0].messages[0]["content"] == "first"
    assert log[1].messages[0]["content"] == "second"


def test_reset_clears_state() -> None:
    fake_llm.register_response(prompt_contains="leak", content="leaked")
    fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "leak"}],
        temperature=0.0,
        max_tokens=100,
    )
    assert fake_llm.call_count() == 1
    fake_llm.reset()
    assert fake_llm.call_count() == 0
    # Registered response is also gone — re-fingerprint without
    # the override should now return the default marker.
    out = fake_llm.fake_complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "leak"}],
        temperature=0.0,
        max_tokens=100,
    )
    assert out["content"] != "leaked"
    assert "[FAKE_LLM" in out["content"]


def test_fake_cost_is_always_zero() -> None:
    """The fake must never trip the daily cost cap."""
    out = fake_llm.fake_complete(
        model="gemini-2.5-pro",
        messages=[{"role": "user", "content": "x" * 10000}],
        temperature=0.0,
        max_tokens=4096,
    )
    assert out["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_router_short_circuits_to_fake_when_flag_set(monkeypatch) -> None:
    """End-to-end: with the flag on, LLMRouter._call_model never
    reaches _call_gemini/claude/openai. We patch all three to raise
    and assert the call still returns a FAKE_LLM response."""
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_LLM", "1")

    from core.llm.router import LLMResponse, LLMRouter

    async def _explode(*args, **kwargs):
        raise AssertionError("real provider must not be called when fake is active")

    router = LLMRouter()
    monkeypatch.setattr(router, "_call_gemini", _explode)
    monkeypatch.setattr(router, "_call_claude", _explode)
    monkeypatch.setattr(router, "_call_openai", _explode)

    resp = await router.complete(
        messages=[{"role": "user", "content": "hello"}],
        model_override="gemini-2.5-flash",
    )
    assert isinstance(resp, LLMResponse)
    assert "[FAKE_LLM" in resp.content or '"fake"' in resp.content
    assert resp.cost_usd == 0.0


@pytest.mark.asyncio
async def test_router_uses_real_provider_when_flag_off(monkeypatch) -> None:
    """Inverse pin: with the flag OFF, the seam must NOT short-
    circuit. We assert the real provider gets called by replacing
    it with a sentinel."""
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)

    from core.llm.router import LLMResponse, LLMRouter

    sentinel_called = {"yes": False}

    async def _sentinel(*args, **kwargs):
        sentinel_called["yes"] = True
        return LLMResponse(content="REAL", model="gemini-2.5-flash")

    router = LLMRouter()
    monkeypatch.setattr(router, "_call_gemini", _sentinel)

    resp = await router.complete(
        messages=[{"role": "user", "content": "hello"}],
        model_override="gemini-2.5-flash",
    )
    assert sentinel_called["yes"] is True
    assert resp.content == "REAL"


def test_conftest_default_makes_fake_active() -> None:
    """The conftest sets the env var so every test gets the fake by
    default. Pin that the variable is set during a test run."""
    assert os.environ.get("AGENTICORG_TEST_FAKE_LLM") == "1"
    assert fake_llm.is_active() is True


@pytest.mark.asyncio
async def test_unsupported_model_still_raises_under_fake() -> None:
    """The fake must NOT mask the production validation contract.
    An unsupported model name should raise ValueError even when
    the fake is active — otherwise tests asserting the rejection
    silently pass (a false-green that Foundation #8 forbids).

    Tests _call_model directly (rather than complete()) because
    complete() has a fallback chain that masks the primary error.
    The fix is to validate BEFORE the fake-active check.
    """
    from core.llm.router import LLMRouter

    router = LLMRouter()
    with pytest.raises(ValueError, match="Unsupported model"):
        await router._call_model(
            model="some-unknown-model-xyz",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=100,
        )
