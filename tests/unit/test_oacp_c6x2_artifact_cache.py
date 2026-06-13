from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpPersistentArtifactCacheRecord,
    evaluate_oacp_persistent_artifact_cache_record,
)

C6X2_DOC_PATH = Path("docs/reports/commerce-agent-c6x2-oacp-artifact-cache-runtime.md")


def _doc() -> str:
    return C6X2_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X2",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X2",),
        evidence_refs=("evidence_price_C6X2_redacted",),
        generated_at="2026-06-11T00:00:00.000Z",
        cached_at="2026-06-11T00:00:10.000Z",
        expires_at="2026-06-11T00:05:00.000Z",
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_observed_at="2026-06-11T00:00:30.000Z",
        revocation_snapshot_age_seconds=30,
        ttl_policy_seconds=300,
        risk_tier="low",
        blocked_capabilities=("checkout_payment_creation", "live_provider_call"),
        unsupported_capabilities=("transaction_authority_from_adapter_preview",),
        verifier_result_ref="verifier_result_price_C6X2",
    )
    return replace(base, **overrides)


def test_c6x2_persistent_cache_supports_all_internal_scopes_without_grantex_toll_booth() -> None:
    records = [
        _record(scope_kind="buyer_agent"),
        _record(
            cache_record_id="cache_seller_C6X2",
            scope_kind="seller_agent",
            buyer_agent_id=None,
            artifact_id="seller_agent_capability_C6X2",
            artifact_type="seller_agent_capability",
            ttl_policy_seconds=6 * 60 * 60,
        ),
        _record(
            cache_record_id="cache_tenant_C6X2",
            scope_kind="tenant",
            merchant_id=None,
            seller_agent_id=None,
            buyer_agent_id=None,
            artifact_id="policy_C6X2",
            artifact_type="policy",
            ttl_policy_seconds=6 * 60 * 60,
        ),
        _record(
            cache_record_id="cache_merchant_C6X2",
            scope_kind="merchant",
            seller_agent_id=None,
            buyer_agent_id=None,
            artifact_id="merchant_capability_C6X2",
            artifact_type="merchant_capability",
            ttl_policy_seconds=24 * 60 * 60,
        ),
    ]

    for record in records:
        result = evaluate_oacp_persistent_artifact_cache_record(
            record=record,
            action_intent="non_binding_preview",
            now_iso="2026-06-11T00:01:00.000Z",
            grantex_available=False,
            expected_scope={"tenant_id": "cten_C6W3"},
        )

        assert result["evaluated"] is True
        assert result["status"] == "usable_for_non_binding_cache"
        assert result["allowed_to_preview"] is True
        assert result["allowed_to_prepare"] is False
        assert result["allowed_to_execute"] is False
        assert result["non_authoritative_for_transaction"] is True
        assert result["no_checkout_payment_enablement"] is True
        assert result["no_live_provider_enablement"] is True
        assert result["no_public_discovery_enablement"] is True
        assert result["grantex_runtime_required"] is False
        assert result["commerce_facts_invented"] is False


def test_c6x2_final_commitment_remains_prepared_only_for_valid_cache() -> None:
    result = evaluate_oacp_persistent_artifact_cache_record(
        record=_record(),
        action_intent="final_commitment",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        expected_scope={
            "tenant_id": "cten_C6W3",
            "merchant_id": "mch_C6W3",
            "seller_agent_id": "seller_C6W3",
            "buyer_agent_id": "buyer_C6W3",
        },
    )

    assert result["evaluated"] is True
    assert result["status"] == "prepared_only_for_commitment_boundary"
    assert result["allowed_to_prepare"] is True
    assert result["allowed_to_execute"] is False
    assert result["prepared_only"] is True
    assert "no execution occurs" in result["buyer_safe_message"]


def test_c6x2_cache_evaluation_fails_closed_for_unsafe_or_ambiguous_states() -> None:
    cases = [
        (_record(expires_at="2026-06-11T00:00:30.000Z"), "expired", "cache_record_expired"),
        (_record(freshness_status="stale"), "stale", "cache_freshness_stale"),
        (_record(revocation_snapshot_status="revoked"), "revoked", "cache_record_revoked"),
        (_record(evidence_refs=("raw_jwt_ref",)), "unsafe", "cache_refs_missing_or_private"),
        (_record(no_live_provider_enablement=False), "unsafe", "non_enablement_flags_missing"),
    ]

    for record, status, refusal_code in cases:
        result = evaluate_oacp_persistent_artifact_cache_record(
            record=record,
            action_intent="non_binding_preview",
            now_iso="2026-06-11T00:01:00.000Z",
            grantex_available=True,
            expected_scope={"tenant_id": "cten_C6W3"},
        )

        assert result["evaluated"] is False
        assert result["status"] == status
        assert result["refusal_code"] == refusal_code
        assert result["allowed_to_execute"] is False


def test_c6x2_cache_rejects_scope_mismatch_and_non_authority_artifacts_for_commitment() -> None:
    mismatch = evaluate_oacp_persistent_artifact_cache_record(
        record=_record(),
        action_intent="prepare_only",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        expected_scope={"merchant_id": "other_merchant"},
    )
    assert mismatch["status"] == "mismatched"
    assert mismatch["refusal_code"] == "cache_scope_mismatch"

    adapter = evaluate_oacp_persistent_artifact_cache_record(
        record=_record(
            artifact_id="protocol_adapter_C6X2",
            artifact_type="protocol_adapter",
            ttl_policy_seconds=24 * 60 * 60,
        ),
        action_intent="final_commitment",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        expected_scope={"tenant_id": "cten_C6W3"},
    )
    assert adapter["status"] == "blocked"
    assert adapter["refusal_code"] == "artifact_not_transaction_authority"
    assert adapter["allowed_to_execute"] is False


def test_c6x2_docs_capture_runtime_cache_boundary_and_guardrails() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Persistent Cache Record Model",
        "Cache Evaluation Helper",
        "Freshness, Revocation, And TTL",
        "Non-Binding Versus Commitment-Bound Use",
        "Persistence And Migration Decision",
        "Fail-Closed Rules",
        "Guardrails",
        "What C6X2 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg remains the buyer and seller AI-agent runtime" in doc
    assert "Grantex remains the trust, protocol, policy, and canonical OACP artifact authority" in doc
    assert "without routing every non-binding turn through Grantex" in doc
    assert "no DB migration" in doc
    assert "no checkout or payment enablement" in doc
    assert "no live provider rail enablement" in doc
    assert "no merchant private API execution" in doc
