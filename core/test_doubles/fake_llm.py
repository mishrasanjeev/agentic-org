"""Deterministic fake LLM for hermetic CI (Foundation #7 PR-A).

Purpose
-------

Real LLM calls in CI are expensive, slow, and non-deterministic.
Tests that need an LLM either:

1. Skip when no API key is present — invisible coverage gap.
2. Use ad-hoc ``MagicMock`` patches — duplicated in 10+ files
   with subtly different return shapes.
3. Hit the real API and rely on a CI secret — the explicit
   pattern the closure plan forbids.

This module gives every test (and prod code under
``AGENTICORG_TEST_FAKE_LLM=1``) a single, deterministic LLM
substitute. Same prompt fingerprint always yields the same
response. Tests can register custom responses for prompts they
care about and rely on the default for everything else.

Activation
----------

Set ``AGENTICORG_TEST_FAKE_LLM=1`` in the environment. The
production ``LLMRouter._call_model`` checks this flag before
dispatching to the real provider and short-circuits to
``fake_complete()`` if set.

Determinism
-----------

The fingerprint is ``sha256(model + json(messages))`` — same
input always returns the same output. Tokens-used and cost are
also deterministic functions of the input length so cost-cap
tests are reproducible.

Custom responses
----------------

::

    from core.test_doubles import fake_llm

    fake_llm.register_response(
        prompt_contains="extract entities",
        content='{"entities": ["Alice", "Bob"]}',
    )

The registered response wins over the default for any messages
whose joined content matches ``prompt_contains`` (case-insensitive
substring).

Tests can ``fake_llm.reset()`` between cases to clear registered
responses + the call log.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeCall:
    """One recorded call to the fake LLM."""

    model: str
    messages: list[dict[str, str]]
    temperature: float
    max_tokens: int
    response_content: str
    fingerprint: str
    timestamp: float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────
# Module-level state — intentional. The seam is global on purpose:
# every code path that calls the real LLM gets the fake when the
# flag is set, without needing to thread an extra dependency
# through every layer.
# ─────────────────────────────────────────────────────────────────

_REGISTERED: list[tuple[str, str]] = []
_CALL_LOG: list[FakeCall] = []


def is_active() -> bool:
    """True iff ``AGENTICORG_TEST_FAKE_LLM`` is set to a truthy value."""
    return os.getenv("AGENTICORG_TEST_FAKE_LLM", "").lower() in (
        "1",
        "true",
        "yes",
    )


def register_response(*, prompt_contains: str, content: str) -> None:
    """Register a custom response for any prompt whose joined
    content contains ``prompt_contains`` (case-insensitive).

    Last-registered wins on conflicts. Use ``reset()`` between
    tests to avoid bleed-over.
    """
    if not prompt_contains.strip():
        raise ValueError("prompt_contains must be non-empty")
    _REGISTERED.append((prompt_contains.lower(), content))


def reset() -> None:
    """Clear all registered responses + the call log. Call from
    pytest fixtures to keep tests isolated."""
    _REGISTERED.clear()
    _CALL_LOG.clear()


def call_log() -> list[FakeCall]:
    """Return a copy of the recorded calls, oldest first."""
    return list(_CALL_LOG)


def call_count() -> int:
    """Number of fake calls since the last ``reset()``."""
    return len(_CALL_LOG)


# ─────────────────────────────────────────────────────────────────
# Fingerprinting + default response
# ─────────────────────────────────────────────────────────────────


def _fingerprint(model: str, messages: list[dict[str, str]]) -> str:
    payload = json.dumps([model, messages], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _joined_content(messages: list[dict[str, str]]) -> str:
    return " ".join(m.get("content", "") for m in messages).lower()


def _default_response(model: str, joined: str) -> str:
    """Generate a marker response that's recognizable in logs but
    looks plausibly LLM-shaped to downstream JSON parsers."""
    if "json" in joined or '"' in joined:
        return '{"fake": true, "model": "' + model + '"}'
    return f"[FAKE_LLM:{model}] deterministic response"


# ─────────────────────────────────────────────────────────────────
# The seam — called by LLMRouter._call_model when is_active()
# ─────────────────────────────────────────────────────────────────


def fake_complete(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Return a deterministic response shaped like the production
    ``LLMResponse`` constructor expects. The router builds the
    real dataclass from this dict.

    Token + cost numbers are deterministic functions of the input
    so cost-cap tests are reproducible without depending on real
    pricing tables.
    """
    joined = _joined_content(messages)
    content = _default_response(model, joined)
    for marker, override in _REGISTERED:
        if marker in joined:
            content = override
            break

    fingerprint = _fingerprint(model, messages)
    in_tokens = max(1, sum(len(m.get("content", "")) for m in messages) // 4)
    out_tokens = max(1, len(content) // 4)

    _CALL_LOG.append(
        FakeCall(
            model=model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            response_content=content,
            fingerprint=fingerprint,
        )
    )

    return {
        "content": content,
        "model": model,
        "tokens_used": in_tokens + out_tokens,
        # $0 cost — fake calls must never trip the cap.
        "cost_usd": 0.0,
        "latency_ms": 0,
        "raw": {
            "fake": True,
            "fingerprint": fingerprint,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
        },
    }
