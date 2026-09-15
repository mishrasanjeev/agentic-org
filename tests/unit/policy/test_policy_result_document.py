# SPDX-License-Identifier: Apache-2.0
"""A PolicyResult renders as a schema-valid policy_result document naming the inputs each fired rule read."""

from __future__ import annotations

import pytest

from core.domain_schemas import validate
from core.policy import EXAMPLES_DIR, evaluate, load_policy
from core.policy.document import policy_result_document


def test_document_validates_and_lists_each_fired_rules_inputs() -> None:
    policy = load_policy(EXAMPLES_DIR / "business_onboarding_uk.yaml")
    evidence = {
        "verification": {"status": "dissolved", "registry_match": True, "overdue_filings": 0},
        "ownership": {"missing_owners": 1, "undeclared_owners": 0},
        "screening": {"unresolved_true_matches": 0, "unresolved_possible_matches": {"nested": 1}},
        "web_presence": {"activity_mismatch": False},
    }
    result = evaluate(policy, evidence)
    document = policy_result_document(policy, result)
    validate("policy_result", document)
    assert document["tier"] == result.tier.value and document["score"] == result.score
    assert document["inputs_digest"] == result.inputs_hash
    reasons = {reason["rule_id"]: reason for reason in document["reasons"]}
    assert [r["rule_id"] for r in document["reasons"]] == list(result.fired_rules)
    assert reasons["ownership_reconciled"]["inputs"] == {
        "ownership.missing_owners": 1,
        "ownership.undeclared_owners": 0,
    }
    assert reasons["screening_possible_match"]["inputs"] == {"screening.unresolved_possible_matches": None}
    assert document["policy"] == {
        "policy_id": "business_onboarding_uk",
        "version": "1.2.0",
        "example": True,
        "reviewed_by": None,
    }


def test_document_refuses_a_result_from_another_policy() -> None:
    uk = load_policy(EXAMPLES_DIR / "business_onboarding_uk.yaml")
    us = load_policy(EXAMPLES_DIR / "business_onboarding_us.yaml")
    with pytest.raises(ValueError, match="not produced by this policy"):
        policy_result_document(uk, evaluate(us, {}))
