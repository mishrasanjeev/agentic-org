from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_c6x9_audit_export_bundle,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)

C6X9_DOC_PATH = Path("docs/reports/commerce-agent-c6x9-oacp-audit-export-bundle.md")


def _doc() -> str:
    return C6X9_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X9",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X9",),
        evidence_refs=("evidence_price_C6X9_redacted",),
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
        verifier_result_ref="verifier_result_price_C6X9",
    )
    return replace(base, **overrides)


def _plan(records: tuple[OacpPersistentArtifactCacheRecord, ...]) -> dict[str, object]:
    return plan_oacp_artifact_cache_maintenance(
        records=records,
        now_iso="2026-06-11T00:01:00.000Z",
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
        reviewer_identity_ref="operator_ref_c6x9_reviewer_001",
        decided_at="2026-06-11T00:02:00.000Z",
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
        generated_at="2026-06-11T00:03:00.000Z",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
    )


def test_c6x9_audit_export_bundle_is_deterministic_redacted_and_non_executing() -> None:
    bundle = _bundle()
    second_bundle = _bundle()

    assert bundle["bundle_id"] == second_bundle["bundle_id"]
    assert bundle["bundle_kind"] == "oacp_cache_operator_decision_audit_export_bundle"
    assert bundle["status"] == "export_ready"
    assert bundle["tenant_id"] == "cten_C6W3"
    assert bundle["merchant_id"] == "mch_C6W3"
    assert bundle["seller_agent_id"] == "seller_C6W3"
    assert bundle["buyer_agent_id"] == "buyer_C6W3"
    assert bundle["scope_summary"]["buyer_agent"] == {"buyer_C6W3": 1}
    assert bundle["artifact_family_counts"] == {"price": 1}
    assert bundle["cache_record_references"] == ["cache_price_C6X9"]
    assert len(bundle["maintenance_plan_references"]) == 1
    assert len(bundle["review_packet_references"]) == 1
    assert len(bundle["decision_record_references"]) == 2
    assert bundle["redacted_evidence_refs"] == ["evidence_price_C6X9_redacted"]
    assert bundle["freshness_ttl_summary"]["freshness"] == {"fresh": 1}
    assert bundle["revocation_snapshot_summary"] == {"fresh": 1}
    assert bundle["risk_tier_summary"] == {"low": 1}
    assert bundle["blocked_capability_summary"] == {
        "checkout_payment_creation": 1,
        "live_provider_call": 1,
    }
    assert bundle["allowed_to_execute"] is False
    assert bundle["no_execution"] is True
    assert bundle["audit_export_bundle_only"] is True
    assert bundle["generated_artifact_written"] is False
    assert bundle["non_authoritative_for_transaction"] is True
    assert bundle["no_checkout_payment_enablement"] is True
    assert bundle["no_live_provider_enablement"] is True
    assert bundle["no_public_discovery_enablement"] is True
    assert bundle["raw_payloads_included"] is False
    assert bundle["grantex_runtime_required"] is False
    assert "raw_jwt" not in str(bundle)
    assert "password" not in str(bundle)


def test_c6x9_audit_export_bundle_fails_closed_for_unsafe_inputs() -> None:
    missing_scope = build_oacp_c6x9_audit_export_bundle(
        cache_records=(_record(),),
        maintenance_plans=(),
        review_packets=(),
        operator_decision_records=(),
        generated_at="2026-06-11T00:03:00.000Z",
        tenant_id="",
        merchant_id="mch_C6W3",
    )
    assert missing_scope["status"] == "blocked"
    assert missing_scope["block_reason"] == "tenant_or_merchant_scope_missing"

    private_ref = _bundle(records=(_record(evidence_refs=("raw_jwt_marker",)),))
    assert private_ref["status"] == "blocked"
    assert private_ref["block_reason"] == "cache_record_private_or_unsafe"

    scope_mismatch = build_oacp_c6x9_audit_export_bundle(
        cache_records=(_record(),),
        maintenance_plans=(_plan((_record(),)),),
        review_packets=(_review_packet(_plan((_record(),))),),
        operator_decision_records=(_decision(_review_packet(_plan((_record(),))),),),
        generated_at="2026-06-11T00:03:00.000Z",
        tenant_id="cten_other",
        merchant_id="mch_C6W3",
    )
    assert scope_mismatch["status"] == "blocked"
    assert scope_mismatch["block_reason"] == "audit_export_scope_mismatch"

    executable_decision = _bundle(decision_overrides={"allowed_to_execute": True})
    assert executable_decision["status"] == "blocked"
    assert executable_decision["block_reason"] == "export_component_executable_or_enabling"


def test_c6x9_blocks_risky_states_represented_as_approved() -> None:
    risky_record = _record(risk_tier="high")
    plan = _plan((risky_record,))
    packet = _review_packet(plan)
    approved_decision = build_oacp_cache_operator_decision_record(
        review_packet=packet,
        decision_kind="approve_future_refresh_request",
        reviewer_identity_ref="operator_ref_c6x9_reviewer_001",
        decided_at="2026-06-11T00:02:00.000Z",
    )

    bundle = build_oacp_c6x9_audit_export_bundle(
        cache_records=(risky_record,),
        maintenance_plans=(plan,),
        review_packets=(packet,),
        operator_decision_records=(approved_decision,),
        generated_at="2026-06-11T00:03:00.000Z",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
    )

    assert bundle["status"] == "blocked"
    assert bundle["block_reason"] == "risky_state_represented_as_approved"
    assert bundle["allowed_to_execute"] is False


def test_c6x9_docs_capture_internal_audit_export_boundaries() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Bundle Inputs",
        "Bundle Output",
        "Fail-Closed Rules",
        "Persistence Migration Scheduler And Export Decision",
        "Guardrails",
        "What C6X9 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "audit export bundle only" in doc
    assert "allowed_to_execute = false" in doc
    assert "does not write generated export files" in doc
    assert "does not call Grantex live" in doc
    assert "not a transaction toll booth" in doc
