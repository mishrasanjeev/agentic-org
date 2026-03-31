"""Create LangChain ChatModel instances from config.

Supports Gemini (default), Claude, and GPT with automatic fallback.
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel


def create_chat_model(
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> BaseChatModel:
    """Create a LangChain ChatModel from a model name string.

    Supported patterns:
      - "gemini-*"  -> ChatGoogleGenerativeAI
      - "claude-*"  -> ChatAnthropic
      - "gpt-*"     -> ChatOpenAI

    Falls back to Gemini Flash if the requested model's API key is missing.
    """
    resolved = _resolve_model(model)

    if "gemini" in resolved:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=resolved,
            temperature=temperature,
            max_output_tokens=max_tokens,
            google_api_key=os.getenv("GOOGLE_GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")),
        )

    if "claude" in resolved:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )

    if "gpt" in resolved:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        )

    # Default fallback
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
