# SPDX-License-Identifier: Apache-2.0
"""Per-agent execution limits and loop detection.

An agent may carry limits in its configuration (``config["limits"]``): the
most model steps a run may take, the longest it may run, the most tool calls
it may make, and the loop rule (how many identical tool calls in a row, and
how long a repeating pattern of tool calls, count as a loop). While
``AGENTICORG_RUNTIME_LIMITS_ENABLED`` is on the graph checks the step limit
before every model call (``check_steps``), checks them all before every round
of tool execution (``check``) and the runner applies the duration as the run's
timeout: a run that exceeds a limit, or repeats a tool call
pattern, is stopped with ``status`` ``failed``, ``error`` ``stopped: ...`` and
a ``limit`` block naming the reason and the detail, which the agents API
audits and meters. Every limit is bounded by the platform's own maxima
(``AGENTICORG_MAX_AGENT_STEPS``, ``AGENTICORG_MAX_AGENT_DURATION_SEC``), which
bound every run whether or not the switch is on; with the switch off a run
that reaches them fails as it always has.

Loop detection reads the tool calls the model has asked for so far, as a
sequence of signatures (the tool name and a hash of its arguments): the same
signature ``max_repeats`` times in a row, or a pattern of up to
``loop_window`` calls repeated twice back to back, is a loop. Off, an agent's
own limits are kept and shown but the platform maxima alone apply.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

LIMITS_KEY = "limits"
PLATFORM_MAX_STEPS = int(os.getenv("AGENTICORG_MAX_AGENT_STEPS", "200"))
PLATFORM_MAX_DURATION_SECONDS = int(os.getenv("AGENTICORG_MAX_AGENT_DURATION_SEC", "1800"))
PLATFORM_MAX_TOOL_CALLS = 500
DEFAULT_MAX_REPEATS = 3
DEFAULT_LOOP_WINDOW = 4
MAX_REPEATS_RANGE = (2, 20)
LOOP_WINDOW_RANGE = (2, 10)
FIELDS: tuple[str, ...] = ("max_steps", "max_duration_seconds", "max_tool_calls", "max_repeats", "loop_window")
REASONS: tuple[str, ...] = ("step_limit", "duration_limit", "tool_call_limit", "loop_detected")


class LimitError(ValueError):
    """The limits cannot be used."""


def enabled() -> bool:
    return bool(settings.runtime_limits_enabled)


@dataclass(frozen=True)
class Limits:
    max_steps: int
    max_duration_seconds: int
    max_tool_calls: int
    max_repeats: int = DEFAULT_MAX_REPEATS
    loop_window: int = DEFAULT_LOOP_WINDOW

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Stop:
    reason: str
    detail: str
    steps: int = 0
    tool_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def platform() -> Limits:
    """The platform's maxima: what applies when an agent declares nothing."""
    return Limits(
        max_steps=PLATFORM_MAX_STEPS,
        max_duration_seconds=PLATFORM_MAX_DURATION_SECONDS,
        max_tool_calls=PLATFORM_MAX_TOOL_CALLS,
    )


def _int_in(raw: dict[str, Any], key: str, low: int, high: int) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise LimitError(f"{key} is a whole number")
    if not low <= value <= high:
        raise LimitError(f"{key} is between {low} and {high}")
    return value


def parse_limits(raw: Any) -> dict[str, Any]:
    """An agent's limits as they are stored: whole numbers within the platform's bounds, unknown keys refused."""
    if not isinstance(raw, dict):
        raise LimitError("the limits must be an object")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise LimitError(f"unknown limit fields: {', '.join(unknown)}")
    parsed: dict[str, Any] = {}
    for key, low, high in (
        ("max_steps", 1, PLATFORM_MAX_STEPS),
        ("max_duration_seconds", 1, PLATFORM_MAX_DURATION_SECONDS),
        ("max_tool_calls", 1, PLATFORM_MAX_TOOL_CALLS),
        ("max_repeats", *MAX_REPEATS_RANGE),
        ("loop_window", *LOOP_WINDOW_RANGE),
    ):
        value = _int_in(raw, key, low, high)
        if value is not None:
            parsed[key] = value
    if not parsed:
        raise LimitError("the limits name at least one of " + ", ".join(FIELDS))
    return parsed


def declared(source: Any) -> dict[str, Any] | None:
    """The limits an agent (or its configuration dict) declares, or None."""
    config = source if isinstance(source, dict) else (getattr(source, "config", None) or {})
    raw = config.get(LIMITS_KEY) if isinstance(config, dict) else None
    return dict(raw) if isinstance(raw, dict) and raw else None


