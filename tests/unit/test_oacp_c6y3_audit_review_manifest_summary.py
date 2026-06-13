from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.commerce.oacp_artifacts import (
    DurableOacpAuditReviewManifestRepository,
    OacpArtifactCacheRepositoryQuery,
    OacpAuditExportReviewRetentionClass,
    OacpAuditReviewManifestRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_c6x9_audit_export_bundle,
    build_oacp_c6y1_audit_export_review_manifest,
    build_oacp_c6y3_audit_review_manifest_summary,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)
from core.models.oacp_audit_review_manifest import OacpAuditReviewManifestRecordRow

C6Y3_DOC_PATH = Path("docs/reports/commerce-agent-c6y3-oacp-audit-review-manifest-summary.md")


def _doc() -> str:
    return C6Y3_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6Y3",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6Y3",),
        evidence_refs=("evidence_price_C6Y3_redacted",),
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
        verifier_result_ref="verifier_result_price_C6Y3",
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
        reviewer_identity_ref="operator_ref_c6y3_reviewer_001",
        decided_at="2026-06-12T00:02:00.000Z",
    )


def _bundle(
    records: tuple[OacpPersistentArtifactCacheRecord, ...],
    *,
    generated_at: str = "2026-06-12T00:03:00.000Z",
) -> dict[str, object]:
    plan = _plan(records)
    packet = _review_packet(plan)
    decision = _decision(packet)
    return build_oacp_c6x9_audit_export_bundle(
        cache_records=records,
        maintenance_plans=(plan,),
        review_packets=(packet,),
        operator_decision_records=(decision,),
        durable_decision_records=(dict(decision),),
        generated_at=generated_at,
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
    )


def _manifest(
    records: tuple[OacpPersistentArtifactCacheRecord, ...] | None = None,
    *,
    generated_at: str = "2026-06-12T00:04:00.000Z",
    retention_class: OacpAuditExportReviewRetentionClass = "standard_internal_review",
    **overrides: object,
) -> dict[str, object]:
    manifest = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=_bundle(records or (_record(),)),
        generated_at=generated_at,
        retention_class=retention_class,
    )
    manifest.update(overrides)
    return manifest


class _FakeScalars:
    def __init__(self, rows: list[OacpAuditReviewManifestRecordRow]) -> None:
        self._rows = rows

    def all(self) -> list[OacpAuditReviewManifestRecordRow]:
        return self._rows


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.rows: dict[str, OacpAuditReviewManifestRecordRow] = {}

    async def get(
        self,
        _model: type[OacpAuditReviewManifestRecordRow],
        manifest_id: str,
    ) -> OacpAuditReviewManifestRecordRow | None:
        return self.rows.get(manifest_id)

    def add(self, row: OacpAuditReviewManifestRecordRow) -> None:
        self.rows[row.manifest_id] = row

    async def flush(self) -> None:
        return None

    async def scalars(self, _statement: object) -> _FakeScalars:
        return _FakeScalars(list(self.rows.values()))


@pytest.fixture()
def repository() -> DurableOacpAuditReviewManifestRepository:
    return DurableOacpAuditReviewManifestRepository(_FakeAsyncSession())


@pytest.mark.asyncio
async def test_c6y3_repository_filters_and_builds_redacted_summary_without_export_or_execution(
    repository: DurableOacpAuditReviewManifestRepository,
) -> None:
    price_manifest = _manifest()
    inventory_manifest = _manifest(
        (
            _record(
                cache_record_id="cache_inventory_C6Y3",
                artifact_id="inventory_C6W3",
                artifact_type="inventory",
                evidence_refs=("evidence_inventory_C6Y3_redacted",),
                risk_tier="medium",
                verifier_result_ref="verifier_result_inventory_C6Y3",
            ),
        ),
        generated_at="2026-06-12T00:04:30.000Z",
    )
    await repository.upsert_manifest(price_manifest)
    await repository.upsert_manifest(inventory_manifest)

    query = OacpAuditReviewManifestRepositoryQuery(
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        artifact_family="price",
        generated_at_after="2026-06-12T00:03:59.000Z",
        generated_at_before="2026-06-12T00:04:01.000Z",
        blocked_capability="checkout_payment_creation",
    )

    scoped = await repository.list_manifests_for_scope(query)
    assert [item["manifest_id"] for item in scoped] == [price_manifest["manifest_id"]]

    summary = await repository.build_redacted_manifest_summary(
        query,
        generated_at="2026-06-12T00:05:00.000Z",
    )
    assert summary["summary_built"] is True
    assert summary["manifest_count"] == 1
    assert summary["artifact_family_counts"] == {"price": 1}
    assert summary["retention_due_count"] == 0
    assert summary["redacted_evidence_ref_count"] == 1
    assert summary["redacted_evidence_ref_counts"] == {price_manifest["manifest_id"]: 1}
    assert summary["allowed_to_execute"] is False
    assert summary["non_authoritative_for_transaction"] is True
    assert summary["no_export_file_written"] is True
    assert summary["grantex_runtime_required"] is False
    assert "evidence_price_C6Y3_redacted" not in str(summary)


