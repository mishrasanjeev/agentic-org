# SPDX-License-Identifier: Apache-2.0
"""Detectors the guardrail engine runs, and the transforms it applies to what they find.

Each detector reads a text and returns the spans it found with a kind and a
score. ``sensitive_data`` uses the platform's PII analyser when it is
installed and its regex recognisers otherwise, plus a card-number check; it
is the detector behind mask, redact and tokenise. ``toxicity`` is the
content-safety classifier with its keyword fallback. ``pattern`` is an
administrator's own list of regular expressions. ``injection`` scores the
phrasings by which a text tries to take over the model (instruction overrides,
system-prompt disclosure, persona switches, hidden characters), direct in a
message and indirect inside a retrieved document alike. ``output_policy``
checks an answer's shape: length, JSON, required keys, forbidden phrases,
links. ``grounding`` (``core.governance.guardrails.grounding``) checks an
answer's claims against the context the run retrieved.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import structlog

from core.governance.guardrails.grounding import GroundingDetector
from core.governance.guardrails.schema import Finding
from core.pii.redactor import _REGEX_FALLBACK_PATTERNS, PIIRedactor

logger = structlog.get_logger()

# The analyser's entity names, as the short kinds the regex recognisers use.
_ENTITY_KINDS: dict[str, str] = {
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "IN_AADHAAR": "AADHAAR",
    "IN_PAN": "PAN",
    "IN_UPI": "UPI",
    "IN_GSTIN": "GSTIN",
    "CREDIT_CARD": "CREDIT_CARD",
}
DEFAULT_ENTITIES: tuple[str, ...] = ("CREDIT_CARD", "AADHAAR", "PAN", "GSTIN", "EMAIL", "UPI", "PHONE")
_CARD_PATTERN = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?![ -]?\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def card_numbers(text: str) -> list[tuple[int, int]]:
    """Spans of digit runs that pass the Luhn check: payment card numbers, with spaces or dashes."""
    spans: list[tuple[int, int]] = []
    for match in _CARD_PATTERN.finditer(text):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            spans.append((match.start(), match.end()))
    return spans


class Detector(Protocol):
    name: str

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]: ...


class SensitiveDataDetector:
    name = "sensitive_data"

    def _analyser_spans(self, text: str, entities: list[str]) -> list[tuple[int, int, str, float]] | None:
        # Only an analyser the platform has already initialised is used: building
        # one cold (loading its language model) would not fit a detector's
        # time budget, and the regex recognisers cover the call meanwhile.
        redactor = PIIRedactor._instance
        if redactor is None or getattr(redactor, "_analyzer", None) is None:
            return None
        wanted = [name for name, kind in _ENTITY_KINDS.items() if kind in entities]
        spans = redactor.find_entities(text, wanted) if wanted else []
        return [(start, end, _ENTITY_KINDS.get(kind, kind), 1.0) for start, end, kind in spans]

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        entities = [str(e).upper() for e in (options.get("entities") or DEFAULT_ENTITIES)]
        spans = self._analyser_spans(text, entities)
        if spans is None:
            spans = [
                (match.start(), match.end(), kind, 1.0)
                for kind, pattern in _REGEX_FALLBACK_PATTERNS
                if kind in entities
                for match in pattern.finditer(text)
            ]
        if "CREDIT_CARD" in entities:
            spans += [(start, end, "CREDIT_CARD", 1.0) for start, end in card_numbers(text)]
        findings = [
            Finding(self.name, kind, start, end, score, f"{kind.lower()} at {start}-{end}")
            for start, end, kind, score in spans
        ]
        return _without_overlaps(findings)


class ToxicityDetector:
    name = "toxicity"

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        from core.content_safety.checker import _check_toxicity

        score, issues = _check_toxicity(text, threshold=threshold)
        if not issues:
            return []
        return [Finding(self.name, "toxicity", 0, len(text), float(score), issue.get("detail", "")) for issue in issues]


class PatternDetector:
    name = "pattern"

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        from core.config import settings

        flags = re.IGNORECASE if options.get("ignore_case", True) else 0
        kind = str(options.get("kind") or "pattern")
        # Patterns are validated against catastrophic shapes when the rule is
        # written; the scan is bounded in length too, and the engine runs it
        # under a time budget off the event loop.
        scanned = text[: settings.guardrails_pattern_max_chars]
        findings = [
            Finding(self.name, kind, match.start(), match.end(), 1.0, f"matched {pattern!r}")
            for pattern in options.get("patterns") or []
            for match in re.compile(pattern, flags).finditer(scanned)
        ]
        return _without_overlaps(findings)


# Phrasings by which a text tries to take over the model, with how sure each
# one makes the detector. Weights are the score; a rule's threshold decides.
_INJECTION_SIGNALS: tuple[tuple[str, float, str], ...] = (
    (
        r"\bignore\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|rules|directions)\b",
        1.0,
        "instruction_override",
    ),
    (
        r"\bdisregard\s+(?:the\s+|your\s+|all\s+|any\s+)?(?:system|previous|prior|above|earlier)\s+(?:prompt|instructions?|rules|message)\b",
        1.0,
        "instruction_override",
    ),
    (r"\bforget\s+(?:everything|all)\s+(?:you|that|above|before)\b", 0.9, "instruction_override"),
    (
        r"\boverride\s+(?:your|the|all)\s+(?:safety|security|guardrails?|polic(?:y|ies)|rules|restrictions)\b",
        0.9,
        "instruction_override",
    ),
    (
        r"\b(?:reveal|print|show|repeat|output|display|leak)\s+(?:me\s+)?(?:your|the)\s+(?:system|hidden|secret|initial|original)\s+(?:prompt|instructions?|message)\b",
        0.9,
        "prompt_disclosure",
    ),
    (r"\byou\s+are\s+now\s+(?:a|an|in|the)\b", 0.7, "persona_switch"),
    (r"\b(?:act|behave|pretend)\s+as\s+(?:if\s+you\s+(?:are|were)|a|an|though)\b", 0.6, "persona_switch"),
    (r"\b(?:developer|god|admin|unrestricted|jailbreak)\s+mode\b", 0.8, "jailbreak"),
    (r"\bdo\s+anything\s+now\b|\bDAN\b", 0.8, "jailbreak"),
    (
        r"\b(?:begin|end|start|stop)\s+(?:of\s+)?(?:system|instruction|developer)\s+(?:prompt|message|block|section)\b",
        0.8,
        "fake_system_block",
    ),
    (
        r"<\|?\s*(?:system|im_start|im_end|endoftext)\s*\|?>|\[(?:system|inst)\]|###\s*(?:system|instruction)s?\b",
        0.8,
        "fake_system_block",
    ),
    (r"\bfrom\s+now\s+on\b.{0,80}\b(?:only|always|never)\b", 0.5, "standing_order"),
    (r"\brespond\s+only\s+(?:with|in)\b", 0.4, "standing_order"),
    (
        r"\bthis\s+is\s+(?:an?\s+)?(?:authorised|authorized|official)\s+(?:instruction|request|override)\b",
        0.7,
        "false_authority",
    ),
)
_INJECTION_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), weight, kind) for pattern, weight, kind in _INJECTION_SIGNALS
)
# Invisible characters hide text from a reader while a model still sees it.
_HIDDEN_CHARACTERS = re.compile(r"[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\ufeff]+")


class InjectionDetector:
    name = "injection"

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        findings = [
            Finding(self.name, kind, match.start(), match.end(), weight, f"{kind}: {match.group(0)[:60]!r}")
            for pattern, weight, kind in _INJECTION_PATTERNS
            for match in pattern.finditer(text)
        ]
        findings += [
            Finding(self.name, "hidden_text", match.start(), match.end(), 0.6, "invisible characters")
            for match in _HIDDEN_CHARACTERS.finditer(text)
        ]
        for extra in options.get("patterns") or []:
            findings += [
                Finding(self.name, "custom", match.start(), match.end(), 1.0, f"matched {extra!r}")
                for match in re.compile(extra, re.IGNORECASE).finditer(text)
            ]
        return _without_overlaps(findings)


_URL = re.compile(r"\bhttps?://[^\s<>\"']+|\bwww\.[^\s<>\"']+", re.IGNORECASE)


class OutputPolicyDetector:
    """An answer's shape. Findings cover the whole text (structure) or the offending spans (phrases, links)."""

    name = "output_policy"

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        findings: list[Finding] = []
        max_length = options.get("max_length")
        if max_length is not None and len(text) > int(max_length):
            findings.append(
                Finding(
                    self.name,
                    "too_long",
                    int(max_length),
                    len(text),
                    1.0,
                    f"{len(text)} characters, limit {max_length}",
                )
            )
        parsed: Any = None
        if options.get("require_json") or options.get("required_keys"):
            try:
                parsed = json.loads(text.strip())
            except (ValueError, TypeError):
                parsed = None
            if not isinstance(parsed, dict | list):
                findings.append(
                    Finding(self.name, "not_json", 0, len(text), 1.0, "the answer is not a JSON object or array")
                )
        for key in options.get("required_keys") or []:
            if isinstance(parsed, dict) and key not in parsed:
                findings.append(
                    Finding(self.name, f"missing_key:{key}", 0, len(text), 1.0, f"required key {key!r} is missing")
                )
        lowered = text.lower()
        for phrase in options.get("forbidden_phrases") or []:
            needle = phrase.lower()
            start = lowered.find(needle)
            while start >= 0:
                findings.append(
                    Finding(self.name, "forbidden_phrase", start, start + len(needle), 1.0, f"contains {phrase!r}")
                )
                start = lowered.find(needle, start + len(needle))
        if options.get("no_urls"):
            findings += [
                Finding(self.name, "url", m.start(), m.end(), 1.0, "contains a link") for m in _URL.finditer(text)
            ]
        return sorted(findings, key=lambda f: (f.start, f.end))


