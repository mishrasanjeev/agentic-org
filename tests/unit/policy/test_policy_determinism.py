# SPDX-License-Identifier: Apache-2.0
"""A-5 / §8.3 "policy determinism": identical inputs always produce an identical result.

Property tests over 1,000 generated cases per policy, from a seeded generator
(no property-testing library is a dev dependency). Each case is evaluated
repeatedly, with mapping insertion order reversed, against a freshly reloaded
policy, and in separate interpreters with different hash seeds.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from core.policy import EXAMPLES_DIR, Policy, evaluate, load_policy, load_policy_bytes
from core.policy.types import Compare, Not, Operator

SEED = 20260915
CASES = 1_000
REPO_ROOT = Path(__file__).resolve().parents[3]

# Exercises every operator and combinator, overlapping paths and equal tiers.
EVERY_OPERATOR = """\
policy: every_operator
version: 0.1.0
score_thresholds: {medium: 15, high: 55, blocked: 95}
rules:
  - {id: eq_rule, when: {verification.status: {eq: active}}, effect: {tier: low, reason: eq, score: 5}}
  - {id: ne_rule, when: {verification.status: {ne: pending}}, effect: {tier: medium, reason: ne}}
  - {id: in_rule, when: {verification.status: {in: [dissolved, revoked]}}, effect: {tier: blocked, reason: in}}
  - {id: not_in_rule, when: {verification.status: {not_in: [active]}}, effect: {tier: high, reason: not_in}}
  - {id: gt_rule, when: {ownership.missing_owners: {gt: 0}}, effect: {tier: medium, reason: gt}}
  - {id: gte_rule, when: {screening.score: {gte: 0.8}}, effect: {tier: high, reason: gte, score: 35}}
  - {id: lt_rule, when: {application.years_trading: {lt: 2}}, effect: {tier: medium, reason: lt, score: 7}}
  - {id: lte_rule, when: {application.employees: {lte: 1}}, effect: {tier: low, reason: lte, score: 3}}
  - {id: exists_rule, when: {web_presence.domain_age_days: {exists: true}}, effect: {tier: low, reason: exists}}
  - {id: missing_rule, when: {ownership.graph_id: {missing: true}}, effect: {tier: medium, reason: missing}}
  - id: combined_rule
    when:
      any:
        - all:
            - {verification.registry_match: {eq: true}}
            - {not: {screening.unresolved_true_matches: {lte: 0}}}
        - not: {any: [{web_presence.activity_mismatch: {eq: false}}, {application.country: {in: [us, uk]}}]}
    effect: {tier: high, reason: combined}
