# SPDX-License-Identifier: Apache-2.0
"""Context-window management: what is sent to a model fits the model, most relevant first.

An agent run accumulates tool results. Sent whole, a long run eventually
exceeds the model's context window and the provider refuses the call, or it
fits but most of the window is spent on results the model no longer needs.

Before a model call the conversation is measured against the model's window
(its catalogue size, less the room kept for the answer and a safety margin).
When it does not fit, tool results are omitted, least valuable first, until
it does:

* never omitted: system messages, what the user wrote, the model's own
  turns, and the newest round of tool results (the ones the model is about
  to read);
* older tool results are ranked by how much of the latest user message's
  wording they share and by how recent they are, and the lowest ranked go
  first;
* an omitted result is replaced by a short marker in place, so every tool
  call still has its answer and the provider accepts the conversation;
* if that is not enough, the largest remaining results are cut to their
  beginning.

Only the copy sent to the model changes. The run's own history keeps every
result, so later turns are measured afresh and the grounding check still sees
everything that was retrieved.

Token counts are estimates (characters over four, plus a small cost per
message): provider tokenisers differ and none is loaded here. The margin
exists because the estimate is rough. When the conversation still does not
fit, it is sent as it is and the provider's own limit applies, as before.

Behind ``AGENTICORG_CONTEXT_WINDOW_MANAGED`` (off by default).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
SAFETY_MARGIN = 0.10
DEFAULT_WINDOW = 32_000
DEFAULT_OUTPUT_RESERVE = 4_096
MAX_OUTPUT_RESERVE_SHARE = 0.25
OMITTED = "[An earlier tool result was omitted to fit the model's context window.]"
TRUNCATED = "\n[The rest of this tool result was cut to fit the model's context window.]"
MIN_TRUNCATED_CHARS = 2_000
RECENCY_WEIGHT = 0.5


def enabled() -> bool:
    return bool(settings.context_window_managed)


def estimate_tokens(text: Any) -> int:
    """A rough token count for ``text`` (a string, or the list form some providers use for content)."""
    if text is None:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def message_tokens(message: Any) -> int:
    calls = getattr(message, "tool_calls", None)
    return MESSAGE_OVERHEAD_TOKENS + estimate_tokens(getattr(message, "content", "")) + estimate_tokens(calls or "")


def total_tokens(messages: list[Any]) -> int:
    return sum(message_tokens(message) for message in messages)


def window_for(model: str | None) -> tuple[int, int]:
    """The model's context window and the room kept for its answer; a default for a model the catalogue lacks."""
    from core.ai_providers.catalog import LLM_CATALOG

    name = (model or "").strip()
    for entry in LLM_CATALOG:
        if entry.model == name:
            reserve = min(entry.max_output_tokens, int(entry.context_window * MAX_OUTPUT_RESERVE_SHARE))
            return entry.context_window, reserve
    return DEFAULT_WINDOW, DEFAULT_OUTPUT_RESERVE


def budget_for(model: str | None) -> int:
    """How many tokens of conversation the model is sent at most."""
    window, reserve = window_for(model)
    return max(1, int((window - reserve) * (1 - SAFETY_MARGIN)))


@dataclass
class Fit:
    messages: list[Any]
    budget: int
    before_tokens: int
    after_tokens: int
    omitted: int = 0
    truncated: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.omitted or self.truncated)

    @property
    def fits(self) -> bool:
        return self.after_tokens <= self.budget


def _with_content(message: Any, content: str) -> Any:
    copy = getattr(message, "model_copy", None)
    if callable(copy):
        return copy(update={"content": content})
    replacement = type(message).__new__(type(message))
    replacement.__dict__.update(message.__dict__)
    replacement.content = content
    return replacement


def _is_tool(message: Any) -> bool:
    return str(getattr(message, "type", "") or "") == "tool"


def _relevance(text: str, wanted: frozenset[str]) -> float:
    """The share of the user's content words that the tool result also carries."""
    if not wanted:
        return 0.0
    from core.governance.guardrails.grounding import content_tokens

    return len(wanted & set(content_tokens(text[:20_000]))) / len(wanted)


def fit(messages: list[Any], model: str | None, *, budget: int | None = None) -> Fit:
    """The conversation as it is sent to ``model``: unchanged when it fits, otherwise with tool results omitted."""
    limit = budget_for(model) if budget is None else budget
    before = total_tokens(messages)
    if before <= limit:
        return Fit(messages=messages, budget=limit, before_tokens=before, after_tokens=before)

    from core.governance.guardrails.grounding import content_tokens

    out = list(messages)
    # The newest round: the tool results after the last turn that is not a tool result.
    last_other = max((index for index, message in enumerate(out) if not _is_tool(message)), default=-1)
    older = [index for index, message in enumerate(out) if _is_tool(message) and index < last_other]
    question = next(
        (
            str(getattr(message, "content", "") or "")
            for message in reversed(out)
            if str(getattr(message, "type", "") or "") == "human"
        ),
        "",
    )
    wanted = frozenset(content_tokens(question))
    span = max(len(out) - 1, 1)
    ranked = sorted(
        older,
        key=lambda index: (
            _relevance(str(getattr(out[index], "content", "") or ""), wanted) + RECENCY_WEIGHT * (index / span),
            index,
        ),
    )
    total = before
    omitted = 0
    for index in ranked:
        if total <= limit:
            break
        was = message_tokens(out[index])
        out[index] = _with_content(out[index], OMITTED)
        total += message_tokens(out[index]) - was
        omitted += 1

    truncated = 0
    if total > limit:
        # Still too large: cut the largest remaining tool results (the newest round included) to their beginning.
        remaining = sorted(
            (index for index, message in enumerate(out) if _is_tool(message) and message.content != OMITTED),
            key=lambda index: -len(str(out[index].content or "")),
        )
        for index in remaining:
            if total <= limit:
                break
            content = str(out[index].content or "")
            excess_chars = (total - limit) * CHARS_PER_TOKEN
            keep = max(MIN_TRUNCATED_CHARS, len(content) - excess_chars - len(TRUNCATED))
            if keep >= len(content):
                continue
            was = message_tokens(out[index])
            out[index] = _with_content(out[index], content[:keep] + TRUNCATED)
            total += message_tokens(out[index]) - was
            truncated += 1

    result = Fit(
        messages=out, budget=limit, before_tokens=before, after_tokens=total, omitted=omitted, truncated=truncated
    )
    logger.info(
        "context_window_fitted",
        model=model,
        budget=limit,
        before_tokens=before,
        after_tokens=total,
        omitted=omitted,
        truncated=truncated,
        fits=result.fits,
    )
    try:
        from observability.metrics import context_window_trims_total

        context_window_trims_total.labels(result="fitted" if result.fits else "still_over").inc()
    # enterprise-gate: broad-except-ok reason=metering-is-best-effort-and-never-fails-a-model-call-safe-to-skip
    except Exception:  # noqa: S110
        pass
    return result


def fit_for_call(messages: list[Any], model: str | None) -> Fit | None:
    """``fit`` when context-window management is on; None when it is off (the caller sends what it had)."""
    if not enabled():
        return None
    return fit(messages, model)
