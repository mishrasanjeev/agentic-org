"""LLM Router — Gemini (free), Claude, GPT-4o with automatic failover."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import external_keys, settings

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
    """Route LLM calls to primary/fallback models.

    Default config uses Gemini Flash (free tier) as primary with
    Gemini Pro as fallback. Switch to Claude/GPT-4o when you have
    paying customers by changing AGENTICORG_LLM_PRIMARY.

    Supported model patterns:
      - "gemini-*"  → Google Generative AI API (free tier available)
      - "claude-*"  → Anthropic API
      - "gpt-*"     → OpenAI API
    """

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
                return await self._call_model(self.fallback_model, messages, temp, max_tokens)
            raise

    async def _call_model(
        self, model: str, messages: list[dict], temperature: float, max_tokens: int
    ) -> LLMResponse:
        start = time.monotonic()

        if "gemini" in model:
            return await self._call_gemini(model, messages, temperature, max_tokens, start)
        elif "claude" in model:
            return await self._call_claude(model, messages, temperature, max_tokens, start)
        elif "gpt" in model:
            return await self._call_openai(model, messages, temperature, max_tokens, start)
        else:
            raise ValueError(f"Unsupported model: {model}")

    async def _call_gemini(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
        """Call Google Gemini via the google.genai SDK.

        Free tier: 15 RPM, 1M tokens/day for Flash.
        Cost beyond free tier: Flash $0.075/1M input, $0.30/1M output.
        """
        from google import genai

        client = genai.Client(api_key=external_keys.google_gemini_api_key)

        # Separate system instruction from conversation
        system_instruction = None
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_instruction = m["content"]
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        config = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system_instruction:
            config["system_instruction"] = system_instruction

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        latency = int((time.monotonic() - start) * 1000)

        # Extract token counts
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        total_tokens = input_tokens + output_tokens

        # Cost (Gemini Flash pricing — free tier covers most pre-customer usage)
        if "flash" in model:
            cost = (input_tokens * 0.075 + output_tokens * 0.30) / 1_000_000
        else:
            cost = (input_tokens * 1.25 + output_tokens * 5.0) / 1_000_000

        return LLMResponse(
            content=response.text,
            model=model,
            tokens_used=total_tokens,
            cost_usd=cost,
            latency_ms=latency,
            raw={"candidates": str(response.candidates)},
        )

    async def _call_claude(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
        """Call Anthropic Claude API."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=external_keys.anthropic_api_key)

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
        cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
        return LLMResponse(
            content=response.content[0].text,
            model=model,
            tokens_used=tokens,
            cost_usd=cost,
            latency_ms=latency,
            raw=response.model_dump(),
        )

    async def _call_openai(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
        """Call OpenAI API."""
        import openai

        client = openai.AsyncOpenAI(api_key=external_keys.openai_api_key)
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency = int((time.monotonic() - start) * 1000)
        tokens = response.usage.total_tokens if response.usage else 0
        cost = tokens * 10 / 1_000_000
        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=model,
            tokens_used=tokens,
            cost_usd=cost,
            latency_ms=latency,
            raw=response.model_dump(),
        )


llm_router = LLMRouter()
