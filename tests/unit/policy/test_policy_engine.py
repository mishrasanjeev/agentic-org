# SPDX-License-Identifier: Apache-2.0
"""A-5: policy evaluation semantics - operators, missing evidence, tiers, scoring, reasons."""

from __future__ import annotations

import copy
import json
import textwrap
from typing import Any

import pytest
from prometheus_client import REGISTRY

from core.policy import (
    ENGINE_VERSION,
    EXAMPLES_DIR,
    Policy,
    PolicyStatus,
    Tier,
    evaluate,
    load_policy,
    load_policy_bytes,
)

PRD_EXAMPLE = """\
policy: business_onboarding_uk
version: 1.2.0
rules:
  - id: registry_active
    when: {verification.status: {not_in: [active]}}
    effect: {tier: high, reason: "Registry status is not active"}
  - id: ownership_reconciled
    when: {ownership.missing_owners: {gt: 0}}
    effect: {tier: medium, reason: "Declared owners do not reconcile with the ownership graph"}
  - id: screening_clear
    when: {screening.unresolved_true_matches: {gt: 0}}
    effect: {tier: blocked, reason: "Unresolved true match"}
"""

CLEAN = {
    "verification": {"status": "active", "registry_match": True, "tax_id_match": True, "overdue_filings": 0},
    "ownership": {"missing_owners": 0, "undeclared_owners": 0},
    "screening": {"unresolved_true_matches": 0, "unresolved_possible_matches": 0},
    "web_presence": {"activity_mismatch": False},
}


def _policy(text: str) -> Policy:
    return load_policy_bytes(textwrap.dedent(text).encode("utf-8"))


def _one_rule(when: str, *, tier: str = "high") -> Policy:
    return _policy(
        f"policy: p\nversion: 1.0.0\nrules:\n  - {{id: r, when: {when}, effect: {{tier: {tier}, reason: r}}}}\n"
    )


