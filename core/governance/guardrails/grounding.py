# SPDX-License-Identifier: Apache-2.0
"""The grounding checker: is each claim of an answer supported by the context the run retrieved?

A deterministic, lexical check, so it runs inside a detector's time budget
with no model call and gives the same verdict for the same texts. The answer
is split into sentences; a sentence with enough content words is a claim.
A claim's support is the share of its content words that also occur in the
context (the tool results and retrieved documents of the run and, unless the
rule says otherwise, what the user wrote). A claim below the rule's
``min_support`` is reported as ``unsupported_claim`` with a score of one
minus its support. A number in a claim that occurs nowhere in the context is
reported as ``unsupported_number`` whatever the claim's support: an invented
amount, date or rate is the failure that matters most in a regulated answer.

What it is not: it does not judge meaning. A claim that reuses the context's
words to say the opposite passes, and a faithful paraphrase in other words is
flagged. It is a floor that catches answers written without the context, not
a proof of faithfulness; model-graded faithfulness belongs to the evaluation
framework.

Findings carry positions, kinds and a support figure; never the text.
"""

from __future__ import annotations

import re
from typing import Any

from core.governance.guardrails.schema import Finding

NAME = "grounding"
DEFAULT_MIN_SUPPORT = 0.5
DEFAULT_MIN_CLAIM_WORDS = 4
# The context is bounded so one very large tool result cannot exhaust the detector's time budget.
MAX_CONTEXT_CHARS = 400_000
UNSUPPORTED_NUMBER_SCORE = 0.9

# A sentence ends at a terminator followed by white space, or at a line break; the point in 3.5 ends nothing.
_BOUNDARY = re.compile(r"(?<=[.!?\u0964])\s+|\n+")
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*%?|\w+", re.UNICODE)
_NUMBER = re.compile(r"\d")
_STOPWORDS = frozenset(
    """
    a about above after again all also am an and any are as at be because been before being below between both but by
    can could did do does doing down during each few for from further had has have having he her here hers him his how
    i if in into is it its itself just me more most my no nor not now of off on once only or other our ours out over
    own same she should so some such than that the their theirs them then there these they this those through to too
    under until up very was we were what when where which while who whom why will with would you your yours
    """.split()
)


def _normalise(token: str) -> str:
    token = token.lower()
    if _NUMBER.search(token):
        # 1,250.00 and 1250 are the same amount; a trailing percent sign is kept as part of the figure.
        percent = token.endswith("%")
        digits = token.rstrip("%").replace(",", "")
        if "." in digits:
            digits = digits.rstrip("0").rstrip(".")
        return digits + ("%" if percent else "")
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def content_tokens(text: str) -> list[str]:
    """The words and figures of ``text`` that carry content: lower-cased, stopwords dropped, plurals folded."""
    out: list[str] = []
    for match in _TOKEN.finditer(text):
        raw = match.group(0)
        if raw.lower() in _STOPWORDS:
            continue
        token = _normalise(raw)
        if token and token not in _STOPWORDS:
            out.append(token)
    return out


def sentences(text: str) -> list[tuple[int, str]]:
    """The sentences of ``text`` with where each starts, surrounding white space trimmed."""
    out: list[tuple[int, str]] = []
    position = 0
    for boundary in [*_BOUNDARY.finditer(text), None]:
        stop = boundary.start() if boundary is not None else len(text)
        piece = text[position:stop]
        trimmed = piece.strip()
        if trimmed:
            out.append((position + (len(piece) - len(piece.lstrip())), trimmed))
        position = boundary.end() if boundary is not None else stop
    return out


def context_vocabulary(context: list[str]) -> frozenset[str]:
    """Every content token of the context, read up to the context bound."""
    seen: set[str] = set()
    budget = MAX_CONTEXT_CHARS
    for item in context:
        if budget <= 0:
            break
        piece = item[:budget]
        budget -= len(piece)
        seen.update(content_tokens(piece))
    return frozenset(seen)


def check(
    text: str,
    context: list[str] | None,
    *,
    min_support: float = DEFAULT_MIN_SUPPORT,
    min_claim_words: int = DEFAULT_MIN_CLAIM_WORDS,
    require_context: bool = False,
) -> list[Finding]:
    """The answer's claims the context does not support, in document order."""
    usable = [item for item in (context or []) if isinstance(item, str) and item.strip()]
    if not usable:
        if require_context and text.strip():
            return [Finding(NAME, "no_context", 0, len(text), 1.0, "the answer was given with no retrieved context")]
        return []
    vocabulary = context_vocabulary(usable)
    findings: list[Finding] = []
    for begin, sentence in sentences(text):
        if sentence.endswith("?"):
            continue
        tokens = content_tokens(sentence)
        if len(tokens) < min_claim_words:
            continue
        supported = sum(1 for token in tokens if token in vocabulary)
        support = supported / len(tokens)
        start, end = begin, begin + len(sentence)
        missing_numbers = sorted({t for t in tokens if _NUMBER.search(t) and t not in vocabulary})
        if missing_numbers:
            score = max(UNSUPPORTED_NUMBER_SCORE, 1.0 - support)
            findings.append(Finding(NAME, "unsupported_number", start, end, round(score, 4), f"support {support:.2f}"))
        elif support < min_support:
            findings.append(
                Finding(NAME, "unsupported_claim", start, end, round(1.0 - support, 4), f"support {support:.2f}")
            )
    return findings


class GroundingDetector:
    """The detector the engine runs; the run's context arrives beside the rule's options."""

    name = NAME
    uses_context = True

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        return check(
            text,
            options.get("_context"),
            min_support=float(options.get("min_support", DEFAULT_MIN_SUPPORT)),
            min_claim_words=int(options.get("min_claim_words", DEFAULT_MIN_CLAIM_WORDS)),
            require_context=bool(options.get("require_context", False)),
        )
