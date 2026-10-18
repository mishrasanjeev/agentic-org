# SPDX-License-Identifier: Apache-2.0
"""Generation settings shared by the native and LangGraph Gemini clients."""

from typing import Any


def thinking_profile(config: object) -> dict[str, str]:
    """Extract the optional agent profile without adding defaults to legacy rows."""
    level = config.get("thinking_level") if isinstance(config, dict) else None
    if level is None:
        return {}
    if level != "medium":
        raise ValueError("Unsupported thinking_level")
    return {"thinking_level": level}


def validate_thinking_level(model: str, thinking_level: str | None) -> None:
    """An explicit profile must not silently apply to another model or provider."""
    if thinking_level is not None and (
        thinking_level != "medium" or model.removeprefix("models/") != "gemini-3.5-flash-lite"
    ):
        raise ValueError("thinking_level supports medium only for gemini-3.5-flash-lite")


def generation_config(
    model: str, temperature: float, max_tokens: int, thinking_level: str | None = None
) -> dict[str, Any]:
    validate_thinking_level(model, thinking_level)
    config: dict[str, Any] = {"max_output_tokens": max_tokens}
    if model.removeprefix("models/") == "gemini-3.5-flash-lite":
        # Omission preserves the provider default. Extra thinking is per-agent opt-in.
        # Sampling parameters are ignored for this model. Keep older models unchanged.
        # https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6
        if thinking_level is not None:
            config["thinking_config"] = {"thinking_level": thinking_level}
    else:
        config["temperature"] = temperature
    return config
