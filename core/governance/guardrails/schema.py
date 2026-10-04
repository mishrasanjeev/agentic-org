# SPDX-License-Identifier: Apache-2.0
"""The guardrail policy model: rules, findings, outcomes and the result of one evaluation.

A rule says, for one stage of a model or tool call (``input``, ``retrieval``,
``output``, ``action``), which detector runs and what happens when it finds
something at or above the rule's threshold: ``flag`` records the finding,
``mask``, ``redact`` and ``tokenise`` transform the text before it travels on,
``block`` refuses the stage. A rule may be narrowed to an agent, a use case or
a risk tier; a rule with none of those applies to every call at its stage.
Every matching rule applies, in priority order; a block wins over a transform.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

STAGES: tuple[str, ...] = ("input", "retrieval", "output", "action")
DETECTORS: tuple[str, ...] = ("sensitive_data", "toxicity", "pattern", "injection", "output_policy", "grounding")
# The options each detector takes; anything else is refused at the boundary.
DETECTOR_OPTIONS: dict[str, tuple[str, ...]] = {
    "sensitive_data": ("entities",),
    "toxicity": (),
    "pattern": ("patterns", "kind", "ignore_case"),
    "injection": ("patterns",),
    "output_policy": ("max_length", "require_json", "required_keys", "forbidden_phrases", "no_urls"),
    "grounding": ("min_support", "min_claim_words", "require_context", "include_user_input"),
}
# Detectors whose findings describe the whole text rather than spans: they flag or block, never transform.
STRUCTURAL_DETECTORS: tuple[str, ...] = ("toxicity", "output_policy", "grounding")
# Detectors that judge an answer and so run at the output stage only.
OUTPUT_ONLY_DETECTORS: tuple[str, ...] = ("output_policy", "grounding")
MAX_PHRASES = 64
MAX_PHRASE_LENGTH = 200
SENSITIVE_ENTITIES: tuple[str, ...] = ("CREDIT_CARD", "AADHAAR", "PAN", "GSTIN", "EMAIL", "UPI", "PHONE")
MAX_PATTERN_LENGTH = 512
MAX_PATTERNS = 32
# Nested or adjacent unbounded quantifiers and backreferences are the shapes
# that backtrack catastrophically; a pattern carrying one is refused.
_UNSAFE_PATTERN_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\([^()]*[+*][^()]*\)\s*[+*{]"),  # (x+)+, (x*)*, (x+){n,}
    re.compile(r"[+*]\s*[+*]"),  # a++, a*+ (possessive forms are not supported by re either)
    re.compile(r"\\[1-9]"),  # backreferences
)
ACTIONS: tuple[str, ...] = ("flag", "mask", "redact", "tokenise", "block")
TRANSFORMS: tuple[str, ...] = ("mask", "redact", "tokenise")
RISK_TIERS: tuple[str, ...] = ("low", "medium", "high", "critical")
MATCH_FIELDS: tuple[str, ...] = ("agent_id", "use_case", "risk_tier")
ERROR_CODE = "E1016"


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    stage: str
    detector: str
    action: str = "flag"
    priority: int = 100
    enabled: bool = True
    threshold: float = 0.5
    agent_id: str | None = None
    use_case: str | None = None
    risk_tier: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["options"] = dict(self.options)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(**{**data, "options": dict(data.get("options") or {})})

    def matches(self, stage: str, *, agent_id: str | None, use_case: str | None, risk_tier: str | None) -> bool:
        if self.stage != stage:
            return False
        actual = {"agent_id": agent_id, "use_case": use_case, "risk_tier": risk_tier}
        for name in MATCH_FIELDS:
            wanted = getattr(self, name)
            if wanted is None:
                continue
            value = actual[name]
            if value is None or str(value).strip().lower() != str(wanted).strip().lower():
                return False
        return True


@dataclass(frozen=True)
class Finding:
    """One thing a detector found: a span of the text, what kind, how sure."""

    detector: str
    kind: str
    start: int
    end: int
    score: float = 1.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Outcome:
    """What one rule did with the text at its stage."""

    rule_id: str
    rule_name: str
    stage: str
    detector: str
    action: str
    findings: int
    score: float
    kinds: list[str]
    # ``applied`` says the action took effect (enforcement on, or a dry run);
    # off, the rule only recorded what it would have done.
    applied: bool
    blocked: bool = False
    transformed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GuardrailResult:
    """The text after the stage's rules, whether the stage may continue, and what each rule did."""

    stage: str
    text: str
    allowed: bool
    enforced: bool
    correlation_id: str
    outcomes: list[Outcome] = field(default_factory=list)
    token_map: dict[str, str] = field(default_factory=dict)
    # Rules whose detector gave no answer (it raised, timed out or is unknown): the rule id, the detector and why.
    unverifiable: list[dict[str, str]] = field(default_factory=list)

    @property
    def findings(self) -> int:
        return sum(outcome.findings for outcome in self.outcomes)

    @property
    def flagged(self) -> bool:
        return bool(self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "text": self.text,
            "allowed": self.allowed,
            "enforced": self.enforced,
            "correlation_id": self.correlation_id,
            "findings": self.findings,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "token_map": dict(self.token_map),
            "unverifiable": [dict(item) for item in self.unverifiable],
        }