REGISTRY: dict[str, Detector] = {
    "sensitive_data": SensitiveDataDetector(),
    "toxicity": ToxicityDetector(),
    "pattern": PatternDetector(),
    "injection": InjectionDetector(),
    "output_policy": OutputPolicyDetector(),
    "grounding": GroundingDetector(),
}


def _without_overlaps(findings: list[Finding]) -> list[Finding]:
    """Keep the widest span where spans overlap, in document order."""
    kept: list[Finding] = []
    for finding in sorted(findings, key=lambda f: (-(f.end - f.start), f.start)):
        if any(finding.start < other.end and finding.end > other.start for other in kept):
            continue
        kept.append(finding)
    return sorted(kept, key=lambda f: f.start)


def apply_transform(
    text: str, findings: list[Finding], action: str, counters: dict[str, int] | None = None
) -> tuple[str, dict[str, str]]:
    """Mask, redact or tokenise the spans the findings cover; spans are replaced last first."""
    counters = counters if counters is not None else {}
    token_map: dict[str, str] = {}
    spans = [f for f in _without_overlaps(findings) if f.end > f.start]
    replacements: list[tuple[Finding, str]] = []
    for finding in spans:
        if action == "mask":
            replacement = "*" * (finding.end - finding.start)
        elif action == "redact":
            replacement = f"<{finding.kind}>"
        elif action == "tokenise":
            counters[finding.kind] = counters.get(finding.kind, 0) + 1
            replacement = f"<{finding.kind}_{counters[finding.kind]}>"
            token_map[replacement] = text[finding.start : finding.end]
        else:
            raise ValueError(f"not a transform: {action}")
        replacements.append((finding, replacement))
    for finding, replacement in sorted(replacements, key=lambda item: item[0].start, reverse=True):
        text = text[: finding.start] + replacement + text[finding.end :]
    return text, token_map
