# SPDX-License-Identifier: Apache-2.0
"""A-5: the policy loader is strict and fails closed at load, with a reason code."""

from __future__ import annotations

import dataclasses
import hashlib
import textwrap
from pathlib import Path

import pytest
import structlog
from prometheus_client import REGISTRY

from core.policy import (
    EXAMPLES_DIR,
    PolicyLoadError,
    PolicyLoadReason,
    PolicyStatus,
    Tier,
    load_policies,
    load_policy,
    load_policy_bytes,
)
from core.policy.loader import MAX_POLICY_BYTES, MAX_RULES

R = PolicyLoadReason

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


def _load(text: str, **kwargs):
    return load_policy_bytes(textwrap.dedent(text).encode("utf-8"), source="test.yaml", **kwargs)


def _policy_with_when(when: str) -> str:
    return f"""\
policy: p
version: 1.0.0
rules:
  - id: r
    when: {when}
    effect: {{tier: high, reason: "r"}}
"""


def _reason_of(text: str) -> PolicyLoadReason:
    with pytest.raises(PolicyLoadError) as info:
        _load(text)
    return info.value.reason


# ── Valid policies ──────────────────────────────────────────────────────────


def test_the_prd_example_policy_loads() -> None:
    policy = _load(PRD_EXAMPLE)
    assert policy.policy_id == "business_onboarding_uk"
    assert policy.version == "1.2.0"
    assert [rule.rule_id for rule in policy.rules] == ["registry_active", "ownership_reconciled", "screening_clear"]
    assert [rule.effect.tier for rule in policy.rules] == [Tier.HIGH, Tier.MEDIUM, Tier.BLOCKED]
    assert policy.referenced_paths == (
        "ownership.missing_owners",
        "screening.unresolved_true_matches",
        "verification.status",
    )


def test_a_policy_without_status_is_treated_as_an_example() -> None:
    assert _load(PRD_EXAMPLE).status is PolicyStatus.EXAMPLE


def test_content_hash_is_sha256_of_the_exact_bytes() -> None:
    data = PRD_EXAMPLE.encode("utf-8")
    policy = load_policy_bytes(data)
    assert policy.content_hash == "sha256:" + hashlib.sha256(data).hexdigest()
    assert load_policy_bytes(data + b"\n").content_hash != policy.content_hash


