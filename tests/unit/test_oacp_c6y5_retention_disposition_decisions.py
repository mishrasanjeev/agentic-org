from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.commerce.oacp_artifacts import (
    DurableOacpRetentionDispositionDecisionRepository,
    OacpArtifactCacheRepositoryQuery,
    OacpAuditReviewManifestRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    OacpRetentionDispositionDecisionRepositoryQuery,
    build_oacp_c6x9_audit_export_bundle,
    build_oacp_c6y1_audit_export_review_manifest,
    build_oacp_c6y3_audit_review_manifest_summary,
    build_oacp_c6y4_retention_disposition_dry_run,
    build_oacp_c6y4_retention_operator_review_packet,
    build_oacp_c6y5_retention_disposition_decision_record,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)
from core.models.oacp_retention_disposition_decision import OacpRetentionDispositionDecisionRecordRow

C6Y5_DOC_PATH = Path("docs/reports/commerce-agent-c6y5-oacp-retention-disposition-decisions.md")
C6Y5_MIGRATION_PATH = Path("migrations/versions/v6_y5_oacp_retention_disposition_decisions.py")


def _doc() -> str:
    return C6Y5_DOC_PATH.read_text(encoding="utf-8")


def _migration() -> str:
    return C6Y5_MIGRATION_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6Y5",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6Y5",),
        evidence_refs=("evidence_price_C6Y5_redacted",),
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
        verifier_result_ref="verifier_result_price_C6Y5",
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


def _operator_decision(packet: dict[str, object]) -> dict[str, object]:
    return build_oacp_cache_operator_decision_record(
        review_packet=packet,
        decision_kind="request_more_evidence",
        reviewer_identity_ref="operator_ref_c6y5_reviewer_001",
        decided_at="2026-06-12T00:02:00.000Z",
    )


def _bundle(records: tuple[OacpPersistentArtifactCacheRecord, ...]) -> dict[str, object]:
    plan = _plan(records)
    packet = _review_packet(plan)
    decision = _operator_decision(packet)
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


def _summary() -> dict[str, object]:
    manifest = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=_bundle((_record(),)),
        generated_at="2026-06-12T00:04:00.000Z",
        retention_class="standard_internal_review",
    )
    return build_oacp_c6y3_audit_review_manifest_summary(
        (manifest,),
        query=OacpAuditReviewManifestRepositoryQuery(
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            retention_class="standard_internal_review",
        ),
        generated_at="2026-06-12T00:05:00.000Z",
    )


def _dry_run() -> dict[str, object]:
    return build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary=_summary(),
        generated_at="2026-06-12T00:06:00.000Z",
    )


def _operator_packet(dry_run: dict[str, object]) -> dict[str, object]:
    return build_oacp_c6y4_retention_operator_review_packet(
        retention_disposition_dry_run=dry_run,
        generated_at="2026-06-12T00:07:00.000Z",
    )


def _disposition_decision(**overrides: object) -> dict[str, object]:
    dry_run = _dry_run()
    decision = build_oacp_c6y5_retention_disposition_decision_record(
        retention_disposition_dry_run=dry_run,
        operator_review_packet=_operator_packet(dry_run),
        decision_kind="approve_future_retention_review",
        reviewer_identity_ref="operator_ref_c6y5_reviewer_001",
        decided_at="2026-06-12T00:08:00.000Z",
    )
    decision.update(overrides)
    return decision


class _FakeScalars:
    def __init__(self, rows: list[OacpRetentionDispositionDecisionRecordRow]) -> None:
        self._rows = rows

    def all(self) -> list[OacpRetentionDispositionDecisionRecordRow]:
        return self._rows


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.rows: dict[str, OacpRetentionDispositionDecisionRecordRow] = {}

    async def get(
        self,
        _model: type[OacpRetentionDispositionDecisionRecordRow],
        disposition_decision_id: str,
    ) -> OacpRetentionDispositionDecisionRecordRow | None:
        return self.rows.get(disposition_decision_id)

    def add(self, row: OacpRetentionDispositionDecisionRecordRow) -> None:
        self.rows[row.disposition_decision_id] = row

    async def flush(self) -> None:
        return None

    async def scalars(self, _statement: object) -> _FakeScalars:
        return _FakeScalars(list(self.rows.values()))


@pytest.fixture()
def repository() -> DurableOacpRetentionDispositionDecisionRepository:
    return DurableOacpRetentionDispositionDecisionRepository(_FakeAsyncSession())


@pytest.mark.asyncio
async def test_c6y5_repository_upserts_gets_lists_and_evaluates_without_retention_execution(
    repository: DurableOacpRetentionDispositionDecisionRepository,
) -> None:
    decision = _disposition_decision()
    result = await repository.upsert_disposition_decision(decision)

    assert result["stored"] is True
    assert result["durable_repository"] is True
    assert result["future_retention_action_allowed"] is False
    assert result["records_deleted"] is False
    assert result["retention_executed"] is False
    assert result["allowed_to_execute"] is False
    assert result["non_authoritative_for_transaction"] is True

    stored = await repository.get_disposition_decision(str(decision["disposition_decision_id"]))
    assert stored is not None
    assert stored["source_summary_id"] == decision["source_summary_id"]
    assert stored["source_dry_run_id"] == decision["source_dry_run_id"]
    assert stored["source_operator_packet_id"] == decision["source_operator_packet_id"]
    assert stored["redacted_evidence_ref_count"] == 1
    assert stored["reviewer_ref"] == "operator_ref_c6y5_reviewer_001"
    assert stored["export_file_written"] is False
    assert stored["scheduler_added"] is False

    scoped = await repository.list_disposition_decisions_for_scope(
        OacpRetentionDispositionDecisionRepositoryQuery(
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            decision_kind="approve_future_retention_review",
            retention_class="standard_internal_review",
        ),
    )
    assert [item["disposition_decision_id"] for item in scoped] == [decision["disposition_decision_id"]]

    evaluation = await repository.evaluate_disposition_decision_for_future_review(
        str(decision["disposition_decision_id"])
    )
    assert evaluation["status"] == "future_retention_review_requires_separate_action"
    assert evaluation["future_retention_action_allowed"] is False
    assert evaluation["records_deleted"] is False
    assert evaluation["retention_executed"] is False
    assert evaluation["allowed_to_execute"] is False


