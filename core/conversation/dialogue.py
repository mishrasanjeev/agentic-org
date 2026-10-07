# SPDX-License-Identifier: Apache-2.0
"""The dialogue: one intent at a time, its slots filled turn by turn, confirmed before anything runs.

``advance`` takes the state and the user's text and returns the next state and
an ``Outcome``: a question for a missing slot (``ask``), a choice between
intents a turn could mean (``clarify``), a summary to confirm (``confirm``), the
action to run (``execute``), a hand-off (``escalate``), or a fallback when
nothing is recognised. Nothing here calls a tool.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from core.conversation import context as conversation_context
from core.conversation import feedback as conversation_feedback
from core.conversation import intents as catalogue
from core.conversation.intents import CLARIFY_MARGIN, INTENTS, MAX_AMOUNT, MIN_CONFIDENCE, Intent, Slot

MAX_RETRIES = 3  # answers for one slot that fail validation before the dialogue gives up


@dataclass(frozen=True)
class Rules:
    """The rules the business console may change per tenant; the defaults are the catalogue's."""

    retries: int = MAX_RETRIES
    amount_limits: dict[str, float] = field(default_factory=dict)

    def ceiling(self, intent: Intent) -> float | None:
        """The most an amount slot of ``intent`` accepts: the console's limit, else the catalogue's."""
        if intent.max_amount is None:
            return None
        limit = self.amount_limits.get(intent.name)
        return float(limit) if limit is not None else intent.max_amount


MAX_HISTORY = 20
STAGE_IDLE = "idle"
STAGE_COLLECTING = "collecting"
STAGE_CONFIRMING = "confirming"
STAGE_CLARIFYING = "clarifying"
STAGE_DONE = "done"
STAGE_OFFERING = "offering"  # a scenario's next step, or a person, offered; yes or no
STAGE_RATING = "rating"  # a rating from 1 to 5 asked once
MAX_ACTIONS = 10

_YES_RE = re.compile(
    r"^\s*(yes|y|yeah|yep|yup|sure|ok|okay|confirm(ed)?|proceed|go ahead|do it|please do|correct|right|haan|ha)\b", re.I
)
_NO_RE = re.compile(r"^\s*(no|n|nope|nah|cancel|stop|abort|never ?mind|don'?t|do not|wrong|nahi)\b", re.I)
_CHANGE_RE = re.compile(r"\b(change|make it|instead|actually|not \d|rather)\b", re.I)


@dataclass
class Outcome:
    kind: str  # ask | clarify | confirm | execute | escalate | fallback | cancelled | greeting | answer
    text: str
    intent: str | None = None
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    action: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Dialogue:
    stage: str = STAGE_IDLE
    intent: str | None = None
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    pending: str | None = None
    retries: int = 0
    options: list[str] = field(default_factory=list)
    carry: dict[str, Any] = field(default_factory=dict)  # entities of a message awaiting clarification
    ambiguous: dict[str, list[Any]] = field(default_factory=dict)  # slot -> the values a message offered
    last_intent: str | None = None  # the last action that ran, for "the same amount" and "again"
    last_slots: dict[str, Any] = field(default_factory=dict)
    fallbacks: int = 0  # fallbacks in a row; after two the offer is a person
    offer: dict[str, Any] | None = None  # the scenario step or person offered (core/conversation/scenarios.py)
    actions: list[dict[str, Any]] = field(default_factory=list)  # what ran, for the summary
    rating: int | None = None
    rating_asked: bool = False
    sentiment: list[dict[str, Any]] = field(default_factory=list)  # the last user turns' scores
    negative_turns: int = 0  # negative turns in a row; two offer a person
    turns: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    started_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Dialogue:
        raw = dict(raw or {})
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in raw.items() if key in known})

    def current(self) -> Intent | None:
        return INTENTS.get(self.intent or "")

    def reset(self) -> None:
        self.stage = STAGE_IDLE
        self.intent = None
        self.confidence = 0.0
        self.slots = {}
        self.pending = None
        self.retries = 0
        self.options = []
        self.carry = {}
        self.ambiguous = {}
        self.offer = None


