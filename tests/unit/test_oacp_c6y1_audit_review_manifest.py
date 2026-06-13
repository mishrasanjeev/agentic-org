from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_c6x9_audit_export_bundle,
    build_oacp_c6y1_audit_export_review_manifest,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)

C6Y1_DOC_PATH = Path("docs/reports/commerce-agent-c6y1-oacp-audit-review-manifest.md")


def _doc() -> str:
    return C6Y1_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6Y1",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6Y1",),
        evidence_refs=("evidence_price_C6Y1_redacted",),
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
        verifier_result_ref="verifier_result_price_C6Y1",
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


def _decision(packet: dict[str, object], **overrides: object) -> dict[str, object]:
    decision = build_oacp_cache_operator_decision_record(
        review_packet=packet,
        decision_kind="request_more_evidence",
        reviewer_identity_ref="operator_ref_c6y1_reviewer_001",
        decided_at="2026-06-12T00:02:00.000Z",
    )
    decision.update(overrides)
    return decision


def _bundle(
    *,
    records: tuple[OacpPersistentArtifactCacheRecord, ...] | None = None,
    decision_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    cache_records = records or (_record(),)
    plan = _plan(cache_records)
    packet = _review_packet(plan)
    decision = _decision(packet, **(decision_overrides or {}))
    return build_oacp_c6x9_audit_export_bundle(
        cache_records=cache_records,
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


def _manifest(**bundle_overrides: object) -> dict[str, object]:
    bundle = _bundle()
    bundle.update(bundle_overrides)
    return build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=bundle,
        generated_at="2026-06-12T00:04:00.000Z",
        retention_class="standard_internal_review",
    )


def test_c6y1_review_manifest_is_deterministic_redacted_and_non_executing() -> None:
    manifest = _manifest()
    second_manifest = _manifest()

    assert manifest["manifest_id"] == second_manifest["manifest_id"]
    assert manifest["manifest_kind"] == "oacp_audit_export_review_manifest"
    assert manifest["status"] == "ready_for_internal_review"
    assert manifest["tenant_id"] == "cten_C6W3"
    assert manifest["merchant_id"] == "mch_C6W3"
    assert manifest["seller_agent_id"] == "seller_C6W3"
    assert manifest["buyer_agent_id"] == "buyer_C6W3"
    assert manifest["artifact_family_counts"] == {"price": 1}
    assert manifest["cache_record_references"] == ["cache_price_C6Y1"]
    assert len(manifest["decision_record_references"]) == 2
    assert manifest["redacted_evidence_refs"] == ["evidence_price_C6Y1_redacted"]
    assert manifest["freshness_ttl_summary"]["freshness"] == {"fresh": 1}
    assert manifest["retention_boundary"] == {
        "retention_class": "standard_internal_review",
        "retention_days": 90,
        "retain_until": "2026-09-10T00:04:00.000Z",
        "retention_clock_source": "manifest_generated_at",
        "persistence_required": False,
        "requires_separate_persistence_approval": True,
        "export_file_writer_added": False,
        "generated_artifact_written": False,
    }
    assert manifest["redaction_boundary"]["redacted_refs_only"] is True
    assert manifest["allowed_to_execute"] is False
    assert manifest["no_execution"] is True
    assert manifest["review_manifest_only"] is True
    assert manifest["retention_boundary_only"] is True
    assert manifest["export_file_written"] is False
    assert manifest["export_writer_added"] is False
    assert manifest["migration_added"] is False
    assert manifest["scheduler_added"] is False
    assert manifest["non_authoritative_for_transaction"] is True
    assert manifest["no_checkout_payment_enablement"] is True
    assert manifest["no_live_provider_enablement"] is True
    assert manifest["no_public_discovery_enablement"] is True
    assert manifest["raw_payloads_included"] is False
    assert manifest["grantex_runtime_required"] is False
    assert "raw_jwt" not in str(manifest)
    assert "password" not in str(manifest)


def test_c6y1_review_manifest_fails_closed_for_unsafe_or_unready_bundles() -> None:
    not_ready = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle={"bundle_id": "bundle_missing_status", "generated_at": "2026-06-12T00:03:00.000Z"},
        generated_at="2026-06-12T00:04:00.000Z",
    )
    assert not_ready["status"] == "blocked"
    assert not_ready["block_reason"] == "audit_export_bundle_not_ready"

    export_writer_claim = _manifest(export_file_written=True)
    assert export_writer_claim["status"] == "blocked"
    assert export_writer_claim["block_reason"] == "bundle_non_enablement_flags_invalid"

    private_ref = _manifest(redacted_evidence_refs=["raw_jwt_marker"])
    assert private_ref["status"] == "blocked"
    assert private_ref["block_reason"] == "private_ref_or_overclaim_detected"

    overclaim = _manifest(operator_safe_message="production readiness approved")
    assert overclaim["status"] == "blocked"
    assert overclaim["block_reason"] == "private_ref_or_overclaim_detected"


def test_c6y1_review_manifest_supports_internal_retention_classes_only() -> None:
    bundle = _bundle()
    manifest = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=bundle,
        generated_at="2026-06-12T00:04:00.000Z",
        retention_class="short_lived_internal_review",
    )
    assert manifest["status"] == "ready_for_internal_review"
    assert manifest["retention_boundary"]["retention_days"] == 30
    assert manifest["retention_boundary"]["persistence_required"] is False

    unsupported = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=bundle,
        generated_at="2026-06-12T00:04:00.000Z",
        retention_class="public_export_ready",  # type: ignore[arg-type]
    )
    assert unsupported["status"] == "blocked"
    assert unsupported["block_reason"] == "retention_class_unsupported"


def test_c6y1_docs_capture_internal_review_manifest_boundary() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Review Manifest Inputs",
        "Review Manifest Output",
        "Retention And Redaction Boundary",
        "Fail-Closed Rules",
        "Persistence Migration Scheduler And Export Writer Decision",
        "Guardrails",
        "What C6Y1 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "review manifest only" in doc
    assert "retention boundary only" in doc
    assert "allowed_to_execute = false" in doc
    assert "does not write export files" in doc
    assert "does not call Grantex live" in doc
    assert "not a transaction toll booth" in doc
