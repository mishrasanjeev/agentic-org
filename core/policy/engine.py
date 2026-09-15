# SPDX-License-Identifier: Apache-2.0
"""Deterministic policy evaluation over a case's evidence fields.

``evaluate(policy, evidence)`` reads the dotted paths the policy references
from a plain nested mapping, decides which rules fire and returns a
:class:`~core.policy.types.PolicyResult`. It never calls a model, never touches
the network or the database, and returns the same result for the same policy
and evidence regardless of mapping order, process or hash seed.

Semantics (``docs/policies/authoring.md`` is the reference):

* A leaf comparison is true, false or *unresolved*. It is unresolved when the
  path is missing (absent, or ``null``) or holds a value the operator cannot
  compare (wrong type, a list or mapping, a non-finite number). ``exists`` and
  ``missing`` are never unresolved.
* ``all``/``any``/``not`` combine these with three-valued (Kleene) logic, so
  missing evidence cannot be negated into a pass.
* A rule fires when its condition is true **or unresolved**. Firing only ever
  raises the tier and adds score, so missing evidence moves a case towards the
  stricter tier and is flagged ``indeterminate``; it never silently passes.
* Tier is the most severe fired rule's tier (``low`` if none fired), raised
  further by the policy's ``score_thresholds``. Score is the sum of fired rule
  scores, capped at 100.
* Reasons are ordered by tier, most severe first, then by rule order in the file.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import structlog
from prometheus_client import Counter

from core.policy.types import (
    ENGINE_VERSION,
    MAX_SAFE_INTEGER,
    MAX_SCORE,
    AllOf,
    AnyOf,
    Compare,
    Condition,
    JsonValue,
    Not,
    Operator,
    Policy,
    PolicyReason,
    PolicyResult,
    Tier,
)

_policy_evaluations_total = Counter(
    "agenticorg_policy_evaluations_total",
    "Policy evaluations, by resulting tier and policy status",
    ["tier", "policy_status"],
)

logger = structlog.get_logger()

_MISSING = object()
# The evidence mapping raised while a path was read. Treated as unusable
# evidence by every operator, including exists and missing, so the rule fires.
_UNREADABLE = object()


def _kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "number" if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else None
    if isinstance(value, float):
        return "number" if math.isfinite(value) else None
    if isinstance(value, str):
        return "string"
    return None


def _read(evidence: Mapping[str, Any], segments: tuple[str, ...]) -> Any:
    node: Any = evidence
    try:
        for segment in segments:
            if not isinstance(node, Mapping) or segment not in node:
                return _MISSING
            node = node[segment]
    # enterprise-gate: broad-except-ok reason=unreadable-evidence-fires-rule-towards-stricter-tier
    except Exception as exc:
        # Evidence that cannot be read must not stop evaluation or pass: the
        # path is recorded as unusable and every rule reading it fires.
        logger.warning("policy_evidence_unreadable", path=".".join(segments), error=type(exc).__name__)
        return _UNREADABLE
    return _MISSING if node is None else node


def _snapshot(value: Any) -> JsonValue:
    if value is _MISSING:
        return None
    if value is _UNREADABLE:
        return {"non_scalar": "unreadable"}
    if _kind(value) is not None:
        return value
    if isinstance(value, float):
        return {"non_scalar": "non_finite_number"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"non_scalar": "integer_out_of_range"}
    if isinstance(value, Mapping):
        return {"non_scalar": "mapping"}
    if isinstance(value, list | tuple):
        return {"non_scalar": "list"}
    return {"non_scalar": "other"}


class _Evaluation:
    """Per-rule evaluation state: values are read once per case, unresolved paths collected."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self.values = values
        self.unresolved: set[str] = set()

    def truth(self, condition: Condition) -> bool | None:
        if isinstance(condition, Compare):
            return self._compare(condition)
        if isinstance(condition, Not):
            inner = self.truth(condition.item)
            return None if inner is None else not inner
        # Evaluate every item (no short-circuit) so unresolved paths are
        # reported completely and identically on every run.
        results = [self.truth(item) for item in condition.items]
        if isinstance(condition, AllOf):
            if False in results:
                return False
            return None if None in results else True
        if isinstance(condition, AnyOf):
            if True in results:
                return True
            return None if None in results else False
        raise TypeError(f"not a compiled condition: {type(condition).__name__}")  # unreachable for loaded policies

    def _compare(self, leaf: Compare) -> bool | None:
        value = self.values[leaf.path]
        if value is _UNREADABLE:
            self.unresolved.add(leaf.path)
            return None
        if leaf.op is Operator.EXISTS:
            return value is not _MISSING
        if leaf.op is Operator.MISSING:
            return value is _MISSING
        kind = None if value is _MISSING else _kind(value)
        if kind is None:
            self.unresolved.add(leaf.path)
            return None

        operand = leaf.operand
        if leaf.op in (Operator.IN, Operator.NOT_IN):
            items = operand if isinstance(operand, tuple) else (operand,)
            if kind != _kind(items[0]):
                self.unresolved.add(leaf.path)
                return None
            found = any(value == item for item in items)
            return found if leaf.op is Operator.IN else not found

        if kind != _kind(operand):
            self.unresolved.add(leaf.path)
            return None
        if leaf.op is Operator.EQ:
            return bool(value == operand)
        if leaf.op is Operator.NE:
            return bool(value != operand)
        if leaf.op is Operator.GT:
            return bool(value > operand)
        if leaf.op is Operator.GTE:
            return bool(value >= operand)
        if leaf.op is Operator.LT:
            return bool(value < operand)
        if leaf.op is Operator.LTE:
            return bool(value <= operand)
        raise TypeError(f"unhandled operator {leaf.op}")  # unreachable: operators are validated at load


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate(policy: Policy, evidence: Mapping[str, Any]) -> PolicyResult:
    """Evaluate ``policy`` over ``evidence``, a nested mapping of case evidence fields."""
    if not isinstance(policy, Policy):
        raise TypeError("policy must be a Policy returned by core.policy.load_policy")
    if not isinstance(evidence, Mapping):
        raise TypeError(f"evidence must be a mapping, got {type(evidence).__name__}")

    values: dict[str, Any] = {}
    for rule in policy.rules:
        _index_paths(rule.when, evidence, values)

    inputs = {path: _snapshot(values[path]) for path in policy.referenced_paths}
    missing = tuple(path for path in policy.referenced_paths if values[path] is _MISSING)
    invalid = tuple(
        path for path in policy.referenced_paths if values[path] is not _MISSING and _kind(values[path]) is None
    )

    fired: list[tuple[int, PolicyReason]] = []
    for index, rule in enumerate(policy.rules):
        state = _Evaluation(values)
        truth = state.truth(rule.when)
        if truth is False:
            continue
        fired.append(
            (
                index,
                PolicyReason(
                    rule_id=rule.rule_id,
                    tier=rule.effect.tier,
                    score=rule.effect.score,
                    reason=rule.effect.reason,
                    indeterminate=truth is None,
                    unresolved_paths=tuple(sorted(state.unresolved)),
                ),
            )
        )

    fired.sort(key=lambda item: (-item[1].tier.rank, item[0]))
    reasons = tuple(reason for _, reason in fired)
    score = min(MAX_SCORE, sum(reason.score for reason in reasons))

    tier = Tier.LOW
    for reason in reasons:
        if reason.tier.rank > tier.rank:
            tier = reason.tier
    tier_source = "rules"
    for threshold_tier, minimum in policy.score_thresholds:
        if score >= minimum and threshold_tier.rank > tier.rank:
            tier = threshold_tier
            tier_source = "score_threshold"

    _policy_evaluations_total.labels(tier=tier.value, policy_status=policy.status.value).inc()
    return PolicyResult(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_status=policy.status,
        policy_hash=policy.content_hash,
        reviewed_by=policy.reviewed_by,
        engine_version=ENGINE_VERSION,
        tier=tier,
        tier_source=tier_source,
        score=score,
        reasons=reasons,
        fired_rules=tuple(reason.rule_id for reason in reasons),
        inputs=inputs,
        missing_inputs=missing,
        invalid_inputs=invalid,
        inputs_hash=_canonical_hash(inputs),
    )


def _index_paths(condition: Condition, evidence: Mapping[str, Any], into: dict[str, Any]) -> None:
    if isinstance(condition, Compare):
        if condition.path not in into:
            into[condition.path] = _read(evidence, condition.segments)
    elif isinstance(condition, Not):
        _index_paths(condition.item, evidence, into)
    else:
        for item in condition.items:
            _index_paths(item, evidence, into)
