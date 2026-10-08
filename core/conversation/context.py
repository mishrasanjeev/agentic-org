# SPDX-License-Identifier: Apache-2.0
"""Multi-turn context: what earlier turns an agent sees, and references a banking turn resolves from the last action.

The chat history is stored per user, company and agent (``api/v1/chat.py``).
With conversational services on, a bounded view of the recent turns travels
to the agent as the run's context, so a follow-up ("and for last month?") is
answered against what was said. In the banking dialogue, "the same amount",
"that account" and "do that again" resolve from the slots of the last action.
"""

from __future__ import annotations

import re
from typing import Any

RECENT_TURNS = 8
TURN_CHARS = 400
CONTEXT_NOTE = (
    "Earlier turns of this conversation are in Context.conversation, oldest first. "
    "Resolve references such as 'the same', 'that one' or 'as before' from them, "
    "and ask one short question when a reference is ambiguous instead of guessing."
)

_SAME_AMOUNT_RE = re.compile(r"\b(the )?(same|that) amount\b|\bsame (sum|figure)\b", re.I)
_SAME_PAYEE_RE = re.compile(
    r"\b(the )?(same|that) (person|payee|beneficiary|recipient)\b|\bto (him|her|them) again\b", re.I
)
_SAME_ACCOUNT_RE = re.compile(r"\b(the )?(same|that) account\b", re.I)
_AGAIN_RE = re.compile(r"^\s*(do (that|it) again|repeat (that|it)|same again|once more|again)\s*[.!]?\s*$", re.I)


def recent_turns(
    entries: list[dict[str, Any]] | None, *, limit: int = RECENT_TURNS, chars: int = TURN_CHARS
) -> list[dict[str, str]]:
    """The last turns of a chat history as the agent sees them: role and bounded text, oldest first."""
    turns: list[dict[str, str]] = []
    for entry in list(entries or [])[-limit:]:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        role = "user" if str(entry.get("role") or "") == "user" else "assistant"
        turns.append({"role": role, "text": text if len(text) <= chars else text[:chars] + "…"})
    return turns


def context_block(entries: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The run context carrying the recent turns; empty when there are none."""
    turns = recent_turns(entries)
    return {"conversation": turns} if turns else {}


def with_context_note(system_prompt: str, entries: list[dict[str, Any]] | None) -> str:
    """The system prompt with the note on earlier turns, only when there are earlier turns."""
    if not recent_turns(entries):
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{CONTEXT_NOTE}"


def references(text: str) -> set[str]:
    """Which of the last action's values the text refers back to."""
    found: set[str] = set()
    if _SAME_AMOUNT_RE.search(text):
        found.add("amount")
    if _SAME_PAYEE_RE.search(text):
        found.add("payee")
    if _SAME_ACCOUNT_RE.search(text):
        found.add("account")
    return found


def repeats_last(text: str) -> bool:
    """Whether the text asks for the last action again, unchanged."""
    return bool(_AGAIN_RE.match(text))


def resolve(text: str, entities: dict[str, Any], last_slots: dict[str, Any] | None) -> dict[str, Any]:
    """``entities`` with the values the text refers back to taken from the last action's slots."""
    last = dict(last_slots or {})
    if not last:
        return dict(entities)
    resolved = dict(entities)
    wanted = references(text)
    if "amount" in wanted and "amount" not in resolved and last.get("amount") is not None:
        resolved["amount"] = last["amount"]
    if "payee" in wanted and "payee" not in resolved and last.get("payee"):
        resolved["payee"] = last["payee"]
    if "account" in wanted and "account" not in resolved:
        account = last.get("from_account") or last.get("account")
        if account:
            resolved["account"] = account
    return resolved
