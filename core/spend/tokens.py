# SPDX-License-Identifier: Apache-2.0
"""Token details a metered call carries beyond its input and output counts.

The gateway record already holds input, output and total tokens. Spend
needs a little more to price a call: how many input tokens were served from
the provider's cache (priced lower), billable output a provider reports
outside its output count (Gemini's thoughts on the router path), an
estimate of the prompt when a router call timed out with no response, the
in-house endpoint a model object points at, and the billing account.

These functions run only inside the meter's guarded body
(``core/spend/meter.py``): a malformed response can never reach the call
site. Only counts and lengths are read, never content.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from core.spend.context import CallUsage

CHARS_PER_TOKEN = 4  # core/rag/chunking.py


@dataclass(frozen=True)
class UsageDetails:
    cached_input_tokens: int | None = None  # subset of the recorded input tokens
    reasoning_tokens: int | None = None  # informational; already inside output where the provider counts it there
    extra_output_tokens: int | None = None  # billable output NOT inside the recorded output (Gemini router thoughts)
    estimated_input_tokens: int | None = None  # router primary timeout: prompt characters / CHARS_PER_TOKEN
    serving_provider: str | None = None  # "ollama"/"vllm" when the model object points at an in-house endpoint
    tenant_id: str | None = None  # used only when the record has no tenant (case_model_call)
    billing_account: str | None = None
    # True when the provider counts cache reads outside its input count (Anthropic's
    # ``cache_read_input_tokens``): the cached tokens are then added, not taken out of the input.
    cached_outside_input: bool = False


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _count(value: Any) -> int | None:
    """A non-negative token count, or ``None`` when absent or malformed."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _path(obj: Any, *names: str) -> Any:
    for name in names:
        obj = _get(obj, name)
        if obj is None:
            return None
    return obj


def details_of(usage: CallUsage | None) -> UsageDetails | None:
    """The details a call site's ``CallUsage`` carries, or ``None``."""
    if usage is None:
        return None
    if usage.source == "router":
        found = from_router(usage.response, usage.error, usage.messages)
    elif usage.source == "message":
        found = from_message(usage.response, llm=usage.llm)
    else:
        found = None
    base = found or UsageDetails()
    return UsageDetails(
        cached_input_tokens=base.cached_input_tokens,
        reasoning_tokens=base.reasoning_tokens,
        extra_output_tokens=base.extra_output_tokens,
        estimated_input_tokens=base.estimated_input_tokens,
        serving_provider=base.serving_provider,
        tenant_id=str(usage.tenant_id) if usage.tenant_id else None,
        billing_account=usage.billing_account or None,
        cached_outside_input=base.cached_outside_input,
    )


def _prompt_estimate(messages: Any) -> int | None:
    total = 0
    for message in messages or []:
        content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
        total += len(str(content if content is not None else ""))
    return math.ceil(total / CHARS_PER_TOKEN) if total else None


def from_router(response: Any, error: BaseException | None, messages: Any) -> UsageDetails | None:
    """Details of a direct-router call from ``LLMResponse.raw``, or the prompt estimate after a timeout."""
    if response is None:
        if isinstance(error, TimeoutError):
            estimate = _prompt_estimate(messages)
            return UsageDetails(estimated_input_tokens=estimate) if estimate else None
        return None
    raw = _get(response, "raw")
    usage = _get(raw, "usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return None
    # OpenAI: prompt_tokens_details.cached_tokens is part of prompt_tokens.
    cached = _count(_path(usage, "prompt_tokens_details", "cached_tokens"))
    reasoning = _count(_path(usage, "completion_tokens_details", "reasoning_tokens"))
    outside = False
    # Anthropic: cache reads are counted outside input_tokens.
    anthropic_cached = _count(usage.get("cache_read_input_tokens"))
    if anthropic_cached:
        cached, outside = anthropic_cached, True
    # Gemini (added to raw by the router only while spend is on): cached content is part of
    # the prompt count; thoughts are billed as output and are not in the candidates count.
    gemini_cached = _count(usage.get("cached_content_token_count"))
    if gemini_cached:
        cached = gemini_cached
    thoughts = _count(usage.get("thoughts_token_count"))
    if not any((cached, reasoning, thoughts)):
        return None
    return UsageDetails(
        cached_input_tokens=cached or None,
        reasoning_tokens=reasoning or None,
        extra_output_tokens=thoughts or None,
        cached_outside_input=outside,
    )


def from_message(message: Any, *, llm: Any = None) -> UsageDetails | None:
    """Details of an AI message (LangChain usage metadata, or the OpenAI token usage in its metadata)."""
    usage = _get(message, "usage_metadata")
    cached = _count(_path(usage, "input_token_details", "cache_read"))
    reasoning = _count(_path(usage, "output_token_details", "reasoning"))
    if cached is None and reasoning is None:
        token_usage = _path(message, "response_metadata", "token_usage")
        cached = _count(_path(token_usage, "prompt_tokens_details", "cached_tokens"))
        reasoning = _count(_path(token_usage, "completion_tokens_details", "reasoning_tokens"))
    serving = serving_provider_of(llm) if llm is not None else None
    if not cached and not reasoning and serving is None:
        return None
    return UsageDetails(
        cached_input_tokens=cached or None, reasoning_tokens=reasoning or None, serving_provider=serving
    )


def _unwrap(llm: Any) -> Any:
    """The chat model behind a ``bind_tools`` binding."""
    for _ in range(4):
        inner = getattr(llm, "bound", None)
        if inner is None:
            return llm
        llm = inner
    return llm


def _base(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def serving_provider_of(llm: Any) -> str | None:
    """``ollama`` or ``vllm`` when the model object points at the deployment's in-house endpoint."""
    model = _unwrap(llm)
    base = _base(getattr(model, "openai_api_base", None))
    if not base:
        return None
    ollama = _base(os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")) + "/v1")
    vllm = _base(os.getenv("VLLM_BASE_URL", os.getenv("VLLM_API_BASE", "http://localhost:8000")) + "/v1")
    if base == ollama:
        return "ollama"
    if base == vllm:
        return "vllm"
    return None


def unwrap_llm(llm: Any) -> Any:
    """The chat model behind a binding (for the provider and model a direct caller used)."""
    return _unwrap(llm)
