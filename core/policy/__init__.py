# SPDX-License-Identifier: Apache-2.0
"""Deterministic case policy engine.

Versioned YAML policies are loaded strictly (every error is found at load) and
evaluated over a plain nested mapping of case evidence fields, producing a
tier, a score and ordered reasons. No model is involved; a model's confidence
is never an input. See ``docs/policies/authoring.md`` and
``docs/adr/0011-policy-over-confidence.md``.

This is unrelated to ``core.approvals.policy_engine``, which routes an approval
through its approver steps once a human review has been requested.
"""

from __future__ import annotations

from core.policy.engine import evaluate
from core.policy.loader import EXAMPLES_DIR, load_policies, load_policy, load_policy_bytes
from core.policy.types import (
    DEFAULT_TIER_SCORE,
    ENGINE_VERSION,
    MAX_SCORE,
    Operator,
    Policy,
    PolicyLoadError,
    PolicyLoadReason,
    PolicyReason,
    PolicyResult,
    PolicyStatus,
    Tier,
)

__all__ = [
    "DEFAULT_TIER_SCORE",
    "ENGINE_VERSION",
    "EXAMPLES_DIR",
    "MAX_SCORE",
    "Operator",
    "Policy",
    "PolicyLoadError",
    "PolicyLoadReason",
    "PolicyReason",
    "PolicyResult",
    "PolicyStatus",
    "Tier",
    "evaluate",
    "load_policies",
    "load_policy",
    "load_policy_bytes",
]
