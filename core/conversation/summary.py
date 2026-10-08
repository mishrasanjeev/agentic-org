# SPDX-License-Identifier: Apache-2.0
"""Conversation summaries: what was asked, what was done, what is pending, for a person or a downstream system.

Built from the dialogue's own record (the actions that ran, the stage it is
in, the hand-off, the rating and the sentiment), not from a model, so it is
the same every time and never invents an action that did not happen.
"""

from __future__ import annotations

from typing import Any

from core.conversation import dialogue as dialogue_engine
from core.conversation.dialogue import Dialogue
from core.conversation.intents import INTENTS

MAX_ACTIONS = 10


def _title(intent: str | None) -> str:
    return INTENTS[intent].title.lower() if intent and intent in INTENTS else "general enquiry"


def _action_line(action: dict[str, Any]) -> str:
    title = _title(action.get("intent"))
    status = str(action.get("status") or "")
    reference = action.get("reference")
    if status == "executed":
        return f"{title} done" + (f" (reference {reference})" if reference else "")
    if status == "refused":
        return f"{title} refused under the grant"
    if status == "unbound":
        return f"{title} could not run (no tool)"
    return f"{title} failed"


def pending_of(dialogue: Dialogue) -> str | None:
    """What the conversation is waiting on, in one phrase."""
    intent = _title(dialogue.intent)
    if dialogue.stage == dialogue_engine.STAGE_COLLECTING and dialogue.pending:
        return f"{intent}: waiting for {dialogue.pending.replace('_', ' ')}"
    if dialogue.stage == dialogue_engine.STAGE_CONFIRMING:
        return f"{intent}: awaiting confirmation"
    if dialogue.stage == dialogue_engine.STAGE_CLARIFYING:
        return "waiting for the user to choose a request"
    if dialogue.stage == "offering" and isinstance(dialogue.offer, dict):
        return f"offer of {_title(dialogue.offer.get('intent'))} awaiting an answer"
    if dialogue.stage == "rating":
        return "waiting for a rating"
    return None


def summarise(dialogue: Dialogue, *, escalation: dict[str, Any] | None = None) -> dict[str, Any]:
    """The summary: a paragraph plus its parts (requests, actions, pending item, hand-off, rating, sentiment)."""
    actions = [dict(a) for a in (dialogue.actions or [])[-MAX_ACTIONS:]]
    requests: list[str] = []
    for action in actions:
        name = action.get("intent")
        if name and name not in requests:
            requests.append(name)
    if dialogue.intent and dialogue.intent not in requests:
        requests.append(dialogue.intent)
    if dialogue.last_intent and dialogue.last_intent not in requests:
        requests.append(dialogue.last_intent)
    pending = pending_of(dialogue)
    rating = dialogue.rating
    sentiment = (
        dialogue.sentiment[-1]["label"] if dialogue.sentiment and isinstance(dialogue.sentiment[-1], dict) else None
    )
    parts = [f"A conversation of {dialogue.turns} turn{'s' if dialogue.turns != 1 else ''}"]
    if requests:
        parts[0] += " about " + ", ".join(_title(name) for name in requests)
    parts[0] += "."
    if actions:
        parts.append("Actions: " + "; ".join(_action_line(a) for a in actions) + ".")
    else:
        parts.append("No action has run.")
    if pending:
        parts.append(f"Pending: {pending}.")
    if escalation:
        who = escalation.get("ticket") or {}
        reference = who.get("reference") if isinstance(who, dict) else None
        parts.append(
            "Handed off to a person"
            + (f" because {escalation.get('reason')}" if escalation.get("reason") else "")
            + (f", ticket {reference}" if reference else "")
            + "."
        )
    if rating is not None:
        parts.append(f"The user rated the conversation {rating}/5.")
    if sentiment and sentiment != "neutral":
        parts.append(f"The user's last message read as {sentiment}.")
    return {
        "text": " ".join(parts),
        "turns": dialogue.turns,
        "requests": requests,
        "actions": actions,
        "pending": pending,
        "escalation": {k: escalation.get(k) for k in ("reason", "intent", "hitl_id", "ticket")} if escalation else None,
        "rating": rating,
        "sentiment": sentiment,
    }
