# SPDX-License-Identifier: Apache-2.0
"""Runtime guardrails: configurable input, retrieval, output and action checks.

See :mod:`core.governance.guardrails.engine` for the evaluation pipeline and
``docs/governance/guardrails.md`` for the policy model.
"""

from core.governance.guardrails.engine import (
    ERROR_CODE,
    FLAG_KEY,
    GuardrailBlocked,
    GuardrailResult,
    active_rules,
    delete_rule,
    enforcing,
    evaluate,
    set_rule,
    update_rule,
)
from core.governance.guardrails.schema import ACTIONS, DETECTORS, STAGES, Finding, Outcome, Rule, validate_rule_fields

__all__ = [
    "ACTIONS",
    "DETECTORS",
    "ERROR_CODE",
    "FLAG_KEY",
    "STAGES",
    "Finding",
    "GuardrailBlocked",
    "GuardrailResult",
    "Outcome",
    "Rule",
    "active_rules",
    "delete_rule",
    "enforcing",
    "evaluate",
    "set_rule",
    "update_rule",
    "validate_rule_fields",
]
