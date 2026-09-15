# SPDX-License-Identifier: Apache-2.0
"""Render a :class:`~core.policy.types.PolicyResult` as a ``policy_result`` schema document.

The engine's result carries more than the published document (engine version, policy hash,
indeterminate flags). The document keeps what a reader of a memo, a case push or an evidence
package needs: the policy identity, the inputs digest, the score, the tier and, for every fired
rule, the evidence values that rule read. The full engine result is kept alongside in the case
record for re-derivation.
"""

from __future__ import annotations

from typing import Any

from core.policy.types import AllOf, AnyOf, Compare, Condition, Not, Policy, PolicyResult, PolicyStatus

SCHEMA_VERSION = "1.0.0"


def _paths(condition: Condition, into: set[str]) -> None:
    if isinstance(condition, Compare):
        into.add(condition.path)
    elif isinstance(condition, Not):
        _paths(condition.item, into)
    elif isinstance(condition, AllOf | AnyOf):
        for item in condition.items:
            _paths(item, into)


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    # Non-scalar snapshots ({"non_scalar": ...}) are unusable evidence; the document records null.
    return None


def policy_result_document(policy: Policy, result: PolicyResult) -> dict[str, Any]:
    """The ``policy_result`` document for ``result``, which must come from evaluating ``policy``."""
    if (policy.policy_id, policy.version, policy.content_hash) != (
        result.policy_id,
        result.policy_version,
        result.policy_hash,
    ):
        raise ValueError("result was not produced by this policy")
    rule_paths: dict[str, list[str]] = {}
    for rule in policy.rules:
        found: set[str] = set()
        _paths(rule.when, found)
        rule_paths[rule.rule_id] = sorted(found)
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "policy_id": result.policy_id,
            "version": result.policy_version,
            "example": result.policy_status is PolicyStatus.EXAMPLE,
            "reviewed_by": result.reviewed_by,
        },
        "inputs_digest": result.inputs_hash,
        "score": result.score,
        "tier": result.tier.value,
        "reasons": [
            {
                "rule_id": reason.rule_id,
                "tier": reason.tier.value,
                "reason": reason.reason,
                "score": reason.score,
                "inputs": {path: _scalar(result.inputs.get(path)) for path in rule_paths[reason.rule_id]},
            }
            for reason in result.reasons
        ],
    }
