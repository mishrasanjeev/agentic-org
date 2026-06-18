from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.commerce.oacp_artifacts import (
    DurableOacpArtifactCacheRepository,
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
)
from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow

C6X4_DOC_PATH = Path("docs/reports/commerce-agent-c6x4-durable-oacp-cache-repository.md")
C6X4_MIGRATION_PATH = Path("migrations/versions/v6_x4_oacp_artifact_cache.py")


def _doc() -> str:
    return C6X4_DOC_PATH.read_text(encoding="utf-8")


def _migration() -> str:
    return C6X4_MIGRATION_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X4",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X4",),
        evidence_refs=("evidence_price_C6X4_redacted",),
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
        verifier_result_ref="verifier_result_price_C6X4",
    )
    return replace(base, **overrides)


class _FakeScalars:
    def __init__(self, rows: list[OacpArtifactCacheRecordRow]) -> None:
        self._rows = rows

    def all(self) -> list[OacpArtifactCacheRecordRow]:
        return self._rows


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.rows: dict[str, OacpArtifactCacheRecordRow] = {}

    async def get(
        self,
        _model: type[OacpArtifactCacheRecordRow],
        cache_record_id: str,
    ) -> OacpArtifactCacheRecordRow | None:
        return self.rows.get(cache_record_id)

    def add(self, row: OacpArtifactCacheRecordRow) -> None:
        self.rows[row.cache_record_id] = row

    async def flush(self) -> None:
        return None

    async def scalars(self, _statement: object) -> _FakeScalars:
        return _FakeScalars(list(self.rows.values()))


@pytest.fixture()
def repository() -> DurableOacpArtifactCacheRepository:
    return DurableOacpArtifactCacheRepository(_FakeAsyncSession())


@pytest.mark.asyncio
async def test_c6x4_durable_repository_upserts_gets_and_lists_all_scopes(
    repository: DurableOacpArtifactCacheRepository,
) -> None:
    records = (
        _record(scope_kind="buyer_agent"),
        _record(
            cache_record_id="cache_seller_C6X4",
            artifact_id="seller_agent_capability_C6X4",
            artifact_type="seller_agent_capability",
            scope_kind="seller_agent",
            buyer_agent_id=None,
            ttl_policy_seconds=6 * 60 * 60,
        ),
        _record(
            cache_record_id="cache_tenant_C6X4",
            artifact_id="policy_C6X4",
            artifact_type="policy",
            scope_kind="tenant",
            merchant_id=None,
            seller_agent_id=None,
            buyer_agent_id=None,
            ttl_policy_seconds=6 * 60 * 60,
        ),
        _record(
            cache_record_id="cache_merchant_C6X4",
            artifact_id="merchant_capability_C6X4",
            artifact_type="merchant_capability",
            scope_kind="merchant",
            seller_agent_id=None,
            buyer_agent_id=None,
            ttl_policy_seconds=24 * 60 * 60,
        ),
    )

    for record in records:
        result = await repository.upsert(record)
        assert result["stored"] is True
        assert result["durable_repository"] is True
        assert result["allowed_to_execute"] is False
        assert result["non_authoritative_for_transaction"] is True

    stored = await repository.get("cache_price_C6X4")
    assert stored is not None
    assert stored.artifact_id == "price_C6W3"
    assert stored.evidence_refs == ("evidence_price_C6X4_redacted",)

    tenant_records = await repository.list_for_scope(OacpArtifactCacheRepositoryQuery(tenant_id="cten_C6W3"))
    assert {record.scope_kind for record in tenant_records} == {"buyer_agent", "seller_agent", "tenant", "merchant"}

    buyer_records = await repository.list_for_scope(
        OacpArtifactCacheRepositoryQuery(
            scope_kind="buyer_agent",
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            seller_agent_id="seller_C6W3",
            buyer_agent_id="buyer_C6W3",
        )
    )
    assert [record.cache_record_id for record in buyer_records] == ["cache_price_C6X4"]


@pytest.mark.asyncio
async def test_c6x4_durable_repository_evaluates_without_grantex_toll_booth(
    repository: DurableOacpArtifactCacheRepository,
) -> None:
    await repository.upsert(_record())

    preview = await repository.evaluate(
        cache_record_id="cache_price_C6X4",
        action_intent="non_binding_preview",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        expected_scope={"tenant_id": "cten_C6W3", "merchant_id": "mch_C6W3"},
    )
    assert preview["status"] == "usable_for_non_binding_cache"
    assert preview["allowed_to_preview"] is True
    assert preview["allowed_to_execute"] is False
    assert preview["grantex_runtime_required"] is False

    commitment = await repository.evaluate(
        cache_record_id="cache_price_C6X4",
        action_intent="final_commitment",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        expected_scope={
            "tenant_id": "cten_C6W3",
            "merchant_id": "mch_C6W3",
            "seller_agent_id": "seller_C6W3",
            "buyer_agent_id": "buyer_C6W3",
        },
    )
    assert commitment["status"] == "prepared_only_for_commitment_boundary"
    assert commitment["allowed_to_prepare"] is True
    assert commitment["allowed_to_execute"] is False
    assert commitment["non_authoritative_for_transaction"] is True


@pytest.mark.asyncio
async def test_c6x4_durable_repository_fails_closed_before_storage(
    repository: DurableOacpArtifactCacheRepository,
) -> None:
    cases = [
        (_record(cache_record_id=""), "cache_record_identity_missing"),
        (_record(evidence_refs=("raw_jwt_ref",)), "cache_refs_missing_or_private"),
        (_record(allowed_to_execute=True), "non_enablement_flags_missing"),
        (_record(cached_at="not-a-date"), "cache_timestamps_invalid"),
        (_record(revocation_snapshot_status="unknown"), "cache_revocation_snapshot_ambiguous"),
    ]

    for record, refusal_code in cases:
        result = await repository.upsert(record)
        assert result["stored"] is False
        assert result["allowed_to_execute"] is False
        assert result["refusal_code"] == refusal_code

    assert await repository.get("") is None


def test_c6x4_migration_has_tenant_safe_indexes_and_non_execution_guards() -> None:
    migration = _migration()
    for expected in (
        "CREATE TABLE IF NOT EXISTS oacp_artifact_cache_records",
        "ix_oacp_cache_tenant_id",
        "ix_oacp_cache_merchant_id",
        "ix_oacp_cache_seller_agent_id",
        "ix_oacp_cache_buyer_agent_id",
        "ix_oacp_cache_artifact_id",
        "ix_oacp_cache_artifact_type",
        "ix_oacp_cache_expires_at",
        "ix_oacp_cache_freshness_status",
        "ix_oacp_cache_revocation_status",
        "uq_oacp_cache_artifact_scope",
        "allowed_to_execute IS FALSE",
        "ENABLE ROW LEVEL SECURITY",
        "DROP TABLE IF EXISTS oacp_artifact_cache_records",
    ):
        assert expected in migration


def test_c6x4_docs_capture_durable_scope_and_guardrails() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Durable Repository Contract",
        "Stored Fields",
        "Migration Decision",
        "Fail-Closed Storage And Evaluation",
        "Guardrails",
        "What C6X4 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg remains the buyer and seller AI-agent runtime" in doc
    assert "without calling Grantex for every non-binding turn" in doc
    assert "v6x4_oacp_cache" in doc
    assert "allowed_to_execute = false" in doc
    assert "no public endpoint" in doc
    assert "checkout, payment" in doc
    assert "no live provider rail" in doc
