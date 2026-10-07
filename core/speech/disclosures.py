# SPDX-License-Identifier: Apache-2.0
"""Disclosure scripts: what an agent must say on a call, whether it was said, and when it became overdue.

A disclosure is a short script the bank requires on certain calls: that
the line is recorded, that the customer's identity was verified, the
product's rate and fees, the cooling-off period, how to complain, consent
to proceed. Each carries the phrases that count as having said it, the
calls it applies to, and for some a deadline from the start of the call.
The checker reads the agent's turns and says, for each required
disclosure, where it was said, that it is missing, or that it was said
late; the live checker raises an overdue flag the moment the deadline
passes without it, so the agent is told during the call, not after.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Disclosure:
    key: str
    title: str
    script: str  # what the agent is expected to say, in the bank's words
    patterns: tuple[str, ...]  # any one of these counts as said
    applies_to: tuple[str, ...] = ()  # call types; empty: every call
    deadline_seconds: float | None = None  # said within this many seconds of the start, or late

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "script": self.script,
            "applies_to": list(self.applies_to),
            "deadline_seconds": self.deadline_seconds,
        }


CATALOGUE: tuple[Disclosure, ...] = (
    Disclosure(
        "recorded_line",
        "Recorded line",
        "This call is being recorded for quality and training purposes.",
        (r"\b(call|line|conversation) (is|may be|will be) (being )?(recorded|monitored)", r"\brecorded (line|call)\b"),
        deadline_seconds=60.0,
    ),
    Disclosure(
        "identity_verification",
        "Identity verified",
        "Before we proceed I need to verify your identity.",
        (
            r"\bverif(y|ied|ication)\b.*\b(identity|details|yourself|you are)\b",
            r"\bsecurity (question|check)s?\b",
            r"\bconfirm (your|the) (date of birth|registered mobile|pan)\b",
        ),
        deadline_seconds=120.0,
    ),
    Disclosure(
        "product_terms",
        "Rate and fees",
        "The interest rate is X per cent per annum and the processing fee is Y.",
        (
            r"\b(interest|rate of interest)\b.*\b(per ?cent|%|per annum)\b",
            r"\b(processing|annual|late payment) (fee|charge)s?\b",
        ),
        applies_to=("loan", "card", "sales"),
    ),
    Disclosure(
        "cooling_off",
        "Cooling-off period",
        "You may cancel within the cooling-off period without penalty.",
        (r"\bcooling[- ]off\b", r"\bfree[- ]look (period)?\b", r"\bcancel within \d+ days\b"),
        applies_to=("loan", "card", "insurance", "sales"),
    ),
    Disclosure(
        "complaint_channel",
        "How to complain",
        "If you are not satisfied you may raise a complaint, and escalate to the banking ombudsman.",
        (r"\b(raise|lodge|register) a complaint\b", r"\bgrievance\b", r"\bombudsman\b"),
        applies_to=("complaint", "collections"),
    ),
    Disclosure(
        "consent_to_proceed",
        "Consent to proceed",
        "Do I have your consent to proceed?",
        (
            r"\b(consent|permission|agree) to proceed\b",
            r"\bdo (i|we) have your (consent|permission)\b",
            r"\bare you happy (for me|for us) to proceed\b",
        ),
        applies_to=("loan", "card", "sales", "collections"),
    ),
    Disclosure(
        "collections_rights",
        "Collections conduct",
        "You have the right to a written notice and to dispute the amount.",
        (r"\bright to (dispute|a written notice|request)\b", r"\bfair practices?\b", r"\bdispute (the|this) amount\b"),
        applies_to=("collections",),
    ),
)
DISCLOSURES: dict[str, Disclosure] = {item.key: item for item in CATALOGUE}
CALL_TYPES: tuple[str, ...] = ("service", "loan", "card", "insurance", "sales", "complaint", "collections")
DEFAULT_REQUIRED: tuple[str, ...] = ("recorded_line", "identity_verification")


def catalogue() -> list[dict[str, Any]]:
    return [item.to_dict() for item in CATALOGUE]


def required_for(call_type: str, required: list[str] | tuple[str, ...]) -> list[Disclosure]:
    """The disclosures a call needs: those the tenant requires that apply to the call's type."""
    kind = (call_type or "service").lower()
    out = []
    for key in required:
        item = DISCLOSURES.get(key)
        if item is None:
            continue
        if item.applies_to and kind not in item.applies_to:
            continue
        out.append(item)
    return out


def said_in(item: Disclosure, text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in item.patterns)


def check(
    turns: list[dict[str, Any]], *, required: list[Disclosure], agent: str | None = None, start: float = 0.0
) -> dict[str, Any]:
    """For each required disclosure: said (where), late (said after its deadline) or missing."""
    found: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    late: list[dict[str, Any]] = []
    for item in required:
        hit = None
        for index, turn in enumerate(turns):
            if agent is not None and turn.get("speaker") not in (agent, None):
                continue
            if said_in(item, str(turn.get("text") or "")):
                hit = (index, float(turn.get("start") or 0.0))
                break
        if hit is None:
            missing.append({"key": item.key, "title": item.title, "script": item.script})
            continue
        entry = {"key": item.key, "title": item.title, "turn": hit[0], "at": round(hit[1], 3)}
        if item.deadline_seconds is not None and hit[1] - start > item.deadline_seconds:
            late.append({**entry, "deadline_seconds": item.deadline_seconds})
        found.append(entry)
    return {
        "required": [item.key for item in required],
        "found": found,
        "missing": missing,
        "late": late,
        "compliant": not missing and not late,
    }


def overdue(required: list[Disclosure], status: dict[str, Any], elapsed: float) -> list[dict[str, Any]]:
    """The required disclosures with a deadline that has passed and that are still missing."""
    missing = {m["key"] for m in status.get("missing", [])}
    return [
        {"key": item.key, "title": item.title, "script": item.script, "deadline_seconds": item.deadline_seconds}
        for item in required
        if item.key in missing and item.deadline_seconds is not None and elapsed > item.deadline_seconds
    ]
