# SPDX-License-Identifier: Apache-2.0
"""Call analytics from a transcript and its segments: sentiment, empathy, interaction and escalation signals.

Everything here is computed from the words and timings, with the
conversation service's sentiment lexicon for the customer's turns and a
small empathy lexicon for the agent's: acknowledgement, apology,
reassurance and thanks, and whether they followed a negative customer
turn. The interaction figures (talk ratio, pace, interruptions, silences,
the longest monologue) come from the segments. Nothing is scored by a
model, so the figures are the same on every run and explainable turn by
turn.
"""

from __future__ import annotations

import re
from typing import Any

from core.conversation import feedback

AGENT_NAMES = ("agent", "advisor", "officer", "representative", "speaker_1")
CUSTOMER_NAMES = ("customer", "caller", "client", "speaker_2")
EMPATHY: dict[str, tuple[str, ...]] = {
    "acknowledgement": ("i understand", "i see", "i hear you", "that makes sense", "i can see why", "understood"),
    "apology": ("sorry", "apologise", "apologize", "apologies", "my apologies", "regret"),
    "reassurance": (
        "don't worry",
        "do not worry",
        "rest assured",
        "i will take care",
        "i'll take care",
        "we will sort",
        "we'll sort",
        "happy to help",
        "let me help",
        "i can help",
        "we can fix",
    ),
    "thanks": ("thank you for", "thanks for", "appreciate your"),
}
ESCALATION_PHRASES = (
    "speak to a manager",
    "talk to a manager",
    "supervisor",
    "complaint",
    "ombudsman",
    "cancel my",
    "close my account",
    "legal action",
    "consumer forum",
)
INTERRUPTION_OVERLAP = 0.3
SILENCE_GAP = 3.0
_WORD_RE = re.compile(r"[\w']+")


def roles_of(
    speakers: list[str], *, agent: str | None = None, customer: str | None = None
) -> tuple[str | None, str | None]:
    """Which speaker is the agent and which the customer: as named, by a recognisable label, else first and second."""
    names = list(speakers)
    if agent in names and customer in names:
        return agent, customer
    found_agent = agent if agent in names else next((n for n in names if n.lower() in AGENT_NAMES), None)
    found_customer = (
        customer
        if customer in names
        else next((n for n in names if n.lower() in CUSTOMER_NAMES and n != found_agent), None)
    )
    if found_agent is None and names:
        found_agent = next((n for n in names if n != found_customer), None)
    if found_customer is None:
        found_customer = next((n for n in names if n != found_agent), None)
    return found_agent, found_customer


