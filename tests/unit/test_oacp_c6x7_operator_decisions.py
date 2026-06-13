from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)

C6X7_DOC_PATH = Path("docs/reports/commerce-agent-c6x7-oacp-operator-decisions.md")


def _doc() -> str:
    return C6X7_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X7",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X7",),
        evidence_refs=("evidence_price_C6X7_redacted",),
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
        verifier_result_ref="verifier_result_price_C6X7",
    )
    return replace(base, **overrides)


def _review_packet() -> dict[str, object]:
    plan = plan_oacp_artifact_cache_maintenance(
        records=(
            _record(cache_record_id="cache_refresh_C6X7", expires_at="2026-06-11T00:01:45.000Z"),
            _record(cache_record_id="cache_revoked_C6X7", revocation_snapshot_status="revoked"),
            _record(cache_record_id="cache_review_C6X7", artifact_type="protocol_adapter", risk_tier="high"),
        ),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        action_intent="final_commitment",
        risk_tier="medium",
        scope_filter=OacpArtifactCacheRepositoryQuery(tenant_id="cten_C6W3"),
    )
    return build_oacp_cache_maintenance_dry_run_report(
        maintenance_plan=plan,
        report_kind="operator_review_packet",
    )


def test_c6x7_operator_decision_record_is_audit_safe_and_non_executing() -> None:
    decision = build_oacp_cache_operator_decision_record(
        review_packet=_review_packet(),
        decision_kind="approve_future_refresh_request",
        reviewer_identity_ref="operator_ref_c6x7_reviewer_001",
        decided_at="2026-06-11T00:02:00.000Z",
    )

    assert decision["decision_kind"] == "approve_future_refresh_request"
    assert decision["status"] == "recorded_for_future_review"
    assert decision["review_packet_id"]
    assert decision["maintenance_plan_id"]
    assert decision["scope_summary"]["buyer_agent"] == {"buyer_C6W3": 3}
    assert decision["artifact_families_affected"] == ["price", "protocol_adapter"]
    assert "cache_refresh_C6X7" in decision["redacted_reason_codes"]
    assert decision["reviewer_identity_ref"] == "operator_ref_c6x7_reviewer_001"
    assert decision["next_step_labels"] == ["future_refresh_request_label_only_no_api_call"]
    assert decision["allowed_to_execute"] is False
    assert decision["no_execution"] is True
    assert decision["operator_decision_only"] is True
    assert decision["audit_safe_decision_record"] is True
    assert decision["non_authoritative_for_transaction"] is True
    assert decision["no_checkout_payment_enablement"] is True
    assert decision["no_live_provider_enablement"] is True
    assert decision["no_public_discovery_enablement"] is True
    assert decision["raw_payloads_included"] is False
    assert decision["grantex_runtime_required"] is False


def test_c6x7_supports_known_future_or_review_decision_kinds() -> None:
    packet = _review_packet()
    for decision_kind in (
        "approve_future_refresh_request",
        "approve_future_eviction_request",
        "approve_future_quarantine_request",
        "request_more_evidence",
        "reject_maintenance_action",
        "defer_until_freshness_update",
        "escalate_to_human_support",
        "block_unsafe_action",
    ):
        decision = build_oacp_cache_operator_decision_record(
            review_packet=packet,
            decision_kind=decision_kind,
            reviewer_identity_ref="reviewer_ref_c6x7_safe_opaque",
        )

        assert decision["status"] == "recorded_for_future_review"
        assert decision["decision_kind"] == decision_kind
        assert decision["allowed_to_execute"] is False
        assert "http" not in " ".join(decision["next_step_labels"])


def test_c6x7_blocks_missing_executable_private_or_non_opaque_inputs() -> None:
    missing = build_oacp_cache_operator_decision_record(
        review_packet=None,
        decision_kind="approve_future_refresh_request",
        reviewer_identity_ref="operator_ref_c6x7_reviewer_001",
    )
    assert missing["status"] == "blocked"
    assert missing["block_reason"] == "review_packet_missing"

    executable_packet = dict(_review_packet())
    executable_packet["allowed_to_execute"] = True
    executable = build_oacp_cache_operator_decision_record(
        review_packet=executable_packet,
        decision_kind="approve_future_eviction_request",
        reviewer_identity_ref="operator_ref_c6x7_reviewer_001",
    )
    assert executable["block_reason"] == "review_packet_executable_or_enabling"
    assert executable["no_execution"] is True

    private_ref_packet = dict(_review_packet())
    private_ref_packet["evidence_refs"] = ["raw_jwt_c6x7_marker"]
    private_ref = build_oacp_cache_operator_decision_record(
        review_packet=private_ref_packet,
        decision_kind="request_more_evidence",
        reviewer_identity_ref="operator_ref_c6x7_reviewer_001",
    )
    assert private_ref["block_reason"] == "review_packet_private_or_missing_refs"

    non_opaque_reviewer = build_oacp_cache_operator_decision_record(
        review_packet=_review_packet(),
        decision_kind="request_more_evidence",
        reviewer_identity_ref="reviewer_ref_phone_marker",
    )
    assert non_opaque_reviewer["block_reason"] == "reviewer_identity_not_opaque"

    immediate_action = build_oacp_cache_operator_decision_record(
        review_packet=_review_packet(),
        decision_kind="execute_maintenance_now",
        reviewer_identity_ref="operator_ref_c6x7_reviewer_001",
    )
    assert immediate_action["block_reason"] == "unsupported_or_executable_decision_kind"
    assert immediate_action["allowed_to_execute"] is False


def test_c6x7_docs_capture_decision_boundaries_and_no_persistence() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Decision Kinds",
        "Decision Record Output",
        "Fail-Closed Rules",
        "Migration Scheduler And Persistence Decision",
        "Guardrails",
        "What C6X7 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "operator decision record only" in doc
    assert "allowed_to_execute = false" in doc
    assert "opaque reviewer reference" in doc
    assert "does not call Grantex live" in doc
    assert "adds no migration" in doc
