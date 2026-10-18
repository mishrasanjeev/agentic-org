"""Generation settings shared by the native and LangGraph Gemini clients."""

from typing import Any


def generation_config(model: str, temperature: float, max_tokens: int) -> dict[str, Any]:
    config: dict[str, Any] = {"max_output_tokens": max_tokens}
    if model.removeprefix("models/") == "gemini-3.5-flash-lite":
        # Minimal is the provider default; autonomous multi-step reasoning needs medium/high.
        # Sampling parameters are ignored for this model. Keep older models unchanged.
        # https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6
        config["thinking_config"] = {"thinking_level": "medium"}
    else:
        config["temperature"] = temperature
    return config
