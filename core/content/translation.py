# SPDX-License-Identifier: Apache-2.0
"""Document translation across Indian languages, with the checks a bank needs before it trusts a translation.

The model translates; the service checks, deterministically, that every
figure of the source is still there, that the glossary was applied, that the
terms to keep verbatim were kept, and that the translation is actually in
the target script. An optional back-translation (a second model call) gives
an overlap score with the source so a reviewer can see how much came through.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.content import services
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source

LANGUAGES: dict[str, dict[str, Any]] = {
    "en": {"name": "English", "script": "Latin", "ranges": ((0x0041, 0x024F),)},
    "hi": {"name": "Hindi", "script": "Devanagari", "ranges": ((0x0900, 0x097F),)},
    "mr": {"name": "Marathi", "script": "Devanagari", "ranges": ((0x0900, 0x097F),)},
    "bn": {"name": "Bengali", "script": "Bengali", "ranges": ((0x0980, 0x09FF),)},
    "as": {"name": "Assamese", "script": "Bengali", "ranges": ((0x0980, 0x09FF),)},
    "gu": {"name": "Gujarati", "script": "Gujarati", "ranges": ((0x0A80, 0x0AFF),)},
    "pa": {"name": "Punjabi", "script": "Gurmukhi", "ranges": ((0x0A00, 0x0A7F),)},
    "or": {"name": "Odia", "script": "Odia", "ranges": ((0x0B00, 0x0B7F),)},
    "ta": {"name": "Tamil", "script": "Tamil", "ranges": ((0x0B80, 0x0BFF),)},
    "te": {"name": "Telugu", "script": "Telugu", "ranges": ((0x0C00, 0x0C7F),)},
    "kn": {"name": "Kannada", "script": "Kannada", "ranges": ((0x0C80, 0x0CFF),)},
    "ml": {"name": "Malayalam", "script": "Malayalam", "ranges": ((0x0D00, 0x0D7F),)},
    "ur": {"name": "Urdu", "script": "Arabic", "ranges": ((0x0600, 0x06FF), (0x0750, 0x077F))},
}
LANGUAGE_CODES = tuple(LANGUAGES)
SCRIPT_FLOOR = 0.5  # the share of letters that must be in the target script
MAX_BATCH = 20
# The output of a translation grows with its input, so the input bound follows the completion budget:
# Indian scripts can take up to about two tokens per source character, plus the JSON around the answer.
COMPLETION_BUDGET = 8_000
TEXT_LIMIT = 4_000

Register = Literal["formal", "neutral"]
TextFormat = Literal["plain", "markdown"]


class GlossaryEntry(BaseModel):
    model_config = {"extra": "forbid"}

    term: str = Field(..., min_length=1, max_length=120)
    translation: str = Field(..., min_length=1, max_length=200)


class TranslateIn(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1, max_length=TEXT_LIMIT)
    target_language: str = Field(..., min_length=2, max_length=5)
    source_language: str = Field("auto", min_length=2, max_length=5)
    glossary: list[GlossaryEntry] = Field(default_factory=list, max_length=50)
    preserve: list[str] = Field(default_factory=list, max_length=50)
    register: Register = "formal"
    format: TextFormat = "plain"
    verify: bool = False


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["translation"],
    "properties": {
        "translation": {"type": "string", "minLength": 1, "maxLength": 4 * TEXT_LIMIT},
        "detected_source_language": {"type": ["string", "null"], "maxLength": 5},
        "notes": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 300}},
    },
}

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _alternation(words: list[str], *, latin_boundary: bool = True) -> str:
    """A regex alternation of ``words``, longest first; a Latin word may not be part of a longer word."""
    parts = []
    for word in sorted({unicodedata.normalize("NFC", w) for w in words}, key=len, reverse=True):
        escaped = re.escape(word)
        if latin_boundary and word.isascii() and word[0].isalpha():
            escaped = f"(?<![a-z]){escaped}"
        if latin_boundary and word.isascii() and word[-1].isalpha():
            escaped = f"{escaped}(?![a-z])"
        parts.append(escaped)
    return "|".join(parts)


# The magnitude words of the supported languages and what each multiplies by.
_MAGNITUDES: dict[int, list[str]] = {
    1_000: [
        "thousand",
        "k",
        "हज़ार",
        "हजार",
        "হাজার",
        "હજાર",
        "ਹਜ਼ਾਰ",
        "ਹਜਾਰ",
        "ହଜାର",
        "ஆயிரம்",
        "వేలు",
        "వెయ్యి",
        "ಸಾವಿರ",
        "ആയിരം",
        "ہزار",
    ],
    100_000: [
        "lakhs",
        "lakh",
        "lacs",
        "lac",
        "लाख",
        "লাখ",
        "লক্ষ",
        "લાખ",
        "ਲੱਖ",
        "ଲକ୍ଷ",
        "லட்சம்",
        "லட்ச",
        "లక్షలు",
        "లక్ష",
        "ಲಕ್ಷ",
        "ലക്ഷം",
        "ലക്ഷ",
        "لاکھ",
    ],
    1_000_000: ["million", "mn"],
    10_000_000: [
        "crores",
        "crore",
        "cr",
        "करोड़",
        "करोड",
        "कोटी",
        "কোটি",
        "કરોડ",
        "ਕਰੋੜ",
        "କୋଟି",
        "கோடி",
        "కోట్లు",
        "కోటి",
        "ಕೋಟಿ",
        "കോടി",
        "کروڑ",
    ],
    1_000_000_000: ["billion", "bn"],
}
_MAGNITUDE_OF = {
    unicodedata.normalize("NFC", word).lower(): factor for factor, words in _MAGNITUDES.items() for word in words
}
_CURRENCY_BEFORE = ["₹", "rs.", "rs", "inr", "रु.", "रु", "रू.", "रू", "ரூ.", "రూ.", "ರೂ.", "രൂ."]
_CURRENCY_AFTER = [
    "rupees",
    "rupee",
    "inr",
    "रुपये",
    "रुपए",
    "रुपया",
    "रुपयों",
    "টাকা",
    "রুপি",
    "રૂપિયા",
    "ਰੁਪਏ",
    "ଟଙ୍କା",
    "ரூபாய்",
    "రూపాయలు",
    "రూపాయి",
    "ರೂಪಾಯಿ",
    "രൂപ",
    "روپے",
    "روپیہ",
]
_PERCENT = [
    "%",
    "percent",
    "per cent",
    "pct",
    "प्रतिशत",
    "फ़ीसदी",
    "फीसदी",
    "टक्के",
    "टक्का",
    "শতাংশ",
    "ટકા",
    "ਪ੍ਰਤੀਸ਼ਤ",
    "ପ୍ରତିଶତ",
    "சதவீதம்",
    "శాతం",
    "ಶೇಕಡಾ",
    "ശതമാനം",
    "فیصد",
]
_PERCENT_WORDS = {unicodedata.normalize("NFC", w).lower() for w in _PERCENT}
_FIGURE_RE = re.compile(
    r"(?P<date>\b[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}\b|\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b)"
    rf"|(?:(?P<before>{_alternation(_CURRENCY_BEFORE)})\s?)?"
    r"(?P<number>(?<![0-9,])(?<![0-9]\.)[0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)"
    rf"(?:\s?(?P<magnitude>{_alternation([w for words in _MAGNITUDES.values() for w in words])}))?"
    rf"(?:\s?(?P<after>{_alternation(_PERCENT + _CURRENCY_AFTER)}))?",
    re.I,
)


def _ascii_digits(text: str) -> str:
    """The text with the digits of every script (Devanagari, Bengali, Tamil and the rest) written as 0-9."""
    return "".join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in text)


def figures(text: str) -> dict[str, str]:
    """The figures of ``text``: a canonical key (currency, full value with its magnitude, percent) to the text.

    ``₹5 lakh``, ``₹5 crore`` and a bare ``5`` are three different figures; ``₹5 lakh`` and ``₹500,000``
    are the same amount. Dates are kept as written.
    """
    found: dict[str, str] = {}
    for match in _FIGURE_RE.finditer(_ascii_digits(unicodedata.normalize("NFC", text))):
        if match.group("date"):
            key = match.group("date")
        else:
            try:
                value = Decimal(match.group("number").replace(",", ""))
            except InvalidOperation:  # pragma: no cover - the pattern only admits digits
                continue
            magnitude = (match.group("magnitude") or "").lower()
            value *= _MAGNITUDE_OF.get(magnitude, 1)
            after = (match.group("after") or "").lower()
            amount = format(value.normalize(), "f")
            if after in _PERCENT_WORDS:
                key = f"{amount}%"
            elif match.group("before") or after:
                key = f"INR {amount}"
            else:
                key = amount
        found.setdefault(key, match.group(0).strip())
    return found


def language_name(code: str) -> str:
    return LANGUAGES.get(code, {}).get("name", code)


def check_language(code: str, *, allow_auto: bool = False) -> str:
    code = code.lower().strip()
    if allow_auto and code == "auto":
        return code
    if code not in LANGUAGES:
        raise services.ContentError(
            422, "language_unsupported", f"language is one of {', '.join(LANGUAGE_CODES)} (got {code!r})"
        )
    return code


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in ranges)


def script_share(text: str, code: str, *, ignore: list[str] | None = None) -> float:
    """The share of letters in ``text`` that are in the language's script, ignoring the terms kept verbatim."""
    body = text
    for term in ignore or []:
        body = re.sub(re.escape(term), " ", body, flags=re.I)
    letters = [ch for ch in body if unicodedata.category(ch).startswith("L")]
    if not letters:
        return 0.0
    ranges = LANGUAGES[code]["ranges"]
    return sum(1 for ch in letters if _in_ranges(ch, ranges)) / len(letters)


