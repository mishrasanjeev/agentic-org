"""LLM Router — Smart model selection with RouteLLM + tiered routing.

Routing tiers:
  - Tier 1 (simple): Gemini 2.5 Flash (free) — data lookups, formatting, simple Q&A
  - Tier 2 (moderate): Gemini 2.5 Pro — analysis, summarization, multi-step reasoning
  - Tier 3 (complex): Claude Opus / GPT-4o — legal analysis, financial modeling

Air-gapped mode (AGENTICORG_LLM_MODE=local):
  - Tier 1: Ollama small model (e.g. llama3.2:3b)
  - Tier 2: Ollama medium model (e.g. llama3.1:8b)
  - Tier 3: vLLM large model (e.g. llama3.1:70b)

Routing modes (AGENTICORG_LLM_ROUTING):
  - "auto": use RouteLLM similarity-weighted router (or heuristic fallback)
  - "tier1" / "tier2" / "tier3": force that tier regardless of query
  - "disabled": use the agent's configured llm_model directly (bypass router)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import external_keys, settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Try to import RouteLLM; fall back to heuristic if unavailable
# ---------------------------------------------------------------------------
_ROUTELLM_AVAILABLE = False
try:
    from routellm.controller import Controller as RouteLLMController  # type: ignore[import-untyped,import-not-found]

    _ROUTELLM_AVAILABLE = True
except Exception:  # noqa: BLE001
    RouteLLMController = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Tier model definitions
# ---------------------------------------------------------------------------

# Cloud tier models
CLOUD_TIERS: dict[str, str] = {
    "tier1": "gemini-2.5-flash",
    "tier2": "gemini-2.5-pro",
    "tier3": os.getenv("AGENTICORG_LLM_TIER3", "claude-sonnet-4-20250514"),
}

# Air-gapped / local tier models (Ollama + vLLM)
LOCAL_TIERS: dict[str, str] = {
    "tier1": os.getenv("AGENTICORG_LOCAL_TIER1", "llama3.2:3b"),
    "tier2": os.getenv("AGENTICORG_LOCAL_TIER2", "llama3.1:8b"),
    "tier3": os.getenv("AGENTICORG_LOCAL_TIER3", "llama3.1:70b"),
}

# Estimated cost per 1K tokens (USD) for display / tracking
TIER_COST_PER_1K: dict[str, float] = {
    "tier1": 0.0,       # Gemini Flash free tier
    "tier2": 0.00125,   # Gemini Pro
    "tier3": 0.015,     # Claude Opus / GPT-4o
}


# ---------------------------------------------------------------------------
# Smart routing logic
# ---------------------------------------------------------------------------


class SmartLLMRouter:
    """Route queries to the optimal LLM tier based on complexity.

    Usage::

        router = SmartLLMRouter()
        model_name = router.route(query="What is 2+2?", config={})
        # -> "gemini-2.5-flash"
    """

    def __init__(self) -> None:
        self._routing_mode: str = settings.llm_routing  # auto|tier1|tier2|tier3|disabled
        self._llm_mode: str = settings.llm_mode          # cloud|local|auto
        self._routellm_controller: Any | None = None
        self._routellm_init_attempted: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, query: str, config: dict[str, Any] | None = None) -> str:
        """Select the best model for *query* given *config*.

        Args:
            query: The user/task query text.
            config: Optional dict; may contain ``routing`` key to override
                    the global routing mode (e.g. from agent's ``llm_config``).

        Returns:
            Model name string suitable for ``create_chat_model()``.
        """
        config = config or {}

        # Per-agent override takes precedence over global setting
        mode = config.get("routing", self._routing_mode).lower().strip()

        # "disabled" => bypass routing entirely; caller uses agent's own model
        if mode == "disabled":
            agent_model = config.get("llm_model", "")
            logger.info(
                "llm_routing_decision",
                tier="disabled",
                model=agent_model or "(agent default)",
                reason="routing disabled",
            )
            return agent_model

        # Forced tier
        if mode in ("tier1", "tier2", "tier3"):
            model = self._resolve_tier_model(mode)
            logger.info(
                "llm_routing_decision",
                tier=mode,
                model=model,
                reason=f"forced tier ({mode})",
            )
            return model

        # Auto mode — classify query complexity
        tier = self._classify_query(query)
        model = self._resolve_tier_model(tier)
        logger.info(
            "llm_routing_decision",
            tier=tier,
            model=model,
            reason="auto-classified",
            query_length=len(query),
        )
        return model

    # ------------------------------------------------------------------
    # Tier resolution
    # ------------------------------------------------------------------

    def _resolve_tier_model(self, tier: str) -> str:
        """Return the concrete model name for *tier*, respecting LLM mode."""
        is_local = self._is_local_mode()
        tiers = LOCAL_TIERS if is_local else CLOUD_TIERS
        return tiers.get(tier, tiers["tier1"])

    def _is_local_mode(self) -> bool:
        """Detect whether we should use local (air-gapped) models."""
        mode = self._llm_mode.lower().strip()
        if mode == "local":
            return True
        if mode == "cloud":
            return False
        # "auto" — detect from env
        if os.getenv("OLLAMA_HOST") or os.getenv("VLLM_API_BASE"):
            return True
        return False

    # ------------------------------------------------------------------
    # Query classification
    # ------------------------------------------------------------------

    def _classify_query(self, query: str) -> str:
        """Classify *query* complexity into tier1/tier2/tier3.

        Uses RouteLLM's similarity-weighted router when available,
        otherwise falls back to a simple length-based heuristic.
        """
        if _ROUTELLM_AVAILABLE:
            try:
                return self._classify_with_routellm(query)
            except Exception as exc:  # noqa: BLE001
                logger.warning("routellm_classify_failed", error=str(exc))
                # Fall through to heuristic

        return self._classify_heuristic(query)

    def _classify_with_routellm(self, query: str) -> str:
        """Use RouteLLM's similarity-weighted (SW) router to score complexity."""
        if not self._routellm_init_attempted:
            self._routellm_init_attempted = True
            try:
                self._routellm_controller = RouteLLMController(
                    routers=["sw_ranking"],
                    strong_model=CLOUD_TIERS["tier3"],
                    weak_model=CLOUD_TIERS["tier1"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("routellm_init_failed", error=str(exc))
                self._routellm_controller = None

        if self._routellm_controller is None:
            return self._classify_heuristic(query)

        # RouteLLM returns the model name; map back to tier
        routed_model = self._routellm_controller.route(
            prompt=query,
            router="sw_ranking",
            threshold=0.5,
        )
        if routed_model == CLOUD_TIERS["tier3"]:
            return "tier3"
        if routed_model == CLOUD_TIERS["tier1"]:
            return "tier1"
        return "tier2"

    @staticmethod
    def _classify_heuristic(query: str) -> str:
        """Simple heuristic fallback when RouteLLM is not installed.

        Rules:
          - query < 100 chars  => tier1 (simple lookup / formatting)
          - query < 500 chars  => tier2 (moderate analysis)
          - query >= 500 chars => tier3 (complex reasoning)
        """
        length = len(query.strip())
        if length < 100:
            return "tier1"
        if length < 500:
            return "tier2"
        return "tier3"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
smart_router = SmartLLMRouter()


# ===================================================================
# Legacy LLMRouter (backward-compatible) — kept for core.agents.base
# ===================================================================


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
      - "gemini-*"  -> Google Generative AI API (free tier available)
      - "claude-*"  -> Anthropic API
      - "gpt-*"     -> OpenAI API
    """

    def __init__(self) -> None:
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
