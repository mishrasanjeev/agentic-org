from __future__ import annotations

from pathlib import Path

C6X1_DOC_PATH = Path("docs/reports/commerce-agent-c6x1-oacp-cache-verifier-runtime-planning.md")


def _doc() -> str:
    return C6X1_DOC_PATH.read_text(encoding="utf-8")


def test_c6x1_documents_correct_ownership_and_cache_boundaries() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Persistent Cache Model",
        "Grantex Issuance And Verifier Boundary",
        "Non-Binding Cache Use",
        "Commitment-Bound Use",
        "Freshness, Revocation, And TTL Defaults",
        "Evidence References",
        "Fail-Closed Rules",
        "Guardrails",
        "What This Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg remains the buyer and seller AI-agent runtime" in doc
    assert "Grantex remains the trust, protocol, policy, and canonical OACP artifact authority" in doc
    assert "Merchant systems remain operational sources of record" in doc
    assert "Provider and fintech rails own mandate and payment execution" in doc
    assert "Valid cached OACP artifacts may support non-binding interactions" in doc


def test_c6x1_keeps_planning_slice_non_enabling() -> None:
    doc = _doc()
    for required_boundary in (
        "docs/tests/planning-first only",
        "no runtime code",
        "no public endpoint",
        "no public OACP publication",
        "no checkout or payment enablement",
        "no live provider rail enablement",
        "no merchant private API execution",
        "no production config change",
        "no production allowlist assignment",
    ):
        assert required_boundary in doc


def test_c6x1_requires_freshness_revocation_and_fail_closed_cache_posture() -> None:
    doc = _doc()
    for required_posture in (
        "issued-at, received-at, and expires-at timestamps",
        "TTL policy and freshness status",
        "revocation snapshot reference and age",
        "non-sensitive evidence references",
        "blocked capability wording",
        "unsupported capability wording",
        "Missing, stale, or ambiguous revocation posture is fail-closed",
        "adapter preview tries to override missing, expired, or revoked canonical artifacts",
    ):
        assert required_posture in doc


def test_c6x1_does_not_reintroduce_old_all_through_grantex_or_readiness_wording() -> None:
    doc = _doc()
    for forbidden_phrase in (
        " ".join(("Grantex is the transaction/control plane", "for every interaction")),
        " ".join(("all provider interaction happens", "through Grantex")),
        " ".join(("AgenticOrg only calls Grantex", "for everything")),
        " ".join(("merchant systems connect only", "to Grantex")),
        " ".join(("Grantex owns payment", "mandate setup")),
        " ".join(("OACP is", "public")),
        " ".join(("OACP is", "standard")),
        " ".join(("OACP is", "cert" + "ified")),
        " ".join(("production", "ready")),
        " ".join(("execution", "ready")),
    ):
        assert forbidden_phrase not in doc