def word_overlap(a: str, b: str) -> float:
    """Jaccard overlap of the content words of two texts (a back-translation against its source)."""
    left = {w.lower() for w in _WORD_RE.findall(a)}
    right = {w.lower() for w in _WORD_RE.findall(b)}
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def messages(payload: TranslateIn, sources: list[Source]) -> list[dict[str, str]]:
    target = check_language(payload.target_language)
    source = check_language(payload.source_language, allow_auto=True)
    glossary = "\n".join(f"- {g.term} -> {g.translation}" for g in payload.glossary) or "- none"
    preserve = ", ".join(payload.preserve) or "none"
    system = (
        "You translate bank documents. Translate faithfully: keep every number, amount, date, percentage and "
        "condition exactly, keep the meaning and the structure, add nothing and leave nothing out. Use the "
        f"glossary for the terms it names. Keep these verbatim, untranslated: {preserve}. Answer with one JSON "
        "object and nothing else: {translation, detected_source_language (ISO code), notes: [anything the "
        "reviewer should know, such as a term with no good equivalent]}."
    )
    user = (
        f"Translate from {'the detected language' if source == 'auto' else language_name(source)} to "
        f"{language_name(target)} ({LANGUAGES[target]['script']} script), {payload.register} register, "
        f"{'keeping Markdown structure' if payload.format == 'markdown' else 'plain text'}.\n"
        f"Glossary:\n{glossary}\n\nText:\n{payload.text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def finish(payload: TranslateIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    target = check_language(payload.target_language)
    translation = str(answer.get("translation") or "")
    lowered_source = payload.text.lower()
    lowered_out = translation.lower()
    glossary_misses = [
        g.term
        for g in payload.glossary
        if g.term.lower() in lowered_source and g.translation.lower() not in lowered_out
    ]
    preserve_misses = [
        term for term in payload.preserve if term.lower() in lowered_source and term.lower() not in lowered_out
    ]
    kept = figures(translation)
    missing_facts = [shown for key, shown in figures(payload.text).items() if key not in kept]
    share = script_share(
        translation,
        target,
        ignore=payload.preserve + [g.translation for g in payload.glossary if g.translation.isascii()],
    )
    script_ok = share >= SCRIPT_FLOOR
    checks = {
        "facts_preserved": not missing_facts,
        "missing_facts": missing_facts,
        "glossary_applied": not glossary_misses,
        "glossary_misses": glossary_misses,
        "preserved": not preserve_misses,
        "preserve_misses": preserve_misses,
        "script_ok": script_ok,
        "script_share": round(share, 2),
        "output_transformed": False,
    }
    return {
        "translation": translation,
        "target_language": target,
        "source_language": payload.source_language if payload.source_language != "auto" else None,
        "detected_source_language": answer.get("detected_source_language"),
        "notes": [str(n) for n in (answer.get("notes") or [])],
        "checks": checks,
        "trusted": all(
            (checks["facts_preserved"], checks["glossary_applied"], checks["preserved"], checks["script_ok"])
        ),
    }


def rendered(output: dict[str, Any]) -> str:
    return str(output.get("translation") or "")


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    """An output guardrail changed the translation after the checks ran, so the checks no longer hold."""
    checks = {**(output.get("checks") or {}), "output_transformed": True}
    return {**output, "translation": text, "checks": checks, "trusted": False}


def transformed(output: dict[str, Any]) -> dict[str, Any]:
    """The output guardrails changed some field after the checks ran: the checks no longer hold."""
    return apply_text(output, str(output.get("translation") or ""))


async def resolve_sources(tenant_id: uuid.UUID, payload: TranslateIn, domains: list[str] | None) -> list[Source]:
    check_language(payload.target_language)
    check_language(payload.source_language, allow_auto=True)
    return [Source(id="source", title="Source text", text=payload.text, origin="inline")]


async def back_translate(
    tenant_id: uuid.UUID, payload: TranslateIn, output: dict[str, Any], *, complete: Any = None
) -> dict:
    """A second model call back to the source language; the overlap with the source is what a reviewer sees."""
    source = (
        payload.source_language
        if payload.source_language != "auto"
        else (output.get("detected_source_language") or "en")
    )
    source = check_language(str(source), allow_auto=False) if str(source) in LANGUAGES else "en"
    messages_back = [
        {
            "role": "system",
            "content": "You translate faithfully. Answer with one JSON object and nothing else: {translation}.",
        },
        {
            "role": "user",
            "content": f"Translate to {language_name(source)}:\n{output.get('translation') or ''}",
        },
    ]
    answer, usage = await services.ask_model(
        tenant_id,
        messages_back,
        {"type": "object", "required": ["translation"], "properties": {"translation": {"type": "string"}}},
        complete=complete,
        max_tokens=COMPLETION_BUDGET,
    )
    back = str(answer.get("translation") or "")
    overlap = word_overlap(payload.text, back)
    return {"back_translation": back, "overlap": round(overlap, 2), "model": usage}


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "hindi-notice",
        "input": "Translate to Hindi: The branch will remain closed on 2 October 2026. ATMs stay open. "
        "Keep 'NetBanking' verbatim.",
        "contains": ["NetBanking", "2026"],
    },
    {
        "id": "tamil-charges",
        "input": "Translate to Tamil: A charge of ₹150 applies when the quarterly average balance is below ₹10,000.",
        "contains": ["150", "10,000"],
    },
    {
        "id": "glossary-term",
        "input": "Translate to Marathi with the glossary term 'overdraft' -> 'ओव्हरड्राफ्ट': Your overdraft "
        "limit is ₹50,000.",
        "contains": ["ओव्हरड्राफ्ट", "50,000"],
    },
]

SERVICE = services.register(
    Service(
        name="translate",
        title="Document translation",
        description="A translation across Indian languages with every figure, glossary term and verbatim term "
        "checked, the target script verified, and an optional back-translation overlap for review.",
        input_model=TranslateIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=True),
        dataset_name="content: document translation",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
        max_tokens=COMPLETION_BUDGET,
        after_output_guard=transformed,
    )
)
