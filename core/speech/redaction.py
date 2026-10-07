# SPDX-License-Identifier: Apache-2.0
"""Spoken sensitive data: card numbers, one-time codes, CVVs and PINs found in timed words, then cut from the transcript and the audio.

Speech comes as digits, number words ("four one two three"), "double" and
"triple", and spelled separators, so the finder first reads every run of
consecutive spoken digits and then judges each run: a run of 13 to 19
digits that passes the Luhn check is a card number; a run of 4 to 8
digits spoken after a one-time-code cue is a code; three or four digits
after a CVV cue are a CVV; four to six after a PIN cue are a PIN. A span
carries the words it covers and their time range, never the digits. The
transcript is rewritten with a marker in place of the words (a card keeps
its last four digits), the audio is silenced over the span with a little
padding on each side, and what was removed is recorded as kinds and
times only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.speech.audio import Recording

KINDS: tuple[str, ...] = ("card", "otp", "cvv", "pin")
DEFAULT_KINDS: tuple[str, ...] = KINDS
PADDING_SECONDS = 0.15
NUMBER_WORDS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "nought": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
MULTIPLIERS = {"double": 2, "triple": 3}
OTP_CUES = (
    "otp",
    "one time password",
    "one-time password",
    "one time code",
    "one-time code",
    "verification code",
    "the code is",
    "code is",
    "passcode",
    "security code sent",
    "sms code",
)
CVV_CUES = ("cvv", "cvc", "security code", "three digits on the back", "card verification")
PIN_CUES = ("pin", "pin is", "pin number", "atm pin", "mpin", "tpin")
CARD_CUES = ("card number", "debit card", "credit card", "card ending", "sixteen digit", "16 digit", "card is")
CUE_WINDOW_WORDS = 8
_DIGITS_RE = re.compile(r"^\d+$")


@dataclass
class Span:
    kind: str
    first: int  # index of the first word
    last: int  # index of the last word, inclusive
    start: float
    end: float
    digits: int
    keep_last: int = 0
    words: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "first_word": self.first,
            "last_word": self.last,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "digits": self.digits,
            "words": len(self.words),
        }


def luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def spoken_digits(token: str, *, multiplier: int = 1) -> str | None:
    """The digits a spoken token stands for, or None when it is not a digit, a number word or a digit group."""
    cleaned = token.strip().lower().strip(".,;:!?\"'()")
    if not cleaned:
        return None
    compact = cleaned.replace("-", "").replace(" ", "")
    if _DIGITS_RE.match(compact):
        return compact * multiplier
    if cleaned in NUMBER_WORDS:
        return NUMBER_WORDS[cleaned] * multiplier
    return None


def digit_runs(words: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    """Runs of consecutive spoken digits: (first index, last index, digits)."""
    runs: list[tuple[int, int, str]] = []
    index = 0
    while index < len(words):
        text = str(words[index].get("text") or "")
        lowered = text.strip().lower().strip(".,;:!?\"'()")
        multiplier = MULTIPLIERS.get(lowered)
        if multiplier and index + 1 < len(words):
            following = spoken_digits(str(words[index + 1].get("text") or ""), multiplier=multiplier)
            if following is not None:
                start = index
                digits = following
                index += 2
                while index < len(words):
                    more_multiplier = MULTIPLIERS.get(str(words[index].get("text") or "").strip().lower())
                    if more_multiplier and index + 1 < len(words):
                        extra = spoken_digits(str(words[index + 1].get("text") or ""), multiplier=more_multiplier)
                        if extra is not None:
                            digits += extra
                            index += 2
                            continue
                    piece = spoken_digits(str(words[index].get("text") or ""))
                    if piece is None:
                        break
                    digits += piece
                    index += 1
                runs.append((start, index - 1, digits))
                continue
        piece = spoken_digits(text)
        if piece is None:
            index += 1
            continue
        start = index
        digits = piece
        index += 1
        while index < len(words):
            more_multiplier = MULTIPLIERS.get(str(words[index].get("text") or "").strip().lower())
            if more_multiplier and index + 1 < len(words):
                extra = spoken_digits(str(words[index + 1].get("text") or ""), multiplier=more_multiplier)
                if extra is not None:
                    digits += extra
                    index += 2
                    continue
            piece = spoken_digits(str(words[index].get("text") or ""))
            if piece is None:
                break
            digits += piece
            index += 1
        runs.append((start, index - 1, digits))
    return runs


def _context(words: list[dict[str, Any]], first: int, *, window: int = CUE_WINDOW_WORDS) -> str:
    return " ".join(str(w.get("text") or "") for w in words[max(0, first - window) : first]).lower()


def _cued(context: str, cues: tuple[str, ...]) -> bool:
    return any(cue in context for cue in cues)


def find_spans(words: list[dict[str, Any]], *, kinds: tuple[str, ...] | list[str] = DEFAULT_KINDS) -> list[Span]:
    """The sensitive spans among timed words, by kind, each with its time range; the digits are not kept."""
    wanted = set(kinds)
    spans: list[Span] = []
    for first, last, digits in digit_runs(words):
        context = _context(words, first)
        kind: str | None = None
        keep_last = 0
        length = len(digits)
        if "card" in wanted and 13 <= length <= 19 and (luhn(digits) or _cued(context, CARD_CUES)):
            kind, keep_last = "card", 4
        elif "otp" in wanted and 4 <= length <= 8 and _cued(context, OTP_CUES):
            kind = "otp"
        elif "cvv" in wanted and 3 <= length <= 4 and _cued(context, CVV_CUES):
            kind = "cvv"
        elif "pin" in wanted and 4 <= length <= 6 and _cued(context, PIN_CUES):
            kind = "pin"
        if kind is None:
            continue
        spans.append(
            Span(
                kind=kind,
                first=first,
                last=last,
                start=float(words[first].get("start") or 0.0),
                end=float(words[last].get("end") or words[last].get("start") or 0.0),
                digits=length,
                keep_last=keep_last,
                words=[str(w.get("text") or "") for w in words[first : last + 1]],
            )
        )
    return spans


def marker(span: Span) -> str:
    if span.kind == "card" and span.keep_last:
        tail = "".join(spoken_digits(w) or "" for w in span.words)[-span.keep_last :]
        return f"[CARD ****{tail}]"
    return f"[{span.kind.upper()} REDACTED]"


def redact_words(words: list[dict[str, Any]], spans: list[Span]) -> list[dict[str, Any]]:
    """The words with each span collapsed into one marker word carrying the span's time range."""
    covered: dict[int, Span] = {}
    for span in spans:
        for index in range(span.first, span.last + 1):
            covered[index] = span
    out: list[dict[str, Any]] = []
    index = 0
    while index < len(words):
        span = covered.get(index)
        if span is None:
            out.append(dict(words[index]))
            index += 1
            continue
        out.append(
            {
                **words[span.first],
                "text": marker(span),
                "start": round(span.start, 3),
                "end": round(span.end, 3),
                "redacted": span.kind,
            }
        )
        index = span.last + 1
    return out


