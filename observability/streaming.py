# SPDX-License-Identifier: Apache-2.0
"""Streaming latency: how long a model call takes to produce its first token.

A model call's total duration says how long the caller waited for the whole
answer; the time to the first token says how long before anything came back
at all, which is what a person in a conversation feels and what separates a
slow provider from a long answer. It can only be measured by reading the
answer as a stream.

Behind ``AGENTICORG_MODEL_STREAM_TIMING_ENABLED`` (off by default): on, the
reasoning node reads each model answer as a stream, notes when the first
chunk carrying content or a tool call arrives, and puts the chunks back
together into the same message a plain call returns. Off, the call is made
exactly as before and no first-token time is reported.

The measurement is a duration; it carries no content.
"""

from __future__ import annotations

import time
from typing import Any

from core.config import settings


def stream_timing_enabled() -> bool:
    return bool(settings.model_stream_timing_enabled)


def _carries_output(chunk: Any) -> bool:
    """Whether a chunk holds the start of an answer: text, or the beginning of a tool call."""
    if getattr(chunk, "content", None):
        return True
    return bool(getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None))


async def invoke_timed(llm: Any, messages: list[Any]) -> tuple[Any, int | None]:
    """Call the model and return its answer with the milliseconds to its first token (None when not measured).

    With stream timing off this is ``llm.ainvoke``. On, the answer is read
    with ``llm.astream`` and reassembled; a model that does not stream yields
    its whole answer as one chunk, so its first-token time is its duration.
    """
    if not stream_timing_enabled():
        return await llm.ainvoke(messages), None
    from langchain_core.messages import message_chunk_to_message

    started = time.monotonic()
    first_token_ms: int | None = None
    answer: Any = None
    async for chunk in llm.astream(messages):
        if first_token_ms is None and _carries_output(chunk):
            first_token_ms = int((time.monotonic() - started) * 1000)
        answer = chunk if answer is None else answer + chunk
    if answer is None:
        # A stream that produced nothing: make the plain call so the caller gets the provider's real answer or error.
        return await llm.ainvoke(messages), None
    return message_chunk_to_message(answer), first_token_ms


def observe_first_token(provider: str | None, model: str | None, first_token_ms: int | None) -> None:
    """Meter a measured first-token time; metering never changes a call's outcome."""
    if first_token_ms is None:
        return
    try:
        from observability.metrics import model_first_token_seconds

        model_first_token_seconds.labels(provider=provider or "unknown", model=model or "unknown").observe(
            max(0, first_token_ms) / 1000.0
        )
    # enterprise-gate: broad-except-ok reason=metering-is-best-effort-and-never-fails-a-model-call-safe-to-skip
    except Exception:  # noqa: S110
        pass


# ---------------------------------------------------------------------------
# Task queue wait
# ---------------------------------------------------------------------------

ENQUEUED_AT_HEADER = "x-agenticorg-enqueued-at"
# A wait longer than this is a clock problem or a task held back on purpose, not queueing.
MAX_QUEUE_WAIT_SECONDS = 24 * 60 * 60.0


def stamp_enqueued(headers: dict[str, Any], *, now: float | None = None) -> None:
    """Record when a task was published, unless the publisher asked for a later start (an eta or a countdown)."""
    if headers.get("eta") or headers.get(ENQUEUED_AT_HEADER):
        return
    headers[ENQUEUED_AT_HEADER] = repr(time.time() if now is None else now)


def queue_wait_seconds(task: Any, *, now: float | None = None) -> float | None:
    """How long the task waited between publish and start; None when it was not stamped or was scheduled."""
    request = getattr(task, "request", None)
    if request is None or getattr(request, "eta", None):
        return None
    stamped = None
    for source in (request, getattr(request, "headers", None)):
        get = getattr(source, "get", None)
        if callable(get):
            stamped = get(ENQUEUED_AT_HEADER)
            if stamped:
                break
    if not stamped:
        return None
    try:
        waited = (time.time() if now is None else now) - float(stamped)
    except (TypeError, ValueError):
        return None
    if waited < 0 or waited > MAX_QUEUE_WAIT_SECONDS:
        return None
    return waited


def _queue_of(task: Any) -> str:
    info = getattr(getattr(task, "request", None), "delivery_info", None) or {}
    return str(info.get("routing_key") or info.get("exchange") or "default")


def observe_queue_wait(task: Any, *, now: float | None = None) -> float | None:
    """Meter the task's queue wait by queue; returns the wait for the caller's span."""
    waited = queue_wait_seconds(task, now=now)
    if waited is None:
        return None
    try:
        from observability.metrics import task_queue_wait_seconds

        task_queue_wait_seconds.labels(queue=_queue_of(task)).observe(waited)
    # enterprise-gate: broad-except-ok reason=metering-is-best-effort-and-never-fails-a-task-safe-to-skip
    except Exception:  # noqa: S110
        pass
    return waited
