from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.commerce.oacp_artifacts import (
    DurableOacpOperatorDecisionRepository,
    OacpArtifactCacheRepositoryQuery,
    OacpOperatorDecisionRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
    build_oacp_cache_maintenance_dry_run_report,
    build_oacp_cache_operator_decision_record,
    plan_oacp_artifact_cache_maintenance,
)
from core.models.oacp_operator_decision import OacpOperatorDecisionRecordRow

C6X8_DOC_PATH = Path("docs/reports/commerce-agent-c6x8-oacp-operator-decision-repository.md")
C6X8_MIGRATION_PATH = Path("migrations/versions/v6_x8_oacp_operator_decisions.py")


def _doc() -> str:
    return C6X8_DOC_PATH.read_text(encoding="utf-8")


def _migration() -> str:
    return C6X8_MIGRATION_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X8",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X8",),
        evidence_refs=("evidence_price_C6X8_redacted",),
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
        verifier_result_ref="verifier_result_price_C6X8",
    )
    return replace(base, **overrides)


def _review_packet() -> dict[str, object]:
    plan = plan_oacp_artifact_cache_maintenance(
        records=(
            _record(cache_record_id="cache_refresh_C6X8", expires_at="2026-06-11T00:01:45.000Z"),
            _record(cache_record_id="cache_review_C6X8", artifact_type="protocol_adapter", risk_tier="high"),
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


def _decision(**overrides: object) -> dict[str, object]:
    decision = build_oacp_cache_operator_decision_record(
        review_packet=_review_packet(),
        decision_kind="approve_future_refresh_request",
        reviewer_identity_ref="operator_ref_c6x8_reviewer_001",
        decided_at="2026-06-11T00:02:00.000Z",
    )
    decision.update(overrides)
    return decision


class _FakeScalars:
    def __init__(self, rows: list[OacpOperatorDecisionRecordRow]) -> None:
        self._rows = rows

    def all(self) -> list[OacpOperatorDecisionRecordRow]:
        return self._rows


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.rows: dict[str, OacpOperatorDecisionRecordRow] = {}

    async def get(
        self,
        _model: type[OacpOperatorDecisionRecordRow],
        decision_id: str,
    ) -> OacpOperatorDecisionRecordRow | None:
        return self.rows.get(decision_id)

    def add(self, row: OacpOperatorDecisionRecordRow) -> None:
        self.rows[row.decision_id] = row

    async def flush(self) -> None:
        return None

    async def scalars(self, _statement: object) -> _FakeScalars:
        return _FakeScalars(list(self.rows.values()))


@pytest.fixture()
def repository() -> DurableOacpOperatorDecisionRepository:
    return DurableOacpOperatorDecisionRepository(_FakeAsyncSession())


@pytest.mark.asyncio
async def test_c6x8_durable_repository_upserts_gets_lists_and_evaluates_without_execution(
    repository: DurableOacpOperatorDecisionRepository,
) -> None:
    decision = _decision()
    result = await repository.upsert_decision(decision)

    assert result["stored"] is True
    assert result["durable_repository"] is True
    assert result["future_action_allowed"] is False
    assert result["allowed_to_execute"] is False
    assert result["non_authoritative_for_transaction"] is True

    stored = await repository.get_decision(str(decision["decision_record_id"]))
    assert stored is not None
    assert stored["review_packet_id"] == decision["review_packet_id"]
    assert stored["evidence_refs"] == ["evidence_price_C6X8_redacted"]
    assert stored["reviewer_identity_ref"] == "operator_ref_c6x8_reviewer_001"

    scoped = await repository.list_decisions_for_scope(
        OacpOperatorDecisionRepositoryQuery(tenant_id="cten_C6W3", buyer_agent_id="buyer_C6W3"),
    )
    assert [item["decision_record_id"] for item in scoped] == [decision["decision_record_id"]]

    evaluation = await repository.evaluate_decision_for_future_action(str(decision["decision_record_id"]))
    assert evaluation["status"] == "future_action_requires_separate_approval"
    assert evaluation["future_action_allowed"] is False
    assert evaluation["allowed_to_execute"] is False
    assert evaluation["prepared_only"] is True


@pytest.mark.asyncio
async def test_c6x8_durable_repository_fails_closed_before_storage(
    repository: DurableOacpOperatorDecisionRepository,
) -> None:
    cases = [
        (_decision(decision_record_id=""), "decision_identity_missing"),
        (_decision(evidence_refs=["raw_jwt_marker"]), "decision_refs_missing_or_private"),
        (_decision(allowed_to_execute=True), "non_enablement_flags_missing"),
        (_decision(reviewer_identity_ref="reviewer_ref_phone_marker"), "reviewer_identity_not_opaque"),
        (_decision(next_step_labels=["execute_cache_eviction_now"]), "decision_labels_executable_or_private"),
    ]

    for decision, refusal_code in cases:
        result = await repository.upsert_decision(decision)
        assert result["stored"] is False
        assert result["allowed_to_execute"] is False
        assert result["future_action_allowed"] is False
        assert result["refusal_code"] == refusal_code

    missing = await repository.evaluate_decision_for_future_action("missing_decision_C6X8")
    assert missing["status"] == "blocked"
    assert missing["refusal_code"] == "decision_missing"


def test_c6x8_migration_has_tenant_safe_indexes_rls_and_non_execution_guards() -> None:
    migration = _migration()
    for expected in (
        "CREATE TABLE IF NOT EXISTS oacp_operator_decision_records",
        "ix_oacp_operator_decision_tenant_id",
        "ix_oacp_operator_decision_merchant_id",
        "ix_oacp_operator_decision_seller_agent_id",
        "ix_oacp_operator_decision_buyer_agent_id",
        "ix_oacp_operator_decision_review_packet_id",
        "ix_oacp_operator_decision_maintenance_plan_id",
        "uq_oacp_operator_decision_packet_kind_reviewer",
        "allowed_to_execute IS FALSE",
        "future_action_allowed IS FALSE",
        "ENABLE ROW LEVEL SECURITY",
        "DROP TABLE IF EXISTS oacp_operator_decision_records",
    ):
        assert expected in migration


def test_c6x8_docs_capture_durable_repository_scope_and_non_goals() -> None:
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
        "What C6X8 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg owns local operator decision handling" in doc
    assert "v6x8_oacp_operator_decisions" in doc
    assert "opaque reviewer reference" in doc
    assert "allowed_to_execute = false" in doc
    assert "future_action_allowed = false" in doc
    assert "does not call Grantex live" in doc