def redact_transcript(
    transcript: dict[str, Any], *, kinds: tuple[str, ...] | list[str] = DEFAULT_KINDS
) -> tuple[dict[str, Any], list[Span]]:
    """The transcript with the sensitive spans cut out of its words, turns and text, and the spans found."""
    from core.speech.transcribe import Word, transcript_of

    words = list(transcript.get("words") or [])
    spans = find_spans(words, kinds=kinds)
    if not spans:
        return transcript, []
    cleaned = redact_words(words, spans)
    rebuilt = transcript_of(
        [
            Word(
                text=str(w["text"]),
                start=float(w["start"]),
                end=float(w["end"]),
                confidence=float(w.get("confidence", 1.0)),
                speaker=w.get("speaker"),
            )
            for w in cleaned
        ],
        [],
    )
    rebuilt["redacted"] = [span.to_dict() for span in spans]
    return rebuilt, spans


def redact_text(text: str, *, kinds: tuple[str, ...] | list[str] = DEFAULT_KINDS) -> tuple[str, list[dict[str, Any]]]:
    """Plain text with its spoken sensitive data cut out (used for live turns), and what was cut as kinds."""
    tokens = text.split()
    words = [{"text": token, "start": float(i), "end": float(i)} for i, token in enumerate(tokens)]
    spans = find_spans(words, kinds=kinds)
    if not spans:
        return text, []
    return " ".join(str(w["text"]) for w in redact_words(words, spans)), [
        {"kind": s.kind, "digits": s.digits} for s in spans
    ]


def silence(recording: Recording, spans: list[Span], *, padding: float = PADDING_SECONDS) -> Recording:
    """The recording with every span's samples set to silence on every channel, padded a little either side."""
    channels = [np.array(channel, dtype=np.float32, copy=True) for channel in recording.channels]
    rate = recording.sample_rate
    for span in spans:
        first = max(0, int((span.start - padding) * rate))
        last = int((span.end + padding) * rate)
        for channel in channels:
            channel[first:last] = 0.0
    return Recording(sample_rate=rate, channels=channels)
