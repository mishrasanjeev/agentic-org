from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpArtifactCacheRepositoryQuery,
    OacpAuditExportReviewRetentionClass,
    OacpAuditReviewManifestRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_c6x9_audit_export_bundle,
    build_oacp_c6y1_audit_export_review_manifest,
    build_oacp_c6y3_audit_review_manifest_summary,
    build_oacp_c6y4_retention_disposition_dry_run,
    build_oacp_c6y4_retention_operator_review_packet,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)

C6Y4_DOC_PATH = Path("docs/reports/commerce-agent-c6y4-oacp-retention-disposition.md")


def _doc() -> str:
    return C6Y4_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6Y4",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6Y4",),
        evidence_refs=("evidence_price_C6Y4_redacted",),
        generated_at="2026-06-12T00:00:00.000Z",
        cached_at="2026-06-12T00:00:10.000Z",
        expires_at="2026-06-12T00:05:00.000Z",
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_observed_at="2026-06-12T00:00:30.000Z",
        revocation_snapshot_age_seconds=30,
        ttl_policy_seconds=300,
        risk_tier="low",
        blocked_capabilities=("checkout_payment_creation", "live_provider_call"),
        unsupported_capabilities=("transaction_authority_from_adapter_preview",),
        verifier_result_ref="verifier_result_price_C6Y4",
    )
    return replace(base, **overrides)


def _plan(records: tuple[OacpPersistentArtifactCacheRecord, ...]) -> dict[str, object]:
    return plan_oacp_artifact_cache_maintenance(
        records=records,
        now_iso="2026-06-12T00:01:00.000Z",
        grantex_available=True,
        action_intent="prepare_only",
        risk_tier="low",
        scope_filter=OacpArtifactCacheRepositoryQuery(tenant_id="cten_C6W3", merchant_id="mch_C6W3"),
    )


def _review_packet(plan: dict[str, object]) -> dict[str, object]:
    return build_oacp_cache_maintenance_dry_run_report(
        maintenance_plan=plan,
        report_kind="operator_review_packet",
    )


def _decision(packet: dict[str, object]) -> dict[str, object]:
    return build_oacp_cache_operator_decision_record(
        review_packet=packet,
        decision_kind="request_more_evidence",
        reviewer_identity_ref="operator_ref_c6y4_reviewer_001",
        decided_at="2026-06-12T00:02:00.000Z",
    )


def _bundle(records: tuple[OacpPersistentArtifactCacheRecord, ...]) -> dict[str, object]:
    plan = _plan(records)
    packet = _review_packet(plan)
    decision = _decision(packet)
    return build_oacp_c6x9_audit_export_bundle(
        cache_records=records,
        maintenance_plans=(plan,),
        review_packets=(packet,),
        operator_decision_records=(decision,),
        durable_decision_records=(dict(decision),),
        generated_at="2026-06-12T00:03:00.000Z",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
    )


def _manifest(
    *,
    retention_class: OacpAuditExportReviewRetentionClass = "standard_internal_review",
    generated_at: str = "2026-06-12T00:04:00.000Z",
) -> dict[str, object]:
    return build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=_bundle((_record(),)),
        generated_at=generated_at,
        retention_class=retention_class,
    )


def _summary(
    *,
    retention_class: OacpAuditExportReviewRetentionClass = "standard_internal_review",
    generated_at: str = "2026-06-12T00:05:00.000Z",
) -> dict[str, object]:
    return build_oacp_c6y3_audit_review_manifest_summary(
        (_manifest(retention_class=retention_class),),
        query=OacpAuditReviewManifestRepositoryQuery(
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            retention_class=retention_class,
        ),
        generated_at=generated_at,
    )


def _disposition_values(packet: dict[str, object]) -> set[str]:
    return {
        str(item["disposition"])
        for item in packet["disposition_previews"]
        if isinstance(item, dict)
    }


