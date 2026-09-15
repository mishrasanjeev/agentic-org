# SPDX-License-Identifier: Apache-2.0
"""Types shared by the policy loader and engine.

A :class:`Policy` is immutable once loaded: every condition is compiled into
frozen dataclasses at load time, so evaluation cannot fail on policy shape and
cannot be changed by the code that evaluates it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

# Bumped when the meaning of a policy file changes (operators, missing-evidence
# semantics, scoring, reason ordering). Recorded in every result so a result can
# be re-derived with the engine semantics that produced it.
ENGINE_VERSION = "1"

type Scalar = str | int | float | bool
type JsonValue = Scalar | None | dict[str, Any] | list[Any]


class Tier(enum.StrEnum):
    """Policy tiers, least to most severe."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK: dict[Tier, int] = {Tier.LOW: 0, Tier.MEDIUM: 1, Tier.HIGH: 2, Tier.BLOCKED: 3}

# Score a fired rule contributes when its effect does not declare one.
DEFAULT_TIER_SCORE: dict[Tier, int] = {Tier.LOW: 0, Tier.MEDIUM: 20, Tier.HIGH: 50, Tier.BLOCKED: 100}
MAX_SCORE = 100
# Integers outside +/-2**53 (the range JSON consumers agree on) are refused as
# operands and treated as unusable evidence.
MAX_SAFE_INTEGER = 2**53


class PolicyStatus(enum.StrEnum):
    EXAMPLE = "example"
    PRODUCTION = "production"


class Operator(enum.StrEnum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EXISTS = "exists"
    MISSING = "missing"


class PolicyLoadReason(enum.StrEnum):
    """Why a policy was refused at load. Stable; used as a metric label."""

    FILE_UNREADABLE = "policy_file_unreadable"
    TOO_LARGE = "policy_too_large"
    ENCODING_INVALID = "policy_encoding_invalid"
    YAML_INVALID = "policy_yaml_invalid"
    DUPLICATE_KEY = "policy_duplicate_key"
    YAML_ALIAS = "policy_yaml_alias_forbidden"
    MISSING_FIELD = "policy_missing_field"
    UNKNOWN_KEY = "policy_unknown_key"
    INVALID_VALUE = "policy_invalid_value"
    INVALID_VERSION = "policy_invalid_version"
    INVALID_CONDITION = "policy_invalid_condition"
    UNKNOWN_OPERATOR = "policy_unknown_operator"
    INVALID_PATH = "policy_invalid_path"
    INVALID_OPERAND = "policy_invalid_operand"
    DUPLICATE_RULE_ID = "policy_duplicate_rule_id"
    LIMIT_EXCEEDED = "policy_limit_exceeded"
    PRODUCTION_UNREVIEWED = "policy_production_unreviewed"
    NOT_PRODUCTION = "policy_not_production"
    DIRECTORY_INVALID = "policy_directory_invalid"
    DUPLICATE_POLICY = "policy_duplicate_policy_id"


class PolicyLoadError(ValueError):
    """A policy file was refused. Raised only at load, never by evaluation."""

    def __init__(self, reason: PolicyLoadReason, detail: str, *, source: str, location: str = "") -> None:
        self.reason = reason
        self.detail = detail
        self.source = source
        self.location = location
        where = f" at {location}" if location else ""
        super().__init__(f"{source}: {reason.value}{where}: {detail}")


# ── Compiled conditions ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Compare:
    """``{<dotted.path>: {<operator>: <operand>}}``."""

    path: str
    segments: tuple[str, ...]
    op: Operator
    operand: Scalar | tuple[Scalar, ...]


@dataclass(frozen=True, slots=True)
class AllOf:
    items: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class AnyOf:
    items: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class Not:
    item: Condition


type Condition = Compare | AllOf | AnyOf | Not


@dataclass(frozen=True, slots=True)
class Effect:
    tier: Tier
    reason: str
    score: int


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    when: Condition
    effect: Effect
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Policy:
    policy_id: str
    version: str
    status: PolicyStatus
    reviewed_by: str | None
    description: str | None
    rules: tuple[Rule, ...]
    # (tier, minimum score) pairs in ascending tier order; empty when the
    # policy does not escalate by score.
    score_thresholds: tuple[tuple[Tier, int], ...]
    # ``sha256:<hex>`` over the exact bytes that were loaded.
    content_hash: str
    source: str
    # Every evidence path any rule reads, sorted.
    referenced_paths: tuple[str, ...]


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PolicyReason:
    rule_id: str
    tier: Tier
    score: int
    reason: str
    # True when the rule fired because evidence it needed was missing or
    # unusable, rather than because the evidence matched.
    indeterminate: bool
    # Evidence paths read by this rule that were missing or unusable, sorted.
    unresolved_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "tier": self.tier.value,
            "score": self.score,
            "reason": self.reason,
            "indeterminate": self.indeterminate,
            "unresolved_paths": list(self.unresolved_paths),
        }


@dataclass(frozen=True, slots=True)
class PolicyResult:
    policy_id: str
    policy_version: str
    policy_status: PolicyStatus
    policy_hash: str
    reviewed_by: str | None
    engine_version: str
    tier: Tier
    # "rules" when the tier is the most severe fired rule's tier (or ``low``
    # when nothing fired); "score_threshold" when accumulated score raised it.
    tier_source: str
    score: int
    reasons: tuple[PolicyReason, ...]
    fired_rules: tuple[str, ...]
    # Every referenced path mapped to the value read: a scalar, ``None`` when
    # missing, or ``{"non_scalar": "mapping" | "list" | "other"}``.
    inputs: dict[str, JsonValue]
    missing_inputs: tuple[str, ...]
    invalid_inputs: tuple[str, ...]
    # ``sha256:<hex>`` over the canonical JSON of ``inputs``.
    inputs_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_status": self.policy_status.value,
            "policy_hash": self.policy_hash,
            "reviewed_by": self.reviewed_by,
            "engine_version": self.engine_version,
            "tier": self.tier.value,
            "tier_source": self.tier_source,
            "score": self.score,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "fired_rules": list(self.fired_rules),
            "inputs": dict(self.inputs),
            "missing_inputs": list(self.missing_inputs),
            "invalid_inputs": list(self.invalid_inputs),
            "inputs_hash": self.inputs_hash,
        }
