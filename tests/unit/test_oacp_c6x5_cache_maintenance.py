from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    plan_oacp_artifact_cache_maintenance,
)

C6X5_DOC_PATH = Path("docs/reports/commerce-agent-c6x5-oacp-cache-maintenance.md")


def _doc() -> str:
    return C6X5_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X5",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X5",),
        evidence_refs=("evidence_price_C6X5_redacted",),
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
        verifier_result_ref="verifier_result_price_C6X5",
    )
    return replace(base, **overrides)


def test_c6x5_maintenance_plan_classifies_cache_records_without_execution() -> None:
    records = (
        _record(cache_record_id="cache_keep_C6X5"),
        _record(cache_record_id="cache_refresh_C6X5", expires_at="2026-06-11T00:01:45.000Z"),
        _record(cache_record_id="cache_expired_C6X5", expires_at="2026-06-11T00:00:59.000Z"),
        _record(cache_record_id="cache_revoked_C6X5", revocation_snapshot_status="revoked"),
        _record(cache_record_id="cache_ambiguous_C6X5", revocation_snapshot_status="unknown"),
        _record(cache_record_id="cache_mismatch_C6X5", tenant_id="cten_other"),
        _record(cache_record_id="cache_private_C6X5", evidence_refs=("raw_jwt_ref",)),
        _record(cache_record_id="cache_source_C6X5", freshness_status="stale"),
        _record(cache_record_id="cache_unsafe_C6X5", allowed_to_execute=True),
    )

    plan = plan_oacp_artifact_cache_maintenance(
        records=records,
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        action_intent="non_binding_preview",
        risk_tier="low",
        scope_filter=OacpArtifactCacheRepositoryQuery(tenant_id="cten_C6W3"),
    )

    assert plan["records_seen"] == len(records)
    assert plan["records_kept"] == ["cache_keep_C6X5"]
    assert set(plan["records_to_refresh"]) == {"cache_refresh_C6X5", "cache_source_C6X5"}
    assert set(plan["records_to_evict"]) == {"cache_expired_C6X5", "cache_revoked_C6X5"}
    assert set(plan["records_to_quarantine"]) == {
        "cache_ambiguous_C6X5",
        "cache_mismatch_C6X5",
        "cache_private_C6X5",
        "cache_unsafe_C6X5",
    }
    assert plan["records_requiring_human_review"] == []
    assert plan["allowed_to_execute"] is False
    assert plan["no_execution"] is True
    assert plan["non_authoritative_for_transaction"] is True
    assert plan["no_checkout_payment_enablement"] is True
    assert plan["no_live_provider_enablement"] is True
    assert plan["no_public_discovery_enablement"] is True

    outcomes = {action["cache_record_id"]: action["maintenance_outcome"] for action in plan["record_actions"]}
    assert outcomes["cache_keep_C6X5"] == "keep_usable"
    assert outcomes["cache_refresh_C6X5"] == "refresh_recommended"
    assert outcomes["cache_expired_C6X5"] == "evict_expired"
    assert outcomes["cache_revoked_C6X5"] == "purge_revoked"
    assert outcomes["cache_ambiguous_C6X5"] == "quarantine_ambiguous_revocation"
    assert outcomes["cache_mismatch_C6X5"] == "quarantine_scope_mismatch"
    assert outcomes["cache_private_C6X5"] == "quarantine_private_or_raw_ref"
    assert outcomes["cache_source_C6X5"] == "source_refresh_needed"
    assert outcomes["cache_unsafe_C6X5"] == "blocked_unsafe"
    assert "raw_jwt_ref" not in plan["evidence_refs"]


def test_c6x5_final_commitment_is_prepared_only_and_stricter() -> None:
    records = (
        _record(cache_record_id="cache_commitment_refresh_C6X5", risk_tier="low"),
        _record(
            cache_record_id="cache_human_review_C6X5",
            artifact_id="protocol_adapter_C6X5",
            artifact_type="protocol_adapter",
            expires_at="2026-06-11T12:00:00.000Z",
            ttl_policy_seconds=24 * 60 * 60,
        ),
    )

    plan = plan_oacp_artifact_cache_maintenance(
        records=records,
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        action_intent="final_commitment",
        risk_tier="medium",
    )

    assert plan["records_to_refresh"] == ["cache_commitment_refresh_C6X5"]
    assert plan["records_requiring_human_review"] == ["cache_human_review_C6X5"]
    assert plan["allowed_to_execute"] is False
    assert plan["no_execution"] is True

    reasons = plan["per_record_reason_codes"]
    assert reasons["cache_commitment_refresh_C6X5"] == ["final_commitment_requires_fresh_source_posture"]
    assert reasons["cache_human_review_C6X5"] == ["artifact_not_transaction_authority"]


def test_c6x5_maintenance_plan_supports_batch_limits_and_source_safe_refs() -> None:
    plan = plan_oacp_artifact_cache_maintenance(
        records=(
            _record(cache_record_id="cache_one_C6X5"),
            _record(cache_record_id="cache_two_C6X5", evidence_refs=("evidence_two_C6X5_redacted",)),
        ),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        action_intent="prepare_only",
        risk_tier="low",
        max_batch_size=1,
    )

    assert plan["records_seen"] == 1
    assert plan["records_kept"] == ["cache_one_C6X5"]
    assert plan["evidence_refs"] == ["evidence_price_C6X5_redacted"]
    assert plan["grantex_runtime_required"] is False


def test_c6x5_docs_capture_planner_boundaries_and_no_scheduler() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Maintenance Planner Inputs",
        "Maintenance Outcomes",
        "Plan Output",
        "Fail-Closed Rules",
        "Migration And Scheduler Decision",
        "Guardrails",
        "What C6X5 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "planner only" in doc
    assert "no migration" in doc
    assert "no scheduler" in doc
    assert "allowed_to_execute = false" in doc
    assert "does not call Grantex live" in doc
    assert "does not call providers" in doc
