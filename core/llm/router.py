"""LLM Router — primary Claude, fallback GPT-4o, optional Gemini."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import settings, external_keys

logger = structlog.get_logger()


@dataclass
class LLMResponse:
    """Standardized LLM response."""
    content: str
    model: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMRouter:
    """Route LLM calls to primary/fallback models."""

    def __init__(self):
        self.primary_model = settings.llm_primary
        self.fallback_model = settings.llm_fallback
        self.temperature = settings.llm_temperature

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_override: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send completion request with automatic failover."""
        model = model_override or self.primary_model
        temp = temperature if temperature is not None else self.temperature

        try:
            return await self._call_model(model, messages, temp, max_tokens)
        except Exception as e:
            logger.warning("llm_primary_failed", model=model, error=str(e))
            if model != self.fallback_model:
                logger.info("llm_falling_back", fallback=self.fallback_model)
                return await self._call_model(
                    self.fallback_model, messages, temp, max_tokens
                )
            raise

    async def _call_model(
        self, model: str, messages: list[dict], temperature: float, max_tokens: int
    ) -> LLMResponse:
        start = time.monotonic()

        if "claude" in model:
            return await self._call_claude(model, messages, temperature, max_tokens, start)
        elif "gpt" in model:
            return await self._call_openai(model, messages, temperature, max_tokens, start)
        else:
            raise ValueError(f"Unsupported model: {model}")

    async def _call_claude(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=external_keys.anthropic_api_key)
        # Separate system message
        system_msg = ""
        user_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                user_msgs.append(m)

        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_msg,
            messages=user_msgs,
        )
        latency = int((time.monotonic() - start) * 1000)
        tokens = response.usage.input_tokens + response.usage.output_tokens
        # Approximate cost (Claude 3.5 Sonnet pricing)
        cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
        return LLMResponse(
            content=response.content[0].text,
            model=model, tokens_used=tokens, cost_usd=cost,
            latency_ms=latency, raw=response.model_dump(),
        )

    async def _call_openai(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
        import openai
        client = openai.AsyncOpenAI(api_key=external_keys.openai_api_key)
        response = await client.chat.completions.create(
            model=model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        latency = int((time.monotonic() - start) * 1000)
        tokens = response.usage.total_tokens if response.usage else 0
        cost = tokens * 10 / 1_000_000  # Approximate
        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=model, tokens_used=tokens, cost_usd=cost,
            latency_ms=latency, raw=response.model_dump(),
        )


llm_router = LLMRouter()
