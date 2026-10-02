# SPDX-License-Identifier: Apache-2.0
"""Detectors the guardrail engine runs, and the transforms it applies to what they find.

Each detector reads a text and returns the spans it found with a kind and a
score. ``sensitive_data`` uses the platform's PII analyser when it is
installed and its regex recognisers otherwise, plus a card-number check; it
is the detector behind mask, redact and tokenise. ``toxicity`` is the
content-safety classifier with its keyword fallback. ``pattern`` is an
administrator's own list of regular expressions. Prompt-injection, output
policy and grounding detectors join in the next parts of the package.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

import structlog

from core.governance.guardrails.schema import Finding

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
        try:
            from core.pii.redactor import PIIRedactor

            redactor = PIIRedactor()
        # enterprise-gate: broad-except-ok reason=analyser-unavailable-degrades-to-regex-recognisers-logged
        except Exception as exc:
            logger.debug("guardrail_pii_analyser_unavailable", error_type=type(exc).__name__)
            return None
        if getattr(redactor, "_analyzer", None) is None:
            return None
        wanted = [name for name, kind in _ENTITY_KINDS.items() if kind in entities]
        spans = redactor.find_entities(text, wanted) if wanted else []
        return [(start, end, _ENTITY_KINDS.get(kind, kind), 1.0) for start, end, kind in spans]

    def detect(self, text: str, options: dict[str, Any], *, threshold: float) -> list[Finding]:
        entities = [str(e).upper() for e in (options.get("entities") or DEFAULT_ENTITIES)]
        spans = self._analyser_spans(text, entities)
        if spans is None:
            from core.pii.redactor import _REGEX_FALLBACK_PATTERNS

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
        flags = re.IGNORECASE if options.get("ignore_case", True) else 0
        kind = str(options.get("kind") or "pattern")
        findings = [
            Finding(self.name, kind, match.start(), match.end(), 1.0, f"matched {pattern!r}")
            for pattern in options.get("patterns") or []
            for match in re.compile(pattern, flags).finditer(text)
        ]
        return _without_overlaps(findings)


REGISTRY: dict[str, Detector] = {
    "sensitive_data": SensitiveDataDetector(),
    "toxicity": ToxicityDetector(),
    "pattern": PatternDetector(),
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