def test_c6y4_retention_disposition_dry_run_and_operator_packet_retain_without_execution() -> None:
    summary = _summary()
    dry_run = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary=summary,
        generated_at="2026-06-12T00:06:00.000Z",
    )
    packet = build_oacp_c6y4_retention_operator_review_packet(
        retention_disposition_dry_run=dry_run,
        generated_at="2026-06-12T00:07:00.000Z",
    )

    assert dry_run["packet_built"] is True
    assert dry_run["packet_kind"] == "oacp_retention_disposition_dry_run"
    assert _disposition_values(dry_run) == {"retain"}
    assert dry_run["future_retention_action_allowed"] is False
    assert dry_run["records_deleted"] is False
    assert dry_run["allowed_to_execute"] is False
    assert dry_run["no_export_file_written"] is True
    assert packet["packet_built"] is True
    assert packet["packet_kind"] == "oacp_retention_disposition_operator_review_packet"
    assert packet["operator_review_packet_only"] is True
    assert packet["allowed_to_execute"] is False
    assert packet["non_authoritative_for_transaction"] is True
    assert "evidence_price_C6Y4_redacted" not in str(dry_run)
    assert "evidence_price_C6Y4_redacted" not in str(packet)


def test_c6y4_retention_disposition_flags_due_and_legal_hold_for_review_only() -> None:
    summary = _summary(
        retention_class="legal_hold_candidate",
        generated_at="2028-06-12T00:05:00.000Z",
    )
    dry_run = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary=summary,
        generated_at="2028-06-12T00:06:00.000Z",
    )

    assert dry_run["packet_built"] is True
    assert _disposition_values(dry_run) == {"legal_hold_review", "retention_due_review"}
    assert dry_run["retention_due_count"] == 1
    assert dry_run["legal_hold_candidate_count"] == 1
    assert dry_run["retention_executed"] is False
    assert dry_run["records_deleted"] is False
    assert dry_run["allowed_to_execute"] is False


def test_c6y4_retention_disposition_requests_redaction_review_without_evidence_values() -> None:
    summary = dict(_summary())
    summary["redacted_evidence_ref_count"] = 0
    summary["redacted_evidence_ref_counts"] = {}
    dry_run = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary=summary,
        generated_at="2026-06-12T00:06:00.000Z",
    )

    assert dry_run["packet_built"] is True
    assert _disposition_values(dry_run) == {"redaction_review_required"}
    assert dry_run["redacted_evidence_ref_count"] == 0
    assert dry_run["allowed_to_execute"] is False
    assert "evidence_price_C6Y4_redacted" not in str(dry_run)


def test_c6y4_retention_disposition_fails_closed_for_unsafe_inputs() -> None:
    summary = _summary()
    missing_scope = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary={**summary, "tenant_id": ""},
        generated_at="2026-06-12T00:06:00.000Z",
    )
    executable = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary={**summary, "allowed_to_execute": True},
        generated_at="2026-06-12T00:06:00.000Z",
    )
    evidence_values = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary={**summary, "redacted_evidence_refs": ["evidence_price_C6Y4_redacted"]},
        generated_at="2026-06-12T00:06:00.000Z",
    )
    bad_generated_at = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary=summary,
        generated_at="not-a-time",
    )
    unsafe_packet = build_oacp_c6y4_retention_operator_review_packet(
        retention_disposition_dry_run={"packet_id": "dry_run_C6Y4", "allowed_to_execute": True},
        generated_at="2026-06-12T00:07:00.000Z",
    )

    assert missing_scope["refusal_code"] == "summary_identity_or_scope_missing"
    assert executable["refusal_code"] == "summary_non_enablement_flags_invalid"
    assert evidence_values["refusal_code"] == "summary_contains_evidence_ref_values"
    assert bad_generated_at["refusal_code"] == "disposition_generated_at_invalid"
    assert unsafe_packet["refusal_code"] == "dry_run_missing_or_unsafe"
    for result in (missing_scope, executable, evidence_values, bad_generated_at, unsafe_packet):
        assert result["packet_built"] is False
        assert result["allowed_to_execute"] is False
        assert result["future_retention_action_allowed"] is False
        assert result["records_deleted"] is False


def test_c6y4_docs_capture_retention_disposition_boundary_and_non_goals() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Disposition Dry-Run Outcomes",
        "Operator Review Packet",
        "Fail-Closed Rules",
        "Migration Scheduler CLI And Export Writer Decision",
        "Guardrails",
        "What C6Y4 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg owns audit review manifest handling" in doc
    assert "not a transaction toll booth" in doc
    assert "allowed_to_execute = false" in doc
    assert "future_retention_action_allowed = false" in doc
    assert "records_deleted = false" in doc
    assert "does not write export files" in doc
    assert "does not call Grantex live" in doc
