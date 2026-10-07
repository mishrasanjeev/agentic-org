# SPDX-License-Identifier: Apache-2.0
"""Banking intents: the catalogue, entity extraction and recognition.

Recognition is deterministic: each intent names weighted patterns, a turn's
score is the sum of the weights its text matches, and the confidence is that
score capped below one. Two intents close in confidence are a clarification,
not a guess. Entities (amounts, account and card endings, dates, payees,
references, periods, loan types, reasons) are extracted once per turn and
fill the slots of whichever intent runs.

The catalogue is synthetic and generic: no institution's products or names.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

MIN_CONFIDENCE = 0.5  # below this a turn is a fallback
CLARIFY_MARGIN = 0.15  # two intents this close are asked about
MAX_CONFIDENCE = 0.98
MAX_AMOUNT = 1_000_000  # the most one conversational transaction may move


@dataclass(frozen=True)
class Slot:
    name: str
    kind: str  # amount | account | card | payee | date | reference | period | choice | text
    prompt: str
    required: bool = True
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Intent:
    name: str
    title: str
    risk: str  # read | transact | handoff | smalltalk
    patterns: tuple[tuple[str, float], ...]
    slots: tuple[Slot, ...] = ()
    confirm: bool = False
    action: str | None = None  # the tool action an agent binds (runtime.ACTIONS)
    max_amount: float | None = MAX_AMOUNT  # the most an amount slot accepts; None for enquiries and disputes

    def slot(self, name: str) -> Slot | None:
        return next((slot for slot in self.slots if slot.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "risk": self.risk,
            "confirm": self.confirm,
            "action": self.action,
            "slots": [
                {"name": s.name, "kind": s.kind, "required": s.required, "prompt": s.prompt, "choices": list(s.choices)}
                for s in self.slots
            ],
        }


CATALOGUE: tuple[Intent, ...] = (
    Intent(
        "greeting",
        "Greeting",
        "smalltalk",
        ((r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|namaste)\b", 0.7),),
    ),
    Intent(
        "balance_enquiry",
        "Balance enquiry",
        "read",
        (
            (r"\bbalance\b", 0.6),
            (r"\bhow much (money|do i have|is (there|left))\b", 0.5),
            (r"\bavailable (funds|amount)\b", 0.4),
        ),
        (Slot("account", "account", "Which account? Tell me the last four digits.", required=False),),
        action="balance_enquiry",
    ),
    Intent(
        "mini_statement",
        "Mini statement",
        "read",
        (
            (r"\b(mini[- ]?)?statement\b", 0.6),
            (r"\b(recent|last|latest) (\d+ )?(transactions|debits|credits|payments)\b", 0.5),
            (r"\btransaction (history|list)\b", 0.5),
        ),
        (
            Slot("account", "account", "Which account? Tell me the last four digits.", required=False),
            Slot("period", "period", "For which period? For example, the last 30 days.", required=False),
        ),
        action="mini_statement",
    ),
    Intent(
        "card_block",
        "Card block",
        "transact",
        (
            (r"\b(block|freeze|hotlist|deactivate|disable)\b.*\bcard\b", 0.6),
            (r"\bcard\b.*\b(blocked|lost|stolen|missing|frozen)\b", 0.5),
            (r"\b(lost|stolen) (my )?card\b", 0.6),
        ),
        (
            Slot("card", "card", "Which card? Tell me the last four digits on it."),
            Slot(
                "reason",
                "choice",
                "Why is it being blocked: lost, stolen or damaged?",
                choices=("lost", "stolen", "damaged"),
            ),
        ),
        confirm=True,
        action="card_block",
    ),
    Intent(
        "fund_transfer",
        "Fund transfer",
        "transact",
        (
            (r"\b(transfer|send|remit|move)\b.*\b(money|funds|amount|sum|rs\.?|rupees|inr|₹|\d)", 0.6),
            (r"\bpay\b.*\bto\b", 0.4),
            (r"\b(neft|imps|rtgs|upi)\b", 0.3),
        ),
        (
            Slot("amount", "amount", "How much should I transfer?"),
            Slot("payee", "payee", "Who should receive it? Give me the payee's name as saved in your beneficiaries."),
            Slot("from_account", "account", "From which account? Tell me the last four digits.", required=False),
            Slot("remarks", "text", "Any remarks for the transfer?", required=False),
        ),
        confirm=True,
        action="fund_transfer",
    ),
    Intent(
        "bill_payment",
        "Bill payment",
        "transact",
        (
            (r"\bpay\b.*\b(bill|electricity|water|gas|broadband|mobile|phone|dth|insurance premium)\b", 0.6),
            (r"\b(recharge|top[- ]?up)\b", 0.6),
            (r"\bbill payment\b", 0.5),
        ),
        (
            Slot("biller", "text", "Which biller? For example, the electricity board or the mobile operator."),
            Slot("amount", "amount", "How much is the bill?"),
            Slot("consumer_id", "reference", "What is the consumer or account number on the bill?", required=False),
        ),
        confirm=True,
        action="bill_payment",
    ),
    Intent(
        "loan_enquiry",
        "Loan enquiry",
        "read",
        (
            (r"^(?![\s\S]*\b(apply|application|status|track(ing)?)\b)[\s\S]*\bloan\b", 0.6),
            (r"\b(emi|interest rate|eligib(le|ility)|tenure)\b", 0.4),
            (r"\bborrow\b", 0.4),
        ),
        (
            Slot(
                "loan_type",
                "choice",
                "Which kind of loan: personal, home, car, education, business or gold?",
                required=False,
                choices=("personal", "home", "car", "education", "business", "gold"),
            ),
            Slot("amount", "amount", "Roughly how much would you like to borrow?", required=False),
        ),
        action="loan_enquiry",
        max_amount=None,
    ),
    Intent(
        "loan_application",
        "Loan application",
        "transact",
        (
            (r"^(?![\s\S]*\b(status|track(ing)?)\b)[\s\S]*\b(apply|application)\b[\s\S]*\bloan\b", 0.8),
            (r"^(?![\s\S]*\b(status|track(ing)?)\b)[\s\S]*\bloan application\b", 0.7),
            (r"\bstart (a |my )?(loan )?application\b", 0.6),
        ),
        (
            Slot(
                "loan_type",
                "choice",
                "Which kind of loan: personal, home, car, education, business or gold?",
                choices=("personal", "home", "car", "education", "business", "gold"),
            ),
            Slot("amount", "amount", "How much would you like to borrow?"),
            Slot("tenure_months", "tenure", "Over how many months or years would you repay it?"),
        ),
        confirm=True,
        action="loan_application",
        max_amount=None,
    ),
    Intent(
        "card_replacement",
        "Card replacement",
        "transact",
        (
            (r"\b(replace(ment)?|reissue|new)\b.*\bcard\b", 0.6),
            (r"\bcard\b.*\b(replace(ment|d)?|reissued?)\b", 0.6),
        ),
        (Slot("card", "card", "Which card should be replaced? Tell me the last four digits."),),
        confirm=True,
        action="card_replacement",
    ),
    Intent(
        "dispute_transaction",
        "Transaction dispute",
        "transact",
        (
            (r"\bdispute\b", 0.6),
            (r"\b(unauthori[sz]ed|fraudulent|did not (make|do|authori[sz]e)|didn'?t (make|do|authori[sz]e))\b", 0.6),
            (r"\b(wrong(ly)?|double|twice|incorrect(ly)?) (charged|debited|deducted|billed)\b", 0.6),
            (r"\b(charged|debited|deducted|billed)\b.*\b(twice|double|again|wrongly|incorrectly)\b", 0.6),
            (r"\b(never|not|didn'?t|did not) (received?|delivered|got)\b", 0.5),
            (r"\b(charged|debited|deducted)\b", 0.3),
            (r"\b(chargeback|refund)\b", 0.3),
        ),
        (
            Slot("amount", "amount", "What was the amount of the transaction?"),
            Slot("transaction_date", "date", "On which date was it? For example 12/03/2026.", required=False),
            Slot("merchant", "text", "Which merchant or description does it show?", required=False),
            Slot(
                "reason",
                "choice",
                "What is the reason: unauthorised, duplicate, wrong amount, or not received?",
                choices=("unauthorised", "duplicate", "wrong amount", "not received"),
            ),
        ),
        confirm=True,
        action="dispute_transaction",
        max_amount=None,
    ),
    Intent(
        "application_status",
        "Application status",
        "read",
        (
            (r"\bstatus\b.*\b(application|loan|card|request|claim)\b", 0.7),
            (r"\b(application|loan|card|request|claim)\b.*\bstatus\b", 0.7),
            (r"\btrack(ing)?\b.*\b(application|request)\b", 0.5),
            (r"\b(where is|what happened to) my (application|loan|card)\b", 0.5),
        ),
        (Slot("reference", "reference", "What is the application or reference number?"),),
        action="application_status",
    ),
    Intent(
        "talk_to_agent",
        "Talk to a person",
        "handoff",
        (
            (r"\b(talk|speak|chat|connect)\b.*\b(human|person|agent|someone|representative|executive|officer)\b", 0.7),
            (r"\b(customer (care|service|support)|helpdesk|help desk)\b", 0.5),
            (r"\b(escalate|complain(t)?)\b", 0.5),
        ),
    ),
)

INTENTS: dict[str, Intent] = {intent.name: intent for intent in CATALOGUE}


# ── Entities ──────────────────────────────────────────────────────────────────

_MULTIPLIERS = {
    "k": 1_000,
    "thousand": 1_000,
    "lakh": 100_000,
    "lac": 100_000,
    "lakhs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}
_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|inr|rupees?)\s*([\d,]+(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|crores?)?"
    r"|([\d,]+(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|crores?|rupees|rs\.?|inr|₹)\b",
    re.I,
)
_BARE_NUMBER_RE = re.compile(
    r"^\s*(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|crores?)?\s*$", re.I
)
_ACCOUNT_RE = re.compile(
    r"\b(?:account|a/c|acct|savings|current)\b[^\d]{0,24}?"
    r"(?:ending(?: in| with)?|last four(?: digits)?|x+|\*+)?\s*(\d{4})\b",
    re.I,
)
_CARD_RE = re.compile(r"\bcard\b[^\d]{0,24}?(?:ending(?: in| with)?|last four(?: digits)?|x+|\*+)?\s*(\d{4})\b", re.I)
_ENDING_RE = re.compile(r"\bending(?: in| with)?\s*(\d{4})\b", re.I)
_LAST4_RE = re.compile(r"^\s*(?:x+|\*+)?(\d{4})\s*$")
_PAYEE_RE = re.compile(r"\b(?:to|for)\s+((?:[A-Z][\w.'-]*)(?:\s+[A-Z][\w.'-]*){0,2})")
_PAYEE_LOWER_RE = re.compile(r"\bto\s+([a-z][\w.'-]*)\b", re.I)
_PAYEE_STOP = {
    "my",
    "the",
    "a",
    "an",
    "account",
    "savings",
    "current",
    "me",
    "him",
    "her",
    "them",
    "it",
    "pay",
    "transfer",
    "send",
    "someone",
    "this",
    "that",
    "card",
    "bank",
    "bill",
    "confirm",
    "cancel",
    "yes",
    "no",
    "block",
    "today",
    "tomorrow",
    "loan",
    "another",
    "other",
    "same",
    "you",
    "your",
}
_DATE_NUMERIC_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DATE_WORDS_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:,?\s+(\d{4}))?\b", re.I
)
_RELATIVE_DATES = {"today": 0, "yesterday": -1, "tomorrow": 1, "day before yesterday": -2}
_REFERENCE_RE = re.compile(
    r"\b(?:ref(?:erence)?|application|app|request|ticket|claim|consumer|customer)\s*(?:no\.?|number|id|#)?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9/-]{4,})\b"
    r"|\b([A-Z]{2,6}[-/]?\d{5,})\b",
    re.I,
)
_PERIOD_RE = re.compile(
    r"\b(?:last|past|previous)\s+(\d{1,3})\s+(days?|weeks?|months?|transactions?)\b"
    r"|\b(this|last|previous) (month|week|year)\b"
    r"|\b(\d{1,3})\s+(days?|transactions?)\b",
    re.I,
)
_PAYEE_OR_RE = re.compile(r"\bto\s+([A-Z][\w.'-]*)\s+or\s+([A-Z][\w.'-]*)\b")
_MERCHANT_RE = re.compile(r"\b(?:at|from|by)\s+((?:[A-Z][\w&.'-]*)(?:\s+[A-Z][\w&.'-]*){0,2})")
_TENURE_RE = re.compile(r"\b(\d{1,3})\s*(months?|mos?|years?|yrs?)\b", re.I)
_LOAN_TYPE_RE = re.compile(r"\b(personal|home|housing|car|vehicle|auto|education|student|business|gold)\b", re.I)
_LOAN_TYPES = {"housing": "home", "vehicle": "car", "auto": "car", "student": "education"}
_REASON_WORDS = {
    "lost": ("lost", "misplaced", "missing"),
    "stolen": ("stolen", "theft", "robbed", "pickpocket"),
    "damaged": ("damaged", "broken", "cracked", "worn"),
    "unauthorised": (
        "unauthorised",
        "unauthorized",
        "fraud",
        "fraudulent",
        "did not make",
        "didn't make",
        "never made",
    ),
    "duplicate": ("duplicate", "twice", "double", "two times"),
    "wrong amount": ("wrong amount", "incorrect amount", "overcharged", "wrongly charged", "wrong"),
    "not received": ("not received", "never received", "not delivered", "did not receive"),
}


_BARE_IN_TEXT_RE = re.compile(r"(?<![\w/:-])(\d[\d,]*(?:\.\d+)?)(?![\w/:-])")
_NOT_AMOUNT_BEFORE_RE = re.compile(
    r"(ending(?: in| with)?|last four(?: digits)?|account|a/c|acct|card|no\.?|number|ref(?:erence)?|id|#|x+|\*+|"
    r"last|past|previous|first)\s*$",
    re.I,
)


_NOT_AMOUNT_AFTER_RE = re.compile(
    r"\s*(?:(?:days?|weeks?|months?|years?|hours?|minutes?|transactions?|times|am|pm)\b|%)", re.I
)


def _bare_amount(text: str) -> float | None:
    """A number with no currency marker, unless what precedes it says it is not an amount."""
    for match in _BARE_IN_TEXT_RE.finditer(text):
        before = text[max(0, match.start() - 24) : match.start()]
        if _NOT_AMOUNT_BEFORE_RE.search(before):
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


def parse_amount(text: str) -> float | None:
    """The first amount in ``text``, in rupees, or None (``5,000``, ``₹5k``, ``2 lakh``, ``rs 1200.50``, ``500``)."""
    match = _AMOUNT_RE.search(text) or _BARE_NUMBER_RE.match(text)
    if not match:
        return _bare_amount(text)
    groups = [g for g in match.groups() if g is not None]
    number = groups[0].replace(",", "")
    unit = groups[1].lower().rstrip(".") if len(groups) > 1 else ""
    try:
        value = float(number)
    except ValueError:
        return None
    unit = unit.rstrip("s") if unit not in _MULTIPLIERS else unit
    value *= _MULTIPLIERS.get(unit, _MULTIPLIERS.get(unit + "s", 1))
    return value if math.isfinite(value) and value > 0 else None


def parse_date(text: str, *, today: date | None = None) -> str | None:
    """The first date in ``text`` as ISO, or None (``12/03/2026``, ``3 March``, ``yesterday``)."""
    today = today or datetime.now(UTC).date()
    lowered = text.lower()
    for word, offset in sorted(_RELATIVE_DATES.items(), key=lambda item: -len(item[0])):
        if re.search(r"\b" + re.escape(word) + r"\b", lowered):
            return (today + timedelta(days=offset)).isoformat()
    match = _DATE_NUMERIC_RE.search(text)
    if match:
        day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    match = _DATE_WORDS_RE.search(text)
    if match:
        day = int(match.group(1))
        month = _MONTHS.index(match.group(2).lower()[:3]) + 1
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def parse_payee(text: str) -> str | None:
    """A payee named after ``to``/``for`` (``to Ravi Kumar``), or None."""
    for pattern in (_PAYEE_RE, _PAYEE_LOWER_RE):
        for match in pattern.finditer(text):
            name = match.group(1).strip(" .,")
            words = [w for w in name.split() if w.lower() not in _PAYEE_STOP]
            if words and not any(ch.isdigit() for ch in name) and words[0].lower() not in _PAYEE_STOP:
                return " ".join(words)[:60]
    return None


def parse_reason(text: str, choices: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    for choice in choices:
        for word in _REASON_WORDS.get(choice, (choice,)):
            if word in lowered:
                return choice
    return None


def parse_period(text: str) -> str | None:
    match = _PERIOD_RE.search(text)
    if not match:
        return None
    if match.group(1):
        return f"last {int(match.group(1))} {match.group(2).lower().rstrip('s')}s"
    if match.group(3):
        return f"{match.group(3).lower()} {match.group(4).lower()}"
    return f"last {int(match.group(5))} {match.group(6).lower().rstrip('s')}s"


def extract_entities(text: str, *, today: date | None = None) -> dict[str, Any]:
    """Every entity the text names, keyed by slot kind (and ``loan_type``/``reason`` by name)."""
    found: dict[str, Any] = {}
    amount = parse_amount(text)
    if amount is not None:
        found["amount"] = amount
        amounts = parse_amounts(text)
        if len(amounts) > 1:
            found["amount_options"] = amounts  # "500 or 600": ambiguous, the dialogue asks which
    account = _ACCOUNT_RE.search(text)
    card = _CARD_RE.search(text)
    if account:
        found["account"] = account.group(1)
    if card:
        found["card"] = card.group(1)
    if not account and not card:
        ending = _ENDING_RE.search(text) or _LAST4_RE.match(text)
        if ending:
            found["ending"] = ending.group(1)
    payee = parse_payee(text)
    if payee:
        found["payee"] = payee
    when = parse_date(text, today=today)
    if when:
        found["date"] = when
    reference = _REFERENCE_RE.search(text)
    if reference:
        found["reference"] = (reference.group(1) or reference.group(2)).upper()
    period = parse_period(text)
    if period:
        found["period"] = period
    loan = _LOAN_TYPE_RE.search(text)
    if loan:
        kind = loan.group(1).lower()
        found["loan_type"] = _LOAN_TYPES.get(kind, kind)
    tenure = parse_tenure(text)
    if tenure is not None:
        found["tenure"] = tenure
    merchant = _MERCHANT_RE.search(text)
    if merchant and merchant.group(1).lower() not in _PAYEE_STOP:
        found["merchant"] = merchant.group(1).strip(" .,")[:80]
    either = _PAYEE_OR_RE.search(text)
    if either and not {either.group(1).lower(), either.group(2).lower()} & _PAYEE_STOP:
        found["payee_options"] = [either.group(1), either.group(2)]
    return found


def parse_tenure(text: str) -> int | None:
    """A repayment tenure in months (``24 months``, ``3 years``), or None."""
    match = _TENURE_RE.search(text)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    months = value * 12 if unit.startswith("y") else value
    return months if 1 <= months <= 600 else None


def parse_amounts(text: str) -> list[float]:
    """Every distinct amount in ``text``, in order ("500 or 600" and "₹500 or 600" both name two).

    Currency-marked amounts and bare numbers are merged by position: a bare
    number counts unless it lies inside a marked amount, what precedes it says
    it is not an amount (an account ending, a reference), or what follows it
    is a unit of time or count ("in 2 days").
    """
    found: list[tuple[int, float]] = []
    marked_spans: list[tuple[int, int]] = []
    for match in _AMOUNT_RE.finditer(text):
        value = parse_amount(match.group(0))
        if value is not None:
            found.append((match.start(), value))
            marked_spans.append(match.span())
    for match in _BARE_IN_TEXT_RE.finditer(text):
        start, end = match.span()
        if any(start < span_end and end > span_start for span_start, span_end in marked_spans):
            continue
        before = text[max(0, start - 24) : start]
        if _NOT_AMOUNT_BEFORE_RE.search(before) or _NOT_AMOUNT_AFTER_RE.match(text, end):
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            found.append((start, value))
    amounts: list[float] = []
    for _, value in sorted(found, key=lambda item: item[0]):
        if value not in amounts:
            amounts.append(value)
    return amounts


# ── Recognition ───────────────────────────────────────────────────────────────


@dataclass
class Match:
    intent: Intent
    confidence: float
    hits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"intent": self.intent.name, "confidence": round(self.confidence, 3), "hits": len(self.hits)}


def recognise(text: str) -> list[Match]:
    """Every intent the text matches, best first, with a confidence from the weights of its matched patterns."""
    matches: list[Match] = []
    for intent in CATALOGUE:
        score = 0.0
        hits: list[str] = []
        for pattern, weight in intent.patterns:
            if re.search(pattern, text, re.I):
                score += weight
                hits.append(pattern)
        if hits:
            matches.append(Match(intent, min(MAX_CONFIDENCE, score), hits))
    matches.sort(key=lambda m: (-m.confidence, CATALOGUE.index(m.intent)))
    return matches


def split_requests(text: str) -> list[str]:
    """A turn's separate requests (``block my card and transfer 500 to Ravi``), when a joiner separates two intents."""
    parts = [part.strip(" ,.") for part in re.split(r"\b(?:and then|and also|and|then|also|;)\b", text, flags=re.I)]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return [text]
    intents = [m[0].intent.name for part in parts if (m := recognise(part)) and m[0].confidence >= MIN_CONFIDENCE]
    return parts if len(set(intents)) > 1 else [text]