class GuardrailBlocked(RuntimeError):  # noqa: N818 - surface name used in error payloads
    """Raised when a rule with action ``block`` matched at a stage with enforcement on."""

    def __init__(
        self, reason: str, *, stage: str, correlation_id: str, rule_id: str | None, rule_name: str | None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage
        self.correlation_id = correlation_id
        self.rule_id = rule_id
        self.rule_name = rule_name

    @property
    def code(self) -> str:
        return ERROR_CODE

    def to_error(self) -> dict[str, Any]:
        return {
            "error": {"code": ERROR_CODE, "message": self.reason},
            "guardrail": {
                "stage": self.stage,
                "correlation_id": self.correlation_id,
                "rule_id": self.rule_id,
                "rule_name": self.rule_name,
            },
        }


def safe_pattern(text: str) -> str:
    """A pattern that compiles, stays short and carries no shape known to backtrack catastrophically."""
    if len(text) > MAX_PATTERN_LENGTH:
        raise ValueError(f"a pattern is at most {MAX_PATTERN_LENGTH} characters")
    for shape in _UNSAFE_PATTERN_SHAPES:
        if shape.search(text):
            raise ValueError(f"pattern {text!r} nests or repeats unbounded quantifiers, or uses a backreference")
    try:
        re.compile(text)
    except re.error as exc:
        raise ValueError(f"pattern {text!r} does not compile: {exc}") from None
    return text


def _clean_options(detector: str, raw: Any) -> dict[str, Any]:
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("options must be an object")
    options = dict(raw or {})
    allowed = DETECTOR_OPTIONS.get(detector, ())
    unknown = sorted(set(options) - set(allowed))
    if unknown:
        raise ValueError(f"options not taken by {detector}: {', '.join(unknown)}")
    out: dict[str, Any] = {}
    if detector == "sensitive_data":
        entities = options.get("entities")
        if entities is not None:
            if not isinstance(entities, list):
                raise ValueError("entities must be a list of entity types")
            names = [e.strip().upper() for e in entities if isinstance(e, str) and e.strip()]
            if not names:
                raise ValueError("entities must name at least one entity type when given")
            if len(names) != len(entities):
                raise ValueError("entities must be a list of entity type names")
            unsupported = sorted(set(names) - set(SENSITIVE_ENTITIES))
            if unsupported:
                raise ValueError(f"entities must be among {', '.join(SENSITIVE_ENTITIES)}")
            out["entities"] = sorted(set(names))
    elif detector == "injection":
        patterns = options.get("patterns")
        if patterns is not None:
            if not isinstance(patterns, list) or len(patterns) > MAX_PATTERNS:
                raise ValueError(f"patterns must be a list of at most {MAX_PATTERNS} expressions")
            cleaned_extra: list[str] = []
            for item in patterns:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("a pattern must be a non-empty string")
                cleaned_extra.append(safe_pattern(item.strip()))
            out["patterns"] = cleaned_extra
    elif detector == "output_policy":
        max_length = options.get("max_length")
        if max_length is not None:
            if isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 1:
                raise ValueError("max_length must be a positive integer")
            out["max_length"] = max_length
        for flag in ("require_json", "no_urls"):
            if flag in options:
                if not isinstance(options[flag], bool):
                    raise ValueError(f"{flag} must be true or false")
                out[flag] = options[flag]
        for name in ("required_keys", "forbidden_phrases"):
            items = options.get(name)
            if items is None:
                continue
            if not isinstance(items, list) or not items or len(items) > MAX_PHRASES:
                raise ValueError(f"{name} must be a non-empty list of at most {MAX_PHRASES} strings")
            cleaned_items: list[str] = []
            for item in items:
                if not isinstance(item, str) or not item.strip() or len(item) > MAX_PHRASE_LENGTH:
                    raise ValueError(
                        f"each entry of {name} is a non-empty string of at most {MAX_PHRASE_LENGTH} characters"
                    )
                cleaned_items.append(item.strip())
            out[name] = cleaned_items
        if "required_keys" in out and not out.get("require_json", False):
            out["require_json"] = True
        if not out:
            raise ValueError("an output_policy rule needs at least one check")
    elif detector == "grounding":
        min_support = options.get("min_support")
        if min_support is not None:
            if isinstance(min_support, bool) or not isinstance(min_support, int | float) or not 0 < min_support <= 1:
                raise ValueError("min_support is a fraction above 0 and at most 1")
            out["min_support"] = float(min_support)
        min_claim_words = options.get("min_claim_words")
        if min_claim_words is not None:
            if (
                isinstance(min_claim_words, bool)
                or not isinstance(min_claim_words, int)
                or not 1 <= min_claim_words <= 50
            ):
                raise ValueError("min_claim_words is a whole number between 1 and 50")
            out["min_claim_words"] = min_claim_words
        for flag in ("require_context", "include_user_input"):
            if flag in options:
                if not isinstance(options[flag], bool):
                    raise ValueError(f"{flag} must be true or false")
                out[flag] = options[flag]
    elif detector == "pattern":
        patterns = options.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            raise ValueError("a pattern rule needs a non-empty list of patterns")
        if len(patterns) > MAX_PATTERNS:
            raise ValueError(f"a pattern rule takes at most {MAX_PATTERNS} patterns")
        cleaned_patterns: list[str] = []
        for item in patterns:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("a pattern must be a non-empty string")
            cleaned_patterns.append(safe_pattern(item.strip()))
        out["patterns"] = cleaned_patterns
        kind = options.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise ValueError("kind must be a string")
        out["kind"] = _clean(kind) or "pattern"
        out["ignore_case"] = bool(options.get("ignore_case", True))
    return out


def validate_rule_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalise and check a rule's fields; raise ``ValueError`` on an unusable rule."""
    out: dict[str, Any] = {}
    name = _clean(fields.get("name"))
    if not name:
        raise ValueError("a rule needs a name")
    out["name"] = name
    stage = (_clean(fields.get("stage")) or "").lower()
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    out["stage"] = stage
    detector = (_clean(fields.get("detector")) or "").lower()
    if detector not in DETECTORS:
        raise ValueError(f"detector must be one of {', '.join(DETECTORS)}")
    out["detector"] = detector
    action = (_clean(fields.get("action")) or "flag").lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    if action == "tokenise" and detector != "sensitive_data":
        raise ValueError("tokenise applies to sensitive data only")
    if action in TRANSFORMS and detector in STRUCTURAL_DETECTORS:
        raise ValueError(f"{detector} findings describe the whole text; the rule flags or blocks")
    if action in TRANSFORMS and stage == "action":
        raise ValueError("an action-stage rule flags or blocks; tool arguments are never rewritten")
    if detector in OUTPUT_ONLY_DETECTORS and stage != "output":
        raise ValueError(f"{detector} applies to the output stage")
    out["action"] = action
    threshold = fields.get("threshold", 0.5)
    if isinstance(threshold, bool) or not isinstance(threshold, int | float) or not 0 <= float(threshold) <= 1:
        raise ValueError("threshold is a fraction between 0 and 1")
    out["threshold"] = float(threshold)
    priority = fields.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        raise ValueError("priority must be a non-negative integer")
    out["priority"] = priority
    out["enabled"] = bool(fields.get("enabled", True))
    out["agent_id"] = _clean(fields.get("agent_id"))
    out["use_case"] = (_clean(fields.get("use_case")) or "").lower() or None
    risk_tier = (_clean(fields.get("risk_tier")) or "").lower() or None
    if risk_tier is not None and risk_tier not in RISK_TIERS:
        raise ValueError(f"risk_tier must be one of {', '.join(RISK_TIERS)}")
    out["risk_tier"] = risk_tier
    out["options"] = _clean_options(detector, fields.get("options"))
    out["reason"] = (fields.get("reason") or "").strip()
    return out


def new_rule_id() -> str:
    return str(uuid.uuid4())
