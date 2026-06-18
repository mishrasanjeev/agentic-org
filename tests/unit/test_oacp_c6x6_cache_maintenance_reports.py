from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_cache_maintenance_dry_run_report,
    plan_oacp_artifact_cache_maintenance,
)

C6X6_DOC_PATH = Path("docs/reports/commerce-agent-c6x6-oacp-cache-maintenance-reports.md")


def _doc() -> str:
    return C6X6_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X6",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X6",),
        evidence_refs=("evidence_price_C6X6_redacted",),
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
        verifier_result_ref="verifier_result_price_C6X6",
    )
    return replace(base, **overrides)


def _plan() -> dict[str, object]:
    return plan_oacp_artifact_cache_maintenance(
        records=(
            _record(cache_record_id="cache_keep_C6X6"),
            _record(cache_record_id="cache_refresh_C6X6", expires_at="2026-06-11T00:01:45.000Z"),
            _record(cache_record_id="cache_expired_C6X6", expires_at="2026-06-11T00:00:59.000Z"),
            _record(cache_record_id="cache_revoked_C6X6", revocation_snapshot_status="revoked"),
            _record(cache_record_id="cache_review_C6X6", artifact_type="protocol_adapter"),
        ),
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
        action_intent="final_commitment",
        risk_tier="medium",
        scope_filter=OacpArtifactCacheRepositoryQuery(tenant_id="cten_C6W3"),
    )


def test_c6x6_dry_run_report_summarizes_maintenance_plan_without_execution() -> None:
    report = build_oacp_cache_maintenance_dry_run_report(maintenance_plan=_plan())

    assert report["report_kind"] == "cache_maintenance_dry_run_report"
    assert report["source_plan_id"]
    assert report["status"] == "prepared_for_operator_review"
    assert report["records_seen"] == 5
    assert report["records_to_refresh"] == ["cache_keep_C6X6", "cache_refresh_C6X6"]
    assert set(report["records_to_evict"]) == {"cache_expired_C6X6", "cache_revoked_C6X6"}
    assert report["records_requiring_human_review"] == ["cache_review_C6X6"]
    assert report["scope_summary"]["buyer_agent"] == {"buyer_C6W3": 5}
    assert report["artifact_family_counts"] == {"price": 4, "protocol_adapter": 1}
    assert report["freshness_summary"] == {"fresh": 5}
    assert report["revocation_snapshot_summary"] == {"fresh": 4, "revoked": 1}
    assert report["risk_tier_summary"] == {"low": 5}
    assert report["ttl_summary"]["records_with_ttl"] == 5
    assert report["allowed_to_execute"] is False
    assert report["no_execution"] is True
    assert report["dry_run_report_only"] is True
    assert report["operator_review_only"] is True
    assert report["non_authoritative_for_transaction"] is True
    assert report["no_checkout_payment_enablement"] is True
    assert report["no_live_provider_enablement"] is True
    assert report["no_public_discovery_enablement"] is True
    assert report["raw_payloads_included"] is False


def test_c6x6_source_refresh_preview_is_label_only_and_redacted() -> None:
    report = build_oacp_cache_maintenance_dry_run_report(
        maintenance_plan=_plan(),
        report_kind="source_refresh_request_preview",
    )

    preview = report["source_refresh_request_preview"]
    assert report["report_kind"] == "source_refresh_request_preview"
    assert preview["preview_only"] is True
    assert preview["next_system_step_label"] == "source_refresh_request_label_only_no_api_call"
    assert preview["records"] == ["cache_keep_C6X6", "cache_refresh_C6X6"]
    assert "evidence_price_C6X6_redacted" in preview["evidence_refs"]
    assert "http" not in preview["next_system_step_label"]
    assert "raw_jwt" not in str(report)
    assert "password" not in str(report)


def test_c6x6_blocks_missing_executable_or_private_plan_inputs() -> None:
    missing_report = build_oacp_cache_maintenance_dry_run_report(maintenance_plan=None)
    assert missing_report["report_kind"] == "blocked_cache_action_report"
    assert missing_report["block_reason"] == "maintenance_plan_missing"
    assert missing_report["allowed_to_execute"] is False

    executable_plan = dict(_plan())
    executable_plan["allowed_to_execute"] = True
    executable_report = build_oacp_cache_maintenance_dry_run_report(maintenance_plan=executable_plan)
    assert executable_report["block_reason"] == "maintenance_plan_executable_or_enabling"
    assert executable_report["no_execution"] is True

    private_plan = dict(_plan())
    private_plan["raw_jwt"] = "raw_jwt_value"
    private_report = build_oacp_cache_maintenance_dry_run_report(maintenance_plan=private_plan)
    assert private_report["block_reason"] == "private_or_enabling_plan_field"
    assert private_report["non_authoritative_for_transaction"] is True


def test_c6x6_docs_capture_report_boundaries_and_no_scheduler() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Report Kinds",
        "Report Output",
        "Operator Review Packet",
        "Fail-Closed Rules",
        "Migration And Scheduler Decision",
        "Guardrails",
        "What C6X6 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "dry-run report only" in doc
    assert "allowed_to_execute = false" in doc
    assert "label-only" in doc
    assert "no scheduler" in doc
    assert "does not call Grantex live" in doc
    assert "does not call providers" in doc
