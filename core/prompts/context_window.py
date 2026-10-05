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

The budget is the window of the model that is actually called: the model is
read through a tool binding when the run did not name one, the catalogue is
searched by provider so a self-hosted or deployment-named model takes its
provider's entry, and the definitions of the tools bound to the call are
counted against the window, since the provider is sent them too. A model
whose window cannot be established is not managed at all: trimming to a
guessed window would drop evidence a larger window could have held.

Behind ``AGENTICORG_CONTEXT_WINDOW_MANAGED`` (off by default).
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
SAFETY_MARGIN = 0.10
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


def unwrap(llm: Any) -> Any:
    """The chat model under a tool binding (or the object itself when it is not wrapped)."""
    seen = 0
    while getattr(llm, "bound", None) is not None and seen < 5:
        llm = llm.bound
        seen += 1
    return llm


def model_name_of(llm: Any, declared: str | None = None) -> str:
    """The model a call goes to: the declared one, else the name the built model carries."""
    if declared:
        return declared
    base = unwrap(llm)
    for attr in ("model", "model_name"):
        value = getattr(base, attr, None)
        if isinstance(value, str) and value:
            # Some clients carry the name with a path prefix (``models/gemini-...``).
            return value.rsplit("/", 1)[-1]
    return ""


def window_for(model: str | None, provider: str | None = None) -> tuple[int, int] | None:
    """The model's context window and the room kept for its answer; None when the catalogue cannot say.

    With a provider the catalogue's own resolver is used, so a provider whose
    entry is a wildcard (a self-hosted endpoint) or a deployment name takes
    that entry. Without one the model is looked up by its exact name.
    """
    from core.ai_providers.catalog import LLM_CATALOG, find_llm

    name = (model or "").strip()
    entry = find_llm(provider, name) if provider else None
    if entry is None and name:
        entry = next((item for item in LLM_CATALOG if item.model == name), None)
    if entry is None:
        return None
    reserve = min(entry.max_output_tokens, int(entry.context_window * MAX_OUTPUT_RESERVE_SHARE))
    return entry.context_window, reserve


def tools_tokens(tools: Any) -> int:
    """A rough token count for the tool definitions sent with the call (name, description and arguments)."""
    total = 0
    for tool in tools or []:
        schema: Any = None
        args = getattr(tool, "args_schema", None)
        describe = getattr(args, "model_json_schema", None)
        if callable(describe):
            try:
                schema = describe()
            # enterprise-gate: broad-except-ok reason=an-undescribable-tool-schema-degrades-to-a-name-only-estimate
            except Exception:  # noqa: S110
                schema = None
        elif isinstance(args, dict):
            schema = args
        definition = {
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
            "parameters": schema or {},
        }
        total += MESSAGE_OVERHEAD_TOKENS + estimate_tokens(json.dumps(definition, default=str))
    return total


def budget_for(model: str | None, provider: str | None = None, *, tools: Any = None) -> int | None:
    """How many tokens of conversation the model is sent at most; None when its window is not known."""
    known = window_for(model, provider)
    if known is None:
        return None
    window, reserve = known
    return max(1, int((window - reserve) * (1 - SAFETY_MARGIN)) - tools_tokens(tools))


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
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"content": content})
    replacement = copy.copy(message)
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


def fit(messages: list[Any], model: str | None, *, budget: int) -> Fit:
    """The conversation as it is sent to ``model``: unchanged when it fits, otherwise with tool results omitted."""
    limit = budget
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


def fit_for_call(
    messages: list[Any],
    llm: Any,
    *,
    model: str | None = None,
    provider: str | None = None,
    tools: Any = None,
) -> Fit | None:
    """``fit`` against the called model's window; None when management is off or the window is not known.

    ``llm`` is the model as it is called (possibly bound to tools); ``model``
    and ``provider`` are what the run declared, when it declared them.
    """
    if not enabled():
        return None
    name = model_name_of(llm, model)
    budget = budget_for(name, provider, tools=tools)
    if budget is None:
        logger.info("context_window_unmanaged", model=name or None, provider=provider or None)
        return None
    return fit(messages, name, budget=budget)
