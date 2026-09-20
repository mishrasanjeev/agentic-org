# SPDX-License-Identifier: Apache-2.0
"""The example onboarding policies only read evidence the underwriter produces.

A policy rule whose evidence path is never resolved fires as indeterminate on
every case, which raises the tier on nothing. A rule that compares
`verification.status` with a value the provider interface cannot return never
fires at all. Both were true of the shipped examples (FINDINGS A-37, A-39), so
the examples are checked here against the real evidence mapping and against
`RegistryStatus`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from connectors.framework.verification_types import RegistryStatus
from core.policy import EXAMPLES_DIR, load_policy
from core.policy.types import AllOf, AnyOf, Compare, Condition, Not, Operator, Policy
from tests.unit.business_underwriter.conftest import ALL_FIXTURES

EXAMPLE_POLICY_FILES = ("business_onboarding_uk.yaml", "business_onboarding_us.yaml")
# Which fixtures each example decides: `conftest.policy_for` picks the UK
# example for `gb-` fixtures and the US one for the rest, so each policy is
# checked against its own jurisdiction only.
POLICY_FIXTURE_PREFIX = {"business_onboarding_uk.yaml": "gb-", "business_onboarding_us.yaml": "us-"}
# One clean fixture per jurisdiction, and the tier it must reach: a clean case
# has to come out `low`, not `medium` on indeterminate reasons.
CLEAN_FIXTURES = {"gb-clean-brightwater": "low", "us-clean-quillfeather": "low", "us-clean-hollowbrook": "low"}
STATUS_PATH = "verification.status"
# Operators whose operand is a status value rather than a presence test.
VALUE_OPERATORS = (Operator.EQ, Operator.NE, Operator.IN, Operator.NOT_IN)


def _examples() -> list[Policy]:
    return [load_policy(EXAMPLES_DIR / name) for name in EXAMPLE_POLICY_FILES]


def _comparisons(condition: Condition) -> Iterator[Compare]:
    if isinstance(condition, Compare):
        yield condition
    elif isinstance(condition, AllOf | AnyOf):
        for item in condition.items:
            yield from _comparisons(item)
    elif isinstance(condition, Not):
        yield from _comparisons(condition.item)


def _resolved_paths(evidence: dict[str, Any], prefix: str = "") -> set[str]:
    """Dotted paths the evidence mapping resolves: present and not ``None``."""
    paths: set[str] = set()
    for key, value in evidence.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths |= _resolved_paths(value, f"{path}.")
        elif value is not None:
            paths.add(path)
    return paths


@pytest.mark.parametrize("name", EXAMPLE_POLICY_FILES)
def test_example_policies_are_marked_as_unreviewed_examples(name: str) -> None:
    policy = load_policy(EXAMPLES_DIR / name)
    assert policy.status.value == "example"
    assert policy.reviewed_by is None
    assert "EXAMPLE ONLY" in (policy.description or "")


def test_every_registry_status_operand_exists_in_the_interface_enum() -> None:
    known = {status.value for status in RegistryStatus}
    tested: set[str] = set()
    for policy in _examples():
        for rule in policy.rules:
            for comparison in _comparisons(rule.when):
                if comparison.path != STATUS_PATH or comparison.op not in VALUE_OPERATORS:
                    continue
                operands = (
                    comparison.operand
                    if isinstance(comparison.operand, tuple)
                    else (comparison.operand,)
                )
                tested.update(str(operand) for operand in operands)
    assert tested, "no example rule compares verification.status"
    assert tested <= known, (
        f"{sorted(tested - known)} are not members of RegistryStatus "
        f"({sorted(known)}), so no provider can ever return them"
    )


@pytest.mark.timeout(300)
async def test_every_referenced_path_is_produced_for_a_fixture_of_its_own_jurisdiction(
    run_case: Callable[..., Any], narrative: Callable[..., Any]
) -> None:
    """Each example's paths must resolve on a fixture that example decides.

    Checked per jurisdiction: a path only a GB fixture produces must not excuse
    a rule in the US example.
    """
    narrative(len(ALL_FIXTURES))
    resolved: dict[str, set[str]] = {}
    indeterminate: dict[str, dict[str, int]] = {}
    tiers: dict[str, str] = {}
    for key in ALL_FIXTURES:
        outcome = await run_case(key)
        assert outcome.status == "completed", outcome.failure_reason
        prefix = key[:3]
        resolved.setdefault(prefix, set()).update(_resolved_paths(outcome.policy_evidence))
        tiers[key] = outcome.policy_result["tier"]
        for reason in outcome.policy_result["reasons"]:
            if reason["indeterminate"]:
                counts = indeterminate.setdefault(prefix, {})
                counts[reason["rule_id"]] = counts.get(reason["rule_id"], 0) + 1

    for name, policy in zip(EXAMPLE_POLICY_FILES, _examples(), strict=True):
        prefix = POLICY_FIXTURE_PREFIX[name]
        fixtures = [key for key in ALL_FIXTURES if key.startswith(prefix)]
        never_produced = [path for path in policy.referenced_paths if path not in resolved[prefix]]
        assert not never_produced, (
            f"{policy.policy_id} reads {never_produced}, which the underwriter's evidence "
            "mapping (core.agents.business_underwriter.facts.policy_evidence) never "
            f"produces for any {prefix} fixture, so those rules fire as indeterminate "
            "on every case it decides"
        )
        # A rule indeterminate on every fixture of its jurisdiction is the
        # shape of a rule reading evidence nothing supplies.
        always = sorted(
            rule_id
            for rule_id, count in indeterminate.get(prefix, {}).items()
            if count == len(fixtures)
        )
        assert not always, f"{always} fired as indeterminate on all {len(fixtures)} {prefix} fixtures"

    for key, expected in CLEAN_FIXTURES.items():
        assert tiers[key] == expected, (
            f"{key} is a clean case and must come out {expected}, not {tiers[key]}"
        )
