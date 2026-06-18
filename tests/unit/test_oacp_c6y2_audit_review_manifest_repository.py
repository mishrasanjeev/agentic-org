from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.commerce.oacp_artifacts import (
    DurableOacpAuditReviewManifestRepository,
    OacpArtifactCacheRepositoryQuery,
    OacpAuditReviewManifestRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_c6x9_audit_export_bundle,
    build_oacp_c6y1_audit_export_review_manifest,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)
from core.models.oacp_audit_review_manifest import OacpAuditReviewManifestRecordRow

C6Y2_DOC_PATH = Path("docs/reports/commerce-agent-c6y2-oacp-audit-review-manifest-repository.md")
C6Y2_MIGRATION_PATH = Path("migrations/versions/v6_y2_oacp_audit_review_manifests.py")


def _doc() -> str:
    return C6Y2_DOC_PATH.read_text(encoding="utf-8")


def _migration() -> str:
    return C6Y2_MIGRATION_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6Y2",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6Y2",),
        evidence_refs=("evidence_price_C6Y2_redacted",),
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
        verifier_result_ref="verifier_result_price_C6Y2",
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
        reviewer_identity_ref="operator_ref_c6y2_reviewer_001",
        decided_at="2026-06-12T00:02:00.000Z",
    )
    decision.update(overrides)
    return decision


def _bundle() -> dict[str, object]:
    cache_records = (_record(),)
    plan = _plan(cache_records)
    packet = _review_packet(plan)
    decision = _decision(packet)
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


def _manifest(**overrides: object) -> dict[str, object]:
    manifest = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=_bundle(),
        generated_at="2026-06-12T00:04:00.000Z",
        retention_class="standard_internal_review",
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
async def test_c6y2_repository_upserts_gets_lists_and_evaluates_without_export_or_execution(
    repository: DurableOacpAuditReviewManifestRepository,
) -> None:
    manifest = _manifest()
    result = await repository.upsert_manifest(manifest)

    assert result["stored"] is True
    assert result["durable_repository"] is True
    assert result["future_export_allowed"] is False
    assert result["allowed_to_execute"] is False
    assert result["non_authoritative_for_transaction"] is True

    stored = await repository.get_manifest(str(manifest["manifest_id"]))
    assert stored is not None
    assert stored["bundle_id"] == manifest["bundle_id"]
    assert stored["redacted_evidence_refs"] == ["evidence_price_C6Y2_redacted"]
    assert stored["retention_boundary"]["retention_class"] == "standard_internal_review"
    assert stored["export_file_written"] is False
    assert stored["grantex_runtime_required"] is False

    scoped = await repository.list_manifests_for_scope(
        OacpAuditReviewManifestRepositoryQuery(
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            buyer_agent_id="buyer_C6W3",
            retention_class="standard_internal_review",
        ),
    )
    assert [item["manifest_id"] for item in scoped] == [manifest["manifest_id"]]

    evaluation = await repository.evaluate_manifest_for_internal_review(str(manifest["manifest_id"]))
    assert evaluation["status"] == "internal_review_requires_separate_export_approval"
    assert evaluation["future_export_allowed"] is False
    assert evaluation["allowed_to_execute"] is False
    assert evaluation["export_file_written"] is False


@pytest.mark.asyncio
async def test_c6y2_repository_fails_closed_before_storage(
    repository: DurableOacpAuditReviewManifestRepository,
) -> None:
    bad_retention = _manifest()
    bad_retention["retention_boundary"] = {
        **dict(bad_retention["retention_boundary"]),
        "retention_class": "public_export_ready",
    }
    bad_order = _manifest()
    bad_order["retention_boundary"] = {
        **dict(bad_order["retention_boundary"]),
        "retain_until": "2026-06-12T00:03:59.000Z",
    }

    cases = [
        (_manifest(manifest_id=""), "manifest_identity_or_scope_missing"),
        (bad_retention, "retention_class_invalid"),
        (bad_order, "retention_timestamps_invalid"),
        (_manifest(redacted_evidence_refs=["raw_jwt_marker"]), "manifest_refs_private_or_overclaim"),
        (_manifest(allowed_to_execute=True), "non_enablement_flags_missing"),
        (_manifest(export_file_written=True), "non_enablement_flags_missing"),
        (_manifest(next_step_labels=["execute_export_file_now"]), "manifest_labels_executable_or_private"),
        (_manifest(operator_safe_message="production readiness approved"), "manifest_refs_private_or_overclaim"),
    ]

    for manifest, refusal_code in cases:
        result = await repository.upsert_manifest(manifest)
        assert result["stored"] is False
        assert result["allowed_to_execute"] is False
        assert result["future_export_allowed"] is False
        assert result["refusal_code"] == refusal_code

    missing = await repository.evaluate_manifest_for_internal_review("missing_manifest_C6Y2")
    assert missing["status"] == "blocked"
    assert missing["refusal_code"] == "manifest_missing"


def test_c6y2_migration_has_tenant_safe_indexes_rls_and_non_execution_guards() -> None:
    migration = _migration()
    for expected in (
        "CREATE TABLE IF NOT EXISTS oacp_audit_review_manifest_records",
        "down_revision = \"v6x9_audit_log_action_text\"",
        "ix_oacp_audit_review_manifest_tenant_id",
        "ix_oacp_audit_review_manifest_merchant_id",
        "ix_oacp_audit_review_manifest_seller_agent_id",
        "ix_oacp_audit_review_manifest_buyer_agent_id",
        "ix_oacp_audit_review_manifest_bundle_id",
        "ix_oacp_audit_review_manifest_retention_class",
        "uq_oacp_review_manifest_bundle_retention_scope",
        "allowed_to_execute IS FALSE",
        "export_file_written IS FALSE",
        "export_writer_added IS FALSE",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "DROP TABLE IF EXISTS oacp_audit_review_manifest_records",
    ):
        assert expected in migration


def test_c6y2_docs_capture_durable_repository_scope_and_non_goals() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Persistence Decision",
        "Durable Repository Contract",
        "Stored Fields",
        "Retention Boundary",
        "Fail-Closed Persistence And Evaluation",
        "Migration Safety",
        "Guardrails",
        "What C6Y2 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg owns audit review manifest handling" in doc
    assert "v6y2_oacp_review_manifests" in doc
    assert "RLS" in doc
    assert "allowed_to_execute = false" in doc
    assert "future_export_allowed = false" in doc
    assert "does not write export files" in doc