def _markers(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    return [kind for kind, phrases in EMPATHY.items() if any(phrase in lowered for phrase in phrases)]


def sentiment_of_turns(turns: list[dict[str, Any]], customer: str | None) -> dict[str, Any]:
    """The customer's sentiment turn by turn, by thirds of the call, and overall; the negative streaks."""
    own = [t for t in turns if customer is None or t.get("speaker") == customer]
    scored = []
    for turn in own:
        mood = feedback.sentiment(str(turn.get("text") or ""))
        scored.append(
            {"start": turn.get("start"), "speaker": turn.get("speaker"), "score": mood["score"], "label": mood["label"]}
        )
    if not scored:
        return {
            "turns": [],
            "overall": 0.0,
            "opening": None,
            "middle": None,
            "closing": None,
            "negative_share": 0.0,
            "longest_negative_streak": 0,
        }
    third = max(1, len(scored) // 3)
    parts = [
        scored[:third],
        scored[third : len(scored) - third] or scored[third : third + 1],
        scored[len(scored) - third :],
    ]
    mean = lambda items: round(sum(i["score"] for i in items) / len(items), 3) if items else None  # noqa: E731
    streak = longest = 0
    for item in scored:
        streak = streak + 1 if item["label"] == "negative" else 0
        longest = max(longest, streak)
    return {
        "turns": scored,
        "overall": mean(scored),
        "opening": mean(parts[0]),
        "middle": mean(parts[1]),
        "closing": mean(parts[2]),
        "negative_share": round(sum(1 for i in scored if i["label"] == "negative") / len(scored), 3),
        "longest_negative_streak": longest,
    }


def empathy_of_turns(turns: list[dict[str, Any]], agent: str | None, customer: str | None) -> dict[str, Any]:
    """The agent's empathy markers, how many negative customer turns were answered with one, and a 0 to 100 score."""
    markers: dict[str, int] = dict.fromkeys(EMPATHY, 0)
    answered = 0
    negatives = 0
    previous_negative = False
    agent_turns = 0
    for turn in turns:
        speaker = turn.get("speaker")
        text = str(turn.get("text") or "")
        if customer is not None and speaker == customer:
            previous_negative = feedback.sentiment(text)["label"] == "negative"
            negatives += int(previous_negative)
            continue
        if agent is not None and speaker != agent:
            continue
        agent_turns += 1
        found = _markers(text)
        for kind in found:
            markers[kind] += 1
        if previous_negative and found:
            answered += 1
        previous_negative = False
    presence = min(1.0, sum(markers.values()) / max(1.0, agent_turns * 0.5)) if agent_turns else 0.0
    responsiveness = answered / negatives if negatives else (1.0 if agent_turns else 0.0)
    score = round(100 * (0.5 * presence + 0.5 * responsiveness))
    return {
        "markers": markers,
        "agent_turns": agent_turns,
        "negative_customer_turns": negatives,
        "answered_with_empathy": answered,
        "score": score,
    }


def interaction_of(segments: list[dict[str, Any]], turns: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    """Talk ratio, pace, interruptions, silences and the longest monologue from the segments and turns."""
    talk: dict[str, float] = {}
    words: dict[str, int] = {}
    for segment in segments:
        talk[segment["speaker"]] = talk.get(segment["speaker"], 0.0) + float(
            segment.get("duration") or (segment["end"] - segment["start"])
        )
    for turn in turns:
        words[turn["speaker"]] = words.get(turn["speaker"], 0) + int(
            turn.get("words") or len(_WORD_RE.findall(str(turn.get("text") or "")))
        )
    total_talk = sum(talk.values()) or 1.0
    ordered = sorted(segments, key=lambda s: float(s["start"]))
    interruptions = []
    silences = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        overlap = float(earlier["end"]) - float(later["start"])
        if earlier["speaker"] != later["speaker"] and overlap >= INTERRUPTION_OVERLAP:
            interruptions.append(
                {"at": round(float(later["start"]), 3), "by": later["speaker"], "overlap": round(overlap, 3)}
            )
        gap = float(later["start"]) - float(earlier["end"])
        if gap >= SILENCE_GAP:
            silences.append(
                {
                    "from": round(float(earlier["end"]), 3),
                    "to": round(float(later["start"]), 3),
                    "seconds": round(gap, 3),
                }
            )
    longest = max(segments, key=lambda s: float(s.get("duration") or (s["end"] - s["start"])), default=None)
    return {
        "duration_seconds": round(duration, 3),
        "talk_seconds": {k: round(v, 3) for k, v in talk.items()},
        "talk_ratio": {k: round(v / total_talk, 3) for k, v in talk.items()},
        "words_per_minute": {k: round(words.get(k, 0) / (talk[k] / 60.0), 1) if talk.get(k) else 0.0 for k in talk},
        "turns": len(turns),
        "interruptions": interruptions,
        "silences": silences,
        "longest_monologue": {"speaker": longest["speaker"], "seconds": round(float(longest.get("duration") or 0), 3)}
        if longest
        else None,
    }


def signals_of(turns: list[dict[str, Any]], sentiment: dict[str, Any], customer: str | None) -> list[dict[str, Any]]:
    """Escalation signals: a phrase that asks for a person or a complaint, and a run of negative turns."""
    out = []
    for turn in turns:
        if customer is not None and turn.get("speaker") != customer:
            continue
        lowered = str(turn.get("text") or "").lower()
        for phrase in ESCALATION_PHRASES:
            if phrase in lowered:
                out.append({"kind": "escalation_phrase", "phrase": phrase, "at": turn.get("start")})
                break
    if sentiment.get("longest_negative_streak", 0) >= 2:
        out.append({"kind": "negative_streak", "turns": sentiment["longest_negative_streak"]})
    if (
        sentiment.get("closing") is not None
        and sentiment.get("opening") is not None
        and sentiment["closing"] < sentiment["opening"] - 0.3
    ):
        out.append({"kind": "mood_fell", "from": sentiment["opening"], "to": sentiment["closing"]})
    return out


def analyse(
    transcript: dict[str, Any],
    segments: list[dict[str, Any]],
    duration: float,
    *,
    agent: str | None = None,
    customer: str | None = None,
) -> dict[str, Any]:
    """The analytics of one call from its transcript turns and segments."""
    turns = list(transcript.get("turns") or [])
    speakers = []
    for item in segments + turns:
        name = item.get("speaker")
        if name and name not in speakers:
            speakers.append(name)
    agent_name, customer_name = roles_of(speakers, agent=agent, customer=customer)
    sentiment = sentiment_of_turns(turns, customer_name)
    empathy = empathy_of_turns(turns, agent_name, customer_name)
    interaction = interaction_of(segments, turns, duration)
    signals = signals_of(turns, sentiment, customer_name)
    balance = interaction["talk_ratio"].get(customer_name, 0.0) if customer_name else 0.0
    return {
        "roles": {"agent": agent_name, "customer": customer_name},
        "sentiment": sentiment,
        "empathy": empathy,
        "interaction": interaction,
        "signals": signals,
        "scores": {
            "customer_sentiment": sentiment["overall"],
            "empathy": empathy["score"],
            "customer_talk_share": round(balance, 3),
            "escalation_risk": "high"
            if any(s["kind"] == "escalation_phrase" for s in signals)
            else "medium"
            if signals
            else "low",
        },
    }
