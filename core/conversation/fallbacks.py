# SPDX-License-Identifier: Apache-2.0
"""Graceful fallbacks: what the user is told when an answer cannot be given, and when to offer a person.

A fallback names what happened in plain words (the systems did not answer in
time, no agent could answer, the answer was not confident enough), says what
is known about the outcome, and offers the next step. A timeout does not say
that nothing changed: the run may have stopped after a tool had already acted,
so the outcome is unknown and the user is asked to check before retrying.
After ``ESCALATE_AFTER`` fallbacks in a row the offer is to connect to a
person; the streak is read back from the session history, where each fallback
answer is marked with ``FALLBACK_KEY``.
"""

from __future__ import annotations

from typing import Any

ESCALATE_AFTER = 2
LOW_CONFIDENCE = 0.5
FALLBACK_KEY = "fallback"  # marks a fallback answer in the session history

KIND_TIMEOUT = "timeout"
KIND_UNAVAILABLE = "unavailable"
KIND_NO_ANSWER = "no_answer"
KIND_LOW_CONFIDENCE = "low_confidence"
KIND_TOOL_FAILURE = "tool_failure"
KIND_REFUSED = "refused"

_MESSAGES = {
    KIND_TIMEOUT: (
        "The banking systems did not answer in time, so I cannot tell whether the request went through. "
        "Please check your recent transactions before trying again, so that nothing is done twice."
    ),
    KIND_UNAVAILABLE: (
        "I cannot reach the banking systems right now, so I have not done anything. Please try again shortly."
    ),
    KIND_NO_ANSWER: "I could not find an answer to that. Nothing has been changed.",
    KIND_LOW_CONFIDENCE: "I am not confident enough in that answer to give it, so I have held it back.",
    KIND_TOOL_FAILURE: "The action did not go through, so nothing has been changed.",
    KIND_REFUSED: "That is not something I am allowed to do from here, so nothing has been done.",
}
_NEXT_STEPS = (
    "I can help with balances, statements, card blocking, transfers, bill payments, loans, "
    "transaction disputes and application status, or you can ask to talk to a person."
)
_ESCALATE = "Would you like me to connect you to a person now? Reply yes and I will hand this over with a summary."


def classify(
    result: dict[str, Any] | None, *, answer: str | None = None, confidence: float | None = None
) -> str | None:
    """The fallback kind a run result calls for, or None when the answer stands."""
    result = result or {}
    error = str(result.get("error") or "").lower()
    status = str(result.get("status") or "")
    if "timeout" in error or "timed out" in error:
        return KIND_TIMEOUT
    if "unavailable" in error or "connect" in error and "fail" in error:
        return KIND_UNAVAILABLE
    if status in ("refused", "blocked", "guardrail_blocked", "operator_override"):
        return KIND_REFUSED
    if status == "failed" and ("tool" in error or result.get("tool_calls")):
        return KIND_TOOL_FAILURE
    if status == "failed" or not answer:
        return KIND_NO_ANSWER
    if confidence is not None and confidence < LOW_CONFIDENCE and status != "hitl_triggered":
        return KIND_LOW_CONFIDENCE
    return None


def message(kind: str, *, consecutive: int = 1) -> str:
    """What the user is told for a fallback kind, offering a person after repeated fallbacks."""
    text = _MESSAGES.get(kind, _MESSAGES[KIND_NO_ANSWER])
    if consecutive >= ESCALATE_AFTER:
        return f"{text} {_ESCALATE}"
    return f"{text} {_NEXT_STEPS}"


def offers_person(consecutive: int) -> bool:
    return consecutive >= ESCALATE_AFTER


def hold_back(
    result: dict[str, Any] | None,
    *,
    answer: str | None,
    confidence: float | None,
    hitl: bool = False,
) -> str | None:
    """The fallback kind a run calls for before its answer is accepted, or None when the answer stands.

    Every answer is classified with its computed confidence, so a non-empty
    answer below ``LOW_CONFIDENCE`` is held back. An answer that reports a
    human-review hand-off stands as it is.
    """
    if answer and hitl:
        return None
    return classify(result, answer=answer, confidence=confidence)


def streak(entries: list[dict[str, Any]] | None) -> int:
    """How many fallback answers in a row end a session history (user turns are skipped)."""
    count = 0
    for entry in reversed(entries or []):
        if not isinstance(entry, dict) or entry.get("role") == "user":
            continue
        if not entry.get(FALLBACK_KEY):
            break
        count += 1
    return count
