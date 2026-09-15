# SPDX-License-Identifier: Apache-2.0
"""Every YAML example in docs/policies/authoring.md loads, or fails with the reason it states."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.policy import PolicyLoadError, Tier, evaluate, load_policy_bytes

DOC = Path(__file__).resolve().parents[3] / "docs" / "policies" / "authoring.md"
_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_REJECTED_RE = re.compile(r"^# rejected: (\w+)\n")


def _blocks() -> list[str]:
    return _BLOCK_RE.findall(DOC.read_text(encoding="utf-8"))


def test_the_authoring_guide_has_yaml_examples() -> None:
    assert len(_blocks()) >= 5


@pytest.mark.parametrize("index", range(len(_blocks())))
def test_documented_yaml_block_behaves_as_documented(index: int) -> None:
    block = _blocks()[index]
    rejected = _REJECTED_RE.match(block)
    if rejected:
        with pytest.raises(PolicyLoadError) as info:
            load_policy_bytes(block.encode("utf-8"), source=f"authoring.md block {index}")
        assert info.value.reason.value == rejected.group(1)
    else:
        load_policy_bytes(block.encode("utf-8"), source=f"authoring.md block {index}")


def _policy_named(name: str):
    for block in _blocks():
        if re.search(rf"^policy: {name}$", block, re.MULTILINE):
            return load_policy_bytes(block.encode("utf-8"))
    raise AssertionError(f"no documented policy named {name}")


def test_documented_claims_about_the_complete_policy() -> None:
    result = evaluate(_policy_named("business_onboarding_uk"), {})
    assert result.tier is Tier.BLOCKED
    assert len(result.reasons) == 3
    assert all(reason.indeterminate for reason in result.reasons)


def test_documented_claims_about_explicit_absence() -> None:
    policy = _policy_named("explicit_absence_example")
    absent = evaluate(policy, {})
    assert (absent.tier, absent.fired_rules, absent.reasons[0].indeterminate) == (Tier.HIGH, ("registry_active",), True)
    dissolved = evaluate(policy, {"verification": {"status": "dissolved"}})
    assert (dissolved.tier, dissolved.fired_rules) == (Tier.BLOCKED, ("registry_dissolved", "registry_active"))


def test_documented_claims_about_scoring() -> None:
    policy = _policy_named("scoring_example")
    base = {"web_presence": {"activity_mismatch": True}, "screening": {"unresolved_possible_matches": 0}}
    alone = evaluate(policy, base)
    assert (alone.tier, alone.score, alone.tier_source) == (Tier.MEDIUM, 10, "score_threshold")
    both = evaluate(policy, {**base, "screening": {"unresolved_possible_matches": 2}})
    assert (both.tier, both.score, both.tier_source) == (Tier.HIGH, 60, "score_threshold")


def test_documented_error_message_format() -> None:
    block = (
        "policy: p\nversion: 1.0.0\nrules:\n"
        "  - {id: r, when: {all: [{a: {eq: 1}}, {b: {between: [1, 2]}}]}, effect: {tier: high, reason: r}}\n"
    )
    with pytest.raises(PolicyLoadError) as info:
        load_policy_bytes(block.encode("utf-8"), source="policies/uk.yaml")
    assert str(info.value).startswith("policies/uk.yaml: policy_unknown_operator at rules[0].when.all[1].b.between: ")
    assert "`policies/uk.yaml: policy_unknown_operator at rules[0].when.all[1].b.between: …`" in DOC.read_text(
        encoding="utf-8"
    )


def test_every_reason_code_is_documented() -> None:
    from core.policy import PolicyLoadReason

    text = DOC.read_text(encoding="utf-8")
    undocumented = [reason.value for reason in PolicyLoadReason if f"`{reason.value}`" not in text]
    assert undocumented == []
