"""LLM Router — Smart model selection with RouteLLM + tiered routing.

Routing tiers (cloud, default ``AGENTICORG_LLM_MODE=cloud``):

  - Tier 1 (simple):    ``gemini-2.5-flash-lite`` — high-volume
                        classification, extraction, simple Q&A.
                        ~$0.04/1M input, ~$0.10/1M output.
  - Tier 2 (moderate):  ``gemini-2.5-flash`` — multi-step reasoning,
                        light synthesis. $0.075/1M input,
                        $0.30/1M output.
  - Tier 3 (complex):   ``gemini-2.5-pro`` — heavy synthesis, deep
                        reasoning. $1.25/1M input, $5/1M output.
                        ``AGENTICORG_LLM_TIER3`` overrides for hosted
                        Claude/GPT.

Air-gapped mode (``AGENTICORG_LLM_MODE=local``):
  - Tier 1: Ollama small model (e.g. llama3.2:3b)
  - Tier 2: Ollama medium model (e.g. llama3.1:8b)
  - Tier 3: vLLM large model (e.g. llama3.1:70b)

Routing modes (``AGENTICORG_LLM_ROUTING``):
  - ``auto``: RouteLLM similarity-weighted (or heuristic fallback).
  - ``tier1`` / ``tier2`` / ``tier3``: force that tier.
  - ``disabled``: use the agent's configured llm_model directly.

Hard cap (``AGENTICORG_GEMINI_DAILY_USD_CAP``, default ``10.0``):
  Before any Gemini call the router sums today's ``cost_usd`` from
  ``agent_task_results`` (UTC day). If the running total ≥ cap, the
  call is refused with :class:`DailyBudgetExceeded` (HTTP 429 surface)
  so a runaway workflow or shadow-batch cannot blow past the daily
  budget. Set the env var to ``0`` to disable (NOT recommended).
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

# Cloud tier models. Tier1 was previously gemini-2.5-flash; the
# 3-tier rework moves Flash to Tier2 and uses Flash-Lite for Tier1
# so high-volume classify/extract calls land at the cheapest model.
CLOUD_TIERS: dict[str, str] = {
    "tier1": os.getenv("AGENTICORG_LLM_TIER1", "gemini-2.5-flash-lite"),
    "tier2": os.getenv("AGENTICORG_LLM_TIER2", "gemini-2.5-flash"),
    "tier3": os.getenv("AGENTICORG_LLM_TIER3", "gemini-2.5-pro"),
}

# Air-gapped / local tier models (Ollama + vLLM)
LOCAL_TIERS: dict[str, str] = {
    "tier1": os.getenv("AGENTICORG_LOCAL_TIER1", "llama3.2:3b"),
    "tier2": os.getenv("AGENTICORG_LOCAL_TIER2", "llama3.1:8b"),
    "tier3": os.getenv("AGENTICORG_LOCAL_TIER3", "llama3.1:70b"),
}

# Display-only running estimates per 1K tokens (USD). Used by the
# ``llm_routing_decision`` log line so operators can sanity-check
# tier choices. Authoritative cost computation lives in
# ``GEMINI_PRICE_PER_1M`` below and is used by the per-call cost
# write to agent_task_results.
TIER_COST_PER_1K: dict[str, float] = {
    "tier1": 0.000_10,    # gemini-2.5-flash-lite output
    "tier2": 0.000_30,    # gemini-2.5-flash output
    "tier3": 0.005_00,    # gemini-2.5-pro output
}

# Pricing matrix — per 1M tokens, USD. Keep this in sync with
# https://ai.google.dev/pricing as Google updates Gemini prices.
# When a new model is added, add an entry here AND update
# core/billing/spend.py if the model name introduces a new prefix.
GEMINI_PRICE_PER_1M: dict[str, dict[str, float]] = {
    # Flash-Lite (cheapest)
    "gemini-2.5-flash-lite": {"input": 0.04, "output": 0.10},
    # Flash (mid)
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.5-flash-preview-05-20": {"input": 0.075, "output": 0.30},
    # Pro (heavy)
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


def gemini_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a single Gemini call.

    Falls back to the Flash price when the model is unknown — the
    fallback is intentionally non-zero so an unknown model never
    silently writes ``cost_usd=0`` (which would make budget alerts
    blind to that traffic). The unknown-model log line lets ops
    catch the gap and add a row to ``GEMINI_PRICE_PER_1M``.
    """
    if model in GEMINI_PRICE_PER_1M:
        rates = GEMINI_PRICE_PER_1M[model]
    else:
        logger.warning("gemini_unknown_model_pricing", model=model)
        rates = GEMINI_PRICE_PER_1M["gemini-2.5-flash"]
    return (
        input_tokens * rates["input"] + output_tokens * rates["output"]
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Hard daily cap (env-tunable)
# ---------------------------------------------------------------------------


class DailyBudgetExceeded(RuntimeError):  # noqa: N818 — surface name; "Error" suffix would be redundant
    """Raised when today's Gemini spend has hit ``AGENTICORG_GEMINI_DAILY_USD_CAP``.

    Surfaced as HTTP 429 by the API layer. Protects against runaway
    workflows / shadow-batches that would otherwise keep spending
    past the daily budget alert.
    """


def _gemini_daily_cap_usd() -> float:
    """Return the configured daily cap. Default $10."""
    raw = (os.getenv("AGENTICORG_GEMINI_DAILY_USD_CAP") or "10.0").strip()
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "gemini_daily_cap_invalid",
            value=raw,
            using_default=10.0,
        )
        return 10.0