@pytest.mark.asyncio
async def test_c6y5_repository_fails_closed_before_storage(
    repository: DurableOacpRetentionDispositionDecisionRepository,
) -> None:
    cases = [
        (_disposition_decision(disposition_decision_id=""), "disposition_decision_identity_or_scope_missing"),
        (_disposition_decision(decision_kind="execute_retention_now"), "decision_kind_invalid"),
        (_disposition_decision(retention_class="public_export_ready"), "retention_class_invalid"),
        (_disposition_decision(retain_until="2026-06-12T00:06:59.000Z"), "decision_timestamps_invalid"),
        (_disposition_decision(allowed_to_execute=True), "non_enablement_flags_missing"),
        (_disposition_decision(records_deleted=True), "non_enablement_flags_missing"),
        (_disposition_decision(retention_executed=True), "non_enablement_flags_missing"),
        (_disposition_decision(export_file_written=True), "non_enablement_flags_missing"),
        (_disposition_decision(scheduler_added=True), "non_enablement_flags_missing"),
        (_disposition_decision(reviewer_ref="reviewer@example.com"), "reviewer_identity_not_opaque"),
        (_disposition_decision(next_step_labels=["delete_records_now"]), "decision_labels_executable_or_private"),
        (
            _disposition_decision(operator_safe_message="production readiness approved"),
            "decision_private_or_overclaiming_values",
        ),
    ]

    for decision, refusal_code in cases:
        result = await repository.upsert_disposition_decision(decision)
        assert result["stored"] is False
        assert result["allowed_to_execute"] is False
        assert result["future_retention_action_allowed"] is False
        assert result["records_deleted"] is False
        assert result["retention_executed"] is False
        assert result["refusal_code"] == refusal_code

    missing = await repository.evaluate_disposition_decision_for_future_review("missing_decision_C6Y5")
    assert missing["status"] == "blocked"
    assert missing["refusal_code"] == "disposition_decision_missing"


def test_c6y5_builder_fails_closed_for_bad_source_packets_and_raw_reviewer_identity() -> None:
    dry_run = _dry_run()
    packet = _operator_packet(dry_run)
    unsafe_source = build_oacp_c6y5_retention_disposition_decision_record(
        retention_disposition_dry_run={**dry_run, "records_deleted": True},
        operator_review_packet=packet,
        decision_kind="approve_future_retention_review",
        reviewer_identity_ref="operator_ref_c6y5_reviewer_001",
        decided_at="2026-06-12T00:08:00.000Z",
    )
    raw_reviewer = build_oacp_c6y5_retention_disposition_decision_record(
        retention_disposition_dry_run=dry_run,
        operator_review_packet=packet,
        decision_kind="approve_future_retention_review",
        reviewer_identity_ref="operator@example.com",
        decided_at="2026-06-12T00:08:00.000Z",
    )

    assert unsafe_source["refusal_code"] == "source_packet_executable_or_enabling"
    assert raw_reviewer["refusal_code"] == "reviewer_identity_not_opaque"
    for result in (unsafe_source, raw_reviewer):
        assert result["decision_record_built"] is False
        assert result["records_deleted"] is False
        assert result["retention_executed"] is False
        assert result["allowed_to_execute"] is False


def test_c6y5_migration_has_tenant_safe_rls_and_non_execution_guards() -> None:
    migration = _migration()
    for expected in (
        "revision = \"v6y5_retention_decisions\"",
        "CREATE TABLE IF NOT EXISTS oacp_retention_disposition_decision_records",
        "down_revision = \"v6y4_repair_a2a_tasks\"",
        "ix_oacp_retention_disposition_decision_tenant_id",
        "ix_oacp_retention_disposition_decision_merchant_id",
        "ix_oacp_retention_disposition_decision_seller_agent_id",
        "ix_oacp_retention_disposition_decision_buyer_agent_id",
        "ix_oacp_retention_disposition_decision_summary_id",
        "ix_oacp_retention_disposition_decision_dry_run_id",
        "ix_oacp_retention_disposition_decision_packet_id",
        "uq_oacp_retention_disposition_decision_packet_kind_reviewer",
        "future_retention_action_allowed IS FALSE",
        "records_deleted IS FALSE",
        "retention_executed IS FALSE",
        "allowed_to_execute IS FALSE",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "DROP TABLE IF EXISTS oacp_retention_disposition_decision_records",
    ):
        assert expected in migration

    assert len("v6y5_retention_decisions") <= 32


def test_c6y5_docs_capture_durable_repository_scope_and_non_goals() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Persistence Decision",
        "Durable Repository Contract",
        "Stored Fields",
        "Fail-Closed Persistence And Evaluation",
        "Migration Safety",
        "Guardrails",
        "What C6Y5 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg owns retention disposition review" in doc
    assert "v6y5_retention_decisions" in doc
    assert "RLS" in doc
    assert "allowed_to_execute = false" in doc
    assert "future_retention_action_allowed = false" in doc
    assert "records_deleted = false" in doc
    assert "does not execute retention" in doc