def effective(raw: Any = None) -> Limits:
    """The limits a run is held to: the agent's own, bounded by the platform's, while the switch is on."""
    base = platform()
    if not enabled() or not isinstance(raw, dict) or not raw:
        return base
    return Limits(
        max_steps=min(int(raw.get("max_steps") or base.max_steps), base.max_steps),
        max_duration_seconds=min(
            int(raw.get("max_duration_seconds") or base.max_duration_seconds), base.max_duration_seconds
        ),
        max_tool_calls=min(int(raw.get("max_tool_calls") or base.max_tool_calls), base.max_tool_calls),
        max_repeats=int(raw.get("max_repeats") or DEFAULT_MAX_REPEATS),
        loop_window=int(raw.get("loop_window") or DEFAULT_LOOP_WINDOW),
    )


def signature(name: Any, args: Any) -> str:
    """A tool call as the loop rule sees it: the tool name and a hash of its arguments, never the arguments."""
    try:
        canonical = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(args)
    return f"{str(name or '')}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def tool_signatures(messages: Any) -> list[str]:
    """The signatures of every tool call the model has asked for, in order."""
    out: list[str] = []
    for message in messages or []:
        calls = getattr(message, "tool_calls", None)
        if not calls or type(message).__name__ != "AIMessage":
            continue
        for call in calls:
            if isinstance(call, dict):
                out.append(signature(call.get("name"), call.get("args")))
            else:
                out.append(signature(getattr(call, "name", None), getattr(call, "args", None)))
    return out


def model_steps(messages: Any) -> int:
    """How many times the model has answered so far."""
    return sum(1 for message in messages or [] if type(message).__name__ == "AIMessage")


def detect_loop(
    signatures: list[str], *, max_repeats: int = DEFAULT_MAX_REPEATS, window: int = DEFAULT_LOOP_WINDOW
) -> str | None:
    """The loop in a sequence of tool call signatures, in words, or None."""
    n = len(signatures)
    if n >= max_repeats and len(set(signatures[-max_repeats:])) == 1:
        name = signatures[-1].split(":", 1)[0]
        return f"the tool call {name} was repeated {max_repeats} times in a row with the same arguments"
    for length in range(2, min(window, n // 2) + 1):
        if signatures[-length:] == signatures[-2 * length : -length]:
            names = ", ".join(s.split(":", 1)[0] for s in signatures[-length:])
            return f"a pattern of {length} tool calls ({names}) was repeated"
    return None


def check_steps(messages: Any, limits: Limits) -> Stop | None:
    """What stops the run before its next model call: the model has already answered max_steps times."""
    steps = model_steps(messages)
    if steps >= limits.max_steps:
        return Stop(
            "step_limit",
            f"the agent reached its limit of {limits.max_steps} model steps",
            steps,
            len(tool_signatures(messages)),
        )
    return None


def check(messages: Any, limits: Limits) -> Stop | None:
    """What stops the run before its next round of tools, if anything.

    The tool calls counted include the round about to run, so the limit stops a
    run only when the model has asked for more than ``max_tool_calls``.
    """
    steps = model_steps(messages)
    signatures = tool_signatures(messages)
    if steps >= limits.max_steps:
        return Stop(
            "step_limit", f"the agent reached its limit of {limits.max_steps} model steps", steps, len(signatures)
        )
    if len(signatures) > limits.max_tool_calls:
        return Stop(
            "tool_call_limit",
            f"the agent asked for more than its limit of {limits.max_tool_calls} tool calls",
            steps,
            len(signatures),
        )
    loop = detect_loop(signatures, max_repeats=limits.max_repeats, window=limits.loop_window)
    if loop:
        return Stop("loop_detected", loop, steps, len(signatures))
    return None


def stop_update(stop: Stop, trace: list[str]) -> dict[str, Any]:
    """The graph state update that stops a run: failed, with the reason in the error and the limit block."""
    logger.warning("agent_run_stopped_by_limit", reason=stop.reason, steps=stop.steps, tool_calls=stop.tool_calls)
    return {
        "status": "failed",
        "error": f"stopped: {stop.detail}",
        "limit_stop": stop.to_dict(),
        "reasoning_trace": [*trace, f"STOPPED ({stop.reason}): {stop.detail}"],
    }


def meter(reason: str) -> None:
    from observability import metrics as prom

    prom.agent_runs_stopped_total.labels(reason=reason if reason in REASONS else "other").inc()