async def _todays_gemini_spend_usd() -> float:
    """Sum today's (UTC) ``cost_usd`` from ``agent_task_results``.

    Read-only, single SELECT. Falls back to 0.0 on any DB error so
    a transient DB blip doesn't block every request — the tradeoff
    is that the cap can lag during the blip, but loud refusal would
    be worse than a brief overshoot.
    """
    try:
        from datetime import UTC, datetime

        from sqlalchemy import text as _text

        from core.database import async_session_factory

        utc_today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with async_session_factory() as session:
            row = (await session.execute(
                _text(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM agent_task_results "
                    "WHERE created_at >= :since AND llm_model LIKE 'gemini%'"
                ),
                {"since": utc_today},
            )).scalar_one()
        return float(row or 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gemini_daily_spend_lookup_failed", error=str(exc))
        return 0.0


async def assert_under_gemini_cap(estimated_cost_usd: float = 0.0) -> None:
    """Refuse the call when today's spend + estimate would exceed the cap.

    Call sites: :class:`LLMRouter._call_gemini` invokes this BEFORE
    making the upstream API call so we never mint a charge that
    pushes us past the cap. Set the cap to ``0`` to disable.
    """
    cap = _gemini_daily_cap_usd()
    if cap <= 0:
        return
    spent = await _todays_gemini_spend_usd()
    if spent + estimated_cost_usd >= cap:
        raise DailyBudgetExceeded(
            f"Gemini daily spend cap reached: spent ${spent:.4f} of "
            f"${cap:.2f}/day. Set AGENTICORG_GEMINI_DAILY_USD_CAP higher "
            "or wait for the UTC day to roll over."
        )


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

        # Validate the model name FIRST so the fake LLM preserves
        # the same contract as the real providers. Without this,
        # a test asserting "unsupported model raises ValueError"
        # would silently pass under the fake — exactly the false-
        # green pattern Foundation #8 forbids.
        if not any(prefix in model for prefix in ("gemini", "claude", "gpt")):
            raise ValueError(f"Unsupported model: {model}")

        # Foundation #7 PR-A: hermetic-CI seam. When the env flag is
        # set, short-circuit ALL providers to the deterministic fake
        # so PR CI never makes a real LLM call. The fake is keyed by
        # prompt fingerprint so cost-cap and routing tests stay
        # reproducible. See docs/hermetic_test_doubles.md.
        from core.test_doubles import fake_llm  # noqa: PLC0415 — local import keeps prod cold-path lean

        if fake_llm.is_active():
            payload = fake_llm.fake_complete(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return LLMResponse(**payload)

        if "gemini" in model:
            return await self._call_gemini(model, messages, temperature, max_tokens, start)
        elif "claude" in model:
            return await self._call_claude(model, messages, temperature, max_tokens, start)
        else:  # gpt
            return await self._call_openai(model, messages, temperature, max_tokens, start)

    async def _call_gemini(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
        """Call Google Gemini via the google.genai SDK.

        Free tier: 15 RPM, 1M tokens/day for Flash and Flash-Lite.
        Cost beyond free tier: see ``GEMINI_PRICE_PER_1M``.

        Refuses the call when today's spend has already hit
        ``AGENTICORG_GEMINI_DAILY_USD_CAP`` (default $10/day) so a
        runaway workflow can't keep charging past the budget.
        """
        from google import genai

        # Hard daily cap — fail-closed BEFORE we mint a new charge.
        await assert_under_gemini_cap()

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

        # Cost — looked up from GEMINI_PRICE_PER_1M so flash-lite,
        # flash, and pro each get their actual price (the previous
        # branch counted flash-lite at flash prices, ~2x reality).
        cost = gemini_cost_usd(model, input_tokens, output_tokens)

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