# ── Formatting ────────────────────────────────────────────────────────────────


def rupees(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    whole = int(amount)
    text = f"{whole:,}"
    if whole >= 100_000:  # Indian grouping: 12,34,567
        digits = str(whole)
        head, tail = digits[:-3], digits[-3:]
        pairs: list[str] = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        text = ",".join([head, *pairs, tail]) if head else ",".join([*pairs, tail])
    fraction = amount - whole
    return f"₹{text}" + (f".{int(round(fraction * 100)):02d}" if fraction >= 0.005 else "")


def summary_of(intent: Intent, slots: dict[str, Any]) -> str:
    """What is about to happen, in one sentence the user confirms."""
    s = slots
    if intent.name == "fund_transfer":
        text = f"Transfer {rupees(s.get('amount'))} to {s.get('payee')}"
        if s.get("from_account"):
            text += f" from the account ending {s['from_account']}"
        if s.get("remarks"):
            text += f" with remarks '{s['remarks']}'"
        return text + "."
    if intent.name == "card_block":
        return f"Block the card ending {s.get('card')} because it is {s.get('reason')}."
    if intent.name == "bill_payment":
        text = f"Pay {rupees(s.get('amount'))} to {s.get('biller')}"
        if s.get("consumer_id"):
            text += f" for consumer number {s['consumer_id']}"
        return text + "."
    if intent.name == "loan_application":
        return (
            f"Apply for a {s.get('loan_type')} loan of {rupees(s.get('amount'))} over {s.get('tenure_months')} months."
        )
    if intent.name == "card_replacement":
        return f"Send a replacement for the card ending {s.get('card')} to the registered address."
    if intent.name == "dispute_transaction":
        text = f"Raise a dispute for {rupees(s.get('amount'))}"
        if s.get("transaction_date"):
            text += f" on {s['transaction_date']}"
        if s.get("merchant"):
            text += f" at {s['merchant']}"
        return text + f" as {s.get('reason')}."
    parts = ", ".join(f"{key} {value}" for key, value in s.items() if value not in (None, ""))
    return f"{intent.title}" + (f" ({parts})" if parts else "") + "."


def handoff_summary(dialogue: Dialogue) -> dict[str, Any]:
    """What a person taking over needs: the intent tag, the slots so far and the last turns."""
    return {
        "intent": dialogue.intent,
        "slots": dict(dialogue.slots),
        "stage": dialogue.stage,
        "turns": dialogue.turns,
        "recent": dialogue.history[-6:],
    }


# ── Slot answers ──────────────────────────────────────────────────────────────


def parse_slot(
    slot: Slot,
    text: str,
    entities: dict[str, Any],
    *,
    today: date | None = None,
    max_amount: float | None = MAX_AMOUNT,
) -> tuple[Any, str | None]:
    """The value of ``slot`` in an answer, or (None, why it was refused)."""
    stripped = text.strip()
    if slot.kind == "amount":
        amount = entities.get("amount")
        if amount is None:
            amount = catalogue.parse_amount(stripped)
        if amount is None:
            return None, "Please give the amount as a number, for example 2500 or ₹2,500."
        if max_amount is not None and amount > max_amount:
            return (
                None,
                f"The most a conversational transaction can move is {rupees(max_amount)}. "
                "Please give a smaller amount.",
            )
        return amount, None
    if slot.kind in ("account", "card"):
        value = entities.get(slot.kind) or entities.get("ending")
        if value is None:
            digits = re.sub(r"\D", "", stripped)
            value = digits[-4:] if len(digits) >= 4 else None
        if value is None or not re.fullmatch(r"\d{4}", str(value)):
            return None, "Please give the last four digits."
        return str(value), None
    if slot.kind == "payee":
        value = (
            entities.get("payee")
            or catalogue.parse_payee("to " + stripped)
            or (stripped if 0 < len(stripped) <= 60 and not any(ch.isdigit() for ch in stripped) else None)
        )
        if not value:
            return None, "Please give the payee's name as saved in your beneficiaries."
        return value, None
    if slot.kind == "date":
        value = entities.get("date") or catalogue.parse_date(stripped, today=today)
        if value is None:
            return None, "Please give the date as day/month/year, for example 12/03/2026."
        return value, None
    if slot.kind == "reference":
        value = entities.get("reference")
        if value is None:
            token = re.sub(r"[^A-Za-z0-9/-]", "", stripped)
            value = token.upper() if 5 <= len(token) <= 40 else None
        if value is None:
            return None, "Please give the reference number as it appears on your acknowledgement."
        return value, None
    if slot.kind == "tenure":
        value = entities.get("tenure") or catalogue.parse_tenure(stripped)
        if value is None and stripped.isdigit() and 1 <= int(stripped) <= 600:
            value = int(stripped)
        if value is None:
            return None, "Please give the tenure in months or years, for example 24 months or 3 years."
        return value, None
    if slot.kind == "period":
        value = entities.get("period") or catalogue.parse_period(stripped)
        if value is None:
            return None, "Please give a period, for example the last 30 days or this month."
        return value, None
    if slot.kind == "choice":
        value = entities.get(slot.name) or catalogue.parse_reason(stripped, slot.choices)
        if value is None:
            lowered = stripped.lower()
            value = next((choice for choice in slot.choices if choice in lowered), None)
        if value is None:
            return None, f"Please choose one of: {', '.join(slot.choices)}."
        return value, None
    if not stripped or len(stripped) > 200:
        return None, "Please keep it short."
    return stripped, None


def fill_from_entities(
    intent: Intent, slots: dict[str, Any], entities: dict[str, Any], text: str = "", *, max_amount: float | None = None
) -> dict[str, Any]:
    """Slots the turn's entities (and, for choices and free text, the message itself) fill, never overwriting."""
    filled = dict(slots)
    ending_used = False
    for slot in intent.slots:
        if filled.get(slot.name) not in (None, ""):
            continue
        value: Any = None
        if slot.kind == "amount":
            value = None if entities.get("amount_options") else entities.get("amount")
        elif slot.kind in ("account", "card"):
            value = entities.get(slot.kind)
            if value is None and entities.get("ending") and not ending_used:
                value = entities["ending"]
                ending_used = True
        elif slot.kind == "payee":
            value = None if entities.get("payee_options") else entities.get("payee")
        elif slot.kind == "date":
            value = entities.get("date")
        elif slot.kind == "reference":
            value = entities.get("reference")
        elif slot.kind == "period":
            value = entities.get("period")
        elif slot.kind == "tenure":
            value = entities.get("tenure")
        elif slot.kind == "choice":
            value = entities.get(slot.name) or (catalogue.parse_reason(text, slot.choices) if text else None)
        elif slot.kind == "text":
            value = entities.get(slot.name)
        if value not in (None, ""):
            ceiling = max_amount if max_amount is not None else intent.max_amount
            if slot.kind == "amount" and ceiling is not None and float(value) > ceiling:
                continue
            filled[slot.name] = value
    return filled


def missing_slots(intent: Intent, slots: dict[str, Any]) -> list[str]:
    return [slot.name for slot in intent.slots if slot.required and slots.get(slot.name) in (None, "")]


# ── The state machine ─────────────────────────────────────────────────────────


def _note(dialogue: Dialogue, role: str, text: str) -> None:
    dialogue.history.append({"role": role, "text": text[:500]})
    del dialogue.history[:-MAX_HISTORY]


def _outcome(dialogue: Dialogue, kind: str, text: str, **extra: Any) -> Outcome:
    _note(dialogue, "assistant", text)
    intent = dialogue.current()
    return Outcome(
        kind=kind,
        text=text,
        intent=dialogue.intent,
        confidence=dialogue.confidence,
        slots=dict(dialogue.slots),
        missing=missing_slots(intent, dialogue.slots) if intent else [],
        **extra,
    )


def _next_step(dialogue: Dialogue, *, today: date | None = None) -> Outcome:
    """Ask for the next missing slot, confirm, or execute."""
    intent = dialogue.current()
    assert intent is not None
    missing = missing_slots(intent, dialogue.slots)
    if missing:
        slot = intent.slot(missing[0])
        assert slot is not None
        dialogue.stage = STAGE_COLLECTING
        dialogue.pending = slot.name
        dialogue.retries = 0
        offered = dialogue.ambiguous.pop(slot.name, None)
        if offered:
            # The message named more than one value: ask which, instead of acting on a guess.
            shown = [rupees(v) if slot.kind == "amount" else str(v) for v in offered]
            return _outcome(
                dialogue,
                "ask",
                f"I see more than one {slot.name.replace('_', ' ')}: {' or '.join(shown)}. Which one?",
                options=[{"value": v, "label": label} for v, label in zip(offered, shown, strict=True)],
            )
        return _outcome(dialogue, "ask", slot.prompt)
    dialogue.pending = None
    if intent.confirm:
        dialogue.stage = STAGE_CONFIRMING
        summary = summary_of(intent, dialogue.slots)
        return _outcome(dialogue, "confirm", f"{summary} Reply yes to confirm or no to cancel.", summary=summary)
    return _execute(dialogue)


def _execute(dialogue: Dialogue) -> Outcome:
    intent = dialogue.current()
    assert intent is not None
    summary = summary_of(intent, dialogue.slots)
    action = intent.action
    slots = dict(dialogue.slots)
    dialogue.stage = STAGE_DONE
    outcome = _outcome(dialogue, "execute", summary, action=action, summary=summary)
    outcome.slots = slots
    dialogue.last_intent = intent.name
    dialogue.last_slots = slots
    dialogue.fallbacks = 0
    dialogue.reset()
    return outcome


def _start(
    dialogue: Dialogue,
    intent: Intent,
    confidence: float,
    entities: dict[str, Any],
    text: str = "",
    *,
    today: date | None = None,
    prefill: dict[str, Any] | None = None,
    rules: Rules | None = None,
) -> Outcome:
    rules = rules or Rules()
    dialogue.intent = intent.name
    dialogue.confidence = confidence
    merged = conversation_context.resolve(text, {**dialogue.carry, **entities}, dialogue.last_slots)
    dialogue.slots = fill_from_entities(intent, dict(prefill or {}), merged, text, max_amount=rules.ceiling(intent))
    dialogue.ambiguous = {
        slot.name: list(merged[f"{slot.kind}_options"])
        for slot in intent.slots
        if merged.get(f"{slot.kind}_options") and dialogue.slots.get(slot.name) in (None, "")
    }
    dialogue.carry = {}
    dialogue.pending = None
    dialogue.retries = 0
    dialogue.options = []
    dialogue.started_at = datetime.now(UTC).isoformat()
    if intent.risk == "smalltalk":
        dialogue.reset()
        return _outcome(
            dialogue,
            "greeting",
            "Hello. I can help with balances, statements, card blocking, transfers, bill payments, loans, "
            "transaction disputes and application status. What would you like to do?",
        )
    if intent.risk == "handoff":
        dialogue.stage = STAGE_DONE
        outcome = _outcome(dialogue, "escalate", "I will connect you to a person. One moment.", summary=None)
        outcome.options = []
        dialogue.reset()
        return outcome
    return _next_step(dialogue, today=today)


def _fallback(dialogue: Dialogue) -> Outcome:
    dialogue.fallbacks += 1
    if dialogue.fallbacks >= 2:
        return _outcome(
            dialogue,
            "fallback",
            "I still did not catch that. Would you like me to connect you to a person? Reply yes and I will "
            "hand this over with a summary.",
            options=[{"intent": "talk_to_agent", "title": "Talk to a person"}],
        )
    return _outcome(
        dialogue,
        "fallback",
        "I did not catch that. I can help with balances, statements, card blocking, transfers, bill payments, "
        "loans, transaction disputes and application status. You can also ask to talk to a person.",
    )


def advance(dialogue: Dialogue, text: str, *, today: date | None = None, rules: Rules | None = None) -> Outcome:
    """One user turn: fill or ask, clarify, confirm, execute, escalate or fall back."""
    rules = rules or Rules()
    dialogue.turns += 1
    _note(dialogue, "user", text)
    entities = catalogue.extract_entities(text, today=today)
    intent = dialogue.current()
    mood = conversation_feedback.sentiment(text)
    dialogue.sentiment = (dialogue.sentiment + [mood])[-5:]
    dialogue.negative_turns = dialogue.negative_turns + 1 if mood["label"] == "negative" else 0

    # Rating: a 1 to 5 is kept; anything else is a new message.
    if dialogue.stage == STAGE_RATING:
        dialogue.stage = STAGE_IDLE
        rating = conversation_feedback.rating_from_text(text)
        if rating is not None:
            dialogue.rating = rating
            return _outcome(dialogue, "rated", "Thank you for the feedback. Is there anything else I can help with?")

    # Offering: yes starts the next step with what is already known; no ends the flow.
    if dialogue.stage == STAGE_OFFERING and isinstance(dialogue.offer, dict):
        offer = dict(dialogue.offer)
        dialogue.offer = None
        dialogue.stage = STAGE_IDLE
        if _YES_RE.match(text):
            next_intent = INTENTS.get(str(offer.get("intent") or ""))
            if next_intent is not None:
                dialogue.negative_turns = 0
                return _start(
                    dialogue, next_intent, 0.9, entities, text, today=today, prefill=offer.get("prefill"), rules=rules
                )
        elif _NO_RE.match(text):
            return _outcome(dialogue, "declined", "Alright. Is there anything else I can help with?")

    # Clarifying: the user picks one of the intents offered (by name, title or number).
    if dialogue.stage == STAGE_CLARIFYING and dialogue.options:
        choice = _pick_option(text, dialogue.options)
        if choice is None:
            matches = [
                m
                for m in catalogue.recognise(text)
                if m.confidence >= MIN_CONFIDENCE and m.intent.name in dialogue.options
            ]
            choice = matches[0].intent.name if matches else None
        if choice is None:
            if _NO_RE.match(text):
                dialogue.reset()
                return _outcome(dialogue, "cancelled", "Alright, nothing has been done. What would you like to do?")
            options = [{"intent": name, "title": INTENTS[name].title} for name in dialogue.options]
            return _outcome(dialogue, "clarify", _clarify_text(dialogue.options), options=options)
        return _start(dialogue, INTENTS[choice], 0.9, entities, text, today=today, rules=rules)

    # Confirming: yes runs it, no cancels it, a change re-confirms it.
    if dialogue.stage == STAGE_CONFIRMING and intent is not None:
        if _YES_RE.match(text):
            return _execute(dialogue)
        if _NO_RE.match(text):
            dialogue.reset()
            return _outcome(dialogue, "cancelled", "Cancelled. Nothing has been done. What would you like to do?")
        changed = fill_from_entities(intent, {}, entities, text, max_amount=rules.ceiling(intent))
        if changed:
            dialogue.slots.update(changed)
            return _next_step(dialogue, today=today)
        summary = summary_of(intent, dialogue.slots)
        return _outcome(dialogue, "confirm", f"{summary} Reply yes to confirm or no to cancel.", summary=summary)

    # Collecting: the answer to the pending slot, plus anything else the turn names.
    if dialogue.stage == STAGE_COLLECTING and intent is not None and dialogue.pending:
        if _NO_RE.match(text) and dialogue.retries == 0 and not entities:
            dialogue.reset()
            return _outcome(dialogue, "cancelled", "Cancelled. Nothing has been done. What would you like to do?")
        slot = intent.slot(dialogue.pending)
        assert slot is not None
        other = catalogue.recognise(text)
        switched = (
            other
            and other[0].confidence >= MIN_CONFIDENCE + 0.1
            and other[0].intent.name != intent.name
            and not entities
        )
        if switched:
            return _start(dialogue, other[0].intent, other[0].confidence, entities, text, today=today, rules=rules)
        value, problem = parse_slot(slot, text, entities, today=today, max_amount=rules.ceiling(intent))
        if problem:
            dialogue.retries += 1
            if dialogue.retries >= rules.retries:
                dialogue.reset()
                return _outcome(
                    dialogue,
                    "escalate",
                    "I could not get what I need for that. Let me connect you to a person who can help.",
                )
            return _outcome(dialogue, "ask", problem)
        dialogue.slots[slot.name] = value
        dialogue.slots = fill_from_entities(
            intent,
            dialogue.slots,
            {k: v for k, v in entities.items() if k != slot.kind},
            text,
            max_amount=rules.ceiling(intent),
        )
        return _next_step(dialogue, today=today)

    # A yes after the offer of a person is the hand-off.
    if dialogue.stage == STAGE_IDLE and dialogue.fallbacks >= 2 and _YES_RE.match(text):
        dialogue.fallbacks = 0
        return _start(dialogue, INTENTS["talk_to_agent"], 0.9, entities, text, today=today, rules=rules)

    # Idle: "again" repeats the last action, confirmed afresh.
    if conversation_context.repeats_last(text) and dialogue.last_intent and dialogue.last_intent in INTENTS:
        repeated = INTENTS[dialogue.last_intent]
        dialogue.intent = repeated.name
        dialogue.confidence = 0.9
        dialogue.slots = dict(dialogue.last_slots)
        dialogue.pending = None
        dialogue.retries = 0
        dialogue.options = []
        dialogue.carry = {}
        dialogue.ambiguous = {}
        dialogue.started_at = datetime.now(UTC).isoformat()
        return _next_step(dialogue, today=today)

    # Idle: recognise the turn.
    parts = catalogue.split_requests(text)
    if len(parts) > 1:
        names: list[str] = []
        for part in parts:
            matches = catalogue.recognise(part)
            if matches and matches[0].confidence >= MIN_CONFIDENCE and matches[0].intent.name not in names:
                names.append(matches[0].intent.name)
        if len(names) > 1:
            dialogue.stage = STAGE_CLARIFYING
            dialogue.options = names
            dialogue.carry = entities
            options = [{"intent": name, "title": INTENTS[name].title} for name in names]
            return _outcome(dialogue, "clarify", _clarify_text(names), options=options)
    matches = catalogue.recognise(text)
    if not matches or matches[0].confidence < MIN_CONFIDENCE:
        return _fallback(dialogue)
    top = matches[0]
    close = [
        m for m in matches[1:] if top.confidence - m.confidence < CLARIFY_MARGIN and m.confidence >= MIN_CONFIDENCE
    ]
    if close and top.intent.risk != "smalltalk":
        names = [top.intent.name] + [m.intent.name for m in close][:2]
        dialogue.stage = STAGE_CLARIFYING
        dialogue.options = names
        dialogue.carry = entities
        options = [{"intent": name, "title": INTENTS[name].title} for name in names]
        return _outcome(dialogue, "clarify", _clarify_text(names), options=options)
    return _start(dialogue, top.intent, top.confidence, entities, text, today=today, rules=rules)


def _clarify_text(names: list[str]) -> str:
    titles = [f"{index + 1}. {INTENTS[name].title.lower()}" for index, name in enumerate(names)]
    return "Which would you like to do first: " + "; ".join(titles) + "? Reply with the number."


def _pick_option(text: str, options: list[str]) -> str | None:
    stripped = text.strip().lower().rstrip(".")
    if stripped.isdigit() and 1 <= int(stripped) <= len(options):
        return options[int(stripped) - 1]
    for name in options:
        if name in stripped or INTENTS[name].title.lower() in stripped:
            return name
    return None
