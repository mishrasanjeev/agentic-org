"""Create LangChain ChatModel instances from config.

Supports Gemini (default), Claude, GPT, and local models (Ollama/vLLM)
with automatic fallback and smart tier-based routing via LLMRouter.
"""

from __future__ import annotations

import os

import structlog
from langchain_core.language_models import BaseChatModel

from core.llm.router import smart_router

logger = structlog.get_logger()


def create_chat_model(
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    *,
    query: str = "",
    routing_config: dict | None = None,
) -> BaseChatModel:
    """Create a LangChain ChatModel, optionally using smart routing.

    When *query* is provided and routing is not disabled, the SmartLLMRouter
    selects the optimal model tier.  Otherwise the explicitly requested
    *model* (or the env-var default) is used directly.

    Args:
        model: Explicit model name override.  When empty, the router or
               env default decides.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        query: The user query / task text.  Passed to the router for
               complexity classification when routing is enabled.
        routing_config: Optional dict from the agent's ``llm_config``
                        (may contain ``routing`` key).

    Returns:
        A LangChain ``BaseChatModel`` ready for ``.ainvoke()``.
    """
    routing_config = routing_config or {}
    routing_mode = routing_config.get("routing", os.getenv("AGENTICORG_LLM_ROUTING", "auto"))

    # If routing is not disabled and we have a query, ask the smart router
    if routing_mode != "disabled" and query:
        try:
            # Let the router pick the best model; pass the agent's own model
            # so "disabled" routing can fall back to it
            cfg = {**routing_config, "llm_model": model}
            routed_model = smart_router.route(query=query, config=cfg)
            if routed_model:
                model = routed_model
        except Exception as exc:  # noqa: BLE001
            logger.warning("smart_routing_failed", error=str(exc), fallback=model)
            # Fall through to normal model resolution

    resolved = _resolve_model(model)
    return _build_model(resolved, temperature, max_tokens)


def _build_model(resolved: str, temperature: float, max_tokens: int) -> BaseChatModel:
    """Instantiate the correct LangChain ChatModel for *resolved* model name."""

    # Local models via Ollama (OpenAI-compatible API)
    if _is_ollama_model(resolved):
        from langchain_openai import ChatOpenAI

        base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1"
        return ChatOpenAI(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            openai_api_key="ollama",  # Ollama doesn't need a real key
            openai_api_base=base_url,
        )

    # Local models via vLLM (OpenAI-compatible API)
    if _is_vllm_model(resolved):
        from langchain_openai import ChatOpenAI

        base_url = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")
        return ChatOpenAI(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            openai_api_key=os.getenv("VLLM_API_KEY", "vllm"),
            openai_api_base=base_url,
        )

    # Cloud: Gemini
    if "gemini" in resolved:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=resolved,
            temperature=temperature,
            max_output_tokens=max_tokens,
            google_api_key=os.getenv("GOOGLE_GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")),
        )

    # Cloud: Claude
    if "claude" in resolved:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(  # type: ignore[call-arg]
            model_name=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )

    # Cloud: GPT
    if "gpt" in resolved:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        )

    # Default fallback — Gemini Flash (free)
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=temperature,
        max_output_tokens=max_tokens,
        google_api_key=os.getenv("GOOGLE_GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")),
    )


def _resolve_model(model: str) -> str:
    """Resolve model to a usable one, falling back to Gemini if API key missing."""
    if not model:
        return os.getenv("AGENTICORG_LLM_PRIMARY", "gemini-2.5-flash")

    m = model.lower()

    # Local models don't need API key checks
    if _is_ollama_model(m) or _is_vllm_model(m):
        return model

    if "gemini" in m:
        return model

    if "claude" in m:
        if os.getenv("ANTHROPIC_API_KEY"):
            return model
        return "gemini-2.5-flash"

    if "gpt" in m:
        if os.getenv("OPENAI_API_KEY"):
            return model
        return "gemini-2.5-flash"

    return "gemini-2.5-flash"


def _is_ollama_model(model: str) -> bool:
    """Check if this is an Ollama-served local model."""
    m = model.lower()
    # Ollama model patterns: "llama3.2:3b", "mistral:7b", etc.
    ollama_prefixes = ("llama", "mistral", "phi", "qwen", "codellama", "deepseek", "gemma")
    return any(m.startswith(prefix) for prefix in ollama_prefixes) or "ollama" in m


def _is_vllm_model(model: str) -> bool:
    """Check if this is a vLLM-served model."""
    m = model.lower()
    return "vllm/" in m or os.getenv("VLLM_API_BASE", "") != "" and ":" in m and not _is_ollama_model(m)
