# SPDX-License-Identifier: Apache-2.0
"""Feedback, sentiment and satisfaction: what the user says about the conversation, and what it says about them.

Sentiment is a small lexicon over each user turn (no model): a score between
-1 and 1 and a label. Two negative turns in a row are an escalation trigger
(the dialogue offers a person). A rating from 1 to 5, asked once per
conversation after an action or a hand-off, or sent from the interface, is
kept on the session and, when an agent owns the conversation, stored with the
agent's feedback (``core/feedback/collector.py``) as thumbs up or down with the
rating in its context.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

STAGE_RATING = "rating"
NEGATIVE_STREAK = 2
RATING_PROMPT = "How did I do today? Reply with a rating from 1 to 5."
SOURCE = "conversation"

_NEGATIVE = {
    "useless": -1.0,
    "terrible": -1.0,
    "worst": -1.0,
    "horrible": -1.0,
    "angry": -0.9,
    "furious": -1.0,
    "frustrated": -0.8,
    "frustrating": -0.8,
    "annoyed": -0.7,
    "annoying": -0.7,
    "ridiculous": -0.8,
    "pathetic": -0.9,
    "waste": -0.7,
    "disappointed": -0.7,
    "disappointing": -0.7,
    "bad": -0.6,
    "wrong": -0.5,
    "not working": -0.7,
    "doesn't work": -0.7,
    "does not work": -0.7,
    "not helpful": -0.8,
    "unhelpful": -0.8,
    "complaint": -0.4,
    "fed up": -0.9,
    "hate": -0.9,
    "stupid": -0.8,
    "nonsense": -0.7,
}
_POSITIVE = {
    "thanks": 0.6,
    "thank you": 0.7,
    "great": 0.7,
    "excellent": 0.9,
    "perfect": 0.9,
    "helpful": 0.7,
    "good": 0.5,
    "awesome": 0.8,
    "brilliant": 0.8,
    "wonderful": 0.8,
    "resolved": 0.6,
    "sorted": 0.6,
    "appreciate": 0.7,
    "well done": 0.8,
    "nice": 0.5,
    "love": 0.7,
}
_NEGATION_RE = re.compile(r"\b(not|never|no|isn'?t|wasn'?t|don'?t|didn'?t|doesn'?t)\s+(\w+\s+){0,2}$")
_RATING_RE = re.compile(r"(?:rating\s*+[:=]?\s*+)?([1-5])\s*+(?:/\s*+5|out of 5|stars?)?\s*+[.!]?", re.I)
_THUMBS = {
    5: ("thumbs up", "👍", "excellent", "very helpful", "perfect"),
    4: ("good", "helpful", "well done", "great"),
    2: ("not helpful", "poor", "bad"),
    1: ("thumbs down", "👎", "terrible", "useless", "awful"),
}


def _phrase_re(phrase: str) -> re.Pattern[str]:
    """A whole-word match for ``phrase``: ``helpful`` does not match inside ``unhelpful``."""
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)")


def _longest_first(entries: dict[str, Any]) -> tuple[tuple[re.Pattern[str], Any], ...]:
    return tuple((_phrase_re(phrase), payload) for phrase, payload in sorted(entries.items(), key=lambda e: -len(e[0])))


_LEXICON_RES = _longest_first({**_NEGATIVE, **_POSITIVE})
_THUMBS_RES = _longest_first({phrase: value for value, phrases in _THUMBS.items() for phrase in phrases})


def _matches(lowered: str, patterns: tuple[tuple[re.Pattern[str], Any], ...]) -> list[tuple[int, int, Any]]:
    """Whole-word matches, longest phrase first; a shorter phrase inside a longer match is not counted again."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, int, Any]] = []
    for pattern, payload in patterns:
        for match in pattern.finditer(lowered):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            taken.append((start, end))
            found.append((start, end, payload))
    return found


def _negated(lowered: str, start: int) -> bool:
    return bool(_NEGATION_RE.search(lowered[max(0, start - 24) : start]))


def sentiment(text: str) -> dict[str, Any]:
    """The score (-1 to 1) and label of a user turn, from the lexicon with light negation."""
    lowered = text.lower()
    score = 0.0
    hits = 0
    for start, _end, weight in _matches(lowered, _LEXICON_RES):
        value = float(weight)
        if _negated(lowered, start):
            value = -value * 0.6
        score += value
        hits += 1
    if hits == 0:
        return {"score": 0.0, "label": "neutral"}
    score = max(-1.0, min(1.0, score / max(1, hits) * (1 + 0.15 * (hits - 1))))
    label = "negative" if score <= -0.35 else "positive" if score >= 0.35 else "neutral"
    return {"score": round(score, 2), "label": label}


def rating_from_text(text: str) -> int | None:
    """A rating the user typed: 1 to 5, "4/5", thumbs or a few plain words; None when it is not one."""
    match = _RATING_RE.fullmatch(text.strip())
    if match:
        return int(match.group(1))
    lowered = text.lower().strip(" .!")
    if len(lowered) > 40:
        return None
    for value, phrases in _THUMBS.items():
        if lowered in phrases:
            return value
    found = _matches(lowered, _THUMBS_RES)
    if not found:
        return None
    start, _end, value = max(found, key=lambda hit: (hit[1] - hit[0], -hit[0]))
    if _negated(lowered, start):
        # "not very good" is a poor rating; "not bad" or "not terrible" is a middling one.
        return 2 if value >= 3 else 3
    return int(value)


def latest_label(scores: list[dict[str, Any]] | None) -> str | None:
    if not scores:
        return None
    last = scores[-1]
    return str(last.get("label")) if isinstance(last, dict) else None


def rating_event_id(session_key: str, rating: int, comment: str = "") -> str:
    """The stable event key of one rating on one session: a retried request stores one feedback row, not two."""
    digest = hashlib.sha256(f"{session_key}\x00{int(rating)}\x00{comment}".encode()).hexdigest()
    return f"{SOURCE}:rating:{digest}"


async def record_rating(
    tenant_id: uuid.UUID,
    *,
    session_key: str,
    agent_id: str | None,
    user_id: str,
    rating: int,
    comment: str = "",
    channel: str = "web",
    intent: str | None = None,
    sentiment_label: str | None = None,
) -> dict[str, Any]:
    """Keep a rating with the agent's feedback when an agent owns the conversation; say what was stored."""
    record = {
        "rating": int(rating),
        "comment": comment[:500],
        "at": datetime.now(UTC).isoformat(),
        "stored_with_agent": False,
    }
    if not agent_id:
        return record
    try:
        uuid.UUID(str(agent_id))
    except ValueError:
        return record
    from core.feedback.collector import submit_feedback

    try:
        result = await submit_feedback(
            agent_id=str(agent_id),
            run_id=session_key[:200],
            feedback_type="thumbs_up" if rating >= 3 else "thumbs_down",
            text=comment[:500],
            tenant_id=str(tenant_id),
            source=SOURCE,
            source_event_id=rating_event_id(session_key, int(rating), comment[:500]),
            actor_id=str(user_id)[:255],
            context={
                "rating": int(rating),
                "session_key": session_key,
                "channel": channel,
                "intent": intent,
                "sentiment": sentiment_label,
            },
        )
        status = result.get("status") if isinstance(result, dict) else None
        record["stored_with_agent"] = status == "stored"
        if status != "stored":
            logger.warning("conversation_feedback_not_stored", status=str(status))
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        logger.warning("conversation_feedback_store_failed", error_type=type(exc).__name__)
    return record