def _with(evidence: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(evidence)
    head, _, leaf = path.rpartition(".")
    node = out
    for segment in head.split(".") if head else []:
        node = node.setdefault(segment, {})
    node[leaf] = value
    return out


# ── The PRD example ─────────────────────────────────────────────────────────


def test_clean_case_is_low_with_no_reasons() -> None:
    result = evaluate(_policy(PRD_EXAMPLE), CLEAN)
    assert result.tier is Tier.LOW
    assert result.score == 0
    assert result.reasons == ()
    assert result.fired_rules == ()
    assert result.tier_source == "rules"
    assert result.missing_inputs == ()
    assert result.invalid_inputs == ()


def test_each_prd_rule_sets_its_tier_and_reason() -> None:
    policy = _policy(PRD_EXAMPLE)
    inactive = evaluate(policy, _with(CLEAN, "verification.status", "dissolved"))
    assert (inactive.tier, inactive.fired_rules) == (Tier.HIGH, ("registry_active",))
    assert inactive.reasons[0].reason == "Registry status is not active"
    assert inactive.reasons[0].indeterminate is False

    owners = evaluate(policy, _with(CLEAN, "ownership.missing_owners", 2))
    assert (owners.tier, owners.fired_rules, owners.score) == (Tier.MEDIUM, ("ownership_reconciled",), 20)

    hit = evaluate(policy, _with(CLEAN, "screening.unresolved_true_matches", 1))
    assert (hit.tier, hit.fired_rules, hit.score) == (Tier.BLOCKED, ("screening_clear",), 100)


def test_result_identifies_the_policy_and_its_inputs() -> None:
    policy = _policy(PRD_EXAMPLE)
    result = evaluate(policy, CLEAN)
    assert result.policy_id == "business_onboarding_uk"
    assert result.policy_version == "1.2.0"
    assert result.policy_status is PolicyStatus.EXAMPLE
    assert result.policy_hash == policy.content_hash
    assert result.engine_version == ENGINE_VERSION
    assert result.inputs == {
        "ownership.missing_owners": 0,
        "screening.unresolved_true_matches": 0,
        "verification.status": "active",
    }
    assert result.inputs_hash.startswith("sha256:")
    payload = result.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert sorted(payload) == sorted(
        [
            "policy_id",
            "policy_version",
            "policy_status",
            "policy_hash",
            "reviewed_by",
            "engine_version",
            "tier",
            "tier_source",
            "score",
            "reasons",
            "fired_rules",
            "inputs",
            "missing_inputs",
            "invalid_inputs",
            "inputs_hash",
        ]
    )


def test_inputs_record_only_referenced_paths_and_mark_non_scalars() -> None:
    policy = _policy(PRD_EXAMPLE)
    evidence = {
        "verification": {"status": {"code": "active"}, "unrelated": "applicant free text"},
        "ownership": {"missing_owners": [1, 2]},
        "screening": {"unresolved_true_matches": float("nan")},
        "notes": "not referenced",
    }
    result = evaluate(policy, evidence)
    assert result.inputs == {
        "ownership.missing_owners": {"non_scalar": "list"},
        "screening.unresolved_true_matches": {"non_scalar": "non_finite_number"},
        "verification.status": {"non_scalar": "mapping"},
    }
    assert result.invalid_inputs == (
        "ownership.missing_owners",
        "screening.unresolved_true_matches",
        "verification.status",
    )
    assert "applicant free text" not in json.dumps(result.to_dict())


def test_reasons_are_ordered_most_severe_first_then_by_file_order() -> None:
    policy = _policy(
        """\
        policy: p
        version: 1.0.0
        rules:
          - {id: m1, when: {x: {gt: 0}}, effect: {tier: medium, reason: m1}}
          - {id: h1, when: {x: {gt: 0}}, effect: {tier: high, reason: h1}}
          - {id: l1, when: {x: {gt: 0}}, effect: {tier: low, reason: l1}}
          - {id: b1, when: {x: {gt: 0}}, effect: {tier: blocked, reason: b1}}
          - {id: m2, when: {x: {gt: 0}}, effect: {tier: medium, reason: m2}}
          - {id: h2, when: {x: {gt: 0}}, effect: {tier: high, reason: h2}}
        """
    )
    result = evaluate(policy, {"x": 1})
    assert result.fired_rules == ("b1", "h1", "h2", "m1", "m2", "l1")
    assert [reason.rule_id for reason in result.reasons] == list(result.fired_rules)


# ── Operators ───────────────────────────────────────────────────────────────

# (condition, evidence value, expected truth) where truth None means unresolved.
_ABSENT = object()


@pytest.mark.parametrize(
    ("when", "value", "expected"),
    [
        ("{a: {eq: active}}", "active", True),
        ("{a: {eq: active}}", "Active", False),
        ("{a: {eq: 1}}", 1.0, True),
        ("{a: {eq: 1}}", True, None),
        ("{a: {eq: true}}", 1, None),
        ("{a: {eq: '1'}}", 1, None),
        ("{a: {ne: active}}", "dissolved", True),
        ("{a: {ne: active}}", "active", False),
        ("{a: {ne: active}}", 3, None),
        ("{a: {in: [active, pending]}}", "pending", True),
        ("{a: {in: [active, pending]}}", "dissolved", False),
        ("{a: {in: [1, 2]}}", "1", None),
        ("{a: {not_in: [active]}}", "dissolved", True),
        ("{a: {not_in: [active]}}", "active", False),
        ("{a: {not_in: [active]}}", False, None),
        ("{a: {gt: 0}}", 1, True),
        ("{a: {gt: 0}}", 0, False),
        ("{a: {gt: 0}}", "1", None),
        ("{a: {gt: 0}}", True, None),
        ("{a: {gt: 0}}", float("inf"), None),
        ("{a: {gte: 0.5}}", 0.5, True),
        ("{a: {gte: 0.5}}", 0.49, False),
        ("{a: {lt: 10}}", -3, True),
        ("{a: {lt: 10}}", 10, False),
        ("{a: {lte: 10}}", 10, True),
        ("{a: {lte: 10}}", 10.01, False),
        ("{a: {eq: active}}", _ABSENT, None),
        ("{a: {eq: active}}", None, None),
        ("{a: {eq: active}}", ["active"], None),
        ("{a: {eq: active}}", {"value": "active"}, None),
        ("{a: {ne: active}}", _ABSENT, None),
        ("{a: {not_in: [active]}}", None, None),
        ("{a: {lt: 1}}", _ABSENT, None),
        ("{a: {exists: true}}", "x", True),
        ("{a: {exists: true}}", 0, True),
        ("{a: {exists: true}}", False, True),
        ("{a: {exists: true}}", [], True),
        ("{a: {exists: true}}", None, False),
        ("{a: {exists: true}}", _ABSENT, False),
        ("{a: {missing: true}}", _ABSENT, True),
        ("{a: {missing: true}}", None, True),
        ("{a: {missing: true}}", "", False),
    ],
)
def test_operator_truth_table(when: str, value: Any, expected: bool | None) -> None:
    policy = _one_rule(when)
    evidence = {} if value is _ABSENT else {"a": value}
    result = evaluate(policy, evidence)
    if expected is False:
        assert result.fired_rules == ()
        return
    assert result.fired_rules == ("r",)
    assert result.reasons[0].indeterminate is (expected is None)
    assert result.reasons[0].unresolved_paths == (("a",) if expected is None else ())


def test_paths_through_non_mappings_are_missing() -> None:
    policy = _one_rule("{a.b.c: {exists: true}}")
    for evidence in ({}, {"a": None}, {"a": "text"}, {"a": [{"b": {"c": 1}}]}, {"a": {"b": 5}}):
        assert evaluate(policy, evidence).fired_rules == ()
        assert evaluate(policy, evidence).missing_inputs == ("a.b.c",)
    assert evaluate(policy, {"a": {"b": {"c": 1}}}).fired_rules == ("r",)


# ── Missing evidence: three-valued logic, fails towards the stricter tier ───


@pytest.mark.parametrize(
    ("when", "evidence", "fires", "indeterminate"),
    [
        # A missing value can never be negated into a pass.
        ("{not: {a: {eq: active}}}", {}, True, True),
        ("{not: {not: {a: {gt: 0}}}}", {}, True, True),
        # A definite false decides ``all`` regardless of the missing value.
        ("{all: [{a: {gt: 0}}, {b: {eq: x}}]}", {"b": "y"}, False, None),
        ("{all: [{a: {gt: 0}}, {b: {eq: x}}]}", {"b": "x"}, True, True),
        # A definite true decides ``any`` regardless of the missing value.
        ("{any: [{a: {gt: 0}}, {b: {eq: x}}]}", {"b": "x"}, True, False),
        ("{any: [{a: {gt: 0}}, {b: {eq: x}}]}", {"b": "y"}, True, True),
        # ``exists`` lets an author make absence explicit.
        ("{all: [{a: {exists: true}}, {a: {eq: dissolved}}]}", {}, False, None),
        ("{any: [{a: {missing: true}}, {a: {lt: 1}}]}", {}, True, False),
        ("{not: {all: [{a: {exists: true}}, {a: {gt: 0}}]}}", {}, True, False),
    ],
)
def test_combinators_use_three_valued_logic(
    when: str, evidence: dict[str, Any], fires: bool, indeterminate: bool | None
) -> None:
    result = evaluate(_one_rule(when), evidence)
    assert (result.fired_rules == ("r",)) is fires
    if fires:
        assert result.reasons[0].indeterminate is indeterminate


def test_a_definite_rule_still_reports_the_paths_it_could_not_read() -> None:
    result = evaluate(_one_rule("{any: [{a: {gt: 0}}, {b: {eq: x}}]}"), {"b": "x"})
    assert result.reasons[0].indeterminate is False
    assert result.reasons[0].unresolved_paths == ("a",)
    assert result.missing_inputs == ("a",)


def test_empty_evidence_fires_every_rule_of_the_prd_example_as_indeterminate() -> None:
    result = evaluate(_policy(PRD_EXAMPLE), {})
    assert result.tier is Tier.BLOCKED
    assert set(result.fired_rules) == {"registry_active", "ownership_reconciled", "screening_clear"}
    assert all(reason.indeterminate for reason in result.reasons)
    assert result.missing_inputs == (
        "ownership.missing_owners",
        "screening.unresolved_true_matches",
        "verification.status",
    )


# ── Scoring and tiers ───────────────────────────────────────────────────────


def test_score_sums_fired_rules_and_is_capped() -> None:
    policy = _policy(
        """\
        policy: p
        version: 1.0.0
        rules:
          - {id: a, when: {x: {gt: 0}}, effect: {tier: medium, reason: a, score: 30}}
          - {id: b, when: {x: {gt: 1}}, effect: {tier: medium, reason: b, score: 45}}
          - {id: c, when: {x: {gt: 2}}, effect: {tier: high, reason: c}}
        """
    )
    assert evaluate(policy, {"x": 1}).score == 30
    assert evaluate(policy, {"x": 2}).score == 75
    assert evaluate(policy, {"x": 3}).score == 100
    assert evaluate(policy, {"x": 3}).tier is Tier.HIGH


def test_score_thresholds_escalate_the_tier_but_never_lower_it() -> None:
    policy = _policy(
        """\
        policy: p
        version: 1.0.0
        score_thresholds: {medium: 10, high: 60}
        rules:
          - {id: a, when: {x: {gt: 0}}, effect: {tier: low, reason: a, score: 10}}
          - {id: b, when: {y: {gt: 0}}, effect: {tier: medium, reason: b, score: 25}}
          - {id: c, when: {z: {gt: 0}}, effect: {tier: medium, reason: c, score: 25}}
          - {id: d, when: {w: {gt: 0}}, effect: {tier: blocked, reason: d, score: 0}}
        """
    )
    base = {"x": 0, "y": 0, "z": 0, "w": 0}
    assert (evaluate(policy, base).tier, evaluate(policy, base).tier_source) == (Tier.LOW, "rules")

    low_rule = evaluate(policy, {**base, "x": 1})
    assert (low_rule.tier, low_rule.tier_source, low_rule.score) == (Tier.MEDIUM, "score_threshold", 10)

    two_mediums = evaluate(policy, {**base, "x": 1, "y": 1, "z": 1})
    assert (two_mediums.tier, two_mediums.tier_source, two_mediums.score) == (Tier.HIGH, "score_threshold", 60)

    blocked = evaluate(policy, {**base, "w": 1})
    assert (blocked.tier, blocked.tier_source, blocked.score) == (Tier.BLOCKED, "rules", 0)


def test_tier_ordering() -> None:
    assert [tier.rank for tier in (Tier.LOW, Tier.MEDIUM, Tier.HIGH, Tier.BLOCKED)] == [0, 1, 2, 3]


# ── Examples ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["business_onboarding_us", "business_onboarding_uk"])
def test_example_policies_are_low_on_a_clean_case(name: str) -> None:
    policy = load_policy(EXAMPLES_DIR / f"{name}.yaml")
    result = evaluate(policy, CLEAN)
    assert (result.tier, result.fired_rules, result.missing_inputs) == (Tier.LOW, (), ())


@pytest.mark.parametrize("name", ["business_onboarding_us", "business_onboarding_uk"])
def test_example_policies_handle_a_thin_file_case_explicitly(name: str) -> None:
    policy = load_policy(EXAMPLES_DIR / f"{name}.yaml")
    thin = _with(_with(CLEAN, "verification.registry_match", False), "verification.status", None)
    result = evaluate(policy, thin)
    assert result.tier is Tier.HIGH
    assert "registry_record_found" in result.fired_rules
    assert "registry_dissolved" not in result.fired_rules
    dissolved = evaluate(policy, _with(CLEAN, "verification.status", "dissolved"))
    assert dissolved.tier is Tier.BLOCKED
    assert dissolved.fired_rules[:2] == ("registry_dissolved", "registry_active")


def test_example_three_mediums_escalate_to_high_by_score() -> None:
    policy = load_policy(EXAMPLES_DIR / "business_onboarding_us.yaml")
    evidence = _with(_with(CLEAN, "ownership.missing_owners", 1), "ownership.undeclared_owners", 1)
    evidence = _with(evidence, "screening.unresolved_possible_matches", 2)
    result = evaluate(policy, evidence)
    assert (result.tier, result.tier_source, result.score) == (Tier.HIGH, "score_threshold", 60)


# ── Boundaries ──────────────────────────────────────────────────────────────


def test_evaluation_does_not_mutate_evidence() -> None:
    evidence = _with(CLEAN, "ownership.missing_owners", 3)
    before = copy.deepcopy(evidence)
    evaluate(_policy(PRD_EXAMPLE), evidence)
    assert evidence == before


def test_non_mapping_evidence_and_unloaded_policies_are_programming_errors() -> None:
    policy = _policy(PRD_EXAMPLE)
    with pytest.raises(TypeError, match="evidence must be a mapping"):
        evaluate(policy, [("verification", {})])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Policy"):
        evaluate({"policy": "p"}, CLEAN)  # type: ignore[arg-type]


def test_evaluations_are_counted_by_tier_and_status() -> None:
    labels = {"tier": "blocked", "policy_status": "example"}
    before = REGISTRY.get_sample_value("agenticorg_policy_evaluations_total", labels) or 0.0
    evaluate(_policy(PRD_EXAMPLE), {})
    assert REGISTRY.get_sample_value("agenticorg_policy_evaluations_total", labels) == before + 1


# ── Review hardening: evaluation never raises on evidence ───────────────────


class _RaisingMapping(dict):
    def __contains__(self, key: object) -> bool:
        raise RuntimeError("backing store unavailable")


class _RaisingGetItem(dict):
    def __getitem__(self, key: object) -> Any:
        raise KeyError(key)


def test_huge_integers_in_evidence_are_unusable_not_a_crash() -> None:
    policy = _policy(PRD_EXAMPLE)
    for value in (10**5000, 2**53 + 1, -(2**60)):
        result = evaluate(policy, _with(CLEAN, "screening.unresolved_true_matches", value))
        assert result.tier is Tier.BLOCKED
        assert result.reasons[0].indeterminate is True
        assert result.inputs["screening.unresolved_true_matches"] == {"non_scalar": "integer_out_of_range"}
        assert result.invalid_inputs == ("screening.unresolved_true_matches",)
        json.dumps(result.to_dict())
    assert evaluate(policy, _with(CLEAN, "screening.unresolved_true_matches", 2**53)).fired_rules == (
        "screening_clear",
    )


@pytest.mark.parametrize("mapping_type", [_RaisingMapping, _RaisingGetItem])
def test_evidence_that_raises_while_read_fires_the_rule_as_indeterminate(mapping_type: type) -> None:
    import structlog

    evidence = copy.deepcopy(CLEAN)
    evidence["screening"] = mapping_type(evidence["screening"])
    policy = _policy(
        PRD_EXAMPLE
        + "  - id: screening_present\n    when: {screening.unresolved_true_matches: {missing: true}}\n"
        + '    effect: {tier: high, reason: "Screening evidence is missing"}\n'
    )
    with structlog.testing.capture_logs() as logs:
        result = evaluate(policy, evidence)
    assert result.tier is Tier.BLOCKED
    assert set(result.fired_rules) == {"screening_clear", "screening_present"}
    assert all(reason.indeterminate for reason in result.reasons)
    assert result.inputs["screening.unresolved_true_matches"] == {"non_scalar": "unreadable"}
    assert "screening.unresolved_true_matches" in result.invalid_inputs
    assert [entry["error"] for entry in logs if entry["event"] == "policy_evidence_unreadable"]