def test_c6y3_summary_counts_due_and_legal_hold_without_evidence_values() -> None:
    manifest = _manifest(
        retention_class="legal_hold_candidate",
        generated_at="2026-06-12T00:04:00.000Z",
    )
    summary = build_oacp_c6y3_audit_review_manifest_summary(
        (manifest,),
        query=OacpAuditReviewManifestRepositoryQuery(
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            retention_class="legal_hold_candidate",
            retain_until_before="2036-06-11T00:00:00.000Z",
        ),
        generated_at="2036-06-11T00:00:00.000Z",
    )

    assert summary["summary_built"] is True
    assert summary["retention_class_counts"] == {"legal_hold_candidate": 1}
    assert summary["retention_due_count"] == 1
    assert summary["legal_hold_candidate_count"] == 1
    assert summary["risk_tier_counts"] == {"low": 1}
    assert summary["redacted_evidence_ref_count"] == 1
    assert "evidence_price_C6Y3_redacted" not in str(summary)


def test_c6y3_summary_fails_closed_for_unsafe_queries_and_manifests() -> None:
    manifest = _manifest()
    missing_tenant = build_oacp_c6y3_audit_review_manifest_summary(
        (manifest,),
        query=OacpAuditReviewManifestRepositoryQuery(merchant_id="mch_C6W3"),
        generated_at="2026-06-12T00:05:00.000Z",
    )
    unsafe_filter = build_oacp_c6y3_audit_review_manifest_summary(
        (manifest,),
        query=OacpAuditReviewManifestRepositoryQuery(
            tenant_id="cten_C6W3",
            blocked_capability="execute_checkout_payment_now",
        ),
        generated_at="2026-06-12T00:05:00.000Z",
    )
    bad_time = build_oacp_c6y3_audit_review_manifest_summary(
        (manifest,),
        query=OacpAuditReviewManifestRepositoryQuery(tenant_id="cten_C6W3"),
        generated_at="not-a-time",
    )
    executable_manifest = build_oacp_c6y3_audit_review_manifest_summary(
        (_manifest(allowed_to_execute=True),),
        query=OacpAuditReviewManifestRepositoryQuery(tenant_id="cten_C6W3"),
        generated_at="2026-06-12T00:05:00.000Z",
    )

    assert missing_tenant["summary_built"] is False
    assert missing_tenant["refusal_code"] == "query_scope_or_filter_unsafe"
    assert unsafe_filter["summary_built"] is False
    assert unsafe_filter["refusal_code"] == "query_scope_or_filter_unsafe"
    assert bad_time["summary_built"] is False
    assert bad_time["refusal_code"] == "summary_generated_at_invalid"
    assert executable_manifest["summary_built"] is False
    assert executable_manifest["refusal_code"] == "manifest_not_safe_for_summary"
    for result in (missing_tenant, unsafe_filter, bad_time, executable_manifest):
        assert result["allowed_to_execute"] is False
        assert result["future_export_allowed"] is False
        assert result["no_export_file_written"] is True


def test_c6y3_docs_capture_query_summary_boundary_and_non_goals() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Query Filters",
        "Redacted Summary Output",
        "Fail-Closed Rules",
        "Persistence Migration Scheduler And Export Writer Decision",
        "Guardrails",
        "What C6Y3 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg owns audit review manifest handling" in doc
    assert "not a transaction toll booth" in doc
    assert "allowed_to_execute = false" in doc
    assert "no_export_file_written = true" in doc
    assert "does not write export files" in doc
    assert "does not call Grantex live" in doc