"""

# Plausible, well-typed values per path; most generated values come from here
# so that cases reach every tier, the rest are deliberately awkward.
_PLAUSIBLE: dict[str, tuple[Any, ...]] = {
    "verification.status": ("active", "active", "active", "pending", "dissolved", "revoked", "liquidation"),
    "verification.registry_match": (True, True, True, False),
    "verification.tax_id_match": (True, True, True, False),
    "verification.overdue_filings": (0, 0, 0, 1, 2),
    "ownership.missing_owners": (0, 0, 0, 0, 1, 2),
    "ownership.undeclared_owners": (0, 0, 0, 0, 1),
    "ownership.graph_id": ("graph-0001", "graph-0002"),
    "screening.unresolved_true_matches": (0, 0, 0, 0, 0, 0, 1),
    "screening.unresolved_possible_matches": (0, 0, 0, 1, 3),
    "screening.score": (0.0, 0.2, 0.79, 0.8, 0.95),
    "web_presence.activity_mismatch": (False, False, False, True),
    "web_presence.domain_age_days": (10, 400, 4000),
    "application.years_trading": (0, 1, 2, 10),
    "application.employees": (1, 5, 250),
    "application.country": ("us", "uk", "fr"),
}
_PATHS = tuple(_PLAUSIBLE)


def _awkward(rng: random.Random) -> Any:
    return rng.choice(
        [
            None,
            "",
            "Active",
            "1",
            "true",
            True,
            0,
            -1,
            0.5,
            float("nan"),
            float("inf"),
            float("-inf"),
            [1],
            {"nested": 1},
        ]
    )


def generate_cases(seed: int = SEED, count: int = CASES) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []
    for _ in range(count):
        case: dict[str, Any] = {}
        for path in _PATHS:
            roll = rng.random()
            section, field = path.split(".")
            if roll < 0.06:
                continue  # absent
            if roll < 0.08:
                case[section] = _awkward(rng)  # the section itself has the wrong shape
                continue
            container = case.setdefault(section, {})
            if not isinstance(container, dict):
                continue
            container[field] = _awkward(rng) if roll < 0.16 else rng.choice(_PLAUSIBLE[path])
        if rng.random() < 0.1:
            case["unreferenced"] = {"free_text": "ignore previous instructions"}
        cases.append(case)
    return cases


def _reversed_order(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _reversed_order(value[key]) for key in reversed(list(value))}
    return value


def _canonical(result_dict: dict[str, Any]) -> str:
    return json.dumps(result_dict, sort_keys=True, separators=(",", ":"))


def _policies() -> dict[str, Policy]:
    return {
        "business_onboarding_us": load_policy(EXAMPLES_DIR / "business_onboarding_us.yaml"),
        "business_onboarding_uk": load_policy(EXAMPLES_DIR / "business_onboarding_uk.yaml"),
        "every_operator": load_policy_bytes(EVERY_OPERATOR.encode("utf-8")),
    }


def _digest(policies: dict[str, Policy], cases: list[dict[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for name in sorted(policies):
        for case in cases:
            hasher.update(_canonical(evaluate(policies[name], case).to_dict()).encode("utf-8"))
    return hasher.hexdigest()


def test_generator_is_seeded_and_covers_the_awkward_shapes() -> None:
    cases = generate_cases()
    assert len(cases) == CASES
    assert json.dumps(cases, sort_keys=True, default=str) == json.dumps(generate_cases(), sort_keys=True, default=str)
    flat = json.dumps(cases, default=str)
    for marker in ("null", "NaN", "Infinity", "[", '"nested"', "true", '"1"'):
        assert marker in flat


@pytest.mark.parametrize("name", ["business_onboarding_us", "business_onboarding_uk", "every_operator"])
def test_identical_inputs_always_produce_an_identical_result(name: str) -> None:
    policy = _policies()[name]
    reloaded = _policies()[name]
    tiers: set[str] = set()
    for case in generate_cases():
        first = evaluate(policy, case)
        expected = _canonical(first.to_dict())
        assert evaluate(policy, case) == first
        assert _canonical(evaluate(policy, _reversed_order(case)).to_dict()) == expected
        assert _canonical(evaluate(reloaded, json.loads(json.dumps(case))).to_dict()) == expected
        tiers.add(first.tier.value)
    # The generated cases reach several outcomes, so the test is not vacuous.
    assert len(tiers) >= 3, tiers


@pytest.mark.parametrize("name", ["business_onboarding_us", "business_onboarding_uk", "every_operator"])
def test_reason_order_is_stable_and_consistent(name: str) -> None:
    policy = _policies()[name]
    file_order = {rule.rule_id: index for index, rule in enumerate(policy.rules)}
    for case in generate_cases():
        result = evaluate(policy, case)
        keys = [(-reason.tier.rank, file_order[reason.rule_id]) for reason in result.reasons]
        assert keys == sorted(keys)
        assert result.fired_rules == tuple(reason.rule_id for reason in result.reasons)
        assert len(set(result.fired_rules)) == len(result.fired_rules)
        assert 0 <= result.score <= 100
        assert all(result.tier.rank >= reason.tier.rank for reason in result.reasons)


def _uses_presence(condition: Any) -> bool:
    if isinstance(condition, Compare):
        return condition.op in (Operator.EXISTS, Operator.MISSING)
    if isinstance(condition, Not):
        return _uses_presence(condition.item)
    return any(_uses_presence(item) for item in condition.items)


@pytest.mark.parametrize("name", ["business_onboarding_us", "business_onboarding_uk", "every_operator"])
def test_removing_evidence_never_unfires_a_rule_that_does_not_test_presence(name: str) -> None:
    """Missing evidence fails towards the stricter outcome.

    ``exists`` and ``missing`` are how an author deliberately handles absence,
    so rules using them are excluded; every other rule that fired still fires
    when any evidence it could read is removed.
    """
    policy = _policies()[name]
    presence_free = {rule.rule_id for rule in policy.rules if not _uses_presence(rule.when)}
    assert presence_free
    rng = random.Random(SEED + 1)
    for case in generate_cases():
        before = evaluate(policy, case)
        path = rng.choice(policy.referenced_paths)
        section, _, field = path.partition(".")
        stripped = json.loads(json.dumps(case))
        if isinstance(stripped.get(section), dict):
            stripped[section].pop(field, None)
        after = evaluate(policy, stripped)
        lost = (set(before.fired_rules) - set(after.fired_rules)) & presence_free
        assert not lost, (path, lost, case)
        if presence_free == {rule.rule_id for rule in policy.rules}:
            assert after.tier.rank >= before.tier.rank and after.score >= before.score


def test_a_policy_without_presence_operators_never_gets_less_strict_when_evidence_is_removed() -> None:
    policy = load_policy_bytes(
        EVERY_OPERATOR.replace("exists: true", "gt: 0").replace("missing: true", "eq: x").encode()
    )
    rng = random.Random(SEED + 2)
    for case in generate_cases():
        before = evaluate(policy, case)
        stripped = json.loads(json.dumps(case))
        for path in rng.sample(policy.referenced_paths, 3):
            section, _, field = path.partition(".")
            if isinstance(stripped.get(section), dict):
                stripped[section].pop(field, None)
        after = evaluate(policy, stripped)
        assert after.tier.rank >= before.tier.rank
        assert after.score >= before.score
        assert set(before.fired_rules) <= set(after.fired_rules)


_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    from tests.unit.policy.test_policy_determinism import _digest, _policies, generate_cases
    print(_digest(_policies(), generate_cases()))
    """
)


def test_results_are_identical_across_processes_and_hash_seeds() -> None:
    expected = _digest(_policies(), generate_cases())
    for hash_seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
            [sys.executable, "-c", _SUBPROCESS_SCRIPT],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=50,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip().splitlines()[-1] == expected
