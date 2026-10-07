# SPDX-License-Identifier: Apache-2.0
"""Graceful fallbacks: what the user is told when an answer cannot be given, and when to offer a person.

A fallback names what happened in plain words (the systems did not answer in
time, no agent could answer, the answer was not confident enough), says that
nothing was changed, and offers the next step. After ``ESCALATE_AFTER``
fallbacks in a row the offer is to connect to a person.
"""

from __future__ import annotations

from typing import Any

ESCALATE_AFTER = 2
LOW_CONFIDENCE = 0.5

KIND_TIMEOUT = "timeout"
KIND_UNAVAILABLE = "unavailable"
KIND_NO_ANSWER = "no_answer"
KIND_LOW_CONFIDENCE = "low_confidence"
KIND_TOOL_FAILURE = "tool_failure"
KIND_REFUSED = "refused"

_MESSAGES = {
    KIND_TIMEOUT: (
        "The banking systems did not answer in time, so I have not done anything. You can try again in a moment."
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