def test_loaded_policies_are_immutable() -> None:
    policy = _load(PRD_EXAMPLE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.rules[0].effect.tier = Tier.LOW  # type: ignore[misc]
    assert isinstance(policy.rules, tuple)


def test_default_scores_come_from_the_tier_and_explicit_scores_are_kept() -> None:
    policy = _load(
        """\
        policy: p
        version: 1.0.0
        rules:
          - {id: a, when: {x: {gt: 0}}, effect: {tier: medium, reason: a}}
          - {id: b, when: {x: {gt: 0}}, effect: {tier: medium, reason: b, score: 5}}
          - {id: c, when: {x: {gt: 0}}, effect: {tier: low, reason: c, score: 0}}
        """
    )
    assert [rule.effect.score for rule in policy.rules] == [20, 5, 0]


def test_yaml_one_point_one_booleans_and_dates_stay_strings() -> None:
    policy = _load(
        """\
        policy: p
        version: 1.0.0
        rules:
          - {id: a, when: {x: {eq: no}}, effect: {tier: high, reason: a}}
          - {id: b, when: {x: {in: [yes, on, off]}}, effect: {tier: high, reason: b}}
          - {id: c, when: {x: {eq: 2026-01-01}}, effect: {tier: high, reason: c}}
          - {id: d, when: {x: {eq: true}}, effect: {tier: high, reason: d}}
        """
    )
    assert policy.rules[0].when.operand == "no"
    assert policy.rules[1].when.operand == ("yes", "on", "off")
    assert policy.rules[2].when.operand == "2026-01-01"
    assert policy.rules[3].when.operand is True


def test_semver_with_prerelease_and_build_metadata_is_accepted() -> None:
    assert _load(PRD_EXAMPLE.replace("1.2.0", "2.0.0-rc.1+build.7")).version == "2.0.0-rc.1+build.7"


# ── Production status and review ────────────────────────────────────────────


def test_production_policy_without_reviewed_by_fails_to_load() -> None:
    text = PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nstatus: production")
    assert _reason_of(text) is R.PRODUCTION_UNREVIEWED
    assert _reason_of(text.replace("status: production", "status: production\nreviewed_by: null")) is (
        R.PRODUCTION_UNREVIEWED
    )
    assert _reason_of(text.replace("status: production", "status: production\nreviewed_by: '  '")) is (R.INVALID_VALUE)


def test_production_policy_with_reviewed_by_loads_without_the_example_warning() -> None:
    text = PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nstatus: production\nreviewed_by: Compliance Owner A")
    with structlog.testing.capture_logs() as logs:
        policy = _load(text, require_production=True)
    assert policy.status is PolicyStatus.PRODUCTION
    assert policy.reviewed_by == "Compliance Owner A"
    events = [entry["event"] for entry in logs]
    assert "policy_example_loaded" not in events
    assert "policy_loaded" in events


def test_require_production_refuses_an_example_policy() -> None:
    with pytest.raises(PolicyLoadError) as info:
        _load(PRD_EXAMPLE, require_production=True)
    assert info.value.reason is R.NOT_PRODUCTION


def test_loading_an_example_policy_logs_a_warning() -> None:
    with structlog.testing.capture_logs() as logs:
        _load(PRD_EXAMPLE)
    warnings = [entry for entry in logs if entry["event"] == "policy_example_loaded"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["policy"] == "business_onboarding_uk"


def test_shipped_examples_are_marked_as_examples_and_warn_on_load() -> None:
    with structlog.testing.capture_logs() as logs:
        policies = load_policies(EXAMPLES_DIR)
    assert sorted(policies) == ["business_onboarding_uk", "business_onboarding_us"]
    for policy in policies.values():
        assert policy.status is PolicyStatus.EXAMPLE
        assert policy.reviewed_by is None
        assert "EXAMPLE ONLY" in (policy.description or "")
        raw = Path(policy.source).read_text(encoding="utf-8")
        assert "EXAMPLE POLICY - NOT REVIEWED - DO NOT USE FOR REAL ONBOARDING DECISIONS" in raw
    warned = sorted(entry["policy"] for entry in logs if entry["event"] == "policy_example_loaded")
    assert warned == ["business_onboarding_uk", "business_onboarding_us"]
    with pytest.raises(PolicyLoadError) as info:
        load_policies(EXAMPLES_DIR, require_production=True)
    assert info.value.reason is R.NOT_PRODUCTION


# ── Rejections ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("when", "reason"),
    [
        ("{verification.status: {matches: active}}", R.UNKNOWN_OPERATOR),
        ("{verification.status: {contains: active}}", R.UNKNOWN_OPERATOR),
        ("{Verification.Status: {eq: active}}", R.INVALID_PATH),
        ("{verification..status: {eq: active}}", R.INVALID_PATH),
        ("{'verification.status.': {eq: active}}", R.INVALID_PATH),
        ("{'owners[0].name': {eq: a}}", R.INVALID_PATH),
        ("{'1abc': {eq: a}}", R.INVALID_PATH),
        ("{a.b.c.d.e.f.g.h.i: {eq: a}}", R.INVALID_PATH),
        ("{verification.status: active}", R.INVALID_CONDITION),
        ("{verification.status: {eq: active, ne: dissolved}}", R.INVALID_CONDITION),
        ("{verification.status: {}}", R.INVALID_CONDITION),
        ("{a: {eq: 1}, b: {eq: 2}}", R.INVALID_CONDITION),
        ("{}", R.INVALID_CONDITION),
        ("[{a: {eq: 1}}]", R.INVALID_CONDITION),
        ("{all: []}", R.INVALID_CONDITION),
        ("{any: {a: {eq: 1}}}", R.INVALID_CONDITION),
        ("{not: [{a: {eq: 1}}]}", R.INVALID_CONDITION),
        ("{a: {exists: false}}", R.INVALID_OPERAND),
        ("{a: {missing: yes}}", R.INVALID_OPERAND),
        ("{a: {gt: '0'}}", R.INVALID_OPERAND),
        ("{a: {gt: true}}", R.INVALID_OPERAND),
        ("{a: {gte: .nan}}", R.INVALID_OPERAND),
        ("{a: {lt: .inf}}", R.INVALID_OPERAND),
        ("{a: {eq: null}}", R.INVALID_OPERAND),
        ("{a: {eq: [1]}}", R.INVALID_OPERAND),
        ("{a: {ne: {b: 1}}}", R.INVALID_OPERAND),
        ("{a: {in: []}}", R.INVALID_OPERAND),
        ("{a: {in: active}}", R.INVALID_OPERAND),
        ("{a: {not_in: [1, one]}}", R.INVALID_OPERAND),
        ("{a: {in: [true, 1]}}", R.INVALID_OPERAND),
        ("{a: {in: [[1]]}}", R.INVALID_OPERAND),
    ],
)
def test_malformed_conditions_are_rejected_at_load(when: str, reason: PolicyLoadReason) -> None:
    assert _reason_of(_policy_with_when(when)) is reason


def test_conditions_nested_too_deeply_are_rejected() -> None:
    when = "{a: {eq: 1}}"
    for _ in range(12):
        when = "{not: " + when + "}"
    assert _reason_of(_policy_with_when(when)) is R.LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (PRD_EXAMPLE + "owner: someone\n", R.UNKNOWN_KEY),
        (PRD_EXAMPLE.replace("  - id: registry_active", "  - id: registry_active\n    severity: high"), R.UNKNOWN_KEY),
        (PRD_EXAMPLE.replace("tier: high, reason", "tier: high, weight: 2, reason"), R.UNKNOWN_KEY),
        (PRD_EXAMPLE.replace("version: 1.2.0\n", ""), R.MISSING_FIELD),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2"), R.INVALID_VERSION),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: '1.2'"), R.INVALID_VERSION),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: v1.2.0"), R.INVALID_VERSION),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 01.2.0"), R.INVALID_VERSION),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 3"), R.INVALID_VERSION),
        (PRD_EXAMPLE.replace("policy: business_onboarding_uk\n", ""), R.MISSING_FIELD),
        (PRD_EXAMPLE.replace("business_onboarding_uk", "Business Onboarding"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("id: ownership_reconciled", "id: registry_active"), R.DUPLICATE_RULE_ID),
        (PRD_EXAMPLE.replace("id: ownership_reconciled", "id: ownership-reconciled"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("tier: high", "tier: critical"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace('reason: "Unresolved true match"', 'reason: ""'), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace('reason: "Unresolved true match"', 'reason: "a\\u0000b"'), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace('reason: "Unresolved true match"', "reason: " + "x" * 501), R.LIMIT_EXCEEDED),
        (PRD_EXAMPLE.replace("  - id: screening_clear", "\t- id: screening_clear"), R.YAML_INVALID),
        (PRD_EXAMPLE.replace("tier: blocked,", "tier: blocked, score: 101,"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("tier: blocked,", "tier: blocked, score: -1,"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("tier: blocked,", "tier: blocked, score: 2.5,"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("tier: blocked,", "tier: blocked, score: true,"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nstatus: live"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nscore_thresholds: {low: 1}"), R.INVALID_VALUE),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nscore_thresholds: {severe: 1}"), R.UNKNOWN_KEY),
        (
            PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nscore_thresholds: {medium: 50, high: 50}"),
            R.INVALID_VALUE,
        ),
        (PRD_EXAMPLE.replace("version: 1.2.0", "version: 1.2.0\nscore_thresholds: {high: 0}"), R.INVALID_VALUE),
        ("policy: p\nversion: 1.0.0\nrules: []\n", R.INVALID_VALUE),
        ("policy: p\nversion: 1.0.0\n", R.MISSING_FIELD),
        ("policy: p\nversion: 1.0.0\nrules: [registry_active]\n", R.INVALID_VALUE),
        ("policy: p\nversion: 1.0.0\nrules:\n  - {id: r, effect: {tier: high, reason: r}}\n", R.MISSING_FIELD),
        ("policy: p\nversion: 1.0.0\nrules:\n  - {id: r, when: {a: {gt: 0}}}\n", R.MISSING_FIELD),
        ("policy: p\nversion: 1.0.0\nrules:\n  - {id: r, when: {a: {gt: 0}}, effect: {tier: high}}\n", R.MISSING_FIELD),
        ("", R.INVALID_VALUE),
        ("- just\n- a list\n", R.INVALID_VALUE),
        ("policy: [unclosed\n", R.YAML_INVALID),
        (PRD_EXAMPLE + "---\npolicy: second\n", R.YAML_INVALID),
        ("policy: p\npolicy: q\nversion: 1.0.0\n", R.DUPLICATE_KEY),
        (_policy_with_when("{a: {gt: 0}, a: {lt: 5}}"), R.DUPLICATE_KEY),
        ("base: &b {tier: high, reason: r}\n" + PRD_EXAMPLE, R.UNKNOWN_KEY),
        (
            "policy: p\nversion: 1.0.0\nrules:\n"
            "  - id: a\n    when: {x: {gt: 0}}\n    effect: &e {tier: high, reason: r}\n"
            "  - id: b\n    when: {x: {gt: 1}}\n    effect: *e\n",
            R.YAML_ALIAS,
        ),
        (
            "policy: p\nversion: 1.0.0\nrules:\n  - id: a\n    when: {x: {gt: 0}}\n"
            "    effect: {<<: {tier: high}, reason: r}\n",
            R.YAML_ALIAS,
        ),
        ("policy: !!binary aGVsbG8=\nversion: 1.0.0\n", R.YAML_INVALID),
        ("policy: !custom p\n", R.YAML_INVALID),
        ("? [a, b]\n: 1\n", R.INVALID_VALUE),
    ],
)
def test_malformed_policies_are_rejected_at_load(text: str, reason: PolicyLoadReason) -> None:
    assert _reason_of(text) is reason


def test_non_utf8_oversize_and_deeply_nested_files_are_rejected() -> None:
    with pytest.raises(PolicyLoadError) as info:
        load_policy_bytes(b"policy: \xff\xfe\n")
    assert info.value.reason is R.ENCODING_INVALID

    with pytest.raises(PolicyLoadError) as info:
        load_policy_bytes(b"#" * (MAX_POLICY_BYTES + 1))
    assert info.value.reason is R.TOO_LARGE

    with pytest.raises(PolicyLoadError) as info:
        load_policy_bytes(b"[" * 100_000)
    assert info.value.reason in (R.LIMIT_EXCEEDED, R.YAML_INVALID)


def test_too_many_rules_are_rejected() -> None:
    rules = "".join(
        f"  - {{id: r{i}, when: {{a: {{gt: {i}}}}}, effect: {{tier: low, reason: r}}}}\n" for i in range(MAX_RULES + 1)
    )
    assert _reason_of(f"policy: p\nversion: 1.0.0\nrules:\n{rules}") is R.LIMIT_EXCEEDED


def test_errors_name_the_source_reason_and_location() -> None:
    with pytest.raises(PolicyLoadError) as info:
        _load(_policy_with_when("{all: [{a: {eq: 1}}, {b: {between: [1, 2]}}]}"))
    error = info.value
    assert error.source == "test.yaml"
    assert error.location == "rules[0].when.all[1].b.between"
    assert str(error).startswith("test.yaml: policy_unknown_operator at rules[0].when.all[1].b.between: ")


def test_rejections_are_logged_and_counted_by_reason() -> None:
    labels = {"outcome": "rejected", "reason": R.DUPLICATE_RULE_ID.value}
    before = REGISTRY.get_sample_value("agenticorg_policy_load_total", labels) or 0.0
    with structlog.testing.capture_logs() as logs:
        _reason_of(PRD_EXAMPLE.replace("id: ownership_reconciled", "id: registry_active"))
    assert REGISTRY.get_sample_value("agenticorg_policy_load_total", labels) == before + 1
    assert [entry["reason"] for entry in logs if entry["event"] == "policy_load_rejected"] == [
        R.DUPLICATE_RULE_ID.value
    ]


# ── Files and directories ───────────────────────────────────────────────────


def test_unreadable_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PolicyLoadError) as info:
        load_policy(tmp_path / "absent.yaml")
    assert info.value.reason is R.FILE_UNREADABLE


def test_load_policy_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "uk.yaml"
    path.write_bytes(PRD_EXAMPLE.encode("utf-8"))
    policy = load_policy(path)
    assert policy.source == str(path)
    assert policy.content_hash == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_policy_directory_rejects_duplicate_ids_empty_and_missing_directories(tmp_path: Path) -> None:
    with pytest.raises(PolicyLoadError) as info:
        load_policies(tmp_path / "absent")
    assert info.value.reason is R.DIRECTORY_INVALID

    with pytest.raises(PolicyLoadError) as info:
        load_policies(tmp_path)
    assert info.value.reason is R.DIRECTORY_INVALID

    (tmp_path / "a.yaml").write_text(PRD_EXAMPLE, encoding="utf-8")
    (tmp_path / "b.yml").write_text(PRD_EXAMPLE.replace("1.2.0", "1.3.0"), encoding="utf-8")
    with pytest.raises(PolicyLoadError) as info:
        load_policies(tmp_path)
    assert info.value.reason is R.DUPLICATE_POLICY


def test_one_invalid_file_refuses_the_whole_directory(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(PRD_EXAMPLE, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(PRD_EXAMPLE.replace("gt: 0", "gt: zero"), encoding="utf-8")
    with pytest.raises(PolicyLoadError) as info:
        load_policies(tmp_path)
    assert info.value.reason is R.INVALID_OPERAND
    assert info.value.source.endswith("b.yaml")


# ── Review hardening ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        _policy_with_when("{a: {gt: !!int abc}}"),
        _policy_with_when("{a: {gt: !!float abc}}"),
        _policy_with_when("{a: {eq: !!timestamp notadate}}"),
        "policy: !!str p\nversion: 1.0.0\n",
        "policy: ! p\nversion: 1.0.0\n",
        "policy: p\nversion: 1.0.0\nrules: !!seq []\n",
        "policy: p\nversion: 1.0.0\nrules:\n  - !!map {id: r, when: {a: {gt: 0}}, effect: {tier: high, reason: r}}\n",
    ],
)
def test_explicit_yaml_tags_are_refused_with_a_reason(text: str) -> None:
    with structlog.testing.capture_logs() as logs:
        assert _reason_of(text) is R.YAML_INVALID
    assert [entry["reason"] for entry in logs if entry["event"] == "policy_load_rejected"] == [R.YAML_INVALID.value]


def test_an_integer_longer_than_the_digit_limit_is_refused_with_a_reason() -> None:
    digits = "9" * 5000
    assert _reason_of(_policy_with_when(f"{{a: {{gt: {digits}}}}}")) is R.YAML_INVALID


@pytest.mark.parametrize("operand", [str(2**53 + 1), str(-(2**53) - 1), "1" * 40])
def test_integer_operands_outside_the_safe_range_are_refused(operand: str) -> None:
    assert _reason_of(_policy_with_when(f"{{a: {{gt: {operand}}}}}")) is R.INVALID_OPERAND
    assert _reason_of(_policy_with_when(f"{{a: {{in: [{operand}]}}}}")) is R.INVALID_OPERAND


def test_integer_operands_at_the_safe_limit_load() -> None:
    _load(_policy_with_when(f"{{a: {{lte: {2**53}}}}}"))


@pytest.mark.parametrize(
    "reviewer",
    ["TODO", "tbd", "T.B.D.", "n/a", "N/A", "none", "x", "-", "??", "xxx", "Reviewer", "placeholder", "  todo  ", "zz"],
)
def test_placeholder_reviewers_are_refused(reviewer: str) -> None:
    production = PRD_EXAMPLE.replace("version: 1.2.0", f"version: 1.2.0\nstatus: production\nreviewed_by: '{reviewer}'")
    assert _reason_of(production) in (R.PRODUCTION_UNREVIEWED, R.INVALID_VALUE)
    if reviewer.strip():
        assert _reason_of(production) is R.PRODUCTION_UNREVIEWED
    example = PRD_EXAMPLE.replace("version: 1.2.0", f"version: 1.2.0\nreviewed_by: '{reviewer}'")
    assert _reason_of(example) is R.INVALID_VALUE


def test_named_reviewers_are_accepted() -> None:
    for reviewer in ("Compliance Owner A", "J. Doe", "mlro@example.com", "Li"):
        text = PRD_EXAMPLE.replace("version: 1.2.0", f"version: 1.2.0\nstatus: production\nreviewed_by: '{reviewer}'")
        assert _load(text).reviewed_by == reviewer


def test_policy_directory_loads_upper_case_suffixes(tmp_path: Path) -> None:
    (tmp_path / "A.YAML").write_text(PRD_EXAMPLE, encoding="utf-8")
    (tmp_path / "b.Yml").write_text(PRD_EXAMPLE.replace("business_onboarding_uk", "second_policy"), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a policy", encoding="utf-8")
    assert sorted(load_policies(tmp_path)) == ["business_onboarding_uk", "second_policy"]
