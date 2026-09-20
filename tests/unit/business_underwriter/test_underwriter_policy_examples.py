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
async def test_every_referenced_path_is_produced_for_at_least_one_mock_fixture(
    run_case: Callable[..., Any], narrative: Callable[..., Any]
) -> None:
    """Every path the examples read must resolve on at least one mock fixture."""
    narrative(len(ALL_FIXTURES))
    resolved: set[str] = set()
    indeterminate_cases: dict[str, int] = {}
    for key in ALL_FIXTURES:
        outcome = await run_case(key)
        assert outcome.status == "completed", outcome.failure_reason
        resolved |= _resolved_paths(outcome.policy_evidence)
        for reason in outcome.policy_result["reasons"]:
            if reason["indeterminate"]:
                indeterminate_cases[reason["rule_id"]] = indeterminate_cases.get(reason["rule_id"], 0) + 1

    for policy in _examples():
        never_produced = [path for path in policy.referenced_paths if path not in resolved]
        assert not never_produced, (
            f"{policy.policy_id} reads {never_produced}, which the underwriter's evidence "
            "mapping (core.agents.business_underwriter.facts.policy_evidence) never "
            "produces, so those rules fire as indeterminate on every case"
        )

    # No rule may be indeterminate on every fixture: that is the shape of a
    # rule reading evidence nothing supplies.
    always_indeterminate = sorted(
        rule_id for rule_id, count in indeterminate_cases.items() if count == len(ALL_FIXTURES)
    )
    assert not always_indeterminate, (
        f"{always_indeterminate} fired as indeterminate on all {len(ALL_FIXTURES)} mock fixtures"
    )
