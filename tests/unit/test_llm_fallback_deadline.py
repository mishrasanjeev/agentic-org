# SPDX-License-Identifier: Apache-2.0
"""Failover is bounded and never bypasses policy or explicit model selection."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.llm import router as router_module


class ProviderStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider status {status_code}")
        self.status_code = status_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), ConnectionError(), ProviderStatusError(429), ProviderStatusError(503)],
)
async def test_transient_primary_failure_uses_fallback(failure: Exception) -> None:
    router = router_module.LLMRouter()
    router.primary_model = "gemini-primary"
    router.fallback_model = "gemini-fallback"
    response = router_module.LLMResponse(content="recovered", model=router.fallback_model)
    router._call_model = AsyncMock(side_effect=[failure, response])

    result = await router.complete([{"role": "user", "content": "hello"}])

    assert result is response
    assert [call.args[0] for call in router._call_model.await_args_list] == [
        router.primary_model,
        router.fallback_model,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ProviderStatusError(400),
        ProviderStatusError(401),
        router_module.LLMProviderConfigurationError("not configured"),
        router_module.DailyBudgetExceeded("cap reached"),
        ValueError("unsupported model"),
        RuntimeError("internal bug"),
    ],
)
async def test_permanent_failure_never_uses_fallback(failure: Exception) -> None:
    router = router_module.LLMRouter()
    router.primary_model = "gemini-primary"
    router.fallback_model = "gemini-fallback"
    router._call_model = AsyncMock(side_effect=failure)

    with pytest.raises(type(failure)):
        await router.complete([{"role": "user", "content": "hello"}])

    assert router._call_model.await_count == 1


@pytest.mark.asyncio
async def test_explicit_model_does_not_silently_change_provider() -> None:
    router = router_module.LLMRouter()
    router._call_model = AsyncMock(side_effect=TimeoutError("upstream timed out"))

    with pytest.raises(TimeoutError):
        await router.complete([{"role": "user", "content": "hello"}], model_override="claude-selected")

    router._call_model.assert_awaited_once()


@pytest.mark.asyncio
async def test_primary_timeout_reserves_time_for_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router_module.settings, "llm_complete_timeout_seconds", 0.3)
    monkeypatch.setattr(router_module.settings, "llm_primary_timeout_fraction", 0.5)
    router = router_module.LLMRouter()
    router.primary_model = "gemini-primary"
    router.fallback_model = "gemini-fallback"

    async def call(model: str, *_args: object) -> router_module.LLMResponse:
        if model == router.primary_model:
            await asyncio.sleep(1)
        return router_module.LLMResponse(content="recovered", model=model)

    router._call_model = AsyncMock(side_effect=call)
    result = await router.complete([{"role": "user", "content": "hello"}])
    assert result.model == router.fallback_model
    assert router._call_model.await_count == 2


@pytest.mark.asyncio
async def test_fallback_cannot_outlive_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router_module.settings, "llm_complete_timeout_seconds", 0.2)
    router = router_module.LLMRouter()
    router.primary_model = "gemini-primary"
    router.fallback_model = "gemini-fallback"

    async def call(model: str, *_args: object) -> router_module.LLMResponse:
        if model == router.primary_model:
            raise TimeoutError("primary timed out")
        await asyncio.sleep(1)
        return router_module.LLMResponse(content="late", model=model)

    router._call_model = AsyncMock(side_effect=call)
    with pytest.raises(TimeoutError):
        await router.complete([{"role": "user", "content": "hello"}])
    assert router._call_model.await_count == 2
