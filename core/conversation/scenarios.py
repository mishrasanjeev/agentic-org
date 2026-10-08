# SPDX-License-Identifier: Apache-2.0
"""Scenario templates: what a conversation offers next once an action has run.

A scenario chains intents into a multi-step flow without a model: a raised
dispute offers to track its reference, a loan enquiry offers to start the
application and the application offers tracking, a blocked card offers a
replacement, and an application that needs something from the user offers a
person. The offer is a question the user answers with yes or no; a yes starts
the next intent with its slots prefilled from what is already known.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

STAGE_OFFERING = "offering"
KIND_FOLLOW_UP = "follow_up"
KIND_PERSON = "person"

_NEEDS_USER = ("documents_required", "pending_documents", "action_required", "information_required", "on_hold")
_REFERENCE_KEYS = (
    "reference",
    "reference_number",
    "dispute_id",
    "application_id",
    "application_number",
    "transaction_id",
    "request_id",
    "ticket_id",
    "number",
    "id",
)


@dataclass
class Offer:
    intent: str
    text: str
    prefill: dict[str, Any] = field(default_factory=dict)
    kind: str = KIND_FOLLOW_UP

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reference_of(execution: dict[str, Any] | None) -> str | None:
    """The reference an executed action returned, wherever the tool put it."""
    if not execution or execution.get("status") != "executed":
        return None
    result = execution.get("result")
    candidates: list[Any] = [result]
    if isinstance(result, dict):
        candidates.extend(result.get(key) for key in ("result", "data", "ticket", "dispute", "application"))
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in _REFERENCE_KEYS:
            value = item.get(key)
            if value not in (None, "", {}, []):
                return str(value)
    return None


def _status_of(execution: dict[str, Any] | None) -> str:
    result = (execution or {}).get("result")
    if isinstance(result, dict):
        return str(result.get("status") or result.get("state") or "").lower().replace(" ", "_")
    return ""


def follow_up(intent: str | None, slots: dict[str, Any], execution: dict[str, Any] | None) -> Offer | None:
    """The next step a scenario offers after ``intent`` ran, or None when the flow ends here."""
    if not execution or execution.get("status") != "executed":
        return None
    reference = reference_of(execution)
    if intent == "dispute_transaction" and reference:
        return Offer(
            "application_status",
            f"Your dispute reference is {reference}. Would you like me to track its status now? Reply yes or no.",
            {"reference": reference},
        )
    if intent == "loan_enquiry":
        prefill = {k: v for k, v in slots.items() if k in ("loan_type", "amount") and v not in (None, "")}
        return Offer(
            "loan_application",
            "Would you like to start a loan application with these details? Reply yes or no.",
            prefill,
        )
    if intent == "loan_application" and reference:
        return Offer(
            "application_status",
            f"Your application reference is {reference}. Would you like me to track its status now? Reply yes or no.",
            {"reference": reference},
        )
    if intent == "card_block":
        prefill = {"card": slots["card"]} if slots.get("card") else {}
        return Offer(
            "card_replacement",
            "The card is blocked. Would you like a replacement card sent to your registered address? Reply yes or no.",
            prefill,
        )
    if intent == "application_status" and _status_of(execution) in _NEEDS_USER:
        return Offer(
            "talk_to_agent",
            "The application needs something from you. Would you like me to connect you to a person who can help? "
            "Reply yes or no.",
            {},
            kind=KIND_PERSON,
        )
    return None


def person_offer(text: str) -> Offer:
    """An offer of a person, for a frustrated user or a stuck flow."""
    return Offer("talk_to_agent", text, {}, kind=KIND_PERSON)
